# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/trade_manager.py — Trade Execution & Management
# =============================================================================
# RESPONSIBILITY:
# Handles all trade lifecycle operations:
# opening trades, managing trailing SL/TP, closing trades.
# Single source of truth for all trade state in trades.db.
#
# WHAT THIS FILE DOES:
# - Opens trades with two-leg scaled entry (60% + 40%)
# - Manages trailing SL (breakeven at 1x risk, trail at 1.5x)
# - Manages partial TP (close 70% at fixed TP, trail 30%)
# - Applies time stops per timeframe
# - Closes trades at SL, TP, time stop, or manual close
# - Records all trades to SQLite database
# - Calculates PnL accurately for paper and live trading
#
# WHAT THIS FILE DOES NOT DO:
# - Does not generate signals (that's signal_engine.py)
# - Does not manage capital slots (that's capital_manager.py)
# - Does not monitor drawdown (that's risk_manager.py)
# =============================================================================

import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.config import (
    PAPER_TRADING, INITIAL_CAPITAL, DB,
    TRAILING_SL, TP, ENTRY, TIME_STOP_CANDLES,
    FILTERS, LOGS,
)
from bot.data_feed import get_exchange

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)


# =============================================================================
# DATABASE SETUP
# =============================================================================

def init_trades_db(conn: sqlite3.Connection):
    """Create trades database tables if they don't exist."""
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol              TEXT NOT NULL,
            direction           TEXT NOT NULL,
            tier                TEXT NOT NULL,
            timeframe           TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'open',

            -- Entry
            entry_price         REAL NOT NULL,
            entry_price_leg2    REAL,
            avg_entry_price     REAL NOT NULL,
            quantity            REAL NOT NULL,
            quantity_leg1       REAL NOT NULL,
            quantity_leg2       REAL,
            position_size_usdt  REAL NOT NULL,
            leverage            INTEGER NOT NULL,

            -- Risk levels
            stop_loss           REAL NOT NULL,
            take_profit         REAL NOT NULL,
            sl_distance         REAL NOT NULL,
            atr_at_entry        REAL NOT NULL,
            rrr                 REAL NOT NULL,

            -- Trailing state
            trailing_sl         REAL NOT NULL,
            at_breakeven        INTEGER DEFAULT 0,
            tier1_tp_hit        INTEGER DEFAULT 0,
            tier2_tp_hit        INTEGER DEFAULT 0,
            quantity_remaining  REAL NOT NULL,

            -- Exit
            exit_price          REAL,
            exit_reason         TEXT,
            pnl_usdt            REAL,
            pnl_pct             REAL,

            -- Metadata
            signal_score        REAL,
            confluence_count    INTEGER,
            candles_open        INTEGER DEFAULT 0,
            entry_time          TEXT NOT NULL,
            leg2_entry_time     TEXT,
            exit_time           TEXT,

            -- Leg 2 tracking
            leg2_pending        INTEGER DEFAULT 1,
            leg2_candles_waited INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            capital     REAL NOT NULL,
            deployed    REAL NOT NULL,
            pnl_today   REAL DEFAULT 0
        )
    """)

    conn.commit()
    logger.info("Trades database initialized")


def get_trades_conn() -> sqlite3.Connection:
    """Return connection to trades database."""
    from pathlib import Path
    Path(DB["trades"]).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB["trades"], check_same_thread=False)


# =============================================================================
# TRADE QUERIES
# =============================================================================

def get_open_trades(conn: sqlite3.Connection) -> list:
    """Return all currently open trades as list of dicts."""
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE status = 'open'")
    columns = [d[0] for d in c.description]
    return [dict(zip(columns, row)) for row in c.fetchall()]


def get_trade(conn: sqlite3.Connection, trade_id: int) -> Optional[dict]:
    """Return a specific trade by ID."""
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    row = c.fetchone()
    if not row:
        return None
    columns = [d[0] for d in c.description]
    return dict(zip(columns, row))


def get_closed_trades(conn: sqlite3.Connection, limit: int = 100) -> list:
    """Return most recent closed trades."""
    c = conn.cursor()
    c.execute(
        "SELECT * FROM trades WHERE status = 'closed' ORDER BY exit_time DESC LIMIT ?",
        (limit,)
    )
    columns = [d[0] for d in c.description]
    return [dict(zip(columns, row)) for row in c.fetchall()]


def get_capital(conn: sqlite3.Connection) -> float:
    """Return current capital from portfolio table."""
    c = conn.cursor()
    c.execute("SELECT capital FROM portfolio ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    return row[0] if row else INITIAL_CAPITAL


def update_capital(conn: sqlite3.Connection, capital: float, deployed: float = 0):
    """Update portfolio capital record."""
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute(
        "INSERT INTO portfolio (timestamp, capital, deployed) VALUES (?, ?, ?)",
        (now, capital, deployed)
    )
    conn.commit()


# =============================================================================
# EXCHANGE ORDER EXECUTION
# =============================================================================

def _place_order(
    symbol: str,
    direction: str,
    quantity: float,
    order_type: str = "market",
    price: float = None,
) -> Optional[dict]:
    """
    Place an order on the exchange.
    In paper trading mode, simulates the order fill at current price.

    Returns order dict with fill price, or None on failure.
    """
    if PAPER_TRADING:
        # Simulate market fill at signal price
        return {
            "id":    f"paper_{datetime.now(timezone.utc).timestamp()}",
            "price": price,
            "filled": quantity,
            "status": "closed",
        }

    exchange = get_exchange()
    side     = "buy" if direction == "long" else "sell"

    try:
        if order_type == "market":
            order = exchange.create_market_order(symbol, side, quantity)
        else:
            order = exchange.create_limit_order(symbol, side, quantity, price)
        return order
    except Exception as e:
        logger.error(f"Order placement failed {symbol} {side} {quantity}: {e}")
        return None


def _set_leverage(symbol: str, leverage: int):
    """Set leverage for a symbol. Only needed for live trading."""
    if PAPER_TRADING:
        return
    try:
        exchange = get_exchange()
        exchange.set_leverage(leverage, symbol)
    except Exception as e:
        logger.warning(f"Leverage set failed for {symbol}: {e}")


# =============================================================================
# TRADE ENTRY — TWO LEG SCALED ENTRY
# =============================================================================

def open_trade(
    conn: sqlite3.Connection,
    signal: dict,
) -> Optional[int]:
    """
    Open a new trade using two-leg scaled entry.

    Leg 1: Enter 60% of position at signal candle close price
    Leg 2: Enter remaining 40% on pullback to EMA within 3 candles
           If no pullback → enter at original price on next candle

    Returns trade_id if successful, None on failure.
    """
    symbol         = signal["symbol"]
    direction      = signal["direction"]
    tier           = signal["tier"]
    timeframe      = signal["timeframe"]
    entry_price    = signal["entry"]
    stop_loss      = signal["stop_loss"]
    take_profit    = signal["take_profit"]
    sl_distance    = signal["sl_distance"]
    atr            = signal["atr"]
    rrr            = signal["rrr"]
    quantity_total = signal["quantity"]
    position_usdt  = signal["position_size_usdt"]
    leverage       = signal["leverage"]
    signal_score   = signal.get("signal_score", 0)
    confluence     = signal.get("confluence_count", 0)

    # Set leverage
    _set_leverage(symbol, leverage)

    # Leg 1 — 60% of position
    qty_leg1 = round(quantity_total * ENTRY["leg1_pct"], 6)

    order1 = _place_order(symbol, direction, qty_leg1, "market", entry_price)
    if not order1:
        logger.error(f"Leg 1 order failed for {symbol}")
        return None

    fill_price_leg1 = order1.get("price", entry_price)
    now             = datetime.now(timezone.utc).isoformat()

    # Insert trade record
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades (
            symbol, direction, tier, timeframe, status,
            entry_price, avg_entry_price,
            quantity, quantity_leg1, quantity_remaining,
            position_size_usdt, leverage,
            stop_loss, take_profit, sl_distance, atr_at_entry, rrr,
            trailing_sl, at_breakeven,
            signal_score, confluence_count,
            entry_time, leg2_pending, leg2_candles_waited
        ) VALUES (
            ?, ?, ?, ?, 'open',
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?, ?,
            ?, 0,
            ?, ?,
            ?, 1, 0
        )
    """, (
        symbol, direction, tier, timeframe,
        fill_price_leg1, fill_price_leg1,
        quantity_total, qty_leg1, qty_leg1,
        position_usdt, leverage,
        stop_loss, take_profit, sl_distance, atr, rrr,
        stop_loss,  # Initial trailing SL = original SL
        signal_score, confluence,
        now, # leg2 is pending
    ))

    trade_id = c.lastrowid
    conn.commit()

    logger.info(
        f"Trade opened: {symbol.replace('/USDT:USDT','')} "
        f"{direction.upper()} | {tier} | {timeframe} | "
        f"Entry: {fill_price_leg1:.4f} | "
        f"SL: {stop_loss:.4f} | TP: {take_profit:.4f} | "
        f"Qty: {qty_leg1} (Leg 1 of 2) | "
        f"ID: {trade_id}"
    )

    try:
        from bot.config import apex_logger
        _tid = f"{symbol.replace('/USDT:USDT','')}_{now[:10].replace('-','')}_{now[11:16].replace(':','')}"
        apex_logger.trade_entry_leg(
            trade_id         = _tid,
            token            = symbol,
            side             = direction,
            leg              = 1,
            entry_price      = fill_price_leg1,
            size_usd         = round(position_usdt * ENTRY["leg1_pct"], 2),
            sl_price         = stop_loss,
            tp_price         = take_profit,
            rrr              = rrr,
            sl_method        = "atr_and_structure",
            strategy         = tier,
            tier             = tier,
            timeframe        = timeframe,
            decision_context = {
                "signal_score":  signal_score,
                "confluence":    confluence,
                "db_trade_id":   trade_id,
                "leverage":      leverage,
            },
        )
    except Exception:
        pass

    return trade_id


