# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# telegram_bot.py — Alerts & Manual Override Commands
# =============================================================================
# RESPONSIBILITY:
# Sends automated Telegram alerts for all bot events.
# Provides manual control commands for the bot via Telegram.
#
# WHAT THIS FILE DOES:
# - Sends trade open/close alerts with full details
# - Sends drawdown circuit breaker alerts
# - Sends rebalance completion alerts
# - Sends token pause/resume alerts
# - Receives commands from user to control the bot:
#   /status   — current bot status
#   /trades   — list open trades
#   /pause    — pause new entries
#   /resume   — resume from pause
#   /stop     — emergency stop
#   /auto     — re-enable circuit breakers
#   /pnl      — performance summary
#   /golive   — check go-live criteria
# - Runs as a separate thread alongside main.py
#
# WHAT THIS FILE DOES NOT DO:
# - Does not execute trades (that's trade_manager.py)
# - Does not manage risk (that's risk_manager.py)
# - Does not generate signals (that's signal_engine.py)
# =============================================================================

import requests
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from bot.config import TELEGRAM, INITIAL_CAPITAL, PAPER_TRADING, LOGS

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)

# =============================================================================
# TELEGRAM API
# =============================================================================

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _api_url(method: str) -> str:
    return TELEGRAM_API_BASE.format(
        token=TELEGRAM["bot_token"],
        method=method,
    )


