import time
import sqlite3
import logging
import schedule
from datetime import datetime, timezone
from config import (TIMEFRAME, MAX_OPEN_TRADES, DB_PATH, LOG_PATH)
from data_feed import (get_exchange, get_top_100_symbols,
                       get_available_futures_symbols, fetch_all_ohlcv,
                       get_current_price)
from strategy import (generate_signal, calculate_momentum_score,
                      rank_signals, detect_market_regime, add_indicators)
from paper_trader import (init_db, get_open_trades, open_trade,
                          check_exits, save_portfolio_snapshot,
                          get_performance_stats)

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def run_bot():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Bot cycle starting...")

    try:
        # --- STEP 1: Connect to exchange ---
        exchange = get_exchange()
        print("✓ Connected to Binance Futures Testnet")

        # --- STEP 2: Get tradeable universe ---
        top_100 = get_top_100_symbols()
        symbols = get_available_futures_symbols(exchange, top_100)
        print(f"✓ Universe: {len(symbols)} symbols")

        if not symbols:
            print("✗ No symbols found. Skipping cycle.")
            return

        # --- STEP 3: Fetch OHLCV data ---
        print("Fetching market data...")
        ohlcv_data = fetch_all_ohlcv(exchange, symbols, timeframe=TIMEFRAME, limit=200)
        print(f"✓ Data fetched for {len(ohlcv_data)} symbols")

        # --- STEP 4: Detect market regime using BTC ---
        btc_symbol = "BTC/USDT:USDT"
        btc_df = ohlcv_data.get(btc_symbol)
        regime = detect_market_regime(btc_df)
        print(f"✓ Market regime: {regime.upper()}")

        # --- STEP 5: Get current prices ---
        current_prices = {}
        for symbol in ohlcv_data:
            price = get_current_price(exchange, symbol)
            if price:
                current_prices[symbol] = price

        # --- STEP 6: Check exits on open trades ---
        conn = sqlite3.connect(DB_PATH)
        open_trades = get_open_trades(conn)
        print(f"✓ Open trades: {len(open_trades)}")

        ohlcv_with_indicators = {}
        for symbol, df in ohlcv_data.items():
            ohlcv_with_indicators[symbol] = add_indicators(df)

        check_exits(conn, open_trades, current_prices, ohlcv_with_indicators)

        # --- STEP 7: Generate signals ---
        open_trades = get_open_trades(conn)  # refresh after exits

        if len(open_trades) < MAX_OPEN_TRADES:
            signals_found = []

            for symbol, df in ohlcv_data.items():
                signal = generate_signal(df)
                if signal:
                    # Skip if regime is ranging — reduce position, don't open new
                    if regime == 'ranging':
                        continue
                    # In bearish regime — only allow shorts
                    if regime == 'bearish' and signal == 'long':
                        continue

                    momentum = calculate_momentum_score(df)
                    signals_found.append({
                        'symbol': symbol,
                        'signal': signal,
                        'df': df,
                        'momentum_score': momentum
                    })

            # --- STEP 8: Rank and execute best signals ---
            ranked = rank_signals(signals_found)
            slots_available = MAX_OPEN_TRADES - len(open_trades)
            executed = 0

            for item in ranked[:slots_available]:
                symbol = item['symbol']
                signal = item['signal']
                df = item['df']
                entry_price = current_prices.get(symbol)

                if not entry_price:
                    continue

                success = open_trade(conn, symbol, signal, entry_price, df)
                if success:
                    executed += 1
                    print(f"  → Opened {signal.upper()} on {symbol} at {entry_price:.4f}")

            if executed == 0 and signals_found:
                print(f"  → {len(signals_found)} signals found but no slots / regime blocked")
            elif not signals_found:
                print("  → No signals this cycle")

        else:
            print("  → Max open trades reached, skipping signal scan")

        # --- STEP 9: Save portfolio snapshot ---
        save_portfolio_snapshot(conn)
        conn.close()

        # --- STEP 10: Print performance summary ---
        conn = sqlite3.connect(DB_PATH)
        stats = get_performance_stats(conn)
        conn.close()

        print(f"\n--- PERFORMANCE SUMMARY ---")
        print(f"  Capital    : ${stats['capital']:,.2f}")
        print(f"  Total PnL  : ${stats['total_pnl']:,.2f}")
        print(f"  Win Rate   : {stats['win_rate']}%")
        print(f"  Trades     : {stats['total_trades']}")
        print(f"  Expectancy : ${stats['expectancy']:.2f}")
        print(f"  Drawdown   : {stats['drawdown']}%")
        print(f"---------------------------")

    except Exception as e:
        logging.error(f"Bot cycle error: {e}")
        print(f"✗ Error: {e}")


def main():
    print("=" * 50)
    print("  CRYPTO FUTURES TRADING BOT — PAPER MODE")
    print("=" * 50)

    # Initialize database
    init_db()
    print("✓ Database initialized")

    # Run immediately on start
    run_bot()

    # Schedule to run every 4 hours (matching timeframe)
    schedule.every(4).hours.do(run_bot)
    print("\n✓ Bot scheduled every 4 hours. Running...")
    print("  Press Ctrl+C to stop.\n")

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()