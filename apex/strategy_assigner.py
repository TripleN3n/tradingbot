# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# apex/strategy_assigner.py — Strategy Assignment
# Version 3.8 — strategy_name, indicator_combo, daily_volume_usd fixes
# =============================================================================
# CHANGES FROM v3.2:
#
# _get_strategy_name():
#   New helper. Maps backtest strategy_type to one of the 5 APEX strategy
#   names used in the dashboard and reference doc:
#     mtf_trend / mtf_trend_4h → Momentum Flow
#     single_tf                → Trend Breakout
#     mean_reversion           → Volatility Surge
#     breakout                 → Alpha Confluence
#     other                    → Momentum Squeeze
#
# _get_indicator_combo():
#   New helper. Produces a short readable string of confirmation indicators
#   (excluding mandatory ema/macro_ref) e.g. "macd+rsi+volume".
#   Stored in indicator_combo column for dashboard display.
#
# init_strategy_db():
#   Added strategy_name TEXT and indicator_combo TEXT to CREATE TABLE.
#   Added safe ALTER TABLE migrations for both columns so existing DBs
#   (APEX) upgrade automatically without data loss.
#
# assign_strategy_for_token():
#   INSERT now populates strategy_name and indicator_combo.
#   ON CONFLICT UPDATE also keeps them in sync on rebalance.
#
# get_all_active_strategies():
#   LEFT JOIN with universe table to fetch daily_volume_usd per token.
#   Previously this was missing — the liquidity filter in signal_engine.py
#   always received 999,999,999 and never actually filtered by volume.
#   Also returns strategy_name in each strategy dict.
#
# Version 3.2 changes retained:
#   _preserve_or_deactivate() — existing assignments kept when no new
#   strategy found, preventing rebalance from wiping working assignments.
# =============================================================================

import sqlite3
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent.parent))
from bot.config import DB, LOGS, TIERS, TIMEFRAMES

logger = logging.getLogger(__name__)


# =============================================================================
# STRATEGY NAME MAPPING
# Maps backtest engine strategy_type to one of the 5 APEX strategy names.
# =============================================================================

_STRATEGY_TYPE_TO_NAME = {
    "mtf_trend":      "Momentum Flow",
    "mtf_trend_4h":   "Momentum Flow",
    "single_tf":      "Trend Breakout",
    "mean_reversion": "Volatility Surge",
    "breakout":       "Alpha Confluence",
}


def _get_strategy_name(strategy_type: str) -> str:
    """Map backtest strategy_type to APEX strategy name."""
    return _STRATEGY_TYPE_TO_NAME.get(strategy_type, "Momentum Squeeze")


def _get_indicator_combo(indicators: list) -> str:
    """
    Build short readable indicator combo string for dashboard display.
    Excludes mandatory ema/macro_ref — shows only confirmation indicators.
    e.g. ["ema", "macro_ref", "rsi", "macd"] → "macd+rsi"
    """
    confirmation = [i for i in indicators if i not in ("ema", "macro_ref")]
    return "+".join(sorted(confirmation)) if confirmation else "ema_only"


# =============================================================================
# DATABASE SETUP
# =============================================================================

