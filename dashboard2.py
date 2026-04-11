#!/usr/bin/env python3
"""
APEX Dashboard v2 — Premium Trading Dashboard
Flask + Vanilla JS | Port 8502
"""

import sqlite3, json, os, re, glob, logging, time, sys, math
from flask import Flask, jsonify, Response
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

# FIX 2026-04-11 audit Phase 4-bis dashboards: import bot.* modules so we can use
# the canonical TIME_STOP_HOURS, fetch_current_prices, etc. instead of hardcoding.
sys.path.insert(0, '/home/opc/tradingbot')
try:
    from bot.trade_manager import TIME_STOP_HOURS
except Exception:
    TIME_STOP_HOURS = {"1h": 30, "4h": 120, "1d": 240}  # safe fallback

logging.basicConfig(level=logging.WARNING)
app = Flask(__name__)

BASE       = Path('/home/opc/tradingbot')
TRADES_DB  = BASE / 'data' / 'trades.db'
APEX_DBS   = [BASE / 'data' / 'apex.db', BASE / 'apex.db']
BOT_LOG    = BASE / 'logs' / 'bot.log'
FILTER_DIR = BASE / 'logs' / 'apex_events' / 'filters'

# FIX 2026-04-11: live-price cache (60s TTL) — replaces stale ohlcv close reads.
# Module-level since Flask doesn't have session_state.
_PRICE_CACHE = {"prices": {}, "ts": 0.0}
_PRICE_TTL_SEC = 60

# ── Helpers ────────────────────────────────────────────────────────────────
def _conn(path):
    c = sqlite3.connect(str(path), timeout=5)
    c.row_factory = sqlite3.Row
    return c

def _f(v, d=0.0):
    try: return float(v) if v is not None else d
    except: return d

def _sym(s):
    return str(s).replace('/USDT:USDT','').replace('/USDT','').replace('USDT','')

def _apex_conn():
    for p in APEX_DBS:
        if Path(p).exists():
            return _conn(p)
    return None

# ── Strategy map ───────────────────────────────────────────────────────────
def get_strategy_map():
    result = {}
    try:
        conn = _apex_conn()
        if conn:
            rows = conn.execute(
                "SELECT symbol, strategy_name FROM strategy_assignments WHERE is_active=1"
            ).fetchall()
            for r in rows:
                result[r['symbol']] = r['strategy_name'] or ''
            conn.close()
    except: pass
    return result

# ── BTC / F&G ──────────────────────────────────────────────────────────────
def get_btc_fg():
    try:
        lines = Path(BOT_LOG).read_text(errors='ignore').splitlines()
        for line in reversed(lines):
            if 'MTF cycle data fetched' in line:
                b = re.search(r'BTC: (\w+) \(1H:(\w+) 4H:(\w+) 1D:(\w+)\)', line)
                f = re.search(r'F&G: (\d+) \(([^)]+)\)', line)
                return {
                    'overall': b.group(1) if b else '?',
                    '1h': b.group(2) if b else '?',
                    '4h': b.group(3) if b else '?',
                    '1d': b.group(4) if b else '?',
                    'fg': int(f.group(1)) if f else 0,
                    'fg_label': f.group(2) if f else '?'
                }
    except: pass
    return {'overall':'?','1h':'?','4h':'?','1d':'?','fg':0,'fg_label':'?'}

def get_bot_status():
    try:
        age = (datetime.now().timestamp() - os.path.getmtime(BOT_LOG)) / 60
        return 'LIVE' if age < 90 else 'OFFLINE'
    except: return 'OFFLINE'

def get_active_tokens():
    try:
        lines = Path(BOT_LOG).read_text(errors='ignore').splitlines()
        for line in reversed(lines):
            m = re.search(r'Active strategies: (\d+)', line)
            if m: return int(m.group(1))
    except: pass
    return 0

# ── Filter rejections ──────────────────────────────────────────────────────
def get_filter_rejects():
    today   = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    counts  = defaultdict(int)
    tok_set = defaultdict(set)
    try:
        for fp in glob.glob(str(FILTER_DIR / '*.jsonl')):
            for line in open(fp, errors='ignore'):
                try:
                    d = json.loads(line)
                    if d.get('ts','').startswith(today):
                        fn = d.get('filter_name','other')
                        counts[fn] += 1
                        tok_set[fn].add(d.get('token',''))
                except: pass
    except: pass
    return [{'name':k,'count':v,'tokens':len(tok_set[k])}
            for k,v in sorted(counts.items(), key=lambda x:-x[1])]

# ── Live price helper (FIX 2026-04-11: replaces stale ohlcv close reads) ──
def get_live_prices(symbols):
    """Cached live-price fetch from bot.data_feed.fetch_current_prices.
    Refreshes every _PRICE_TTL_SEC seconds. Returns {symbol: price} dict.
    Falls back to last cached values on fetch error."""
    if not symbols:
        return {}
    now = time.time()
    if (now - _PRICE_CACHE["ts"]) < _PRICE_TTL_SEC and _PRICE_CACHE["prices"]:
        return _PRICE_CACHE["prices"]
    try:
        from bot.data_feed import fetch_current_prices
        fresh = fetch_current_prices(list(symbols))
        if isinstance(fresh, dict) and fresh:
            _PRICE_CACHE["prices"] = fresh
            _PRICE_CACHE["ts"]     = now
        return _PRICE_CACHE["prices"]
    except Exception:
        return _PRICE_CACHE["prices"] or {}

