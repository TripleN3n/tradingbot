# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/trade_manager.py — Trade Execution & Management
# Version 3.7 — Exit-type based cooldown
# =============================================================================
# CHANGES FROM v3.6:
#
# close_trade():
#   FIXED: Cooldown now applies to ALL exit types including take_profit.
#   Previously only stop_loss and time_stop triggered cooldown.
#   Updated set_cooldown() call to pass exit reason instead of timeframe —
#   cooldown duration is now determined by exit type (v3.7 filters.py):
#     stop_loss:   4 candles
#     time_stop:   2 candles
#     take_profit: 1 candle
#
# Version 3.6 changes retained:
#   Time stop now time-elapsed based (TIME_STOP_HOURS).
#   Cooldown fires after both stop_loss and time_stop.
#
# Version 3.2 changes retained:
#   partial_pnl_usdt column and TP accounting fixes.
# =============================================================================

import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.config import (
    PAPER_TRADING, INITIAL_CAPITAL, DB,
    TRAILING_SL, TP, ENTRY,
    FILTERS, LOGS,
)
from bot.data_feed import get_exchange

logger = logging.getLogger(__name__)

# =============================================================================
# TIME STOP DURATION MAP (v3.6)
# =============================================================================

TIME_STOP_HOURS = {
    "1h":  30,
    "4h":  120,
    "1d":  240,
}


# =============================================================================
# DATABASE SETUP
# =============================================================================

def init_trades_db(conn: sqlite3.Connection):
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol              TEXT NOT NULL,
            direction           TEXT NOT NULL,
            tier                TEXT NOT NULL,
            timeframe           TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'open',
            entry_price         REAL NOT NULL,
            entry_price_leg2    REAL,
            avg_entry_price     REAL NOT NULL,
            quantity            REAL NOT NULL,
            quantity_leg1       REAL NOT NULL,
            quantity_leg2       REAL,
            position_size_usdt  REAL NOT NULL,
            leverage            INTEGER NOT NULL,
            stop_loss           REAL NOT NULL,
            take_profit         REAL NOT NULL,
            sl_distance         REAL NOT NULL,
            atr_at_entry        REAL NOT NULL,
            rrr                 REAL NOT NULL,
            trailing_sl         REAL NOT NULL,
            at_breakeven        INTEGER DEFAULT 0,
            tier1_tp_hit        INTEGER DEFAULT 0,
            tier2_tp_hit        INTEGER DEFAULT 0,
            quantity_remaining  REAL NOT NULL,
            exit_price          REAL,
            exit_reason         TEXT,
            pnl_usdt            REAL,
            pnl_pct             REAL,
            partial_pnl_usdt    REAL DEFAULT 0,
            signal_score        REAL,
            confluence_count    INTEGER,
            candles_open        INTEGER DEFAULT 0,
            entry_time          TEXT NOT NULL,
            leg2_entry_time     TEXT,
            exit_time           TEXT,
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

    try:
        c.execute("ALTER TABLE trades ADD COLUMN partial_pnl_usdt REAL DEFAULT 0")
        conn.commit()
        logger.info("Trades DB: added partial_pnl_usdt column (schema migration)")
    except Exception:
        pass

    try:
        c.execute("ALTER TABLE trades ADD COLUMN last_candle_ts TEXT DEFAULT NULL")
        conn.commit()
        logger.info("Trades DB: added last_candle_ts column (schema migration)")
    except Exception:
        pass
    logger.info("Trades database initialized")


def get_trades_conn() -> sqlite3.Connection:
    from pathlib import Path
    Path(DB["trades"]).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB["trades"], check_same_thread=False)


# =============================================================================
# TRADE QUERIES
# =============================================================================

def get_open_trades(conn: sqlite3.Connection) -> list:
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE status = 'open'")
    columns = [d[0] for d in c.description]
    return [dict(zip(columns, row)) for row in c.fetchall()]


def get_trade(conn: sqlite3.Connection, trade_id: int) -> Optional[dict]:
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    row = c.fetchone()
    if not row:
        return None
    columns = [d[0] for d in c.description]
    return dict(zip(columns, row))


def get_closed_trades(conn: sqlite3.Connection, limit: int = 100) -> list:
    c = conn.cursor()
    c.execute(
        "SELECT * FROM trades WHERE status = 'closed' ORDER BY exit_time DESC LIMIT ?",
        (limit,)
    )
    columns = [d[0] for d in c.description]
    return [dict(zip(columns, row)) for row in c.fetchall()]


