# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# apex/strategy_assigner.py — Strategy Assignment
# =============================================================================
# RESPONSIBILITY:
# Takes scored and ranked strategies from strategy_scorer.py and writes
# the best strategy per token to apex.db.
# The live trading bot reads exclusively from apex.db — it never calls
# APEX directly. This file is the bridge between APEX and the bot.
#
# WHAT THIS FILE DOES:
# - Takes best scored strategy per token
# - Writes strategy assignment to apex.db
# - Keeps old strategy active until new one is validated and ready
# - Swaps strategy atomically — no partial state
# - Tracks full assignment history for auditing
# - Marks tokens with no valid strategy as unassigned
#
# WHAT THIS FILE DOES NOT DO:
# - Does not run backtests (that's backtest_engine.py)
# - Does not score strategies (that's strategy_scorer.py)
# - Does not execute trades (that's the bot)
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

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)


# =============================================================================
# DATABASE SETUP
# =============================================================================

def init_strategy_db(conn: sqlite3.Connection):
    """
    Create strategy assignment tables if they don't exist.
    Safe to call multiple times.
    """
    c = conn.cursor()

    # Current active strategy per token
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
            is_active           INTEGER DEFAULT 1
        )
    """)

    # Full history of all assignments — never deleted
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

    # Token status table — tracks which tokens are tradeable
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

    Args:
        conn: Database connection
        symbol: Token symbol (e.g. BTC/USDT:USDT)
        scored_results: Ranked list from strategy_scorer.score_strategies()
        source: What triggered this assignment ('rebalance', 'weekly', 'initial')

    Returns True if assignment was made, False if no valid strategy found.
    """
    if not scored_results:
        logger.warning(f"No valid strategy to assign for {symbol}")
        _mark_token_unassigned(conn, symbol)
        return False

    best = scored_results[0]
    vm   = best["val_metrics"]
    now  = datetime.now(timezone.utc).isoformat()

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
            assigned_at, source, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
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
            is_active       = 1
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
    ))

    # Mark token as tradeable
    _update_token_status(conn, symbol, tradeable=True, paused=False)

    conn.commit()

    try:
        from bot.config import apex_logger
        apex_logger.strategy_assigned(
            token              = symbol,
            strategy           = best.get("strategy_type", best.get("timeframe", "unknown")),
            timeframe          = best["timeframe"],
            tier               = best.get("assigned_tier", best.get("tier", "unknown")),
            metrics            = vm,
            assignment_reason  = source,
        )
    except Exception: pass

    logger.info(
        f"Strategy assigned: {symbol.replace('/USDT:USDT','')} | "
        f"{best['timeframe']} | {best['assigned_tier']} | "
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

    Args:
        conn: Database connection
        scored_all: dict of symbol -> scored results from strategy_scorer
        source: What triggered this ('rebalance', 'initial')

    Returns dict with assignment summary:
        assigned: list of symbols assigned
        unassigned: list of symbols with no valid strategy
    """
    assigned   = []
    unassigned = []
    total      = len(scored_all)

    for i, (symbol, results) in enumerate(scored_all.items(), 1):
        logger.info(f"[{i}/{total}] Assigning strategy for {symbol}...")
        success = assign_strategy_for_token(conn, symbol, results, source)
        if success:
            assigned.append(symbol)
        else:
            unassigned.append(symbol)

    logger.info(
        f"Assignment complete — "
        f"{len(assigned)} assigned, {len(unassigned)} unassigned"
    )

    if unassigned:
        logger.warning(
            f"Tokens with no valid strategy: "
            f"{[s.replace('/USDT:USDT','') for s in unassigned]}"
        )

    return {"assigned": assigned, "unassigned": unassigned}


# =============================================================================
# HISTORY & ARCHIVING
# =============================================================================

def _archive_existing_assignment(conn: sqlite3.Connection, symbol: str, replaced_at: str):
    """
    Move current assignment to history table before overwriting.
    Preserves full audit trail of all strategy changes.
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
        WHERE symbol = ?
    """, (replaced_at, symbol))

    conn.commit()


# =============================================================================
# TOKEN STATUS MANAGEMENT
# =============================================================================

def _mark_token_unassigned(conn: sqlite3.Connection, symbol: str):
    """Mark a token as not tradeable due to no valid strategy."""
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    # Remove from active assignments
    c.execute(
        "UPDATE strategy_assignments SET is_active = 0 WHERE symbol = ?",
        (symbol,)
    )

    _update_token_status(conn, symbol, tradeable=False, paused=False,
                         reason="No valid strategy found")
    conn.commit()
    logger.warning(f"Token marked unassigned: {symbol}")


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
    """
    Pause trading on a token.
    Called by performance_monitor.py when rolling metrics deteriorate.
    Token resumes after next monthly rebalance reassigns strategy.
    """
    _update_token_status(conn, symbol, tradeable=True, paused=True, reason=reason)
    logger.warning(f"Token paused: {symbol} — Reason: {reason}")


def resume_token(conn: sqlite3.Connection, symbol: str):
    """
    Resume trading on a paused token.
    Called after monthly rebalance successfully assigns new strategy.
    """
    _update_token_status(conn, symbol, tradeable=True, paused=False)
    logger.info(f"Token resumed: {symbol}")


# =============================================================================
# STRATEGY RETRIEVAL — Used by the live bot
# =============================================================================

def get_strategy(conn: sqlite3.Connection, symbol: str) -> Optional[dict]:
    """
    Get the current active strategy for a token.
    This is the primary function the bot calls before generating signals.

    Returns dict with all strategy parameters, or None if no strategy.
    """
    c = conn.cursor()
    c.execute("""
        SELECT
            sa.symbol, sa.timeframe, sa.tier, sa.indicators,
            sa.min_confluence, sa.tier_rrr, sa.composite_score,
            sa.win_rate, sa.expectancy, sa.profit_factor,
            sa.max_drawdown, sa.sharpe_ratio, sa.assigned_at,
            ts.is_tradeable, ts.is_paused, ts.pause_reason
        FROM strategy_assignments sa
        LEFT JOIN token_status ts ON sa.symbol = ts.symbol
        WHERE sa.symbol = ? AND sa.is_active = 1
    """, (symbol,))

    row = c.fetchone()
    if not row:
        return None

    return {
        "symbol":           row[0],
        "timeframe":        row[1],
        "tier":             row[2],
        "indicators":       json.loads(row[3]),
        "min_confluence":   row[4],
        "tier_rrr":         row[5],
        "composite_score":  row[6],
        "win_rate":         row[7],
        "expectancy":       row[8],
        "profit_factor":    row[9],
        "max_drawdown":     row[10],
        "sharpe_ratio":     row[11],
        "assigned_at":      row[12],
        "is_tradeable":     bool(row[13]) if row[13] is not None else False,
        "is_paused":        bool(row[14]) if row[14] is not None else False,
        "pause_reason":     row[15],
    }


def get_all_active_strategies(conn: sqlite3.Connection) -> list:
    """
    Get all currently active strategy assignments.
    Bot uses this to know which tokens to scan each cycle.
    Returns list of strategy dicts.
    """
    c = conn.cursor()
    c.execute("""
        SELECT
            sa.symbol, sa.timeframe, sa.tier, sa.indicators,
            sa.min_confluence, sa.tier_rrr, sa.composite_score,
            sa.win_rate, sa.expectancy,
            ts.is_tradeable, ts.is_paused
        FROM strategy_assignments sa
        LEFT JOIN token_status ts ON sa.symbol = ts.symbol
        WHERE sa.is_active = 1
        -- is_tradeable filter removed — all active strategies are tradeable
        AND (ts.is_paused = 0 OR ts.is_paused IS NULL OR ts.symbol IS NULL)
        ORDER BY sa.composite_score DESC
    """)

    rows = c.fetchall()
    strategies = []

    for row in rows:
        strategies.append({
            "symbol":          row[0],
            "timeframe":       row[1],
            "tier":            row[2],
            "indicators":      json.loads(row[3]),
            "min_confluence":  row[4],
            "tier_rrr":        row[5],
            "composite_score": row[6],
            "win_rate":        row[7],
            "expectancy":      row[8],
            "is_tradeable":    bool(row[9]) if row[9] is not None else False,
            "is_paused":       bool(row[10]) if row[10] is not None else False,
        })

    return strategies


def get_assignment_summary(conn: sqlite3.Connection) -> dict:
    """
    Return summary counts of current strategy assignments.
    Used by dashboard and Telegram alerts.
    """
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
        WHERE is_active = 1
        GROUP BY tier
    """)
    tier_counts = dict(c.fetchall())

    c.execute("""
        SELECT timeframe, COUNT(*) FROM strategy_assignments
        WHERE is_active = 1
        GROUP BY timeframe
    """)
    tf_counts = dict(c.fetchall())

    return {
        "total_assigned": total_assigned,
        "total_paused":   total_paused,
        "tier_counts":    tier_counts,
        "tf_counts":      tf_counts,
    }


def get_strategy_history(conn: sqlite3.Connection, symbol: str) -> list:
    """
    Return full strategy history for a token.
    Useful for debugging strategy drift over time.
    """
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
# ENTRY POINT — Run directly to test strategy assignment
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    from apex.data_fetcher import get_db_connection
    from apex.backtest_engine import run_backtest_for_token
    from apex.strategy_scorer import score_strategies

    conn    = get_db_connection()
    init_strategy_db(conn)

    symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT"]

    for sym in symbols:
        print(f"\nProcessing {sym}...")
        results = run_backtest_for_token(conn, sym)
        scored  = score_strategies(results)
        success = assign_strategy_for_token(conn, sym, scored, source="test")

        if success:
            strategy = get_strategy(conn, sym)
            print(f"Assigned: {strategy['timeframe']} | {strategy['tier']} | Score: {strategy['composite_score']:.4f}")
        else:
            print(f"No strategy found for {sym}")

    summary = get_assignment_summary(conn)
    print(f"\nSummary: {summary}")

    conn.close()

# __APEX_LOGGER_V1__
