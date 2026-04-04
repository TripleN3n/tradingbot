# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# apex/backtest_engine.py — Vectorized Multi-Strategy Engine v4.1
# =============================================================================
# SPEED IMPROVEMENT in v4.1:
# All indicator signals pre-computed as numpy arrays ONCE per window.
# Inner loop does array lookups only — no per-candle calculations.
# Expected speedup: 5-10x over v4.0
#
# STRATEGY TYPES:
# 1. MTF Trend      — 1D+4H confirm, 1H triggers
# 2. Single TF      — Best single timeframe (1H, 4H, 1D)
# 3. Mean Reversion — RSI extremes + Bollinger boundaries
# 4. Breakout       — Volume surge + Bollinger squeeze
# =============================================================================

import pandas as pd
import numpy as np
import sqlite3
import logging
import itertools
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent.parent))
from bot.config import (
    BACKTEST, SCORING_MINIMUMS, TIERS,
    EMA, RSI, MACD, BOLLINGER, VOLUME,
    VWAP_TIMEFRAMES, SL, TRAILING_SL, TP,
    FILTERS, CONFIRMATION_INDICATORS,
    TIME_STOP_CANDLES, DB,
)
from apex.data_fetcher import load_ohlcv, get_db_connection

logger = logging.getLogger(__name__)


# =============================================================================
# INDICATOR CALCULATION
# =============================================================================

def calculate_indicators(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"]  = df["close"].ewm(span=EMA["fast"],  adjust=False).mean()
    df["ema_slow"]  = df["close"].ewm(span=EMA["slow"],  adjust=False).mean()
    df["ema_macro"] = df["close"].ewm(span=EMA["macro"], adjust=False).mean()

    if timeframe in VWAP_TIMEFRAMES:
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_vol"]        = df["typical_price"] * df["volume"]
        df["macro_ref"]     = df["tp_vol"].rolling(24).sum() / df["volume"].rolling(24).sum()
    else:
        df["macro_ref"] = df["ema_macro"]

    delta = df["close"].diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=RSI["period"]-1, adjust=False).mean()
    avg_l = loss.ewm(com=RSI["period"]-1, adjust=False).mean()
    df["rsi"] = 100 - (100 / (1 + avg_g / avg_l.replace(0, np.nan)))

    ef  = df["close"].ewm(span=MACD["fast"],   adjust=False).mean()
    es  = df["close"].ewm(span=MACD["slow"],   adjust=False).mean()
    df["macd"]        = ef - es
    df["macd_signal"] = df["macd"].ewm(span=MACD["signal"], adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    df["bb_mid"]   = df["close"].rolling(BOLLINGER["period"]).mean()
    bb_std         = df["close"].rolling(BOLLINGER["period"]).std()
    df["bb_upper"] = df["bb_mid"] + BOLLINGER["std_dev"] * bb_std
    df["bb_lower"] = df["bb_mid"] - BOLLINGER["std_dev"] * bb_std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    df["volume_ma"]    = df["volume"].rolling(VOLUME["period"]).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma"].replace(0, np.nan)

    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift()).abs()
    lc  = (df["low"]  - df["close"].shift()).abs()
    df["atr"] = pd.concat([hl, hc, lc], axis=1).max(axis=1).ewm(
        span=SL["atr_period"], adjust=False).mean()

    df.dropna(inplace=True)
    return df


# =============================================================================
# VECTORIZED SIGNAL PRE-COMPUTATION
# =============================================================================

