import streamlit as st
import sqlite3
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
from config import INITIAL_CAPITAL, DB_PATH
from paper_trader import get_performance_stats, get_open_trades, get_capital

st.set_page_config(
    page_title="Trading Bot Dashboard",
    page_icon="📈",
    layout="wide"
)

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def get_closed_trades(conn):
    try:
        df = pd.read_sql_query(
            "SELECT * FROM trades WHERE status='closed' ORDER BY exit_time DESC",
            conn
        )
        return df
    except:
        return pd.DataFrame()

def get_portfolio_history(conn):
    try:
        df = pd.read_sql_query(
            "SELECT * FROM portfolio ORDER BY timestamp ASC",
            conn
        )
        return df
    except:
        return pd.DataFrame()

# --- HEADER ---
st.title("🤖 Crypto Futures Trading Bot")
st.caption(f"Paper Trading Mode | Initial Capital: ${INITIAL_CAPITAL:,.2f} USDT")
st.divider()

conn = get_conn()
stats = get_performance_stats(conn)
open_trades = get_open_trades(conn)
closed_trades = get_closed_trades(conn)
portfolio_history = get_portfolio_history(conn)

# --- TOP METRICS ---
col1, col2, col3, col4, col5, col6 = st.columns(6)

with col1:
    st.metric("💰 Current Capital",
              f"${stats['capital']:,.2f}",
              f"{stats['total_pnl']:+.2f}")

with col2:
    st.metric("📊 Total Trades", stats['total_trades'])

with col3:
    win_color = "normal" if stats['win_rate'] >= 50 else "inverse"
    st.metric("🎯 Win Rate", f"{stats['win_rate']}%")

with col4:
    st.metric("💵 Total PnL",
              f"${stats['total_pnl']:,.2f}",
              delta_color="normal" if stats['total_pnl'] >= 0 else "inverse")

with col5:
    st.metric("📉 Max Drawdown", f"{stats['drawdown']}%")

with col6:
    st.metric("⚡ Expectancy", f"${stats['expectancy']:.2f}")

st.divider()

# --- EQUITY CURVE ---
if not portfolio_history.empty:
    st.subheader("📈 Equity Curve")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=portfolio_history['timestamp'],
        y=portfolio_history['capital'],
        mode='lines',
        name='Capital',
        line=dict(color='#00d4aa', width=2),
        fill='tozeroy',
        fillcolor='rgba(0, 212, 170, 0.1)'
    ))
    fig.add_hline(y=INITIAL_CAPITAL, line_dash="dash",
                  line_color="gray", annotation_text="Starting Capital")
    fig.update_layout(
        height=300,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        yaxis=dict(gridcolor='rgba(128,128,128,0.2)'),
        xaxis=dict(gridcolor='rgba(128,128,128,0.2)')
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Equity curve will appear after first portfolio snapshot.")

st.divider()

# --- OPEN TRADES ---
st.subheader(f"🔓 Open Trades ({len(open_trades)})")
if open_trades:
    open_df = pd.DataFrame(open_trades)
    display_cols = ['symbol', 'signal', 'entry_price', 'stop_loss',
                    'take_profit', 'position_size', 'leverage', 'entry_time']
    open_df = open_df[display_cols]
    open_df.columns = ['Symbol', 'Direction', 'Entry Price', 'Stop Loss',
                       'Take Profit', 'Size', 'Leverage', 'Entry Time']

    def color_direction(val):
        color = '#00d4aa' if val == 'long' else '#ff4b4b'
        return f'color: {color}; font-weight: bold'

    styled = open_df.style.applymap(color_direction, subset=['Direction'])
    st.dataframe(styled, use_container_width=True, hide_index=True)
else:
    st.info("No open trades currently.")

st.divider()

# --- CLOSED TRADES ---
st.subheader("📋 Trade History")
if not closed_trades.empty:
    col_l, col_r = st.columns(2)

    # PnL per trade bar chart
    with col_l:
        fig2 = go.Figure()
        colors = ['#00d4aa' if p > 0 else '#ff4b4b' for p in closed_trades['pnl']]