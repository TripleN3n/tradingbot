import streamlit as st
import sqlite3
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timezone
from config import INITIAL_CAPITAL, DB_PATH
from paper_trader import get_performance_stats, get_open_trades, get_capital

st.set_page_config(
    page_title="Crypto Trading AI Agent",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap');
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    .main { background-color: #0a0a0f; }
    .block-container { padding: 1.5rem 2rem; max-width: 1400px; }
    .metric-card {
        background: linear-gradient(135deg, #111118 0%, #1a1a2e 100%);
        border: 1px solid #2a2a3e;
        border-radius: 12px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 0.5rem;
    }
    .metric-label {
        font-size: 0.72rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: #6b6b8a;
        font-family: 'Space Mono', monospace;
        margin-bottom: 0.3rem;
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 600;
        color: #e8e8f0;
        font-family: 'Space Mono', monospace;
        letter-spacing: -0.02em;
    }
    .metric-delta-pos { color: #00d4aa; font-size: 0.85rem; }
    .metric-delta-neg { color: #ff4b6e; font-size: 0.85rem; }
    .regime-badge {
        display: inline-block;
        padding: 0.3rem 1rem;
        border-radius: 20px;
        font-family: 'Space Mono', monospace;
        font-size: 0.75rem;
        letter-spacing: 0.08em;
        font-weight: 700;
    }
    .regime-trending { background: rgba(0,212,170,0.15); color: #00d4aa; border: 1px solid rgba(0,212,170,0.3); }
    .regime-bearish { background: rgba(255,75,110,0.15); color: #ff4b6e; border: 1px solid rgba(255,75,110,0.3); }
    .regime-ranging { background: rgba(255,165,0,0.15); color: #ffa500; border: 1px solid rgba(255,165,0,0.3); }
    .regime-unknown { background: rgba(107,107,138,0.15); color: #6b6b8a; border: 1px solid rgba(107,107,138,0.3); }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
    .status-live { background: #00d4aa; box-shadow: 0 0 8px #00d4aa; animation: pulse 2s infinite; }
    .status-offline { background: #ff4b6e; }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
    .section-header {
        font-family: 'Space Mono', monospace;
        font-size: 0.7rem;
        letter-spacing: 0.15em;
        text-transform: uppercase;
        color: #4a4a6a;
        padding: 0.5rem 0;
        border-bottom: 1px solid #1e1e2e;
        margin-bottom: 1rem;
    }
    div[data-testid="stDataFrame"] { border: 1px solid #2a2a3e; border-radius: 8px; overflow: hidden; }
    footer { display: none; }
    #MainMenu { display: none; }
    header { display: none; }
</style>
""", unsafe_allow_html=True)


def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def get_closed_trades(conn):
    try:
        return pd.read_sql_query(
            "SELECT * FROM trades WHERE status='closed' ORDER BY exit_time DESC", conn
        )
    except:
        return pd.DataFrame()


def get_portfolio_history(conn):
    try:
        return pd.read_sql_query(
            "SELECT * FROM portfolio ORDER BY timestamp ASC", conn
        )
    except:
        return pd.DataFrame()


def get_last_cycle_time(conn):
    try:
        c = conn.cursor()
        c.execute("SELECT timestamp FROM portfolio ORDER BY timestamp DESC LIMIT 1")
        row = c.fetchone()
        return row[0] if row else None
    except:
        return None


def get_market_regime_from_log():
    try:
        with open('logs/bot.log', 'r') as f:
            lines = f.readlines()
        for line in reversed(lines):
            if 'regime' in line.lower():
                if 'bearish' in line.lower():
                    return 'BEARISH'
                elif 'trending' in line.lower():
                    return 'TRENDING'
                elif 'ranging' in line.lower():
                    return 'RANGING'
        return 'UNKNOWN'
    except:
        return 'UNKNOWN'


# HEADER
col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown("""
        <h1 style="font-family:'Space Mono',monospace; font-size:1.4rem; letter-spacing:0.05em; margin:0; color:#e8e8f0;">
        ⚡ CRYPTO TRADING AI AGENT
        </h1>
        <p style="color:#4a4a6a; font-size:0.8rem; margin:0; font-family:'Space Mono',monospace;">
        PAPER TRADING MODE — BINANCE FUTURES — TOP 100 TOKENS
        </p>
    """, unsafe_allow_html=True)

with col_status:
    regime = get_market_regime_from_log()
    regime_class = f"regime-{regime.lower()}"
    st.markdown(f"""
        <div style="text-align:right; padding-top:0.5rem;">
            <span class="status-dot status-live"></span>
            <span style="font-family:'Space Mono',monospace; font-size:0.72rem; color:#6b6b8a;">AGENT ACTIVE</span>
            &nbsp;&nbsp;
            <span class="regime-badge {regime_class}">{regime}</span>
        </div>
    """, unsafe_allow_html=True)

st.markdown('<hr style="border:none; border-top:1px solid #1e1e2e; margin:1rem 0;">', unsafe_allow_html=True)

# DATA
conn = get_conn()
stats = get_performance_stats(conn)
open_trades = get_open_trades(conn)
closed_trades = get_closed_trades(conn)
portfolio_history = get_portfolio_history(conn)
last_cycle = get_last_cycle_time(conn)

# TOP METRICS
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

metrics = [
    (c1, "Capital", f"${stats['capital']:,.2f}", f"{stats['total_pnl']:+.2f}", stats['total_pnl'] >= 0),
    (c2, "Total PnL", f"${stats['total_pnl']:,.2f}", f"{(stats['total_pnl']/INITIAL_CAPITAL*100):+.1f}%", stats['total_pnl'] >= 0),
    (c3, "Win Rate", f"{stats['win_rate']}%", None, None),
    (c4, "Trades", f"{stats['total_trades']}", f"{len(open_trades)} open", True),
    (c5, "Expectancy", f"${stats['expectancy']:.2f}", "per trade", stats['expectancy'] >= 0),
    (c6, "Drawdown", f"{abs(stats['drawdown'])}%", "from peak", False),
    (c7, "Avg Win", f"${stats['avg_win']:.2f}", f"vs ${abs(stats['avg_loss']):.2f} loss", stats['avg_win'] >= abs(stats['avg_loss'])),
]

for col, label, value, delta, is_pos in metrics:
    with col:
        delta_class = "metric-delta-pos" if is_pos else "metric-delta-neg"
        delta_html = f'<div class="{delta_class}">{delta}</div>' if delta else ""
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            {delta_html}
        </div>
        """, unsafe_allow_html=True)

st.markdown("")

# EQUITY CURVE + TRADE ACTIVITY
col_eq, col_freq = st.columns([2, 1])

with col_eq:
    st.markdown('<div class="section-header">Equity Curve</div>', unsafe_allow_html=True)
    if not portfolio_history.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=portfolio_history['timestamp'],
            y=portfolio_history['capital'],
            mode='lines',
            line=dict(color='#00d4aa', width=2),
            fill='tozeroy',
            fillcolor='rgba(0,212,170,0.05)'
        ))
        fig.add_hline(y=INITIAL_CAPITAL, line_dash="dash",
                      line_color="#2a2a3e", annotation_text="Start",
                      annotation_font_color="#4a4a6a")
        fig.update_layout(
            height=220, margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            yaxis=dict(gridcolor='rgba(42,42,62,0.5)', tickfont=dict(color='#4a4a6a', size=10)),
            xaxis=dict(gridcolor='rgba(42,42,62,0.5)', tickfont=dict(color='#4a4a6a', size=10)),
            showlegend=False
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Equity curve will appear after first trades.")

with col_freq:
    st.markdown('<div class="section-header">Daily Trade Activity</div>', unsafe_allow_html=True)
    if not closed_trades.empty:
        closed_trades['exit_date'] = pd.to_datetime(closed_trades['exit_time']).dt.date
        daily = closed_trades.groupby('exit_date').size().reset_index(name='count')
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=daily['exit_date'], y=daily['count'],
            marker_color='#5a5aff', marker_opacity=0.8
        ))
        fig2.update_layout(
            height=220, margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            yaxis=dict(gridcolor='rgba(42,42,62,0.5)', tickfont=dict(color='#4a4a6a', size=10)),
            xaxis=dict(gridcolor='rgba(42,42,62,0.5)', tickfont=dict(color='#4a4a6a', size=10)),
            showlegend=False
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("Activity chart will appear after first trades.")

# OPEN POSITIONS
st.markdown('<div class="section-header">Open Positions</div>', unsafe_allow_html=True)

if open_trades:
    rows = []
    for t in open_trades:
        stop_pct = abs(t['entry_price'] - t['stop_loss']) / t['entry_price'] * 100
        tp_pct = abs(t['take_profit'] - t['entry_price']) / t['entry_price'] * 100
        direction = "▲ LONG" if t['signal'] == 'long' else "▼ SHORT"
        rows.append({
            'Token': t['symbol'].replace('/USDT:USDT', ''),
            'Direction': direction,
            'Entry $': f"${t['entry_price']:,.4f}",
            'Stop Loss': f"${t['stop_loss']:,.4f} (-{stop_pct:.1f}%)",
            'Take Profit': f"${t['take_profit']:,.4f} (+{tp_pct:.1f}%)",
            'Size': f"{t['position_size']:.4f}",
            'Leverage': f"{t['leverage']}x",
            'Opened': t['entry_time'][:16].replace('T', ' '),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.markdown("""
    <div style="background:#111118; border:1px solid #2a2a3e; border-radius:8px; padding:1.5rem;
         text-align:center; color:#4a4a6a; font-family:'Space Mono',monospace; font-size:0.8rem;">
    NO OPEN POSITIONS — AI AGENT SCANNING FOR SIGNALS
    </div>
    """, unsafe_allow_html=True)

# PERFORMANCE ANALYSIS
if not closed_trades.empty:
    st.markdown("")
    st.markdown('<div class="section-header">Performance Analysis</div>', unsafe_allow_html=True)

    col_pnl, col_exit, col_token, col_direction = st.columns(4)

    with col_pnl:
        colors = ['#00d4aa' if p > 0 else '#ff4b6e' for p in closed_trades['pnl']]
        fig3 = go.Figure()
        fig3.add_trace(go.Bar(
            x=list(range(1, len(closed_trades)+1)),
            y=closed_trades['pnl'],
            marker_color=colors
        ))
        fig3.update_layout(
            title=dict(text="PnL Per Trade", font=dict(color='#6b6b8a', size=11)),
            height=220, margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            yaxis=dict(gridcolor='rgba(42,42,62,0.5)', tickfont=dict(color='#4a4a6a', size=9)),
            xaxis=dict(gridcolor='rgba(42,42,62,0.5)', tickfont=dict(color='#4a4a6a', size=9)),
            showlegend=False
        )
        st.plotly_chart(fig3, use_container_width=True)

    with col_exit:
        exit_counts = closed_trades['exit_reason'].value_counts()
        fig4 = px.pie(
            values=exit_counts.values,
            names=exit_counts.index,
            hole=0.5,
            color_discrete_map={
                'take_profit': '#00d4aa',
                'stop_loss': '#ff4b6e',
                'time_stop': '#ffa500'
            }
        )
        fig4.update_layout(
            title=dict(text="Exit Reasons", font=dict(color='#6b6b8a', size=11)),
            height=220, margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor='rgba(0,0,0,0)',
            legend=dict(font=dict(color='#6b6b8a', size=9))
        )
        st.plotly_chart(fig4, use_container_width=True)

    with col_token:
        token_pnl = closed_trades.copy()
        token_pnl['token'] = token_pnl['symbol'].str.replace('/USDT:USDT', '')
        token_summary = token_pnl.groupby('token')['pnl'].sum().sort_values()
        fig5 = go.Figure()
        fig5.add_trace(go.Bar(
            x=token_summary.values,
            y=token_summary.index,
            orientation='h',
            marker_color=['#00d4aa' if v > 0 else '#ff4b6e' for v in token_summary.values]
        ))
        fig5.update_layout(
            title=dict(text="PnL by Token", font=dict(color='#6b6b8a', size=11)),
            height=220, margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            yaxis=dict(tickfont=dict(color='#4a4a6a', size=9)),
            xaxis=dict(gridcolor='rgba(42,42,62,0.5)', tickfont=dict(color='#4a4a6a', size=9)),
            showlegend=False
        )
        st.plotly_chart(fig5, use_container_width=True)

    with col_direction:
        long_pnl = closed_trades[closed_trades['signal'] == 'long']['pnl'].sum()
        short_pnl = closed_trades[closed_trades['signal'] == 'short']['pnl'].sum()
        long_count = len(closed_trades[closed_trades['signal'] == 'long'])
        short_count = len(closed_trades[closed_trades['signal'] == 'short'])
        fig6 = go.Figure()
        fig6.add_trace(go.Bar(
            x=['LONG', 'SHORT'],
            y=[long_pnl, short_pnl],
            marker_color=['#00d4aa', '#ff4b6e'],
            text=[f'{long_count} trades', f'{short_count} trades'],
            textposition='auto',
            textfont=dict(color='white', size=9)
        ))
        fig6.update_layout(
            title=dict(text="Long vs Short PnL", font=dict(color='#6b6b8a', size=11)),
            height=220, margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            yaxis=dict(gridcolor='rgba(42,42,62,0.5)', tickfont=dict(color='#4a4a6a', size=9)),
            xaxis=dict(tickfont=dict(color='#6b6b8a', size=10)),
            showlegend=False
        )
        st.plotly_chart(fig6, use_container_width=True)

# TRADE HISTORY
if not closed_trades.empty:
    st.markdown("")
    st.markdown('<div class="section-header">Trade History</div>', unsafe_allow_html=True)
    display = closed_trades[['symbol', 'signal', 'entry_price', 'exit_price',
                              'pnl', 'pnl_pct', 'exit_reason',
                              'entry_time', 'exit_time']].copy()
    display['symbol'] = display['symbol'].str.replace('/USDT:USDT', '')
    display['entry_time'] = display['entry_time'].str[:16].str.replace('T', ' ')
    display['exit_time'] = display['exit_time'].str[:16].str.replace('T', ' ')
    display.columns = ['Token', 'Direction', 'Entry $', 'Exit $',
                        'PnL $', 'PnL %', 'Exit Reason', 'Entry Time', 'Exit Time']
    st.dataframe(display, use_container_width=True, hide_index=True)

# FOOTER
st.markdown('<hr style="border:none; border-top:1px solid #1e1e2e; margin:1.5rem 0 0.5rem;">', unsafe_allow_html=True)
col_f1, col_f2, col_f3 = st.columns(3)

with col_f1:
    if last_cycle:
        st.markdown(f'<span style="color:#4a4a6a; font-size:0.72rem; font-family:Space Mono,monospace;">LAST CYCLE: {last_cycle[:16].replace("T"," ")}</span>', unsafe_allow_html=True)

with col_f2:
    st.markdown(f'<span style="color:#4a4a6a; font-size:0.72rem; font-family:Space Mono,monospace; display:block; text-align:center;">INITIAL CAPITAL: ${INITIAL_CAPITAL:,.0f} USDT</span>', unsafe_allow_html=True)

with col_f3:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    st.markdown(f'<span style="color:#4a4a6a; font-size:0.72rem; font-family:Space Mono,monospace; display:block; text-align:right;">REFRESHED: {now}</span>', unsafe_allow_html=True)

st.markdown('<meta http-equiv="refresh" content="60">', unsafe_allow_html=True)