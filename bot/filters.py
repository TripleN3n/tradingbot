# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/filters.py — Entry Filters
# Version 3.3 — F&G hard block removed (lagging indicator, redundant with confluence)
# =============================================================================
# CHANGES FROM v3.2:
# - filter_fear_greed(): Function retained but removed from run_all_filters().
#   F&G is a lagging indicator — redundant with real-time technical confluence.
#   RSI, EMA, Volume already capture sentiment in real time.
#   Hard blocking longs during Extreme Fear caused missed recoveries.
# =============================================================================
# CHANGES FROM v3.1:
# - filter_session(): Now tier-aware per strategy spec.
#   Tier1/Tier2 allowed in Asian session at 50% position size (was fully blocked).
#   Tier3 still fully blocked in Asian session.
#   Timeframe parameter added for future-proofing and logging clarity.
# - run_all_filters(): Passes timeframe to filter_session().
# =============================================================================
# All filters are independent and pluggable.
# Add a new filter: add function + one line in run_all_filters(). Done.
# Disable a filter: set "enabled": False in config.py. Nothing else changes.
#
# FILTERS:
# 1. Candle close confirmation
# 2. Volume filter
# 3. Liquidity filter
# 4. Funding rate filter
# 5. Fear & Greed filter — RETAINED but NOT ACTIVE (removed from run_all_filters)
# 6. BTC trend filter — matches token's entry timeframe exactly
# 7. Correlation filter
# 8. Session filter — tier-aware Asian session handling
# 9. Cooldown filter
# =============================================================================

import pandas as pd
import numpy as np
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.config import FILTERS, TIERS, BTC_FILTER

logger = logging.getLogger(__name__)


# =============================================================================
# FILTER RESULT
# =============================================================================

class FilterResult:
    def __init__(self, passed: bool, reason: str = "", size_multiplier: float = 1.0):
        self.passed          = passed
        self.reason          = reason
        self.size_multiplier = size_multiplier

    def __repr__(self):
        return f"FilterResult({'✅' if self.passed else '❌'} {self.reason})"


# =============================================================================
# FILTER 1 — CANDLE CLOSE CONFIRMATION
# =============================================================================

def filter_candle_close(df: pd.DataFrame) -> FilterResult:
    if not FILTERS["candle_close"]["enabled"]:
        return FilterResult(True, "disabled")
    if df is None or df.empty:
        return FilterResult(False, "No candle data")
    last_ts  = df.index[-1]
    now_utc  = pd.Timestamp.now(tz="UTC")
    age_secs = (now_utc - last_ts).total_seconds()
    if age_secs < 60:
        return FilterResult(False, "Last candle still forming")
    return FilterResult(True, "Candle closed")


# =============================================================================
# FILTER 2 — VOLUME FILTER
# =============================================================================

def filter_volume(df: pd.DataFrame) -> FilterResult:
    if not FILTERS["volume"]["enabled"]:
        return FilterResult(True, "disabled")
    if df is None or df.empty:
        return FilterResult(False, "No data")
    vol_ratio = df.iloc[-1].get("volume_ratio", 0)
    if pd.isna(vol_ratio) or vol_ratio == 0:
        return FilterResult(False, "Volume ratio unavailable")
    min_mult = FILTERS["volume"]["min_multiplier"]
    if vol_ratio < min_mult:
        return FilterResult(False, f"Volume too low: {vol_ratio:.2f}x (need {min_mult}x)")
    return FilterResult(True, f"Volume OK: {vol_ratio:.2f}x")


# =============================================================================
# FILTER 3 — LIQUIDITY FILTER
# =============================================================================

def filter_liquidity(daily_volume_usd: float) -> FilterResult:
    if not FILTERS["liquidity"]["enabled"]:
        return FilterResult(True, "disabled")
    min_vol = FILTERS["liquidity"]["min_daily_volume_usd"]
    if daily_volume_usd < min_vol:
        return FilterResult(False, f"Volume too low: ${daily_volume_usd:,.0f}")
    return FilterResult(True, f"Liquidity OK: ${daily_volume_usd:,.0f}")


