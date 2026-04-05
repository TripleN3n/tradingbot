# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/signal_engine.py — Live Signal Generation Engine
# Version 3.2 — Timeframe gating fix, SL floor added
# =============================================================================
# CHANGES FROM v3.1:
#
# should_generate_signal():
#   Previously gated 1D signals to UTC hour 0 only. This caused a complete
#   deadlock: hour 0 is Asian session, and the session filter blocked Asian
#   session. Result: 1D tokens could NEVER open a trade.
#   FIX: Returns True for all timeframes every cycle. The OHLCV data already
#   contains only closed candles, so signals are always based on the correct
#   most-recently-closed candle regardless of when the cycle runs.
#   Duplicate entry prevention is handled by the open_symbols check.
#
# calculate_sl_tp_live():
#   Previously had a 3% hard CAP on SL distance but no minimum FLOOR.
#   When ATR was small, SLs as tight as 0.52% were produced — well within
#   normal 1H candle noise, causing guaranteed stop-outs on minor wicks.
#   FIX: Added 1.5% minimum SL floor. Combined with 3% cap, SL distance is
#   now bounded between 1.5% and 3% of entry price.
#   Configuration: SL["min_pct"] = 0.015, SL["max_pct"] = 0.03 in config.py.
# =============================================================================

import pandas as pd
import numpy as np
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.config import (
    TIERS, FILTERS, BTC_FILTER, INITIAL_CAPITAL,
    LOGS, DB, MTF_CONFIG, RSI, MACD, BOLLINGER, VOLUME, EMA, SL,
    VWAP_TIMEFRAMES, TRAILING_SL, TP,
)
from bot.filters import run_all_filters

logger = logging.getLogger(__name__)


# =============================================================================
# INDICATOR SIGNAL FUNCTIONS
# Must stay identical to backtest_engine.py
# =============================================================================

def check_ema_signal(df: pd.DataFrame) -> Optional[str]:
    if df is None or len(df) < 2: return None
    latest = df.iloc[-1]
    if latest["ema_fast"] > latest["ema_slow"]: return "long"
    if latest["ema_fast"] < latest["ema_slow"]: return "short"
    return None


def check_macro_ref_signal(df: pd.DataFrame) -> Optional[str]:
    if df is None or df.empty: return None
    latest = df.iloc[-1]
    macro  = latest.get("macro_ref")
    if pd.isna(macro): return None
    if latest["close"] > macro: return "long"
    if latest["close"] < macro: return "short"
    return None


def check_rsi_signal(df: pd.DataFrame) -> Optional[str]:
    if df is None or len(df) < 2: return None
    rsi      = df.iloc[-1]["rsi"]
    rsi_prev = df.iloc[-2]["rsi"]
    if pd.isna(rsi): return None
    if rsi < RSI["oversold"]:   return "long"
    if rsi > RSI["overbought"]: return "short"
    if rsi > rsi_prev and rsi < 60: return "long"
    if rsi < rsi_prev and rsi > 40: return "short"
    return None


def check_macd_signal(df: pd.DataFrame) -> Optional[str]:
    if df is None or len(df) < 2: return None
    macd_now  = df.iloc[-1]["macd"]
    sig_now   = df.iloc[-1]["macd_signal"]
    hist_now  = df.iloc[-1]["macd_hist"]
    hist_prev = df.iloc[-2]["macd_hist"]
    if pd.isna(macd_now): return None
    if macd_now > sig_now and hist_now > hist_prev: return "long"
    if macd_now < sig_now and hist_now < hist_prev: return "short"
    return None


def check_bollinger_signal(df: pd.DataFrame) -> Optional[str]:
    if df is None or len(df) < 2: return None
    latest = df.iloc[-1]
    prev   = df.iloc[-2]
    if pd.isna(latest["bb_upper"]): return None
    if not pd.isna(prev["bb_width"]) and latest["bb_width"] > prev["bb_width"]:
        if latest["close"] > latest["bb_mid"]: return "long"
        if latest["close"] < latest["bb_mid"]: return "short"
    if latest["close"] <= latest["bb_lower"]: return "long"
    if latest["close"] >= latest["bb_upper"]: return "short"
    return None