def process_leg2(
    conn: sqlite3.Connection,
    trade: dict,
    current_price: float,
    ema_price: float,
) -> bool:
    """
    Process Leg 2 entry for a pending two-leg trade.

    Leg 2 triggers if:
    - Price pulls back to EMA within 3 candles → enter at EMA price
    - No pullback after 3 candles → enter at original entry price

    Returns True if Leg 2 was entered.
    """
    if not trade["leg2_pending"]:
        return False

    trade_id       = trade["id"]
    direction      = trade["direction"]
    symbol         = trade["symbol"]
    entry_price    = trade["entry_price"]
    qty_leg1       = trade["quantity_leg1"]
    qty_total      = trade["quantity"]
    candles_waited = trade["leg2_candles_waited"]

    qty_leg2  = round(qty_total * ENTRY["leg2_pct"], 6)
    max_wait  = ENTRY["leg2_candle_window"]

    # Check if pullback to EMA has occurred
    pullback_occurred = (
        (direction == "long"  and current_price <= ema_price) or
        (direction == "short" and current_price >= ema_price)
    )

    leg2_price = None

    if pullback_occurred:
        leg2_price = ema_price
        logger.info(f"Leg 2 triggered by EMA pullback: {symbol} at {leg2_price:.4f}")
    elif candles_waited >= max_wait:
        leg2_price = entry_price
        logger.info(f"Leg 2 triggered by timeout: {symbol} at {leg2_price:.4f}")

    if leg2_price is None:
        # Increment candle counter and wait
        conn.cursor().execute(
            "UPDATE trades SET leg2_candles_waited = ? WHERE id = ?",
            (candles_waited + 1, trade_id)
        )
        conn.commit()
        return False

    # Place Leg 2 order
    order2 = _place_order(symbol, direction, qty_leg2, "market", leg2_price)
    if not order2:
        logger.warning(f"Leg 2 order failed for {symbol} — trade continues with Leg 1 only")
        conn.cursor().execute(
            "UPDATE trades SET leg2_pending = 0 WHERE id = ?",
            (trade_id,)
        )
        conn.commit()
        return False

    fill_price_leg2 = order2.get("price", leg2_price)

    # Calculate new average entry price
    avg_entry = (
        (entry_price * qty_leg1) + (fill_price_leg2 * qty_leg2)
    ) / (qty_leg1 + qty_leg2)

    now = datetime.now(timezone.utc).isoformat()

    conn.cursor().execute("""
        UPDATE trades SET
            entry_price_leg2    = ?,
            avg_entry_price     = ?,
            quantity_remaining  = ?,
            leg2_pending        = 0,
            leg2_entry_time     = ?
        WHERE id = ?
    """, (fill_price_leg2, avg_entry, qty_leg1 + qty_leg2, now, trade_id))
    conn.commit()

    logger.info(
        f"Leg 2 entered: {symbol.replace('/USDT:USDT','')} | "
        f"Leg 2 price: {fill_price_leg2:.4f} | "
        f"Avg entry: {avg_entry:.4f}"
    )
    return True


