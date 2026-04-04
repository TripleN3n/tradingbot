# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/main.py — Main Bot Loop
# Version 3.0 — Multi-Timeframe Strategy
# =============================================================================

import sqlite3
import logging
import time
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

sys.path.append(str(Path(__file__).resolve().parent.parent))

from bot.config import PAPER_TRADING, INITIAL_CAPITAL, LOGS, DB, apex_logger, get_config_dict
from bot.data_feed import fetch_cycle_data
from bot.signal_engine import generate_signals, build_price_history
from bot.capital_manager import (
    SlotTracker, SignalQueue,
    allocate_signals, prepare_execution,
    release_slot, get_capital_status,
)
from bot.trade_manager import (
    init_trades_db, get_trades_conn,
    get_open_trades, get_capital, update_capital,
    open_trade, monitor_open_trades,
    get_performance_stats,
)
from bot.risk_manager import (
    RiskState, restore_risk_state,
    check_circuit_breakers, log_risk_report,
)
from bot.performance_monitor import (
    check_all_tokens_performance,
    log_performance_summary,
)
from bot.filters import decrement_cooldowns
from apex.data_fetcher import get_db_connection
from apex.strategy_assigner import get_all_active_strategies

# =============================================================================
# LOGGING
# =============================================================================

Path(LOGS["bot"]).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOGS["level"]),
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOGS["bot"]),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


# =============================================================================
# BOT STATE
# =============================================================================

class BotState:
    def __init__(self):
        self.risk_state       = RiskState()
        self.slot_tracker     = SlotTracker()
        self.signal_queue     = SignalQueue()
        self.cooldown_tracker = {}
        self.cycle_count      = 0
        self.started_at       = datetime.now(timezone.utc).isoformat()
        self.last_cycle_at    = None
        self.trades_conn      = None
        self.apex_conn        = None

    def get_trades_conn(self):
        try:
            self.trades_conn.execute("SELECT 1")
        except Exception:
            self.trades_conn = get_trades_conn()
        return self.trades_conn

    def get_apex_conn(self):
        try:
            self.apex_conn.execute("SELECT 1")
        except Exception:
            self.apex_conn = get_db_connection()
        return self.apex_conn


_bot_state = None

def get_bot_state() -> BotState:
    global _bot_state
    if _bot_state is None:
        _bot_state = BotState()
    return _bot_state


# =============================================================================
# TELEGRAM HELPERS
# =============================================================================

def send_telegram(msg: str):
    try:
        from telegram_bot import send_message
        send_message(msg)
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")