def check_volume_signal(df: pd.DataFrame) -> Optional[str]:
    if df is None or len(df) < 2: return None
    vol_ratio = df.iloc[-1]["volume_ratio"]
    if pd.isna(vol_ratio): return None
    if vol_ratio >= VOLUME["min_multiplier"]:
        return "long" if df.iloc[-1]["close"] > df.iloc[-2]["close"] else "short"
    return None


SIGNAL_FUNCTIONS = {
    "ema":       check_ema_signal,
    "macro_ref": check_macro_ref_signal,
    "rsi":       check_rsi_signal,
    "macd":      check_macd_signal,
    "bollinger": check_bollinger_signal,
    "volume":    check_volume_signal,
}


# =============================================================================
# MULTI-TIMEFRAME DIRECTION
# =============================================================================

def get_timeframe_direction(df: pd.DataFrame) -> Optional[str]:
    """
    Get trend direction from the latest candle of a DataFrame.
    EMA fast vs slow AND price vs macro_ref must both agree.
    """
    if df is None or df.empty or len(df) < 2:
        return None

    latest    = df.iloc[-1]
    ema_fast  = latest.get("ema_fast")
    ema_slow  = latest.get("ema_slow")
    macro_ref = latest.get("macro_ref")
    close     = latest.get("close")

    if pd.isna(ema_fast) or pd.isna(ema_slow) or pd.isna(macro_ref):
        return None

    if ema_fast > ema_slow and close > macro_ref:
        return "long"
    elif ema_fast < ema_slow and close < macro_ref:
        return "short"

    return None


def get_mtf_direction(df_higher: pd.DataFrame, df_confirm: pd.DataFrame) -> Optional[str]:
    """
    Get direction from two timeframes — both must agree.
    Used for 1H entries (1D + 4H must agree).
    """
    if not MTF_CONFIG.get("all_required", True):
        return "any"

    dir_higher  = get_timeframe_direction(df_higher)
    dir_confirm = get_timeframe_direction(df_confirm)

    if dir_higher is None or dir_confirm is None:
        return None

    if dir_higher == dir_confirm:
        return dir_higher

    return None  # Disagree — market transitioning


def get_mtf_direction_for_entry(
    timeframe: str,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_1d: pd.DataFrame,
) -> Optional[str]:
    """
    Get the required MTF direction based on the token's entry timeframe.

    1H entry: 1D + 4H must both agree → confirmed direction
    4H entry: 1D must confirm → 1D direction
    1D entry: 1D is the entry timeframe → use 1D direction directly

    Returns the required direction, or None if confirmation fails.
    """
    if timeframe == "1h":
        return get_mtf_direction(df_1d, df_4h)
    elif timeframe == "4h":
        return get_timeframe_direction(df_1d)
    elif timeframe == "1d":
        return get_timeframe_direction(df_1d)
    return None


def get_entry_df(
    timeframe: str,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_1d: pd.DataFrame,
) -> Optional[pd.DataFrame]:
    """Return the dataframe corresponding to the token's assigned entry timeframe."""
    mapping = {"1h": df_1h, "4h": df_4h, "1d": df_1d}
    return mapping.get(timeframe, df_1h)


# =============================================================================
# TIMEFRAME SCHEDULING
# =============================================================================

def should_generate_signal(timeframe: str) -> bool:
    """
    Determine whether signal generation should run for this timeframe this cycle.

    FIX from v3.1: Previously gated 1D signals to UTC hour 0 only, which
    conflicted with the session filter blocking Asian session (hour 0).
    This created a permanent deadlock for 1D-assigned tokens — they could
    never execute a trade.

    Now returns True for all timeframes on every cycle. This is safe because:
    - OHLCV data already only contains closed candles (fetch_ohlcv drops the
      last forming candle), so signals are always based on correct data.
    - Duplicate entry prevention is handled by open_symbols checks downstream.
    - The session filter controls when entries are actually allowed to execute.

    The previous optimisation (avoiding computation on non-candle-close cycles)
    is sacrificed in favour of correctness. The added overhead is negligible.
    """
    return True