# =============================================================================
# TRADE MANAGEMENT — TRAILING SL/TP
# =============================================================================

def update_trailing_sl(
    conn: sqlite3.Connection,
    trade: dict,
    current_price: float,
    current_atr: float,
) -> dict:
    """
    Update trailing stop loss based on current price.

    Rules:
    - At 1x risk in profit → move SL to breakeven
    - At 1.5x risk in profit → trail SL to lock 0.5x risk as profit
    - After primary TP hit → trail with 1x ATR

    Returns updated trade dict.
    """
    trade_id    = trade["id"]
    direction   = trade["direction"]
    avg_entry   = trade["avg_entry_price"]
    sl_distance = trade["sl_distance"]
    current_sl  = trade["trailing_sl"]
    at_breakeven = bool(trade["at_breakeven"])
    tp_hit      = bool(trade.get("tier2_tp_hit", 0))

    new_sl      = current_sl
    at_be       = at_breakeven

    if direction == "long":
        profit_dist = current_price - avg_entry

        # Move to breakeven at 1x risk
        if not at_breakeven and profit_dist >= sl_distance * TRAILING_SL["breakeven_at"]:
            new_sl = avg_entry
            at_be  = True
            logger.info(f"SL moved to breakeven: {trade['symbol'].replace('/USDT:USDT','')}")

        # Trail to lock 0.5x risk at 1.5x risk in profit
        if profit_dist >= sl_distance * TRAILING_SL["trail_at"]:
            lock_sl = avg_entry + (sl_distance * TRAILING_SL["trail_lock"])
            new_sl  = max(new_sl, lock_sl)

        # Trail with ATR after primary TP hit
        if tp_hit and current_atr > 0:
            atr_trail = current_price - (current_atr * TP["trail_atr_multiplier"])
            new_sl    = max(new_sl, atr_trail)

    else:  # short
        profit_dist = avg_entry - current_price

        # Move to breakeven
        if not at_breakeven and profit_dist >= sl_distance * TRAILING_SL["breakeven_at"]:
            new_sl = avg_entry
            at_be  = True
            logger.info(f"SL moved to breakeven: {trade['symbol'].replace('/USDT:USDT','')}")

        # Trail to lock profit
        if profit_dist >= sl_distance * TRAILING_SL["trail_at"]:
            lock_sl = avg_entry - (sl_distance * TRAILING_SL["trail_lock"])
            new_sl  = min(new_sl, lock_sl)

        # Trail with ATR after primary TP hit
        if tp_hit and current_atr > 0:
            atr_trail = current_price + (current_atr * TP["trail_atr_multiplier"])
            new_sl    = min(new_sl, atr_trail)

    # Update DB if SL changed
    if new_sl != current_sl or at_be != at_breakeven:
        conn.cursor().execute("""
            UPDATE trades SET trailing_sl = ?, at_breakeven = ? WHERE id = ?
        """, (new_sl, 1 if at_be else 0, trade_id))
        conn.commit()

    trade["trailing_sl"]  = new_sl
    trade["at_breakeven"] = 1 if at_be else 0
    return trade



