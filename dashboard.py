# =============================================================================
# APEX — AI Trading Bot  |  dashboard.py v4.2
# All dashboard logic consolidated into single file
# =============================================================================

import streamlit as st, sqlite3, pandas as pd, plotly.graph_objects as go
import numpy as np, os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
from streamlit_autorefresh import st_autorefresh

from bot.config import (INITIAL_CAPITAL, DB, PAPER_TRADING, DRAWDOWN)

st.set_page_config(page_title="APEX", page_icon="▲",
                   layout="wide", initial_sidebar_state="collapsed")

st_autorefresh(interval=30000, key="apex_refresh")

st.markdown("""
<style>
iframe[title="streamlit_autorefresh.autorefresh"] {
    display: none !important; height: 0 !important; position: absolute !important;
}
div[data-testid="stIFrame"] { display:none!important; height:0!important; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=JetBrains+Mono:wght@300;400;500;700&family=Syne:wght@400;600;700;800&display=swap');
:root{
  --bg:#080b0f;--bg2:#0d1117;--bg3:#111820;
  --border:#1e2a38;--border2:#243040;
  --text:#d8e0e8;--dim:#6b7a8d;--mid:#9aa5b4;
  --green:#00e5a0;--red:#ff3d6b;--gold:#f5a623;--blue:#58a6ff;--purple:#bc8cff;
  --mono:'JetBrains Mono',monospace;--display:'Bebas Neue',sans-serif;
}
html,body,[class*="css"]{font-family:'Syne',sans-serif!important;background:var(--bg)!important;color:var(--text);}
.main,.stApp{background:var(--bg)!important;}
.block-container{padding:0 1.5rem 2rem 1.5rem!important;max-width:1800px!important;margin-top:-5rem!important;}
footer,#MainMenu,header,.stDeployButton,[data-testid="stToolbar"],[data-testid="stHeader"],[data-testid="stDecoration"],[data-testid="stStatusWidget"]{display:none!important;height:0!important;overflow:hidden!important;}
[data-testid="stAppViewContainer"]>section>div:first-child{padding-top:0!important;}
div.stMainBlockContainer{padding-top:0.5rem!important;}
[data-testid="stHeader"]{min-height:0!important;}
div[data-stale="true"]{opacity:1!important;transition:none!important;}
.element-container:has(iframe[title="streamlit_autorefresh.autorefresh"]){display:none!important;height:0!important;}

/* ── BORDERED CONTAINERS ── */
div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"],
div[data-testid="stHorizontalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]{
  background:var(--bg2)!important;border:1px solid var(--border)!important;
  border-radius:6px!important;margin-bottom:1.2rem!important;
}
div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:hover,
div[data-testid="stHorizontalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:hover{
  border-color:var(--border2)!important;
}
/* Columns never get their own border */
div[data-testid="column"] div[data-testid="stVerticalBlockBorderWrapper"] {
  border:none!important;background:transparent!important;
  margin-bottom:0!important;border-radius:0!important;box-shadow:none!important;
}
/* Kill borders wrapping column rows */
[data-testid="stVerticalBlockBorderWrapper"]:has([data-testid="stHorizontalBlock"]) {
  border:none!important;background:transparent!important;
  margin-bottom:0!important;box-shadow:none!important;padding:0!important;
}
[data-testid="stAppViewBlockContainer"] > div > div[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stMain"] > div > div[data-testid="stVerticalBlockBorderWrapper"]{
  border:none!important;background:transparent!important;
}

/* ── DOWNLOAD BUTTONS ── */
div[data-testid="stDownloadButton"] { margin:0!important; padding:0!important; }
div[data-testid="stDownloadButton"] > button {
    background:transparent!important;border:1px solid #1e2a38!important;
    border-radius:3px!important;color:#2a3548!important;
    font-family:'JetBrains Mono',monospace!important;font-size:0.44rem!important;
    letter-spacing:0.12em!important;text-transform:uppercase!important;
    padding:0.12rem 0.5rem!important;height:auto!important;
    min-height:unset!important;line-height:1.5!important;width:100%!important;
}
div[data-testid="stDownloadButton"] > button:hover {
    border-color:#00e5a0!important;color:#00e5a0!important;
    background:rgba(0,229,160,0.06)!important;
}

/* ── HEADER ── */
.apex-header{display:flex;align-items:center;justify-content:space-between;
  padding:1.2rem 0 1rem 0;border-bottom:1px solid var(--border);margin-bottom:1.4rem;
  flex-wrap:wrap;gap:0.8rem;}
.apex-logo{font-family:'Bebas Neue',sans-serif;font-size:3.8rem;letter-spacing:0.08em;line-height:1;
  background:linear-gradient(135deg,#00e5a0 0%,#58a6ff 50%,#bc8cff 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.apex-subtitle{font-family:var(--mono);font-size:0.65rem;letter-spacing:0.22em;
  text-transform:uppercase;color:var(--mid);margin-top:-0.2rem;}
.header-right{display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap;}
.sentiment-col{display:flex;flex-direction:column;gap:0.3rem;}
.sent-row{display:flex;align-items:center;gap:0.5rem;font-family:var(--mono);font-size:0.58rem;}
.sent-tf{color:var(--dim);letter-spacing:0.12em;width:1.8rem;}
.sent-badge{padding:0.15rem 0.45rem;border-radius:2px;font-weight:700;font-size:0.6rem;letter-spacing:0.1em;}
.sent-bull{color:var(--green);border:1px solid var(--green);background:rgba(0,229,160,0.08);}
.sent-bear{color:var(--red);border:1px solid var(--red);background:rgba(255,61,107,0.08);}
.sent-neut{color:var(--gold);border:1px solid var(--gold);background:rgba(245,166,35,0.08);}
.live-ind{display:flex;align-items:center;gap:0.4rem;font-family:var(--mono);
  font-size:0.62rem;letter-spacing:0.12em;color:var(--green);}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--green);
  box-shadow:0 0 8px var(--green);animation:blink 1.5s ease-in-out infinite;}
@keyframes blink{0%,100%{opacity:1;}50%{opacity:0.2;}}
.last-upd{font-family:var(--mono);text-align:right;line-height:1.7;white-space:nowrap;}
.last-upd .lu-label{font-size:0.4rem;color:var(--dim);letter-spacing:0.2em;text-transform:uppercase;display:block;}
.last-upd .lu-date{font-size:0.56rem;color:var(--text);display:block;font-weight:600;}
.last-upd .lu-times{font-size:0.48rem;color:var(--mid);display:block;letter-spacing:0.05em;}
.refresh-bar{display:flex;align-items:center;gap:0.4rem;font-family:var(--mono);
  font-size:0.44rem;color:var(--dim);letter-spacing:0.1em;justify-content:flex-end;margin-top:0.2rem;}

/* ── METRIC CARDS ── */
.metric-row{display:grid;grid-template-columns:repeat(3,1fr);gap:0.6rem;margin-bottom:0.6rem;}
.mcard{background:#131c27;border:1px solid #243040;border-radius:5px;
  padding:0.65rem 1rem;position:relative;overflow:hidden;}
.mcard::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--ac,var(--border2));}
.mcard-label{font-family:var(--mono);font-size:0.52rem;letter-spacing:0.16em;
  text-transform:uppercase;color:var(--dim);margin-bottom:0.25rem;}
.mcard-value{font-family:var(--mono);font-size:1.1rem;font-weight:700;
  color:var(--text);letter-spacing:-0.02em;line-height:1;}
.mcard-sub{font-family:var(--mono);font-size:0.52rem;color:var(--dim);margin-top:0.25rem;}
.val-green{color:var(--green)!important;}.val-red{color:var(--red)!important;}
.val-gold{color:var(--gold)!important;}.val-blue{color:var(--blue)!important;}
.val-purple{color:var(--purple)!important;}
.cd-inline{display:flex;align-items:stretch;gap:0;margin-top:0.5rem;}
.cd-block{flex:1;text-align:center;padding:0.35rem 0.2rem;
  background:var(--bg3);border:1px solid var(--border);}
.cd-block:first-child{border-radius:3px 0 0 3px;}
.cd-block:last-child{border-radius:0 3px 3px 0;}
.cd-block:not(:last-child){border-right:none;}
.cd-tf{font-family:var(--mono);font-size:0.46rem;color:var(--dim);
  letter-spacing:0.15em;text-transform:uppercase;margin-bottom:0.12rem;}
.cd-val{font-family:var(--mono);font-size:0.78rem;color:var(--gold);font-weight:700;line-height:1;}

/* ── SECTION TITLE ── */
.sec-title{font-family:var(--mono);font-size:0.6rem;letter-spacing:0.2em;
  text-transform:uppercase;color:var(--dim);padding-bottom:0.6rem;
  border-bottom:1px solid var(--border);margin-bottom:0.9rem;
  display:flex;align-items:center;gap:0.5rem;}
.sec-title::before{content:'▸';color:var(--green);font-size:0.65rem;}

/* ── TABLES ── */
.trow{display:grid;gap:0.4rem;padding:0.55rem 0.7rem;border-bottom:1px solid var(--border);
  font-family:var(--mono);font-size:0.68rem;align-items:center;}
.trow:hover{background:var(--bg3);}
.th{background:rgba(30,42,56,0.7)!important;color:var(--mid)!important;
  font-size:0.55rem!important;letter-spacing:0.14em;text-transform:uppercase;
  border-bottom:2px solid var(--border2)!important;padding:0.5rem 0.7rem!important;
  position:sticky;top:0;z-index:1;}
.dl{color:var(--green);font-weight:700;}.ds{color:var(--red);font-weight:700;}
.pp{color:var(--green);}.pn{color:var(--red);}
.scr{max-height:300px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;}
.scr::-webkit-scrollbar{width:6px;}
.scr::-webkit-scrollbar-track{background:var(--bg2);border-radius:3px;}
.scr::-webkit-scrollbar-thumb{background:#3a4a5a;border-radius:3px;}
.scr::-webkit-scrollbar-thumb:hover{background:var(--blue);}
.sb{grid-template-columns:2.2fr 0.7fr 0.7fr 0.8fr 1fr 0.9fr 0.9fr;}
.rp{grid-template-columns:2.2fr 0.7fr 0.9fr 0.7fr 1fr 1.2fr;}
.sa{grid-template-columns:1.3fr 1.8fr 2.2fr 0.55fr 0.6fr 0.75fr 0.75fr 0.65fr;}
.sdot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px;flex-shrink:0;}
.ra{color:var(--green);font-weight:800;font-size:0.85rem;}
.rb{color:var(--blue);font-weight:800;font-size:0.85rem;}
.rc{color:var(--gold);font-weight:800;font-size:0.85rem;}
.si{background:var(--bg3);border:1px solid var(--border);border-radius:3px;padding:0.6rem 0.7rem;}
.sl{font-family:var(--mono);font-size:0.54rem;color:var(--dim);letter-spacing:0.12em;margin-bottom:0.25rem;}
.sv{font-family:var(--mono);font-size:0.82rem;font-weight:700;}
.empty{padding:1.8rem;text-align:center;color:#2a3548;font-family:var(--mono);
  font-size:0.68rem;letter-spacing:0.15em;border:1px dashed #1e2a38;border-radius:4px;}

/* ── EQUITY CURVE ── */
div[data-testid="stRadio"] > label { display:none!important; }
div[data-testid="stRadio"] > div {
    gap:0!important;width:100%!important;flex-wrap:nowrap!important;margin:0!important;padding:0!important;
}
div[data-testid="stRadio"] > div > label {
    flex:1 1 0!important;background:transparent!important;
    border-top:1px solid #1e2a38!important;border-bottom:1px solid #1e2a38!important;
    border-left:1px solid #1e2a38!important;border-right:none!important;
    border-radius:0!important;padding:0!important;height:1.8rem!important;line-height:1.8rem!important;
    font-family:'JetBrains Mono',monospace!important;font-size:0.55rem!important;
    letter-spacing:0.07em!important;color:#6b7a8d!important;cursor:pointer!important;
    white-space:nowrap!important;margin:0!important;
    display:flex!important;align-items:center!important;justify-content:center!important;
}
div[data-testid="stRadio"] > div > label:first-child{border-radius:3px 0 0 3px!important;}
div[data-testid="stRadio"] > div > label:last-child{border-right:1px solid #1e2a38!important;border-radius:0 3px 3px 0!important;}
div[data-testid="stRadio"] > div > label:hover{background:rgba(255,255,255,0.03)!important;color:#9aa5b4!important;}
div[data-testid="stRadio"] > div > label:has(input:checked){
    background:rgba(0,229,160,0.10)!important;border-color:#00e5a0!important;
    color:#00e5a0!important;font-weight:700!important;z-index:1!important;
}
div[data-testid="stRadio"] > div > label > div:first-child{display:none!important;}
div[data-testid="stDateInput"]{
    display:flex!important;align-items:center!important;gap:0!important;
    height:1.8rem!important;border:1px solid #1e2a38!important;border-radius:3px!important;
    overflow:hidden!important;background:transparent!important;padding:0!important;margin:0!important;
}
div[data-testid="stDateInput"] > label{
    display:flex!important;align-items:center!important;height:1.8rem!important;
    padding:0 0.45rem!important;font-family:'JetBrains Mono',monospace!important;
    font-size:0.46rem!important;letter-spacing:0.14em!important;text-transform:uppercase!important;
    color:#2a3548!important;white-space:nowrap!important;background:transparent!important;
    border-right:1px solid #1e2a38!important;margin:0!important;flex-shrink:0!important;
}
div[data-testid="stDateInput"] > div{flex:1!important;height:1.8rem!important;min-height:1.8rem!important;padding:0!important;margin:0!important;}
div[data-testid="stDateInput"] > div > div,
div[data-testid="stDateInput"] [data-baseweb="form-control"]{height:1.8rem!important;padding:0!important;margin:0!important;}
div[data-testid="stDateInput"] [data-baseweb="input"]{
    background:transparent!important;background-color:transparent!important;
    border:none!important;height:1.8rem!important;min-height:1.8rem!important;
    padding:0!important;box-shadow:none!important;
}
div[data-testid="stDateInput"] [data-baseweb="base-input"]{
    background:transparent!important;height:1.8rem!important;min-height:1.8rem!important;padding:0!important;
}
div[data-testid="stDateInput"] input{
    background:transparent!important;color:#9aa5b4!important;
    font-family:'JetBrains Mono',monospace!important;font-size:0.55rem!important;
    padding:0 0.4rem!important;height:1.8rem!important;border:none!important;
    outline:none!important;caret-color:#00e5a0!important;
}
div[data-testid="stDateInput"] input::placeholder{color:#2a3548!important;}
div[data-testid="stDateInput"] svg{fill:#2a3548!important;width:11px!important;height:11px!important;}
div[data-testid="stDateInput"] button{background:transparent!important;border:none!important;padding:0 0.25rem!important;}
div[data-testid="stDateInput"]:focus-within{border-color:#00e5a0!important;}
[data-testid="column"]{padding-top:0!important;padding-bottom:0!important;}
div[data-testid="stRadio"]{margin:0!important;padding:0!important;}
div[data-testid="stPlotlyChart"]{margin-top:0.2rem!important;}
div[data-testid="stPlotlyChart"] > div{background:transparent!important;}

@media(max-width:900px){
  .metric-row{grid-template-columns:repeat(2,1fr)!important;}
  .sb,.rp,.sa{grid-template-columns:1fr 1fr!important;}
  .apex-logo{font-size:2.8rem!important;}
}
@media(max-width:500px){
  .metric-row{grid-template-columns:repeat(2,1fr)!important;}
  .apex-logo{font-size:2.2rem!important;}
}
</style>
""", unsafe_allow_html=True)


