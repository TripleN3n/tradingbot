# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# apex/data_fetcher.py — Historical OHLCV Data Fetcher
# =============================================================================
# RESPONSIBILITY:
# Fetches and stores historical OHLCV candlestick data for all tokens
# across all configured timeframes from Binance Futures.
# Uses CoinGecko for true market cap ranking.
# Uses Binance Futures for volume filtering and OHLCV data.
#
# WHAT THIS FILE DOES:
# - Fetches top 100 tokens by TRUE market cap from CoinGecko (free API)
# - Cross-references with Binance Futures available symbols
# - Applies $10M minimum daily volume filter
# - Downloads 1 year of OHLCV data per token per timeframe
# - Stores data in SQLite for backtest engine to consume
# - Handles rate limiting, retries, and partial failures gracefully
#
# WHAT THIS FILE DOES NOT DO:
# - Does not run backtests (that's backtest_engine.py)
# - Does not assign strategies (that's strategy_assigner.py)
# - Does not fetch live data (that's bot/data_feed.py)
# =============================================================================

import ccxt
import sqlite3
import pandas as pd
import requests
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

from bot.config import (
    EXCHANGE, PAPER_TRADING, TIMEFRAMES, BACKTEST,
    EXCLUDED_TOKENS, FILTERS, DB, LOGS,
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
# CONSTANTS
# =============================================================================

CANDLES_PER_REQUEST = 1000
RATE_LIMIT_SLEEP    = 0.3
MAX_RETRIES         = 3
RETRY_WAIT          = 5

TIMEFRAME_MS = {
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}

# CoinGecko free API — no key required
COINGECKO_TOP_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc&per_page=250&page={page}"
    "&sparkline=false"
)
COINGECKO_RATE_LIMIT_SLEEP = 1.5  # Free tier: max 30 req/min


# =============================================================================
# EXCHANGE CONNECTION
# =============================================================================

def get_exchange() -> ccxt.binanceusdm:
    """
    Create and return authenticated Binance Futures exchange connection.
    Uses demo API for paper trading, live API for real trading.
    """
    config = EXCHANGE["paper"] if PAPER_TRADING else EXCHANGE["live"]

    exchange = ccxt.binanceusdm({
        "apiKey":          config["api_key"],
        "secret":          config["api_secret"],
        "enableRateLimit": True,
        "options":         {"defaultType": "future"},
    })

    if PAPER_TRADING and "urls" in config:
        exchange.urls["api"] = config["urls"]["api"]

    logger.info(f"Exchange connected — {'PAPER' if PAPER_TRADING else 'LIVE'} mode")
    return exchange


# =============================================================================
# DATABASE SETUP
# =============================================================================

def get_db_connection() -> sqlite3.Connection:
    """Return a connection to the APEX database."""
    Path(DB["apex"]).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB["apex"], check_same_thread=False)


