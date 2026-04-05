# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# apex/universe_manager.py — Token Universe Manager
# =============================================================================
# RESPONSIBILITY:
# Manages the weekly refresh of the token universe.
# Determines which tokens are in/out of the top 100 and triggers
# immediate backtesting for newly added tokens.
#
# WHAT THIS FILE DOES:
# - Runs every Sunday 00:00 UTC automatically
# - Fetches current top 100 Binance Futures tokens by market cap
# - Identifies tokens added to or dropped from the universe
# - Dropped tokens: blocks new entries, lets existing trades finish naturally
# - New tokens: fetches their data immediately and triggers backtest
# - Writes universe changes to apex.db for the bot to read
# - Sends Telegram alerts for universe changes
#
# WHAT THIS FILE DOES NOT DO:
# - Does not run the full monthly rebalance (that's rebalancer.py)
# - Does not assign strategies (that's strategy_assigner.py)
# - Does not close existing trades (that's the bot's job)
# =============================================================================

import sqlite3
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from bot.config import REBALANCE, FILTERS, DB, LOGS, TIMEFRAMES, BACKTEST
from apex.data_fetcher import (
    get_exchange,
    get_db_connection,
    get_top_100_futures_symbols,
    update_universe,
    get_active_symbols,
    fetch_token_data,
    init_database,
)

# =============================================================================
# LOGGING SETUP
# =============================================================================

logging.basicConfig(
    level=getattr(logging, LOGS["level"]),
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOGS["apex"]),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


# =============================================================================
# UNIVERSE STATE MANAGEMENT
# =============================================================================

def get_universe_snapshot(conn: sqlite3.Connection) -> dict:
    """
    Return current universe state as a dict.
    Used to detect changes between refreshes.
    """
    c = conn.cursor()
    c.execute("""
        SELECT symbol, market_cap_rank, daily_volume_usd, is_active
        FROM universe
        ORDER BY market_cap_rank ASC
    """)
    rows = c.fetchall()
    return {
        row[0]: {
            "rank": row[1],
            "volume": row[2],
            "active": bool(row[3]),
        }
        for row in rows
    }


def mark_token_no_new_entries(conn: sqlite3.Connection, symbol: str):
    """
    Mark a dropped token so the bot knows not to open new trades on it.
    Existing trades continue to run naturally.
    This writes to the universe table — bot reads this flag before every entry.
    """
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("""
        UPDATE universe
        SET is_active = 0, updated_at = ?
        WHERE symbol = ?
    """, (now, symbol))
    conn.commit()
    logger.info(f"Blocked new entries for dropped token: {symbol}")


def get_tokens_pending_backtest(conn: sqlite3.Connection) -> list[str]:
    """
    Return list of tokens that were recently added to universe
    but don't yet have a strategy assigned in apex.db.
    These are queued for immediate backtest.
    """
    c = conn.cursor()

    # Tokens in universe but not in strategy_assignments
    c.execute("""
        SELECT u.symbol
        FROM universe u
        LEFT JOIN strategy_assignments sa ON u.symbol = sa.symbol
        WHERE u.is_active = 1
        AND sa.symbol IS NULL
        ORDER BY u.market_cap_rank ASC
    """)
    return [row[0] for row in c.fetchall()]


def is_strategy_assigned(conn: sqlite3.Connection, symbol: str) -> bool:
    """Check if a token already has a strategy assigned."""
    c = conn.cursor()
    try:
        c.execute("""
            SELECT COUNT(*) FROM strategy_assignments
            WHERE symbol = ? AND is_active = 1
        """, (symbol,))
        return c.fetchone()[0] > 0
    except sqlite3.OperationalError:
        # Table doesn't exist yet — no strategies assigned
        return False


# =============================================================================
# TELEGRAM ALERTS
# =============================================================================

def send_universe_alert(added: list[str], dropped: list[str]):
    """
    Send Telegram alert summarising universe changes.
    Only sends if there are actual changes.
    Import is local to avoid circular dependency — telegram_bot is independent.
    """
    if not added and not dropped:
        return

    try:
        from telegram_bot import send_message
        lines = ["🔄 *Weekly Universe Refresh*\n"]

        if added:
            lines.append(f"✅ *Added ({len(added)}):*")
            for s in added:
                lines.append(f"  • {s.replace('/USDT:USDT', '')}")

        if dropped:
            lines.append(f"\n❌ *Removed ({len(dropped)}):*")
            for s in dropped:
                lines.append(f"  • {s.replace('/USDT:USDT', '')}")

        send_message("\n".join(lines))
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")


def send_backtest_complete_alert(symbol: str):
    """Alert when a new token's backtest and strategy assignment is complete."""
    try:
        from telegram_bot import send_message
        token = symbol.replace("/USDT:USDT", "")
        send_message(f"✅ *{token}* — strategy assigned, bot now trading this token.")
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")


# =============================================================================
# WEEKLY UNIVERSE REFRESH — CORE LOGIC
# =============================================================================