def check_primary_tp(
    conn,
    trade: dict,
    current_high: float,
    current_low: float,
) -> bool:
    """
    Tiered TP exit strategy:
    - Tier 1: Close 40% at 1.5x RRR
    - Tier 2: Close 30% at 2.0x RRR
    - Remaining 30%: trail with 1.5x ATR
    """
    trade_id      = trade["id"]
    direction     = trade["direction"]
    avg_entry     = trade["avg_entry_price"]
    qty_remaining = trade["quantity_remaining"]
    sl_distance   = trade["sl_distance"]
    tier1_hit     = bool(trade.get("tier1_tp_hit", 0))
    tier2_hit     = bool(trade.get("tier2_tp_hit", 0))

    if tier2_hit:
        return False

    t1_pct = TP.get("tier1_close_pct", 0.40)
    t2_pct = TP.get("tier2_close_pct", 0.30)
    t1_rrr = TP.get("tier1_rrr", 1.5)
    t2_rrr = TP.get("tier2_rrr", 2.0)

    if direction == "long":
        tp1 = avg_entry + sl_distance * t1_rrr
        tp2 = avg_entry + sl_distance * t2_rrr
    else:
        tp1 = avg_entry - sl_distance * t1_rrr
        tp2 = avg_entry - sl_distance * t2_rrr

    close_side = "sell" if direction == "long" else "buy"
    hit_any    = False

    # Tier 1 — close 40% at 1.5x RRR
    if not tier1_hit:
        t1_hit = (
            (direction == "long"  and current_high >= tp1) or
            (direction == "short" and current_low  <= tp1)
        )
        if t1_hit:
            qty_close = round(qty_remaining * t1_pct, 6)
            order = _place_order(trade["symbol"], close_side, qty_close, "market", tp1)
            if order:
                pnl = ((tp1 - avg_entry) if direction == "long" else (avg_entry - tp1)) * qty_close
                new_qty = round(qty_remaining - qty_close, 6)
                conn.cursor().execute(
                    "UPDATE trades SET tier1_tp_hit=1, quantity_remaining=? WHERE id=?",
                    (new_qty, trade_id)
                )
                conn.commit()
                logger.info(f"Tier1 TP: {trade['symbol'].replace('/USDT:USDT','')} | 40% closed at {tp1:.4f} | PnL: {pnl:.2f} | 60% running...")
                trade["tier1_tp_hit"] = 1
                trade["quantity_remaining"] = new_qty
                qty_remaining = new_qty
                tier1_hit     = True
                hit_any       = True

    # Tier 2 — close 30% at 2x RRR
    if tier1_hit and not tier2_hit:
        t2_hit = (
            (direction == "long"  and current_high >= tp2) or
            (direction == "short" and current_low  <= tp2)
        )
        if t2_hit:
            qty_close = round(qty_remaining * (t2_pct / (1 - t1_pct + 0.001)), 6)
            order = _place_order(trade["symbol"], close_side, qty_close, "market", tp2)
            if order:
                pnl = ((tp2 - avg_entry) if direction == "long" else (avg_entry - tp2)) * qty_close
                new_qty = round(qty_remaining - qty_close, 6)
                conn.cursor().execute(
                    "UPDATE trades SET tier2_tp_hit=1, quantity_remaining=? WHERE id=?",
                    (new_qty, trade_id)
                )
                conn.commit()
                logger.info(f"Tier2 TP: {trade['symbol'].replace('/USDT:USDT','')} | 30% closed at {tp2:.4f} | PnL: {pnl:.2f} | 30% trailing...")
                hit_any = True

    return hit_any