# =============================================================================
# CONFLUENCE CHECK
# =============================================================================

def check_confluence_mtf(
    df: pd.DataFrame,
    indicators: list,
    min_confluence: int,
    required_direction: str,
) -> tuple:
    """
    Check confluence on the entry timeframe dataframe.
    Signal must match required_direction from MTF confirmation.

    Returns (direction, confluence_count) or (None, 0).
    """
    if df is None or df.empty:
        return None, 0

    signals = {}
    for ind in indicators:
        fn = SIGNAL_FUNCTIONS.get(ind)
        if fn:
            sig = fn(df)
            if sig:
                signals[ind] = sig

    ema_sig   = signals.get("ema")
    macro_sig = signals.get("macro_ref")

    if not ema_sig or not macro_sig or ema_sig != macro_sig:
        return None, 0

    direction = ema_sig

    if required_direction != "any" and direction != required_direction:
        return None, 0

    agreeing = sum(1 for s in signals.values() if s == direction)

    if agreeing >= min_confluence:
        return direction, agreeing

    return None, 0


# =============================================================================
# ADDITIONAL LIVE FILTERS
# =============================================================================

def passes_rsi_zone_live(df: pd.DataFrame, direction: str) -> bool:
    """
    Gate entries based on RSI zone.
    Prevents entering longs when RSI is already overbought,
    or shorts when RSI is already deeply oversold.

    FIX: short_min_rsi reduced from 40 to 28 in config.py.
    Previously blocked ALL short signals in Extreme Fear (RSI 20–35).
    """
    rsi_config = FILTERS.get("rsi_zone", {})
    if not rsi_config.get("enabled", True):
        return True
    if df is None or df.empty:
        return True
    rsi = df.iloc[-1].get("rsi")
    if pd.isna(rsi):
        return True
    if direction == "long"  and rsi > rsi_config.get("long_max_rsi", 60):  return False
    if direction == "short" and rsi < rsi_config.get("short_min_rsi", 28): return False
    return True


def passes_price_position_live(df: pd.DataFrame, direction: str) -> bool:
    """
    Gate entries based on price distance from fast EMA.
    Prevents chasing extended moves.

    FIX: max_ema_distance increased from 3% to 8% in config.py.
    3% was too tight — blocked entries in trending markets where price
    routinely sits 5–15% from the 20-period EMA.
    """
    pp_config = FILTERS.get("price_position", {})
    if not pp_config.get("enabled", True):
        return True
    if df is None or df.empty:
        return True
    latest   = df.iloc[-1]
    ema_fast = latest.get("ema_fast")
    close    = latest.get("close")
    if pd.isna(ema_fast) or ema_fast == 0:
        return True
    distance = abs(close - ema_fast) / ema_fast
    return distance <= pp_config.get("max_ema_distance", 0.08)


# =============================================================================
# SL/TP CALCULATION
# =============================================================================