# =============================================================================
# FILTER 4 — FUNDING RATE FILTER
# =============================================================================

def filter_funding_rate(funding_rate: float, direction: str) -> FilterResult:
    if not FILTERS["funding_rate"]["enabled"]:
        return FilterResult(True, "disabled")
    max_long  = FILTERS["funding_rate"]["max_long"]
    min_short = FILTERS["funding_rate"]["min_short"]
    if direction == "long" and funding_rate > max_long:
        return FilterResult(False, f"Funding too high for long: {funding_rate:.4%}")
    if direction == "short" and funding_rate < min_short:
        return FilterResult(False, f"Funding too low for short: {funding_rate:.4%}")
    return FilterResult(True, f"Funding OK: {funding_rate:.4%}")


# =============================================================================
# FILTER 5 — FEAR & GREED FILTER
# RETAINED but NOT ACTIVE — removed from run_all_filters()
# Reason: F&G is lagging — price recovers before F&G reflects it.
# Real-time confluence (RSI, EMA, Volume) already captures sentiment.
# Re-enable by adding back to filter_checks in run_all_filters() if needed.
# =============================================================================

def filter_fear_greed(fear_greed_value: int, direction: str) -> FilterResult:
    if not FILTERS["fear_greed"]["enabled"]:
        return FilterResult(True, "disabled")
    extreme_fear  = FILTERS["fear_greed"]["extreme_fear_threshold"]
    extreme_greed = FILTERS["fear_greed"]["extreme_greed_threshold"]
    if fear_greed_value < extreme_fear and direction == "long":
        return FilterResult(False, f"Extreme Fear ({fear_greed_value}) — longs blocked")
    if fear_greed_value > extreme_greed and direction == "short":
        return FilterResult(False, f"Extreme Greed ({fear_greed_value}) — shorts blocked")
    return FilterResult(True, f"F&G OK: {fear_greed_value}")


# =============================================================================
# FILTER 6 — BTC TREND FILTER (v3.1 — timeframe-matched)
# =============================================================================

def filter_btc_trend(
    btc_trend: dict,
    direction: str,
    timeframe: str,
    tier: str = "tier3",
    confluence_count: int = 0,
) -> FilterResult:
    """
    BTC trend filter — checks BTC direction on the SAME timeframe as token entry.

    1H token → checks btc_trend["1h"]
    4H token → checks btc_trend["4h"]
    1D token → checks btc_trend["1d"]

    Rules:
    - BTC neutral on that timeframe → trade allowed
    - BTC aligned with trade direction → trade allowed
    - BTC opposed to trade direction:
        Tier1 / Tier2 → soft override: allowed if confluence_count >= 4
        Tier3         → soft override: allowed if confluence_count >= 5
    """
    if not BTC_FILTER.get("enabled", True):
        return FilterResult(True, "BTC filter disabled")

    btc_direction = btc_trend.get(timeframe, "neutral")

    if btc_direction == "neutral":
        return FilterResult(True, f"BTC {timeframe} neutral — trade allowed")

    aligned = (
        (direction == "long"  and btc_direction == "bullish") or
        (direction == "short" and btc_direction == "bearish")
    )

    if aligned:
        return FilterResult(True, f"BTC {timeframe} aligned: {btc_direction}")

    # BTC conflict — check soft override by tier
    if tier in ("tier1", "tier2") and confluence_count >= 4:
        return FilterResult(
            True,
            f"BTC {timeframe} conflict overridden: {tier} with {confluence_count}/5 confluence"
        )

    if tier == "tier3" and confluence_count >= 5:
        return FilterResult(
            True,
            f"BTC {timeframe} conflict overridden: tier3 with {confluence_count}/5 confluence"
        )

    return FilterResult(
        False,
        f"BTC {timeframe} conflict: BTC={btc_direction}, signal={direction} (confluence {confluence_count} insufficient for override)"
    )


# =============================================================================
# FILTER 7 — CORRELATION FILTER
# =============================================================================

