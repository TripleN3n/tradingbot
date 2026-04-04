# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/data_feed.py — Live Data Feed
# Version 3.1 — Fetches all 3 timeframes per token + 1D BTC trend
# =============================================================================

import ccxt
import requests
import pandas as pd
import numpy as np
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from bot.config import (
    EXCHANGE, PAPER_TRADING, FILTERS, LOGS,
    EMA, RSI, MACD, BOLLINGER, VOLUME, SL,
    VWAP_TIMEFRAMES, TRAILING_SL, TP, MTF_CONFIG,
)

logger = logging.getLogger(__name__)

MAX_RETRIES        = 3
RETRY_WAIT         = 3
OHLCV_LIMIT        = 300
FEAR_GREED_URL     = "https://api.alternative.me/fng/?limit=1"
FEAR_GREED_TIMEOUT = 10

_exchange_instance = None

def get_exchange() -> ccxt.binanceusdm:
    global _exchange_instance
    if _exchange_instance is None:
        config = EXCHANGE["paper"] if PAPER_TRADING else EXCHANGE["live"]
        _exchange_instance = ccxt.binanceusdm({
            "apiKey":          config["api_key"],
            "secret":          config["api_secret"],
            "enableRateLimit": True,
            "options":         {"defaultType": "future"},
        })
        if PAPER_TRADING and "urls" in config:
            _exchange_instance.urls["api"] = config["urls"]["api"]
        logger.info(f"Exchange connected — {'PAPER' if PAPER_TRADING else 'LIVE'} mode")
    return _exchange_instance


def _calculate_indicators(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Calculate all indicators. Must stay in sync with backtest_engine.py."""
    df = df.copy()

    df["ema_fast"]  = df["close"].ewm(span=EMA["fast"],  adjust=False).mean()
    df["ema_slow"]  = df["close"].ewm(span=EMA["slow"],  adjust=False).mean()
    df["ema_macro"] = df["close"].ewm(span=EMA["macro"], adjust=False).mean()

    if timeframe in VWAP_TIMEFRAMES:
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_vol"]        = df["typical_price"] * df["volume"]
        df["vwap"]          = df["tp_vol"].rolling(window=24).sum() / df["volume"].rolling(window=24).sum()
        df["macro_ref"]     = df["vwap"]
    else:
        df["macro_ref"] = df["ema_macro"]

    delta  = df["close"].diff()
    gain   = delta.clip(lower=0)
    loss   = -delta.clip(upper=0)
    avg_g  = gain.ewm(com=RSI["period"] - 1, adjust=False).mean()
    avg_l  = loss.ewm(com=RSI["period"] - 1, adjust=False).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    ema_fast_m        = df["close"].ewm(span=MACD["fast"],   adjust=False).mean()
    ema_slow_m        = df["close"].ewm(span=MACD["slow"],   adjust=False).mean()
    df["macd"]        = ema_fast_m - ema_slow_m
    df["macd_signal"] = df["macd"].ewm(span=MACD["signal"], adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    df["bb_mid"]   = df["close"].rolling(BOLLINGER["period"]).mean()
    bb_std         = df["close"].rolling(BOLLINGER["period"]).std()
    df["bb_upper"] = df["bb_mid"] + (BOLLINGER["std_dev"] * bb_std)
    df["bb_lower"] = df["bb_mid"] - (BOLLINGER["std_dev"] * bb_std)
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    df["volume_ma"]    = df["volume"].rolling(VOLUME["period"]).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma"].replace(0, np.nan)

    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"]  = true_range.ewm(span=SL["atr_period"], adjust=False).mean()

    return df


def fetch_ohlcv(symbol: str, timeframe: str, limit: int = OHLCV_LIMIT) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV for a symbol/timeframe.
    Returns only CLOSED candles (drops last forming candle).
    """
    exchange = get_exchange()
    retries  = 0

    while retries < MAX_RETRIES:
        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit + 1)
            if not raw or len(raw) < 2:
                return None

            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("timestamp", inplace=True)
            df = df.iloc[:-1]  # Drop last forming candle

            if df.empty:
                return None

            df = _calculate_indicators(df, timeframe)
            return df

        except ccxt.RateLimitExceeded:
            time.sleep(RETRY_WAIT)
            retries += 1
        except ccxt.NetworkError as e:
            logger.warning(f"Network error {symbol} {timeframe}: {e}")
            time.sleep(RETRY_WAIT)
            retries += 1
        except Exception as e:
            logger.error(f"OHLCV fetch error {symbol} {timeframe}: {e}")
            return None

    return None