def calculate_sl_tp_live(
    df: pd.DataFrame,
    direction: str,
    tier_rrr: float,
) -> Optional[dict]:
    """
    Calculate SL and TP from the entry timeframe data using ATR + structure.

    SL placement logic:
    1. ATR-based minimum: SL = 1.5× ATR (ensures minimum breathing room)
    2. Structure-based: SL just beyond nearest S/R (10 candle lookback)
    3. Take the wider of ATR vs structure — never compromise SL tightness
    4. Apply floor: SL distance must be at least SL["min_pct"] of entry (1.5%)
       FIX: This floor was missing. SLs as tight as 0.52% were hitting on noise.
    5. Apply cap: SL distance cannot exceed SL["max_pct"] of entry (3%)

    The floor and cap together bound SL distance between 1.5% and 3%.
    """
    if df is None or df.empty:
        return None

    latest = df.iloc[-1]
    entry  = latest["close"]
    atr    = latest["atr"]

    if pd.isna(atr) or atr == 0:
        return None

    atr_sl_dist = atr * SL["atr_multiplier"]
    lookback    = min(10, len(df) - 1)

    if direction == "long":
        structure_low = df["low"].iloc[-lookback:].min()
        sl_dist       = max(entry - (structure_low - atr * 0.1), atr_sl_dist)
        stop_loss     = entry - sl_dist
        take_profit   = entry + sl_dist * tier_rrr
        if take_profit <= entry: return None
    else:
        structure_high = df["high"].iloc[-lookback:].max()
        sl_dist        = max((structure_high + atr * 0.1) - entry, atr_sl_dist)
        stop_loss      = entry + sl_dist
        take_profit    = entry - sl_dist * tier_rrr
        if take_profit >= entry: return None

    if sl_dist <= 0:
        return None

    # --- FLOOR: SL distance must be at least 1.5% of entry ---
    # FIX: Previously absent. Prevented noise-level SLs (0.52–0.94%)
    # that were guaranteed to hit on normal candle wicks.
    min_sl_dist = entry * SL.get("min_pct", 0.015)
    if sl_dist < min_sl_dist:
        sl_dist = min_sl_dist
        if direction == "long":
            stop_loss   = entry - sl_dist
            take_profit = entry + sl_dist * tier_rrr
        else:
            stop_loss   = entry + sl_dist
            take_profit = entry - sl_dist * tier_rrr

    # --- CAP: SL distance cannot exceed 3% of entry ---
    max_sl_dist = entry * SL.get("max_pct", 0.03)
    if sl_dist > max_sl_dist:
        sl_dist = max_sl_dist
        if direction == "long":
            stop_loss   = entry - sl_dist
            take_profit = entry + sl_dist * tier_rrr
        else:
            stop_loss   = entry + sl_dist
            take_profit = entry - sl_dist * tier_rrr

    # Final RRR check — must still meet tier minimum after adjustments
    actual_rrr = abs(take_profit - entry) / sl_dist
    if actual_rrr < tier_rrr:
        return None

    return {
        "entry":       entry,
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "sl_distance": sl_dist,
        "atr":         atr,
        "rrr":         actual_rrr,
    }


# =============================================================================
# SIGNAL SCORING
# =============================================================================

def score_signal(
    confluence_count: int,
    strategy_score: float,
    tier: str,
    mtf_direction: str,
) -> float:
    """Score a signal for queue priority. Tier1 signals get priority."""
    tier_bonuses     = {"tier1": 1.3, "tier2": 1.1, "tier3": 1.0}
    confluence_bonus = 1.0 + (confluence_count / 6) * 0.5
    tier_bonus       = tier_bonuses.get(tier, 1.0)
    score            = strategy_score * confluence_bonus * tier_bonus
    return round(score, 6)


# =============================================================================
# MAIN SIGNAL GENERATION
# =============================================================================