def precompute_signals(df: pd.DataFrame) -> dict:
    """
    Pre-compute ALL indicator signals as numpy arrays in one pass.
    Returns dict of signal arrays: 1=long, -1=short, 0=no signal.
    This is the core speedup — computed ONCE, reused across all permutations.
    """
    n = len(df)

    close  = df["close"].values
    ef     = df["ema_fast"].values
    es     = df["ema_slow"].values
    mr     = df["macro_ref"].values
    rsi    = df["rsi"].values
    macd   = df["macd"].values
    msig   = df["macd_signal"].values
    mhist  = df["macd_hist"].values
    bbu    = df["bb_upper"].values
    bbl    = df["bb_lower"].values
    bbm    = df["bb_mid"].values
    bbw    = df["bb_width"].values
    vr     = df["volume_ratio"].values

    # EMA signal: fast > slow = long, fast < slow = short
    ema_sig = np.zeros(n, dtype=np.int8)
    ema_sig[ef > es] = 1
    ema_sig[ef < es] = -1

    # Macro ref signal: close > macro_ref = long
    mac_sig = np.zeros(n, dtype=np.int8)
    mac_sig[close > mr] = 1
    mac_sig[close < mr] = -1

    # RSI signal
    rsi_sig = np.zeros(n, dtype=np.int8)
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = rsi[0]
    rsi_sig[rsi < RSI["oversold"]]  = 1
    rsi_sig[rsi > RSI["overbought"]] = -1
    rsi_sig[(rsi > rsi_prev) & (rsi < 60) & (rsi_sig == 0)] = 1
    rsi_sig[(rsi < rsi_prev) & (rsi > 40) & (rsi_sig == 0)] = -1

    # MACD signal
    macd_sig  = np.zeros(n, dtype=np.int8)
    mhist_prev = np.roll(mhist, 1); mhist_prev[0] = mhist[0]
    macd_sig[(macd > msig) & (mhist > mhist_prev)] = 1
    macd_sig[(macd < msig) & (mhist < mhist_prev)] = -1

    # Bollinger signal
    bb_sig   = np.zeros(n, dtype=np.int8)
    bbw_prev = np.roll(bbw, 1); bbw_prev[0] = bbw[0]
    # Squeeze breakout
    expanding = bbw > bbw_prev
    bb_sig[expanding & (close > bbm)] = 1
    bb_sig[expanding & (close < bbm)] = -1
    # Mean reversion at bands
    bb_sig[close <= bbl] = 1
    bb_sig[close >= bbu] = -1

    # Volume signal
    vol_sig   = np.zeros(n, dtype=np.int8)
    close_prev = np.roll(close, 1); close_prev[0] = close[0]
    high_vol   = vr >= VOLUME["min_multiplier"]
    vol_sig[high_vol & (close > close_prev)] = 1
    vol_sig[high_vol & (close <= close_prev)] = -1

    # Volume ratio array for filtering
    vol_ratio = vr

    # ATR array
    atr = df["atr"].values

    return {
        "ema":       ema_sig,
        "macro_ref": mac_sig,
        "rsi":       rsi_sig,
        "macd":      macd_sig,
        "bollinger": bb_sig,
        "volume":    vol_sig,
        "vol_ratio": vol_ratio,
        "atr":       atr,
        "close":     close,
        "high":      df["high"].values,
        "low":       df["low"].values,
        "rsi_raw":   rsi,
        "ema_fast":  ef,
    }


def precompute_mtf(df_1h: pd.DataFrame, df_4h: pd.DataFrame, df_1d: pd.DataFrame) -> np.ndarray:
    """
    Pre-compute MTF direction for every 1H candle as numpy array.
    Returns array: 1=long, -1=short, 0=no signal.
    O(n) pointer scan — computed ONCE per window.
    """
    n   = len(df_1h)
    mtf = np.zeros(n, dtype=np.int8)

    def get_dir(df, i):
        if i < 1 or i >= len(df): return 0
        ef = df["ema_fast"].iloc[i]; es = df["ema_slow"].iloc[i]
        mr = df["macro_ref"].iloc[i]; cl = df["close"].iloc[i]
        if pd.isna(ef) or pd.isna(es) or pd.isna(mr): return 0
        if ef > es and cl > mr: return 1
        if ef < es and cl < mr: return -1
        return 0

    i4 = 0; i1 = 0
    n4 = len(df_4h); n1 = len(df_1d)

    for idx, ts in enumerate(df_1h.index):
        while i4 < n4-1 and df_4h.index[i4+1] <= ts: i4 += 1
        while i1 < n1-1 and df_1d.index[i1+1] <= ts: i1 += 1
        if i4 < 1 or i1 < 1: continue
        d4 = get_dir(df_4h, i4)
        d1 = get_dir(df_1d, i1)
        if d4 != 0 and d4 == d1:
            mtf[idx] = d4

    return mtf


# =============================================================================
# SL/TP CALCULATION
# =============================================================================

def calc_sl_tp(sigs, i, direction, rrr):
    """ATR + structure based SL/TP using pre-computed arrays."""
    entry = sigs["close"][i]
    atr   = sigs["atr"][i]
    if np.isnan(atr) or atr == 0: return None

    atr_dist = atr * SL["atr_multiplier"]
    lb       = max(0, i - 10)

    if direction == 1:  # long
        sl_dist = max(entry - (sigs["low"][lb:i+1].min() - atr*0.1), atr_dist)
        return entry, entry - sl_dist, entry + sl_dist * rrr, sl_dist
    else:  # short
        sl_dist = max((sigs["high"][lb:i+1].max() + atr*0.1) - entry, atr_dist)
        return entry, entry + sl_dist, entry - sl_dist * rrr, sl_dist


