import pandas as pd
import numpy as np
import logging
from config import (EMA_FAST, EMA_SLOW, RSI_PERIOD, RSI_LONG_MIN, RSI_LONG_MAX,
                    RSI_SHORT_MIN, RSI_SHORT_MAX, ADX_PERIOD, ADX_THRESHOLD,
                    ATR_PERIOD, VOLUME_MULTIPLIER, ATR_STOP_MULTIPLIER,
                    ATR_TRAIL_MULTIPLIER, LEVERAGE, RISK_PER_TRADE_PCT,
                    INITIAL_CAPITAL, TAKER_FEE, SLIPPAGE,
                    STOCH_K_PERIOD, STOCH_D_PERIOD, TOKEN_STRATEGIES)


def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series, period):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).ewm(span=period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_atr(df, period):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calculate_adx(df, period):
    plus_dm = df['high'].diff()
    minus_dm = -df['low'].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr = calculate_atr(df, period)
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)) * 100
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx, plus_di, minus_di


def calculate_vwap(df):
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    return (typical_price * df['volume']).cumsum() / df['volume'].cumsum()


def calculate_stochastic(df, k_period=14, d_period=3):
    low_min = df['low'].rolling(k_period).min()
    high_max = df['high'].rolling(k_period).max()
    k = 100 * (df['close'] - low_min) / (high_max - low_min + 1e-10)
    d = k.rolling(d_period).mean()
    return k, d


def calculate_macd(series, fast=12, slow=26, signal=9):
    fast_ema = series.ewm(span=fast, adjust=False).mean()
    slow_ema = series.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def add_indicators(df):
    df = df.copy()
    df['ema_fast'] = calculate_ema(df['close'], EMA_FAST)
    df['ema_slow'] = calculate_ema(df['close'], EMA_SLOW)
    df['rsi'] = calculate_rsi(df['close'], RSI_PERIOD)
    df['atr'] = calculate_atr(df, ATR_PERIOD)
    df['adx'], df['plus_di'], df['minus_di'] = calculate_adx(df, ADX_PERIOD)
    df['vwap'] = calculate_vwap(df)
    df['stoch_k'], df['stoch_d'] = calculate_stochastic(df, STOCH_K_PERIOD, STOCH_D_PERIOD)
    df['macd_line'], df['macd_signal'], df['macd_hist'] = calculate_macd(df['close'])
    df['volume_ma'] = df['volume'].rolling(20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma']
    return df


def signal_rsi_pullback_vwap(df):
    """
    RSI_Pullback_VWAP strategy:
    Long: Price above VWAP, RSI pulling back to 35-50 (buying the dip in uptrend)
    Short: Price below VWAP, RSI pulling back to 50-65 (selling the rally in downtrend)
    """
    if len(df) < 60:
        return None
    df = add_indicators(df)
    latest = df.iloc[-1]

    if latest['volume_ratio'] < VOLUME_MULTIPLIER:
        return None

    # LONG
    if (latest['close'] > latest['vwap'] and
        latest['ema_fast'] > latest['ema_slow'] and
        RSI_LONG_MIN <= latest['rsi'] <= RSI_LONG_MAX):
        return 'long'

    # SHORT
    if (latest['close'] < latest['vwap'] and
        latest['ema_fast'] < latest['ema_slow'] and
        RSI_SHORT_MIN <= latest['rsi'] <= RSI_SHORT_MAX):
        return 'short'

    return None


def signal_stoch_ema_volume(df):
    """
    Stoch_EMA_Volume strategy:
    Long: Stoch K crosses above D, K below 80, EMA aligned bullish, volume spike
    Short: Stoch K crosses below D, K above 20, EMA aligned bearish, volume spike
    """
    if len(df) < 60:
        return None
    df = add_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    if latest['volume_ratio'] < VOLUME_MULTIPLIER:
        return None

    # LONG
    if (latest['stoch_k'] > latest['stoch_d'] and
        prev['stoch_k'] <= prev['stoch_d'] and
        latest['stoch_k'] < 80 and
        latest['ema_fast'] > latest['ema_slow']):
        return 'long'

    # SHORT
    if (latest['stoch_k'] < latest['stoch_d'] and
        prev['stoch_k'] >= prev['stoch_d'] and
        latest['stoch_k'] > 20 and
        latest['ema_fast'] < latest['ema_slow']):
        return 'short'

    return None


def generate_signal(df, symbol=None):
    """
    Route to the correct per-token strategy based on backtesting results.
    Falls back to RSI_Pullback_VWAP if no specific strategy assigned.
    """
    if len(df) < 60:
        return None

    # Get assigned strategy for this token
    strategy_name = TOKEN_STRATEGIES.get(symbol, 'RSI_Pullback_VWAP')

    if strategy_name == 'RSI_Pullback_VWAP':
        return signal_rsi_pullback_vwap(df)
    elif strategy_name == 'Stoch_EMA_Volume':
        return signal_stoch_ema_volume(df)
    else:
        return signal_rsi_pullback_vwap(df)


def calculate_position_size(capital, entry_price, stop_price):
    risk_amount = INITIAL_CAPITAL * RISK_PER_TRADE_PCT  # Fixed, not compounding
    price_risk_pct = abs(entry_price - stop_price) / entry_price
    if price_risk_pct == 0:
        return 0
    position_value = risk_amount / price_risk_pct
    position_size = (position_value * LEVERAGE) / entry_price
    return round(position_size, 4)


def calculate_stops(df, signal, entry_price):
    atr = df['atr'].iloc[-1] if 'atr' in df.columns else calculate_atr(df, ATR_PERIOD).iloc[-1]
    if signal == 'long':
        stop_loss = entry_price - (ATR_STOP_MULTIPLIER * atr)
        take_profit = entry_price + (3 * ATR_STOP_MULTIPLIER * atr)
    else:
        stop_loss = entry_price + (ATR_STOP_MULTIPLIER * atr)
        take_profit = entry_price - (3 * ATR_STOP_MULTIPLIER * atr)
    return round(stop_loss, 6), round(take_profit, 6)


def update_trailing_stop(signal, current_price, entry_price, current_stop, atr):
    trail_distance = ATR_TRAIL_MULTIPLIER * atr
    if signal == 'long':
        new_stop = current_price - trail_distance
        if new_stop > current_stop:
            return round(new_stop, 6)
    elif signal == 'short':
        new_stop = current_price + trail_distance
        if new_stop < current_stop:
            return round(new_stop, 6)
    return current_stop


def calculate_momentum_score(df):
    price_change = (df['close'].iloc[-1] - df['close'].iloc[-6]) / df['close'].iloc[-6]
    volume_change = df['volume'].iloc[-1] / df['volume'].iloc[-6:].mean()
    return price_change * volume_change


def rank_signals(signals_data):
    return sorted(signals_data, key=lambda x: abs(x['momentum_score']), reverse=True)


def detect_market_regime(btc_df):
    if btc_df is None or len(btc_df) < 60:
        return 'ranging'
    btc_df = add_indicators(btc_df)
    latest = btc_df.iloc[-1]
    adx = latest['adx']
    close = latest['close']
    ema_slow = latest['ema_slow']
    if adx > ADX_THRESHOLD and close > ema_slow:
        return 'trending'
    elif adx > ADX_THRESHOLD and close < ema_slow:
        return 'bearish'
    else:
        return 'ranging'