def close_trade(
    conn: sqlite3.Connection,
    trade: dict,
    exit_price: float,
    reason: str,
    cooldown_tracker: dict = None,
) -> dict:
    """
    Close a trade and record final PnL.

    Args:
        conn:             Database connection
        trade:            Trade dict from get_open_trades()
        exit_price:       Price at which trade exits
        reason:           'stop_loss', 'take_profit', 'time_stop', 'manual'
        cooldown_tracker: Optional dict to update cooldown after SL hit

    Returns updated trade dict with PnL.
    """
    trade_id      = trade["id"]
    direction     = trade["direction"]
    symbol        = trade["symbol"]
    avg_entry     = trade["avg_entry_price"]
    qty_remaining = trade["quantity_remaining"]
    leverage      = trade["leverage"]
    position_usdt = trade["position_size_usdt"]
    tp_hit        = bool(trade["tier1_tp_hit"])

    # Place closing order
    close_side = "sell" if direction == "long" else "buy"
    order = _place_order(symbol, close_side, qty_remaining, "market", exit_price)

    if not order and not PAPER_TRADING:
        logger.error(f"Close order failed for {symbol} — retrying...")
        order = _place_order(symbol, close_side, qty_remaining, "market", exit_price)

    # qty already includes leverage (pos_usdt * leverage / entry) — do NOT multiply again
    if direction == "long":
        remaining_pnl = (exit_price - avg_entry) * qty_remaining
    else:
        remaining_pnl = (avg_entry - exit_price) * qty_remaining
    # Total PnL includes any partial TP already taken
    # (partial TP PnL was recorded when tier1_tp_hit was set)
    total_pnl     = remaining_pnl
    pnl_pct       = (total_pnl / position_usdt) * 100

    now = datetime.now(timezone.utc).isoformat()

    conn.cursor().execute("""
        UPDATE trades SET
            status       = 'closed',
            exit_price   = ?,
            exit_reason  = ?,
            pnl_usdt     = ?,
            pnl_pct      = ?,
            exit_time    = ?
        WHERE id = ?
    """, (exit_price, reason, round(total_pnl, 4), round(pnl_pct, 4), now, trade_id))
    conn.commit()

    # Update capital
    capital = get_capital(conn)
    new_capital = capital + total_pnl
    update_capital(conn, new_capital)

    # Set cooldown after SL hit
    if reason == "stop_loss" and cooldown_tracker is not None:
        from bot.filters import set_cooldown
        set_cooldown(cooldown_tracker, symbol, trade["timeframe"])

    logger.info(
        f"Trade closed: {symbol.replace('/USDT:USDT','')} "
        f"{direction.upper()} | "
        f"Exit: {exit_price:.4f} | "
        f"PnL: {total_pnl:+.2f} USDT ({pnl_pct:+.2f}%) | "
        f"Reason: {reason}"
    )

    try:
        from bot.config import apex_logger
        apex_logger.trade_exit(
            trade_id         = str(trade["id"]),
            token            = symbol,
            side             = direction,
            exit_price       = exit_price,
            entry_price      = avg_entry,
            exit_reason      = reason,
            pnl_usd          = round(total_pnl, 4),
            pnl_pct          = round(pnl_pct, 4),
            duration_candles = trade.get("candles_open", 0),
            strategy         = trade.get("tier", "unknown"),
            tier             = trade.get("tier", "unknown"),
            timeframe        = trade.get("timeframe", "1h"),
            market_at_exit   = {},
        )
    except Exception:
        pass

    trade["exit_price"]  = exit_price
    trade["exit_reason"] = reason
    trade["pnl_usdt"]    = total_pnl
    trade["pnl_pct"]     = pnl_pct
    trade["status"]      = "closed"

    return trade