# =============================================================================
# TRADE SIMULATION — TIERED EXIT
# =============================================================================

def simulate(sigs, idx, direction, entry, sl, tp_unused, sl_dist, tf):
    """
    Tiered exit:
    - 40% at 1.5x RRR
    - 30% at 2.0x RRR
    - 30% trailing with 1.5x ATR
    """
    ts      = TIME_STOP_CANDLES.get(tf, 24)
    n       = len(sigs["close"])
    csl     = sl
    be      = sl_dist * TRAILING_SL["breakeven_at"]
    tr_lock = sl_dist * TRAILING_SL["trail_at"]
    rem     = 1.0; total = 0.0
    t1_done = False; t2_done = False

    t1_pct  = TP.get("tier1_close_pct", 0.40)
    t2_pct  = TP.get("tier2_close_pct", 0.30)
    at_mult = TP.get("trail_atr_multiplier", 1.5)

    if direction == 1:
        tp1 = entry + sl_dist * TP.get("tier1_rrr", 1.5)
        tp2 = entry + sl_dist * TP.get("tier2_rrr", 2.0)
    else:
        tp1 = entry - sl_dist * TP.get("tier1_rrr", 1.5)
        tp2 = entry - sl_dist * TP.get("tier2_rrr", 2.0)

    for j in range(idx+1, min(idx+ts+1, n)):
        hi = sigs["high"][j]; lo = sigs["low"][j]
        cl = sigs["close"][j]; at = sigs["atr"][j]

        if direction == 1:
            pd_ = cl - entry
            if lo <= csl:
                total += (csl - entry) / entry * rem
                return {"pnl_pct": total, "exit_reason": "stop_loss", "win": total > 0}
            if pd_ >= be and csl < entry: csl = entry
            if pd_ >= tr_lock: csl = max(csl, entry + sl_dist * TRAILING_SL["trail_lock"])
            if not t1_done and hi >= tp1:
                total += (tp1-entry)/entry * t1_pct; rem -= t1_pct
                t1_done = True; csl = max(csl, entry)
            if t1_done and not t2_done and hi >= tp2:
                total += (tp2-entry)/entry * t2_pct; rem -= t2_pct
                t2_done = True; csl = max(csl, tp1)
            if t2_done and not np.isnan(at):
                csl = max(csl, cl - at * at_mult)
        else:
            pd_ = entry - cl
            if hi >= csl:
                total += (entry-csl)/entry * rem
                return {"pnl_pct": total, "exit_reason": "stop_loss", "win": total > 0}
            if pd_ >= be and csl > entry: csl = entry
            if pd_ >= tr_lock: csl = min(csl, entry - sl_dist * TRAILING_SL["trail_lock"])
            if not t1_done and lo <= tp1:
                total += (entry-tp1)/entry * t1_pct; rem -= t1_pct
                t1_done = True; csl = min(csl, entry)
            if t1_done and not t2_done and lo <= tp2:
                total += (entry-tp2)/entry * t2_pct; rem -= t2_pct
                t2_done = True; csl = min(csl, tp1)
            if t2_done and not np.isnan(at):
                csl = min(csl, cl + at * at_mult)

    ep    = sigs["close"][min(idx+ts, n-1)]
    total += ((ep-entry) if direction==1 else (entry-ep)) / entry * rem
    reason = "take_profit" if t1_done else "time_stop"
    return {"pnl_pct": total, "exit_reason": reason, "win": total > 0}


# =============================================================================
# FAST BACKTEST WINDOWS — Use pre-computed signal arrays
# =============================================================================

