import ccxt
import pandas as pd
import requests
import time
import logging
from config import (API_KEY, SECRET_KEY, TESTNET, TOP_N_COINS,
                    STABLE_COINS, BLACKLIST, TIMEFRAME, LOG_PATH)

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def get_exchange():
    exchange = ccxt.binanceusdm({
        'apiKey': API_KEY,
        'secret': SECRET_KEY,
        'options': {
            'defaultType': 'future',
        },
        'urls': {
            'api': {
                'fapiPublic': 'https://demo-fapi.binance.com/fapi/v1',
                'fapiPrivate': 'https://demo-fapi.binance.com/fapi/v1',
                'fapiPublicV2': 'https://demo-fapi.binance.com/fapi/v2',
                'fapiPrivateV2': 'https://demo-fapi.binance.com/fapi/v2',
            }
        },
    })
    exchange.load_markets()
    return exchange

def get_top_100_symbols():
    """Fetch top 100 tokens by market cap from CoinGecko, return as Binance USDT futures symbols."""
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            'vs_currency': 'usd',
            'order': 'market_cap_desc',
            'per_page': 150,
            'page': 1,
            'sparkline': False
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        symbols = []
        for coin in data:
            symbol = coin['symbol'].upper()
            if symbol in STABLE_COINS:
                continue
            if symbol in BLACKLIST:
                continue
            binance_symbol = f"{symbol}/USDT:USDT"
            symbols.append(binance_symbol)
            if len(symbols) >= TOP_N_COINS:
                break

        logging.info(f"Fetched {len(symbols)} symbols from CoinGecko")
        return symbols

    except Exception as e:
        logging.error(f"Error fetching top 100: {e}")
        return []


def get_available_futures_symbols(exchange, target_symbols):
    """Filter target symbols to only those available on Binance Futures."""
    available = set(exchange.markets.keys())
    filtered = [s for s in target_symbols if s in available]
    logging.info(f"{len(filtered)} symbols available on Binance Futures")
    return filtered


def fetch_ohlcv(exchange, symbol, timeframe=TIMEFRAME, limit=200):
    """Fetch OHLCV candle data for a symbol."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df = df.astype(float)
        return df
    except Exception as e:
        logging.warning(f"Failed to fetch OHLCV for {symbol}: {e}")
        return None


def fetch_all_ohlcv(exchange, symbols, timeframe=TIMEFRAME, limit=200, delay=0.2):
    """Fetch OHLCV for all symbols with rate limit delay."""
    data = {}
    for symbol in symbols:
        df = fetch_ohlcv(exchange, symbol, timeframe, limit)
        if df is not None and len(df) >= 50:
            data[symbol] = df
        time.sleep(delay)
    logging.info(f"Fetched OHLCV data for {len(data)} symbols")
    return data


def get_current_price(exchange, symbol):
    """Get latest price for a symbol."""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return ticker['last']
    except Exception as e:
        logging.warning(f"Failed to fetch price for {symbol}: {e}")
        return None