def send_startup_alert(paper: bool):
    mode = "PAPER TRADING" if paper else "LIVE TRADING"
    send_telegram(
        f"🚀 *AUTO-TRADING AI AGENT Started*\n"
        f"Mode: {mode} | Strategy: MTF v3.0\n"
        f"Capital: ${INITIAL_CAPITAL:,.2f} USDT\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )


def send_trade_alert(trade: dict, event: str):
    try:
        from telegram_bot import send_trade_notification
        send_trade_notification(trade, event)
    except Exception as e:
        logger.warning(f"Trade alert failed: {e}")


# =============================================================================
# MAIN BOT CYCLE
# =============================================================================

def run_cycle():
    """
    Execute one complete bot cycle.
    Runs every hour via APScheduler.

    MTF v3.0 cycle:
    1. Load active strategies from apex.db
    2. Fetch all 3 timeframes per token (1H, 4H, 1D)
    3. Monitor open trades
    4. Check drawdown circuit breakers
    5. Generate MTF signals (1D+4H confirm, 1H triggers)
    6. Allocate signals to slots
    7. Execute approved signals
    8. Check rolling performance
    9. Decrement cooldowns
    10. Log summary
    """
    state = get_bot_state()
    state.cycle_count += 1
    cycle_start = time.time()

    logger.info(
        f"{'='*60}\n"
        f"Cycle #{state.cycle_count} | "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    trades_conn = state.get_trades_conn()
    apex_conn   = state.get_apex_conn()

    try:
        # ----------------------------------------------------------------
        # STEP 1 — Get active strategies
        # ----------------------------------------------------------------
        strategies = get_all_active_strategies(apex_conn)

        if not strategies:
            logger.warning("No active strategies — waiting for APEX rebalance")
            return

        logger.info(f"Active strategies: {len(strategies)}")

        # ----------------------------------------------------------------
        # STEP 2 — Fetch all 3 timeframes per token
        # ----------------------------------------------------------------
        cycle_data = fetch_cycle_data(strategies)
        ohlcv_data = cycle_data.get("ohlcv", {})

        if not ohlcv_data:
            logger.error("No OHLCV data — skipping cycle")
            return

        # Log cycle heartbeat with full market context
        _btc_d = cycle_data.get("btc_trend", {})
        _fg_d  = cycle_data.get("fear_greed", {})
        apex_logger.cycle_start(
            cycle_number         = state.cycle_count,
            open_trades          = len(get_open_trades(trades_conn)),
            capital_deployed_pct = 0.0,
            fg_index             = _fg_d.get("value", 50),
            fg_label             = _fg_d.get("label", "Unknown"),
            btc_trend            = _btc_d.get("direction", "neutral"),
            btc_price            = 0.0,
            total_equity         = get_capital(trades_conn),
        )

        # ----------------------------------------------------------------
        # STEP 3 — Monitor open trades
        # ----------------------------------------------------------------
        open_trades = get_open_trades(trades_conn)

        if open_trades:
            logger.info(f"Monitoring {len(open_trades)} open trade(s)...")
            closed_trades, state.cooldown_tracker = monitor_open_trades(
                trades_conn, open_trades, ohlcv_data, state.cooldown_tracker
            )

            for closed in closed_trades:
                release_slot(state.slot_tracker, closed["tier"], closed["symbol"])
                send_trade_alert(closed, "close")

            if closed_trades:
                logger.info(f"Closed {len(closed_trades)} trade(s) this cycle")

        # ----------------------------------------------------------------
        # STEP 4 — Refresh capital and open trades
        # ----------------------------------------------------------------
        capital     = get_capital(trades_conn)
        open_trades = get_open_trades(trades_conn)

        # ----------------------------------------------------------------
        # STEP 5 — Check drawdown circuit breakers
        # ----------------------------------------------------------------
        state.risk_state = check_circuit_breakers(
            state.risk_state, capital, open_trades, trades_conn
        )

        # ----------------------------------------------------------------
        # STEP 6 — Generate MTF signals
        # ----------------------------------------------------------------
        signals = []

        if state.risk_state.can_open_trades():
            price_history = build_price_history(ohlcv_data)

            signals = generate_signals(
                strategies       = strategies,
                cycle_data       = cycle_data,
                open_trades      = open_trades,
                price_history    = price_history,
                cooldown_tracker = state.cooldown_tracker,
            )
        else:
            logger.info(f"Bot state: {state.risk_state.state} — signals skipped")

        # ----------------------------------------------------------------
        # STEP 7 — Allocate signals to slots
        # ----------------------------------------------------------------
        execute_list = []

        if signals:
            execute_list, state.signal_queue, state.slot_tracker = allocate_signals(
                signals        = signals,
                slot_tracker   = state.slot_tracker,
                signal_queue   = state.signal_queue,
                open_trades    = open_trades,
                capital        = capital,
                current_ohlcv  = ohlcv_data,
            )

        # ----------------------------------------------------------------
        # STEP 8 — Execute signals
        # ----------------------------------------------------------------
        for signal in execute_list:
            prepared = prepare_execution(signal)

            if prepared is None:
                logger.warning(f"Execution prep failed: {signal['symbol']}")
                continue

            trade_id = open_trade(trades_conn, prepared)

            if trade_id:
                send_trade_alert({**prepared, "id": trade_id}, "open")
            else:
                release_slot(state.slot_tracker, prepared["tier"], prepared["symbol"])
                logger.error(f"Trade open failed: {prepared['symbol']}")

        # ----------------------------------------------------------------
        # STEP 9 — Rolling performance check
        # ----------------------------------------------------------------
        active_symbols = [s["symbol"] for s in strategies]
        check_all_tokens_performance(trades_conn, active_symbols)

        # ----------------------------------------------------------------
        # STEP 10 — Decrement cooldowns
        # ----------------------------------------------------------------
        state.cooldown_tracker = decrement_cooldowns(state.cooldown_tracker)

        # ----------------------------------------------------------------
        # STEP 11 — Log cycle summary
        # ----------------------------------------------------------------
        open_trades = get_open_trades(trades_conn)
        cap_status  = get_capital_status(
            capital, open_trades, state.slot_tracker, state.signal_queue
        )

        elapsed = time.time() - cycle_start
        state.last_cycle_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"Cycle #{state.cycle_count} done in {elapsed:.1f}s | "
            f"Capital: ${capital:,.2f} | "
            f"Open: {len(open_trades)} | "
            f"Deployed: {cap_status['utilisation_pct']:.1f}% | "
            f"Queue: {cap_status['queue_size']} | "
            f"DD: {state.risk_state.current_dd_pct:.1f}% | "
            f"State: {state.risk_state.state}"
        )

        # Detailed log every 24 cycles (~24 hours)
        if state.cycle_count % 24 == 0:
            log_risk_report(state.risk_state, capital, open_trades)
            log_performance_summary(trades_conn, active_symbols)

    except Exception as e:
        elapsed = time.time() - cycle_start
        logger.error(f"Cycle #{state.cycle_count} FAILED after {elapsed:.1f}s: {e}", exc_info=True)
        apex_logger.bot_error(str(e), {"cycle_number": state.cycle_count})
        send_telegram(
            f"❌ *Bot Cycle Error*\n"
            f"Cycle: #{state.cycle_count}\n"
            f"Error: {str(e)[:200]}"
        )


# =============================================================================
# STARTUP
# =============================================================================

def startup():
    logger.info("=" * 60)
    logger.info("AUTO-TRADING AI AGENT v3.0 — Starting")
    logger.info(f"Mode: {'PAPER' if PAPER_TRADING else 'LIVE'} | Strategy: MTF")
    logger.info(f"Capital: ${INITIAL_CAPITAL:,.2f} USDT")
    logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    state = get_bot_state()

    state.trades_conn = get_trades_conn()
    state.apex_conn   = get_db_connection()

    init_trades_db(state.trades_conn)

    capital = get_capital(state.trades_conn)
    if capital == INITIAL_CAPITAL:
        update_capital(state.trades_conn, INITIAL_CAPITAL)
        logger.info(f"Capital initialized: ${INITIAL_CAPITAL:,.2f}")

    state.risk_state = restore_risk_state(state.trades_conn)

    strategies = get_all_active_strategies(state.apex_conn)
    if not strategies:
        logger.critical(
            "No strategies in APEX! Run: python3 -m apex.rebalancer setup"
        )
        send_telegram(
            "🚨 *Bot startup failed*\n"
            "No APEX strategies found.\n"
            "Run initial setup first."
        )
        sys.exit(1)

    logger.info(f"APEX strategies loaded: {len(strategies)}")

    open_trades = get_open_trades(state.trades_conn)
    if open_trades:
        logger.info(f"Resuming with {len(open_trades)} open trade(s)")
        state.slot_tracker.sync_from_trades(open_trades)

    # Start Telegram command listener
    try:
        from telegram_bot import start_command_listener
        start_command_listener()
    except Exception as e:
        logger.warning(f"Telegram listener not started: {e}")

    apex_logger.bot_start(
        version         = "3.0",
        mode            = "PAPER" if PAPER_TRADING else "LIVE",
        initial_capital = INITIAL_CAPITAL,
        config_snapshot = get_config_dict(),
    )
    send_startup_alert(PAPER_TRADING)
    logger.info("Startup complete — entering main loop")
    logger.info("=" * 60)


# =============================================================================
# SCHEDULER
# =============================================================================

def start():
    """Start the trading bot. Runs one cycle immediately then every hour."""
    startup()

    scheduler = BlockingScheduler(timezone="UTC")

    # Run first cycle immediately
    run_cycle()

    # Then every hour
    scheduler.add_job(
        run_cycle,
        IntervalTrigger(hours=1),
        id="main_cycle",
        name="Main Trading Cycle",
        misfire_grace_time=300,
        max_instances=1,
    )

    logger.info("Scheduler started — running every hour")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
        scheduler.shutdown()
        _cleanup()


def _cleanup():
    state = get_bot_state()
    try:
        if state.trades_conn: state.trades_conn.close()
        if state.apex_conn:   state.apex_conn.close()
    except Exception:
        pass
    apex_logger.bot_stop("shutdown", 0.0, get_bot_state().cycle_count, 0)
    logger.info("Shutdown complete.")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    start()

# __APEX_LOGGER_V1__
