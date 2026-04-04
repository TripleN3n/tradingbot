# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/risk_manager.py — Risk Management & Drawdown Circuit Breakers
# =============================================================================
# RESPONSIBILITY:
# Monitors portfolio-level risk and triggers circuit breakers when
# drawdown thresholds are breached. Protects against cascading
# correlated losses in broad market crashes.
#
# WHAT THIS FILE DOES:
# - Tracks drawdown from peak capital in real time
# - Triggers Telegram alerts at 20% drawdown
# - Pauses new entries at 35% drawdown
# - Stops bot completely and closes all trades at 50% drawdown
# - Resets circuit breakers when capital recovers
# - Provides manual override via Telegram commands
# - Logs all risk events for audit trail
#
# WHAT THIS FILE DOES NOT DO:
# - Does not manage individual trade SL (that's trade_manager.py)
# - Does not generate signals (that's signal_engine.py)
# - Does not manage capital slots (that's capital_manager.py)
# =============================================================================

import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.config import (
    INITIAL_CAPITAL, DRAWDOWN, LOGS, DB,
)

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)


# =============================================================================
# RISK STATE
# =============================================================================

class RiskState:
    """
    Tracks current risk state of the bot.
    Single instance maintained in main.py and passed through the cycle.

    States:
    - ACTIVE:  Normal trading — all operations allowed
    - PAUSED:  New entries blocked — existing trades run to completion
    - STOPPED: Bot fully stopped — all trades closed
    """

    def __init__(self):
        self.state          = "ACTIVE"
        self.peak_capital   = INITIAL_CAPITAL
        self.current_dd_pct = 0.0
        self.alert_sent     = False   # 20% alert already sent this drawdown cycle
        self.paused_at      = None
        self.stopped_at     = None
        self.manual_override = False  # Set by Telegram command

    def is_active(self) -> bool:
        return self.state == "ACTIVE"

    def is_paused(self) -> bool:
        return self.state == "PAUSED"

    def is_stopped(self) -> bool:
        return self.state == "STOPPED"

    def can_open_trades(self) -> bool:
        """Returns True only when bot is fully active."""
        return self.state == "ACTIVE"

    def set_active(self):
        self.state        = "ACTIVE"
        self.paused_at    = None
        self.stopped_at   = None
        self.alert_sent   = False
        logger.info("Risk state: ACTIVE")

    def set_paused(self):
        self.state     = "PAUSED"
        self.paused_at = datetime.now(timezone.utc).isoformat()
        logger.warning("Risk state: PAUSED — new entries blocked")

    def set_stopped(self):
        self.state      = "STOPPED"
        self.stopped_at = datetime.now(timezone.utc).isoformat()
        logger.critical("Risk state: STOPPED — all trading halted")

    def update_peak(self, capital: float):
        """Update peak capital — only moves up, never down."""
        if capital > self.peak_capital:
            self.peak_capital = capital

    def calculate_drawdown(self, capital: float) -> float:
        """Calculate current drawdown from peak as percentage."""
        if self.peak_capital <= 0:
            return 0.0
        dd = (self.peak_capital - capital) / self.peak_capital * 100
        self.current_dd_pct = max(0.0, dd)
        return self.current_dd_pct

    def to_dict(self) -> dict:
        return {
            "state":           self.state,
            "peak_capital":    round(self.peak_capital, 2),
            "drawdown_pct":    round(self.current_dd_pct, 2),
            "alert_sent":      self.alert_sent,
            "paused_at":       self.paused_at,
            "stopped_at":      self.stopped_at,
            "manual_override": self.manual_override,
        }


# =============================================================================
# DRAWDOWN CALCULATION
# =============================================================================

