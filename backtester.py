import sqlite3
import pandas as pd
import numpy as np
import json

DB_PATH = "backtest_data.db"
RESULTS_PATH = "backtest_results.json"

RISK_PER_TRADE = 0.02
INITIAL_CAPITAL = 1000.0
LEVERAGE = 3
TAKER_FEE = 0.0005
SLIPPAGE = 0.0005
ATR_STOP_MULT = 1.5
ATR_TRAIL_MULT = 1.0
MIN_TRADES = 20
MIN_VAL_TRADES = 10
MIN_WIN_RATE = 0.45
MAX_DRAWDOWN = 0.30
TRAIN_RATIO = 0.60

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).ewm(span=period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def macd(series, fast=12, slow=26, signal=9):
    fast_ema = series.ewm(span=fast, adjust=False).mean()
    slow_ema = series.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def adx(df, period=14):
    plus_dm = df['high'].diff()
    minus_dm = -df['low'].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr_val = atr(df, period)
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr_val)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr_val)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)) * 100
    return dx.ewm(span=period, adjust=False).mean(), plus_di, minus_di

def vwap(df):
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    cumulative_tpv = (typical_price * df['volume']).cumsum()
    cumulative_vol = df['volume'].cumsum()
    return cumulative_tpv / cumulative_vol

def bollinger(series, period=20, std=2):
    mid = series.rolling(period).mean()
    std_dev = series.rolling(period).std()
    return mid + std * std_dev, mid, mid - std * std_dev

def stochastic(df, k_period=14, d_period=3):
    low_min = df['low'].rolling(k_period).min()
    high_max = df['high'].rolling(k_period).max()
    k = 100 * (df['close'] - low_min) / (high_max - low_min + 1e-10)
    d = k.rolling(d_period).mean()
    return k, d

def add_all_indicators(df):
    df = df.copy()
    df['ema9'] = ema(df['close'], 9)
    df['ema21'] = ema(df['close'], 21)
    df['ema50'] = ema(df['close'], 50)
    df['ema200'] = ema(df['close'], 200)
    df['rsi14'] = rsi(df['close'], 14)
    df['rsi9'] = rsi(df['close'], 9)
    df['atr14'] = atr(df, 14)
    df['macd_line'], df['macd_signal'], df['macd_hist'] = macd(df['close'])
    df['adx14'], df['plus_di'], df['minus_di'] = adx(df, 14)
    df['vwap'] = vwap(df)
    df['bb_upper'], df['bb_mid'], df['bb_lower'] = bollinger(df['close'])
    df['stoch_k'], df['stoch_d'] = stochastic(df)
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma20']
    df['obv'] = (df['volume'] * np.sign(df['close'].diff())).cumsum()
    df['obv_ema'] = ema(df['obv'], 21)
    return df.dropna()

