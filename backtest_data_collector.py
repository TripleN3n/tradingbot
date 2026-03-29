import ccxt
import pandas as pd
import sqlite3
import time
import requests
from datetime import datetime, timezone

DB_PATH = "backtest_data.db"
TIMEFRAME = "1h"
YEARS_BACK = 5
STABLE_COINS = ["USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USDS"]

DEMO_URLS = {
    'fapiPublic': 'https://demo-fapi.binance.com/fapi/v1',
    'fapiPrivate': 'https://demo-fapi.binance.com/fapi/v1',
    'fapiPublicV2': 'https://demo-fapi.binance.com/fapi/v2',
    'fapiPrivateV2': 'https://demo-fapi.binance.com/fapi/v2',
}

def get_exchange():
    exchange = ccxt.binanceusdm({
        'options': {'defaultType': 'future'},
        'urls': {'api': DEMO_URLS},
    })
    exchange.load_markets()
    return exchange

def get_top_100_symbols():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            'vs_currency': 'usd',
            'order': 'market_cap_desc',
            'per_page': 150,
            'page': 1,
            'sparkline': False
        }
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        symbols = []
        for coin in data:
            symbol = coin['symbol'].upper()
            if symbol in STABLE_COINS:
                continue
            symbols.append(f"{symbol}/USDT:USDT")
            if len(symbols) >= 100:
                break
        return symbols
    except Exception as e:
        print(f"Error fetching top 100: {e}")
        return []

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol TEXT,
            timestamp INTEGER,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (symbol, timestamp)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS data_status (
            symbol TEXT PRIMARY KEY,
            candles INTEGER,
            start_date TEXT,
            end_date TEXT,
            years_available REAL,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

def fetch_full_history(exchange, symbol, years=5):
    since_ms = int((datetime.now(timezone.utc).timestamp() - (years * 365 * 24 * 3600)) * 1000)
    all_candles = []
    current_since = since_ms
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, since=current_since, limit=1000)
            if not candles:
                break
            all_candles.extend(candles)
            if len(candles) < 1000:
                break
            current_since = candles[-1][0] + 1
            time.sleep(0.1)
        except Exception as e:
            print(f"  Error fetching {symbol}: {e}")
            break
    return all_candles

def save_to_db(conn, symbol, candles):
    c = conn.cursor()
    c.executemany('''
        INSERT OR REPLACE INTO ohlcv (symbol, timestamp, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', [(symbol, c[0], c[1], c[2], c[3], c[4], c[5]) for c in candles])
    conn.commit()

def main():
    print("=" * 60)
    print("  BACKTESTING DATA COLLECTOR")
    print("  Fetching 5 years of 1H data for top 100 tokens")
    print("=" * 60)

    init_db()
    exchange = get_exchange()
    print("Connected to Binance")

    top_100 = get_top_100_symbols()
    available = [s for s in top_100 if s in exchange.markets]
    print(f"{len(available)} symbols available on Binance Futures\n")

    conn = sqlite3.connect(DB_PATH)
    results = []

    for i, symbol in enumerate(available):
        print(f"[{i+1}/{len(available)}] Fetching {symbol}...", end=" ", flush=True)
        candles = fetch_full_history(exchange, symbol, years=YEARS_BACK)

        if len(candles) < 500:
            print(f"SKIP - only {len(candles)} candles")
            results.append((symbol, 0, None, None, 0, 'insufficient_data'))
            continue

        save_to_db(conn, symbol, candles)

        start_dt = datetime.fromtimestamp(candles[0][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d')
        end_dt = datetime.fromtimestamp(candles[-1][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d')
        years = len(candles) / (365 * 24)

        print(f"OK - {len(candles):,} candles | {start_dt} to {end_dt} ({years:.1f} years)")
        results.append((symbol, len(candles), start_dt, end_dt, round(years, 1), 'ok'))
        time.sleep(0.3)

    c = conn.cursor()
    c.executemany('''
        INSERT OR REPLACE INTO data_status (symbol, candles, start_date, end_date, years_available, status)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', results)
    conn.commit()
    conn.close()

    ok_count = len([r for r in results if r[5] == 'ok'])
    print(f"\nData collection complete. {ok_count} symbols ready for backtesting.")
    print(f"Database saved to: {DB_PATH}")

if __name__ == "__main__":
    main()