# =============================================================================
# TRADE MONITORING — CHECK ALL OPEN TRADES
# =============================================================================

def monitor_open_trades(
    conn: sqlite3.Connection,
    open_trades: list,
    ohlcv_data: dict,
    cooldown_tracker: dict,
) -> tuple:
    """
    Monitor all open trades for SL hits, TP hits, and time stops.
    Called every bot cycle.

    Returns (closed_trades, updated_cooldown_tracker)
    """
    closed_trades = []

    for trade in open_trades:
        symbol    = trade["symbol"]
        timeframe = trade["timeframe"]
        df        = ohlcv_data.get(symbol)

        if df is None or df.empty:
            logger.warning(f"No OHLCV data for open trade {symbol} — skipping monitor")
            continue

        latest        = df.iloc[-1]
        current_price = latest["close"]
        current_high  = latest["high"]
        current_low   = latest["low"]
        current_atr   = latest.get("atr", trade["atr_at_entry"])
        ema_price     = latest.get("ema_fast", current_price)

        direction   = trade["direction"]
        trailing_sl = trade["trailing_sl"]

        try:
            # Process Leg 2 if pending
            if trade.get("leg2_pending"):
                process_leg2(conn, trade, current_price, ema_price)
                trade = get_trade(conn, trade["id"])  # Refresh

            # Update trailing SL
            trade = update_trailing_sl(conn, trade, current_price, current_atr)

            # Check primary TP
            check_primary_tp(conn, trade, current_high, current_low)
            trade = get_trade(conn, trade["id"])  # Refresh after TP check

            # Increment candle counter
            conn.cursor().execute(
                "UPDATE trades SET candles_open = candles_open + 1 WHERE id = ?",
                (trade["id"],)
            )
            conn.commit()
            trade = get_trade(conn, trade["id"])

            # Check SL hit
            sl_hit = (
                (direction == "long"  and current_low  <= trade["trailing_sl"]) or
                (direction == "short" and current_high >= trade["trailing_sl"])
            )

            if sl_hit:
                closed = close_trade(
                    conn, trade, trade["trailing_sl"],
                    "stop_loss", cooldown_tracker
                )
                closed_trades.append(closed)
                continue

            # Check time stop
            time_stop = TIME_STOP_CANDLES.get(timeframe, 30)
            if trade["candles_open"] >= time_stop:
                closed = close_trade(
                    conn, trade, current_price,
                    "time_stop", cooldown_tracker
                )
                closed_trades.append(closed)
                continue

            # Check if remaining 30% hit trailing SL after primary TP
            if trade.get("tier2_tp_hit", 0):
                trail_sl_hit = (
                    (direction == "long"  and current_low  <= trade["trailing_sl"]) or
                    (direction == "short" and current_high >= trade["trailing_sl"])
                )
                if trail_sl_hit:
                    closed = close_trade(
                        conn, trade, trade["trailing_sl"],
                        "take_profit", cooldown_tracker
                    )
                    closed_trades.append(closed)

        except Exception as e:
            logger.error(f"Error monitoring trade {trade['id']} {symbol}: {e}", exc_info=True)

    return closed_trades, cooldown_tracker