def fetch_all_ohlcv_mtf(strategies: list) -> dict:
    """
    Fetch all 3 timeframes (1H, 4H, 1D) for all active tokens.

    Returns dict with keys:
    - "{symbol}_1h" → 1H DataFrame
    - "{symbol}_4h" → 4H DataFrame
    - "{symbol}_1d" → 1D DataFrame

    Also keeps "{symbol}" as alias for 1H for backward compatibility.
    """
    results    = {}
    timeframes = ["1h", "4h", "1d"]
    tf_limits  = {"1h": 300, "4h": 150, "1d": 60}

    symbols = list({s["symbol"] for s in strategies})

    for symbol in symbols:
        for tf in timeframes:
            limit = tf_limits.get(tf, 100)
            df    = fetch_ohlcv(symbol, tf, limit)

            if df is not None and not df.empty:
                results[f"{symbol}_{tf}"] = df
                if tf == "1h":
                    results[symbol] = df
            else:
                logger.warning(f"Failed to fetch {symbol} {tf}")

    logger.debug(f"Fetched MTF data for {len(symbols)} tokens × 3 timeframes")
    return results


# =============================================================================
# BTC TREND — All 3 timeframes returned
# =============================================================================

def fetch_btc_trend() -> dict:
    """
    Fetch BTC trend for all 3 timeframes (1H, 4H, 1D).

    Returns direction per timeframe so the BTC filter can match
    the token's entry timeframe exactly:
    - 1H token → check BTC 1H direction
    - 4H token → check BTC 4H direction
    - 1D token → check BTC 1D direction

    Overall 'direction' key is the majority consensus across timeframes.
    """
    if not BTC_FILTER_ENABLED():
        return {"direction": "neutral", "1h": "neutral", "4h": "neutral", "1d": "neutral"}

    symbol = "BTC/USDT:USDT"

    df_1h = fetch_ohlcv(symbol, "1h", 100)
    df_4h = fetch_ohlcv(symbol, "4h",  60)
    df_1d = fetch_ohlcv(symbol, "1d",  30)

    def get_dir(df: Optional[pd.DataFrame]) -> str:
        if df is None or df.empty:
            return "neutral"
        latest = df.iloc[-1]
        ef = latest.get("ema_fast")
        es = latest.get("ema_slow")
        mr = latest.get("macro_ref")
        if pd.isna(ef) or pd.isna(es) or pd.isna(mr):
            return "neutral"
        if ef > es and latest["close"] > mr:
            return "bullish"
        if ef < es and latest["close"] < mr:
            return "bearish"
        return "neutral"

    dir_1h = get_dir(df_1h)
    dir_4h = get_dir(df_4h)
    dir_1d = get_dir(df_1d)

    # Overall consensus — majority vote across non-neutral timeframes
    directions = [d for d in [dir_1h, dir_4h, dir_1d] if d != "neutral"]
    if not directions:
        direction = "neutral"
    elif directions.count("bullish") > directions.count("bearish"):
        direction = "bullish"
    elif directions.count("bearish") > directions.count("bullish"):
        direction = "bearish"
    else:
        # Tie — use 1D as the deciding vote
        direction = dir_1d if dir_1d != "neutral" else "neutral"

    logger.debug(
        f"BTC trend — 1H:{dir_1h} 4H:{dir_4h} 1D:{dir_1d} → overall:{direction}"
    )

    return {
        "direction": direction,
        "1h":        dir_1h,
        "4h":        dir_4h,
        "1d":        dir_1d,
    }


def BTC_FILTER_ENABLED():
    try:
        from bot.config import BTC_FILTER
        return BTC_FILTER.get("enabled", True)
    except Exception:
        return True


# =============================================================================
# FUNDING RATES
# =============================================================================

def fetch_funding_rates(symbols: list) -> dict:
    if not FILTERS["funding_rate"]["enabled"]:
        return {s: 0.0 for s in symbols}

    exchange = get_exchange()
    try:
        funding_data = exchange.fetch_funding_rates(symbols)
        return {
            symbol: float(info.get("fundingRate", 0.0) or 0.0)
            for symbol, info in funding_data.items()
        }
    except Exception as e:
        logger.warning(f"Funding rates fetch failed: {e}")
        return {s: 0.0 for s in symbols}


def fetch_funding_rate(symbol: str) -> float:
    return fetch_funding_rates([symbol]).get(symbol, 0.0)


# =============================================================================
# FEAR & GREED INDEX
# =============================================================================

