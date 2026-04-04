"""
APEX Unified Logging System
============================
Structured JSONL logging for AI self-learning, retrospective analysis,
fine-tuning/RAG, and live agent feedback loops.

Design principles:
  - FAIL-SAFE: logging errors NEVER crash or slow the bot
  - NON-BLOCKING: all writes happen on daemon threads
  - DUAL-WRITE: every event goes to category file + unified event stream
  - REASONING-FIRST: every log captures WHY, not just WHAT
  - LLM-READY: consistent schema, ISO timestamps, no abbreviations

File layout (all in logs/ directory):
  events/          Unified chronological stream
  signals/         Signal scan decisions (including skips and no-signals)
  trades/          Trade lifecycle (entry legs, SL moves, exits)
  filters/         Filter rejections with full market context
  apex/            Backtest results, strategy assignments, rebalance events
  universe/        Token universe additions and removals
  risk/            Drawdown alerts, circuit breaker triggers
  performance/     Rolling monitor pauses and resumes
  system/          Bot start/stop, cycle heartbeats, unhandled errors
  snapshots/       Full config snapshot JSON on every bot start
"""

import json
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

CATEGORIES = [
    "events",
    "signals",
    "trades",
    "filters",
    "apex",
    "universe",
    "risk",
    "performance",
    "system",
    "snapshots",
]

# Exit reasons — used consistently across logs for LLM pattern matching
EXIT_TP           = "tp"
EXIT_PARTIAL_TP   = "partial_tp"
EXIT_SL           = "sl"
EXIT_TIME_STOP    = "time_stop"
EXIT_CIRCUIT_BREAK= "circuit_breaker"
EXIT_MANUAL       = "manual"

# Signal results
SIG_ENTERED       = "entered"
SIG_QUEUED        = "queued"
SIG_SKIPPED       = "skipped"
SIG_NO_SIGNAL     = "no_signal"

# Filter names — use these strings in filter_rejection() calls
FILTER_VOLUME         = "entry_volume"
FILTER_FUNDING        = "funding_rate"
FILTER_CORRELATION    = "correlation"
FILTER_SESSION        = "session"
FILTER_COOLDOWN       = "cooldown"
FILTER_BTC_TREND      = "btc_trend"
FILTER_FG_INDEX       = "fear_greed"
FILTER_MIN_RRR        = "min_rrr"
FILTER_NO_SLOT        = "no_slot_available"
FILTER_PERF_PAUSED    = "performance_monitor_paused"


# ─────────────────────────────────────────────
# Logger
# ─────────────────────────────────────────────