def init_database(conn: sqlite3.Connection):
    """
    Create all database tables if they don't exist.
    Safe to call multiple times.
    """
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            timeframe   TEXT NOT NULL,
            timestamp   INTEGER NOT NULL,
            open        REAL NOT NULL,
            high        REAL NOT NULL,
            low         REAL NOT NULL,
            close       REAL NOT NULL,
            volume      REAL NOT NULL,
            UNIQUE(symbol, timeframe, timestamp)
        )
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf
        ON ohlcv (symbol, timeframe)
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS universe (
            symbol              TEXT PRIMARY KEY,
            base_asset          TEXT NOT NULL,
            coingecko_id        TEXT,
            market_cap_rank     INTEGER,
            market_cap_usd      REAL,
            daily_volume_usd    REAL,
            added_at            TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            is_active           INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            symbol      TEXT NOT NULL,
            timeframe   TEXT NOT NULL,
            fetched_at  TEXT NOT NULL,
            candles     INTEGER NOT NULL,
            status      TEXT NOT NULL,
            PRIMARY KEY (symbol, timeframe)
        )
    """)

    conn.commit()
    logger.info("Database initialized")


# =============================================================================
# COINGECKO — TRUE MARKET CAP RANKING
# =============================================================================

def get_coingecko_top_tokens(limit: int = 250) -> list:
    """
    Fetch top tokens by true market cap from CoinGecko free API.
    Returns list of dicts: coingecko_id, symbol, market_cap_rank, market_cap_usd.
    Fetches 250 to provide buffer after stablecoin/availability filtering.
    """
    logger.info("Fetching market cap rankings from CoinGecko...")
    all_tokens = []

    retries = 0
    while retries < MAX_RETRIES:
        try:
            url = COINGECKO_TOP_URL.format(page=1)
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            for item in data:
                symbol = item.get("symbol", "").upper()
                if symbol not in EXCLUDED_TOKENS:
                    all_tokens.append({
                        "coingecko_id":    item.get("id"),
                        "symbol":          symbol,
                        "market_cap_rank": item.get("market_cap_rank"),
                        "market_cap_usd":  item.get("market_cap", 0) or 0,
                    })
            break

        except requests.exceptions.RequestException as e:
            retries += 1
            logger.warning(f"CoinGecko fetch error (attempt {retries}): {e}")
            time.sleep(RETRY_WAIT)

    if not all_tokens:
        logger.error("CoinGecko fetch failed — cannot determine market cap ranking")

    logger.info(f"CoinGecko returned {len(all_tokens)} non-stable tokens")
    return all_tokens


# =============================================================================
# BINANCE FUTURES — AVAILABLE SYMBOLS & VOLUME
# =============================================================================

def get_binance_futures_symbols(exchange: ccxt.binanceusdm) -> dict:
    """
    Fetch all active USDT perpetual futures from Binance.
    Returns dict: base_asset -> full_symbol
    e.g. {"BTC": "BTC/USDT:USDT", "ETH": "ETH/USDT:USDT"}
    """
    logger.info("Fetching Binance Futures available markets...")
    markets = exchange.load_markets()

    available = {}
    for symbol, market in markets.items():
        if (
            market.get("quote") == "USDT"
            and market.get("type") == "swap"
            and market.get("active", False)
            and market.get("linear", False)
        ):
            base = market.get("base", "")
            if base not in EXCLUDED_TOKENS:
                available[base] = symbol

    logger.info(f"Binance Futures: {len(available)} active USDT perpetuals")
    return available


def get_binance_volumes(exchange: ccxt.binanceusdm, symbols: list) -> dict:
    """
    Fetch 24h quote volume in USD for a list of Binance Futures symbols.
    Returns dict: symbol -> 24h volume USD.
    """
    logger.info(f"Fetching 24h volumes for {len(symbols)} symbols...")
    tickers = exchange.fetch_tickers(symbols)
    return {
        symbol: (ticker.get("quoteVolume", 0) or 0)
        for symbol, ticker in tickers.items()
    }


# =============================================================================
# TOKEN UNIVERSE — COMBINED LOGIC
# =============================================================================

def get_top_100_futures_symbols(exchange: ccxt.binanceusdm) -> list:
    """
    Get top 100 tokens by TRUE market cap (CoinGecko) that are:
    1. Available on Binance Futures as USDT perpetuals
    2. Not stablecoins or wrapped tokens
    3. Have at least $10M daily volume on Binance Futures

    Returns list of dicts with full token details.
    """

    # Step 1 — True market cap ranking from CoinGecko
    cg_tokens = get_coingecko_top_tokens(limit=250)
    if not cg_tokens:
        raise RuntimeError("CoinGecko data unavailable — cannot build universe")

    # Step 2 — Available Binance Futures symbols
    binance_available = get_binance_futures_symbols(exchange)

    # Step 3 — Cross-reference
    matched = []
    for token in cg_tokens:
        cg_symbol = token["symbol"]
        if cg_symbol in binance_available:
            matched.append({
                **token,
                "futures_symbol": binance_available[cg_symbol],
            })

    logger.info(f"Matched {len(matched)} CoinGecko tokens to Binance Futures")

    # Step 4 — Fetch 24h volumes
    futures_symbols = [t["futures_symbol"] for t in matched]
    volumes = get_binance_volumes(exchange, futures_symbols)

    # Step 5 — Apply minimum volume filter
    min_volume = FILTERS["liquidity"]["min_daily_volume_usd"]
    qualified = []

    for token in matched:
        volume = volumes.get(token["futures_symbol"], 0)
        if volume >= min_volume:
            qualified.append({
                "symbol":           token["futures_symbol"],
                "base_asset":       token["symbol"],
                "coingecko_id":     token["coingecko_id"],
                "market_cap_rank":  token["market_cap_rank"],
                "market_cap_usd":   token["market_cap_usd"],
                "daily_volume_usd": volume,
            })

    # Step 6 — Sort by true market cap rank, take top 100
    qualified.sort(key=lambda x: x["market_cap_rank"] or 9999)
    top_100 = qualified[:100]

    logger.info(
        f"Final universe: {len(top_100)} tokens "
        f"(excluded {len(matched) - len(qualified)} below ${min_volume:,.0f} volume)"
    )
    return top_100


# =============================================================================
# UNIVERSE DATABASE MANAGEMENT
# =============================================================================

def update_universe(conn: sqlite3.Connection, tokens: list):
    """
    Update universe table. Mark dropped tokens inactive.
    Returns (added_symbols, dropped_symbols).
    """
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    c.execute("SELECT symbol FROM universe WHERE is_active = 1")
    current_symbols = {row[0] for row in c.fetchall()}
    new_symbols = {t["symbol"] for t in tokens}

    dropped = list(current_symbols - new_symbols)
    for symbol in dropped:
        c.execute(
            "UPDATE universe SET is_active = 0, updated_at = ? WHERE symbol = ?",
            (now, symbol)
        )
        logger.info(f"Removed from universe: {symbol}")

    for token in tokens:
        c.execute("""
            INSERT INTO universe
                (symbol, base_asset, coingecko_id, market_cap_rank,
                 market_cap_usd, daily_volume_usd, added_at, updated_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(symbol) DO UPDATE SET
                coingecko_id     = excluded.coingecko_id,
                market_cap_rank  = excluded.market_cap_rank,
                market_cap_usd   = excluded.market_cap_usd,
                daily_volume_usd = excluded.daily_volume_usd,
                updated_at       = excluded.updated_at,
                is_active        = 1
        """, (
            token["symbol"],        token["base_asset"],
            token.get("coingecko_id"),
            token["market_cap_rank"],
            token.get("market_cap_usd", 0),
            token.get("daily_volume_usd", 0),
            now, now,
        ))

    conn.commit()
    added = list(new_symbols - current_symbols)
    logger.info(
        f"Universe updated — {len(new_symbols)} active, "
        f"{len(added)} added, {len(dropped)} removed"
    )
    return added, dropped


def get_active_symbols(conn: sqlite3.Connection) -> list:
    """Return active symbols ordered by market cap rank."""
    c = conn.cursor()
    c.execute("""
        SELECT symbol FROM universe
        WHERE is_active = 1
        ORDER BY market_cap_rank ASC
    """)
    return [row[0] for row in c.fetchall()]


# =============================================================================
# OHLCV FETCH & STORE
# =============================================================================

def fetch_ohlcv_since(
    exchange: ccxt.binanceusdm,
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
) -> list:
    """
    Fetch all OHLCV candles between since_ms and until_ms.
    Handles Binance pagination automatically.
    """
    all_candles = []
    current_since = since_ms
    tf_ms = TIMEFRAME_MS[timeframe]

    while current_since < until_ms:
        retries = 0
        candles = None

        while retries < MAX_RETRIES:
            try:
                candles = exchange.fetch_ohlcv(
                    symbol, timeframe=timeframe,
                    since=current_since, limit=CANDLES_PER_REQUEST,
                )
                break
            except ccxt.RateLimitExceeded:
                time.sleep(RETRY_WAIT)
                retries += 1
            except ccxt.NetworkError as e:
                logger.warning(f"Network error {symbol} {timeframe}: {e}")
                time.sleep(RETRY_WAIT)
                retries += 1
            except Exception as e:
                logger.error(f"Error {symbol} {timeframe}: {e}")
                retries += 1

        if not candles:
            break

        candles = [c for c in candles if c[0] < until_ms]
        if not candles:
            break

        all_candles.extend(candles)
        current_since = candles[-1][0] + tf_ms
        time.sleep(RATE_LIMIT_SLEEP)

        if len(candles) < CANDLES_PER_REQUEST:
            break

    return all_candles


def store_ohlcv(conn: sqlite3.Connection, symbol: str, timeframe: str, candles: list) -> int:
    """Store candles in DB. Returns count of new candles inserted."""
    if not candles:
        return 0

    c = conn.cursor()
    inserted = 0

    for candle in candles:
        try:
            c.execute("""
                INSERT OR IGNORE INTO ohlcv
                (symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, timeframe, candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]))
            inserted += c.rowcount
        except Exception as e:
            logger.error(f"Store error {symbol} {timeframe}: {e}")

    conn.commit()
    return inserted


