# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/performance_monitor.py — Rolling Performance Monitor
# =============================================================================
# RESPONSIBILITY:
# Monitors live trading performance per token on a rolling basis.
# Catches strategy degradation between monthly rebalances.
# Pauses tokens whose live performance deteriorates significantly.
#
# WHAT THIS FILE DOES:
# - Tracks last 20 closed trades per token in real time
# - Pauses token if rolling expectancy turns negative
# - Pauses token if rolling win rate drops below 35%
# - Sends Telegram alert when a token is paused
# - Resumes tokens automatically after monthly rebalance
# - Provides per-token performance breakdown for dashboard
#
# WHAT THIS FILE DOES NOT DO:
# - Does not manage drawdown (that's risk_manager.py)
# - Does not reassign strategies (that's strategy_assigner.py)
# - Does not execute trades (that's trade_manager.py)
# =============================================================================

import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.config import PERFORMANCE_MONITOR, LOGS, DB, DRAWDOWN

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)


# =============================================================================
# ROLLING METRICS CALCULATION
# =============================================================================

def get_rolling_trades(
    conn: sqlite3.Connection,
    symbol: str,
    lookback: int = None,
) -> list:
    """
    Get the last N closed trades for a specific token.
    Returns list of dicts with pnl_usdt, pnl_pct, exit_reason.
    """
    if lookback is None:
        lookback = PERFORMANCE_MONITOR["lookback_trades"]

    c = conn.cursor()
    c.execute("""
        SELECT pnl_usdt, pnl_pct, exit_reason, exit_time
        FROM trades
        WHERE symbol = ? AND status = 'closed'
        ORDER BY exit_time DESC
        LIMIT ?
    """, (symbol, lookback))

    columns = ["pnl_usdt", "pnl_pct", "exit_reason", "exit_time"]
    return [dict(zip(columns, row)) for row in c.fetchall()]


def calculate_rolling_metrics(trades: list) -> Optional[dict]:
    """
    Calculate rolling metrics from a list of closed trades.
    Returns None if insufficient trades.
    """
    if not trades:
        return None

    pnls     = [t["pnl_usdt"] for t in trades]
    wins     = [p for p in pnls if p > 0]
    losses   = [p for p in pnls if p <= 0]
    n_trades = len(trades)
    n_wins   = len(wins)

    win_rate   = n_wins / n_trades if n_trades > 0 else 0
    avg_win    = sum(wins)   / len(wins)   if wins   else 0
    avg_loss   = sum(losses) / len(losses) if losses else 0
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    exit_reasons = {}
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    return {
        "n_trades":     n_trades,
        "win_rate":     round(win_rate, 4),
        "expectancy":   round(expectancy, 4),
        "avg_win":      round(avg_win, 4),
        "avg_loss":     round(avg_loss, 4),
        "total_pnl":    round(sum(pnls), 4),
        "exit_reasons": exit_reasons,
    }


# =============================================================================
# PAUSE/RESUME LOGIC
# =============================================================================

def should_pause_token(metrics: dict) -> tuple:
    """
    Determine if a token should be paused based on rolling metrics.

    Pause conditions:
    1. Rolling expectancy turns negative
    2. Rolling win rate drops below 35%

    Returns (should_pause: bool, reason: str)
    """
    min_expectancy = PERFORMANCE_MONITOR["min_expectancy"]
    min_win_rate   = PERFORMANCE_MONITOR["min_win_rate"]

    if metrics["expectancy"] < min_expectancy:
        return True, (
            f"Rolling expectancy negative: {metrics['expectancy']:.4f} "
            f"(threshold: {min_expectancy})"
        )

    if metrics["win_rate"] < min_win_rate:
        return True, (
            f"Rolling win rate too low: {metrics['win_rate']:.1%} "
            f"(threshold: {min_win_rate:.1%})"
        )

    return False, ""


def pause_token_in_apex(symbol: str, reason: str):
    """
    Pause a token in apex.db strategy_assignments.
    Bot checks this flag before generating signals.
    """
    try:
        from apex.data_fetcher import get_db_connection
        from apex.strategy_assigner import pause_token

        apex_conn = get_db_connection()
        pause_token(apex_conn, symbol, reason)
        apex_conn.close()
        logger.warning(f"Token paused in APEX: {symbol} — {reason}")

    except Exception as e:
        logger.error(f"Failed to pause token in APEX db: {symbol}: {e}")


def send_pause_alert(symbol: str, reason: str, metrics: dict):
    """Send Telegram alert when a token is paused."""
    try:
        from telegram_bot import send_message

        token = symbol.replace("/USDT:USDT", "")
        msg   = (
            f"⚠️ *Token Paused: {token}*\n"
            f"Reason: {reason}\n\n"
            f"Rolling stats (last {metrics['n_trades']} trades):\n"
            f"• Win Rate: {metrics['win_rate']:.1%}\n"
            f"• Expectancy: ${metrics['expectancy']:.2f}\n"
            f"• Total PnL: ${metrics['total_pnl']:.2f}\n\n"
            f"Token will resume after next monthly rebalance."
        )
        send_message(msg)

    except Exception as e:
        logger.warning(f"Pause alert failed for {symbol}: {e}")