def backtest_mtf(sigs, indicators, min_conf, rrr, mtf_arr, tf="1h"):
    """MTF trend backtest using pre-computed signal arrays."""
    trades = []; cd = 0
    n      = len(sigs["close"])
    rsi_lm = FILTERS.get("rsi_zone", {}).get("long_max_rsi", 60)
    rsi_sm = FILTERS.get("rsi_zone", {}).get("short_min_rsi", 40)
    ema_md = FILTERS.get("price_position", {}).get("max_ema_distance", 0.03)
    vol_mn = VOLUME["min_multiplier"]

    for i in range(1, n-1):
        if cd > 0: cd -= 1; continue
        if sigs["vol_ratio"][i] < vol_mn or np.isnan(sigs["vol_ratio"][i]): continue

        req = mtf_arr[i]
        if req == 0: continue

        # Count agreeing indicators
        agree = 0
        for ind in indicators:
            if sigs[ind][i] == req: agree += 1
        if agree < min_conf: continue

        # EMA and macro_ref mandatory
        if sigs["ema"][i] != req or sigs["macro_ref"][i] != req: continue

        direction = req

        # RSI zone filter
        rsi = sigs["rsi_raw"][i]
        if direction == 1  and rsi > rsi_lm: continue
        if direction == -1 and rsi < rsi_sm: continue

        # Price position filter
        ef = sigs["ema_fast"][i]; cl = sigs["close"][i]
        if ef > 0 and abs(cl - ef) / ef > ema_md: continue

        r = calc_sl_tp(sigs, i, direction, rrr)
        if r is None: continue
        entry, sl, tp, sl_dist = r
        trade = simulate(sigs, i, direction, entry, sl, tp, sl_dist, tf)
        trades.append(trade)
        if trade["exit_reason"] == "stop_loss":
            cd = (FILTERS["cooldown"]["candles_after_sl"].get(tf, 4) if isinstance(FILTERS["cooldown"]["candles_after_sl"], dict) else FILTERS["cooldown"]["candles_after_sl"])

    return trades


def backtest_single_tf(sigs, indicators, min_conf, rrr, tf):
    """Single timeframe trend backtest."""
    trades = []; cd = 0
    n      = len(sigs["close"])
    vol_mn = VOLUME["min_multiplier"]

    for i in range(1, n-1):
        if cd > 0: cd -= 1; continue
        if sigs["vol_ratio"][i] < vol_mn or np.isnan(sigs["vol_ratio"][i]): continue

        # EMA and macro_ref mandatory and must agree
        em = sigs["ema"][i]; mm = sigs["macro_ref"][i]
        if em == 0 or mm == 0 or em != mm: continue
        direction = em

        # Count agreeing indicators
        agree = sum(1 for ind in indicators if sigs[ind][i] == direction)
        if agree < min_conf: continue

        r = calc_sl_tp(sigs, i, direction, rrr)
        if r is None: continue
        entry, sl, tp, sl_dist = r
        trade = simulate(sigs, i, direction, entry, sl, tp, sl_dist, tf)
        trades.append(trade)
        if trade["exit_reason"] == "stop_loss":
            cd = (FILTERS["cooldown"]["candles_after_sl"].get(tf, 4) if isinstance(FILTERS["cooldown"]["candles_after_sl"], dict) else FILTERS["cooldown"]["candles_after_sl"])

    return trades


def backtest_mean_reversion(sigs, rrr, tf):
    """Mean reversion backtest — RSI extremes + Bollinger boundaries."""
    trades = []; cd = 0
    n      = len(sigs["close"])
    close  = sigs["close"]
    rsi    = sigs["rsi_raw"]

    # Pre-compute MR signals vectorized
    bb_lower = None; bb_upper = None
    # Use bollinger signal from sigs — already computed
    # MR long: RSI < 35 AND bollinger = long
    # MR short: RSI > 65 AND bollinger = short
    mr_long  = (rsi < 35) & (sigs["bollinger"] == 1)
    mr_short = (rsi > 65) & (sigs["bollinger"] == -1)

    for i in range(1, n-1):
        if cd > 0: cd -= 1; continue

        if mr_long[i]:   direction = 1
        elif mr_short[i]: direction = -1
        else: continue

        r = calc_sl_tp(sigs, i, direction, rrr)
        if r is None: continue
        entry, sl, tp, sl_dist = r
        trade = simulate(sigs, i, direction, entry, sl, tp, sl_dist, tf)
        trades.append(trade)
        if trade["exit_reason"] == "stop_loss":
            cd = (FILTERS["cooldown"]["candles_after_sl"].get(tf, 4) if isinstance(FILTERS["cooldown"]["candles_after_sl"], dict) else FILTERS["cooldown"]["candles_after_sl"])

    return trades