def calculate_drawdown_from_db(conn: sqlite3.Connection) -> dict:
    """
    Calculate drawdown from trade history in database.
    Used on bot startup to restore accurate peak capital.

    Returns dict with peak_capital, current_capital, drawdown_pct.
    """
    import numpy as np

    c = conn.cursor()

    # Get all closed trade PnLs in chronological order
    c.execute("""
        SELECT pnl_usdt FROM trades
        WHERE status = 'closed'
        ORDER BY exit_time ASC
    """)
    pnls = [row[0] for row in c.fetchall()]

    # Get current capital
    c.execute("SELECT capital FROM portfolio ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    current_capital = row[0] if row else INITIAL_CAPITAL

    if not pnls:
        return {
            "peak_capital":    INITIAL_CAPITAL,
            "current_capital": current_capital,
            "drawdown_pct":    0.0,
        }

    # Reconstruct capital curve
    capital_curve = np.cumsum(pnls) + INITIAL_CAPITAL
    peak_capital  = float(np.max(capital_curve))
    peak_capital  = max(peak_capital, INITIAL_CAPITAL)

    drawdown_pct = max(
        0.0,
        (peak_capital - current_capital) / peak_capital * 100
    )

    return {
        "peak_capital":    round(peak_capital, 2),
        "current_capital": round(current_capital, 2),
        "drawdown_pct":    round(drawdown_pct, 2),
    }


# =============================================================================
# CIRCUIT BREAKER LOGIC
# =============================================================================

def check_circuit_breakers(
    risk_state: RiskState,
    capital: float,
    open_trades: list,
    conn: sqlite3.Connection,
) -> RiskState:
    """
    Check drawdown levels and trigger circuit breakers as needed.
    Called every bot cycle.

    Thresholds (from config):
    - 20% drawdown → Telegram alert, bot continues
    - 35% drawdown → Pause new entries, existing trades finish
    - 50% drawdown → Stop bot, close all trades

    Returns updated RiskState.
    """
    # Update peak capital
    risk_state.update_peak(capital)

    # Calculate current drawdown
    dd_pct = risk_state.calculate_drawdown(capital)

    alert_threshold  = DRAWDOWN["alert_pct"]  * 100
    pause_threshold  = DRAWDOWN["pause_pct"]  * 100
    stop_threshold   = DRAWDOWN["stop_pct"]   * 100

    # Manual override — skip circuit breaker checks
    if risk_state.manual_override:
        logger.debug(f"Manual override active — skipping circuit breakers (DD: {dd_pct:.1f}%)")
        return risk_state

    # STOP threshold — highest priority
    if dd_pct >= stop_threshold:
        if not risk_state.is_stopped():
            logger.critical(
                f"CIRCUIT BREAKER: STOP triggered at {dd_pct:.1f}% drawdown "
                f"(threshold: {stop_threshold}%)"
            )
            risk_state.set_stopped()
            try:
                from bot.config import apex_logger
                apex_logger.drawdown_event(
                    "stop_50", dd_pct, "stopped_all",
                    risk_state.peak_capital, capital, len(open_trades)
                )
            except Exception: pass
            _close_all_trades(conn, open_trades)
            _send_circuit_breaker_alert("stop", dd_pct, capital)
        return risk_state

    # PAUSE threshold
    if dd_pct >= pause_threshold:
        if not risk_state.is_paused() and not risk_state.is_stopped():
            logger.warning(
                f"CIRCUIT BREAKER: PAUSE triggered at {dd_pct:.1f}% drawdown "
                f"(threshold: {pause_threshold}%)"
            )
            risk_state.set_paused()
            try:
                from bot.config import apex_logger
                apex_logger.drawdown_event(
                    "pause_35", dd_pct, "paused_new_entries",
                    risk_state.peak_capital, capital, len(open_trades)
                )
            except Exception: pass
            _send_circuit_breaker_alert("pause", dd_pct, capital)
        return risk_state

    # ALERT threshold
    if dd_pct >= alert_threshold:
        if not risk_state.alert_sent:
            logger.warning(
                f"CIRCUIT BREAKER: ALERT at {dd_pct:.1f}% drawdown "
                f"(threshold: {alert_threshold}%)"
            )
            risk_state.alert_sent = True
            try:
                from bot.config import apex_logger
                apex_logger.drawdown_event(
                    "alert_20", dd_pct, "telegram_alert",
                    risk_state.peak_capital, capital, len(open_trades)
                )
            except Exception: pass
            _send_circuit_breaker_alert("alert", dd_pct, capital)
        return risk_state

    # Capital has recovered — reset states
    if dd_pct < alert_threshold:
        if risk_state.is_paused():
            logger.info(
                f"Drawdown recovered to {dd_pct:.1f}% — "
                f"resuming from PAUSED state"
            )
            risk_state.set_active()
            _send_circuit_breaker_alert("recovered", dd_pct, capital)

        elif risk_state.alert_sent and dd_pct < alert_threshold * 0.8:
            # Reset alert flag when drawdown drops meaningfully below threshold
            risk_state.alert_sent = False
            logger.info(f"Drawdown alert reset — DD now at {dd_pct:.1f}%")

    return risk_state


def _close_all_trades(conn: sqlite3.Connection, open_trades: list):
    """
    Emergency close all open trades at market price.
    Called when 50% stop threshold is triggered.
    """
    if not open_trades:
        return

    logger.critical(f"Emergency closing {len(open_trades)} open trades...")

    from bot.trade_manager import close_trade
    from bot.data_feed import fetch_current_prices

    symbols = [t["symbol"] for t in open_trades]
    prices  = fetch_current_prices(symbols)

    for trade in open_trades:
        symbol       = trade["symbol"]
        exit_price   = prices.get(symbol, trade["avg_entry_price"])

        try:
            close_trade(conn, trade, exit_price, "emergency_stop")
            logger.info(f"Emergency closed: {symbol} at {exit_price:.4f}")
        except Exception as e:
            logger.error(f"Emergency close failed for {symbol}: {e}")


# =============================================================================
# TELEGRAM ALERTS
# =============================================================================

def _send_circuit_breaker_alert(event: str, dd_pct: float, capital: float):
    """Send Telegram alert for circuit breaker events."""
    try:
        from telegram_bot import send_message

        messages = {
            "alert": (
                f"⚠️ *Drawdown Alert*\n"
                f"Current drawdown: *{dd_pct:.1f}%*\n"
                f"Capital: ${capital:,.2f}\n"
                f"Bot continues trading — monitoring closely."
            ),
            "pause": (
                f"🔴 *Drawdown Pause Triggered*\n"
                f"Current drawdown: *{dd_pct:.1f}%*\n"
                f"Capital: ${capital:,.2f}\n"
                f"New entries BLOCKED. Existing trades finishing naturally.\n"
                f"Bot will resume when drawdown recovers below threshold."
            ),
            "stop": (
                f"🚨 *EMERGENCY STOP TRIGGERED*\n"
                f"Current drawdown: *{dd_pct:.1f}%*\n"
                f"Capital: ${capital:,.2f}\n"
                f"ALL trades closed. Bot completely halted.\n"
                f"Manual intervention required to restart."
            ),
            "recovered": (
                f"✅ *Drawdown Recovered*\n"
                f"Current drawdown: *{dd_pct:.1f}%*\n"
                f"Capital: ${capital:,.2f}\n"
                f"Bot resuming normal trading."
            ),
        }

        msg = messages.get(event, f"Risk event: {event} at {dd_pct:.1f}%")
        send_message(msg)

    except Exception as e:
        logger.warning(f"Circuit breaker Telegram alert failed: {e}")


# =============================================================================
# MANUAL OVERRIDE — via Telegram commands
# =============================================================================

def manual_resume(risk_state: RiskState, conn: sqlite3.Connection) -> str:
    """
    Manually resume the bot from PAUSED or STOPPED state.
    Called by telegram_bot.py when user sends /resume command.

    Returns status message.
    """
    if risk_state.is_active():
        return "Bot is already active — nothing to resume."

    prev_state            = risk_state.state
    risk_state.set_active()
    risk_state.manual_override = True

    logger.warning(
        f"Manual override: Bot resumed from {prev_state} state. "
        f"Circuit breakers temporarily disabled."
    )

    return (
        f"✅ Bot manually resumed from {prev_state}.\n"
        f"Manual override is ACTIVE — circuit breakers paused.\n"
        f"Use /auto to re-enable automatic circuit breakers."
    )


def manual_pause(risk_state: RiskState) -> str:
    """
    Manually pause the bot via Telegram.
    Returns status message.
    """
    risk_state.set_paused()
    risk_state.manual_override = True

    logger.warning("Manual override: Bot paused via Telegram command.")

    return "⏸ Bot manually paused. New entries blocked. Existing trades continue."


def manual_stop(risk_state: RiskState, conn: sqlite3.Connection, open_trades: list) -> str:
    """
    Manually stop the bot and close all trades via Telegram.
    Returns status message.
    """
    risk_state.set_stopped()
    risk_state.manual_override = True
    _close_all_trades(conn, open_trades)

    logger.warning("Manual override: Bot stopped and all trades closed via Telegram command.")

    return "🛑 Bot manually stopped. All trades closed at market."


def enable_auto_circuit_breakers(risk_state: RiskState) -> str:
    """
    Re-enable automatic circuit breakers after manual override.
    Returns status message.
    """
    risk_state.manual_override = False
    logger.info("Automatic circuit breakers re-enabled.")
    return "✅ Automatic circuit breakers re-enabled."


# =============================================================================
# RISK REPORTING
# =============================================================================

def get_risk_report(
    risk_state: RiskState,
    capital: float,
    open_trades: list,
) -> dict:
    """
    Return comprehensive risk report for dashboard and logging.
    """
    from bot.capital_manager import get_capital_utilisation

    util = get_capital_utilisation(capital, open_trades)

    return {
        "bot_state":         risk_state.state,
        "drawdown_pct":      round(risk_state.current_dd_pct, 2),
        "peak_capital":      round(risk_state.peak_capital, 2),
        "current_capital":   round(capital, 2),
        "capital_deployed":  round(util["deployed"], 2),
        "capital_available": round(util["available"], 2),
        "utilisation_pct":   round(util["utilisation_pct"], 1),
        "open_trades":       len(open_trades),
        "alert_threshold":   DRAWDOWN["alert_pct"]  * 100,
        "pause_threshold":   DRAWDOWN["pause_pct"]  * 100,
        "stop_threshold":    DRAWDOWN["stop_pct"]   * 100,
        "manual_override":   risk_state.manual_override,
    }


def log_risk_report(risk_state: RiskState, capital: float, open_trades: list):
    """Log current risk status — called periodically from main.py."""
    report = get_risk_report(risk_state, capital, open_trades)
    logger.info(
        f"Risk Report | "
        f"State: {report['bot_state']} | "
        f"DD: {report['drawdown_pct']:.1f}% | "
        f"Capital: ${report['current_capital']:,.2f} | "
        f"Peak: ${report['peak_capital']:,.2f} | "
        f"Open trades: {report['open_trades']} | "
        f"Deployed: {report['utilisation_pct']:.1f}%"
    )


# =============================================================================
# STARTUP — Restore risk state from database
# =============================================================================

def restore_risk_state(conn: sqlite3.Connection) -> RiskState:
    """
    Restore risk state on bot startup.
    Recalculates peak capital from trade history so drawdown
    is accurate even after a restart.
    """
    risk_state = RiskState()

    dd_data = calculate_drawdown_from_db(conn)
    risk_state.peak_capital   = dd_data["peak_capital"]
    risk_state.current_dd_pct = dd_data["drawdown_pct"]

    logger.info(
        f"Risk state restored — "
        f"Peak: ${risk_state.peak_capital:,.2f} | "
        f"DD: {risk_state.current_dd_pct:.1f}%"
    )

    return risk_state


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    from bot.trade_manager import get_trades_conn, init_trades_db

    conn = get_trades_conn()
    init_trades_db(conn)

    risk_state = restore_risk_state(conn)
    print(f"\nRisk State: {risk_state.to_dict()}")

    # Simulate drawdown check
    test_capital = INITIAL_CAPITAL * 0.75  # 25% drawdown
    risk_state   = check_circuit_breakers(risk_state, test_capital, [], conn)
    print(f"\nAfter 25% drawdown: {risk_state.to_dict()}")

    conn.close()

# __APEX_LOGGER_V1__