# =============================================================================
# PERFORMANCE STATS — Used by dashboard
# =============================================================================

def get_performance_stats(conn: sqlite3.Connection) -> dict:
    """Calculate performance statistics from closed trades."""
    c = conn.cursor()

    c.execute("""
        SELECT pnl_usdt, pnl_pct FROM trades WHERE status = 'closed'
    """)
    rows = c.fetchall()

    if not rows:
        capital = get_capital(conn)
        return {
            "capital":     capital,
            "total_pnl":   0,
            "total_trades": 0,
            "win_rate":    0,
            "expectancy":  0,
            "avg_win":     0,
            "avg_loss":    0,
            "drawdown":    0,
        }

    pnls    = [r[0] for r in rows]
    wins    = [p for p in pnls if p > 0]
    losses  = [p for p in pnls if p <= 0]

    capital    = get_capital(conn)
    total_pnl  = sum(pnls)
    win_rate   = len(wins) / len(pnls) * 100 if pnls else 0
    avg_win    = sum(wins) / len(wins) if wins else 0
    avg_loss   = sum(losses) / len(losses) if losses else 0
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    # Drawdown from peak
    import numpy as np
    cumulative = np.cumsum(pnls)
    peak       = np.maximum.accumulate(cumulative + INITIAL_CAPITAL)
    current    = cumulative[-1] + INITIAL_CAPITAL
    peak_val   = peak[-1]
    drawdown   = (peak_val - current) / peak_val * 100 if peak_val > 0 else 0

    return {
        "capital":      round(capital, 2),
        "total_pnl":    round(total_pnl, 2),
        "total_trades": len(pnls),
        "win_rate":     round(win_rate, 1),
        "expectancy":   round(expectancy, 2),
        "avg_win":      round(avg_win, 2),
        "avg_loss":     round(avg_loss, 2),
        "drawdown":     round(drawdown, 2),
    }


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    conn = get_trades_conn()
    init_trades_db(conn)

    stats = get_performance_stats(conn)
    print(f"Performance stats: {stats}")

    open_trades = get_open_trades(conn)
    print(f"Open trades: {len(open_trades)}")

    conn.close()

# __APEX_LOGGER_V1__