def filter_correlation(
    symbol: str,
    open_trades: list,
    price_history: dict,
) -> FilterResult:
    if not FILTERS["correlation"]["enabled"]:
        return FilterResult(True, "disabled")
    if not open_trades:
        return FilterResult(True, "No open trades")

    threshold = FILTERS["correlation"]["correlation_threshold"]
    max_corr  = FILTERS["correlation"]["max_correlated_trades"]

    new_prices = price_history.get(symbol, [])
    if len(new_prices) < 30:
        return FilterResult(True, "Insufficient price history")

    new_returns      = pd.Series(new_prices).pct_change().dropna()
    correlated_count = 0

    for trade in open_trades:
        trade_symbol = trade.get("symbol")
        if trade_symbol == symbol:
            continue
        trade_prices = price_history.get(trade_symbol, [])
        if len(trade_prices) < 30:
            continue
        trade_returns = pd.Series(trade_prices).pct_change().dropna()
        min_len = min(len(new_returns), len(trade_returns))
        if min_len < 20:
            continue
        corr = new_returns.iloc[-min_len:].corr(trade_returns.iloc[-min_len:])
        if not np.isnan(corr) and abs(corr) > threshold:
            correlated_count += 1

    if correlated_count >= max_corr:
        return FilterResult(False, f"Too many correlated trades: {correlated_count}")
    return FilterResult(True, f"Correlation OK: {correlated_count} correlated")


# =============================================================================
# FILTER 8 — SESSION FILTER (v3.2 — tier-aware)
# =============================================================================

def filter_session(tier: str, timeframe: str = "1h") -> FilterResult:
    """
    Session-based position sizing filter.

    FIX from v3.1: Now tier-aware per strategy spec.
    - US session (13:00–21:00 UTC):   All tiers, full size.
    - Euro session (07:00–13:00 UTC): All tiers, full size.
    - Asian session (all other hours): Tier-dependent:
        - Tier1 / Tier2: 50% position size (reduced, but allowed)
        - Tier3:         Skipped entirely

    Previously, Asian session blocked ALL tiers with size_multiplier=0.0.
    This inadvertently deadlocked 1D-timeframe tokens whose signals
    only fired at midnight UTC (hour 0 = Asian session).
    """
    if not FILTERS["session"]["enabled"]:
        return FilterResult(True, "disabled", size_multiplier=1.0)

    hour         = datetime.now(timezone.utc).hour
    us_session   = FILTERS["session"]["us_session"]
    euro_session = FILTERS["session"]["euro_session"]
    asian_cfg    = FILTERS["session"]["asian_session"]

    # US session: full size, all tiers
    if us_session["start"] <= hour < us_session["end"]:
        return FilterResult(True, f"US session (hour {hour}:xx UTC)", size_multiplier=1.0)

    # Euro session: full size, all tiers
    if euro_session["start"] <= hour < euro_session["end"]:
        return FilterResult(True, f"Euro session (hour {hour}:xx UTC)", size_multiplier=1.0)

    # Asian / off-hours session (hours 0–6 and 21–23 UTC)
    # Resolve the size multiplier for this tier
    if tier == "tier1":
        size = asian_cfg.get("tier1_size", 0.5)
    elif tier == "tier2":
        size = asian_cfg.get("tier2_size", 0.5)
    else:
        size = asian_cfg.get("tier3_size", 0.0)

    if size > 0:
        return FilterResult(
            True,
            f"Asian session — {tier} allowed at {int(size * 100)}% size (hour {hour}:xx UTC)",
            size_multiplier=size,
        )

    return FilterResult(
        False,
        f"Asian session — {tier} skipped (hour {hour}:xx UTC)",
        size_multiplier=0.0,
    )


# =============================================================================
# MASTER FILTER — Run all filters in sequence
# =============================================================================