def update_fetch_log(conn, symbol, timeframe, candles, status):
    """Record fetch result in log table."""
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("""
        INSERT INTO fetch_log (symbol, timeframe, fetched_at, candles, status)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(symbol, timeframe) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            candles    = excluded.candles,
            status     = excluded.status
    """, (symbol, timeframe, now, candles, status))
    conn.commit()


# =============================================================================
# MAIN FETCH FUNCTIONS
# =============================================================================

def fetch_token_data(exchange, conn, symbol, timeframes=None, lookback_years=None):
    """Fetch 1 year of OHLCV data for a single token across all timeframes."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    if lookback_years is None:
        lookback_years = BACKTEST["lookback_years"]

    now_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=365 * lookback_years)).timestamp() * 1000)

    for timeframe in timeframes:
        logger.info(f"  {symbol} {timeframe}...")
        try:
            candles = fetch_ohlcv_since(exchange, symbol, timeframe, since_ms, now_ms)
            if candles:
                stored = store_ohlcv(conn, symbol, timeframe, candles)
                update_fetch_log(conn, symbol, timeframe, stored, "success")
                logger.info(f"    → {stored} candles stored")
            else:
                update_fetch_log(conn, symbol, timeframe, 0, "empty")
                logger.warning(f"    → no candles returned")
        except Exception as e:
            update_fetch_log(conn, symbol, timeframe, 0, f"error: {str(e)}")
            logger.error(f"    → failed: {e}")


def fetch_all_tokens(exchange, conn, symbols=None, lookback_years=None):
    """Fetch data for all active tokens. Used during monthly rebalance."""
    if symbols is None:
        symbols = get_active_symbols(conn)
    if lookback_years is None:
        lookback_years = BACKTEST["lookback_years"]

    total = len(symbols)
    logger.info(f"Full data fetch — {total} tokens × {len(TIMEFRAMES)} timeframes")

    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{total}] {symbol}")
        fetch_token_data(exchange, conn, symbol, TIMEFRAMES, lookback_years)

    logger.info("Full data fetch complete")


# =============================================================================
# DATA RETRIEVAL — Used by backtest engine
# =============================================================================

def load_ohlcv(conn: sqlite3.Connection, symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Load OHLCV data into a DataFrame.
    Returns DataFrame with UTC DatetimeIndex.
    Columns: open, high, low, close, volume.
    """
    df = pd.read_sql_query("""
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv WHERE symbol = ? AND timeframe = ?
        ORDER BY timestamp ASC
    """, conn, params=(symbol, timeframe))

    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df


def get_data_summary(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return data availability summary per symbol/timeframe."""
    return pd.read_sql_query("""
        SELECT
            symbol, timeframe, COUNT(*) as candles,
            datetime(MIN(timestamp)/1000, 'unixepoch') as from_date,
            datetime(MAX(timestamp)/1000, 'unixepoch') as to_date
        FROM ohlcv
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
    """, conn)


# =============================================================================
# ENTRY POINT
# =============================================================================

def run_initial_fetch():
    """Run full initial data fetch. Call once during first-time setup."""
    logger.info("=" * 60)
    logger.info("APEX Data Fetcher — Initial Full Fetch")
    logger.info("=" * 60)

    exchange = get_exchange()
    conn = get_db_connection()
    init_database(conn)

    tokens = get_top_100_futures_symbols(exchange)
    update_universe(conn, tokens)

    symbols = [t["symbol"] for t in tokens]
    fetch_all_tokens(exchange, conn, symbols)

    summary = get_data_summary(conn)
    logger.info(f"\nData Summary:\n{summary.to_string()}")

    conn.close()
    logger.info("Initial fetch complete.")


if __name__ == "__main__":
    run_initial_fetch()