def run_weekly_refresh():
    """
    Main function for weekly universe refresh.
    Runs every Sunday 00:00 UTC via scheduler in rebalancer.py.

    Steps:
    1. Fetch current top 100 from Binance Futures
    2. Compare against stored universe
    3. Mark dropped tokens — block new entries
    4. Add new tokens — fetch data immediately + trigger backtest
    5. Send Telegram alert
    """
    logger.info("=" * 60)
    logger.info("APEX Weekly Universe Refresh — Starting")
    logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    exchange = get_exchange()
    conn = get_db_connection()
    init_database(conn)

    # Step 1 — Snapshot current universe
    before = get_universe_snapshot(conn)
    logger.info(f"Current universe: {sum(1 for v in before.values() if v['active'])} active tokens")

    # Step 2 — Fetch new top 100
    try:
        tokens = get_top_100_futures_symbols(exchange)
    except Exception as e:
        logger.error(f"Failed to fetch top 100 tokens: {e}")
        return

    # Step 3 — Update universe, get added/dropped lists
    added_symbols, dropped_symbols = update_universe(conn, tokens)

    # Step 4 — Handle dropped tokens
    if dropped_symbols:
        logger.info(f"Dropped tokens ({len(dropped_symbols)}): {dropped_symbols}")
        for symbol in dropped_symbols:
            mark_token_no_new_entries(conn, symbol)
            try:
                from bot.config import apex_logger
                apex_logger.universe_token_removed(token=symbol, reason="dropped_out_of_top100")
            except Exception: pass
            # Note: existing trades on this token continue naturally
            # The bot checks is_active flag before opening new trades

    # Step 5 — Handle new tokens
    if added_symbols:
        logger.info(f"New tokens ({len(added_symbols)}): {added_symbols}")
        for symbol in added_symbols:
            logger.info(f"Fetching historical data for new token: {symbol}")
            try:
                # Fetch 1 year of data immediately
                fetch_token_data(
                    exchange,
                    conn,
                    symbol,
                    TIMEFRAMES,
                    BACKTEST["lookback_years"],
                )
                logger.info(f"Data fetch complete for {symbol} — queuing for backtest")
            except Exception as e:
                logger.error(f"Data fetch failed for {symbol}: {e}")

        # Trigger immediate backtest for new tokens
        _backtest_new_tokens(conn, added_symbols)

    # Step 6 — Send Telegram alert
    send_universe_alert(added_symbols, dropped_symbols)
    try:
        from bot.config import apex_logger
        apex_logger.universe_refresh_summary(
            tokens_added=added_symbols,
            tokens_removed=dropped_symbols,
            total_universe_size=active_count,
            refresh_type="weekly",
        )
        for _sym in added_symbols:
            apex_logger.universe_token_added(token=_sym, market_cap_rank=0, daily_volume_usd=0)
    except Exception: pass

    # Step 7 — Log final state
    after = get_universe_snapshot(conn)
    active_count = sum(1 for v in after.values() if v["active"])
    logger.info(f"Weekly refresh complete — {active_count} active tokens in universe")
    logger.info("=" * 60)

    conn.close()


def _backtest_new_tokens(conn: sqlite3.Connection, symbols: list[str]):
    """
    Trigger immediate backtest and strategy assignment for newly added tokens.
    Runs inline — new tokens get their strategy same day they're added.
    """
    if not symbols:
        return

    logger.info(f"Running immediate backtest for {len(symbols)} new token(s)...")

    # Import here to avoid circular imports
    from apex.backtest_engine import run_backtest_for_token
    from apex.strategy_assigner import assign_strategy_for_token

    for symbol in symbols:
        try:
            logger.info(f"Backtesting new token: {symbol}")
            results = run_backtest_for_token(conn, symbol)

            if results:
                assign_strategy_for_token(conn, symbol, results)
                logger.info(f"Strategy assigned for new token: {symbol}")
                send_backtest_complete_alert(symbol)
            else:
                logger.warning(f"No valid strategy found for new token: {symbol} — will retry in monthly rebalance")

        except Exception as e:
            logger.error(f"Backtest failed for new token {symbol}: {e}")


# =============================================================================
# UNIVERSE QUERY HELPERS — Used by bot and other APEX modules
# =============================================================================

def get_active_universe(conn: sqlite3.Connection) -> list[dict]:
    """
    Return full details of all currently active tokens.
    Used by bot's signal engine to know which tokens to scan.
    """
    c = conn.cursor()
    c.execute("""
        SELECT symbol, base_asset, market_cap_rank, daily_volume_usd
        FROM universe
        WHERE is_active = 1
        ORDER BY market_cap_rank ASC
    """)
    columns = ["symbol", "base_asset", "rank", "volume"]
    return [dict(zip(columns, row)) for row in c.fetchall()]


def is_token_active(conn: sqlite3.Connection, symbol: str) -> bool:
    """
    Check if a specific token is currently active in the universe.
    Bot calls this before opening a new trade.
    """
    c = conn.cursor()
    c.execute("""
        SELECT is_active FROM universe WHERE symbol = ?
    """, (symbol,))
    row = c.fetchone()
    return bool(row[0]) if row else False


def get_universe_change_log(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    """
    Return recent universe changes for dashboard display.
    Shows which tokens were added/removed in the last N days.
    """
    c = conn.cursor()
    cutoff = datetime.now(timezone.utc).isoformat()

    c.execute("""
        SELECT symbol, is_active, updated_at
        FROM universe
        WHERE updated_at >= datetime('now', ?)
        ORDER BY updated_at DESC
    """, (f"-{days} days",))

    columns = ["symbol", "is_active", "updated_at"]
    return [dict(zip(columns, row)) for row in c.fetchall()]


# =============================================================================
# ENTRY POINT — Run directly to test weekly refresh
# =============================================================================

if __name__ == "__main__":
    run_weekly_refresh()

# __APEX_LOGGER_V1__