def backtest_breakout(sigs, rrr, tf):
    """Breakout backtest — volume surge + Bollinger expansion."""
    trades = []; cd = 0
    n      = len(sigs["close"])

    # Pre-compute breakout signals vectorized
    high_vol  = sigs["vol_ratio"] >= 1.5
    bb_expand = sigs["bollinger"] != 0  # BB signal fires on expansion
    bo_long   = high_vol & (sigs["volume"] == 1) & (sigs["ema"] == 1)
    bo_short  = high_vol & (sigs["volume"] == -1) & (sigs["ema"] == -1)

    for i in range(3, n-1):
        if cd > 0: cd -= 1; continue

        if bo_long[i]:   direction = 1
        elif bo_short[i]: direction = -1
        else: continue

        r = calc_sl_tp(sigs, i, direction, rrr)
        if r is None: continue
        entry, sl, tp, sl_dist = r
        trade = simulate(sigs, i, direction, entry, sl, tp, sl_dist, tf)
        trades.append(trade)
        if trade["exit_reason"] == "stop_loss":
            cd = (FILTERS["cooldown"]["candles_after_sl"].get(tf, 4) if isinstance(FILTERS["cooldown"]["candles_after_sl"], dict) else FILTERS["cooldown"]["candles_after_sl"])

    return trades


# =============================================================================
# METRICS & THRESHOLDS
# =============================================================================