# ── Portfolio capital helper (FIX 2026-04-11: read authoritative portfolio table) ──
def get_portfolio_capital():
    """Read the canonical capital from the portfolio table. Falls back to 10000.0
    only if the table is missing or empty (cold-boot). This replaces the
    hardcoded `10000 + sum(pnl)` recompute that was bypassing the source of truth."""
    if not TRADES_DB.exists():
        return 10000.0
    try:
        conn = _conn(TRADES_DB)
        r = conn.execute("SELECT capital FROM portfolio ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        if r:
            return _f(r['capital'], 10000.0)
    except Exception:
        pass
    return 10000.0

# ── Trade data ─────────────────────────────────────────────────────────────
def get_trades():
    # FIX 2026-04-11: was returning 3-tuple in the cold-boot branch but 2-tuple
    # in the normal branch — caller unpacks 2, would crash on cold boot. Fixed
    # to return 2-tuple in both branches (capital comes from get_portfolio_capital).
    if not TRADES_DB.exists():
        return [], []
    conn     = _conn(TRADES_DB)
    open_t   = [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE status='open' ORDER BY entry_time DESC")]
    closed_t = [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE status!='open' ORDER BY exit_time DESC")]
    conn.close()
    return open_t, closed_t

def pnl_of(t):
    # FIX 2026-04-11 audit Phase 4-bis dashboard agent: was double-counting partial_pnl_usdt
    # on closed trades. trade_manager.close_trade() at line 624-625 already writes
    # pnl_usdt = remaining_pnl + partial_pnl, so the partial is already inside pnl_usdt
    # for closed trades. Adding partial_pnl_usdt on top counts it twice — inflating
    # capital, total P&L, drawdown, etc. For OPEN trades pnl_usdt is NULL and
    # partial_pnl_usdt is the only realized portion (from Stage 1/Stage 2 fires that
    # haven't yet been final-closed); reading partial_pnl_usdt directly is correct
    # there. The dormant case in pure_trailing_mode (no Stage 1/2 ever fires) is
    # safe either way.
    status = (t.get('status') or '').lower()
    if status == 'closed':
        return _f(t.get('pnl_usdt', 0))
    return _f(t.get('partial_pnl_usdt', 0))

def wl_of(p):
    if p > 0.005:  return 'W'
    if p < -0.005: return 'L'
    return 'N'

# ── Stats ──────────────────────────────────────────────────────────────────
def calc_stats(closed):
    pnls = [pnl_of(t) for t in closed]
    if not pnls:
        return dict(total=0,wins=0,losses=0,neutral=0,wr=0.0,exp=0.0,
                    pf=0.0,avg_win=0.0,avg_loss=0.0,best=0.0,worst=0.0,total_pnl=0.0,
                    max_win_streak=0,max_loss_streak=0)
    wins   = [p for p in pnls if p >  0.005]
    losses = [p for p in pnls if p < -0.005]
    neut   = len(pnls) - len(wins) - len(losses)
    wr     = len(wins)/len(pnls)*100
    aw     = sum(wins)/len(wins) if wins else 0.0
    al     = sum(losses)/len(losses) if losses else 0.0
    gp     = sum(wins)
    gl     = abs(sum(losses))

    # FIX 2026-04-11 (user feedback round 2): compute Max Win Streak and Max
    # Loss Streak. closed comes from get_trades sorted by exit_time DESC, so we
    # reverse to chronological order (oldest first). Neutral trades (|pnl|<0.005)
    # are ignored — they neither extend nor break a streak.
    closed_chrono = sorted(closed, key=lambda t: t.get('exit_time', '') or '')
    max_w_streak = max_l_streak = cur_w = cur_l = 0
    for t in closed_chrono:
        p = pnl_of(t)
        if p > 0.005:
            cur_w += 1; cur_l = 0
            if cur_w > max_w_streak: max_w_streak = cur_w
        elif p < -0.005:
            cur_l += 1; cur_w = 0
            if cur_l > max_l_streak: max_l_streak = cur_l

    return dict(
        total=len(pnls), wins=len(wins), losses=len(losses), neutral=neut,
        wr=round(wr,1), exp=round((wr/100)*aw+(1-wr/100)*al,2),
        pf=round(gp/gl if gl else 0,2),
        avg_win=round(aw,2), avg_loss=round(al,2),
        best=round(max(pnls),2), worst=round(min(pnls),2),
        total_pnl=round(sum(pnls),2),
        max_win_streak=max_w_streak,
        max_loss_streak=max_l_streak,
    )

def get_today_pnl(closed):
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return round(sum(pnl_of(t) for t in closed
                     if str(t.get('exit_time','') or '').startswith(today)), 2)

def get_unrealized_pnl(open_trades):
    """FIX 2026-04-11: was reading the most recent OHLCV candle close from apex.db
    (potentially hours stale on 1d timeframes). Now uses live ticker prices via
    bot.data_feed.fetch_current_prices (cached 60s) — same source dashboard.py uses,
    so the two dashboards stay consistent. Note: qty already includes leverage
    (capital_manager.calculate_position_size), so do NOT multiply by leverage again."""
    if not open_trades:
        return 0.0
    symbols = [t.get('symbol', '') for t in open_trades if t.get('symbol')]
    prices  = get_live_prices(symbols)
    total   = 0.0
    for t in open_trades:
        sym   = t.get('symbol', '')
        entry = _f(t.get('avg_entry_price', t.get('entry_price', 0)))
        qty   = _f(t.get('quantity_remaining', t.get('quantity', 0)))
        dirn  = t.get('direction', '')
        curr  = _f(prices.get(sym), entry)  # fallback to entry if no live price
        if curr <= 0 or entry <= 0 or qty <= 0:
            continue
        total += (curr - entry) * qty if dirn == 'long' else (entry - curr) * qty
    return round(total, 2)

# ── Equity curve ───────────────────────────────────────────────────────────
def build_equity(closed, init=10000.0):
    if not closed:
        return [{'x':'Start','y':init,'ts':''}]
    pts = [{'x':'Start','y':round(init,2),'ts':''}]
    cap = init
    for t in sorted(closed, key=lambda x: x.get('exit_time','') or ''):
        cap += pnl_of(t)
        raw = str(t.get('exit_time','') or '')
        try:    lbl = datetime.fromisoformat(raw).strftime('%b %d')
        except: lbl = raw[:10]
        pts.append({'x':lbl,'y':round(cap,2),'ts':raw[:19]})
    return pts

# ── Breakdown ──────────────────────────────────────────────────────────────
def build_breakdown(closed, strat_map):
    d = defaultdict(lambda: {'w':0,'t':0,'pnl':0.0,'strat':'','tf':''})
    for t in closed:
        sym = t.get('symbol','')
        s   = _sym(sym)
        p   = pnl_of(t)
        d[s]['t']    += 1
        d[s]['w']    += 1 if p > 0.005 else 0
        d[s]['pnl']  += p
        d[s]['strat'] = strat_map.get(sym,'')
        d[s]['tf']    = t.get('timeframe','')
    return sorted([
        {'coin':k,'strategy':v['strat'],'tf':v['tf'],
         'trades':v['t'],'wins':v['w'],
         'wr':round(v['w']/v['t']*100,1) if v['t'] else 0,
         'net_pnl':round(v['pnl'],2)}
        for k,v in d.items()
    ], key=lambda x: x['net_pnl'], reverse=True)

# ── Format rows ────────────────────────────────────────────────────────────
# Timeframe → hours-per-candle map (for time-stop bars-left calc)
_TF_HOURS = {'1h': 1, '4h': 4, '1d': 24}

def fmt_open(t, strat_map, prices=None):
    """FIX 2026-04-11 (user feedback round 2) — Open trades row with:
       - bars: WALL-CLOCK elapsed candles (not DB candles_open which has stale
         legacy values from pre-v3.8.1 cycle counter + spurious +1 per restart)
       - left: same wall-clock math, total - elapsed
       - entry/stop/current: rounded to 6 decimals (was raw float ~17 digits)
       - rating: A/B/C from tier1/2/3 (matches dashboard.py convention)
       - field order rearranged for logical reading sequence
       - stop reads trailing_sl with stop_loss fallback (live, not frozen original)
       - current uses live prices via fetch_current_prices (not stale ohlcv close)
    """
    sym   = t.get('symbol', '')
    entry = _f(t.get('avg_entry_price', t.get('entry_price', 0)))
    dirn  = t.get('direction', '')
    qty   = _f(t.get('quantity_remaining', t.get('quantity', 0)))
    tf    = t.get('timeframe', '1h')
    tier  = t.get('tier', 'tier2')

    # Live current price (with fallback to entry)
    curr = _f((prices or {}).get(sym), entry)

    # Unrealized P&L (qty already includes leverage — do NOT multiply by lev)
    pnl = ((curr - entry) * qty if dirn == 'long' else (entry - curr) * qty) if qty > 0 else 0.0

    # BARS + LEFT — wall-clock based. The DB's candles_open field is unreliable
    # (legacy stale values from pre-v3.8.1 cycle counter + spurious +1 per restart).
    #
    # FIX 2026-04-11 round 2 (user feedback): the previous floor/floor formula
    # lost 1 candle to truncation in both directions, so bars+left=9 for a
    # 10-candle time-stop period (1d). New formula:
    #   bars = ceil(elapsed_h / tf_h)   — entry candle counts as "touched"
    #   left = floor((total_h - elapsed_h) / tf_h) — full guaranteed remaining
    #   bars + left = total_candles (always)
    # For 1d: NEO/LINK/CFX (opened today) → bars=1, left=9, sum=10. HBAR
    # (opened ~59h ago) → bars=ceil(2.48)=3, left=floor(7.52)=7, sum=10.
    total_hours = TIME_STOP_HOURS.get(tf, 30)
    tf_h        = _TF_HOURS.get(tf, 1)
    total_candles = max(1, int(total_hours / tf_h))
    bars = 0
    left = total_candles
    entry_str = t.get('entry_time', '')
    if entry_str:
        try:
            entry_dt  = datetime.fromisoformat(str(entry_str).replace('Z', '+00:00'))
            elapsed_h = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
            if elapsed_h > 0:
                bars = max(1, math.ceil(elapsed_h / tf_h))
                left = max(0, int((total_hours - elapsed_h) / tf_h))
        except Exception:
            pass

    # Live trailing stop (the SL the engine actually checks), fallback to original
    live_stop = _f(t.get('trailing_sl'), _f(t.get('stop_loss', 0)))

    # Rating: A (tier1) / B (tier2) / C (tier3) — matches dashboard.py convention
    rating_map = {'tier1': 'A', 'tier2': 'B', 'tier3': 'C'}
    rating     = rating_map.get(tier, '?')

    return {
        # Reordered for logical reading: identity → quality → direction → timing → prices → result → time-stop
        'coin':     _sym(sym),
        'rating':   rating,
        'side':     dirn,
        'tf':       tf,
        'strategy': strat_map.get(sym, ''),
        'opened':   str(t.get('entry_time', ''))[:16],
        'entry':    round(entry, 6),
        'stop':     round(live_stop, 6),
        'current':  round(curr, 6),
        'pnl':      round(pnl, 2),
        'bars':     bars,
        'left':     left,
    }

def fmt_closed(t, strat_map):
    sym = t.get('symbol','')
    p   = pnl_of(t)
    return {
        'coin':       _sym(sym),
        'side':       t.get('direction',''),
        'strategy':   strat_map.get(sym,''),
        'tf':         t.get('timeframe',''),
        'pnl':        round(p,2),
        'reason':     t.get('exit_reason',''),
        'wl':         wl_of(p),
        'close_time': str(t.get('exit_time',''))[:16],
    }

# ── API ────────────────────────────────────────────────────────────────────
@app.route('/api/data')
def api_data():
    open_t, closed_t = get_trades()
    strat_map = get_strategy_map()
    s         = calc_stats(closed_t)
    # FIX 2026-04-11: read authoritative capital from portfolio table instead of
    # hardcoded 10000+sum recompute. Also fixes the (now-dormant) double-count of
    # partial_pnl_usdt that was inflating capital, drawdown, etc. on dashboard2.
    cap       = round(get_portfolio_capital(), 2)
    eq_pts    = build_equity(closed_t, init=10000.0)
    peak      = max((p['y'] for p in eq_pts), default=cap)
    peak      = max(peak, cap)  # peak should never be below current capital
    dd        = round((peak - cap) / peak * 100, 1) if peak > 0 else 0.0

    # Pre-fetch live prices once per request and pass into fmt_open + get_unrealized_pnl
    open_symbols = [t.get('symbol', '') for t in open_t if t.get('symbol')]
    live_prices  = get_live_prices(open_symbols)

    return jsonify({
        'status':         get_bot_status(),
        'capital':        cap,
        'peak':           round(peak, 2),
        'drawdown':       dd,
        'open_count':     len(open_t),
        'active_tokens':  get_active_tokens(),
        'today_pnl':      get_today_pnl(closed_t),
        'unrealized_pnl': get_unrealized_pnl(open_t),
        'stats':          s,
        'btc_fg':         get_btc_fg(),
        'filters':        get_filter_rejects(),
        'open_trades':    [fmt_open(t, strat_map, prices=live_prices) for t in open_t],
        'closed_trades':  [fmt_closed(t, strat_map) for t in closed_t[:20]],
        'breakdown':      build_breakdown(closed_t, strat_map),
        'equity':         eq_pts,
        'updated':        datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
    })

@app.route('/')
def index():
    return Response(HTML, mimetype='text/html')

# ── HTML ───────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>APEX</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{
  --bg:#0e1117;--bg2:#151b27;--bg3:#1a2230;
  --bd:#1e2d3d;--bd2:#243447;
  --t1:#e2e8f0;--t2:#94a3b8;--t3:#4a5568;
  --teal:#00d4aa;--red:#f87171;--amber:#f0b429;
  --blue:#60a5fa;--green:#4ade80;--purple:#a78bfa;
  --ui:'Outfit',sans-serif;--mono:'Space Mono',monospace;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--t1);font-family:var(--ui);font-size:14px;min-height:100vh;}
.wrap{max-width:1440px;margin:0 auto;padding:16px;}

/* Header */
.hdr{display:flex;justify-content:space-between;align-items:center;padding-bottom:16px;border-bottom:1px solid var(--bd);margin-bottom:18px;flex-wrap:wrap;gap:10px;}
.hdr-left{display:flex;align-items:center;gap:12px;}
.live-dot{width:8px;height:8px;border-radius:50%;background:var(--teal);flex-shrink:0;animation:pulse 2s infinite;}
.live-dot.off{background:var(--red);animation:none;}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:.35;}}
.logo{font-family:var(--mono);font-size:17px;font-weight:700;color:#fff;letter-spacing:.12em;}
.logo-sub{font-size:9px;color:var(--t3);letter-spacing:.06em;margin-top:2px;text-transform:uppercase;}
.hdr-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.pill{font-size:10px;padding:4px 11px;border-radius:20px;font-weight:500;letter-spacing:.04em;white-space:nowrap;}
.pill-paper{background:#f0b42912;color:var(--amber);border:1px solid #f0b42928;}
.pill-live{background:#00d4aa12;color:var(--teal);border:1px solid #00d4aa28;}
.pill-off{background:#f8717112;color:var(--red);border:1px solid #f8717128;}
.p-btc,.p-fg{background:var(--bg3);color:var(--t2);border:1px solid var(--bd);}
.p-btc .v,.p-fg .v{font-weight:600;}
.bullish{color:var(--teal);}
.bearish{color:var(--red);}
.neutral-btc{color:var(--amber);}
.fg-fear{color:var(--red);}
.fg-neutral-col{color:var(--amber);}
.fg-greed{color:var(--green);}
.updated{font-size:9px;color:var(--t3);}

/* KPI rows — FIX 2026-04-11 user feedback round 2: 5 cards per row, 2 rows
   = 10 cards total. Row 1 = current state, Row 2 = historical performance. */
.kpi-row1{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:10px;}
.kpi-row2{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:14px;}
.mc{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;padding:14px 16px;position:relative;overflow:hidden;}
.mc::after{content:'';position:absolute;top:0;left:0;right:0;height:2px;border-radius:10px 10px 0 0;}
.mc-teal::after{background:var(--teal);}
.mc-red::after{background:var(--red);}
.mc-amber::after{background:var(--amber);}
.mc-blue::after{background:var(--blue);}
.mc-green::after{background:var(--green);}
.mc-purple::after{background:var(--purple);}
.mc-lbl{font-size:10px;color:var(--t3);letter-spacing:.06em;text-transform:uppercase;margin-bottom:6px;}
.mc-val{font-family:var(--mono);font-size:20px;font-weight:700;line-height:1.15;}
.mc-sub{font-size:10px;color:var(--t3);margin-top:5px;}
.up{color:var(--teal);}
.dn{color:var(--red);}

/* Panel */
.panel{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;padding:16px;margin-bottom:12px;}
.p-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:8px;}
.p-title{font-size:10px;color:var(--t3);letter-spacing:.08em;text-transform:uppercase;}
.p-meta{font-size:10px;color:var(--t3);}

/* Equity filters */
.eq-filters{display:flex;align-items:center;gap:6px;flex-wrap:wrap;}
.eq-btn{font-size:10px;padding:3px 10px;border-radius:4px;border:1px solid var(--bd);background:transparent;color:var(--t3);cursor:pointer;font-family:var(--ui);transition:all .15s;}
.eq-btn:hover{border-color:var(--bd2);color:var(--t2);}
.eq-btn.active{background:var(--teal);border-color:var(--teal);color:#000;font-weight:600;}
.eq-dates{display:flex;align-items:center;gap:5px;}
.eq-dates input[type=date]{background:var(--bg3);border:1px solid var(--bd);color:var(--t2);font-size:10px;padding:3px 8px;border-radius:4px;font-family:var(--ui);outline:none;color-scheme:dark;}
.eq-dates input:focus{border-color:var(--teal);}
.eq-wrap{position:relative;height:190px;margin-top:4px;}

/* Tables */
.tbl-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
table{width:100%;border-collapse:collapse;font-size:12px;min-width:560px;}
th{font-size:9px;color:var(--t3);letter-spacing:.07em;text-transform:uppercase;padding:0 12px 10px 0;font-weight:500;text-align:left;white-space:nowrap;}
td{padding:8px 12px 8px 0;border-top:1px solid var(--bd);color:var(--t2);white-space:nowrap;vertical-align:middle;}
.tc{color:var(--t1);font-weight:600;font-size:13px;font-family:var(--mono);}
.badge{font-size:9px;padding:2px 8px;border-radius:4px;font-weight:600;letter-spacing:.04em;display:inline-block;}
.b-long,.b-w{background:#00d4aa12;color:var(--teal);border:1px solid #00d4aa25;}
.b-short,.b-l{background:#f8717112;color:var(--red);border:1px solid #f8717125;}
.b-n{background:#f0b42912;color:var(--amber);border:1px solid #f0b42925;}
.pv{font-family:var(--mono);color:var(--teal);}
.nv{font-family:var(--mono);color:var(--red);}
.mv{font-family:var(--mono);color:var(--t2);}
.zv{font-family:var(--mono);color:var(--t3);}
.empty-row td{text-align:center;color:var(--t3);padding:22px;font-size:12px;border-top:none;}

/* Rating pill (A/B/C from tier1/2/3) — added 2026-04-11 user feedback */
.rating{font-size:10px;padding:2px 8px;border-radius:4px;font-weight:700;letter-spacing:.04em;display:inline-block;font-family:var(--mono);}
.r-A{background:#00d4aa15;color:var(--teal);border:1px solid #00d4aa30;}
.r-B{background:#60a5fa15;color:var(--blue);border:1px solid #60a5fa30;}
.r-C{background:#f0b42915;color:var(--amber);border:1px solid #f0b42930;}

/* Open Positions footer (unrealized P&L summary) — added 2026-04-11 user feedback */
.open-footer{display:flex;justify-content:flex-end;align-items:center;gap:12px;padding:12px 0 2px;border-top:1px solid var(--bd);margin-top:8px;}
.of-lbl{font-size:10px;color:var(--t3);letter-spacing:.08em;text-transform:uppercase;}
.of-val{font-family:var(--mono);font-size:15px;font-weight:700;}

/* Two col */
.two-col{display:grid;grid-template-columns:3fr 2fr;gap:12px;margin-bottom:12px;}

/* Stats */
.stat-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-top:1px solid var(--bd);}
.stat-row:first-child{border-top:none;padding-top:0;}
.s-lbl{font-size:12px;color:var(--t3);}
.s-val{font-family:var(--mono);font-size:12px;font-weight:700;}
.divider{height:1px;background:var(--bd);margin:10px 0;}

/* Filters */
.fi{display:flex;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--bd);}
.fi:first-child{border-top:none;padding-top:0;}
.fi-ic{width:26px;height:26px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;flex-shrink:0;font-family:var(--mono);}
.fi-vol{background:#f0b42912;color:var(--amber);}
.fi-btc{background:#60a5fa12;color:var(--blue);}
.fi-fund{background:#f8717112;color:var(--red);}
.fi-oth{background:var(--bg3);color:var(--t3);}
.fi-name{color:var(--t2);flex:1;font-size:12px;text-transform:capitalize;}
.fi-cnt{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--t1);}
.fi-tok{font-size:9px;color:var(--t3);margin-left:3px;}

/* Breakdown bar */
.br-cell{display:flex;align-items:center;gap:8px;}
.br-bg{width:48px;height:3px;background:var(--bd);border-radius:2px;flex-shrink:0;}
.br-fill{height:100%;border-radius:2px;}

.footer{text-align:center;color:var(--t3);font-size:10px;padding:20px 0 8px;letter-spacing:.04em;}

@media(max-width:1300px){.kpi-row1,.kpi-row2{grid-template-columns:repeat(3,1fr);}}
@media(max-width:900px){.kpi-row1,.kpi-row2{grid-template-columns:repeat(2,1fr);}}
@media(max-width:768px){
  .kpi-row1,.kpi-row2{grid-template-columns:repeat(2,1fr);}
  .two-col{grid-template-columns:1fr;}
  .hdr{flex-direction:column;align-items:flex-start;}
  .wrap{padding:12px;}
}
</style>
</head>
<body>
<div class="wrap">

<!-- Header -->
<div class="hdr">
  <div class="hdr-left">
    <div class="live-dot" id="liveDot"></div>
    <div>
      <div class="logo">APEX</div>
      <div class="logo-sub">Adaptive Per-Token Execution Engine</div>
    </div>
  </div>
  <div class="hdr-right">
    <div class="pill p-btc">BTC &nbsp;<span class="v" id="btcPill">—</span></div>
    <div class="pill p-fg">F&amp;G &nbsp;<span class="v" id="fgPill">—</span></div>
    <div class="pill pill-paper">PAPER</div>
    <div class="pill" id="statusPill">—</div>
    <div class="updated" id="updatedLbl">—</div>
  </div>
</div>

<!-- KPI Row 1 — Current State -->
<div class="kpi-row1">
  <div class="mc mc-teal">
    <div class="mc-lbl">Capital</div>
    <div class="mc-val" id="mCap">—</div>
    <div class="mc-sub" id="mCapSub">—</div>
  </div>
  <div class="mc mc-blue">
    <div class="mc-lbl">Open Positions</div>
    <div class="mc-val" id="mOpen">—</div>
    <div class="mc-sub" id="mOpenSub">—</div>
  </div>
  <div class="mc mc-amber">
    <div class="mc-lbl">Strategies</div>
    <div class="mc-val" id="mStrategies">—</div>
    <div class="mc-sub" id="mStrategiesSub">active tokens</div>
  </div>
  <div class="mc mc-purple">
    <div class="mc-lbl">Today's P&amp;L</div>
    <div class="mc-val" id="mTodayPnl">—</div>
    <div class="mc-sub" id="mTodaySub">—</div>
  </div>
  <div class="mc mc-blue">
    <div class="mc-lbl">Unrealized P&amp;L</div>
    <div class="mc-val" id="mUnreal">—</div>
    <div class="mc-sub" id="mUnrealSub">—</div>
  </div>
</div>

<!-- KPI Row 2 — Historical Performance -->
<div class="kpi-row2">
  <div class="mc mc-green">
    <div class="mc-lbl">Total P&amp;L</div>
    <div class="mc-val" id="mTotalPnl">—</div>
    <div class="mc-sub" id="mTotalSub">—</div>
  </div>
  <div class="mc mc-amber">
    <div class="mc-lbl">Win Rate</div>
    <div class="mc-val" id="mWR">—</div>
    <div class="mc-sub" id="mWRSub">—</div>
  </div>
  <div class="mc mc-red">
    <div class="mc-lbl">Drawdown</div>
    <div class="mc-val" id="mDD">—</div>
    <div class="mc-sub" id="mDDSub">—</div>
  </div>
  <div class="mc mc-teal">
    <div class="mc-lbl">Max Win Streak</div>
    <div class="mc-val" id="mWinStreak">—</div>
    <div class="mc-sub" id="mWinStreakSub">consecutive wins</div>
  </div>
  <div class="mc mc-red">
    <div class="mc-lbl">Max Loss Streak</div>
    <div class="mc-val" id="mLossStreak">—</div>
    <div class="mc-sub" id="mLossStreakSub">consecutive losses</div>
  </div>
</div>

<!-- Equity Curve -->
<div class="panel">
  <div class="p-hdr">
    <span class="p-title">Equity Curve</span>
    <div class="eq-filters">
      <button class="eq-btn" onclick="setEqPeriod('this_week',this)">This Week</button>
      <button class="eq-btn" onclick="setEqPeriod('last_week',this)">Last Week</button>
      <button class="eq-btn" onclick="setEqPeriod('1m',this)">1 Month</button>
      <button class="eq-btn active" onclick="setEqPeriod('all',this)">All</button>
      <div class="eq-dates">
        <input type="date" id="eqFrom" onchange="setEqPeriod('custom',null)">
        <span style="color:var(--t3);font-size:10px">→</span>
        <input type="date" id="eqTo" onchange="setEqPeriod('custom',null)">
      </div>
    </div>
  </div>
  <div class="eq-wrap"><canvas id="eqChart"></canvas></div>
</div>

<!-- Open Positions -->
<div class="panel">
  <div class="p-hdr">
    <span class="p-title">Open Positions</span>
    <span class="p-meta" id="openMeta">0 active</span>
  </div>
  <div class="tbl-scroll">
    <table>
      <colgroup>
        <col style="width:8%">  <!-- Coin -->
        <col style="width:6%">  <!-- Rating -->
        <col style="width:7%">  <!-- Side -->
        <col style="width:5%">  <!-- TF -->
        <col style="width:14%"> <!-- Strategy -->
        <col style="width:11%"> <!-- Opened -->
        <col style="width:10%"> <!-- Entry -->
        <col style="width:10%"> <!-- Stop -->
        <col style="width:10%"> <!-- Current -->
        <col style="width:8%">  <!-- P&L -->
        <col style="width:5%">  <!-- Bars -->
        <col style="width:6%">  <!-- Left -->
      </colgroup>
      <thead><tr>
        <th>Coin</th><th>Rating</th><th>Side</th><th>TF</th>
        <th>Strategy</th><th>Opened</th>
        <th>Entry</th><th>Stop</th><th>Current</th>
        <th>P&amp;L</th><th>Bars</th><th>Left</th>
      </tr></thead>
      <tbody id="openBody"><tr class="empty-row"><td colspan="12">No open positions</td></tr></tbody>
    </table>
  </div>
  <div class="open-footer">
    <span class="of-lbl">Unrealized P&amp;L</span>
    <span class="of-val" id="openUnreal">$0.00</span>
  </div>
</div>

<!-- Closed Trades + Stats -->
<div class="two-col">
  <div class="panel">
    <div class="p-hdr">
      <span class="p-title">Recent Closed Trades</span>
      <span class="p-meta" id="closedMeta">—</span>
    </div>
    <div class="tbl-scroll">
      <table>
        <thead><tr>
          <th>Date / Time</th><th>Coin</th><th>Strategy</th><th>TF</th>
          <th>Side</th><th>P&amp;L</th><th>Reason</th><th>W/L</th>
        </tr></thead>
        <tbody id="closedBody"><tr class="empty-row"><td colspan="8">No closed trades yet</td></tr></tbody>
      </table>
    </div>
  </div>
  <div class="panel">
    <div class="p-hdr"><span class="p-title">Performance Stats</span></div>
    <div class="stat-row"><span class="s-lbl">Expectancy</span><span class="s-val" id="sExp">—</span></div>
    <div class="stat-row"><span class="s-lbl">Profit Factor</span><span class="s-val" id="sPF">—</span></div>
    <div class="stat-row"><span class="s-lbl">Avg Win</span><span class="s-val" id="sAW">—</span></div>
    <div class="stat-row"><span class="s-lbl">Avg Loss</span><span class="s-val" id="sAL">—</span></div>
    <div class="stat-row"><span class="s-lbl">Best Trade</span><span class="s-val" id="sBest">—</span></div>
    <div class="stat-row"><span class="s-lbl">Worst Trade</span><span class="s-val" id="sWorst">—</span></div>
    <div class="divider"></div>
    <div class="stat-row"><span class="s-lbl">Total Trades</span><span class="s-val" id="sTotal" style="color:var(--t1)">—</span></div>
    <div class="stat-row"><span class="s-lbl">W / L / N</span><span class="s-val" id="sWLN">—</span></div>
    <div class="stat-row"><span class="s-lbl">Active Tokens</span><span class="s-val" id="sTokens" style="color:var(--t1)">—</span></div>
  </div>
</div>

<!-- Breakdown + Filters -->
<div class="two-col">
  <div class="panel">
    <div class="p-hdr">
      <span class="p-title">Coin P&amp;L Breakdown</span>
      <span class="p-meta">sorted by net P&amp;L</span>
    </div>
    <div class="tbl-scroll">
      <table>
        <thead><tr>
          <th>Coin</th><th>Strategy</th><th>TF</th>
          <th>Trades</th><th>Wins</th><th>WR %</th><th>Net P&amp;L</th>
        </tr></thead>
        <tbody id="bdBody"><tr class="empty-row"><td colspan="7">No data yet</td></tr></tbody>
      </table>
    </div>
  </div>
  <div class="panel">
    <div class="p-hdr">
      <span class="p-title">Filter Rejections Today</span>
      <span class="p-meta" id="filterMeta">—</span>
    </div>
    <div id="filterBody"></div>
  </div>
</div>

<div class="footer" id="footer">APEX v3.5 &nbsp;·&nbsp; Loading…</div>
</div>

<script>
let eqChart  = null;
let allEquity = [];
let curPeriod = 'all';

const $  = id => document.getElementById(id);
const fm = v  => '$' + parseFloat(v).toLocaleString('en',{minimumFractionDigits:2,maximumFractionDigits:2});

function fp(v) {
  const n = parseFloat(v);
  if (Math.abs(n) < 0.005) return `<span class="zv">$0.00</span>`;
  return n > 0
    ? `<span class="pv">+$${n.toFixed(2)}</span>`
    : `<span class="nv">-$${Math.abs(n).toFixed(2)}</span>`;
}
function fmono(v, pos) {
  return `<span style="color:${pos?'var(--teal)':'var(--red)'};font-family:var(--mono);font-weight:700">${v}</span>`;
}

// ── Equity ──────────────────────────────────────────────────────────────
function filterEquityPts(pts, period) {
  if (period === 'all') return pts;
  const now = new Date();
  let start, end = now;
  if (period === 'this_week') {
    const day = now.getDay();
    start = new Date(now); start.setDate(now.getDate()-(day===0?6:day-1)); start.setHours(0,0,0,0);
  } else if (period === 'last_week') {
    const day = now.getDay();
    end = new Date(now); end.setDate(now.getDate()-(day===0?6:day-1)); end.setHours(0,0,0,0);
    start = new Date(end); start.setDate(end.getDate()-7);
  } else if (period === '1m') {
    start = new Date(now); start.setDate(now.getDate()-30);
  } else if (period === 'custom') {
    const f = $('eqFrom').value, t = $('eqTo').value;
    start = f ? new Date(f) : new Date(0);
    end   = t ? new Date(t+'T23:59:59') : now;
  }
  return pts.filter(p => {
    if (!p.ts) return true;
    const d = new Date(p.ts);
    return d >= start && d <= end;
  });
}

function setEqPeriod(period, btn) {
  curPeriod = period;
  document.querySelectorAll('.eq-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderEquity(filterEquityPts(allEquity, period));
}

function renderEquity(pts) {
  const ctx  = $('eqChart').getContext('2d');
  const labels = pts.map(p => p.x);
  const data   = pts.map(p => p.y);
  const last   = data[data.length-1] || 10000;
  const first  = data[0] || 10000;
  const col    = last >= first ? '#00d4aa' : '#f87171';
  const grad   = ctx.createLinearGradient(0,0,0,170);
  grad.addColorStop(0, col+'28'); grad.addColorStop(1, col+'00');
  if (eqChart) eqChart.destroy();
  eqChart = new Chart(ctx, {
    type:'line',
    data:{labels,datasets:[{data,borderColor:col,borderWidth:1.5,pointRadius:0,
      pointHoverRadius:4,pointHoverBackgroundColor:col,fill:true,backgroundColor:grad,tension:0.4}]},
    options:{
      responsive:true,maintainAspectRatio:false,
      interaction:{intersect:false,mode:'index'},
      plugins:{
        legend:{display:false},
        tooltip:{backgroundColor:'#151b27',borderColor:'#1e2d3d',borderWidth:1,
          titleColor:'#94a3b8',bodyColor:'#e2e8f0',
          bodyFont:{family:'Space Mono',size:11},
          callbacks:{label:c=>' $'+c.parsed.y.toFixed(2)}}
      },
      scales:{
        x:{grid:{color:'#1a2535',lineWidth:.5},ticks:{color:'#4a5568',font:{size:9},maxTicksLimit:10}},
        y:{grid:{color:'#1a2535',lineWidth:.5},ticks:{color:'#4a5568',font:{family:'Space Mono',size:9},callback:v=>'$'+v.toFixed(0)}}
      }
    }
  });
}

function renderOpen(trades) {
  $('openMeta').textContent = trades.length+' active';
  const el = $('openBody');
  const footEl = $('openUnreal');
  if (!trades.length){
    el.innerHTML='<tr class="empty-row"><td colspan="12">No open positions</td></tr>';
    if(footEl){footEl.textContent='$0.00'; footEl.style.color='var(--t3)';}
    return;
  }
  // Aggregate unrealized for footer (sum of per-row pnl values)
  let totalUnreal = 0;
  trades.forEach(t => { totalUnreal += parseFloat(t.pnl) || 0; });

  el.innerHTML = trades.map(t=>`<tr>
    <td class="tc">${t.coin}</td>
    <td><span class="rating r-${t.rating}">${t.rating}</span></td>
    <td><span class="badge b-${t.side}">${t.side.toUpperCase()}</span></td>
    <td style="color:var(--t3)">${t.tf}</td>
    <td style="color:var(--t2);font-size:11px">${t.strategy||'—'}</td>
    <td style="color:var(--t3);font-size:11px">${t.opened}</td>
    <td class="mv">${t.entry}</td>
    <td class="nv">${t.stop}</td>
    <td class="mv">${t.current}</td>
    <td>${fp(t.pnl)}</td>
    <td style="color:var(--t3)">${t.bars}</td>
    <td style="color:${t.left<=3?'var(--amber)':'var(--t3)'}">${t.left}</td>
  </tr>`).join('');

  // Render footer unrealized P&L
  if (footEl) {
    footEl.textContent = (totalUnreal>=0?'+$':'-$') + Math.abs(totalUnreal).toFixed(2);
    footEl.style.color = totalUnreal>0?'var(--green)':totalUnreal<0?'var(--red)':'var(--t3)';
  }
}

function renderClosed(trades) {
  $('closedMeta').textContent='last '+Math.min(trades.length,20);
  const el=$('closedBody');
  if(!trades.length){el.innerHTML='<tr class="empty-row"><td colspan="8">No closed trades yet</td></tr>';return;}
  el.innerHTML=trades.map(t=>`<tr>
    <td style="color:var(--t3);font-size:11px">${t.close_time}</td>
    <td class="tc">${t.coin}</td>
    <td style="color:var(--t2);font-size:11px">${t.strategy||'—'}</td>
    <td style="color:var(--t3)">${t.tf}</td>
    <td><span class="badge b-${t.side}">${t.side.toUpperCase()}</span></td>
    <td>${fp(t.pnl)}</td>
    <td style="color:var(--t3);font-size:11px">${t.reason}</td>
    <td><span class="badge b-${t.wl.toLowerCase()}">${t.wl}</span></td>
  </tr>`).join('');
}

function renderStats(s, tokens) {
  $('sExp').innerHTML   = fmono((s.exp>=0?'+':'')+'\$'+s.exp, s.exp>=0);
  $('sPF').innerHTML    = fmono(s.pf.toFixed(2), s.pf>=1);
  $('sAW').innerHTML    = fmono('+\$'+s.avg_win, true);
  $('sAL').innerHTML    = fmono('-\$'+Math.abs(s.avg_loss).toFixed(2), false);
  $('sBest').innerHTML  = fmono('+\$'+s.best, true);
  $('sWorst').innerHTML = fmono('-\$'+Math.abs(s.worst).toFixed(2), false);
  $('sTotal').textContent = s.total;
  $('sWLN').innerHTML =
    `<span style="color:var(--teal);font-family:var(--mono)">${s.wins}</span>` +
    `<span style="color:var(--t3)"> / </span>` +
    `<span style="color:var(--red);font-family:var(--mono)">${s.losses}</span>` +
    `<span style="color:var(--t3)"> / </span>` +
    `<span style="color:var(--amber);font-family:var(--mono)">${s.neutral}</span>`;
  $('sTokens').textContent = tokens;
}

function renderBreakdown(items) {
  const el=$('bdBody');
  if(!items.length){el.innerHTML='<tr class="empty-row"><td colspan="7">No closed trades yet</td></tr>';return;}
  const maxAbs=Math.max(...items.map(i=>Math.abs(i.net_pnl)),1);
  el.innerHTML=items.map(i=>{
    const bw=Math.round(Math.abs(i.net_pnl)/maxAbs*48);
    const col=i.net_pnl>=0?'var(--teal)':'var(--red)';
    return `<tr>
      <td class="tc">${i.coin}</td>
      <td style="color:var(--t2);font-size:11px">${i.strategy||'—'}</td>
      <td style="color:var(--t3)">${i.tf}</td>
      <td class="mv">${i.trades}</td>
      <td class="mv">${i.wins}</td>
      <td style="font-family:var(--mono);color:${i.wr>=50?'var(--teal)':'var(--red)'}">${i.wr}%</td>
      <td><div class="br-cell">
        <div class="br-bg"><div class="br-fill" style="width:${bw}px;max-width:48px;background:${col}"></div></div>
        ${fp(i.net_pnl)}
      </div></td>
    </tr>`;
  }).join('');
}

const FI_MAP={volume:{cls:'fi-vol',lbl:'VOL'},btc_trend:{cls:'fi-btc',lbl:'BTC'},funding_rate:{cls:'fi-fund',lbl:'FND'}};
function renderFilters(items) {
  const total=items.reduce((a,i)=>a+i.count,0);
  $('filterMeta').textContent=total+' total';
  const el=$('filterBody');
  if(!items.length){el.innerHTML='<div style="color:var(--t3);font-size:12px;padding:8px 0;">No rejections today</div>';return;}
  el.innerHTML=items.map(i=>{
    const ic=FI_MAP[i.name]||{cls:'fi-oth',lbl:'?'};
    return `<div class="fi">
      <div class="fi-ic ${ic.cls}">${ic.lbl}</div>
      <div class="fi-name">${i.name.replace(/_/g,' ')}</div>
      <span class="fi-cnt">${i.count}&times;</span>
      <span class="fi-tok">${i.tokens} token${i.tokens!==1?'s':''}</span>
    </div>`;
  }).join('');
}

function render(d) {
  const fg=d.btc_fg;
  const btcEl=$('btcPill');
  btcEl.textContent=fg.overall;
  btcEl.className='v '+(fg.overall==='bullish'?'bullish':fg.overall==='bearish'?'bearish':'neutral-btc');
  const fgEl=$('fgPill');
  fgEl.textContent=fg.fg+' — '+fg.fg_label;
  fgEl.className='v '+(fg.fg<20?'fg-fear':fg.fg>80?'fg-greed':'fg-neutral-col');

  const live=d.status==='LIVE';
  $('statusPill').textContent='● '+d.status;
  $('statusPill').className='pill '+(live?'pill-live':'pill-off');
  $('liveDot').className='live-dot'+(live?'':' off');
  $('updatedLbl').textContent='Updated '+d.updated;

  const diff=d.capital-10000;
  const pct=(Math.abs(diff)/100).toFixed(2);
  $('mCap').textContent=fm(d.capital);
  $('mCap').style.color=diff>=0?'var(--teal)':'var(--red)';
  $('mCapSub').innerHTML=`<span class="${diff>=0?'up':'dn'}">${diff>=0?'▲':'▼'} \$${Math.abs(diff).toFixed(2)} (${pct}%)</span> from start`;

  $('mOpen').textContent=d.open_count;
  $('mOpenSub').textContent=d.open_count?d.open_count+' active position'+(d.open_count>1?'s':''):'No open positions';

  // FIX 2026-04-11 user feedback round 2: new Strategies card
  $('mStrategies').textContent=d.active_tokens||0;
  $('mStrategiesSub').textContent=(d.active_tokens||0)+' tokens scanning';

  $('mWR').textContent=d.stats.wr+'%';
  $('mWR').style.color=d.stats.wr>=50?'var(--teal)':'var(--amber)';
  $('mWRSub').textContent=d.stats.wins+'W  '+d.stats.losses+'L  '+d.stats.neutral+'N  of  '+d.stats.total;

  // FIX 2026-04-11 user feedback round 2: Max Win/Loss Streak cards
  const ws=d.stats.max_win_streak||0;
  $('mWinStreak').textContent=ws;
  $('mWinStreak').style.color=ws>=5?'var(--teal)':ws>=3?'var(--green)':'var(--t1)';

  const ls=d.stats.max_loss_streak||0;
  $('mLossStreak').textContent=ls;
  $('mLossStreak').style.color=ls>=5?'var(--red)':ls>=3?'var(--amber)':'var(--t1)';

  $('mDD').textContent=d.drawdown+'%';
  $('mDD').style.color=d.drawdown>20?'var(--red)':d.drawdown>10?'var(--amber)':'var(--t1)';
  $('mDDSub').textContent='Peak '+fm(d.peak);

  const tp=d.stats.total_pnl;
  $('mTotalPnl').textContent=(tp>=0?'+':'-')+'\$'+Math.abs(tp).toFixed(2);
  $('mTotalPnl').style.color=tp>=0?'var(--green)':'var(--red)';
  $('mTotalSub').textContent=tp>=0?'All-time profitable':'All-time in loss';

  const tdp=d.today_pnl;
  $('mTodayPnl').textContent=(tdp>=0?'+':'-')+'\$'+Math.abs(tdp).toFixed(2);
  $('mTodayPnl').style.color=tdp>0?'var(--purple)':tdp<0?'var(--red)':'var(--t3)';
  $('mTodaySub').textContent=tdp===0?'No closed trades today':tdp>0?'Profitable today':'Loss today';

  const up=d.unrealized_pnl;
  $('mUnreal').textContent=(up>=0?'+':'-')+'\$'+Math.abs(up).toFixed(2);
  $('mUnreal').style.color=up>0?'var(--blue)':up<0?'var(--red)':'var(--t3)';
  $('mUnrealSub').textContent=d.open_count>0?d.open_count+' open position'+(d.open_count>1?'s':''):'No open positions';

  allEquity=d.equity;
  renderEquity(filterEquityPts(allEquity,curPeriod));
  renderOpen(d.open_trades);
  renderClosed(d.closed_trades);
  renderStats(d.stats,d.active_tokens);
  renderBreakdown(d.breakdown);
  renderFilters(d.filters);
  $('footer').innerHTML='APEX v3.5 &nbsp;&middot;&nbsp; Updated '+d.updated+' &nbsp;&middot;&nbsp; Auto-refresh 30s';
}

async function load() {
  try {
    const r=await fetch('/api/data');
    if(!r.ok) throw new Error(r.status);
    render(await r.json());
  } catch(e) {
    $('footer').textContent='Connection error — retrying in 30s';
  }
}

load();
setInterval(load,30000);
</script>
</body>
</html>"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8502, debug=False)