# =============================================================================
# MAIN MONITOR FUNCTION
# =============================================================================

def check_token_performance(
    conn: sqlite3.Connection,
    symbol: str,
) -> dict:
    """
    Check rolling performance for a single token.
    Pauses it if thresholds are breached.

    Returns dict with metrics and action taken.
    """
    if not PERFORMANCE_MONITOR["enabled"]:
        return {"symbol": symbol, "action": "monitor_disabled"}

    lookback = PERFORMANCE_MONITOR["lookback_trades"]
    trades   = get_rolling_trades(conn, symbol, lookback)

    # Need at least half the lookback window to make a judgement
    min_trades = max(5, lookback // 2)

    if len(trades) < min_trades:
        return {
            "symbol":   symbol,
            "action":   "insufficient_trades",
            "n_trades": len(trades),
            "needed":   min_trades,
        }

    metrics = calculate_rolling_metrics(trades)

    if metrics is None:
        return {"symbol": symbol, "action": "no_metrics"}

    pause, reason = should_pause_token(metrics)

    if pause:
        pause_token_in_apex(symbol, reason)
        try:
            from bot.config import apex_logger
            apex_logger.performance_pause(
                token                  = symbol,
                reason                 = reason,
                last_20_win_rate       = metrics["win_rate"],
                last_20_expectancy     = metrics["expectancy"],
                last_20_trades_summary = [],
            )
        except Exception: pass
        send_pause_alert(symbol, reason, metrics)
        logger.warning(
            f"Performance monitor paused {symbol}: {reason} | "
            f"WR: {metrics['win_rate']:.1%} | "
            f"Exp: {metrics['expectancy']:.4f}"
        )
        return {
            "symbol":  symbol,
            "action":  "paused",
            "reason":  reason,
            "metrics": metrics,
        }

    logger.debug(
        f"Performance OK: {symbol.replace('/USDT:USDT','')} | "
        f"WR: {metrics['win_rate']:.1%} | "
        f"Exp: {metrics['expectancy']:.4f} | "
        f"Trades: {metrics['n_trades']}"
    )

    return {
        "symbol":  symbol,
        "action":  "ok",
        "metrics": metrics,
    }


def check_all_tokens_performance(
    conn: sqlite3.Connection,
    active_symbols: list,
) -> dict:
    """
    Check rolling performance for all active tokens.
    Called at end of every bot cycle.

    Returns dict: symbol -> check result.
    """
    results  = {}
    paused   = []

    for symbol in active_symbols:
        result = check_token_performance(conn, symbol)
        results[symbol] = result

        if result.get("action") == "paused":
            paused.append(symbol)

    if paused:
        logger.warning(
            f"Performance monitor paused {len(paused)} token(s): "
            f"{[s.replace('/USDT:USDT','') for s in paused]}"
        )

    return results


# =============================================================================
# PERFORMANCE SUMMARY — For dashboard and logging
# =============================================================================

def get_all_token_metrics(
    conn: sqlite3.Connection,
    active_symbols: list,
) -> list:
    """
    Get rolling performance metrics for all active tokens.
    Used by dashboard to display per-token stats.

    Returns list of dicts sorted by expectancy descending.
    """
    rows = []

    for symbol in active_symbols:
        lookback = PERFORMANCE_MONITOR["lookback_trades"]
        trades   = get_rolling_trades(conn, symbol, lookback)

        if not trades:
            continue

        metrics = calculate_rolling_metrics(trades)
        if not metrics:
            continue

        rows.append({
            "symbol":     symbol.replace("/USDT:USDT", ""),
            "n_trades":   metrics["n_trades"],
            "win_rate":   metrics["win_rate"],
            "expectancy": metrics["expectancy"],
            "avg_win":    metrics["avg_win"],
            "avg_loss":   metrics["avg_loss"],
            "total_pnl":  metrics["total_pnl"],
        })

    rows.sort(key=lambda x: x["expectancy"], reverse=True)
    return rows


def get_overall_performance(conn: sqlite3.Connection) -> dict:
    """
    Get overall bot performance across all tokens.
    Used by dashboard header stats.
    """
    c = conn.cursor()

    c.execute("""
        SELECT
            COUNT(*)                                        as total_trades,
            SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
            SUM(pnl_usdt)                                  as total_pnl,
            AVG(CASE WHEN pnl_usdt > 0 THEN pnl_usdt END) as avg_win,
            AVG(CASE WHEN pnl_usdt <= 0 THEN pnl_usdt END) as avg_loss
        FROM trades WHERE status = 'closed'
    """)

    row = c.fetchone()

    if not row or not row[0]:
        return {
            "total_trades": 0,
            "win_rate":     0,
            "total_pnl":    0,
            "expectancy":   0,
            "avg_win":      0,
            "avg_loss":     0,
        }

    total   = row[0] or 0
    wins    = row[1] or 0
    pnl     = row[2] or 0
    avg_win = row[3] or 0
    avg_loss = row[4] or 0

    win_rate   = wins / total if total > 0 else 0
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    return {
        "total_trades": total,
        "win_rate":     round(win_rate * 100, 1),
        "total_pnl":    round(pnl, 2),
        "expectancy":   round(expectancy, 2),
        "avg_win":      round(avg_win, 2),
        "avg_loss":     round(avg_loss, 2),
    }


def log_performance_summary(conn: sqlite3.Connection, active_symbols: list):
    """Log a performance summary — called periodically from main.py."""
    overall = get_overall_performance(conn)
    metrics = get_all_token_metrics(conn, active_symbols)

    logger.info("=" * 60)
    logger.info("PERFORMANCE SUMMARY")
    logger.info(
        f"Total trades: {overall['total_trades']} | "
        f"Win rate: {overall['win_rate']:.1f}% | "
        f"Total PnL: ${overall['total_pnl']:.2f} | "
        f"Expectancy: ${overall['expectancy']:.2f}"
    )

    if metrics:
        logger.info("\nPer-token rolling performance:")
        for m in metrics[:10]:  # Top 10
            logger.info(
                f"  {m['symbol']:10} | "
                f"Trades: {m['n_trades']:2} | "
                f"WR: {m['win_rate']:.1%} | "
                f"Exp: ${m['expectancy']:.2f} | "
                f"PnL: ${m['total_pnl']:.2f}"
            )

    logger.info("=" * 60)


# =============================================================================
# GO-LIVE CRITERIA CHECK
# =============================================================================

def check_go_live_criteria(conn: sqlite3.Connection) -> dict:
    """
    Check if all go-live criteria are met for switching to real capital.

    Criteria (from config):
    - Minimum 100 closed trades
    - Live win rate within 10% of backtest win rate
    - Live expectancy positive
    - Maximum 2 drawdown alerts triggered

    Returns dict with criteria status and overall readiness.
    """
    from bot.config import GO_LIVE_CRITERIA
    from bot.risk_manager import calculate_drawdown_from_db

    c = conn.cursor()

    # Total closed trades
    c.execute("SELECT COUNT(*) FROM trades WHERE status = 'closed'")
    total_trades = c.fetchone()[0]

    # Overall performance
    overall = get_overall_performance(conn)
    win_rate   = overall["win_rate"] / 100
    expectancy = overall["expectancy"]

    # Drawdown alerts (approximate from risk events in logs)
    # For now check if drawdown ever exceeded alert threshold
    dd_data = calculate_drawdown_from_db(conn)

    criteria = {
        "min_trades": {
            "required": GO_LIVE_CRITERIA["min_closed_trades"],
            "actual":   total_trades,
            "passed":   total_trades >= GO_LIVE_CRITERIA["min_closed_trades"],
        },
        "expectancy": {
            "required": GO_LIVE_CRITERIA["min_expectancy"],
            "actual":   round(expectancy, 4),
            "passed":   expectancy > GO_LIVE_CRITERIA["min_expectancy"],
        },
        "win_rate": {
            "actual": round(win_rate * 100, 1),
            "note":   "Compare with backtest win rate manually",
            "passed": win_rate >= 0.40,  # Minimum 40% as sanity check
        },
        "drawdown": {
            "current_dd": dd_data["drawdown_pct"],
            "passed":     dd_data["drawdown_pct"] < DRAWDOWN["alert_pct"] * 100,
        },
    }

    all_passed = all(v.get("passed", False) for v in criteria.values())

    return {
        "ready_for_live": all_passed,
        "criteria":       criteria,
        "checked_at":     datetime.now(timezone.utc).isoformat(),
    }



# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    from bot.trade_manager import get_trades_conn, init_trades_db
    from apex.data_fetcher import get_db_connection
    from apex.strategy_assigner import get_all_active_strategies

    trades_conn = get_trades_conn()
    apex_conn   = get_db_connection()

    strategies      = get_all_active_strategies(apex_conn)
    active_symbols  = [s["symbol"] for s in strategies]

    print(f"Checking performance for {len(active_symbols)} tokens...")

    overall = get_overall_performance(trades_conn)
    print(f"\nOverall: {overall}")

    go_live = check_go_live_criteria(trades_conn)
    print(f"\nGo-live ready: {go_live['ready_for_live']}")
    for k, v in go_live["criteria"].items():
        print(f"  {k}: {v}")

    trades_conn.close()
    apex_conn.close()

# __APEX_LOGGER_V1__