def send_message(
    text: str,
    parse_mode: str = "Markdown",
    chat_id: str = None,
) -> bool:
    """
    Send a Telegram message.
    Returns True if successful, False on failure.
    Silent failure — never crashes the bot.
    """
    if not TELEGRAM["enabled"]:
        return False

    if not TELEGRAM["bot_token"] or not TELEGRAM["chat_id"]:
        logger.debug("Telegram not configured — skipping alert")
        return False

    target_chat = chat_id or TELEGRAM["chat_id"]

    try:
        response = requests.post(
            _api_url("sendMessage"),
            json={
                "chat_id":    target_chat,
                "text":       text,
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
        if not response.ok:
            logger.warning(f"Telegram send failed: {response.status_code} {response.text[:100]}")
            return False
        return True

    except Exception as e:
        logger.warning(f"Telegram error: {e}")
        return False


# =============================================================================
# TRADE NOTIFICATIONS
# =============================================================================

def send_trade_notification(trade: dict, event: str):
    """
    Send trade open or close notification.

    event: 'open' or 'close'
    """
    if not TELEGRAM["alerts"].get(f"trade_{event}", True):
        return

    symbol    = trade.get("symbol", "").replace("/USDT:USDT", "")
    direction = trade.get("direction", "").upper()
    tier      = trade.get("tier", "").replace("tier", "Tier ")
    timeframe = trade.get("timeframe", "").upper()

    if event == "open":
        entry     = trade.get("entry_price", trade.get("avg_entry_price", 0))
        sl        = trade.get("stop_loss", 0)
        tp        = trade.get("take_profit", 0)
        size      = trade.get("position_size_usdt", 0)
        leverage  = trade.get("leverage", 1)
        score     = trade.get("signal_score", 0)
        rrr       = trade.get("rrr", 0)

        direction_emoji = "🟢" if direction == "LONG" else "🔴"

        msg = (
            f"{direction_emoji} *Trade Opened*\n"
            f"Token: *{symbol}* {direction}\n"
            f"Tier: {tier} | TF: {timeframe}\n"
            f"Entry:  `{entry:.4f}`\n"
            f"SL:     `{sl:.4f}`\n"
            f"TP:     `{tp:.4f}`\n"
            f"RRR:    1:{rrr:.2f}\n"
            f"Size:   ${size:.2f} × {leverage}x\n"
            f"Score:  {score:.4f}\n"
            f"Time:   {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )

    elif event == "close":
        exit_price = trade.get("exit_price", 0)
        pnl        = trade.get("pnl_usdt", 0)
        pnl_pct    = trade.get("pnl_pct", 0)
        reason     = trade.get("exit_reason", "unknown").replace("_", " ").title()

        pnl_emoji = "✅" if pnl > 0 else "❌"

        reason_emojis = {
            "Stop Loss":   "🛑",
            "Take Profit": "🎯",
            "Time Stop":   "⏱",
            "Emergency Stop": "🚨",
            "Manual":      "👤",
        }
        reason_emoji = reason_emojis.get(reason, "📤")

        msg = (
            f"{pnl_emoji} *Trade Closed*\n"
            f"Token: *{symbol}* {direction}\n"
            f"Exit:   `{exit_price:.4f}`\n"
            f"PnL:    `{pnl:+.2f} USDT ({pnl_pct:+.2f}%)`\n"
            f"{reason_emoji} Reason: {reason}\n"
            f"Time:   {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )
    else:
        return

    send_message(msg)


# =============================================================================
# COMMAND HANDLERS
# =============================================================================

def _handle_status(chat_id: str):
    """Return current bot status."""
    try:
        from bot.trade_manager import get_trades_conn, get_capital, get_open_trades
        from bot.risk_manager import get_risk_report
        from bot.capital_manager import SlotTracker, SignalQueue, get_capital_status

        conn        = get_trades_conn()
        capital     = get_capital(conn)
        open_trades = get_open_trades(conn)

        from bot.main import get_bot_state
        state      = get_bot_state()
        cap_status = get_capital_status(
            capital, open_trades, state.slot_tracker, state.signal_queue
        )

        mode = "📄 PAPER" if PAPER_TRADING else "💰 LIVE"

        msg = (
            f"📊 *Bot Status*\n"
            f"Mode: {mode}\n"
            f"State: *{state.risk_state.state}*\n"
            f"Capital: `${capital:,.2f}` / `${INITIAL_CAPITAL:,.2f}`\n"
            f"PnL: `${capital - INITIAL_CAPITAL:+,.2f}`\n"
            f"Drawdown: `{state.risk_state.current_dd_pct:.1f}%`\n"
            f"Open trades: `{len(open_trades)}`\n"
            f"Deployed: `{cap_status['utilisation_pct']:.1f}%`\n"
            f"Queue: `{cap_status['queue_size']} signals`\n"
            f"Cycles run: `{state.cycle_count}`\n"
            f"Started: {state.started_at[:16].replace('T',' ')} UTC"
        )
        conn.close()

    except Exception as e:
        msg = f"❌ Status error: {e}"

    send_message(msg, chat_id=chat_id)


def _handle_trades(chat_id: str):
    """List all open trades."""
    try:
        from bot.trade_manager import get_trades_conn, get_open_trades

        conn        = get_trades_conn()
        open_trades = get_open_trades(conn)
        conn.close()

        if not open_trades:
            send_message("No open trades at the moment.", chat_id=chat_id)
            return

        lines = [f"📋 *Open Trades ({len(open_trades)})*\n"]

        for t in open_trades:
            symbol    = t["symbol"].replace("/USDT:USDT", "")
            direction = t["direction"].upper()
            entry     = t["avg_entry_price"]
            sl        = t["trailing_sl"]
            tp        = t["take_profit"]
            tier      = t["tier"].replace("tier", "T")
            candles   = t["candles_open"]
            emoji     = "🟢" if direction == "LONG" else "🔴"

            lines.append(
                f"{emoji} *{symbol}* {direction} | {tier}\n"
                f"   Entry: `{entry:.4f}` | SL: `{sl:.4f}` | TP: `{tp:.4f}`\n"
                f"   Candles open: {candles}"
            )

        send_message("\n".join(lines), chat_id=chat_id)

    except Exception as e:
        send_message(f"❌ Trades error: {e}", chat_id=chat_id)


def _handle_pnl(chat_id: str):
    """Show performance summary."""
    try:
        from bot.trade_manager import get_trades_conn, get_performance_stats

        conn  = get_trades_conn()
        stats = get_performance_stats(conn)
        conn.close()

        msg = (
            f"📈 *Performance Summary*\n"
            f"Capital:    `${stats['capital']:,.2f}`\n"
            f"Total PnL:  `${stats['total_pnl']:+,.2f}`\n"
            f"Trades:     `{stats['total_trades']}`\n"
            f"Win Rate:   `{stats['win_rate']:.1f}%`\n"
            f"Expectancy: `${stats['expectancy']:.2f}` per trade\n"
            f"Avg Win:    `${stats['avg_win']:.2f}`\n"
            f"Avg Loss:   `${stats['avg_loss']:.2f}`\n"
            f"Drawdown:   `{stats['drawdown']:.2f}%` from peak"
        )

    except Exception as e:
        msg = f"❌ PnL error: {e}"

    send_message(msg, chat_id=chat_id)


def _handle_pause(chat_id: str):
    """Pause the bot."""
    try:
        from bot.main import get_bot_state
        from bot.risk_manager import manual_pause

        state = get_bot_state()
        msg   = manual_pause(state.risk_state)
        send_message(msg, chat_id=chat_id)

    except Exception as e:
        send_message(f"❌ Pause error: {e}", chat_id=chat_id)


def _handle_resume(chat_id: str):
    """Resume the bot."""
    try:
        from bot.main import get_bot_state
        from bot.risk_manager import manual_resume
        from bot.trade_manager import get_trades_conn

        state       = get_bot_state()
        trades_conn = get_trades_conn()
        msg         = manual_resume(state.risk_state, trades_conn)
        trades_conn.close()
        send_message(msg, chat_id=chat_id)

    except Exception as e:
        send_message(f"❌ Resume error: {e}", chat_id=chat_id)


def _handle_stop(chat_id: str):
    """Emergency stop — close all trades."""
    try:
        from bot.main import get_bot_state
        from bot.risk_manager import manual_stop
        from bot.trade_manager import get_trades_conn, get_open_trades

        state       = get_bot_state()
        trades_conn = get_trades_conn()
        open_trades = get_open_trades(trades_conn)
        msg         = manual_stop(state.risk_state, trades_conn, open_trades)
        trades_conn.close()
        send_message(msg, chat_id=chat_id)

    except Exception as e:
        send_message(f"❌ Stop error: {e}", chat_id=chat_id)


def _handle_auto(chat_id: str):
    """Re-enable automatic circuit breakers."""
    try:
        from bot.main import get_bot_state
        from bot.risk_manager import enable_auto_circuit_breakers

        state = get_bot_state()
        msg   = enable_auto_circuit_breakers(state.risk_state)
        send_message(msg, chat_id=chat_id)

    except Exception as e:
        send_message(f"❌ Auto error: {e}", chat_id=chat_id)


def _handle_golive(chat_id: str):
    """Check go-live criteria."""
    try:
        from bot.trade_manager import get_trades_conn
        from bot.performance_monitor import check_go_live_criteria

        conn   = get_trades_conn()
        result = check_go_live_criteria(conn)
        conn.close()

        ready  = result["ready_for_live"]
        emoji  = "✅" if ready else "❌"

        lines = [f"{emoji} *Go-Live Criteria*\n"]

        for name, data in result["criteria"].items():
            passed = data.get("passed", False)
            icon   = "✅" if passed else "❌"
            actual = data.get("actual", data.get("current_dd", "—"))
            req    = data.get("required", "—")
            lines.append(f"{icon} {name}: `{actual}` (need `{req}`)")

        lines.append(
            f"\n{'🚀 Ready for live trading!' if ready else '⏳ Not ready yet — keep paper trading.'}"
        )

        send_message("\n".join(lines), chat_id=chat_id)

    except Exception as e:
        send_message(f"❌ Go-live check error: {e}", chat_id=chat_id)


def _handle_help(chat_id: str):
    """Show available commands."""
    msg = (
        "🤖 *Auto-Trading AI Agent Commands*\n\n"
        "/status  — Bot status and capital\n"
        "/trades  — List open trades\n"
        "/pnl     — Performance summary\n"
        "/pause   — Pause new entries\n"
        "/resume  — Resume from pause\n"
        "/stop    — Emergency stop (closes all trades)\n"
        "/auto    — Re-enable auto circuit breakers\n"
        "/golive  — Check go-live criteria\n"
        "/help    — Show this message"
    )
    send_message(msg, chat_id=chat_id)


# Command registry — add new commands here only
COMMANDS = {
    "/status":  _handle_status,
    "/trades":  _handle_trades,
    "/pnl":     _handle_pnl,
    "/pause":   _handle_pause,
    "/resume":  _handle_resume,
    "/stop":    _handle_stop,
    "/auto":    _handle_auto,
    "/golive":  _handle_golive,
    "/help":    _handle_help,
    "/start":   _handle_help,
}


# =============================================================================
# POLLING LOOP — Receives commands from Telegram
# =============================================================================

_last_update_id = 0


def _poll_commands():
    """
    Poll Telegram for new messages and handle commands.
    Runs in a background thread.
    """
    global _last_update_id

    while True:
        try:
            response = requests.get(
                _api_url("getUpdates"),
                params={
                    "offset":  _last_update_id + 1,
                    "timeout": 30,  # Long polling
                    "limit":   10,
                },
                timeout=35,
            )

            if not response.ok:
                time.sleep(5)
                continue

            updates = response.json().get("result", [])

            for update in updates:
                _last_update_id = update["update_id"]

                message = update.get("message", {})
                text    = message.get("text", "").strip()
                chat_id = str(message.get("chat", {}).get("id", ""))

                # Only respond to configured chat
                if chat_id != str(TELEGRAM["chat_id"]):
                    logger.debug(f"Ignoring message from unknown chat: {chat_id}")
                    continue

                # Find and execute command
                command = text.split()[0].lower() if text else ""
                handler = COMMANDS.get(command)

                if handler:
                    logger.info(f"Telegram command received: {command}")
                    threading.Thread(
                        target=handler,
                        args=(chat_id,),
                        daemon=True,
                    ).start()
                elif text.startswith("/"):
                    send_message(
                        f"Unknown command: `{command}`\nType /help for available commands.",
                        chat_id=chat_id,
                    )

        except requests.exceptions.Timeout:
            continue  # Normal — long polling timeout
        except Exception as e:
            logger.warning(f"Telegram polling error: {e}")
            time.sleep(10)


def start_command_listener():
    """
    Start the Telegram command listener in a background thread.
    Called from main.py startup sequence.
    Non-blocking — runs alongside the main bot loop.
    """
    if not TELEGRAM["enabled"] or not TELEGRAM["bot_token"]:
        logger.info("Telegram not configured — command listener not started")
        return

    thread = threading.Thread(
        target=_poll_commands,
        name="TelegramListener",
        daemon=True,
    )
    thread.start()
    logger.info("Telegram command listener started")
    return thread


# =============================================================================
# ENTRY POINT — Run directly to test Telegram connectivity
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    print("Testing Telegram connectivity...")

    ok = send_message(
        "✅ *APEX Telegram Test*\n"
        "Bot connectivity confirmed.\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

    if ok:
        print("✅ Telegram message sent successfully")
        print("\nStarting command listener — send /help to your bot...")
        start_command_listener()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        print("❌ Telegram send failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
