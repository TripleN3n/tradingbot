import sqlite3
import pandas as pd
import logging
from datetime import datetime, timezone
from config import (INITIAL_CAPITAL, MAX_DRAWDOWN_PCT, MAX_OPEN_TRADES,
                    TIME_STOP_HOURS, TAKER_FEE, SLIPPAGE, DB_PATH,
                    ATR_TRAIL_MULTIPLIER)
from strategy import (calculate_stops, calculate_position_size,
                      update_trailing_stop)


def init_db():
    """Initialize SQLite database and create tables."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            signal TEXT,
            entry_price REAL,
            exit_price REAL,
            stop_loss REAL,
            take_profit REAL,
            position_size REAL,
            leverage INTEGER,
            entry_time TEXT,
            exit_time TEXT,
            exit_reason TEXT,
            pnl REAL,
            pnl_pct REAL,
            fees REAL,
            status TEXT
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            capital REAL,
            open_trades INTEGER,
            total_pnl REAL,
            win_rate REAL,
            drawdown REAL
        )
    ''')

    conn.commit()
    conn.close()
    logging.info("Database initialized")


def get_capital(conn):
    """Get current capital from closed trades + initial capital."""
    c = conn.cursor()
    c.execute("SELECT SUM(pnl) FROM trades WHERE status='closed'")
    result = c.fetchone()[0]
    total_pnl = result if result else 0.0
    return INITIAL_CAPITAL + total_pnl


def get_open_trades(conn):
    """Return all open trades."""
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE status='open'")
    rows = c.fetchall()
    columns = ['id', 'symbol', 'signal', 'entry_price', 'exit_price',
               'stop_loss', 'take_profit', 'position_size', 'leverage',
               'entry_time', 'exit_time', 'exit_reason', 'pnl',
               'pnl_pct', 'fees', 'status']
    return [dict(zip(columns, row)) for row in rows]


def open_trade(conn, symbol, signal, entry_price, df):
    """Open a new paper trade."""
    capital = get_capital(conn)
    open_trades = get_open_trades(conn)

    # Check max open trades
    if len(open_trades) >= MAX_OPEN_TRADES:
        logging.info(f"Max open trades reached. Skipping {symbol}")
        return False

    # Check drawdown limit
    peak = INITIAL_CAPITAL
    if capital < peak * (1 - MAX_DRAWDOWN_PCT):
        logging.warning("Max drawdown reached. No new trades.")
        return False

    # Check if symbol already has open trade
    open_symbols = [t['symbol'] for t in open_trades]
    if symbol in open_symbols:
        logging.info(f"Already have open trade for {symbol}")
        return False

    stop_loss, take_profit = calculate_stops(df, signal, entry_price)
    position_size = calculate_position_size(capital, entry_price, stop_loss)

    if position_size <= 0:
        return False

    # Simulate slippage on entry
    if signal == 'long':
        actual_entry = entry_price * (1 + SLIPPAGE)
    else:
        actual_entry = entry_price * (1 - SLIPPAGE)

    fees = actual_entry * position_size * TAKER_FEE
    entry_time = datetime.now(timezone.utc).isoformat()

    c = conn.cursor()
    c.execute('''
        INSERT INTO trades (symbol, signal, entry_price, stop_loss, take_profit,
                           position_size, leverage, entry_time, fees, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
    ''', (symbol, signal, actual_entry, stop_loss, take_profit,
          position_size, 3, entry_time, fees))
    conn.commit()
    logging.info(f"Opened {signal} trade on {symbol} at {actual_entry:.4f}")
    return True


def close_trade(conn, trade, exit_price, exit_reason):
    """Close an existing paper trade."""
    signal = trade['signal']
    entry_price = trade['entry_price']
    position_size = trade['position_size']

    # Simulate slippage on exit
    if signal == 'long':
        actual_exit = exit_price * (1 - SLIPPAGE)
        raw_pnl = (actual_exit - entry_price) * position_size
    else:
        actual_exit = exit_price * (1 + SLIPPAGE)
        raw_pnl = (entry_price - actual_exit) * position_size

    exit_fees = actual_exit * position_size * TAKER_FEE
    total_fees = trade['fees'] + exit_fees
    net_pnl = raw_pnl - total_fees
    pnl_pct = (net_pnl / INITIAL_CAPITAL) * 100
    exit_time = datetime.now(timezone.utc).isoformat()

    c = conn.cursor()
    c.execute('''
        UPDATE trades
        SET exit_price=?, exit_time=?, exit_reason=?, pnl=?,
            pnl_pct=?, fees=?, status='closed'
        WHERE id=?
    ''', (actual_exit, exit_time, exit_reason, net_pnl,
          pnl_pct, total_fees, trade['id']))
    conn.commit()
    logging.info(f"Closed {signal} on {trade['symbol']} | PnL: {net_pnl:.2f} USDT | Reason: {exit_reason}")


def update_trade_stop(conn, trade_id, new_stop):
    """Update trailing stop loss for an open trade."""
    c = conn.cursor()
    c.execute("UPDATE trades SET stop_loss=? WHERE id=?", (new_stop, trade_id))
    conn.commit()


def check_exits(conn, open_trades, current_prices, ohlcv_data):
    """Check all open trades for exit conditions."""
    for trade in open_trades:
        symbol = trade['symbol']
        if symbol not in current_prices:
            continue

        current_price = current_prices[symbol]
        signal = trade['signal']
        stop_loss = trade['stop_loss']
        take_profit = trade['take_profit']
        entry_time = datetime.fromisoformat(trade['entry_time'])
        hours_open = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600

        # Update trailing stop
        if symbol in ohlcv_data:
            atr = ohlcv_data[symbol]['atr'].iloc[-1]
            new_stop = update_trailing_stop(signal, current_price,
                                            trade['entry_price'], stop_loss, atr)
            if new_stop != stop_loss:
                update_trade_stop(conn, trade['id'], new_stop)
                stop_loss = new_stop

        # Check stop loss
        if signal == 'long' and current_price <= stop_loss:
            close_trade(conn, trade, current_price, 'stop_loss')

        elif signal == 'short' and current_price >= stop_loss:
            close_trade(conn, trade, current_price, 'stop_loss')

        # Check take profit
        elif signal == 'long' and current_price >= take_profit:
            close_trade(conn, trade, current_price, 'take_profit')

        elif signal == 'short' and current_price <= take_profit:
            close_trade(conn, trade, current_price, 'take_profit')

        # Time stop
        elif hours_open >= TIME_STOP_HOURS:
            close_trade(conn, trade, current_price, 'time_stop')


def save_portfolio_snapshot(conn):
    """Save current portfolio state to DB."""
    capital = get_capital(conn)
    open_trades = get_open_trades(conn)

    c = conn.cursor()
    c.execute("SELECT pnl FROM trades WHERE status='closed'")
    closed_pnls = [row[0] for row in c.fetchall()]

    total_pnl = sum(closed_pnls)
    wins = len([p for p in closed_pnls if p > 0])
    win_rate = (wins / len(closed_pnls) * 100) if closed_pnls else 0.0
    drawdown = min(0, (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100)

    c.execute('''
        INSERT INTO portfolio (timestamp, capital, open_trades, total_pnl, win_rate, drawdown)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (datetime.now(timezone.utc).isoformat(), capital,
          len(open_trades), total_pnl, win_rate, drawdown))
    conn.commit()


def get_performance_stats(conn):
    """Return performance summary dict."""
    c = conn.cursor()
    c.execute("SELECT pnl, pnl_pct FROM trades WHERE status='closed'")
    rows = c.fetchall()

    if not rows:
        return {
            'total_trades': 0, 'win_rate': 0, 'total_pnl': 0,
            'avg_win': 0, 'avg_loss': 0, 'expectancy': 0,
            'capital': INITIAL_CAPITAL, 'drawdown': 0
        }

    pnls = [r[0] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / len(pnls) * 100
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    expectancy = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)
    capital = get_capital(conn)
    drawdown = min(0, (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100)

    return {
        'total_trades': len(pnls),
        'win_rate': round(win_rate, 2),
        'total_pnl': round(sum(pnls), 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'expectancy': round(expectancy, 2),
        'capital': round(capital, 2),
        'drawdown': round(drawdown, 2)
    }