def run_all_filters(
    symbol: str,
    direction: str,
    tier: str,
    df: pd.DataFrame,
    btc_trend: dict,
    funding_rate: float,
    fear_greed_value: int,
    daily_volume_usd: float,
    confluence_count: int,
    open_trades: list,
    price_history: dict,
    cooldown_tracker: dict,
    timeframe: str,
) -> dict:
    """
    Run all active entry filters in sequence.
    Returns dict with passed, size_multiplier, failures, details.

    BTC filter uses the token's entry timeframe to check the matching BTC direction.
    Session filter is now tier-aware — Asian session allowed for tier1/2 at 50% size.
    F&G filter removed from active checks (v3.3) — lagging, redundant with confluence.

    To add a new filter:
    1. Create a filter function above
    2. Add one line in filter_checks below
    Nothing else changes.
    """
    results         = {}
    size_multiplier = 1.0
    failures        = []

    filter_checks = [
        ("candle_close",  lambda: filter_candle_close(df)),
        ("volume",        lambda: filter_volume(df)),
        ("liquidity",     lambda: filter_liquidity(daily_volume_usd)),
        ("funding_rate",  lambda: filter_funding_rate(funding_rate, direction)),
        ("btc_trend",     lambda: filter_btc_trend(btc_trend, direction, timeframe, tier, confluence_count)),
        ("correlation",   lambda: filter_correlation(symbol, open_trades, price_history)),
        ("session",       lambda: filter_session(tier, timeframe)),
        ("cooldown",      lambda: filter_cooldown(symbol, cooldown_tracker)),
    ]

    for filter_name, filter_fn in filter_checks:
        try:
            result = filter_fn()
            results[filter_name] = result

            if not result.passed:
                failures.append(filter_name)
                if len(failures) == 1:
                    try:
                        from bot.config import apex_logger
                        apex_logger.filter_rejection(
                            token        = symbol,
                            filter_name  = filter_name,
                            side         = direction,
                            value        = result.reason,
                            threshold    = None,
                            full_context = {
                                "tier":             tier,
                                "timeframe":        timeframe,
                                "btc_trend":        btc_trend.get("direction", "?") if isinstance(btc_trend, dict) else str(btc_trend),
                                "btc_tf_direction": btc_trend.get(timeframe, "?") if isinstance(btc_trend, dict) else "?",
                                "fg_index":         fear_greed_value,
                                "funding_rate":     funding_rate,
                                "confluence_count": confluence_count,
                                "daily_volume_usd": daily_volume_usd,
                            },
                        )
                    except Exception:
                        pass

            if filter_name == "session" and result.passed:
                size_multiplier *= result.size_multiplier

        except Exception as e:
            logger.error(f"Filter {filter_name} error for {symbol}: {e}")
            results[filter_name] = FilterResult(False, f"Exception: {e}")
            failures.append(filter_name)

    passed = len(failures) == 0

    if not passed:
        logger.debug(f"Filters FAILED {symbol} {direction} [{timeframe}]: {failures}")
    else:
        logger.debug(
            f"All filters PASSED {symbol} {direction} [{timeframe}] "
            f"(size: {size_multiplier:.2f}x)"
        )

    return {
        "passed":          passed,
        "size_multiplier": size_multiplier,
        "failures":        failures,
        "details":         results,
    }


# =============================================================================
# COOLDOWN MANAGEMENT
# =============================================================================

def filter_cooldown(symbol: str, cooldown_tracker: dict) -> FilterResult:
    if not FILTERS["cooldown"]["enabled"]:
        return FilterResult(True, "disabled")
    remaining = cooldown_tracker.get(symbol, 0)
    if remaining > 0:
        return FilterResult(False, f"Cooldown: {remaining} candles after SL")
    return FilterResult(True, "No cooldown")


def decrement_cooldowns(cooldown_tracker: dict) -> dict:
    """Decrement all cooldown counters by 1. Remove expired ones."""
    return {s: r - 1 for s, r in cooldown_tracker.items() if r > 1}


def set_cooldown(cooldown_tracker: dict, symbol: str, timeframe: str) -> dict:
    """Set cooldown after SL hit. Duration is timeframe-aware."""
    candles_cfg = FILTERS["cooldown"]["candles_after_sl"]
    if isinstance(candles_cfg, dict):
        tf      = str(timeframe).lower() if timeframe else "1h"
        candles = candles_cfg.get(tf, candles_cfg.get("1h", 4))
    else:
        candles = candles_cfg
    cooldown_tracker[symbol] = candles
    logger.debug(f"Cooldown set: {symbol} — {candles} {timeframe} candles")
    return cooldown_tracker

# __APEX_LOGGER_V1__