_fear_greed_cache = {"value": 50, "label": "Neutral", "fetched_at": None}


def fetch_fear_greed() -> dict:
    if not FILTERS["fear_greed"]["enabled"]:
        return {"value": 50, "label": "Neutral"}

    now = datetime.now(timezone.utc)
    if (
        _fear_greed_cache["fetched_at"] is not None
        and (now - _fear_greed_cache["fetched_at"]).seconds < 3600
    ):
        return {"value": _fear_greed_cache["value"], "label": _fear_greed_cache["label"]}

    retries = 0
    while retries < MAX_RETRIES:
        try:
            response = requests.get(FILTERS["fear_greed"]["api_url"], timeout=FEAR_GREED_TIMEOUT)
            response.raise_for_status()
            entry = response.json()["data"][0]
            value = int(entry["value"])
            label = entry["value_classification"]
            _fear_greed_cache.update({"value": value, "label": label, "fetched_at": now})
            return {"value": value, "label": label}
        except Exception as e:
            retries += 1
            logger.warning(f"Fear & Greed fetch failed ({retries}): {e}")
            time.sleep(RETRY_WAIT)

    return {"value": _fear_greed_cache["value"], "label": _fear_greed_cache["label"]}


# =============================================================================
# PRICE FETCHING
# =============================================================================

def fetch_current_price(symbol: str) -> Optional[float]:
    exchange = get_exchange()
    for _ in range(MAX_RETRIES):
        try:
            ticker = exchange.fetch_ticker(symbol)
            return float(ticker["last"])
        except Exception:
            time.sleep(RETRY_WAIT)
    return None


def fetch_current_prices(symbols: list) -> dict:
    exchange = get_exchange()
    try:
        tickers = exchange.fetch_tickers(symbols)
        return {s: float(t["last"]) for s, t in tickers.items() if t.get("last")}
    except Exception as e:
        logger.error(f"Bulk price fetch failed: {e}")
        prices = {}
        for symbol in symbols:
            p = fetch_current_price(symbol)
            if p: prices[symbol] = p
        return prices


# =============================================================================
# FULL CYCLE DATA FETCH
# =============================================================================

def fetch_cycle_data(strategies: list) -> dict:
    """
    Fetch all data needed for one complete bot cycle.
    Fetches all 3 timeframes per token + all 3 BTC timeframes.

    Returns dict with:
    - ohlcv:         symbol_tf -> DataFrame (all 3 timeframes per token)
    - btc_trend:     dict with direction for each timeframe (1h, 4h, 1d)
    - funding_rates: symbol -> rate
    - fear_greed:    dict with value
    - fetched_at:    timestamp
    """
    logger.debug("Fetching MTF cycle data...")
    start   = time.time()
    symbols = [s["symbol"] for s in strategies]

    ohlcv_data    = fetch_all_ohlcv_mtf(strategies)
    btc_trend     = fetch_btc_trend()
    funding_rates = fetch_funding_rates(symbols)
    fear_greed    = fetch_fear_greed()

    elapsed        = time.time() - start
    tokens_fetched = len({k.split("_")[0] for k in ohlcv_data.keys()})

    logger.info(
        f"MTF cycle data fetched in {elapsed:.1f}s | "
        f"Tokens: {tokens_fetched}/{len(strategies)} | "
        f"BTC: {btc_trend['direction']} "
        f"(1H:{btc_trend.get('1h','?')} 4H:{btc_trend.get('4h','?')} 1D:{btc_trend.get('1d','?')}) | "
        f"F&G: {fear_greed['value']} ({fear_greed['label']})"
    )

    return {
        "ohlcv":         ohlcv_data,
        "btc_trend":     btc_trend,
        "funding_rates": funding_rates,
        "fear_greed":    fear_greed,
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# ENTRY POINT — Test
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    test_strategies = [
        {"symbol": "BTC/USDT:USDT"},
        {"symbol": "ETH/USDT:USDT"},
    ]

    data = fetch_cycle_data(test_strategies)

    print(f"\nBTC Trend: {data['btc_trend']}")
    print(f"Fear & Greed: {data['fear_greed']}")

    for symbol in ["BTC/USDT:USDT", "ETH/USDT:USDT"]:
        for tf in ["1h", "4h", "1d"]:
            key = f"{symbol}_{tf}"
            df  = data["ohlcv"].get(key)
            if df is not None:
                print(f"\n{symbol} {tf} — {len(df)} candles | Last close: {df['close'].iloc[-1]:.4f}")

# __APEX_LOGGER_V1__
