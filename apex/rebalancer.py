# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# apex/rebalancer.py — Rebalance Orchestrator
# =============================================================================
# RESPONSIBILITY:
# Orchestrates the full automated rebalancing schedule.
# Ties together data_fetcher, backtest_engine, strategy_scorer,
# strategy_assigner, and universe_manager into a single automated flow.
#
# WHAT THIS FILE DOES:
# - Runs weekly universe refresh (every Sunday 00:00 UTC)
# - Runs monthly full rebalance (1st of every month)
# - Processes tokens in batches to avoid overloading the server
# - Bot continues trading normally during rebalance
# - Old strategies stay active until new ones are validated
# - Sends Telegram alerts on completion
# - Logs everything for audit and debugging
#
# WHAT THIS FILE DOES NOT DO:
# - Does not execute trades (that's the bot)
# - Does not generate signals (that's signal_engine.py)
# =============================================================================

import sqlite3
import logging
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.config import (
    REBALANCE, BACKTEST, DB, LOGS, TIMEFRAMES,
)
from apex.data_fetcher import (
    get_exchange, get_db_connection, init_database,
    get_top_100_futures_symbols, update_universe,
    get_active_symbols, fetch_all_tokens, fetch_token_data,
    get_data_summary,
)
from apex.universe_manager import (
    run_weekly_refresh, get_active_universe,
)
from apex.backtest_engine import (
    run_backtest_for_token, run_backtest_for_tokens,
)
from apex.strategy_scorer import (
    score_strategies, score_all_tokens, print_scoring_summary,
)
from apex.strategy_assigner import (
    init_strategy_db, assign_strategy_for_token,
    assign_strategies_for_all_tokens, get_assignment_summary,
    resume_token,
)

# =============================================================================
# LOGGING
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
# TELEGRAM ALERTS
# =============================================================================

def send_rebalance_alert(event: str, details: dict):
    """Send Telegram alert for rebalance events."""
    try:
        from telegram_bot import send_message

        if event == "monthly_start":
            msg = (
                f"🔄 *Monthly Rebalance Started*\n"
                f"Tokens to process: {details.get('total', 0)}\n"
                f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            )
        elif event == "monthly_complete":
            msg = (
                f"✅ *Monthly Rebalance Complete*\n"
                f"Assigned: {details.get('assigned', 0)} tokens\n"
                f"Unassigned: {details.get('unassigned', 0)} tokens\n"
                f"Duration: {details.get('duration_mins', 0):.1f} minutes"
            )
        elif event == "monthly_error":
            msg = (
                f"❌ *Monthly Rebalance Error*\n"
                f"Error: {details.get('error', 'Unknown')}"
            )
        else:
            msg = f"ℹ️ APEX: {event}"

        send_message(msg)
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")


# =============================================================================
# MONTHLY FULL REBALANCE
# =============================================================================