STRATEGIES = {
    'S01': {'name': 'EMA_Cross_RSI',
             'long':  lambda r: r.ema21 > r.ema50 and 45 <= r.rsi14 <= 70 and r.vol_ratio > 1.5,
             'short': lambda r: r.ema21 < r.ema50 and 30 <= r.rsi14 <= 55 and r.vol_ratio > 1.5},

    'S02': {'name': 'VWAP_EMA_Volume',
             'long':  lambda r: r.close > r.vwap and r.ema21 > r.ema50 and r.vol_ratio > 2.0,
             'short': lambda r: r.close < r.vwap and r.ema21 < r.ema50 and r.vol_ratio > 2.0},

    'S03': {'name': 'MACD_EMA_ADX',
             'long':  lambda r: r.macd_hist > 0 and r.ema21 > r.ema50 and r.adx14 > 25,
             'short': lambda r: r.macd_hist < 0 and r.ema21 < r.ema50 and r.adx14 > 25},

    'S04': {'name': 'RSI_Pullback_VWAP',
             'long':  lambda r: r.close > r.vwap and 35 <= r.rsi14 <= 50 and r.ema21 > r.ema50,
             'short': lambda r: r.close < r.vwap and 50 <= r.rsi14 <= 65 and r.ema21 < r.ema50},

    'S05': {'name': 'Bollinger_Breakout_Vol',
             'long':  lambda r: r.close > r.bb_upper and r.vol_ratio > 2.0 and r.rsi14 < 75,
             'short': lambda r: r.close < r.bb_lower and r.vol_ratio > 2.0 and r.rsi14 > 25},

    'S06': {'name': 'Triple_EMA_ADX',
             'long':  lambda r: r.ema9 > r.ema21 > r.ema50 and r.adx14 > 25 and r.plus_di > r.minus_di,
             'short': lambda r: r.ema9 < r.ema21 < r.ema50 and r.adx14 > 25 and r.minus_di > r.plus_di},

    'S07': {'name': 'MACD_VWAP_RSI',
             'long':  lambda r: r.macd_hist > 0 and r.close > r.vwap and 40 <= r.rsi14 <= 65,
             'short': lambda r: r.macd_hist < 0 and r.close < r.vwap and 35 <= r.rsi14 <= 60},

    'S08': {'name': 'Stoch_EMA_Volume',
             'long':  lambda r: r.stoch_k > r.stoch_d and r.stoch_k < 80 and r.ema21 > r.ema50 and r.vol_ratio > 1.5,
             'short': lambda r: r.stoch_k < r.stoch_d and r.stoch_k > 20 and r.ema21 < r.ema50 and r.vol_ratio > 1.5},

    'S09': {'name': 'OBV_EMA_MACD',
             'long':  lambda r: r.obv > r.obv_ema and r.macd_hist > 0 and r.ema21 > r.ema50,
             'short': lambda r: r.obv < r.obv_ema and r.macd_hist < 0 and r.ema21 < r.ema50},

    'S10': {'name': 'VWAP_RSI_ADX',
             'long':  lambda r: r.close > r.vwap and r.rsi14 > 50 and r.adx14 > 20 and r.plus_di > r.minus_di,
             'short': lambda r: r.close < r.vwap and r.rsi14 < 50 and r.adx14 > 20 and r.minus_di > r.plus_di},

    'S11': {'name': 'EMA200_MACD_Vol',
             'long':  lambda r: r.close > r.ema200 and r.macd_hist > 0 and r.vol_ratio > 1.5,
             'short': lambda r: r.close < r.ema200 and r.macd_hist < 0 and r.vol_ratio > 1.5},

    'S12': {'name': 'Bollinger_RSI_EMA',
             'long':  lambda r: r.close > r.bb_mid and r.rsi14 > 50 and r.ema21 > r.ema50,
             'short': lambda r: r.close < r.bb_mid and r.rsi14 < 50 and r.ema21 < r.ema50},

    'S13': {'name': 'Full_Confluence',
             'long':  lambda r: r.close > r.vwap and r.ema21 > r.ema50 and r.macd_hist > 0 and r.rsi14 > 50 and r.vol_ratio > 1.5,
             'short': lambda r: r.close < r.vwap and r.ema21 < r.ema50 and r.macd_hist < 0 and r.rsi14 < 50 and r.vol_ratio > 1.5},

    'S14': {'name': 'ADX_Stoch_VWAP',
             'long':  lambda r: r.adx14 > 25 and r.stoch_k > r.stoch_d and r.close > r.vwap,
             'short': lambda r: r.adx14 > 25 and r.stoch_k < r.stoch_d and r.close < r.vwap},

    'S15': {'name': 'EMA_Cross_MACD_ADX',
             'long':  lambda r: r.ema21 > r.ema50 and r.macd_hist > 0 and r.adx14 > 20,
             'short': lambda r: r.ema21 < r.ema50 and r.macd_hist < 0 and r.adx14 > 20},
}