def calc_metrics(trades):
    if not trades: return None
    pnls = np.array([t["pnl_pct"] for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    n = len(pnls); wr = len(wins)/n if n else 0
    aw = wins.mean() if len(wins) else 0
    al = losses.mean() if len(losses) else 0
    exp = wr*aw + (1-wr)*al
    gp = wins.sum(); gl = abs(losses.sum())
    pf = gp/gl if gl > 0 else 0
    cum = np.cumsum(pnls); pk = np.maximum.accumulate(cum)
    mdd = float((pk-cum).max()) if len(cum) > 0 else 0
    sh  = pnls.mean()/pnls.std()*np.sqrt(252) if pnls.std() > 0 else 0
    return {
        "win_rate": wr, "expectancy": exp, "profit_factor": pf,
        "max_drawdown": mdd, "sharpe_ratio": sh, "n_trades": n,
        "avg_win": float(aw), "avg_loss": float(al),
    }


def passes_thresholds(m):
    if m["expectancy"]    < SCORING_MINIMUMS["expectancy"]:    return False
    if m["win_rate"]      < SCORING_MINIMUMS["win_rate"]:       return False
    if m["max_drawdown"]  > SCORING_MINIMUMS["max_drawdown"]:   return False
    if m["profit_factor"] < SCORING_MINIMUMS["profit_factor"]:  return False
    if m["sharpe_ratio"]  < SCORING_MINIMUMS["sharpe_ratio"]:   return False
    return True


def check_overfitting(tm, vm):
    if tm["expectancy"] > 0:
        gap = abs(tm["expectancy"] - vm["expectancy"]) / tm["expectancy"]
    else:
        gap = 1.0
    return gap <= BACKTEST["max_overfitting_gap"], gap


def get_permutations():
    mandatory = ["ema", "macro_ref"]
    perms = []
    for r in range(1, len(CONFIRMATION_INDICATORS)+1):
        for combo in itertools.combinations(CONFIRMATION_INDICATORS, r):
            perms.append(mandatory + list(combo))
    return perms


def make_result(symbol, strategy_type, timeframe, tier_name, tier_cfg,
                indicators, tm, vm, gap, train_n, val_n):
    return {
        "symbol":          symbol,
        "strategy_type":   strategy_type,
        "timeframe":       timeframe,
        "tier":            tier_name,
        "indicators":      indicators,
        "min_confluence":  tier_cfg["min_confluence"],
        "tier_rrr":        tier_cfg["min_rrr"],
        "train_metrics":   tm,
        "val_metrics":     vm,
        "train_trades":    train_n,
        "val_trades":      val_n,
        "overfitting_gap": gap,
    }


# =============================================================================
# WINDOW SPLITTING
# =============================================================================

def split_1h(df_1h, df_4h, df_1d):
    tc = BACKTEST["train_months"]    * 24 * 30
    vc = BACKTEST["validate_months"] * 24 * 30
    if len(df_1h) < tc + vc: return None

    w  = df_1h.iloc[-(tc+vc):]
    tr = w.iloc[:tc]; vl = w.iloc[tc:]
    ts = tr.index[0]; te = tr.index[-1]
    vs = vl.index[0]; ve = vl.index[-1]

    f4tr = df_4h[(df_4h.index>=ts)&(df_4h.index<=te)]
    f1tr = df_1d[(df_1d.index>=ts)&(df_1d.index<=te)]
    f4vl = df_4h[(df_4h.index>=vs)&(df_4h.index<=ve)]
    f1vl = df_1d[(df_1d.index>=vs)&(df_1d.index<=ve)]

    if len(f4tr) < 30 or len(f1tr) < 10: return None
    return tr, vl, f4tr, f1tr, f4vl, f1vl


def split_tf(df, tc, vc):
    if len(df) < tc + vc: return None, None
    w = df.iloc[-(tc+vc):]
    return w.iloc[:tc], w.iloc[tc:]


# =============================================================================
# MAIN BACKTEST RUNNER
# =============================================================================

def run_backtest_for_token(conn, symbol):
    """
    Exhaustive multi-strategy backtest with vectorized signal computation.
    Pre-computes ALL signals once per window — then reuses across permutations.
    """
    logger.info(f"Backtesting {symbol} (v4.1 vectorized)...")

    df_1h = load_ohlcv(conn, symbol, "1h")
    df_4h = load_ohlcv(conn, symbol, "4h")
    df_1d = load_ohlcv(conn, symbol, "1d")

    if df_1h.empty or len(df_1h) < 500:
        logger.warning(f"  {symbol} insufficient 1H"); return []
    if df_4h.empty or len(df_4h) < 100:
        logger.warning(f"  {symbol} insufficient 4H"); return []
    if df_1d.empty or len(df_1d) < 30:
        logger.warning(f"  {symbol} insufficient 1D"); return []

    try:
        df_1h = calculate_indicators(df_1h, "1h")
        df_4h = calculate_indicators(df_4h, "4h")
        df_1d = calculate_indicators(df_1d, "1d")
    except Exception as e:
        logger.error(f"  {symbol} indicator error: {e}"); return []

    # Split windows
    windows = split_1h(df_1h, df_4h, df_1d)
    if windows is None:
        logger.warning(f"  {symbol} insufficient data"); return []

    tr_1h, vl_1h, f4tr, f1tr, f4vl, f1vl = windows

    tc_4h = BACKTEST["train_months"]    * 6 * 30
    vc_4h = BACKTEST["validate_months"] * 6 * 30
    tc_1d = BACKTEST["train_months"]    * 30
    vc_1d = BACKTEST["validate_months"] * 30

    tr_4h, vl_4h = split_tf(df_4h, tc_4h, vc_4h)
    tr_1d, vl_1d = split_tf(df_1d, tc_1d, vc_1d)

    # ----------------------------------------------------------------
    # PRE-COMPUTE ALL SIGNALS ONCE PER WINDOW — KEY SPEEDUP
    # ----------------------------------------------------------------
    tr_sigs_1h = precompute_signals(tr_1h)
    vl_sigs_1h = precompute_signals(vl_1h)

    tr_sigs_4h = precompute_signals(tr_4h) if tr_4h is not None else None
    vl_sigs_4h = precompute_signals(vl_4h) if vl_4h is not None else None

    tr_sigs_1d = precompute_signals(tr_1d) if tr_1d is not None else None
    vl_sigs_1d = precompute_signals(vl_1d) if vl_1d is not None else None

    # MTF direction arrays
    mtf_tr = precompute_mtf(tr_1h, f4tr, f1tr)
    mtf_vl = precompute_mtf(vl_1h, f4vl, f1vl)

    n_mtf_tr = int((mtf_tr != 0).sum())
    n_mtf_vl = int((mtf_vl != 0).sum())
    logger.info(f"  {symbol} — MTF signals: train={n_mtf_tr}, val={n_mtf_vl}")

    perms = get_permutations()
    min_t = BACKTEST["min_trades"]
    min_1h = min_t.get("1h", 10) if isinstance(min_t, dict) else 10
    min_4h = min_t.get("4h", 5)  if isinstance(min_t, dict) else 5
    min_1d = min_t.get("1d", 3)  if isinstance(min_t, dict) else 3
    min_v  = 3

    all_results = []

    logger.info(f"  {symbol} — {len(perms) * len(TIERS)} MTF + {len(perms) * len(TIERS) * 3} single TF + MR + BO")

    for tier_name, tier_cfg in TIERS.items():
        rrr      = tier_cfg["min_rrr"]
        min_conf = tier_cfg["min_confluence"]

        for indicators in perms:
            try:
                # --- MTF TREND ---
                if n_mtf_tr >= min_1h and n_mtf_vl >= min_v:
                    tr_t = backtest_mtf(tr_sigs_1h, indicators, min_conf, rrr, mtf_tr, "1h")
                    if len(tr_t) >= min_1h:
                        vl_t = backtest_mtf(vl_sigs_1h, indicators, min_conf, rrr, mtf_vl, "1h")
                        if len(vl_t) >= min_v:
                            tm = calc_metrics(tr_t); vm = calc_metrics(vl_t)
                            if tm and vm and passes_thresholds(vm):
                                ok, gap = check_overfitting(tm, vm)
                                if ok:
                                    all_results.append(make_result(
                                        symbol, "mtf_trend", "1h", tier_name, tier_cfg,
                                        indicators, tm, vm, gap, len(tr_t), len(vl_t)
                                    ))

                # --- SINGLE TF 1H ---
                tr_t = backtest_single_tf(tr_sigs_1h, indicators, min_conf, rrr, "1h")
                if len(tr_t) >= min_1h:
                    vl_t = backtest_single_tf(vl_sigs_1h, indicators, min_conf, rrr, "1h")
                    if len(vl_t) >= min_v:
                        tm = calc_metrics(tr_t); vm = calc_metrics(vl_t)
                        if tm and vm and passes_thresholds(vm):
                            ok, gap = check_overfitting(tm, vm)
                            if ok:
                                all_results.append(make_result(
                                    symbol, "single_tf", "1h", tier_name, tier_cfg,
                                    indicators, tm, vm, gap, len(tr_t), len(vl_t)
                                ))

                # --- SINGLE TF 4H ---
                if tr_sigs_4h and vl_sigs_4h:
                    tr_t = backtest_single_tf(tr_sigs_4h, indicators, min_conf, rrr, "4h")
                    if len(tr_t) >= min_4h:
                        vl_t = backtest_single_tf(vl_sigs_4h, indicators, min_conf, rrr, "4h")
                        if len(vl_t) >= min_v:
                            tm = calc_metrics(tr_t); vm = calc_metrics(vl_t)
                            if tm and vm and passes_thresholds(vm):
                                ok, gap = check_overfitting(tm, vm)
                                if ok:
                                    all_results.append(make_result(
                                        symbol, "single_tf", "4h", tier_name, tier_cfg,
                                        indicators, tm, vm, gap, len(tr_t), len(vl_t)
                                    ))

                # --- SINGLE TF 1D ---
                if tr_sigs_1d and vl_sigs_1d:
                    tr_t = backtest_single_tf(tr_sigs_1d, indicators, min_conf, rrr, "1d")
                    if len(tr_t) >= min_1d:
                        vl_t = backtest_single_tf(vl_sigs_1d, indicators, min_conf, rrr, "1d")
                        if len(vl_t) >= min_v:
                            tm = calc_metrics(tr_t); vm = calc_metrics(vl_t)
                            if tm and vm and passes_thresholds(vm):
                                ok, gap = check_overfitting(tm, vm)
                                if ok:
                                    all_results.append(make_result(
                                        symbol, "single_tf", "1d", tier_name, tier_cfg,
                                        indicators, tm, vm, gap, len(tr_t), len(vl_t)
                                    ))

            except Exception as e:
                logger.debug(f"  Perm error: {e}")
                continue

        # --- MEAN REVERSION 1H ---
        try:
            tr_t = backtest_mean_reversion(tr_sigs_1h, rrr, "1h")
            if len(tr_t) >= min_1h:
                vl_t = backtest_mean_reversion(vl_sigs_1h, rrr, "1h")
                if len(vl_t) >= min_v:
                    tm = calc_metrics(tr_t); vm = calc_metrics(vl_t)
                    if tm and vm and passes_thresholds(vm):
                        ok, gap = check_overfitting(tm, vm)
                        if ok:
                            all_results.append(make_result(
                                symbol, "mean_reversion", "1h", tier_name, tier_cfg,
                                ["rsi", "bollinger"], tm, vm, gap, len(tr_t), len(vl_t)
                            ))
        except Exception as e:
            logger.debug(f"  MR 1H error: {e}")

        # --- MEAN REVERSION 4H ---
        if tr_sigs_4h and vl_sigs_4h:
            try:
                tr_t = backtest_mean_reversion(tr_sigs_4h, rrr, "4h")
                if len(tr_t) >= min_4h:
                    vl_t = backtest_mean_reversion(vl_sigs_4h, rrr, "4h")
                    if len(vl_t) >= min_v:
                        tm = calc_metrics(tr_t); vm = calc_metrics(vl_t)
                        if tm and vm and passes_thresholds(vm):
                            ok, gap = check_overfitting(tm, vm)
                            if ok:
                                all_results.append(make_result(
                                    symbol, "mean_reversion", "4h", tier_name, tier_cfg,
                                    ["rsi", "bollinger"], tm, vm, gap, len(tr_t), len(vl_t)
                                ))
            except Exception as e:
                logger.debug(f"  MR 4H error: {e}")

        # --- BREAKOUT 1H ---
        try:
            tr_t = backtest_breakout(tr_sigs_1h, rrr, "1h")
            if len(tr_t) >= min_1h:
                vl_t = backtest_breakout(vl_sigs_1h, rrr, "1h")
                if len(vl_t) >= min_v:
                    tm = calc_metrics(tr_t); vm = calc_metrics(vl_t)
                    if tm and vm and passes_thresholds(vm):
                        ok, gap = check_overfitting(tm, vm)
                        if ok:
                            all_results.append(make_result(
                                symbol, "breakout", "1h", tier_name, tier_cfg,
                                ["bollinger", "volume"], tm, vm, gap, len(tr_t), len(vl_t)
                            ))
        except Exception as e:
            logger.debug(f"  BO 1H error: {e}")

        # --- BREAKOUT 4H ---
        if tr_sigs_4h and vl_sigs_4h:
            try:
                tr_t = backtest_breakout(tr_sigs_4h, rrr, "4h")
                if len(tr_t) >= min_4h:
                    vl_t = backtest_breakout(vl_sigs_4h, rrr, "4h")
                    if len(vl_t) >= min_v:
                        tm = calc_metrics(tr_t); vm = calc_metrics(vl_t)
                        if tm and vm and passes_thresholds(vm):
                            ok, gap = check_overfitting(tm, vm)
                            if ok:
                                all_results.append(make_result(
                                    symbol, "breakout", "4h", tier_name, tier_cfg,
                                    ["bollinger", "volume"], tm, vm, gap, len(tr_t), len(vl_t)
                                ))
            except Exception as e:
                logger.debug(f"  BO 4H error: {e}")

    # Sort: win rate first, then expectancy
    all_results.sort(
        key=lambda x: (x["val_metrics"]["win_rate"], x["val_metrics"]["expectancy"]),
        reverse=True
    )

    if all_results:
        b  = all_results[0]
        vm = b["val_metrics"]
        logger.info(
            f"  {symbol} — {len(all_results)} valid | "
            f"Best: {b['strategy_type']} {b['timeframe']} "
            f"WR:{vm['win_rate']:.1%} Exp:{vm['expectancy']:.4f}"
        )
    else:
        logger.info(f"  {symbol} — 0 valid strategies found")

    return all_results


def run_backtest_for_tokens(conn, symbols):
    all_results = {}
    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{len(symbols)}] {symbol}")
        try:
            results = run_backtest_for_token(conn, symbol)
            all_results[symbol] = results
            if results:
                try:
                    from bot.config import apex_logger
                    best = results[0]; vm = best["val_metrics"]
                    apex_logger.backtest_result(
                        token          = symbol,
                        timeframe      = best["timeframe"],
                        strategy_name  = best.get("strategy_type", "unknown"),
                        win_rate       = vm["win_rate"],
                        expectancy     = vm["expectancy"],
                        profit_factor  = vm["profit_factor"],
                        sharpe         = vm["sharpe_ratio"],
                        max_drawdown   = vm["max_drawdown"],
                        total_trades   = vm["n_trades"],
                        passed_filter  = True,
                    )
                except Exception: pass
        except Exception as e:
            logger.error(f"Failed {symbol}: {e}")
            all_results[symbol] = []
    return all_results


if __name__ == "__main__":
    import time
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    conn = get_db_connection()

    for sym in ["ETH/USDT:USDT", "SOL/USDT:USDT", "BTC/USDT:USDT"]:
        t0 = time.time()
        results = run_backtest_for_token(conn, sym)
        elapsed = time.time() - t0
        print(f"\n{sym}: {len(results)} valid strategies in {elapsed:.1f}s")
        if results:
            b  = results[0]; vm = b["val_metrics"]
            print(f"  Best: {b['strategy_type']} | {b['timeframe']} | {b['tier']}")
            print(f"  WR:{vm['win_rate']:.1%} Exp:{vm['expectancy']:.4f} PF:{vm['profit_factor']:.2f} Trades:{vm['n_trades']}")

    conn.close()

# __APEX_LOGGER_V1__