def run_monthly_rebalance():
    """
    Full monthly rebalance — runs on 1st of every month at 01:00 UTC.

    Steps:
    1. Fetch fresh 1 year of data for all active tokens
    2. Run full permutation backtest for all tokens (in batches)
    3. Score and rank all strategies
    4. Assign best strategy per token
    5. Resume any tokens that were paused and now have valid strategy
    6. Send Telegram summary

    Bot continues trading throughout — old strategies stay active
    until new ones are written to apex.db token by token.
    """
    start_time = datetime.now(timezone.utc)

    logger.info("=" * 70)
    logger.info("APEX Monthly Full Rebalance — Starting")
    logger.info(f"Time: {start_time.isoformat()}")
    logger.info("=" * 70)

    conn     = get_db_connection()
    exchange = get_exchange()

    try:
        # Step 1 — Get active token list
        symbols = get_active_symbols(conn)
        total   = len(symbols)
        logger.info(f"Tokens to process: {total}")

        send_rebalance_alert("monthly_start", {"total": total})
        try:
            from bot.config import apex_logger
            apex_logger.rebalance_event("start", "monthly", token_count=total)
        except Exception: pass

        # Step 2 — Fetch fresh data for all tokens
        logger.info("Step 1/4 — Fetching fresh 1 year data for all tokens...")
        fetch_all_tokens(exchange, conn, symbols)

        # Step 3 — Run backtests in batches
        logger.info("Step 2/4 — Running permutation backtests...")
        batch_size   = BACKTEST["batch_size"]
        all_results  = {}

        for batch_start in range(0, total, batch_size):
            batch = symbols[batch_start: batch_start + batch_size]
            batch_num = (batch_start // batch_size) + 1
            total_batches = (total // batch_size) + 1

            logger.info(
                f"  Batch {batch_num}/{total_batches}: "
                f"{[s.replace('/USDT:USDT','') for s in batch]}"
            )

            batch_results = run_backtest_for_tokens(conn, batch)
            all_results.update(batch_results)

            # Small pause between batches to avoid overloading server
            time.sleep(2)

        # Step 4 — Score all strategies
        logger.info("Step 3/4 — Scoring and ranking strategies...")
        scored_all = score_all_tokens(all_results)
        print_scoring_summary(scored_all)

        # Step 5 — Assign best strategy per token
        logger.info("Step 4/4 — Assigning strategies...")
        summary = assign_strategies_for_all_tokens(conn, scored_all, source="monthly_rebalance")

        # Step 6 — Resume paused tokens that now have valid strategy
        _resume_eligible_tokens(conn, summary["assigned"])

        # Done
        end_time     = datetime.now(timezone.utc)
        duration_sec = (end_time - start_time).total_seconds()
        duration_min = duration_sec / 60

        logger.info("=" * 70)
        logger.info("APEX Monthly Rebalance Complete")
        logger.info(f"Duration:   {duration_min:.1f} minutes")
        logger.info(f"Assigned:   {len(summary['assigned'])} tokens")
        logger.info(f"Unassigned: {len(summary['unassigned'])} tokens")
        logger.info("=" * 70)

        send_rebalance_alert("monthly_complete", {
            "assigned":     len(summary["assigned"]),
            "unassigned":   len(summary["unassigned"]),
            "duration_mins": duration_min,
        })
        try:
            from bot.config import apex_logger
            apex_logger.rebalance_event("complete", "monthly",
                duration_seconds = duration_sec,
                tokens_changed   = len(summary["assigned"]),
                tokens_unchanged = len(summary["unassigned"]),
            )
        except Exception: pass

    except Exception as e:
        logger.error(f"Monthly rebalance failed: {e}", exc_info=True)
        send_rebalance_alert("monthly_error", {"error": str(e)})

    finally:
        conn.close()


def _resume_eligible_tokens(conn: sqlite3.Connection, assigned_symbols: list):
    """
    Resume any previously paused tokens that now have a valid strategy assigned.
    Called after monthly rebalance completes.
    """
    c = conn.cursor()
    c.execute("""
        SELECT symbol FROM token_status
        WHERE is_paused = 1
    """)
    paused_symbols = [row[0] for row in c.fetchall()]

    for symbol in paused_symbols:
        if symbol in assigned_symbols:
            resume_token(conn, symbol)
            logger.info(f"Resumed paused token after rebalance: {symbol}")


# =============================================================================
# INITIAL SETUP — Run once when deploying APEX for the first time
# =============================================================================

def run_initial_setup():
    """
    One-time setup when deploying APEX for the first time.

    Steps:
    1. Initialize databases
    2. Fetch top 100 tokens from CoinGecko + Binance
    3. Fetch 1 year of historical data for all tokens
    4. Run full backtest and strategy assignment
    5. Bot is ready to start trading

    Run this once manually before starting the bot.
    """
    start_time = datetime.now(timezone.utc)

    logger.info("=" * 70)
    logger.info("APEX Initial Setup — Starting")
    logger.info("This will take significant time — do not interrupt")
    logger.info("=" * 70)

    conn     = get_db_connection()
    exchange = get_exchange()

    # Initialize databases
    init_database(conn)
    init_strategy_db(conn)

    # Fetch token universe
    logger.info("Step 1/4 — Fetching top 100 tokens...")
    tokens = get_top_100_futures_symbols(exchange)
    added, dropped = update_universe(conn, tokens)
    logger.info(f"Universe: {len(tokens)} tokens")

    # Fetch historical data
    logger.info("Step 2/4 — Fetching 1 year historical data...")
    symbols = [t["symbol"] for t in tokens]
    fetch_all_tokens(exchange, conn, symbols)

    # Run backtests
    logger.info("Step 3/4 — Running permutation backtests...")
    logger.info("This is the longest step — may take several hours")

    batch_size  = BACKTEST["batch_size"]
    all_results = {}
    total       = len(symbols)

    for batch_start in range(0, total, batch_size):
        batch = symbols[batch_start: batch_start + batch_size]
        batch_num = (batch_start // batch_size) + 1
        total_batches = (total + batch_size - 1) // batch_size

        logger.info(
            f"  Batch {batch_num}/{total_batches}: "
            f"{[s.replace('/USDT:USDT','') for s in batch]}"
        )

        batch_results = run_backtest_for_tokens(conn, batch)
        all_results.update(batch_results)
        time.sleep(2)

    # Score and assign
    logger.info("Step 4/4 — Scoring and assigning strategies...")
    scored_all = score_all_tokens(all_results)
    print_scoring_summary(scored_all)
    summary = assign_strategies_for_all_tokens(conn, scored_all, source="initial")

    # Summary
    end_time     = datetime.now(timezone.utc)
    duration_min = (end_time - start_time).total_seconds() / 60

    logger.info("=" * 70)
    logger.info("APEX Initial Setup Complete")
    logger.info(f"Duration:   {duration_min:.1f} minutes")
    logger.info(f"Assigned:   {len(summary['assigned'])} tokens")
    logger.info(f"Unassigned: {len(summary['unassigned'])} tokens")
    logger.info("Bot is ready to start trading.")
    logger.info("=" * 70)

    # Print data summary
    data_summary = get_data_summary(conn)
    logger.info(f"\nData coverage:\n{data_summary.to_string()}")

    conn.close()

    try:
        from telegram_bot import send_message
        send_message(
            f"✅ *APEX Initial Setup Complete*\n"
            f"Tokens assigned: {len(summary['assigned'])}\n"
            f"Duration: {duration_min:.1f} mins\n"
            f"Bot is ready to start."
        )
    except Exception:
        pass


# =============================================================================
# SCHEDULER — Runs automated weekly + monthly rebalance
# =============================================================================

def start_scheduler():
    """
    Start the APEX automated rebalancing scheduler.
    Runs as a background process alongside the trading bot.

    Weekly:  Every Sunday at 00:00 UTC
    Monthly: 1st of every month at 01:00 UTC
    """
    scheduler = BlockingScheduler(timezone="UTC")

    if REBALANCE["weekly"]["enabled"]:
        scheduler.add_job(
            run_weekly_refresh,
            CronTrigger(
                day_of_week="sun",
                hour=REBALANCE["weekly"]["hour"],
                minute=REBALANCE["weekly"]["minute"],
                timezone="UTC",
            ),
            id="weekly_refresh",
            name="APEX Weekly Universe Refresh",
            misfire_grace_time=3600,  # Allow up to 1 hour late start
        )
        logger.info(
            f"Weekly refresh scheduled: "
            f"Every Sunday {REBALANCE['weekly']['hour']:02d}:{REBALANCE['weekly']['minute']:02d} UTC"
        )

    if REBALANCE["monthly"]["enabled"]:
        scheduler.add_job(
            run_monthly_rebalance,
            CronTrigger(
                day=REBALANCE["monthly"]["day"],
                hour=REBALANCE["monthly"]["hour"],
                minute=REBALANCE["monthly"]["minute"],
                timezone="UTC",
            ),
            id="monthly_rebalance",
            name="APEX Monthly Full Rebalance",
            misfire_grace_time=3600,
        )
        logger.info(
            f"Monthly rebalance scheduled: "
            f"Day {REBALANCE['monthly']['day']} of every month at "
            f"{REBALANCE['monthly']['hour']:02d}:{REBALANCE['monthly']['minute']:02d} UTC"
        )

    logger.info("APEX Scheduler started — waiting for scheduled jobs...")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("APEX Scheduler stopped.")
        scheduler.shutdown()


# =============================================================================
# STATUS & MONITORING
# =============================================================================

def get_rebalance_status() -> dict:
    """
    Return current APEX status for dashboard display.
    """
    conn = get_db_connection()
    try:
        summary    = get_assignment_summary(conn)
        active_uni = get_active_universe(conn)

        return {
            "total_tokens_in_universe": len(active_uni),
            "total_strategies_assigned": summary["total_assigned"],
            "total_paused":              summary["total_paused"],
            "tier_counts":               summary["tier_counts"],
            "timeframe_counts":          summary["tf_counts"],
        }
    except Exception as e:
        logger.error(f"Error getting rebalance status: {e}")
        return {}
    finally:
        conn.close()


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="APEX Rebalancer")
    parser.add_argument(
        "command",
        choices=["setup", "rebalance", "weekly", "schedule", "status"],
        help=(
            "setup     = Run initial one-time setup\n"
            "rebalance = Run monthly rebalance now\n"
            "weekly    = Run weekly refresh now\n"
            "schedule  = Start automated scheduler\n"
            "status    = Print current APEX status"
        )
    )
    args = parser.parse_args()

    if args.command == "setup":
        # FIX 2026-04-10 audit C-γ: 'setup' wipes apex.db schema and all strategy assignments.
        # Previously had ZERO confirmation prompt — running on a live server destroyed all
        # strategies and triggered hours of re-backtesting. Now requires explicit confirmation.
        import sys
        print()
        print("=" * 70)
        print("WARNING: 'setup' will reinitialize apex.db schema.")
        print("All current strategy_assignments will be LOST.")
        print("The bot will have no strategies until backtests repopulate them")
        print("(monthly rebalance can take several hours).")
        print("=" * 70)
        print()
        confirm = input("Type 'WIPE APEX DB' (in capitals) to confirm, or anything else to abort: ")
        if confirm != "WIPE APEX DB":
            print("Aborted — confirmation phrase did not match.")
            sys.exit(1)
        run_initial_setup()

    elif args.command == "rebalance":
        run_monthly_rebalance()

    elif args.command == "weekly":
        run_weekly_refresh()

    elif args.command == "schedule":
        start_scheduler()

    elif args.command == "status":
        status = get_rebalance_status()
        print("\nAPEX Status:")
        for k, v in status.items():
            print(f"  {k}: {v}")

# __APEX_LOGGER_V1__