def generate_signal_for_token(
    strategy: dict,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_1d: pd.DataFrame,
    cycle_data: dict,
    open_trades: list,
    price_history: dict,
    cooldown_tracker: dict,
) -> Optional[dict]:
    """
    Generate a trading signal for a single token.

    Entry timeframe is read from the strategy's assigned timeframe (apex.db).
    MTF confirmation scales with the entry timeframe:
    - 1H entry: 1D + 4H must agree → check 1H confluence
    - 4H entry: 1D must confirm    → check 4H confluence
    - 1D entry: 1D direction used  → check 1D confluence

    BTC filter is applied on the same timeframe as the entry.
    """
    symbol     = strategy["symbol"]
    tier       = strategy["tier"]
    indicators = strategy["indicators"]
    min_conf   = strategy["min_confluence"]
    tier_rrr   = strategy["tier_rrr"]
    comp_score = strategy["composite_score"]
    timeframe  = strategy.get("timeframe", "1h")

    # STEP 1 — Check if this timeframe's candle should be evaluated this cycle.
    # FIX: Returns True for all timeframes now. See should_generate_signal() docstring.
    if not should_generate_signal(timeframe):
        logger.debug(f"{symbol}: {timeframe} candle not due this cycle — skipping")
        return None

    # STEP 2 — Get the entry dataframe for this token's assigned timeframe
    df_entry = get_entry_df(timeframe, df_1h, df_4h, df_1d)
    if df_entry is None or df_entry.empty:
        logger.debug(f"{symbol}: No {timeframe} data available")
        return None

    # STEP 3 — Get MTF confirmation direction based on entry timeframe
    mtf_direction = get_mtf_direction_for_entry(timeframe, df_1h, df_4h, df_1d)
    if mtf_direction is None:
        logger.debug(f"{symbol}: MTF confirmation failed for {timeframe} entry")
        return None

    # STEP 4 — Check confluence on the entry timeframe
    direction, confluence_count = check_confluence_mtf(
        df_entry, indicators, min_conf, mtf_direction
    )
    if not direction:
        return None

    # STEP 5 — RSI zone filter on entry timeframe
    if not passes_rsi_zone_live(df_entry, direction):
        logger.debug(f"{symbol}: RSI zone filter blocked {direction} on {timeframe}")
        return None

    # STEP 6 — Price position filter on entry timeframe
    if not passes_price_position_live(df_entry, direction):
        logger.debug(f"{symbol}: Price too extended from EMA on {timeframe}")
        return None

    # STEP 7 — Calculate SL/TP from entry timeframe data
    sl_tp = calculate_sl_tp_live(df_entry, direction, tier_rrr)
    if sl_tp is None:
        logger.debug(f"{symbol}: No valid SL/TP for {direction} on {timeframe}")
        return None

    # STEP 8 — Run all entry filters (BTC filter uses entry timeframe)
    btc_trend      = cycle_data.get("btc_trend", {"direction": "neutral"})
    funding_rates  = cycle_data.get("funding_rates", {})
    fear_greed     = cycle_data.get("fear_greed", {"value": 50})
    funding_rate   = funding_rates.get(symbol, 0.0)
    fear_greed_val = fear_greed.get("value", 50)
    daily_volume   = strategy.get("daily_volume_usd", 999_999_999)

    filter_result = run_all_filters(
        symbol           = symbol,
        direction        = direction,
        tier             = tier,
        df               = df_entry,
        btc_trend        = btc_trend,
        funding_rate     = funding_rate,
        fear_greed_value = fear_greed_val,
        daily_volume_usd = daily_volume,
        confluence_count = confluence_count,
        open_trades      = open_trades,
        price_history    = price_history,
        cooldown_tracker = cooldown_tracker,
        timeframe        = timeframe,
    )

    if not filter_result["passed"]:
        logger.debug(f"{symbol}: Filters blocked — {filter_result['failures']}")
        return None

    # STEP 9 — Score and build signal
    signal_score = score_signal(confluence_count, comp_score, tier, mtf_direction)

    signal = {
        "symbol":           symbol,
        "direction":        direction,
        "tier":             tier,
        "timeframe":        timeframe,
        "mtf_direction":    mtf_direction,
        "entry":            sl_tp["entry"],
        "stop_loss":        sl_tp["stop_loss"],
        "take_profit":      sl_tp["take_profit"],
        "sl_distance":      sl_tp["sl_distance"],
        "atr":              sl_tp["atr"],
        "rrr":              sl_tp["rrr"],
        "signal_score":     signal_score,
        "confluence_count": confluence_count,
        "indicators":       indicators,
        "size_multiplier":  filter_result["size_multiplier"],
        "generated_at":     datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        f"Signal: {symbol.replace('/USDT:USDT', '')} {direction.upper()} | "
        f"{tier} | {timeframe} | MTF:{mtf_direction} | "
        f"Score:{signal_score:.4f} | Conf:{confluence_count} | "
        f"RRR:{sl_tp['rrr']:.2f} | SL:{sl_tp['sl_distance'] / sl_tp['entry'] * 100:.2f}%"
    )

    return signal


