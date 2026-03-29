import pandas as pd
import numpy as np
import logging
from config import (EMA_FAST, EMA_SLOW, RSI_PERIOD, RSI_LONG_MIN, RSI_LONG_MAX,
                    RSI_SHORT_MIN, RSI_SHORT_MAX, ADX_PERIOD, ADX_THRESHOLD,
                    ATR_PERIOD, VOLUME_MULTIPLIER, ATR_STOP_MULTIPLIER,
                    ATR_TRAIL_MULTIPLIER, LEVERAGE, RISK_PER_TRADE_PCT,
                    INITIAL_CAPITAL, TAKER_FEE, SLIPPAGE)


def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series, period):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_atr(df, period):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calculate_adx(df, period):
    plus_dm = df['high'].diff()
    minus_dm = -df['low'].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr = calculate_atr(df, period)
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx, plus_di, minus_di


def calculate_momentum_score(df):
    """24H momentum score based on price change and volume."""
    price_change = (df['close'].iloc[-1] - df['close'].iloc[-6]) / df['close'].iloc[-6]
    volume_change = df['volume'].iloc[-1] / df['volume'].iloc[-6:].mean()
    return price_change * volume_change


def add_indicators(df):
    """Add all technical indicators to dataframe."""
    df = df.copy()
    df['ema_fast'] = calculate_ema(df['close'], EMA_FAST)
    df['ema_slow'] = calculate_ema(df['close'], EMA_SLOW)
    df['rsi'] = calculate_rsi(df['close'], RSI_PERIOD)
    df['atr'] = calculate_atr(df, ATR_PERIOD)
    df['adx'], df['plus_di'], df['minus_di'] = calculate_adx(df, ADX_PERIOD)
    df['volume_ma'] = df['volume'].rolling(20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma']
    return df


def generate_signal(df):
    """
    Generate trading signal for a single symbol.
    Returns: 'long', 'short', or None
    """
    if len(df) < 60:
        return None

    df = add_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    adx = latest['adx']
    rsi = latest['rsi']
    ema_fast = latest['ema_fast']
    ema_slow = latest['ema_slow']
    close = latest['close']
    volume_ratio = latest['volume_ratio']

    # Volume confirmation
    if volume_ratio < VOLUME_MULTIPLIER:
        return None

    # Trend strength filter
    if adx < ADX_THRESHOLD:
        return None

    # LONG conditions
    long_conditions = [
        close > ema_fast > ema_slow,           # Price above both EMAs, fast above slow
        RSI_LONG_MIN <= rsi <= RSI_LONG_MAX,   # RSI not overbought
        latest['plus_di'] > latest['minus_di'], # Bullish directional movement
        prev['ema_fast'] <= prev['ema_slow'] or close > ema_fast,  # EMA crossover or momentum
    ]

    # SHORT conditions
    short_conditions = [
        close < ema_fast < ema_slow,            # Price below both EMAs, fast below slow
        RSI_SHORT_MIN <= rsi <= RSI_SHORT_MAX,  # RSI not oversold
        latest['minus_di'] > latest['plus_di'], # Bearish directional movement
        prev['ema_fast'] >= prev['ema_slow'] or close < ema_fast,
    ]

    if all(long_conditions):
        return 'long'
    elif all(short_conditions):
        return 'short'
    else:
        return None


def calculate_position_size(capital, entry_price, stop_price):
    """Calculate position size based on fixed risk per trade."""
    risk_amount = capital * RISK_PER_TRADE_PCT
    price_risk_pct = abs(entry_price - stop_price) / entry_price
    if price_risk_pct == 0:
        return 0
    position_value = risk_amount / price_risk_pct
    position_size = (position_value * LEVERAGE) / entry_price
    return round(position_size, 4)


def calculate_stops(df, signal, entry_price):
    """Calculate stop loss and initial take profit levels."""
    atr = df['atr'].iloc[-1]
    if signal == 'long':
        stop_loss = entry_price - (ATR_STOP_MULTIPLIER * atr)
        take_profit = entry_price + (3 * ATR_STOP_MULTIPLIER * atr)  # 1:3 RR
    else:
        stop_loss = entry_price + (ATR_STOP_MULTIPLIER * atr)
        take_profit = entry_price - (3 * ATR_STOP_MULTIPLIER * atr)
    return round(stop_loss, 6), round(take_profit, 6)


def update_trailing_stop(signal, current_price, entry_price, current_stop, atr):
    """Update trailing stop as price moves in favor."""
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


def rank_signals(signals_data):
    """
    Rank multiple signals by momentum score.
    signals_data: list of dicts with keys: symbol, signal, df, momentum_score
    Returns sorted list, strongest first.
    """
    ranked = sorted(signals_data, key=lambda x: abs(x['momentum_score']), reverse=True)
    return ranked


def detect_market_regime(btc_df):
    """
    Detect overall market regime using BTC trend.
    Returns: 'trending', 'ranging', or 'bearish'
    """
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