def get_capital(conn: sqlite3.Connection) -> float:
    c = conn.cursor()
    c.execute("SELECT capital FROM portfolio ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    return row[0] if row else INITIAL_CAPITAL


def update_capital(conn: sqlite3.Connection, capital: float, deployed: float = 0):
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
    if PAPER_TRADING:
        return {
            "id":     f"paper_{datetime.now(timezone.utc).timestamp()}",
            "price":  price,
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
    if PAPER_TRADING:
        return
    try:
        exchange = get_exchange()
        exchange.set_leverage(leverage, symbol)
    except Exception as e:
        logger.warning(f"Leverage set failed for {symbol}: {e}")


# =============================================================================
# TRADE ENTRY
# =============================================================================

def open_trade(
    conn: sqlite3.Connection,
    signal: dict,
) -> Optional[int]:
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

    _set_leverage(symbol, leverage)

    qty_leg1 = round(quantity_total * ENTRY["leg1_pct"], 6)

    order1 = _place_order(symbol, direction, qty_leg1, "market", entry_price)
    if not order1:
        logger.error(f"Leg 1 order failed for {symbol}")
        return None

    fill_price_leg1 = order1.get("price", entry_price)
    now             = datetime.now(timezone.utc).isoformat()

    c = conn.cursor()
    c.execute("""
        INSERT INTO trades (
            symbol, direction, tier, timeframe, status,
            entry_price, avg_entry_price,
            quantity, quantity_leg1, quantity_remaining,
            position_size_usdt, leverage,
            stop_loss, take_profit, sl_distance, atr_at_entry, rrr,
            trailing_sl, at_breakeven,
            partial_pnl_usdt,
            signal_score, confluence_count,
            entry_time, leg2_pending, leg2_candles_waited
        ) VALUES (
            ?, ?, ?, ?, 'open',
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?, ?,
            ?, 0,
            0,
            ?, ?,
            ?, 1, 0
        )
    """, (
        symbol, direction, tier, timeframe,
        fill_price_leg1, fill_price_leg1,
        quantity_total, qty_leg1, qty_leg1,
        position_usdt, leverage,
        stop_loss, take_profit, sl_distance, atr, rrr,
        stop_loss,
        signal_score, confluence,
        now,
    ))

    trade_id = c.lastrowid
    conn.commit()

    logger.info(
        f"Trade opened: {symbol.replace('/USDT:USDT', '')} "
        f"{direction.upper()} | {tier} | {timeframe} | "
        f"Entry: {fill_price_leg1:.4f} | "
        f"SL: {stop_loss:.4f} ({sl_distance / fill_price_leg1 * 100:.2f}%) | "
        f"TP: {take_profit:.4f} | RRR: {rrr:.2f} | "
        f"Qty: {qty_leg1} (Leg 1 of 2) | ID: {trade_id}"
    )

    try:
        from bot.config import apex_logger
        _tid = f"{symbol.replace('/USDT:USDT', '')}_{now[:10].replace('-', '')}_{now[11:16].replace(':', '')}"
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
                "signal_score": signal_score,
                "confluence":   confluence,
                "db_trade_id":  trade_id,
                "leverage":     leverage,
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
        conn.cursor().execute(
            "UPDATE trades SET leg2_candles_waited = ? WHERE id = ?",
            (candles_waited + 1, trade_id)
        )
        conn.commit()
        return False

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
        f"Leg 2 entered: {symbol.replace('/USDT:USDT', '')} | "
        f"Leg 2 price: {fill_price_leg2:.4f} | "
        f"Avg entry: {avg_entry:.4f}"
    )
    return True


# =============================================================================
# TRAILING SL/TP
# =============================================================================

def update_trailing_sl(
    conn: sqlite3.Connection,
    trade: dict,
    current_price: float,
    current_atr: float,
) -> dict:
    trade_id     = trade["id"]
    direction    = trade["direction"]
    avg_entry    = trade["avg_entry_price"]
    sl_distance  = trade["sl_distance"]
    current_sl   = trade["trailing_sl"]
    at_breakeven = bool(trade["at_breakeven"])
    tp_hit       = bool(trade.get("tier2_tp_hit", 0))

    new_sl = current_sl
    at_be  = at_breakeven

    if direction == "long":
        profit_dist = current_price - avg_entry

        if not at_breakeven and profit_dist >= sl_distance * TRAILING_SL["breakeven_at"]:
            new_sl = avg_entry
            at_be  = True
            logger.info(f"SL moved to breakeven: {trade['symbol'].replace('/USDT:USDT', '')}")

        if profit_dist >= sl_distance * TRAILING_SL["trail_at"]:
            lock_sl = avg_entry + (sl_distance * TRAILING_SL["trail_lock"])
            new_sl  = max(new_sl, lock_sl)

        if tp_hit and current_atr > 0:
            atr_trail = current_price - (current_atr * TP["trail_atr_multiplier"])
            new_sl    = max(new_sl, atr_trail)

    else:
        profit_dist = avg_entry - current_price

        if not at_breakeven and profit_dist >= sl_distance * TRAILING_SL["breakeven_at"]:
            new_sl = avg_entry
            at_be  = True
            logger.info(f"SL moved to breakeven: {trade['symbol'].replace('/USDT:USDT', '')}")

        if profit_dist >= sl_distance * TRAILING_SL["trail_at"]:
            lock_sl = avg_entry - (sl_distance * TRAILING_SL["trail_lock"])
            new_sl  = min(new_sl, lock_sl)

        if tp_hit and current_atr > 0:
            atr_trail = current_price + (current_atr * TP["trail_atr_multiplier"])
            new_sl    = min(new_sl, atr_trail)

    if new_sl != current_sl or at_be != at_breakeven:
        conn.cursor().execute("""
            UPDATE trades SET trailing_sl = ?, at_breakeven = ? WHERE id = ?
        """, (new_sl, 1 if at_be else 0, trade_id))
        conn.commit()

    trade["trailing_sl"]  = new_sl
    trade["at_breakeven"] = 1 if at_be else 0
    return trade


def check_primary_tp(
    conn: sqlite3.Connection,
    trade: dict,
    current_high: float,
    current_low: float,
) -> bool:
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

    if not tier1_hit:
        t1_hit = (
            (direction == "long"  and current_high >= tp1) or
            (direction == "short" and current_low  <= tp1)
        )
        if t1_hit:
            qty_close = round(qty_remaining * t1_pct, 6)
            order = _place_order(trade["symbol"], close_side, qty_close, "market", tp1)
            if order:
                pnl     = ((tp1 - avg_entry) if direction == "long" else (avg_entry - tp1)) * qty_close
                new_qty = round(qty_remaining - qty_close, 6)

                conn.cursor().execute("""
                    UPDATE trades SET
                        tier1_tp_hit       = 1,
                        quantity_remaining = ?,
                        partial_pnl_usdt   = COALESCE(partial_pnl_usdt, 0) + ?
                    WHERE id = ?
                """, (new_qty, round(pnl, 4), trade_id))
                conn.commit()

                capital = get_capital(conn)
                update_capital(conn, capital + pnl)

                logger.info(
                    f"Stage1 TP: {trade['symbol'].replace('/USDT:USDT', '')} | "
                    f"40% closed at {tp1:.4f} | "
                    f"PnL: +{pnl:.2f} USDT | "
                    f"Remaining: {new_qty:.6f} ({int((1 - t1_pct) * 100)}%)"
                )

                trade["tier1_tp_hit"]       = 1
                trade["quantity_remaining"]  = new_qty
                trade["partial_pnl_usdt"]   = trade.get("partial_pnl_usdt", 0) + pnl
                qty_remaining                = new_qty
                tier1_hit                    = True
                hit_any                      = True

    if tier1_hit and not tier2_hit:
        t2_hit = (
            (direction == "long"  and current_high >= tp2) or
            (direction == "short" and current_low  <= tp2)
        )
        if t2_hit:
            qty_close = round(qty_remaining * (t2_pct / (1 - t1_pct + 0.001)), 6)
            order = _place_order(trade["symbol"], close_side, qty_close, "market", tp2)
            if order:
                pnl     = ((tp2 - avg_entry) if direction == "long" else (avg_entry - tp2)) * qty_close
                new_qty = round(qty_remaining - qty_close, 6)

                conn.cursor().execute("""
                    UPDATE trades SET
                        tier2_tp_hit       = 1,
                        quantity_remaining = ?,
                        partial_pnl_usdt   = COALESCE(partial_pnl_usdt, 0) + ?
                    WHERE id = ?
                """, (new_qty, round(pnl, 4), trade_id))
                conn.commit()

                capital = get_capital(conn)
                update_capital(conn, capital + pnl)

                logger.info(
                    f"Stage2 TP: {trade['symbol'].replace('/USDT:USDT', '')} | "
                    f"30% closed at {tp2:.4f} | "
                    f"PnL: +{pnl:.2f} USDT | "
                    f"Remaining: {new_qty:.6f} (30% trailing)"
                )
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

    FIX v3.7: Cooldown now applies to ALL exit types including take_profit.
    set_cooldown() receives exit reason instead of timeframe — duration is
    determined by exit type:
        stop_loss:   4 candles
        time_stop:   2 candles
        take_profit: 1 candle

    FIX v3.6: Cooldown fires after stop_loss AND time_stop (previously SL only).
    Time stop is time-elapsed based via TIME_STOP_HOURS.
    """
    trade_id      = trade["id"]
    direction     = trade["direction"]
    symbol        = trade["symbol"]
    avg_entry     = trade["avg_entry_price"]
    qty_remaining = trade["quantity_remaining"]
    position_usdt = trade["position_size_usdt"]

    close_side = "sell" if direction == "long" else "buy"
    order = _place_order(symbol, close_side, qty_remaining, "market", exit_price)

    if not order and not PAPER_TRADING:
        logger.error(f"Close order failed for {symbol} — retrying...")
        order = _place_order(symbol, close_side, qty_remaining, "market", exit_price)

    if direction == "long":
        remaining_pnl = (exit_price - avg_entry) * qty_remaining
    else:
        remaining_pnl = (avg_entry - exit_price) * qty_remaining

    partial_pnl = trade.get("partial_pnl_usdt", 0) or 0
    total_pnl   = remaining_pnl + partial_pnl
    pnl_pct     = (total_pnl / position_usdt) * 100

    now = datetime.now(timezone.utc).isoformat()

    conn.cursor().execute("""
        UPDATE trades SET
            status      = 'closed',
            exit_price  = ?,
            exit_reason = ?,
            pnl_usdt    = ?,
            pnl_pct     = ?,
            exit_time   = ?
        WHERE id = ?
    """, (exit_price, reason, round(total_pnl, 4), round(pnl_pct, 4), now, trade_id))
    conn.commit()

    capital     = get_capital(conn)
    new_capital = capital + remaining_pnl
    update_capital(conn, new_capital)

    # FIX v3.7: Cooldown applies to all exit types.
    # Pass reason to set_cooldown() — duration determined by exit type.
    if cooldown_tracker is not None:
        from bot.filters import set_cooldown
        set_cooldown(cooldown_tracker, symbol, reason)

    logger.info(
        f"Trade closed: {symbol.replace('/USDT:USDT', '')} "
        f"{direction.upper()} | "
        f"Exit: {exit_price:.4f} | "
        f"Partial TP PnL: {partial_pnl:+.2f} | "
        f"Remaining PnL: {remaining_pnl:+.2f} | "
        f"Total PnL: {total_pnl:+.2f} USDT ({pnl_pct:+.2f}%) | "
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
# TRADE MONITORING
# =============================================================================

def monitor_open_trades(
    conn: sqlite3.Connection,
    open_trades: list,
    ohlcv_data: dict,
    cooldown_tracker: dict,
) -> tuple:
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
        direction     = trade["direction"]

        try:
            if trade.get("leg2_pending"):
                process_leg2(conn, trade, current_price, ema_price)
                trade = get_trade(conn, trade["id"])

            trade = update_trailing_sl(conn, trade, current_price, current_atr)

            check_primary_tp(conn, trade, current_high, current_low)
            trade = get_trade(conn, trade["id"])

            _tf_hours     = {"1h": 1, "4h": 4, "1d": 24}
            _ch           = _tf_hours.get(timeframe, 1)
            _now          = datetime.now(timezone.utc)
            _floor_h      = (_now.hour // _ch) * _ch
            _candle_floor = _now.replace(hour=_floor_h, minute=0, second=0, microsecond=0).isoformat()
            _last_ts      = trade.get("last_candle_ts") or ""
            if _candle_floor != _last_ts:
                conn.cursor().execute(
                    "UPDATE trades SET candles_open = candles_open + 1, last_candle_ts = ? WHERE id = ?",
                    (_candle_floor, trade["id"])
                )
                conn.commit()
            trade = get_trade(conn, trade["id"])

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

            # FIX v3.6: Time stop uses elapsed wall-clock hours
            entry_time       = datetime.fromisoformat(trade["entry_time"])
            elapsed_hours    = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600
            time_limit_hours = TIME_STOP_HOURS.get(timeframe, 30)

            if elapsed_hours >= time_limit_hours:
                logger.info(
                    f"Time stop: {symbol.replace('/USDT:USDT', '')} | "
                    f"{timeframe} | Elapsed: {elapsed_hours:.1f}h / {time_limit_hours}h"
                )
                closed = close_trade(
                    conn, trade, current_price,
                    "time_stop", cooldown_tracker
                )
                closed_trades.append(closed)
                continue

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
# PERFORMANCE STATS
# =============================================================================

def get_performance_stats(conn: sqlite3.Connection) -> dict:
    c = conn.cursor()

    c.execute("SELECT pnl_usdt, pnl_pct FROM trades WHERE status = 'closed'")
    rows = c.fetchall()

    if not rows:
        capital = get_capital(conn)
        return {
            "capital":      capital,
            "total_pnl":    0,
            "total_trades": 0,
            "win_rate":     0,
            "expectancy":   0,
            "avg_win":      0,
            "avg_loss":     0,
            "drawdown":     0,
        }

    pnls   = [r[0] for r in rows]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    capital    = get_capital(conn)
    total_pnl  = sum(pnls)
    win_rate   = len(wins) / len(pnls) * 100 if pnls else 0
    avg_win    = sum(wins) / len(wins) if wins else 0
    avg_loss   = sum(losses) / len(losses) if losses else 0
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

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