class APEXLogger:
    """
    Thread-safe, fail-safe structured logger.

    Usage:
        from bot.logger import APEXLogger
        logger = APEXLogger("/home/opc/tradingbot/logs")

        # Then call specific methods at each decision point
        logger.signal_scan(...)
        logger.trade_entry(...)
        logger.filter_rejection(...)
    """

    def __init__(self, base_log_dir: str):
        self.base_dir = Path(base_log_dir)
        self._lock = threading.Lock()
        self._ensure_dirs()

    # ── Internal helpers ──────────────────────

    def _ensure_dirs(self):
        for cat in CATEGORIES:
            (self.base_dir / cat).mkdir(parents=True, exist_ok=True)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _build_entry(self, event_type: str, category: str, data: Dict[str, Any]) -> Dict:
        return {
            "ts":         self._now_iso(),
            "event_type": event_type,
            "category":   category,
            **data,
        }

    def _write(self, category: str, entry: Dict):
        """Actual disk write — called on a daemon thread."""
        try:
            line = json.dumps(entry, default=str) + "\n"
            date = self._today()

            cat_file   = self.base_dir / category / f"{category}_{date}.jsonl"
            event_file = self.base_dir / "events"  / f"events_{date}.jsonl"

            with self._lock:
                with open(cat_file, "a", encoding="utf-8") as f:
                    f.write(line)
                # snapshots don't go into unified stream (too large)
                if category != "snapshots":
                    with open(event_file, "a", encoding="utf-8") as f:
                        f.write(line)
        except Exception as e:
            # Never propagate — just print so it appears in system logs
            print(f"[APEX LOGGER ERROR] Failed to write {category}/{entry.get('event_type')}: {e}")

    def log(self, event_type: str, category: str, data: Dict[str, Any]):
        """Fire-and-forget structured log. Non-blocking."""
        entry = self._build_entry(event_type, category, data)
        t = threading.Thread(target=self._write, args=(category, entry), daemon=True)
        t.start()


    # ═══════════════════════════════════════════
    # SYSTEM EVENTS
    # ═══════════════════════════════════════════

    def bot_start(self, version: str, mode: str, initial_capital: float,
                  config_snapshot: Dict):
        """
        Call once when bot process starts.
        Saves full config to snapshots/ so every decision can be replayed
        against the exact parameters in force at that time.
        """
        self.log("bot_start", "system", {
            "version":         version,
            "mode":            mode,          # PAPER or LIVE
            "initial_capital": initial_capital,
            "config_summary": {
                "paper_trading":      config_snapshot.get("PAPER_TRADING"),
                "leverage_max":       config_snapshot.get("LEVERAGE_MAX"),
                "slot_pct":           config_snapshot.get("SLOT_PCT"),
                "min_win_rate":       config_snapshot.get("MIN_WIN_RATE"),
                "drawdown_alert":     config_snapshot.get("DRAWDOWN_ALERT_PCT"),
                "drawdown_pause":     config_snapshot.get("DRAWDOWN_PAUSE_PCT"),
                "drawdown_stop":      config_snapshot.get("DRAWDOWN_STOP_PCT"),
            },
        })
        # Full config snapshot as separate JSON file for exact replay
        try:
            ts        = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
            snap_file = self.base_dir / "snapshots" / f"config_{ts}.json"
            payload   = {
                "ts":              self._now_iso(),
                "event_type":      "config_snapshot",
                "bot_version":     version,
                "mode":            mode,
                "initial_capital": initial_capital,
                "config":          config_snapshot,
            }
            with open(snap_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)
        except Exception as e:
            print(f"[APEX LOGGER ERROR] Config snapshot failed: {e}")

    def bot_stop(self, reason: str, uptime_seconds: float,
                 total_cycles: int, closed_trades: int):
        self.log("bot_stop", "system", {
            "reason":          reason,
            "uptime_seconds":  uptime_seconds,
            "total_cycles":    total_cycles,
            "closed_trades":   closed_trades,
        })

    def bot_error(self, error_msg: str, context: Optional[Dict] = None):
        """Call in except blocks. Captures full traceback automatically."""
        self.log("bot_error", "system", {
            "error":     error_msg,
            "traceback": traceback.format_exc(),
            "context":   context or {},
        })

    def cycle_start(self, cycle_number: int, open_trades: int,
                    capital_deployed_pct: float, fg_index: int,
                    fg_label: str, btc_trend: str, btc_price: float,
                    total_equity: float):
        """
        Log at the beginning of every hourly bot cycle.
        Creates a heartbeat for the unified timeline.
        """
        self.log("cycle_start", "system", {
            "cycle_number":          cycle_number,
            "open_trades":           open_trades,
            "capital_deployed_pct":  capital_deployed_pct,
            "total_equity":          total_equity,
            "market_context": {
                "fg_index":  fg_index,
                "fg_label":  fg_label,      # Extreme Fear | Fear | Neutral | Greed | Extreme Greed
                "btc_trend": btc_trend,     # bullish | bearish | neutral
                "btc_price": btc_price,
            },
        })


    # ═══════════════════════════════════════════
    # SIGNAL EVENTS
    # ═══════════════════════════════════════════

    def signal_scan_complete(self, token: str, timeframe: str,
                              strategy: str, tier: int,
                              result: str, reason: str,
                              direction: Optional[str],
                              indicators: Dict, filters_result: Dict,
                              market: Dict):
        """
        Log outcome of every signal scan for every token.
        This is the richest log — captures EVERYTHING the bot saw.

        Parameters
        ----------
        token       : e.g. "AVAX"
        timeframe   : "1H" | "4H" | "1D"
        strategy    : e.g. "Volatility Surge"
        tier        : 1 | 2 | 3
        result      : SIG_ENTERED | SIG_QUEUED | SIG_SKIPPED | SIG_NO_SIGNAL
        reason      : human-readable reason string
        direction   : "long" | "short" | None (if no signal)
        indicators  : {
            "ema_signal":    "bullish" | "bearish" | "neutral",
            "vwap_signal":   "bullish" | "bearish" | "neutral",   (1H only)
            "ema200_signal": "bullish" | "bearish" | "neutral",   (4H/1D)
            "rsi":           float,
            "rsi_signal":    "bullish" | "bearish" | "neutral",
            "macd_signal":   "bullish" | "bearish" | "neutral",
            "volume_ratio":  float,     # ratio vs 20-period average
            "volume_signal": "bullish" | "bearish" | "neutral",
            "bb_signal":     "bullish" | "bearish" | "neutral",
            "indicators_agreeing": int,
        }
        filters_result : {
            "btc_trend":         "bullish" | "bearish" | "neutral",
            "btc_filter":        "pass" | "fail" | "soft_override",
            "fg_index":          int,
            "fg_filter":         "pass" | "fail",
            "funding_rate":      float,
            "funding_filter":    "pass" | "fail",
            "session":           "US" | "EU" | "Asian",
            "session_filter":    "pass" | "half_size" | "skip",
            "correlation_filter":"pass" | "fail",
            "cooldown_filter":   "pass" | "fail",
            "volume_filter":     "pass" | "fail",
            "min_rrr_filter":    "pass" | "fail",
        }
        market : {
            "price":         float,
            "volume_24h_usd":float,
            "atr":           float,
            "potential_sl":  float,
            "potential_tp":  float,
            "potential_rrr": float,
        }
        """
        self.log("signal_scan", "signals", {
            "token":     token,
            "timeframe": timeframe,
            "strategy":  strategy,
            "tier":      tier,
            "result":    result,
            "reason":    reason,
            "direction": direction,
            "indicators":      indicators,
            "filters_result":  filters_result,
            "market":          market,
        })

    def signal_queued(self, token: str, tier: int, score: float,
                      queue_position: int, queue_reason: str):
        """Token signal is valid but no slot is available — entered queue."""
        self.log("signal_queued", "signals", {
            "token":          token,
            "tier":           tier,
            "score":          score,
            "queue_position": queue_position,
            "queue_reason":   queue_reason,
        })

    def signal_queue_promoted(self, token: str, tier: int, wait_cycles: int):
        """Signal moved from queue into an open slot."""
        self.log("signal_queue_promoted", "signals", {
            "token":       token,
            "tier":        tier,
            "wait_cycles": wait_cycles,
        })

    def signal_queue_dropped(self, token: str, reason: str):
        """
        Signal dropped from queue because conditions are no longer valid.
        Reason examples: 'conditions_stale' | 'token_removed_from_universe'
                         | 'correlation_now_blocked' | 'fg_index_changed'
        """
        self.log("signal_queue_dropped", "signals", {
            "token":  token,
            "reason": reason,
        })


    # ═══════════════════════════════════════════
    # FILTER REJECTION EVENTS
    # ═══════════════════════════════════════════

    def filter_rejection(self, token: str, filter_name: str,
                          side: str, value: Any, threshold: Any,
                          full_context: Dict):
        """
        Call whenever a filter blocks a trade entry.
        This is the most valuable data for LLM tuning — reveals which
        filters are most restrictive and whether they're saving or costing profit.

        Parameters
        ----------
        filter_name : use FILTER_* constants defined at top of this file
        side        : "long" | "short"
        value       : the actual value that triggered rejection
        threshold   : the limit that was breached
        full_context: snapshot of all indicators + market state at rejection time
        """
        self.log("filter_rejection", "filters", {
            "token":       token,
            "filter_name": filter_name,
            "side":        side,
            "value":       value,
            "threshold":   threshold,
            "full_context": full_context,
        })


    # ═══════════════════════════════════════════
    # TRADE EVENTS
    # ═══════════════════════════════════════════

    def trade_entry_leg(self, trade_id: str, token: str, side: str,
                         leg: int, entry_price: float, size_usd: float,
                         sl_price: float, tp_price: float, rrr: float,
                         sl_method: str, strategy: str, tier: int,
                         timeframe: str, decision_context: Dict):
        """
        Log each entry leg separately (leg 1 = 60%, leg 2 = 40%).

        Parameters
        ----------
        trade_id       : "{TOKEN}_{YYYYMMDD}_{HHMM}" e.g. "AVAX_20260401_1800"
        sl_method      : "atr" | "structure" | "atr_and_structure"
        decision_context: the full indicators + filters snapshot from signal scan
        """
        self.log(f"trade_entry_leg{leg}", "trades", {
            "trade_id":    trade_id,
            "token":       token,
            "side":        side,
            "leg":         leg,
            "entry_price": entry_price,
            "size_usd":    size_usd,
            "sl_price":    sl_price,
            "tp_price":    tp_price,
            "rrr":         rrr,
            "sl_method":   sl_method,
            "strategy":    strategy,
            "tier":        tier,
            "timeframe":   timeframe,
            "decision_context": decision_context,
        })

    def trade_sl_moved(self, trade_id: str, token: str,
                        old_sl: float, new_sl: float,
                        reason: str, price_at_move: float,
                        profit_locked_usd: float):
        """
        Log every SL movement (breakeven move, trail, etc.).
        reason: "breakeven" | "trail_0.5x" | "manual"
        """
        self.log("trade_sl_moved", "trades", {
            "trade_id":          trade_id,
            "token":             token,
            "old_sl":            old_sl,
            "new_sl":            new_sl,
            "reason":            reason,
            "price_at_move":     price_at_move,
            "profit_locked_usd": profit_locked_usd,
        })

    def trade_exit(self, trade_id: str, token: str, side: str,
                    exit_price: float, entry_price: float,
                    exit_reason: str, pnl_usd: float, pnl_pct: float,
                    duration_candles: int, strategy: str, tier: int,
                    timeframe: str, partial: bool = False,
                    partial_pct: float = 100.0,
                    market_at_exit: Optional[Dict] = None):
        """
        Log every trade exit — full close, partial TP, SL, time stop.

        exit_reason : use EXIT_* constants defined at top of this file
        partial_pct : percentage of position closed (70 for first TP, 100 for full)
        market_at_exit : {fg_index, btc_trend, btc_price} — important for LLM
                         to learn whether exit conditions were macro-driven
        """
        self.log("trade_exit", "trades", {
            "trade_id":        trade_id,
            "token":           token,
            "side":            side,
            "exit_price":      exit_price,
            "entry_price":     entry_price,
            "exit_reason":     exit_reason,
            "pnl_usd":         pnl_usd,
            "pnl_pct":         pnl_pct,
            "duration_candles":duration_candles,
            "strategy":        strategy,
            "tier":            tier,
            "timeframe":       timeframe,
            "partial":         partial,
            "partial_pct":     partial_pct,
            "market_at_exit":  market_at_exit or {},
        })

    def trade_time_stop(self, trade_id: str, token: str,
                         candles_elapsed: int, candles_limit: int,
                         pnl_usd: float, pnl_pct: float):
        """Separate event for time stops — useful to count how often time stops hit."""
        self.log("trade_time_stop", "trades", {
            "trade_id":       trade_id,
            "token":          token,
            "candles_elapsed":candles_elapsed,
            "candles_limit":  candles_limit,
            "pnl_usd":        pnl_usd,
            "pnl_pct":        pnl_pct,
        })


    # ═══════════════════════════════════════════
    # RISK EVENTS
    # ═══════════════════════════════════════════

    def drawdown_event(self, level: str, drawdown_pct: float,
                        action: str, peak_capital: float,
                        current_capital: float, open_trades: int):
        """
        level  : "alert_20" | "pause_35" | "stop_50"
        action : "telegram_alert" | "paused_new_entries" | "stopped_all"
        """
        self.log("drawdown_event", "risk", {
            "level":           level,
            "drawdown_pct":    drawdown_pct,
            "action":          action,
            "peak_capital":    peak_capital,
            "current_capital": current_capital,
            "open_trades":     open_trades,
        })

    def drawdown_recovery(self, recovered_above_level: str,
                           current_capital: float, peak_capital: float):
        """Called when capital recovers above a circuit breaker threshold."""
        self.log("drawdown_recovery", "risk", {
            "recovered_above_level": recovered_above_level,
            "current_capital":       current_capital,
            "peak_capital":          peak_capital,
        })

    def circuit_breaker_triggered(self, level: str, reason: str,
                                   trades_closed: int,
                                   capital_at_trigger: float):
        self.log("circuit_breaker_triggered", "risk", {
            "level":              level,
            "reason":             reason,
            "trades_closed":      trades_closed,
            "capital_at_trigger": capital_at_trigger,
        })


    # ═══════════════════════════════════════════
    # PERFORMANCE MONITOR EVENTS
    # ═══════════════════════════════════════════

    def performance_pause(self, token: str, reason: str,
                           last_20_win_rate: float,
                           last_20_expectancy: float,
                           last_20_trades_summary: list):
        """
        reason: "negative_expectancy" | "win_rate_below_35pct"
        last_20_trades_summary: list of {trade_id, pnl_pct, exit_reason}
        """
        self.log("performance_pause", "performance", {
            "token":                token,
            "reason":               reason,
            "last_20_win_rate":     last_20_win_rate,
            "last_20_expectancy":   last_20_expectancy,
            "last_20_trades_summary": last_20_trades_summary,
        })

    def performance_resume(self, token: str, reason: str,
                            new_strategy: Optional[str] = None):
        """reason: "monthly_rebalance" | "manual_override" """
        self.log("performance_resume", "performance", {
            "token":        token,
            "reason":       reason,
            "new_strategy": new_strategy,
        })


    # ═══════════════════════════════════════════
    # APEX / STRATEGY EVENTS
    # ═══════════════════════════════════════════

    def backtest_result(self, token: str, timeframe: str,
                         strategy_name: str, win_rate: float,
                         expectancy: float, profit_factor: float,
                         sharpe: float, max_drawdown: float,
                         total_trades: int, passed_filter: bool,
                         fail_reason: Optional[str] = None):
        """
        Log every backtest permutation result — pass or fail.
        The failed results are as valuable as passes for LLM analysis
        (reveal which strategies don't suit which tokens).
        """
        self.log("backtest_result", "apex", {
            "token":     token,
            "timeframe": timeframe,
            "strategy":  strategy_name,
            "metrics": {
                "win_rate":       win_rate,
                "expectancy":     expectancy,
                "profit_factor":  profit_factor,
                "sharpe":         sharpe,
                "max_drawdown":   max_drawdown,
                "total_trades":   total_trades,
            },
            "passed_filter": passed_filter,
            "fail_reason":   fail_reason,   # e.g. "win_rate_below_75pct"
        })

    def strategy_assigned(self, token: str, strategy: str,
                           timeframe: str, tier: int,
                           metrics: Dict,
                           previous_strategy: Optional[str] = None,
                           previous_timeframe: Optional[str] = None,
                           assignment_reason: str = "monthly_rebalance"):
        """
        assignment_reason: "initial" | "monthly_rebalance" | "weekly_new_token"
        """
        self.log("strategy_assigned", "apex", {
            "token":               token,
            "strategy":            strategy,
            "timeframe":           timeframe,
            "tier":                tier,
            "metrics":             metrics,
            "previous_strategy":   previous_strategy,
            "previous_timeframe":  previous_timeframe,
            "assignment_reason":   assignment_reason,
        })

    def rebalance_event(self, event: str, rebalance_type: str,
                         token_count: int = 0,
                         duration_seconds: float = 0.0,
                         tokens_changed: int = 0,
                         tokens_unchanged: int = 0,
                         tokens_paused: int = 0):
        """
        event: "start" | "complete" | "batch_start" | "batch_complete"
        rebalance_type: "weekly" | "monthly"
        """
        self.log(f"rebalance_{event}", "apex", {
            "rebalance_type":   rebalance_type,
            "token_count":      token_count,
            "duration_seconds": duration_seconds,
            "tokens_changed":   tokens_changed,
            "tokens_unchanged": tokens_unchanged,
            "tokens_paused":    tokens_paused,
        })


    # ═══════════════════════════════════════════
    # UNIVERSE EVENTS
    # ═══════════════════════════════════════════

    def universe_token_added(self, token: str, market_cap_rank: int,
                              daily_volume_usd: float,
                              backtest_triggered: bool = True):
        self.log("universe_token_added", "universe", {
            "token":              token,
            "market_cap_rank":    market_cap_rank,
            "daily_volume_usd":   daily_volume_usd,
            "backtest_triggered": backtest_triggered,
        })

    def universe_token_removed(self, token: str, reason: str,
                                market_cap_rank: Optional[int] = None,
                                daily_volume_usd: Optional[float] = None,
                                open_trades_running: int = 0):
        """
        reason: "dropped_out_of_top100" | "below_min_volume" | "manual_exclusion"
        open_trades_running: how many open trades are still running to natural conclusion
        """
        self.log("universe_token_removed", "universe", {
            "token":               token,
            "reason":              reason,
            "market_cap_rank":     market_cap_rank,
            "daily_volume_usd":    daily_volume_usd,
            "open_trades_running": open_trades_running,
        })

    def universe_refresh_summary(self, tokens_added: list, tokens_removed: list,
                                  total_universe_size: int,
                                  refresh_type: str):
        """Summary log after each universe refresh."""
        self.log("universe_refresh_summary", "universe", {
            "refresh_type":        refresh_type,   # "weekly" | "monthly"
            "tokens_added":        tokens_added,
            "tokens_removed":      tokens_removed,
            "total_universe_size": total_universe_size,
        })