def generate_signals(
    strategies: list,
    cycle_data: dict,
    open_trades: list,
    price_history: dict,
    cooldown_tracker: dict,
) -> list:
    """
    Generate signals for all active tokens.
    Each token uses its assigned timeframe from apex.db.
    Returns list sorted by signal_score descending (Tier1 first).
    """
    ohlcv_data   = cycle_data.get("ohlcv", {})
    signals      = []
    open_symbols = {t["symbol"] for t in open_trades}

    for strategy in strategies:
        symbol    = strategy["symbol"]
        timeframe = strategy.get("timeframe", "1h")

        # Skip tokens that already have an open trade
        if symbol in open_symbols:
            continue

        # Get all 3 timeframes for this token
        df_1h = ohlcv_data.get(f"{symbol}_1h")
        if df_1h is None: df_1h = ohlcv_data.get(symbol)
        df_4h = ohlcv_data.get(f"{symbol}_4h")
        df_1d = ohlcv_data.get(f"{symbol}_1d")

        if df_1h is None or df_4h is None or df_1d is None:
            logger.debug(f"{symbol}: Missing timeframe data — skipping")
            continue

        try:
            signal = generate_signal_for_token(
                strategy         = strategy,
                df_1h            = df_1h,
                df_4h            = df_4h,
                df_1d            = df_1d,
                cycle_data       = cycle_data,
                open_trades      = open_trades,
                price_history    = price_history,
                cooldown_tracker = cooldown_tracker,
            )
            if signal:
                signals.append(signal)
                try:
                    from bot.config import apex_logger
                    _fg  = cycle_data.get("fear_greed", {})
                    _btc = cycle_data.get("btc_trend", {})
                    apex_logger.signal_scan_complete(
                        token      = symbol.replace("/USDT:USDT", ""),
                        timeframe  = signal["timeframe"],
                        strategy   = strategy.get("strategy_type", strategy.get("tier", "unknown")),
                        tier       = signal["tier"],
                        result     = "entered",
                        reason     = "all_conditions_met",
                        direction  = signal["direction"],
                        indicators = {
                            "indicators_agreeing": signal.get("confluence_count", 0),
                            "rrr":                 signal.get("rrr", 0),
                        },
                        filters_result = {
                            "passed":    True,
                            "btc_trend": _btc.get("direction", "?"),
                            "btc_tf":    _btc.get(timeframe, "?"),
                            "fg_index":  _fg.get("value", 50),
                        },
                        market = {
                            "price":         signal["entry"],
                            "atr":           signal.get("atr", 0),
                            "potential_rrr": signal.get("rrr", 0),
                        },
                    )
                except Exception:
                    pass
            else:
                try:
                    from bot.config import apex_logger
                    _fg  = cycle_data.get("fear_greed", {})
                    _btc = cycle_data.get("btc_trend", {})
                    apex_logger.signal_scan_complete(
                        token      = symbol.replace("/USDT:USDT", ""),
                        timeframe  = timeframe,
                        strategy   = strategy.get("strategy_type", strategy.get("tier", "unknown")),
                        tier       = strategy["tier"],
                        result     = "no_signal",
                        reason     = "filtered_or_no_confluence",
                        direction  = None,
                        indicators = {},
                        filters_result = {
                            "passed":    False,
                            "btc_trend": _btc.get("direction", "?"),
                            "btc_tf":    _btc.get(timeframe, "?"),
                            "fg_index":  _fg.get("value", 50),
                        },
                        market = {},
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Signal error {symbol}: {e}", exc_info=True)

    # Sort by signal_score descending — Tier1 naturally floats to top
    signals.sort(key=lambda x: x["signal_score"], reverse=True)

    if signals:
        logger.info(
            f"Generated {len(signals)} signal(s): "
            f"{[s['symbol'].replace('/USDT:USDT', '') + ' ' + s['direction'] + ' [' + s['timeframe'] + ']' for s in signals]}"
        )
    else:
        logger.info("No signals this cycle")

    return signals


def build_price_history(ohlcv_data: dict, lookback: int = 90) -> dict:
    """Build price history for correlation filter."""
    history = {}
    for key, df in ohlcv_data.items():
        if df is not None and not df.empty:
            symbol = key.replace("_1h", "").replace("_4h", "").replace("_1d", "")
            if symbol not in history:
                history[symbol] = df["close"].tail(lookback).tolist()
    return history

# __APEX_LOGGER_V1__