# =============================================================================
# HELPERS
# =============================================================================

def get_tc(): return sqlite3.connect(DB["trades"], check_same_thread=False)
def get_ac(): return sqlite3.connect(DB["apex"],   check_same_thread=False)

def sq(conn, q, p=None):
    try: return pd.read_sql_query(q, conn, params=p)
    except: return pd.DataFrame()

def get_capital(conn):
    try:
        r = conn.execute("SELECT capital FROM portfolio ORDER BY id DESC LIMIT 1").fetchone()
        if r: return float(r[0])
        r = conn.execute("SELECT capital FROM bot_state ORDER BY id DESC LIMIT 1").fetchone()
        return float(r[0]) if r else INITIAL_CAPITAL
    except: return INITIAL_CAPITAL

def next_candle(tf):
    now = datetime.now(timezone.utc)
    if tf=="1h":   nxt = now.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
    elif tf=="4h":
        h4 = (now.hour//4+1)*4
        nxt = now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(hours=h4)
    else: nxt = (now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    d = nxt - now
    h,r = divmod(int(d.total_seconds()),3600); m,s = divmod(r,60)
    return f"{h}h{m:02d}m" if h>0 else f"{m}m{s:02d}s"

def get_sentiment(conn):
    out = {"1H":"NEUT","4H":"NEUT","1D":"NEUT"}
    try:
        from bot.data_feed import fetch_ohlcv
        for tf, lbl in [("1h","1H"),("4h","4H"),("1d","1D")]:
            df = fetch_ohlcv("BTC/USDT:USDT", tf, limit=100)
            if df is None or df.empty or len(df) < 20: continue
            c = df["close"].values
            last     = float(c[-1])
            ema_fast = float(pd.Series(c).ewm(span=9,  adjust=False).mean().iloc[-1])
            ema_slow = float(pd.Series(c).ewm(span=21, adjust=False).mean().iloc[-1])
            ema_200  = float(pd.Series(c).ewm(span=min(50,len(c)), adjust=False).mean().iloc[-1])
            score = 0
            if ema_fast > ema_slow: score += 1
            else: score -= 1
            if last > ema_fast: score += 1
            else: score -= 1
            if last > ema_200: score += 1
            else: score -= 1
            if score >= 2:    out[lbl] = "BULL"
            elif score <= -2: out[lbl] = "BEAR"
    except:
        try:
            for tf,lbl in [("1h","1H"),("4h","4H"),("1d","1D")]:
                df = sq(conn, f"SELECT close FROM ohlcv WHERE symbol='BTC/USDT:USDT' AND timeframe='{tf}' ORDER BY timestamp DESC LIMIT 100")
                if df.empty or len(df)<20: continue
                c = df["close"].values[::-1]
                last     = float(c[-1])
                ema_fast = float(pd.Series(c).ewm(span=9,  adjust=False).mean().iloc[-1])
                ema_slow = float(pd.Series(c).ewm(span=21, adjust=False).mean().iloc[-1])
                ema_200  = float(pd.Series(c).ewm(span=min(50,len(c)), adjust=False).mean().iloc[-1])
                score = 0
                if ema_fast > ema_slow: score += 1
                else: score -= 1
                if last > ema_fast: score += 1
                else: score -= 1
                if last > ema_200: score += 1
                else: score -= 1
                if score >= 2:    out[lbl] = "BULL"
                elif score <= -2: out[lbl] = "BEAR"
        except: pass
    return out

COLORS=["#00e5a0","#58a6ff","#bc8cff","#f5a623","#ff3d6b","#00bcd4","#ff9800","#e91e63","#9c27b0","#4caf50"]


# =============================================================================
# LOAD DATA
# =============================================================================

tc = get_tc(); ac = get_ac()
capital    = get_capital(tc)
open_tr    = sq(tc,"SELECT * FROM trades WHERE status='open' ORDER BY entry_time DESC")
closed_tr  = sq(tc,"SELECT * FROM trades WHERE status='closed' ORDER BY exit_time DESC")
# FIX 2026-04-10 audit C-α: was `source as strategy` which displayed assignment origin
# ('rebalance'/'test'/'backtester') instead of strategy name. dashboard2.py was correct.
strategies = sq(ac,"""SELECT symbol,COALESCE(strategy_name,source) as strategy,timeframe,tier,win_rate,
                       expectancy,profit_factor,val_trades,
                       COALESCE(indicator_combo,'') as indicator_combo
                       FROM strategy_assignments WHERE is_active=1 ORDER BY win_rate DESC""")
sentiment  = get_sentiment(ac)

pnl_ok   = not closed_tr.empty and "pnl_usdt" in closed_tr.columns
total_tr = len(closed_tr)
open_cnt = len(open_tr)
pnl_tot  = closed_tr["pnl_usdt"].sum() if pnl_ok else 0.0
wins     = closed_tr[closed_tr["pnl_usdt"]>=0] if pnl_ok else pd.DataFrame()
losses   = closed_tr[closed_tr["pnl_usdt"]<0]  if pnl_ok else pd.DataFrame()
wr       = len(wins)/total_tr*100 if total_tr>0 else 0.0
pnl_pct  = (capital-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
dd_pct   = max(0,(INITIAL_CAPITAL-capital)/INITIAL_CAPITAL*100) if capital<INITIAL_CAPITAL else 0.0

now_utc    = datetime.now(timezone.utc)
gst_offset = timedelta(hours=4)
now_gst    = now_utc + gst_offset

bot_alive = False
try:
    lp = Path(__file__).parent / "logs" / "bot.log"
    if lp.exists():
        age_mins = (now_utc.timestamp() - os.path.getmtime(lp)) / 60
        bot_alive = age_mins < 90
except: bot_alive = False

today_str = now_utc.strftime("%Y-%m-%d")
if pnl_ok and "exit_time" in closed_tr.columns:
    today_trades = closed_tr[closed_tr["exit_time"].astype(str).str.startswith(today_str)]
    today_pnl = today_trades["pnl_usdt"].sum() if not today_trades.empty else 0.0
else:
    today_pnl = 0.0

unreal_pnl = 0.0
cur_prices_cached = {}
if not open_tr.empty:
    try:
        import time
        now_ts = time.time()
        cache_age = now_ts - st.session_state.get("prices_ts", 0)
        if cache_age > 60 or "prices_cache" not in st.session_state:
            from bot.data_feed import fetch_current_prices
            st.session_state["prices_cache"] = fetch_current_prices(open_tr["symbol"].tolist())
            st.session_state["prices_ts"] = now_ts
        cur_prices_cached = st.session_state.get("prices_cache", {})
        for _, row in open_tr.iterrows():
            sym       = row.get("symbol","")
            direction = str(row.get("direction","")).lower()
            entry     = float(row.get("avg_entry_price", row.get("entry_price", 0)) or 0)
            qty       = float(row.get("quantity_remaining", row.get("quantity", 0)) or 0)
            leverage  = float(row.get("leverage", 1) or 1)
            cur_price = cur_prices_cached.get(sym, entry)
            if direction == "long": unreal_pnl += (cur_price - entry) * qty * leverage
            else:                   unreal_pnl += (entry - cur_price) * qty * leverage
    except: unreal_pnl = 0.0

def get_live_price(symbol: str):
    return cur_prices_cached.get(symbol)

avg_win  = wins["pnl_usdt"].mean()        if not wins.empty   else 0
avg_loss = abs(losses["pnl_usdt"].mean()) if not losses.empty else 0
live_pf  = (avg_win*len(wins))/(avg_loss*len(losses)) if avg_loss>0 and len(losses)>0 else (999.0 if len(wins)>0 else 0.0)

sent_rows = ""
for k,v in sentiment.items():
    cls = "sent-bull" if v=="BULL" else "sent-bear" if v=="BEAR" else "sent-neut"
    sent_rows += f'<div class="sent-row"><span class="sent-tf">{k}</span><span class="sent-badge {cls}">{v}</span></div>'

st.markdown(f"""
<div class="apex-header">
  <div>
    <div class="apex-logo">APEX</div>
    <div class="apex-subtitle">AI Trading Bot</div>
  </div>
  <div class="header-right">
    <div class="live-ind">
      <div class="live-dot" style="background:{'var(--green)' if bot_alive else 'var(--red)'};box-shadow:0 0 8px {'var(--green)' if bot_alive else 'var(--red)'}"></div>
      <span style="color:{'var(--green)' if bot_alive else 'var(--red)'}">{'LIVE' if bot_alive else 'OFFLINE'}</span>
    </div>
    <div class="sentiment-col">{sent_rows}</div>
    <div>
      <div class="last-upd">
        <span class="lu-label">Last Updated</span>
        <span class="lu-date">{now_utc.strftime("%d %b %Y")}</span>
        <span class="lu-times">{now_utc.strftime("%H:%M")} UTC &nbsp;·&nbsp; {now_gst.strftime("%H:%M")} GST</span>
      </div>
      <div class="refresh-bar">AUTO-REFRESH&nbsp;<span style="color:var(--gold);font-weight:700">30s</span></div>
    </div>
  </div>
</div>
<script>
(function(){{
  var prevOpen={open_cnt}; var prevClosed={total_tr};
  function beep(){{
    try{{
      var ctx=new(window.AudioContext||window.webkitAudioContext)();
      var o=ctx.createOscillator(); var g=ctx.createGain();
      o.connect(g); g.connect(ctx.destination);
      o.frequency.value=880; o.type='sine';
      g.gain.setValueAtTime(0.3,ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.4);
      o.start(ctx.currentTime); o.stop(ctx.currentTime+0.4);
    }}catch(e){{}}
  }}
  var sO=parseInt(sessionStorage.getItem('apexOpen')||'-1');
  var sC=parseInt(sessionStorage.getItem('apexClosed')||'-1');
  if(sO>=0&&(prevOpen!==sO||prevClosed!==sC)){{beep();}}
  sessionStorage.setItem('apexOpen',prevOpen);
  sessionStorage.setItem('apexClosed',prevClosed);
  var s=30; var el=document.getElementById('rc');
  var t=setInterval(function(){{s--;if(el)el.textContent=s;if(s<=0){{clearInterval(t);window.location.reload();}}}},1000);
}})();
</script>
""", unsafe_allow_html=True)


# =============================================================================
# METRIC CARDS
# =============================================================================

pc   = "val-green" if pnl_tot>=0 else "val-red"
ps   = "+" if pnl_tot>=0 else ""
wc   = "val-green" if wr>=60 else "val-gold" if wr>=40 else "val-red"
cd1  = next_candle("1h"); cd4 = next_candle("4h"); cd1d = next_candle("1d")
tpc  = "val-green" if today_pnl>=0 else "val-red"
tps  = "+" if today_pnl>=0 else ""
upc  = "val-green" if unreal_pnl>=0 else "val-red"
ups  = "+" if unreal_pnl>=0 else ""
pfc  = "val-green" if live_pf>=1.5 else "val-gold" if live_pf>=1 else "val-red"
live_pf_str = "∞" if live_pf>=999 else f"{live_pf:.2f}"

st.markdown(f"""
<div class="metric-row" style="grid-template-columns:repeat(4,1fr);margin-bottom:0.6rem">
  <div class="mcard" style="--ac:var(--green)">
    <div class="mcard-label">Portfolio Value</div>
    <div class="mcard-value val-green">${capital:,.2f}</div>
    <div class="mcard-sub">Initial ${INITIAL_CAPITAL:,.0f}</div>
  </div>
  <div class="mcard" style="--ac:{'var(--green)' if today_pnl>=0 else 'var(--red)'}">
    <div class="mcard-label">Today's P&L</div>
    <div class="mcard-value {tpc}">{tps}${today_pnl:,.2f}</div>
    <div class="mcard-sub">Realised today</div>
  </div>
  <div class="mcard" style="--ac:{'var(--green)' if pnl_tot>=0 else 'var(--red)'}">
    <div class="mcard-label">Total P&L</div>
    <div class="mcard-value {pc}">{ps}${pnl_tot:,.2f}</div>
    <div class="mcard-sub">{ps}{pnl_pct:.2f}% return</div>
  </div>
  <div class="mcard" style="--ac:{'var(--green)' if unreal_pnl>=0 else 'var(--red)'}">
    <div class="mcard-label">Unrealized P&L</div>
    <div class="mcard-value {upc}">{ups}${unreal_pnl:,.2f}</div>
    <div class="mcard-sub">Open positions</div>
  </div>
</div>
<div class="metric-row" style="grid-template-columns:repeat(4,1fr);margin-bottom:1.4rem">
  <div class="mcard" style="--ac:var(--blue)">
    <div class="mcard-label">Open Positions</div>
    <div class="mcard-value val-blue">{open_cnt}</div>
    <div class="mcard-sub">{total_tr} closed total</div>
  </div>
  <div class="mcard" style="--ac:{'var(--green)' if wr>=60 else 'var(--gold)'}">
    <div class="mcard-label">Win Rate</div>
    <div class="mcard-value {wc}">{wr:.1f}%</div>
    <div class="mcard-sub">{len(wins)}/{total_tr} won</div>
  </div>
  <div class="mcard" style="--ac:{'var(--green)' if live_pf>=1.5 else 'var(--gold)'}">
    <div class="mcard-label">Live Profit Factor</div>
    <div class="mcard-value {pfc}">{live_pf_str}</div>
    <div class="mcard-sub">Gross win / gross loss</div>
  </div>
  <div class="mcard" style="--ac:var(--gold)">
    <div class="mcard-label">Next Candle Close</div>
    <div class="cd-inline">
      <div class="cd-block"><div class="cd-tf">1H</div><div class="cd-val">{cd1}</div></div>
      <div class="cd-block"><div class="cd-tf">4H</div><div class="cd-val">{cd4}</div></div>
      <div class="cd-block"><div class="cd-tf">1D</div><div class="cd-val">{cd1d}</div></div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)


# =============================================================================
# EQUITY CURVE (inline)
# =============================================================================

def render_equity_curve(tc, get_live_price=None, unreal_pnl=0.0):
    st.markdown('<div class="sec-title">Equity Curve</div>', unsafe_allow_html=True)

    c_period, c_from, c_to = st.columns([5.6, 2.1, 2.1])
    with c_period:
        period = st.radio("", ["This Week","Last Week","This Month","This Year","All"],
                          horizontal=True, key="eq_period", label_visibility="collapsed")

    today = now_utc.date()
    if period == "This Week":
        default_from = today - timedelta(days=today.weekday()); default_to = today
    elif period == "Last Week":
        w_end = today - timedelta(days=today.weekday())
        default_from = w_end - timedelta(days=7); default_to = w_end - timedelta(days=1)
    elif period == "This Month":
        default_from = today.replace(day=1); default_to = today
    elif period == "This Year":
        default_from = today.replace(month=1, day=1); default_to = today
    else:
        default_from = today - timedelta(days=365); default_to = today

    with c_from:
        date_from = st.date_input("FROM", value=default_from, key="eq_from", format="DD/MM/YYYY")
    with c_to:
        date_to = st.date_input("TO", value=default_to, key="eq_to", format="DD/MM/YYYY")

    try:
        df = pd.read_sql_query("SELECT exit_time, pnl_usdt FROM trades WHERE status='closed' AND exit_time IS NOT NULL ORDER BY exit_time ASC", tc)
    except: df = pd.DataFrame()

    has_trades = not df.empty and "pnl_usdt" in df.columns and len(df) > 0
    if has_trades:
        df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True, errors="coerce")
        df = df.dropna(subset=["exit_time"]).sort_values("exit_time").reset_index(drop=True)
        df["equity"] = INITIAL_CAPITAL + df["pnl_usdt"].cumsum()
        t0 = df["exit_time"].iloc[0] - timedelta(minutes=30)
        start = pd.DataFrame([{"exit_time": t0, "pnl_usdt": 0.0, "equity": float(INITIAL_CAPITAL)}])
        df = pd.concat([start, df], ignore_index=True)
    else:
        df = pd.DataFrame([
            {"exit_time": pd.Timestamp(now_utc - timedelta(hours=6)), "equity": float(INITIAL_CAPITAL)},
            {"exit_time": pd.Timestamp(now_utc), "equity": float(INITIAL_CAPITAL)},
        ])

    try:
        dt_from = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
        dt_to   = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
        fdf = df[(df["exit_time"] >= dt_from) & (df["exit_time"] <= dt_to)]
    except: fdf = df.copy()

    if fdf.empty or len(fdf) < 2: fdf = df.copy()
    if fdf.empty or len(fdf) < 2:
        fdf = pd.DataFrame([
            {"exit_time": pd.Timestamp(now_utc - timedelta(hours=1)), "equity": float(INITIAL_CAPITAL)},
            {"exit_time": pd.Timestamp(now_utc), "equity": float(INITIAL_CAPITAL)},
        ])

    xs = fdf["exit_time"].tolist()
    ys = [float(v) for v in fdf["equity"].tolist()]
    last_eq = ys[-1]
    is_up = last_eq >= INITIAL_CAPITAL
    line_col = "#00e5a0" if is_up else "#ff3d6b"
    fill_col = "rgba(0,229,160,0.08)" if is_up else "rgba(255,61,107,0.08)"
    y_span = max(max(ys) - min(ys), 50.0)
    y_lo = min(ys) - y_span * 0.15
    y_hi = max(ys) + y_span * 0.10

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=[y_lo]*len(xs), mode="lines",
        line=dict(width=0, color="rgba(0,0,0,0)"), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines",
        line=dict(color=line_col, width=2, shape="spline", smoothing=0.3),
        fill="tonexty", fillcolor=fill_col,
        hovertemplate="<b>%{x|%d %b %H:%M UTC}</b><br><b>$%{y:,.2f}</b><extra></extra>",
        showlegend=False))
    if y_lo < INITIAL_CAPITAL < y_hi:
        fig.add_hline(y=INITIAL_CAPITAL, line=dict(color="#1e2a38", width=1, dash="dot"))
    fig.add_trace(go.Scatter(x=[xs[-1]], y=[last_eq], mode="markers",
        marker=dict(color=line_col, size=9, symbol="circle", line=dict(color="#0d1117", width=2)),
        hovertemplate=f"<b>Current: ${last_eq:,.2f}</b><extra></extra>", showlegend=False))
    fig.update_layout(
        height=265, margin=dict(l=60, r=20, t=6, b=38),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="JetBrains Mono, monospace", size=10, color="#6b7a8d"),
        showlegend=False, hovermode="x unified",
        hoverlabel=dict(bgcolor="#0d1117", bordercolor="#1e2a38",
            font=dict(family="JetBrains Mono, monospace", size=11, color="#d8e0e8")),
        xaxis=dict(showgrid=False, showline=False, zeroline=False,
            tickfont=dict(size=9, color="#6b7a8d", family="JetBrains Mono, monospace"),
            tickformat="%m-%d %H:%M", nticks=8, tickangle=0,
            tickcolor="#1e2a38", ticks="outside", ticklen=3),
        yaxis=dict(showgrid=True, gridcolor="rgba(30,42,56,0.5)", gridwidth=1,
            showline=False, zeroline=False,
            tickfont=dict(size=9, color="#6b7a8d", family="JetBrains Mono, monospace"),
            tickprefix="$", tickformat=",.0f", range=[y_lo, y_hi], side="left",
            tickcolor="rgba(0,0,0,0)"),
        dragmode=False)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False,"staticPlot":False,"scrollZoom":False})

with st.container(border=True):
    render_equity_curve(tc, get_live_price, unreal_pnl)


# =============================================================================
# OPEN POSITIONS
# =============================================================================

with st.container(border=True):
    st.markdown(f'<div class="sec-title">Open Positions <span style="color:var(--dim);font-size:0.52rem;font-weight:400;margin-left:0.5rem">· {open_cnt} Active</span></div>', unsafe_allow_html=True)
    if not open_tr.empty:
        cur_prices = cur_prices_cached if cur_prices_cached else {}
        strat_map = {}
        if not strategies.empty:
            for _, sr in strategies.iterrows():
                strat_map[sr["symbol"]] = sr
        rows = ""; total_unreal = 0.0
        for _, row in open_tr.iterrows():
            sym      = str(row.get("symbol","")).replace("/USDT:USDT","")
            full_sym = str(row.get("symbol",""))
            d        = str(row.get("direction","")).upper()
            dc       = "dl" if d == "LONG" else "ds"
            tf       = str(row.get("timeframe","")).upper()
            tier     = str(row.get("tier","tier2"))
            e        = float(row.get("avg_entry_price", row.get("entry_price",0)) or 0)
            sl       = float(row.get("trailing_sl", row.get("stop_loss",0)) or 0)
            qty      = float(row.get("quantity_remaining", row.get("quantity",0)) or 0)
            lev      = float(row.get("leverage",1) or 1)
            bars     = int(row.get("candles_open",0) or 0)
            cur      = cur_prices.get(full_sym, e)
            # FIX 2026-04-10 audit C-η: qty already includes leverage (capital_manager.calculate_position_size
            # bakes leverage into qty per gotcha #4). Multiplying by lev was double-counting and inflating
            # the displayed dollar PnL by `lev` times. up_pct then divided by leveraged notional which
            # cancelled the error by accident. Both fixed: qty-only PnL, margin-relative %.
            if d == "LONG": up = (cur - e) * qty
            else:           up = (e - cur) * qty
            total_unreal += up
            margin = (e * qty / lev) if lev > 0 else (e * qty)
            up_pct = (up / margin) * 100 if margin > 0 else 0
            uc = "val-green" if up >= 0 else "val-red"
            us = "+" if up >= 0 else ""
            rating_map = {"tier1":"A","tier2":"B","tier3":"C"}
            rating     = rating_map.get(tier,"B")
            rating_col = {"A":"var(--green)","B":"var(--blue)","C":"var(--gold)"}.get(rating,"var(--mid)")
            tier_label = tier.replace("tier","T")
            tier_col   = {"tier1":"var(--green)","tier2":"var(--blue)","tier3":"var(--purple)"}.get(tier,"var(--mid)")
            sr_info    = strat_map.get(full_sym, {})
            if hasattr(sr_info, "get"):
                strat_name = str(sr_info.get("strategy","—")).split(" + ")[0][:16]
                indicators = str(sr_info.get("indicator_combo","—"))[:28]
            else:
                strat_name = "—"; indicators = "—"
            sl_pct = abs(sl - e) / e * 100 if sl > 0 and e > 0 else 0
            try:
                et_raw = str(row.get("entry_time",""))
                if et_raw and et_raw != "None":
                    dt = datetime.fromisoformat(et_raw.replace("Z","+00:00"))
                    entry_dt = dt.strftime("%d %b %H:%M")
                else: entry_dt = "—"
            except: entry_dt = "—"
            from bot.trade_manager import TIME_STOP_HOURS
            from datetime import datetime as _dt, timezone as _tz
            _tf_candle_h = {"1h": 1, "4h": 4, "1d": 24}
            _entry_str   = row.get("entry_time", "")
            try:
                _entry_dt  = _dt.fromisoformat(_entry_str)
                _elapsed_h = (_dt.now(_tz.utc) - _entry_dt).total_seconds() / 3600
            except Exception:
                _elapsed_h = 0
            _limit_h   = TIME_STOP_HOURS.get(tf.lower(), 30)
            _ch        = _tf_candle_h.get(tf.lower(), 1)
            bars_left  = max(0, int((_limit_h - _elapsed_h) / _ch))
            bl_color   = "var(--red)" if bars_left <= 1 else "var(--gold)" if bars_left <= 3 else "var(--dim)"
            rows += f"""<div class="op-row">
                <span style="color:var(--text);font-weight:700">{sym}</span>
                <span><span class="{dc}" style="padding:0.1rem 0.4rem;border-radius:3px;font-size:0.58rem">{d.title()}</span></span>
                <span style="color:{rating_col};font-weight:800;font-size:0.85rem">{rating}</span>
                <span style="color:{tier_col};font-weight:600;font-size:0.62rem">{tier_label}</span>
                <span style="color:var(--blue);font-size:0.6rem;font-weight:600">{strat_name}</span>
                <span style="color:var(--gold);font-weight:700;font-size:0.62rem">{tf}</span>
                <span style="color:var(--mid);font-size:0.52rem">{indicators}</span>
                <span style="color:var(--mid);font-size:0.62rem">{entry_dt}</span>
                <span style="color:#c8d8e8;font-size:0.64rem;font-weight:600">${e:,.4f}</span>
                <span style="color:var(--mid)">${sl:,.4f} <span style="font-size:0.52rem;color:var(--dim)">({sl_pct:.0f}%)</span></span>
                <span style="color:var(--mid)">${cur:,.4f}</span>
                <span class="{uc}" style="font-weight:600">{us}${up:,.2f} <span style="font-size:0.55rem">({us}{up_pct:.1f}%)</span></span>
                <span style="color:var(--dim)">{bars}</span>
                <span style="color:{bl_color};font-weight:600">{bars_left}</span>
            </div>"""
        tu_c = "val-green" if total_unreal >= 0 else "val-red"
        tu_s = "+" if total_unreal >= 0 else ""
        st.markdown(f"""
        <style>
        .op-wrap{{width:100%;height:295px;overflow-y:auto;overflow-x:hidden;
            border:1px solid #1e2a38;border-radius:4px;background:#0d1117;}}
        .op-wrap::-webkit-scrollbar{{width:8px;}}
        .op-wrap::-webkit-scrollbar-track{{background:#0d1117;}}
        .op-wrap::-webkit-scrollbar-thumb{{background:#3a4a5a;border-radius:4px;}}
        .op-wrap::-webkit-scrollbar-thumb:hover{{background:#58a6ff;}}
        .op-head,.op-row{{display:grid;
            grid-template-columns:0.8fr 0.6fr 0.5fr 0.4fr 0.9fr 0.45fr 1.3fr 0.8fr 0.9fr 0.8fr 1.1fr 0.9fr 0.5fr 0.5fr;
            padding:0.5rem 0.8rem;border-bottom:1px solid #1e2a38;
            font-family:'JetBrains Mono',monospace;align-items:center;
            width:100%;box-sizing:border-box;}}
        .op-head{{background:#0f1923;color:#6b7a8d;font-size:0.52rem;
            letter-spacing:0.12em;text-transform:uppercase;
            border-bottom:2px solid #243040;position:sticky;top:0;z-index:10;}}
        .op-row{{font-size:0.64rem;}}
        .op-row:hover{{background:#111820;}}
        </style>
        <div class="op-wrap">
            <div class="op-head">
                <span>COIN</span><span>SIDE</span><span>RATING</span><span>TIER</span>
                <span>STRATEGY</span><span>TF</span><span>INDICATORS</span>
                <span>OPENED</span><span>ENTRY</span><span>STOP</span>
                <span>CURRENT</span><span>P&amp;L</span><span>BARS</span><span>LEFT</span>
            </div>
            {rows}
        </div>
        <div style="text-align:right;padding:0.4rem 0.8rem;font-family:var(--mono);
            font-size:0.58rem;color:var(--dim)">
            Unrealized: <span class="{tu_c}" style="font-weight:700">{tu_s}${total_unreal:,.2f}</span>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty">NO OPEN POSITIONS — WAITING FOR SIGNALS</div>', unsafe_allow_html=True)


# =============================================================================
# RECENT CLOSED TRADES
# =============================================================================

with st.container(border=True):
    st.markdown('<div class="sec-title">Recent Closed Trades</div>', unsafe_allow_html=True)
    if pnl_ok and total_tr > 0:
        strat_map = {}
        if not strategies.empty:
            for _, sr in strategies.iterrows():
                strat_map[sr["symbol"]] = sr
        reason_styles = {
            "stop_loss":       ("#ff3d6b","rgba(255,61,107,0.12)","SL HIT"),
            "take_profit":     ("#00e5a0","rgba(0,229,160,0.12)","TP HIT"),
            "partial_tp":      ("#00e5a0","rgba(0,229,160,0.08)","PARTIAL TP"),
            "time_stop":       ("#f5a623","rgba(245,166,35,0.12)","TIME STOP"),
            "circuit_breaker": ("#ff3d6b","rgba(255,61,107,0.12)","CIRCUIT BRK"),
            "emergency_stop":  ("#ff3d6b","rgba(255,61,107,0.12)","EMERGENCY"),
            "manual":          ("#58a6ff","rgba(88,166,255,0.12)","MANUAL"),
        }
        rows = ""
        for _, row in closed_tr.head(15).iterrows():
            sym      = str(row.get("symbol","")).replace("/USDT:USDT","")
            full_sym = str(row.get("symbol",""))
            d        = str(row.get("direction","")).upper()
            dc       = "dl" if d == "LONG" else "ds"
            tier     = str(row.get("tier","tier2"))
            e        = float(row.get("avg_entry_price", row.get("entry_price",0)) or 0)
            ex       = float(row.get("exit_price",0) or 0)
            tf_ct    = str(row.get("timeframe","—")).upper()
            rsn_raw  = str(row.get("exit_reason","")).lower().strip()
            rc,rbg,rlbl = reason_styles.get(rsn_raw, ("#6b7a8d","rgba(107,122,141,0.1)",rsn_raw.replace("_"," ").upper()))
            pnl      = float(row.get("pnl_usdt",0) or 0)
            pct      = float(row.get("pnl_pct",0) or 0)
            pc2      = "pp" if pnl >= 0 else "pn"
            sg       = "+" if pnl >= 0 else ""
            wl_label = "W" if pnl >= 0 else "L"
            wl_col   = "var(--green)" if pnl >= 0 else "var(--red)"
            rating_map = {"tier1":"A","tier2":"B","tier3":"C"}
            rating     = rating_map.get(tier,"B")
            rating_col = {"A":"var(--green)","B":"var(--blue)","C":"var(--gold)"}.get(rating,"var(--mid)")
            tier_label = tier.replace("tier","T")
            tier_col   = {"tier1":"var(--green)","tier2":"var(--blue)","tier3":"var(--purple)"}.get(tier,"var(--mid)")
            sr_info    = strat_map.get(full_sym, {})
            if hasattr(sr_info, "get"):
                strat_name = str(sr_info.get("strategy","—")).split(" + ")[0][:18]
                indicators = str(sr_info.get("indicator_combo","—"))[:30]
            else:
                strat_name = "—"; indicators = "—"
            try:
                def fmt_dt(s):
                    if not s or s == "None": return "—"
                    try:
                        dt = datetime.fromisoformat(s.replace("Z","+00:00"))
                        return dt.strftime("%d %b %H:%M")
                    except: return s[:13]
                entry_dt = fmt_dt(str(row.get("entry_time","")))
                exit_dt  = fmt_dt(str(row.get("exit_time","")))
            except: entry_dt = "—"; exit_dt = "—"
            reason_badge = (f'<span style="color:{rc};background:{rbg};padding:0.1rem 0.35rem;'
                           f'border-radius:2px;font-size:0.48rem;font-weight:700;'
                           f'letter-spacing:0.05em;border:1px solid {rc};white-space:nowrap;'
                           f'display:inline-block;max-width:95px;overflow:hidden;'
                           f'text-overflow:ellipsis">{rlbl}</span>')
            rows += (
                f'<div class="tr-r">'
                f'<span style="color:var(--text);font-weight:700">{sym}</span>'
                f'<span><span class="{dc}" style="padding:0.1rem 0.4rem;border-radius:3px;font-size:0.58rem">{d.title()}</span></span>'
                f'<span style="color:{rating_col};font-weight:800;font-size:0.85rem">{rating}</span>'
                f'<span style="color:{tier_col};font-weight:600;font-size:0.62rem">{tier_label}</span>'
                f'<span style="color:var(--blue);font-size:0.6rem;font-weight:600">{strat_name}</span>'
                f'<span style="color:var(--gold);font-weight:700;font-size:0.62rem">{tf_ct}</span>'
                f'<span style="color:var(--mid);font-size:0.52rem">{indicators}</span>'
                f'<span style="color:#c8d8e8;font-size:0.64rem;font-weight:600">${e:,.4f}</span>'
                f'<span style="color:#a8bfd0;font-size:0.64rem">${ex:,.4f}</span>'
                f'<span style="color:var(--mid);font-size:0.55rem;line-height:1.4">{entry_dt}</span>'
                f'<span style="color:var(--dim);font-size:0.55rem;line-height:1.4">{exit_dt}</span>'
                f'{reason_badge}'
                f'<span class="{pc2}" style="font-weight:600">{sg}${pnl:,.2f}</span>'
                f'<span class="{pc2}">{sg}{pct:.2f}%</span>'
                f'<span style="color:{wl_col};font-weight:800;font-size:0.65rem;letter-spacing:0.05em">{wl_label}</span>'
                f'</div>'
            )
        trades_html = f"""
        <style>
        .tw{{width:100%;height:440px;overflow-y:auto;overflow-x:hidden;
            border:1px solid #1e2a38;border-radius:4px;background:#0d1117;box-sizing:border-box;}}
        .tw::-webkit-scrollbar{{width:8px;}}
        .tw::-webkit-scrollbar-track{{background:#0d1117;}}
        .tw::-webkit-scrollbar-thumb{{background:#3a4a5a;border-radius:4px;}}
        .tw::-webkit-scrollbar-thumb:hover{{background:#58a6ff;}}
        .tr-h,.tr-r{{display:grid;
            grid-template-columns:0.9fr 0.6fr 0.5fr 0.4fr 0.9fr 0.45fr 1.3fr 0.8fr 0.8fr 1.0fr 1.0fr 0.9fr 0.5fr 0.8fr 0.6fr;
            padding:0.5rem 0.8rem;border-bottom:1px solid #1e2a38;
            font-family:'JetBrains Mono',monospace;align-items:center;
            width:100%;box-sizing:border-box;}}
        .tr-h{{background:#0f1923;color:#6b7a8d;font-size:0.52rem;
            letter-spacing:0.12em;text-transform:uppercase;
            border-bottom:2px solid #243040;position:sticky;top:0;z-index:10;}}
        .tr-r{{font-size:0.64rem;}}
        .tr-r:hover{{background:#111820;}}
        </style>
        <div class="tw">
            <div class="tr-h">
                <span>TOKEN</span><span>SIDE</span><span>RATING</span><span>TIER</span>
                <span>STRATEGY</span><span>TF</span><span>INDICATORS</span>
                <span>ENTRY</span><span>EXIT</span><span>OPENED</span><span>CLOSED</span>
                <span>REASON</span><span>P&amp;L</span><span>RETURN</span><span>W/L</span>
            </div>
            {rows}
        </div>"""
        st.markdown(trades_html, unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty">NO CLOSED TRADES YET</div>', unsafe_allow_html=True)
    if pnl_ok and total_tr > 0:
        try:
            import io as _io
            _buf = _io.BytesIO()
            _dl = closed_tr[['symbol','direction','tier','timeframe','entry_price','exit_price',
                              'entry_time','exit_time','exit_reason','pnl_usdt','pnl_pct']].copy()
            _dl.columns = ['Token','Direction','Tier','Timeframe','Entry','Exit',
                           'Opened','Closed','Reason','P&L (USDT)','Return %']
            _dl['W/L'] = _dl['P&L (USDT)'].apply(lambda x: 'W' if x >= 0 else 'L')
            with pd.ExcelWriter(_buf, engine='openpyxl') as _w:
                _dl.to_excel(_w, index=False, sheet_name='Closed Trades')
            _buf.seek(0)
            _sp1, _bc1 = st.columns([9.5, 1.5])
            with _bc1:
                st.download_button(label='⬇ EXPORT XLS', data=_buf,
                    file_name='apex_closed_trades.xlsx',
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    key='dl_trades')
        except Exception: pass


# =============================================================================
# STRATEGY BREAKDOWN
# =============================================================================

with st.container(border=True):
    st.markdown('<div class="sec-title">Strategy Breakdown</div>', unsafe_allow_html=True)
    if not strategies.empty:
        if pnl_ok and total_tr > 0:
            try:
                mg  = closed_tr.merge(strategies[["symbol","strategy"]], on="symbol", how="left")
                grp = mg.groupby("strategy").agg(
                    coins=("symbol","nunique"), trades=("pnl_usdt","count"),
                    pnl=("pnl_usdt","sum"), wins=("pnl_usdt",lambda x:(x>=0).sum())
                ).reset_index()
                grp["wr"]  = (grp["wins"]/grp["trades"]*100).round(1)
                grp["epf"] = grp["strategy"].map(strategies.groupby("strategy")["profit_factor"].mean()).fillna(0)
                grp["lpf"] = (grp["pnl"]/(grp["trades"].replace(0,np.nan))).fillna(0)
                grp = grp.sort_values("pnl", ascending=False)
                use_grp = True
            except: use_grp = False
        else: use_grp = False
        if not use_grp:
            grp = strategies.groupby("strategy").agg(
                coins=("symbol","count"), avg_wr=("win_rate","mean"), avg_pf=("profit_factor","mean")
            ).reset_index().sort_values("avg_pf", ascending=False)
        h = '<div class="trow sb th"><span>Strategy</span><span>Coins</span><span>Trades</span><span>Win%</span><span>P&L</span><span>Exp PF</span><span>Live PF</span></div>'
        rows = ""
        for i, (_, row) in enumerate(grp.iterrows()):
            dc2   = COLORS[i % len(COLORS)]
            strat = str(row.get("strategy",""))
            coins = int(row.get("coins",0))
            if use_grp:
                trd=int(row.get("trades",0)); wr2=float(row.get("wr",0))
                pnl=float(row.get("pnl",0)); epf=float(row.get("epf",0)); lpf=float(row.get("lpf",0))
                wstr=f"{wr2:.1f}%" if trd>0 else "--"
                pstr=f'{"+" if pnl>=0 else ""}${pnl:,.2f}'
                lstr=f"{lpf:.2f}" if trd>0 else "--"
                wc2="pp" if wr2>=50 else "pn"; pc3="pp" if pnl>=0 else "pn"; lc="pp" if lpf>=1 else "pn"
            else:
                trd=0; wstr="--"; pstr="--"; lstr="--"
                epf=float(row.get("avg_pf",0)); wc2=""; pc3=""; lc=""
            rows += (f'<div class="trow sb">'
                    f'<span style="display:flex;align-items:center"><span class="sdot" style="background:{dc2}"></span>'
                    f'<span style="color:var(--text);font-size:0.65rem">{strat}</span></span>'
                    f'<span style="color:var(--mid)">{coins}</span>'
                    f'<span style="color:var(--mid)">{trd}</span>'
                    f'<span class="{wc2}">{wstr}</span>'
                    f'<span class="{pc3}">{pstr}</span>'
                    f'<span style="color:var(--mid)">{epf:.1f}</span>'
                    f'<span class="{lc}">{lstr}</span></div>')
        st.markdown(f'<div class="scr">{h}{rows}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty">NO STRATEGY DATA</div>', unsafe_allow_html=True)


# =============================================================================
# RATING PERFORMANCE
# =============================================================================

with st.container(border=True):
    st.markdown('<div class="sec-title">Rating Performance</div>', unsafe_allow_html=True)
    if not strategies.empty:
        pf_map = strategies.groupby("symbol")["profit_factor"].first()
        h='<div class="trow rp th"><span>Rating</span><span>Coins</span><span>Position Size</span><span>Trades</span><span>P&L</span><span>Avg P&L / Trade</span></div>'
        rows=""
        for grade,label,lo,hi,pos in [
            ("A","A-Rated (PF > 5)",5,999,"20%"),
            ("B","B-Rated (PF 3–5)",3,5,"15%"),
            ("C","C-Rated (PF 1.5–3)",1.5,3,"10%"),
        ]:
            syms = pf_map[(pf_map>=lo)&(pf_map<hi)].index.tolist()
            if not syms: continue
            n=len(syms)
            if pnl_ok:
                t=closed_tr[closed_tr["symbol"].isin(syms)]
                nt=len(t); pnl=t["pnl_usdt"].sum() if nt>0 else 0; avg=pnl/nt if nt>0 else 0
            else: nt=0; pnl=0; avg=0
            gc={"A":"ra","B":"rb","C":"rc"}.get(grade,"")
            pc4="pp" if pnl>=0 else "pn"; ac2="pp" if avg>=0 else "pn"
            sg="+" if pnl>=0 else ""; ag="+" if avg>=0 else ""
            rows+=f'<div class="trow rp"><span><span class="{gc}">{grade}</span>&nbsp;<span style="color:var(--mid);font-size:0.65rem">{label}</span></span><span style="color:var(--text);font-weight:600">{n}</span><span style="color:var(--blue)">{pos}</span><span style="color:var(--mid)">{nt}</span><span class="{pc4}">{sg}${pnl:,.2f}</span><span class="{ac2}">{ag}${avg:,.2f}</span></div>'
        st.markdown(f'{h}{rows}', unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty">NO RATING DATA</div>', unsafe_allow_html=True)


# =============================================================================
# PERFORMANCE + SYSTEM
# =============================================================================

r2, r3 = st.columns([1,1])
with r2:
    with st.container(border=True):
        st.markdown('<div class="sec-title">Performance Stats</div>', unsafe_allow_html=True)
        aw  = wins["pnl_usdt"].mean()        if not wins.empty   else 0
        al2 = abs(losses["pnl_usdt"].mean()) if not losses.empty else 0
        pf2 = (aw*len(wins))/(al2*len(losses)) if al2>0 and len(losses)>0 else 0
        ex2 = (wr/100*aw)-((1-wr/100)*al2)
        st.markdown(f"""
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.7rem;">
          <div class="si"><div class="sl">AVG WIN</div><div class="sv val-green">${aw:,.2f}</div></div>
          <div class="si"><div class="sl">AVG LOSS</div><div class="sv val-red">${al2:,.2f}</div></div>
          <div class="si"><div class="sl">PROFIT FACTOR</div>
            <div class="sv" style="color:{'var(--green)' if pf2>1.5 else 'var(--gold)'}">{pf2:.2f}</div></div>
          <div class="si"><div class="sl">EXPECTANCY</div>
            <div class="sv" style="color:{'var(--green)' if ex2>0 else 'var(--red)'}">${ex2:,.2f}</div></div>
          <div class="si"><div class="sl">TOTAL TRADES</div><div class="sv val-blue">{total_tr}</div></div>
          <div class="si"><div class="sl">WIN RATE</div>
            <div class="sv" style="color:{'var(--green)' if wr>=60 else 'var(--gold)'}">{wr:.1f}%</div></div>
        </div>""", unsafe_allow_html=True)

with r3:
    with st.container(border=True):
        st.markdown('<div class="sec-title">System Status</div>', unsafe_allow_html=True)
        lt="N/A"
        try:
            lp=Path(__file__).parent/"logs"/"bot.log"
            if lp.exists():
                lt=datetime.fromtimestamp(os.path.getmtime(lp),tz=timezone.utc).strftime("%H:%M:%S UTC")
        except: pass
        st.markdown(f"""
        <div style="display:grid;grid-template-columns:1fr;gap:0.6rem;">
          <div class="si" style="display:flex;justify-content:space-between;align-items:center">
            <span class="sl">TRADING ENGINE</span><span class="sv" style="color:var(--green)">● RUNNING</span></div>
          <div class="si" style="display:flex;justify-content:space-between;align-items:center">
            <span class="sl">EXCHANGE</span><span class="sv" style="color:var(--green)">● CONNECTED</span></div>
          <div class="si" style="display:flex;justify-content:space-between;align-items:center">
            <span class="sl">LAST CYCLE</span><span class="sv" style="color:var(--blue)">{lt}</span></div>
          <div class="si" style="display:flex;justify-content:space-between;align-items:center">
            <span class="sl">MODE</span>
            <span class="sv" style="color:{'var(--gold)' if PAPER_TRADING else 'var(--green)'}">
              {"PAPER" if PAPER_TRADING else "LIVE"}</span></div>
        </div>""", unsafe_allow_html=True)


# =============================================================================
# TOKEN UNIVERSE
# =============================================================================

with st.container(border=True):
    _tu_count = len(strategies) if not strategies.empty else 0
    st.markdown(f'<div class="sec-title">Token Universe <span style="color:var(--dim);font-size:0.52rem;font-weight:400;margin-left:0.5rem">· {_tu_count} Tokens Active</span></div>', unsafe_allow_html=True)
    if not strategies.empty:
        strategies = strategies.copy()
        strategies['_score'] = strategies['win_rate'].fillna(0) * strategies['profit_factor'].fillna(0)
        strategies = strategies.sort_values('_score', ascending=False).reset_index(drop=True)
        h='<div class="trow sa th"><span>Token</span><span>Strategy Name</span><span>Indicators</span><span>TF</span><span>Tier</span><span>Win Rate</span><span>Profit Factor</span><span>Trades</span></div>'
        rows=""
        for _,row in strategies.iterrows():
            sym   = str(row.get("symbol","")).replace("/USDT:USDT","")
            st3   = str(row.get("strategy",""))
            combo = str(row.get("indicator_combo",""))
            tf2   = str(row.get("timeframe","")).upper()
            tier  = str(row.get("tier",""))
            wr3   = float(row.get("win_rate",0)or 0)*100
            pf3   = float(row.get("profit_factor",0)or 0)
            trd2  = int(row.get("val_trades",0)or 0)
            wc3   = "val-green" if wr3>=70 else "val-gold" if wr3>=50 else "val-red"
            pfc3  = "val-green" if pf3>=2.0 else "val-gold" if pf3>=1.5 else "val-red"
            tc2   = "val-green" if "1" in tier else "val-blue" if "2" in tier else "val-purple"
            rows+=f'''<div class="trow sa">
              <span style="color:var(--text);font-weight:600">{sym}</span>
              <span style="color:var(--blue);font-size:0.65rem;font-weight:600">{st3}</span>
              <span style="color:var(--dim);font-size:0.55rem;letter-spacing:0.03em">{combo}</span>
              <span style="color:var(--gold);font-weight:600">{tf2}</span>
              <span class="{tc2}">{tier.replace("tier","T")}</span>
              <span class="{wc3}">{wr3:.1f}%</span>
              <span class="{pfc3}">{pf3:.2f}</span>
              <span style="color:var(--dim)">{trd2}</span>
            </div>'''
        st.markdown(f'<div class="scr" style="max-height:460px">{h}{rows}</div>', unsafe_allow_html=True)
        try:
            import io
            buf = io.BytesIO()
            dl_df = strategies[['symbol','strategy','timeframe','tier','win_rate','profit_factor','val_trades','indicator_combo']].copy()
            dl_df.columns = ['Token','Strategy','Timeframe','Tier','Win Rate','Profit Factor','Trades','Indicators']
            dl_df['Win Rate'] = (dl_df['Win Rate'].fillna(0)*100).round(2)
            dl_df['Profit Factor'] = dl_df['Profit Factor'].fillna(0).round(3)
            with pd.ExcelWriter(buf, engine='openpyxl') as writer:
                dl_df.to_excel(writer, index=False, sheet_name='Token Universe')
            buf.seek(0)
            _sp2, _bc2 = st.columns([9.5, 1.5])
            with _bc2:
                st.download_button(label='⬇ EXPORT XLS', data=buf,
                    file_name='apex_token_universe.xlsx',
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    key='dl_universe')
        except Exception: pass
    else:
        st.markdown('<div class="empty">NO STRATEGIES LOADED</div>', unsafe_allow_html=True)


# =============================================================================
# FOOTER
# =============================================================================

st.markdown("""
<div style="text-align:center;margin-top:1rem;padding-top:1rem;border-top:1px solid #1e2a38;
  font-family:'JetBrains Mono',monospace;font-size:0.52rem;letter-spacing:0.15em;color:#2a3548;">
  APEX v4.2 &nbsp;·&nbsp; AI TRADING BOT &nbsp;·&nbsp; AUTO-REFRESH 30s &nbsp;·&nbsp; ALL TIMES UTC
</div>
""", unsafe_allow_html=True)