def init_strategy_db(conn: sqlite3.Connection):
    """
    Create strategy assignment tables if they don't exist.
    Safe to call multiple times.
    Runs safe ALTER TABLE migrations for new columns on existing DBs.
    """
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS strategy_assignments (
            symbol              TEXT PRIMARY KEY,
            timeframe           TEXT NOT NULL,
            tier                TEXT NOT NULL,
            indicators          TEXT NOT NULL,
            min_confluence      INTEGER NOT NULL,
            tier_rrr            REAL NOT NULL,
            composite_score     REAL NOT NULL,
            win_rate            REAL NOT NULL,
            expectancy          REAL NOT NULL,
            profit_factor       REAL NOT NULL,
            max_drawdown        REAL NOT NULL,
            sharpe_ratio        REAL NOT NULL,
            train_trades        INTEGER NOT NULL,
            val_trades          INTEGER NOT NULL,
            overfitting_gap     REAL NOT NULL,
            assigned_at         TEXT NOT NULL,
            source              TEXT NOT NULL,
            is_active           INTEGER DEFAULT 1,
            strategy_name       TEXT,
            indicator_combo     TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS strategy_history (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol              TEXT NOT NULL,
            timeframe           TEXT NOT NULL,
            tier                TEXT NOT NULL,
            indicators          TEXT NOT NULL,
            min_confluence      INTEGER NOT NULL,
            tier_rrr            REAL NOT NULL,
            composite_score     REAL NOT NULL,
            win_rate            REAL NOT NULL,
            expectancy          REAL NOT NULL,
            profit_factor       REAL NOT NULL,
            max_drawdown        REAL NOT NULL,
            sharpe_ratio        REAL NOT NULL,
            train_trades        INTEGER NOT NULL,
            val_trades          INTEGER NOT NULL,
            overfitting_gap     REAL NOT NULL,
            assigned_at         TEXT NOT NULL,
            replaced_at         TEXT,
            source              TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS token_status (
            symbol              TEXT PRIMARY KEY,
            is_tradeable        INTEGER DEFAULT 0,
            is_paused           INTEGER DEFAULT 0,
            pause_reason        TEXT,
            paused_at           TEXT,
            last_updated        TEXT NOT NULL
        )
    """)

    conn.commit()

    # Safe migrations — add new columns to existing DBs without data loss
    for col, definition in [
        ("strategy_name",   "TEXT"),
        ("indicator_combo", "TEXT"),
    ]:
        try:
            c.execute(
                f"ALTER TABLE strategy_assignments ADD COLUMN {col} {definition}"
            )
            conn.commit()
            logger.info(f"strategy_assignments: added {col} column (migration)")
        except Exception:
            pass  # Column already exists

    logger.info("Strategy database tables initialized")


# =============================================================================
# STRATEGY ASSIGNMENT
# =============================================================================

def assign_strategy_for_token(
    conn: sqlite3.Connection,
    symbol: str,
    scored_results: list,
    source: str = "rebalance",
) -> bool:
    """
    Assign the best scored strategy to a token.
    Atomically replaces any existing assignment.
    Archives old strategy to history table before replacing.

    FIX v3.8: Now populates strategy_name and indicator_combo columns.

    FIX v3.2: When no valid strategy found, existing assignment is
    preserved instead of being deactivated.

    Returns True if a new assignment was made, False if none found.
    """
    if not scored_results:
        logger.warning(f"No valid strategy to assign for {symbol}")
        _preserve_or_deactivate(conn, symbol)
        return False

    best            = scored_results[0]
    vm              = best["val_metrics"]
    now             = datetime.now(timezone.utc).isoformat()
    strategy_name   = _get_strategy_name(best.get("strategy_type", ""))
    indicator_combo = _get_indicator_combo(best.get("indicators", []))

    c = conn.cursor()

    # Archive existing assignment to history before replacing
    _archive_existing_assignment(conn, symbol, now)

    # Write new assignment
    c.execute("""
        INSERT INTO strategy_assignments (
            symbol, timeframe, tier, indicators, min_confluence,
            tier_rrr, composite_score, win_rate, expectancy,
            profit_factor, max_drawdown, sharpe_ratio,
            train_trades, val_trades, overfitting_gap,
            assigned_at, source, is_active,
            strategy_name, indicator_combo
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            timeframe       = excluded.timeframe,
            tier            = excluded.tier,
            indicators      = excluded.indicators,
            min_confluence  = excluded.min_confluence,
            tier_rrr        = excluded.tier_rrr,
            composite_score = excluded.composite_score,
            win_rate        = excluded.win_rate,
            expectancy      = excluded.expectancy,
            profit_factor   = excluded.profit_factor,
            max_drawdown    = excluded.max_drawdown,
            sharpe_ratio    = excluded.sharpe_ratio,
            train_trades    = excluded.train_trades,
            val_trades      = excluded.val_trades,
            overfitting_gap = excluded.overfitting_gap,
            assigned_at     = excluded.assigned_at,
            source          = excluded.source,
            is_active       = 1,
            strategy_name   = excluded.strategy_name,
            indicator_combo = excluded.indicator_combo
    """, (
        symbol,
        best["timeframe"],
        best["assigned_tier"],
        json.dumps(best["indicators"]),
        best["min_confluence"],
        best["tier_rrr"],
        best["composite_score"],
        vm["win_rate"],
        vm["expectancy"],
        vm["profit_factor"],
        vm["max_drawdown"],
        vm["sharpe_ratio"],
        best["train_trades"],
        best["val_trades"],
        best["overfitting_gap"],
        now,
        source,
        strategy_name,
        indicator_combo,
    ))

    _update_token_status(conn, symbol, tradeable=True, paused=False)
    conn.commit()

    try:
        from bot.config import apex_logger
        apex_logger.strategy_assigned(
            token             = symbol,
            strategy          = strategy_name,
            timeframe         = best["timeframe"],
            tier              = best.get("assigned_tier", best.get("tier", "unknown")),
            metrics           = vm,
            assignment_reason = source,
        )
    except Exception:
        pass

    logger.info(
        f"Strategy assigned: {symbol.replace('/USDT:USDT', '')} | "
        f"{best['timeframe']} | {best['assigned_tier']} | "
        f"{strategy_name} ({indicator_combo}) | "
        f"Score: {best['composite_score']:.4f} | "
        f"WR: {vm['win_rate']:.1%} | "
        f"Exp: {vm['expectancy']:.4f} | "
        f"PF: {vm['profit_factor']:.2f}"
    )

    return True


def assign_strategies_for_all_tokens(
    conn: sqlite3.Connection,
    scored_all: dict,
    source: str = "rebalance",
) -> dict:
    """
    Assign strategies for all tokens in one pass.

    Returns dict:
        assigned:   list of symbols with a new strategy assigned
        preserved:  list of symbols where existing strategy was kept
        unassigned: list of symbols with no strategy at all
    """
    assigned   = []
    preserved  = []
    unassigned = []
    total      = len(scored_all)

    for i, (symbol, results) in enumerate(scored_all.items(), 1):
        logger.info(f"[{i}/{total}] Assigning strategy for {symbol}...")
        success = assign_strategy_for_token(conn, symbol, results, source)
        if success:
            assigned.append(symbol)
        else:
            c = conn.cursor()
            c.execute(
                "SELECT 1 FROM strategy_assignments WHERE symbol=? AND is_active=1",
                (symbol,)
            )
            if c.fetchone():
                preserved.append(symbol)
            else:
                unassigned.append(symbol)

    logger.info(
        f"Assignment complete — "
        f"{len(assigned)} new | "
        f"{len(preserved)} preserved | "
        f"{len(unassigned)} no strategy"
    )

    if unassigned:
        logger.warning(
            f"Tokens with no strategy: "
            f"{[s.replace('/USDT:USDT', '') for s in unassigned]}"
        )

    if preserved:
        logger.info(
            f"Tokens keeping existing strategy: "
            f"{[s.replace('/USDT:USDT', '') for s in preserved]}"
        )

    return {
        "assigned":   assigned,
        "preserved":  preserved,
        "unassigned": unassigned,
    }


# =============================================================================
# HISTORY & ARCHIVING
# =============================================================================

def _archive_existing_assignment(conn: sqlite3.Connection, symbol: str, replaced_at: str):
    """
    Move current assignment to history before overwriting.
    Only archives if an active assignment exists.
    """
    c = conn.cursor()
    c.execute("""
        INSERT INTO strategy_history (
            symbol, timeframe, tier, indicators, min_confluence,
            tier_rrr, composite_score, win_rate, expectancy,
            profit_factor, max_drawdown, sharpe_ratio,
            train_trades, val_trades, overfitting_gap,
            assigned_at, replaced_at, source
        )
        SELECT
            symbol, timeframe, tier, indicators, min_confluence,
            tier_rrr, composite_score, win_rate, expectancy,
            profit_factor, max_drawdown, sharpe_ratio,
            train_trades, val_trades, overfitting_gap,
            assigned_at, ?, source
        FROM strategy_assignments
        WHERE symbol = ? AND is_active = 1
    """, (replaced_at, symbol))
    conn.commit()


# =============================================================================
# TOKEN STATUS MANAGEMENT
# =============================================================================

def _preserve_or_deactivate(conn: sqlite3.Connection, symbol: str):
    """
    Called when no valid strategy is found for a token.

    If active assignment exists → preserve it (don't downgrade working tokens).
    If no active assignment → mark inactive.
    """
    c = conn.cursor()
    c.execute(
        "SELECT symbol, timeframe, tier, composite_score "
        "FROM strategy_assignments WHERE symbol = ? AND is_active = 1",
        (symbol,)
    )
    existing = c.fetchone()

    if existing:
        logger.info(
            f"No new strategy for {symbol.replace('/USDT:USDT', '')} — "
            f"keeping existing: {existing[1]} | {existing[2]} | "
            f"score={existing[3]:.4f}"
        )
    else:
        c.execute(
            "UPDATE strategy_assignments SET is_active = 0 WHERE symbol = ?",
            (symbol,)
        )
        _update_token_status(conn, symbol, tradeable=False, paused=False,
                             reason="No valid strategy found")
        conn.commit()
        logger.warning(f"Token marked unassigned (no prior strategy): {symbol}")


def _mark_token_unassigned(conn: sqlite3.Connection, symbol: str):
    """Backward compatibility — delegates to _preserve_or_deactivate."""
    _preserve_or_deactivate(conn, symbol)


def _update_token_status(
    conn: sqlite3.Connection,
    symbol: str,
    tradeable: bool,
    paused: bool,
    reason: str = None,
):
    """Update the token_status table."""
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    c.execute("""
        INSERT INTO token_status
            (symbol, is_tradeable, is_paused, pause_reason, paused_at, last_updated)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            is_tradeable = excluded.is_tradeable,
            is_paused    = excluded.is_paused,
            pause_reason = excluded.pause_reason,
            paused_at    = CASE WHEN excluded.is_paused = 1 THEN excluded.paused_at ELSE NULL END,
            last_updated = excluded.last_updated
    """, (
        symbol,
        1 if tradeable else 0,
        1 if paused else 0,
        reason,
        now if paused else None,
        now,
    ))
    conn.commit()


def pause_token(conn: sqlite3.Connection, symbol: str, reason: str):
    """Pause trading on a token."""
    _update_token_status(conn, symbol, tradeable=True, paused=True, reason=reason)
    logger.warning(f"Token paused: {symbol} — Reason: {reason}")


def resume_token(conn: sqlite3.Connection, symbol: str):
    """Resume trading on a paused token."""
    _update_token_status(conn, symbol, tradeable=True, paused=False)
    logger.info(f"Token resumed: {symbol}")


# =============================================================================
# STRATEGY RETRIEVAL — Used by the live bot
# =============================================================================

def get_strategy(conn: sqlite3.Connection, symbol: str) -> Optional[dict]:
    """Get the current active strategy for a token."""
    c = conn.cursor()
    c.execute("""
        SELECT
            sa.symbol, sa.timeframe, sa.tier, sa.indicators,
            sa.min_confluence, sa.tier_rrr, sa.composite_score,
            sa.win_rate, sa.expectancy, sa.profit_factor,
            sa.max_drawdown, sa.sharpe_ratio, sa.assigned_at,
            sa.strategy_name, sa.indicator_combo,
            ts.is_tradeable, ts.is_paused, ts.pause_reason
        FROM strategy_assignments sa
        LEFT JOIN token_status ts ON sa.symbol = ts.symbol
        WHERE sa.symbol = ? AND sa.is_active = 1
    """, (symbol,))

    row = c.fetchone()
    if not row:
        return None

    return {
        "symbol":          row[0],
        "timeframe":       row[1],
        "tier":            row[2],
        "indicators":      json.loads(row[3]),
        "min_confluence":  row[4],
        "tier_rrr":        row[5],
        "composite_score": row[6],
        "win_rate":        row[7],
        "expectancy":      row[8],
        "profit_factor":   row[9],
        "max_drawdown":    row[10],
        "sharpe_ratio":    row[11],
        "assigned_at":     row[12],
        "strategy_name":   row[13],
        "indicator_combo": row[14],
        "is_tradeable":    bool(row[15]) if row[15] is not None else False,
        "is_paused":       bool(row[16]) if row[16] is not None else False,
        "pause_reason":    row[17],
    }


def get_all_active_strategies(conn: sqlite3.Connection) -> list:
    """
    Get all currently active strategy assignments.
    Bot uses this to know which tokens to scan each cycle.

    FIX v3.8: LEFT JOIN with universe table to fetch daily_volume_usd.
    Previously the liquidity filter in signal_engine.py always received
    999_999_999 because this field was never populated — every token
    bypassed the volume check silently.
    Also returns strategy_name for logging and dashboard.
    """
    c = conn.cursor()
    c.execute("""
        SELECT
            sa.symbol, sa.timeframe, sa.tier, sa.indicators,
            sa.min_confluence, sa.tier_rrr, sa.composite_score,
            sa.win_rate, sa.expectancy,
            sa.strategy_name, sa.indicator_combo,
            ts.is_tradeable, ts.is_paused,
            COALESCE(u.daily_volume_usd, 999999999) as daily_volume_usd
        FROM strategy_assignments sa
        LEFT JOIN token_status ts ON sa.symbol = ts.symbol
        LEFT JOIN universe u ON sa.symbol = u.symbol
        WHERE sa.is_active = 1
        AND (ts.is_paused = 0 OR ts.is_paused IS NULL OR ts.symbol IS NULL)
        ORDER BY sa.composite_score DESC
    """)

    rows = c.fetchall()
    strategies = []

    for row in rows:
        strategies.append({
            "symbol":           row[0],
            "timeframe":        row[1],
            "tier":             row[2],
            "indicators":       json.loads(row[3]),
            "min_confluence":   row[4],
            "tier_rrr":         row[5],
            "composite_score":  row[6],
            "win_rate":         row[7],
            "expectancy":       row[8],
            "strategy_name":    row[9] or "Unknown",
            "indicator_combo":  row[10] or "",
            "is_tradeable":     bool(row[11]) if row[11] is not None else False,
            "is_paused":        bool(row[12]) if row[12] is not None else False,
            "daily_volume_usd": row[13],
        })

    return strategies


def get_assignment_summary(conn: sqlite3.Connection) -> dict:
    """Return summary counts of current strategy assignments."""
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM strategy_assignments WHERE is_active = 1")
    total_assigned = c.fetchone()[0]

    c.execute("""
        SELECT COUNT(*) FROM strategy_assignments sa
        JOIN token_status ts ON sa.symbol = ts.symbol
        WHERE sa.is_active = 1 AND ts.is_paused = 1
    """)
    total_paused = c.fetchone()[0]

    c.execute("""
        SELECT tier, COUNT(*) FROM strategy_assignments
        WHERE is_active = 1 GROUP BY tier
    """)
    tier_counts = dict(c.fetchall())

    c.execute("""
        SELECT timeframe, COUNT(*) FROM strategy_assignments
        WHERE is_active = 1 GROUP BY timeframe
    """)
    tf_counts = dict(c.fetchall())

    return {
        "total_assigned": total_assigned,
        "total_paused":   total_paused,
        "tier_counts":    tier_counts,
        "tf_counts":      tf_counts,
    }


def get_strategy_history(conn: sqlite3.Connection, symbol: str) -> list:
    """Return full strategy history for a token."""
    c = conn.cursor()
    c.execute("""
        SELECT timeframe, tier, composite_score, win_rate,
               expectancy, assigned_at, replaced_at, source
        FROM strategy_history
        WHERE symbol = ?
        ORDER BY assigned_at DESC
    """, (symbol,))

    columns = ["timeframe", "tier", "score", "win_rate",
               "expectancy", "assigned_at", "replaced_at", "source"]
    return [dict(zip(columns, row)) for row in c.fetchall()]


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    from apex.data_fetcher import get_db_connection
    from apex.backtest_engine import run_backtest_for_token
    from apex.strategy_scorer import score_strategies

    conn = get_db_connection()
    init_strategy_db(conn)

    symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT"]

    for sym in symbols:
        print(f"\nProcessing {sym}...")
        results = run_backtest_for_token(conn, sym)
        scored  = score_strategies(results)
        success = assign_strategy_for_token(conn, sym, scored, source="test")

        if success:
            strategy = get_strategy(conn, sym)
            print(
                f"Assigned: {strategy['timeframe']} | {strategy['tier']} | "
                f"{strategy['strategy_name']} | Score: {strategy['composite_score']:.4f}"
            )
        else:
            print(f"No new strategy — existing preserved if available")

    summary = get_assignment_summary(conn)
    print(f"\nSummary: {summary}")

    conn.close()

# __APEX_LOGGER_V1__