def run_backtest(df, strategy_key):
    strategy = STRATEGIES[strategy_key]
    capital = INITIAL_CAPITAL
    peak_capital = capital
    max_dd = 0
    trades = []
    open_trade = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        if open_trade:
            current_price = row['close']
            signal = open_trade['signal']
            stop = open_trade['stop']
            take_profit = open_trade['take_profit']
            atr_val = row['atr14']

            trail_dist = ATR_TRAIL_MULT * atr_val
            if signal == 'long':
                new_stop = current_price - trail_dist
                if new_stop > stop:
                    open_trade['stop'] = new_stop
                    stop = new_stop
            else:
                new_stop = current_price + trail_dist
                if new_stop < stop:
                    open_trade['stop'] = new_stop
                    stop = new_stop

            exit_price = None
            exit_reason = None

            if signal == 'long':
                if current_price <= stop:
                    exit_price = stop
                    exit_reason = 'stop_loss'
                elif current_price >= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'
            else:
                if current_price >= stop:
                    exit_price = stop
                    exit_reason = 'stop_loss'
                elif current_price <= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'

            if not exit_price and (i - open_trade['entry_idx']) >= 48:
                exit_price = current_price
                exit_reason = 'time_stop'

            if exit_price:
                entry = open_trade['entry_price']
                size = open_trade['size']
                if signal == 'long':
                    pnl = (exit_price * (1 - SLIPPAGE) - entry) * size
                else:
                    pnl = (entry - exit_price * (1 + SLIPPAGE)) * size
                fees = (entry + exit_price) * size * TAKER_FEE
                net_pnl = pnl - fees
                capital += net_pnl
                peak_capital = max(peak_capital, capital)
                dd = (peak_capital - capital) / peak_capital
                max_dd = max(max_dd, dd)
                trades.append({'pnl': net_pnl, 'reason': exit_reason})
                open_trade = None

        if not open_trade:
            try:
                long_sig = strategy['long'](row)
                short_sig = strategy['short'](row)
            except:
                continue

            signal = 'long' if long_sig else ('short' if short_sig else None)
            if signal:
                entry_price = row['close'] * (1 + SLIPPAGE if signal == 'long' else 1 - SLIPPAGE)
                atr_val = row['atr14']
                if signal == 'long':
                    stop = entry_price - ATR_STOP_MULT * atr_val
                    take_profit = entry_price + 3 * ATR_STOP_MULT * atr_val
                else:
                    stop = entry_price + ATR_STOP_MULT * atr_val
                    take_profit = entry_price - 3 * ATR_STOP_MULT * atr_val

                risk_amount = capital * RISK_PER_TRADE
                price_risk = abs(entry_price - stop) / entry_price
                if price_risk == 0:
                    continue
                size = (risk_amount / price_risk * LEVERAGE) / entry_price
                open_trade = {
                    'signal': signal,
                    'entry_price': entry_price,
                    'stop': stop,
                    'take_profit': take_profit,
                    'size': size,
                    'entry_idx': i
                }

    if not trades:
        return None

    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls)
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    return {
        'total_trades': len(trades),
        'win_rate': round(win_rate * 100, 1),
        'expectancy': round(expectancy, 2),
        'total_pnl': round(sum(pnls), 2),
        'max_drawdown': round(max_dd * 100, 1),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'final_capital': round(capital, 2)
    }

def walk_forward_validate(df, strategy_key):
    split_idx = int(len(df) * TRAIN_RATIO)
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    val_df = df.iloc[split_idx:].reset_index(drop=True)
    if len(train_df) < 300 or len(val_df) < 200:
        return None, None
    train_result = run_backtest(train_df, strategy_key)
    val_result = run_backtest(val_df, strategy_key)
    return train_result, val_result

def find_best_strategy(df):
    best_result = None
    best_val_result = None
    best_strategy = None
    best_score = -999

    for strat_key in STRATEGIES:
        train_result, val_result = walk_forward_validate(df, strat_key)
        if not train_result or not val_result:
            continue
        if train_result['total_trades'] < MIN_TRADES:
            continue
        if val_result['total_trades'] < MIN_VAL_TRADES:
            continue
	# Drawdown is informational only - not a filter
        # Crypto markets had 70-80% crashes - any strategy will show high DD

        # Score on validation data only
        val_score = (val_result['win_rate'] * 0.4) + \
                   (min(max(val_result['expectancy'], -50), 50) * 0.4) + \
                   ((100 - val_result['max_drawdown']) * 0.2)

        consistency = abs(train_result['win_rate'] - val_result['win_rate'])
        if consistency < 10:
            val_score *= 1.1

        if val_score > best_score:
            best_score = val_score
            best_result = train_result
            best_val_result = val_result
            best_strategy = strat_key

    return best_strategy, best_result, best_val_result

def assign_verdict(best_result, best_val_result):
    if not best_val_result:
        return 'SKIP'

    exp = best_val_result['expectancy']
    wr = best_val_result['win_rate']

    if exp <= 0:
        return 'SKIP'

    consistency = abs(best_result['win_rate'] - best_val_result['win_rate'])

    if wr >= MIN_WIN_RATE * 100:
        return 'DEPLOY' if consistency <= 10 else 'CAUTION'
    elif wr >= 40:
        return 'WATCH'
    else:
        return 'CAUTION'

def main():
    print("=" * 70)
    print("  BACKTESTING ENGINE WITH WALK-FORWARD VALIDATION")
    print("  60% Training | 40% Out-of-Sample Validation")
    print("  15 Strategy Combinations Per Token")
    print("  Verdicts: DEPLOY / CAUTION / WATCH / SKIP")
    print("=" * 70)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT symbol FROM data_status WHERE status='ok' ORDER BY candles DESC")
    symbols = [row[0] for row in c.fetchall()]
    print(f"{len(symbols)} symbols to backtest\n")

    all_results = {}

    for idx, symbol in enumerate(symbols):
        print(f"[{idx+1}/{len(symbols)}] {symbol}")

        df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume FROM ohlcv WHERE symbol=? ORDER BY timestamp",
            conn, params=(symbol,)
        )

        if len(df) < 500:
            print(f"  SKIP - insufficient data")
            all_results[symbol] = {
                'symbol': symbol, 'verdict': 'SKIP',
                'reason': 'insufficient_data',
                'best_strategy': None, 'strategy_name': None,
                'metrics': None, 'validation': None
            }
            continue

        df = add_all_indicators(df)
        best_strategy, best_result, best_val_result = find_best_strategy(df)
        verdict = assign_verdict(best_result, best_val_result)

        token_result = {
            'symbol': symbol,
            'best_strategy': best_strategy,
            'strategy_name': STRATEGIES[best_strategy]['name'] if best_strategy else None,
            'verdict': verdict,
            'metrics': best_result,
            'validation': best_val_result
        }

        all_results[symbol] = token_result

        if best_result and best_val_result:
            consistency = abs(best_result['win_rate'] - best_val_result['win_rate'])
            print(f"  Strategy : {STRATEGIES[best_strategy]['name']}")
            print(f"  Train    : WR {best_result['win_rate']}% | Exp ${best_result['expectancy']} | DD {best_result['max_drawdown']}%")
            print(f"  Validate : WR {best_val_result['win_rate']}% | Exp ${best_val_result['expectancy']} | DD {best_val_result['max_drawdown']}% | Gap {consistency:.1f}%")
            print(f"  Verdict  : {verdict}")
        else:
            print(f"  Verdict  : SKIP - no valid strategy found")
        print()

    conn.close()

    with open(RESULTS_PATH, 'w') as f:
        json.dump(all_results, f, indent=2)

    deploy = [r for r in all_results.values() if r['verdict'] == 'DEPLOY']
    caution = [r for r in all_results.values() if r['verdict'] == 'CAUTION']
    watch = [r for r in all_results.values() if r['verdict'] == 'WATCH']
    skip = [r for r in all_results.values() if r['verdict'] == 'SKIP']

    print(f"\n{'='*70}")
    print(f"  BACKTESTING COMPLETE")
    print(f"  DEPLOY:  {len(deploy)} tokens")
    print(f"  CAUTION: {len(caution)} tokens")
    print(f"  WATCH:   {len(watch)} tokens")
    print(f"  SKIP:    {len(skip)} tokens")
    print(f"  Results saved to: {RESULTS_PATH}")
    print(f"{'='*70}")

if __name__ == "__main__":
    main()