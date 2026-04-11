#!/usr/bin/env python3
"""
APEX Dashboard v2 — Premium Trading Dashboard
Flask + Vanilla JS | Port 8502
"""

import sqlite3, json, os, re, glob, logging, time, sys, math
from flask import Flask, jsonify, Response
from datetime import datetime, timezone, timedelta
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

# ── Signal Journal — round 7 (replaces Performance Stats panel) ──
def get_signal_journal():
    """Aggregate filter-rejection events SINCE INCEPTION (all-time).
    Round 7d: removed the rolling-window cutoff per user feedback — they
    want full lifetime analytics, not last-7-days.

    Returns:
        {
          'window_label':           'since inception',
          'first_event':            ISO timestamp or None,
          'total_scanned':          100,        # top-N universe (constant)
          'active_assignments':     <int>,      # passed scoring + active strat
          'total_rejections':       <int>,      # event count, all-time
          'unique_tokens_rejected': <int>,      # distinct tokens hit a filter
          'breakdown': [
              {'name':'volume', 'count':220, 'tokens':41, 'pct':52.0}, ...
          ]
        }
    """
    counts  = defaultdict(int)
    tok_set = defaultdict(set)
    all_rejected = set()
    first_ts = None
    try:
        for fp in sorted(glob.glob(str(FILTER_DIR / '*.jsonl'))):
            for line in open(fp, errors='ignore'):
                try:
                    d = json.loads(line)
                    fn  = d.get('filter_name', 'other') or 'other'
                    tok = d.get('token', '')
                    ts  = d.get('ts', '')
                    counts[fn]   += 1
                    if tok:
                        tok_set[fn].add(tok)
                        all_rejected.add(tok)
                    if ts and (first_ts is None or ts < first_ts):
                        first_ts = ts
                except Exception:
                    pass
    except Exception:
        pass

    total_rej = sum(counts.values())
    breakdown = []
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        pct = round(v / total_rej * 100, 1) if total_rej else 0.0
        breakdown.append({
            'name':   k,
            'count':  v,
            'tokens': len(tok_set[k]),
            'pct':    pct,
        })

    return {
        'window_label':           'since inception',
        'first_event':            first_ts,
        'total_scanned':          100,
        'active_assignments':     get_active_tokens(),
        'total_rejections':       total_rej,
        'unique_tokens_rejected': len(all_rejected),
        'breakdown':              breakdown,
    }

# ── Strategy breakdown — round 9f (replaces Filter Rejections panel) ──
def get_strategy_breakdown(closed, strat_map):
    """Aggregate closed trades by base strategy type. Strategy comes from
    the current assignment map; '+ BTC' modifier variants collapse to the
    base type. Returns rows sorted by total P&L descending."""
    by = {}
    for t in closed:
        sym  = t.get('symbol', '')
        full = strat_map.get(sym, '') or ''
        base = full.split(' + ')[0].strip() if full else ''
        if not base:
            base = 'Unassigned'
        if base not in by:
            by[base] = {'name': base, 'trades': 0, 'wins': 0, 'losses': 0,
                        'neutral': 0, 'total_pnl': 0.0}
        d = by[base]
        p = pnl_of(t)
        d['trades']    += 1
        d['total_pnl'] += p
        if p > 0.005:
            d['wins'] += 1
        elif p < -0.005:
            d['losses'] += 1
        else:
            d['neutral'] += 1
    out = []
    for k in by:
        d = by[k]
        decided = d['wins'] + d['losses']
        # Round 9n: WR replaced by signed "Edge" — (wins-losses)/decided*100.
        # Range -100 (all losses) to +100 (all wins). 0 = break-even.
        d['edge']      = round((d['wins'] - d['losses']) / decided * 100, 1) if decided else 0.0
        d['avg_pnl']   = round(d['total_pnl'] / d['trades'], 2) if d['trades'] else 0.0
        d['total_pnl'] = round(d['total_pnl'], 2)
        out.append(d)
    out.sort(key=lambda r: -r['total_pnl'])
    return out

# ── Rating performance — round 9f ──
def get_rating_performance(closed):
    """Aggregate closed trades by rating (A/B/C from tier1/2/3)."""
    rating_map = {'tier1': 'A', 'tier2': 'B', 'tier3': 'C'}
    by = {r: {'rating': r, 'trades': 0, 'wins': 0, 'losses': 0,
              'neutral': 0, 'total_pnl': 0.0}
          for r in ('A', 'B', 'C')}
    for t in closed:
        tier   = (t.get('tier') or 'tier2').lower()
        rating = rating_map.get(tier, 'B')
        d = by[rating]
        p = pnl_of(t)
        d['trades']    += 1
        d['total_pnl'] += p
        if p > 0.005:
            d['wins'] += 1
        elif p < -0.005:
            d['losses'] += 1
        else:
            d['neutral'] += 1
    out = []
    for r in ('A', 'B', 'C'):
        d = by[r]
        decided = d['wins'] + d['losses']
        # Round 9n: signed Edge metric (see Strategy Breakdown helper)
        d['edge']      = round((d['wins'] - d['losses']) / decided * 100, 1) if decided else 0.0
        d['avg_pnl']   = round(d['total_pnl'] / d['trades'], 2) if d['trades'] else 0.0
        d['total_pnl'] = round(d['total_pnl'], 2)
        out.append(d)
    return out

# ── Timeframe performance — round 9h ──
def get_timeframe_performance(closed):
    """Aggregate closed trades by timeframe (1h/4h/1d)."""
    tfs = ('1h', '4h', '1d')
    by = {tf: {'tf': tf, 'trades': 0, 'wins': 0, 'losses': 0,
               'neutral': 0, 'total_pnl': 0.0}
          for tf in tfs}
    for t in closed:
        tf = (t.get('timeframe') or '').lower()
        if tf not in by:
            continue
        d = by[tf]
        p = pnl_of(t)
        d['trades']    += 1
        d['total_pnl'] += p
        if p > 0.005:
            d['wins'] += 1
        elif p < -0.005:
            d['losses'] += 1
        else:
            d['neutral'] += 1
    out = []
    for tf in tfs:
        d = by[tf]
        decided = d['wins'] + d['losses']
        # Round 9n: signed Edge metric (see Strategy Breakdown helper)
        d['edge']      = round((d['wins'] - d['losses']) / decided * 100, 1) if decided else 0.0
        d['avg_pnl']   = round(d['total_pnl'] / d['trades'], 2) if d['trades'] else 0.0
        d['total_pnl'] = round(d['total_pnl'], 2)
        out.append(d)
    return out

# ── Long / Short analysis — round 7 ──
def get_long_short_stats(closed):
    """Per-direction stats over closed trades. Returns dict with two keys
    'long' and 'short', each containing trades / wins / losses / neutral /
    wr / total_pnl / avg_pnl / best / worst."""
    def empty():
        return {'trades':0,'wins':0,'losses':0,'neutral':0,
                'wr':0.0,'total_pnl':0.0,'avg_pnl':0.0,
                'best':0.0,'worst':0.0}
    out = {'long': empty(), 'short': empty()}
    pnl_buckets = {'long': [], 'short': []}
    for t in closed:
        side = (t.get('direction') or '').lower()
        if side not in out:
            continue
        p = pnl_of(t)
        out[side]['trades'] += 1
        out[side]['total_pnl'] += p
        pnl_buckets[side].append(p)
        if p > 0.005:
            out[side]['wins'] += 1
        elif p < -0.005:
            out[side]['losses'] += 1
        else:
            out[side]['neutral'] += 1
    for side in ('long', 'short'):
        d = out[side]
        n = d['trades']
        if n:
            decided  = d['wins'] + d['losses']
            d['wr']        = round(d['wins'] / decided * 100, 1) if decided else 0.0
            d['avg_pnl']   = round(d['total_pnl'] / n, 2)
            d['total_pnl'] = round(d['total_pnl'], 2)
            d['best']      = round(max(pnl_buckets[side]), 2)
            d['worst']     = round(min(pnl_buckets[side]), 2)
    return out

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

# ── Strategy types helper (FIX 2026-04-11 user feedback round 4) ──
def get_strategy_types_count():
    """Count distinct BASE strategy types across active assignments. The
    apex.db strategy_name column contains 'Trend Breakout', 'Momentum Flow',
    etc. AND modifier variants like 'Momentum Squeeze + BTC' and
    'Trend Breakout + BTC!' (BTC-trend-override marker). Collapse the
    '+ BTC' suffixes to get the count of REAL strategy types in use.
    The strategy reference doc lists 5 types: Volatility Surge,
    Momentum Squeeze, Momentum Flow, Trend Breakout, Alpha Confluence."""
    try:
        conn = _apex_conn()
        if not conn:
            return 0
        rows = conn.execute(
            "SELECT DISTINCT strategy_name FROM strategy_assignments WHERE is_active=1"
        ).fetchall()
        conn.close()
        bases = set()
        for r in rows:
            name = (r['strategy_name'] or '').strip()
            if not name:
                continue
            base = name.split(' + ')[0].strip()
            if base:
                bases.add(base)
        return len(bases)
    except Exception:
        return 0

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
                    pf=0.0,avg_win=0.0,avg_loss=0.0,best=0.0,worst=0.0,total_pnl=0.0)
    wins   = [p for p in pnls if p >  0.005]
    losses = [p for p in pnls if p < -0.005]
    neut   = len(pnls) - len(wins) - len(losses)
    wr     = len(wins)/len(pnls)*100
    aw     = sum(wins)/len(wins) if wins else 0.0
    al     = sum(losses)/len(losses) if losses else 0.0
    gp     = sum(wins)
    gl     = abs(sum(losses))
    return dict(
        total=len(pnls), wins=len(wins), losses=len(losses), neutral=neut,
        wr=round(wr,1), exp=round((wr/100)*aw+(1-wr/100)*al,2),
        pf=round(gp/gl if gl else 0,2),
        avg_win=round(aw,2), avg_loss=round(al,2),
        best=round(max(pnls),2), worst=round(min(pnls),2),
        total_pnl=round(sum(pnls),2)
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
    """Closed trade row. Round 6 (2026-04-11) added:
       - bars: candles between entry_time and exit_time at the trade's TF
       - rating: A/B/C from tier1/2/3 (matches open-trades pill convention)
       - tier:   T1/T2/T3 numeric label (parallel to rating, easier to skim)
    """
    sym = t.get('symbol','')
    p   = pnl_of(t)
    tf  = t.get('timeframe','1h')

    # Bars held = ceil(duration_hours / tf_hours). Closed trades, so the
    # window is fully resolved — no clamping needed.
    bars = 0
    entry_str = t.get('entry_time','')
    exit_str  = t.get('exit_time','')
    if entry_str and exit_str:
        try:
            edt = datetime.fromisoformat(str(entry_str).replace('Z','+00:00'))
            xdt = datetime.fromisoformat(str(exit_str).replace('Z','+00:00'))
            dur_h = (xdt - edt).total_seconds() / 3600
            tf_h  = _TF_HOURS.get(tf, 1)
            if dur_h > 0:
                bars = max(1, math.ceil(dur_h / tf_h))
        except Exception:
            pass

    tier_raw = (t.get('tier') or 'tier2').lower()
    rating_map = {'tier1': 'A', 'tier2': 'B', 'tier3': 'C'}
    tier_map   = {'tier1': 'T1', 'tier2': 'T2', 'tier3': 'T3'}
    rating = rating_map.get(tier_raw, '?')
    tier   = tier_map.get(tier_raw, '—')

    return {
        'coin':       _sym(sym),
        'rating':     rating,
        'tier':       tier,
        'side':       t.get('direction',''),
        'strategy':   strat_map.get(sym,''),
        'tf':         tf,
        'bars':       bars,
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
        'status':           get_bot_status(),
        'capital':          cap,
        'peak':             round(peak, 2),
        'drawdown':         dd,
        'open_count':       len(open_t),
        'active_tokens':    get_active_tokens(),
        'strategy_types':   get_strategy_types_count(),  # FIX 2026-04-11 round 4
        'today_pnl':        get_today_pnl(closed_t),
        'unrealized_pnl':   get_unrealized_pnl(open_t),
        'stats':            s,
        'btc_fg':           get_btc_fg(),
        'filters':          get_filter_rejects(),
        'open_trades':      [fmt_open(t, strat_map, prices=live_prices) for t in open_t],
        # Round 6: serve ALL closed trades (was [:20]); UI provides a
        # vertical scroll container so the user can see history beyond ~10.
        'closed_trades':    [fmt_closed(t, strat_map) for t in closed_t],
        'breakdown':        build_breakdown(closed_t, strat_map),
        # Round 7: Performance Stats panel replaced by Signal Journal pie + L/S
        # Round 7d: signal journal aggregates since inception (no window).
        'signal_journal':   get_signal_journal(),
        'long_short':       get_long_short_stats(closed_t),
        # Round 9f: Filter Rejections panel replaced by Strategy + Rating
        'strategy_breakdown':  get_strategy_breakdown(closed_t, strat_map),
        'rating_performance':  get_rating_performance(closed_t),
        # Round 9h: Timeframe Performance added under Rating Performance
        'timeframe_performance': get_timeframe_performance(closed_t),
        'equity':           eq_pts,
        'updated':          datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
    })

@app.route('/')
def index():
    # Round 8c: force no-store so browsers always refetch the HTML/CSS/JS
    # bundle. Until this was added, every CSS edit required a manual hard-
    # refresh — now a regular reload picks up the new file.
    resp = Response(HTML, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma']        = 'no-cache'
    resp.headers['Expires']       = '0'
    return resp

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
  /* Round 9b — "Indigo Aurora" v2: BOLDER. Deeper navy-violet base,
     richer indigo accent, stronger background aurora gradients,
     visible glow effects on KPI values. */
  --bg:#05071a;--bg2:#0e1228;--bg3:#161b35;
  --bd:#252e4f;--bd2:#3a456b;
  --t1:#f5f7ff;--t2:#a3aed0;--t3:#5e688a;
  --teal:#2dd4a8;--red:#fb5b6e;--amber:#fbbf24;
  --blue:#818cf8;--green:#34e0b3;--purple:#c084fc;
  --accent:#6366f1;--accent2:#a855f7;
  --grad-accent:linear-gradient(135deg,#6366f1 0%,#a855f7 100%);
  --grad-up:linear-gradient(135deg,#2dd4a8 0%,#34e0b3 100%);
  --grad-down:linear-gradient(135deg,#fb5b6e 0%,#f43f5e 100%);
  --grad-amber:linear-gradient(135deg,#fbbf24 0%,#f59e0b 100%);
  --grad-violet:linear-gradient(135deg,#a855f7 0%,#c084fc 100%);
  --shadow-panel:0 1px 0 rgba(255,255,255,.04) inset,
                 0 1px 0 rgba(99,102,241,.05),
                 0 12px 32px -14px rgba(0,0,0,.7);
  --shadow-card:0 1px 0 rgba(255,255,255,.05) inset,
                0 1px 0 rgba(99,102,241,.07),
                0 8px 22px -10px rgba(0,0,0,.65);
  --glow-indigo:0 0 24px -6px rgba(99,102,241,.55);
  --glow-up:0 0 24px -6px rgba(45,212,168,.55);
  --glow-down:0 0 24px -6px rgba(251,91,110,.55);
  --ui:'Outfit',sans-serif;--mono:'Space Mono',monospace;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{
  background:var(--bg);
  background-image:
    radial-gradient(1200px 700px at 15% -5%, rgba(99,102,241,.22), transparent 55%),
    radial-gradient(1000px 600px at 88% 5%, rgba(168,85,247,.18), transparent 55%),
    radial-gradient(900px 700px at 50% 105%, rgba(45,212,168,.10), transparent 55%);
  background-attachment:fixed;
  color:var(--t1);font-family:var(--ui);font-size:15px;min-height:100vh;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1600px;margin:0 auto;padding:18px 22px;}

/* Header */
.hdr{display:flex;justify-content:space-between;align-items:center;padding-bottom:18px;border-bottom:1px solid var(--bd);margin-bottom:20px;flex-wrap:wrap;gap:10px;}
.hdr-left{display:flex;align-items:center;gap:14px;}
.live-dot{width:9px;height:9px;border-radius:50%;background:var(--teal);flex-shrink:0;
          box-shadow:0 0 0 4px rgba(16,217,160,.12), 0 0 12px rgba(16,217,160,.55);
          animation:pulse 2s infinite;}
.live-dot.off{background:var(--red);
              box-shadow:0 0 0 4px rgba(251,91,110,.12), 0 0 10px rgba(251,91,110,.5);
              animation:none;}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:.45;}}
.logo{font-family:var(--mono);font-size:21px;font-weight:700;letter-spacing:.14em;
      background:var(--grad-accent);-webkit-background-clip:text;
      background-clip:text;color:transparent;}
.logo-sub{font-size:10px;color:var(--t3);letter-spacing:.08em;margin-top:4px;
          text-transform:uppercase;font-weight:500;}
.hdr-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.pill{font-size:11px;padding:6px 14px;border-radius:999px;font-weight:600;letter-spacing:.05em;
      white-space:nowrap;backdrop-filter:saturate(140%);}
.pill-paper{background:rgba(251,191,36,.10);color:var(--amber);border:1px solid rgba(251,191,36,.28);}
.pill-live{background:rgba(16,217,160,.10);color:var(--teal);border:1px solid rgba(16,217,160,.30);
           box-shadow:0 0 12px -4px rgba(16,217,160,.4);}
.pill-off{background:rgba(251,91,110,.10);color:var(--red);border:1px solid rgba(251,91,110,.28);}
.p-btc,.p-fg{background:var(--bg3);color:var(--t2);border:1px solid var(--bd);}
.p-fg .v{font-weight:600;}
/* Round 9d: BTC pill now shows 1H / 4H / 1D sentiment as three colored
   arrows. Layout = inline-flex chips with subtle dividers. */
.p-btc{display:inline-flex;align-items:center;gap:11px;padding:6px 14px;}
.p-btc .btc-lead{font-size:10px;color:var(--t3);letter-spacing:.1em;
                 font-weight:700;text-transform:uppercase;}
.p-btc .btc-tf{display:inline-flex;align-items:center;gap:5px;font-size:10px;
               color:var(--t3);font-weight:600;letter-spacing:.04em;
               position:relative;padding-left:11px;}
.p-btc .btc-tf::before{content:'';position:absolute;left:0;top:50%;
                       width:1px;height:13px;background:var(--bd2);
                       transform:translateY(-50%);opacity:.6;}
.p-btc .btc-tf .arr{font-family:var(--mono);font-size:13px;font-weight:700;
                    line-height:1;}
.bullish{color:var(--teal);}
.bearish{color:var(--red);}
.neutral-btc{color:var(--amber);}
.fg-fear{color:var(--red);}
.fg-neutral-col{color:var(--amber);}
.fg-greed{color:var(--green);}
.updated{font-size:10px;color:var(--t3);}

/* KPI rows — FIX 2026-04-11 user feedback round 3: 4 cards per row, 2 rows
   = 8 cards total. Row 1 = current state, Row 2 = performance. Card width
   gets ~25% more vs the previous 5-per-row. */
.kpi-row1{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:14px;}
.kpi-row2{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px;}
.mc{background:
    linear-gradient(180deg, rgba(99,102,241,.05) 0%, transparent 70%),
    linear-gradient(180deg, rgba(255,255,255,.025) 0%, transparent 60%),
    var(--bg2);
    border:1px solid var(--bd);border-radius:14px;padding:22px 18px 20px;
    position:relative;overflow:hidden;text-align:center;
    box-shadow:var(--shadow-card);
    transition:transform .2s ease, box-shadow .2s ease, border-color .2s ease;}
.mc:hover{transform:translateY(-2px);border-color:var(--bd2);
          box-shadow:0 1px 0 rgba(255,255,255,.06) inset,
                     0 18px 36px -16px rgba(0,0,0,.75),
                     0 0 0 1px rgba(99,102,241,.18);}
/* Round 9c: removed the colored top accent stripes per user request —
   KPI cards now have a clean uniform top edge. */
.mc-lbl{font-size:11px;color:var(--t2);letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;font-weight:600;}
.mc-val{font-family:var(--mono);font-size:26px;font-weight:700;line-height:1.15;
        text-shadow:0 0 24px rgba(99,102,241,.18);}
.mc-sub{font-size:11px;color:var(--t3);margin-top:8px;font-weight:500;}
/* Split-value card (Tokens / Strategies) — round 5c: replaced the vertical
   divider between numbers + the bar in the label with a SINGLE soft
   horizontal divider that sits between the heading and the value row.
   The two numbers occupy equal halves of the value row, each centered. */
.mc-split{display:flex;align-items:center;justify-content:center;padding:0;}
.mc-half{flex:1 1 0;display:flex;align-items:center;justify-content:center;
         font-variant-numeric:tabular-nums;line-height:1.15;}
.mc-hdiv{height:1px;background:var(--bd2);opacity:.55;margin:0 6px 8px;}
.up{color:var(--teal);}
.dn{color:var(--red);}

/* Panel */
.panel{background:
       linear-gradient(180deg, rgba(99,102,241,.04) 0%, transparent 60%),
       linear-gradient(180deg, rgba(255,255,255,.018) 0%, transparent 50%),
       var(--bg2);
       border:1px solid var(--bd);border-radius:16px;padding:22px;margin-bottom:14px;
       box-shadow:var(--shadow-panel);
       backdrop-filter:saturate(150%);}
.p-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px;}
.p-title{font-size:12px;color:var(--t2);letter-spacing:.1em;text-transform:uppercase;font-weight:600;}
.p-meta{font-size:11px;color:var(--t3);font-weight:500;}

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
/* Closed trades — round 6: vertical scroll, sticky header.
   Round 8f: bumped max-height from 450 → 540 to use the empty space at
   the bottom of the panel that was sitting unused (the panel itself
   stretches in the grid row to match the right column). 540px now fits
   ~17 rows at default density — no font/padding shrinking needed. */
/* Round 9k: closed-scroll now uses the same flex-fill technique as
   bd-scroll so its bottom matches the right column (Signal Journal +
   Long/Short stack). flex:1 1 0 + height:0 means it contributes 0 to
   the row's intrinsic size; the right column drives the row height. */
.closed-scroll{flex:1 1 0;min-height:0;height:0;overflow-y:auto;overflow-x:auto;
               -webkit-overflow-scrolling:touch;
               scrollbar-width:thin;scrollbar-color:var(--bd) var(--bg2);}
.closed-scroll::-webkit-scrollbar{width:8px;height:8px;}
.closed-scroll::-webkit-scrollbar-track{background:var(--bg2);}
.closed-scroll::-webkit-scrollbar-thumb{background:var(--bd);border-radius:4px;}
.closed-scroll::-webkit-scrollbar-thumb:hover{background:var(--bd2);}
.closed-scroll::-webkit-scrollbar-corner{background:var(--bg2);}
.closed-scroll table{min-width:820px;}
.closed-scroll thead th{position:sticky;top:0;background:var(--bg2);z-index:2;
                         box-shadow:0 1px 0 0 var(--bd);}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:560px;}
th{font-size:11px;color:var(--t3);letter-spacing:.07em;text-transform:uppercase;padding:0 14px 12px 0;font-weight:500;text-align:left;white-space:nowrap;}
td{padding:10px 14px 10px 0;border-top:1px solid var(--bd);color:var(--t2);white-space:nowrap;vertical-align:middle;}
.tc{color:var(--t1);font-weight:600;font-size:14px;font-family:var(--mono);}
.badge{font-size:10px;padding:3px 9px;border-radius:4px;font-weight:600;letter-spacing:.04em;display:inline-block;}
.b-long,.b-w{background:#00d4aa12;color:var(--teal);border:1px solid #00d4aa25;}
.b-short,.b-l{background:#f8717112;color:var(--red);border:1px solid #f8717125;}
.b-n{background:#f0b42912;color:var(--amber);border:1px solid #f0b42925;}
.pv{font-family:var(--mono);color:var(--teal);}
.nv{font-family:var(--mono);color:var(--red);}
.mv{font-family:var(--mono);color:var(--t2);}
.zv{font-family:var(--mono);color:var(--t3);}
.empty-row td{text-align:center;color:var(--t3);padding:24px;font-size:13px;border-top:none;}

/* Rating pill (A/B/C from tier1/2/3) — added 2026-04-11 user feedback */
.rating{font-size:11px;padding:3px 9px;border-radius:4px;font-weight:700;letter-spacing:.04em;display:inline-block;font-family:var(--mono);}
.r-A{background:#00d4aa15;color:var(--teal);border:1px solid #00d4aa30;}
.r-B{background:#60a5fa15;color:var(--blue);border:1px solid #60a5fa30;}
.r-C{background:#f0b42915;color:var(--amber);border:1px solid #f0b42930;}

/* Open Positions footer (unrealized P&L summary) — added 2026-04-11 user feedback */
.open-footer{display:flex;justify-content:flex-end;align-items:center;gap:14px;padding:14px 0 4px;border-top:1px solid var(--bd);margin-top:10px;}
.of-lbl{font-size:11px;color:var(--t3);letter-spacing:.08em;text-transform:uppercase;}
.of-val{font-family:var(--mono);font-size:17px;font-weight:700;}

/* Two col — round 9l: bumped from 3fr 2fr (60/40) to 7fr 3fr (70/30)
   to give Recent Closed Trades + Coin P&L Breakdown more horizontal
   room. The right column (Long/Short cards / Strategy-Rating-Timeframe
   tables) has minimum content widths around ~410-440px which still
   comfortably fits in the 30% slot at 1600px max-width. */
.two-col{display:grid;grid-template-columns:7fr 3fr;gap:14px;margin-bottom:14px;}
.two-col > .panel{margin-bottom:0;}
/* Round 7: right column of Closed Trades two-col now stacks two panels */
.right-stack{display:flex;flex-direction:column;gap:12px;}
.right-stack .panel{margin-bottom:0;}

/* ── Signal Journal (round 7b redesign) ────────────────────────────── */
.sj-kpi{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;
        padding:8px 0 16px;border-bottom:1px solid var(--bd);margin-bottom:16px;}
.sj-kpi-cell{text-align:center;}
.sj-kpi-val{font-family:var(--mono);font-size:26px;font-weight:700;
            color:var(--t1);line-height:1.1;font-variant-numeric:tabular-nums;}
.sj-kpi-lbl{font-size:10px;color:var(--t3);text-transform:uppercase;
            letter-spacing:.07em;margin-top:5px;}
.sj-kpi-of{color:var(--t3);font-family:var(--mono);}
/* Pie + side legend (round 7c) */
.sj-pie-wrap{display:grid;grid-template-columns:150px 1fr;gap:16px;align-items:center;}
.sj-pie-box{position:relative;height:150px;width:150px;}
.sj-legend{display:flex;flex-direction:column;gap:9px;}
.sj-leg{display:grid;grid-template-columns:11px 1fr auto;gap:9px;align-items:baseline;
        font-size:12px;}
.sj-sw{display:inline-block;width:11px;height:11px;border-radius:2px;align-self:center;}
.sj-leg-name{color:var(--t2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.sj-leg-meta{font-family:var(--mono);font-size:12px;color:var(--t3);
             font-variant-numeric:tabular-nums;text-align:right;}
.sj-leg-pct{color:var(--t1);font-weight:700;}
.sj-empty{color:var(--t3);font-size:12px;padding:14px 0;text-align:center;}
@media(max-width:1100px){
  .sj-pie-wrap{grid-template-columns:130px 1fr;}
  .sj-pie-box{height:130px;width:130px;}
}

/* Round 9g: Coin P&L Breakdown panel flex-fills to match the
   stacked Strategy + Rating + Timeframe panels on the right. Scoped to
   .two-col-aligned so it doesn't affect the Closed Trades two-col.
   Round 9j fix: flex-basis MUST be 0, not auto — otherwise .bd-scroll
   reports its full table height as its intrinsic size and the LEFT
   column becomes the row driver, leaving empty space at the bottom of
   the right column. With basis:0 the scroll contributes 0 to the row
   intrinsic and the right column (Strategy+Rating+Timeframe stack)
   drives the row height. */
.two-col-aligned{align-items:stretch;}
.two-col-aligned > .panel{display:flex;flex-direction:column;min-height:0;}
.two-col-aligned > .right-stack{align-self:stretch;}
.bd-scroll{flex:1 1 0;min-height:0;height:0;overflow-y:auto;overflow-x:auto;
           -webkit-overflow-scrolling:touch;
           scrollbar-width:thin;scrollbar-color:var(--bd) var(--bg2);}
.bd-scroll::-webkit-scrollbar{width:8px;height:8px;}
.bd-scroll::-webkit-scrollbar-track{background:var(--bg2);}
.bd-scroll::-webkit-scrollbar-thumb{background:var(--bd);border-radius:4px;}
.bd-scroll::-webkit-scrollbar-thumb:hover{background:var(--bd2);}
.bd-scroll::-webkit-scrollbar-corner{background:var(--bg2);}
/* Round 9m: explicit column widths + table-layout:fixed for predictable
   distribution. Coin/Strategy left, TF/Trades/Wins/WR centered, Net P&L
   right-aligned (currency convention). The percentages sum to 100%. */
.bd-scroll table{min-width:0;width:100%;table-layout:fixed;}
/* Column widths */
.bd-scroll th:nth-child(1), .bd-scroll td:nth-child(1){width:11%;}
.bd-scroll th:nth-child(2), .bd-scroll td:nth-child(2){width:28%;}
.bd-scroll th:nth-child(3), .bd-scroll td:nth-child(3){width:8%;}
.bd-scroll th:nth-child(4), .bd-scroll td:nth-child(4){width:9%;}
.bd-scroll th:nth-child(5), .bd-scroll td:nth-child(5){width:9%;}
.bd-scroll th:nth-child(6), .bd-scroll td:nth-child(6){width:11%;}
.bd-scroll th:nth-child(7), .bd-scroll td:nth-child(7){width:24%;}
/* Column alignment per type */
.bd-scroll th:nth-child(1), .bd-scroll td:nth-child(1),
.bd-scroll th:nth-child(2), .bd-scroll td:nth-child(2){text-align:left;}
.bd-scroll th:nth-child(3), .bd-scroll td:nth-child(3),
.bd-scroll th:nth-child(4), .bd-scroll td:nth-child(4),
.bd-scroll th:nth-child(5), .bd-scroll td:nth-child(5),
.bd-scroll th:nth-child(6), .bd-scroll td:nth-child(6){text-align:center;}
.bd-scroll th:nth-child(7), .bd-scroll td:nth-child(7){text-align:right;padding-right:4px;}
/* Net P&L cell: bar viz + value flushed to the right edge */
.bd-scroll td:nth-child(7) .br-cell{justify-content:flex-end;}
/* Long strategy names truncate cleanly with ellipsis instead of wrapping */
.bd-scroll td:nth-child(2){overflow:hidden;text-overflow:ellipsis;}
.bd-scroll thead th{position:sticky;top:0;background:var(--bg2);z-index:2;
                    box-shadow:0 1px 0 0 var(--bd);}

/* ── Strategy Breakdown + Rating Performance (round 9f) ──────────── */
.sb-tbl, .rp-tbl{width:100%;border-collapse:collapse;font-size:13px;min-width:0;}
.sb-tbl th, .rp-tbl th{font-size:11px;color:var(--t3);letter-spacing:.07em;
                       text-transform:uppercase;padding:0 10px 10px 0;
                       font-weight:500;text-align:right;white-space:nowrap;}
.sb-tbl th:first-child, .rp-tbl th:first-child{text-align:left;}
.sb-tbl td, .rp-tbl td{padding:9px 10px 9px 0;border-top:1px solid var(--bd);
                       font-family:var(--mono);font-weight:600;text-align:right;
                       white-space:nowrap;font-variant-numeric:tabular-nums;}
.sb-tbl tbody tr:first-child td, .rp-tbl tbody tr:first-child td{border-top:none;}
.sb-tbl .sb-name{font-family:var(--ui);font-weight:500;color:var(--t1);
                 text-align:left;font-size:13px;}
.rp-tbl .rp-rating-cell{text-align:left;}
.sb-pos{color:var(--teal);}
.sb-neg{color:var(--red);}
.sb-zero{color:var(--t1);}
.sb-mute{color:var(--t2);}
/* Round 9h: timeframe tag pill — consistent with rating pill style */
.tf-tag{display:inline-block;font-size:11px;padding:3px 10px;border-radius:4px;
        font-weight:700;letter-spacing:.04em;font-family:var(--mono);
        background:var(--bg3);color:var(--t1);border:1px solid var(--bd2);}

/* ── Long / Short hero cards (round 7b redesign) ──────────────────── */
.ls-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
.ls-card{background:var(--bg3);border:1px solid var(--bd);border-radius:8px;
         padding:16px 16px 14px;position:relative;overflow:hidden;}
.ls-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;}
.ls-card-long::before{background:var(--teal);}
.ls-card-short::before{background:var(--red);}
.ls-card-hd{display:flex;justify-content:space-between;align-items:baseline;
            margin-bottom:10px;}
.ls-side-lbl{font-size:11px;color:var(--t3);letter-spacing:.1em;font-weight:700;}
.ls-card-long .ls-side-lbl{color:var(--teal);}
.ls-card-short .ls-side-lbl{color:var(--red);}
.ls-trades{font-family:var(--mono);font-size:12px;color:var(--t3);
           font-variant-numeric:tabular-nums;}
.ls-wr{font-family:var(--mono);font-size:32px;font-weight:700;line-height:1;
       color:var(--t1);font-variant-numeric:tabular-nums;text-align:center;}
.ls-wr-lbl{font-size:10px;color:var(--t3);text-transform:uppercase;
           letter-spacing:.06em;text-align:center;margin-top:4px;
           padding-bottom:12px;border-bottom:1px solid var(--bd);}
.ls-pnl{font-family:var(--mono);font-size:22px;font-weight:700;line-height:1.1;
        text-align:center;margin-top:12px;font-variant-numeric:tabular-nums;}
.ls-pnl-lbl{font-size:10px;color:var(--t3);text-transform:uppercase;
            letter-spacing:.06em;text-align:center;margin-top:4px;
            padding-bottom:12px;border-bottom:1px solid var(--bd);}
.ls-mini{display:flex;flex-direction:column;gap:6px;margin-top:12px;}
.ls-mini-row{display:flex;justify-content:space-between;align-items:baseline;
             font-size:11px;}
.ls-mini-row span:first-child{color:var(--t3);text-transform:uppercase;
             letter-spacing:.04em;}
.ls-mini-row span:last-child{font-family:var(--mono);color:var(--t2);
             font-weight:600;font-variant-numeric:tabular-nums;}
@media(max-width:1100px){
  .ls-grid{grid-template-columns:1fr;}
}

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
.fi-cnt{font-family:var(--mono);font-size:12px;font-weight:700;color:var(--t1);}
.fi-tok{font-size:9px;color:var(--t3);margin-left:3px;}

/* Breakdown bar */
.br-cell{display:flex;align-items:center;gap:8px;}
.br-bg{width:48px;height:3px;background:var(--bd);border-radius:2px;flex-shrink:0;}
.br-fill{height:100%;border-radius:2px;}

.footer{text-align:center;color:var(--t3);font-size:12px;padding:24px 0 10px;letter-spacing:.04em;}

/* ── Responsive (round 9i: rebuilt for proper mobile layout) ──── */
/* Tablets and small laptops */
@media(max-width:1100px){
  .kpi-row1,.kpi-row2{grid-template-columns:repeat(2,1fr);}
  .ls-grid{grid-template-columns:1fr;}
}
/* Tablets / large phones — stack two-col, allow header to wrap */
@media(max-width:900px){
  .wrap{padding:14px 14px;}
  .two-col{grid-template-columns:1fr;}
  .hdr{flex-direction:column;align-items:flex-start;gap:12px;}
  .hdr-right{flex-wrap:wrap;}
  .panel{padding:18px;border-radius:14px;margin-bottom:12px;}
  .closed-scroll{max-height:60vh;}
  .sj-pie-wrap{grid-template-columns:1fr;justify-items:center;text-align:center;}
  .sj-pie-box{height:170px;width:170px;}
  .sj-legend{width:100%;max-width:340px;margin-top:14px;}
}
/* Phones */
@media(max-width:640px){
  .wrap{padding:12px 12px;}
  .kpi-row1,.kpi-row2{grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:10px;}
  .panel{padding:16px;}
  .mc{padding:18px 14px 14px;}
  .mc-val{font-size:22px;}
  .mc-lbl{font-size:10px;margin-bottom:8px;}
  .mc-sub{font-size:10px;}
  .logo{font-size:19px;letter-spacing:.12em;}
  .logo-sub{font-size:9px;}
  .pill{font-size:10px;padding:5px 11px;}
  .p-btc{gap:8px;padding:5px 11px;}
  .p-btc .btc-tf{padding-left:9px;font-size:9px;}
  .p-btc .btc-tf::before{height:11px;}
  .p-title{font-size:11px;}
  .ls-wr{font-size:26px;}
  .ls-pnl{font-size:18px;}
  .sj-kpi-val{font-size:22px;}
  .ls-grid{gap:10px;}
  table{font-size:12px;}
  th{font-size:10px;padding:0 10px 10px 0;}
  td{padding:8px 10px 8px 0;}
  .sb-tbl, .rp-tbl{font-size:12px;}
  .sb-tbl th, .rp-tbl th{font-size:10px;padding:0 8px 8px 0;}
  .sb-tbl td, .rp-tbl td{padding:8px 8px 8px 0;}
  .sb-tbl .sb-name{font-size:12px;}
  .closed-scroll{max-height:55vh;}
}
/* Very narrow phones — tighter still */
@media(max-width:420px){
  .kpi-row1,.kpi-row2{grid-template-columns:1fr 1fr;gap:8px;}
  .mc{padding:14px 10px 12px;}
  .mc-val{font-size:20px;}
  .mc-lbl{font-size:9px;letter-spacing:.06em;}
  .logo{font-size:17px;}
  .panel{padding:14px;}
  .ls-wr{font-size:24px;}
  .ls-pnl{font-size:17px;}
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
    <div class="pill p-btc">
      <span class="btc-lead">BTC</span>
      <span class="btc-tf">1H<span class="arr" id="btc1h">—</span></span>
      <span class="btc-tf">4H<span class="arr" id="btc4h">—</span></span>
      <span class="btc-tf">1D<span class="arr" id="btc1d">—</span></span>
    </div>
    <div class="pill p-fg">F&amp;G &nbsp;<span class="v" id="fgPill">—</span></div>
    <div class="pill pill-paper">PAPER</div>
    <div class="pill" id="statusPill">—</div>
    <div class="updated" id="updatedLbl">—</div>
  </div>
</div>

<!-- KPI Row 1 — Capital + P&L summary (4 cards)
     Order per user request 2026-04-11 round 5: Capital, Total P&L,
     Today's P&L, Unrealized P&L. -->
<div class="kpi-row1">
  <div class="mc mc-teal">
    <div class="mc-lbl">Capital</div>
    <div class="mc-val" id="mCap">—</div>
    <div class="mc-sub" id="mCapSub">—</div>
  </div>
  <div class="mc mc-green">
    <div class="mc-lbl">Total P&amp;L</div>
    <div class="mc-val" id="mTotalPnl">—</div>
    <div class="mc-sub" id="mTotalSub">—</div>
  </div>
  <div class="mc mc-blue">
    <div class="mc-lbl">Today's P&amp;L</div>
    <div class="mc-val" id="mTodayPnl">—</div>
    <div class="mc-sub" id="mTodaySub">—</div>
  </div>
  <div class="mc mc-purple">
    <div class="mc-lbl">Unrealized P&amp;L</div>
    <div class="mc-val" id="mUnreal">—</div>
    <div class="mc-sub" id="mUnrealSub">—</div>
  </div>
</div>

<!-- KPI Row 2 — Activity + risk (4 cards)
     Order: Open Positions, Tokens · Strategies, Win Rate, Drawdown.
     Tokens card uses a split layout with a soft vertical divider line
     instead of a middot — round 5 user feedback. -->
<div class="kpi-row2">
  <div class="mc mc-blue">
    <div class="mc-lbl">Open Positions</div>
    <div class="mc-val" id="mOpen">—</div>
    <div class="mc-sub" id="mOpenSub">—</div>
  </div>
  <div class="mc mc-amber">
    <div class="mc-lbl">Tokens &nbsp;/&nbsp; Strategies</div>
    <div class="mc-hdiv"></div>
    <div class="mc-val mc-split">
      <div class="mc-half"><span id="mTokens">—</span></div>
      <div class="mc-half"><span id="mStrats">—</span></div>
    </div>
    <div class="mc-sub" id="mTokensSub">of top 100 scanning</div>
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

<!-- Closed Trades + Stats — round 9k: now uses two-col-aligned so the
     left panel (Closed Trades) bottom matches the right column (Signal
     Journal + Long/Short stack) bottom. -->
<div class="two-col two-col-aligned">
  <div class="panel">
    <div class="p-hdr">
      <span class="p-title">Recent Closed Trades</span>
    </div>
    <!-- Round 6: vertical scroll container, sticky header, no '/last 20'
         meta, full closed-trade history. New columns: Rating, Tier, Bars. -->
    <div class="closed-scroll">
      <table>
        <thead><tr>
          <th>Date / Time</th><th>Coin</th><th>Rating</th><th>Tier</th>
          <th>Strategy</th><th>TF</th><th>Side</th><th>Bars</th>
          <th>P&amp;L</th><th>Reason</th><th>W/L</th>
        </tr></thead>
        <tbody id="closedBody"><tr class="empty-row"><td colspan="11">No closed trades yet</td></tr></tbody>
      </table>
    </div>
  </div>
  <!-- Round 7b: redesigned Signal Journal (horizontal bar list + KPI strip)
       and Long/Short analysis (two hero cards with sub-stats). -->
  <div class="right-stack">

    <!-- Signal Journal -->
    <div class="panel">
      <div class="p-hdr">
        <span class="p-title">Signal Journal</span>
        <span class="p-meta" id="sjMeta">since inception</span>
      </div>
      <!-- Compact KPI strip -->
      <div class="sj-kpi">
        <div class="sj-kpi-cell">
          <div class="sj-kpi-val" id="sjActive">—</div>
          <div class="sj-kpi-lbl">Active <span class="sj-kpi-of">/ <span id="sjUniverse">—</span></span></div>
        </div>
        <div class="sj-kpi-cell">
          <div class="sj-kpi-val" id="sjUniqRej">—</div>
          <div class="sj-kpi-lbl">Tokens Filtered</div>
        </div>
        <div class="sj-kpi-cell">
          <div class="sj-kpi-val" id="sjTotalRej">—</div>
          <div class="sj-kpi-lbl">Reject Events</div>
        </div>
      </div>
      <!-- Pie chart: top 3 filters as individual slices + 'Others' bucket
           for everything else. Round 7c — replaces horizontal bar list. -->
      <div class="sj-pie-wrap">
        <div class="sj-pie-box"><canvas id="sjPie"></canvas></div>
        <div class="sj-legend" id="sjLegend"></div>
      </div>
    </div>

    <!-- Long / Short Analysis -->
    <div class="panel">
      <div class="p-hdr"><span class="p-title">Long / Short Analysis</span></div>
      <div class="ls-grid">
        <div class="ls-card ls-card-long">
          <div class="ls-card-hd"><span class="ls-side-lbl">LONG</span><span class="ls-trades" id="lsLTrades">—</span></div>
          <div class="ls-wr" id="lsLWR">—</div>
          <div class="ls-wr-lbl">Win Rate</div>
          <div class="ls-pnl" id="lsLPnl">—</div>
          <div class="ls-pnl-lbl">Total P&amp;L</div>
          <div class="ls-mini">
            <div class="ls-mini-row"><span>W / L / N</span><span id="lsLWLN">—</span></div>
            <div class="ls-mini-row"><span>Avg P&amp;L</span><span id="lsLAvg">—</span></div>
            <div class="ls-mini-row"><span>Best</span><span id="lsLBest">—</span></div>
            <div class="ls-mini-row"><span>Worst</span><span id="lsLWorst">—</span></div>
          </div>
        </div>
        <div class="ls-card ls-card-short">
          <div class="ls-card-hd"><span class="ls-side-lbl">SHORT</span><span class="ls-trades" id="lsSTrades">—</span></div>
          <div class="ls-wr" id="lsSWR">—</div>
          <div class="ls-wr-lbl">Win Rate</div>
          <div class="ls-pnl" id="lsSPnl">—</div>
          <div class="ls-pnl-lbl">Total P&amp;L</div>
          <div class="ls-mini">
            <div class="ls-mini-row"><span>W / L / N</span><span id="lsSWLN">—</span></div>
            <div class="ls-mini-row"><span>Avg P&amp;L</span><span id="lsSAvg">—</span></div>
            <div class="ls-mini-row"><span>Best</span><span id="lsSBest">—</span></div>
            <div class="ls-mini-row"><span>Worst</span><span id="lsSWorst">—</span></div>
          </div>
        </div>
      </div>
    </div>

  </div>
</div>

<!-- Breakdown + Strategy/Rating analysis (round 9f) -->
<div class="two-col two-col-aligned">
  <div class="panel">
    <div class="p-hdr">
      <span class="p-title">Coin P&amp;L Breakdown</span>
      <span class="p-meta">sorted by net P&amp;L</span>
    </div>
    <!-- Round 9g: vertical scroll with sticky header so the table fills
         the panel height regardless of how many coins are listed. -->
    <div class="bd-scroll">
      <table>
        <thead><tr>
          <th>Coin</th><th>Strategy</th><th>TF</th>
          <th>Trades</th><th>Wins</th><th>WR %</th><th>Net P&amp;L</th>
        </tr></thead>
        <tbody id="bdBody"><tr class="empty-row"><td colspan="7">No data yet</td></tr></tbody>
      </table>
    </div>
  </div>
  <!-- Round 9f: replaced "Filter Rejections Today" with two stacked panels:
       Strategy Breakdown + Rating Performance. -->
  <div class="right-stack">
    <div class="panel">
      <div class="p-hdr">
        <span class="p-title">Strategy Breakdown</span>
        <span class="p-meta">closed trades</span>
      </div>
      <table class="sb-tbl">
        <thead><tr>
          <th>Strategy</th><th>Trades</th><th>Edge</th><th>Net P&amp;L</th>
        </tr></thead>
        <tbody id="sbBody"><tr class="empty-row"><td colspan="4">No data yet</td></tr></tbody>
      </table>
    </div>
    <div class="panel">
      <div class="p-hdr">
        <span class="p-title">Rating Performance</span>
        <span class="p-meta">A / B / C tiers</span>
      </div>
      <table class="rp-tbl">
        <thead><tr>
          <th>Rating</th><th>Trades</th><th>Edge</th><th>Net P&amp;L</th>
        </tr></thead>
        <tbody id="rpBody"><tr class="empty-row"><td colspan="4">No data yet</td></tr></tbody>
      </table>
    </div>
    <!-- Round 9h: Timeframe Performance (1h / 4h / 1d) -->
    <div class="panel">
      <div class="p-hdr">
        <span class="p-title">Timeframe Performance</span>
        <span class="p-meta">1h / 4h / 1d</span>
      </div>
      <table class="rp-tbl">
        <thead><tr>
          <th>Timeframe</th><th>Trades</th><th>Edge</th><th>Net P&amp;L</th>
        </tr></thead>
        <tbody id="tfBody"><tr class="empty-row"><td colspan="4">No data yet</td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<div class="footer" id="footer">APEX v3.5 · build r9n · Indigo Aurora v2 &nbsp;·&nbsp; Loading…</div>
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
    <td style="color:var(--t2);font-size:12px">${t.strategy||'—'}</td>
    <td style="color:var(--t3);font-size:12px">${t.opened}</td>
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
  // Round 6: no 'last 20' meta — full history rendered, vertical scroll.
  // Columns: Date/Time, Coin, Rating, Tier, Strategy, TF, Side, Bars, P&L, Reason, W/L
  const el=$('closedBody');
  if(!trades.length){el.innerHTML='<tr class="empty-row"><td colspan="11">No closed trades yet</td></tr>';return;}
  el.innerHTML=trades.map(t=>`<tr>
    <td style="color:var(--t3);font-size:12px">${t.close_time}</td>
    <td class="tc">${t.coin}</td>
    <td><span class="rating r-${t.rating}">${t.rating}</span></td>
    <td style="color:var(--t2);font-family:var(--mono);font-size:12px">${t.tier}</td>
    <td style="color:var(--t2);font-size:12px">${t.strategy||'—'}</td>
    <td style="color:var(--t3)">${t.tf}</td>
    <td><span class="badge b-${t.side}">${t.side.toUpperCase()}</span></td>
    <td class="mv">${t.bars}</td>
    <td>${fp(t.pnl)}</td>
    <td style="color:var(--t3);font-size:12px">${t.reason}</td>
    <td><span class="badge b-${t.wl.toLowerCase()}">${t.wl}</span></td>
  </tr>`).join('');
}

// ── Round 7b redesigned: Signal Journal (horizontal bars + KPI strip) ──
const SJ_COLORS = {
  volume:       '#f0b429',
  btc_trend:    '#60a5fa',
  session:      '#a78bfa',
  cooldown:     '#94a3b8',
  fear_greed:   '#fb923c',
  funding_rate: '#22d3ee',
  funding:      '#22d3ee',
  correlation:  '#ec4899',
  confluence:   '#00d4aa',
  other:        '#64748b'
};
const SJ_NAMES = {
  volume:'Volume', fear_greed:'Fear / Greed', session:'Session',
  btc_trend:'BTC Trend', funding_rate:'Funding', funding:'Funding',
  confluence:'Confluence', cooldown:'Cooldown', correlation:'Correlation',
  other:'Other'
};
function sjLabel(k){ return SJ_NAMES[k] || k.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase()); }
function sjColor(k){ return SJ_COLORS[k] || '#64748b'; }

// Round 7c: Signal Journal pie chart — top 3 individual slices + Others.
let sjPieChart = null;
const SJ_OTHERS_COLOR = '#475569';
function renderSignalJournal(sj) {
  if (!sj) return;
  // Round 7d: window_label is 'since inception' (or compatible legacy label)
  $('sjMeta').textContent = sj.window_label || 'since inception';
  $('sjUniverse').textContent = sj.total_scanned;
  $('sjActive').textContent   = sj.active_assignments;
  $('sjUniqRej').textContent  = sj.unique_tokens_rejected;
  $('sjTotalRej').textContent = sj.total_rejections;

  const items = sj.breakdown || [];
  const lgEl  = $('sjLegend');
  const ctx   = $('sjPie').getContext('2d');
  if (sjPieChart) sjPieChart.destroy();

  if (!items.length) {
    lgEl.innerHTML = '<div class="sj-empty">No filter rejections in window</div>';
    sjPieChart = new Chart(ctx, {
      type:'doughnut',
      data:{labels:['No data'],datasets:[{data:[1],backgroundColor:['#1e2d3d'],borderWidth:0}]},
      options:{responsive:true,maintainAspectRatio:false,cutout:'62%',
        plugins:{legend:{display:false},tooltip:{enabled:false}}}
    });
    return;
  }

  // Top 3 + Others bucket
  const top3   = items.slice(0, 3);
  const rest   = items.slice(3);
  const restCt = rest.reduce((a,i) => a + i.count, 0);
  const restPct = rest.reduce((a,i) => a + i.pct, 0);
  const slices = top3.map(i => ({
    name: sjLabel(i.name),
    color: sjColor(i.name),
    count: i.count,
    pct:   i.pct
  }));
  if (restCt > 0) {
    slices.push({
      name:  'Others',
      color: SJ_OTHERS_COLOR,
      count: restCt,
      pct:   Math.round(restPct * 10) / 10
    });
  }

  // Side legend
  lgEl.innerHTML = slices.map(s => `
    <div class="sj-leg">
      <span class="sj-sw" style="background:${s.color}"></span>
      <span class="sj-leg-name">${s.name}</span>
      <span class="sj-leg-meta"><span class="sj-leg-pct">${s.pct}%</span> &middot; ${s.count}</span>
    </div>`).join('');

  // Pie / doughnut
  sjPieChart = new Chart(ctx, {
    type:'doughnut',
    data:{
      labels: slices.map(s => s.name),
      datasets:[{
        data: slices.map(s => s.count),
        backgroundColor: slices.map(s => s.color),
        borderColor:'#0d131e',
        borderWidth:2,
        hoverOffset:6
      }]
    },
    options:{
      responsive:true,maintainAspectRatio:false,cutout:'58%',
      plugins:{
        legend:{display:false},
        tooltip:{
          backgroundColor:'#151b27',borderColor:'#1e2d3d',borderWidth:1,
          titleColor:'#94a3b8',bodyColor:'#e2e8f0',
          bodyFont:{family:'Space Mono',size:11},
          callbacks:{
            label:c=>{
              const total = c.dataset.data.reduce((a,b)=>a+b,0);
              const pct   = total ? (c.parsed/total*100).toFixed(1) : 0;
              return ' ' + c.label + ': ' + c.parsed + ' (' + pct + '%)';
            }
          }
        }
      }
    }
  });
}

// ── Round 7b redesigned: Long / Short hero cards ───────────────────────
function _lsPnl(v) {
  const n = parseFloat(v) || 0;
  if (Math.abs(n) < 0.005) return '$0.00';
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(2);
}
function _lsWLN(side) {
  return `<span style="color:var(--teal)">${side.wins||0}</span>` +
         `<span style="color:var(--t3)"> / </span>` +
         `<span style="color:var(--red)">${side.losses||0}</span>` +
         `<span style="color:var(--t3)"> / </span>` +
         `<span style="color:var(--amber)">${side.neutral||0}</span>`;
}
function _setSidePnl(elId, val) {
  const n = parseFloat(val) || 0;
  const el = $(elId);
  el.textContent = _lsPnl(val);
  el.style.color = n > 0 ? 'var(--teal)' : n < 0 ? 'var(--red)' : 'var(--t3)';
}
function _setSideWR(elId, wr, trades) {
  const el = $(elId);
  el.textContent = (parseFloat(wr) || 0).toFixed(1) + '%';
  if (!trades) { el.style.color = 'var(--t3)'; return; }
  el.style.color = wr >= 50 ? 'var(--teal)' : wr >= 35 ? 'var(--amber)' : 'var(--red)';
}
function renderLongShort(ls) {
  if (!ls) return;
  const L = ls.long || {}, S = ls.short || {};

  $('lsLTrades').textContent = (L.trades||0) + ' trades';
  $('lsSTrades').textContent = (S.trades||0) + ' trades';

  _setSideWR('lsLWR', L.wr, L.trades);
  _setSideWR('lsSWR', S.wr, S.trades);

  _setSidePnl('lsLPnl', L.total_pnl);
  _setSidePnl('lsSPnl', S.total_pnl);

  $('lsLWLN').innerHTML = _lsWLN(L);
  $('lsSWLN').innerHTML = _lsWLN(S);
  $('lsLAvg').textContent  = _lsPnl(L.avg_pnl);
  $('lsSAvg').textContent  = _lsPnl(S.avg_pnl);
  $('lsLBest').textContent = _lsPnl(L.best);
  $('lsSBest').textContent = _lsPnl(S.best);
  $('lsLWorst').textContent= _lsPnl(L.worst);
  $('lsSWorst').textContent= _lsPnl(S.worst);
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
      <td style="color:var(--t2);font-size:12px">${i.strategy||'—'}</td>
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

// Round 9f/n: Strategy/Rating/Timeframe tables — uses signed Edge metric.
function _pnlClass(v){ return v > 0 ? 'sb-pos' : v < 0 ? 'sb-neg' : 'sb-zero'; }
function _pnlText(v) {
  const n = parseFloat(v) || 0;
  if (Math.abs(n) < 0.005) return '$0.00';
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(2);
}
// Round 9n: Edge = (wins - losses) / decided * 100. Range -100 to +100.
// -100% = every decided trade lost. +100% = every decided trade won. 0 = even.
function _edgeText(edge, trades) {
  if (!trades) return '—';
  const n = parseFloat(edge) || 0;
  return (n > 0 ? '+' : '') + n.toFixed(1) + '%';
}
function _edgeClass(edge, trades) {
  if (!trades) return 'sb-mute';
  const n = parseFloat(edge) || 0;
  return n > 0 ? 'sb-pos' : n < 0 ? 'sb-neg' : 'sb-zero';
}

function renderStrategyBreakdown(items) {
  const el = $('sbBody');
  if (!items || !items.length) {
    el.innerHTML = '<tr class="empty-row"><td colspan="4">No closed trades yet</td></tr>';
    return;
  }
  el.innerHTML = items.map(i => `<tr>
    <td class="sb-name">${i.name}</td>
    <td class="sb-mute">${i.trades}</td>
    <td class="${_edgeClass(i.edge, i.trades)}">${_edgeText(i.edge, i.trades)}</td>
    <td class="${_pnlClass(i.total_pnl)}">${_pnlText(i.total_pnl)}</td>
  </tr>`).join('');
}

function renderRatingPerformance(items) {
  const el = $('rpBody');
  if (!items || !items.length) {
    el.innerHTML = '<tr class="empty-row"><td colspan="4">No closed trades yet</td></tr>';
    return;
  }
  el.innerHTML = items.map(i => `<tr>
    <td class="rp-rating-cell"><span class="rating r-${i.rating}">${i.rating}</span></td>
    <td class="sb-mute">${i.trades}</td>
    <td class="${_edgeClass(i.edge, i.trades)}">${_edgeText(i.edge, i.trades)}</td>
    <td class="${_pnlClass(i.total_pnl)}">${_pnlText(i.total_pnl)}</td>
  </tr>`).join('');
}

// Round 9h: Timeframe Performance render
function renderTimeframePerformance(items) {
  const el = $('tfBody');
  if (!items || !items.length) {
    el.innerHTML = '<tr class="empty-row"><td colspan="4">No closed trades yet</td></tr>';
    return;
  }
  el.innerHTML = items.map(i => `<tr>
    <td class="rp-rating-cell"><span class="tf-tag">${i.tf}</span></td>
    <td class="sb-mute">${i.trades}</td>
    <td class="${_edgeClass(i.edge, i.trades)}">${_edgeText(i.edge, i.trades)}</td>
    <td class="${_pnlClass(i.total_pnl)}">${_pnlText(i.total_pnl)}</td>
  </tr>`).join('');
}

// Round 9d: BTC pill renders 1H / 4H / 1D arrows from d.btc_fg
function setBtcArrow(elId, side) {
  const el = $(elId);
  if (!el) return;
  let arr = '→', cls = 'neutral-btc';
  if (side === 'bullish')      { arr = '↑'; cls = 'bullish'; }
  else if (side === 'bearish') { arr = '↓'; cls = 'bearish'; }
  el.textContent = arr;
  el.className = 'arr ' + cls;
}

function render(d) {
  const fg=d.btc_fg;
  setBtcArrow('btc1h', fg['1h']);
  setBtcArrow('btc4h', fg['4h']);
  setBtcArrow('btc1d', fg['1d']);
  const fgEl=$('fgPill');
  fgEl.textContent=fg.fg+' — '+fg.fg_label;
  fgEl.className='v '+(fg.fg<20?'fg-fear':fg.fg>80?'fg-greed':'fg-neutral-col');

  const live=d.status==='LIVE';
  $('statusPill').textContent='● '+d.status;
  $('statusPill').className='pill '+(live?'pill-live':'pill-off');
  $('liveDot').className='live-dot'+(live?'':' off');
  $('updatedLbl').textContent='Updated '+d.updated;

  // Round 9e: unified KPI color rule — positive → green, negative → red,
  // zero/neutral → white. Pure-count KPIs (Open, Tokens, WR) stay white.
  // Drawdown is conceptually a loss, so any non-zero value is red.
  const COL_POS = 'var(--teal)';
  const COL_NEG = 'var(--red)';
  const COL_NEU = 'var(--t1)';
  const colorOf = v => v > 0 ? COL_POS : v < 0 ? COL_NEG : COL_NEU;

  const diff=d.capital-10000;
  const pct=(Math.abs(diff)/100).toFixed(2);
  $('mCap').textContent=fm(d.capital);
  $('mCap').style.color = colorOf(diff);
  $('mCapSub').innerHTML=`<span class="${diff>=0?'up':'dn'}">${diff>=0?'▲':'▼'} \$${Math.abs(diff).toFixed(2)} (${pct}%)</span> from start`;

  $('mOpen').textContent=d.open_count;
  $('mOpen').style.color = COL_NEU;
  $('mOpenSub').textContent=d.open_count?d.open_count+' active position'+(d.open_count>1?'s':''):'No open positions';

  // Tokens / Strategies — pure counts, white.
  $('mTokens').textContent = d.active_tokens||0;
  $('mStrats').textContent = d.strategy_types||0;
  $('mTokens').style.color = COL_NEU;
  $('mStrats').style.color = COL_NEU;
  $('mTokensSub').textContent = 'of top 100 scanning';

  $('mWR').textContent=d.stats.wr+'%';
  $('mWR').style.color = COL_NEU;
  $('mWRSub').textContent=d.stats.wins+'W  '+d.stats.losses+'L  '+d.stats.neutral+'N  of  '+d.stats.total;

  // Drawdown — non-zero = loss = red, zero = white. Never green.
  $('mDD').textContent=d.drawdown+'%';
  $('mDD').style.color = d.drawdown > 0 ? COL_NEG : COL_NEU;
  $('mDDSub').textContent='Peak '+fm(d.peak);

  const tp=d.stats.total_pnl;
  $('mTotalPnl').textContent=(tp>=0?'+':'-')+'\$'+Math.abs(tp).toFixed(2);
  $('mTotalPnl').style.color = colorOf(tp);
  $('mTotalSub').textContent=tp>0?'All-time profitable':tp<0?'All-time in loss':'All-time flat';

  const tdp=d.today_pnl;
  $('mTodayPnl').textContent=(tdp>=0?'+':'-')+'\$'+Math.abs(tdp).toFixed(2);
  $('mTodayPnl').style.color = colorOf(tdp);
  $('mTodaySub').textContent=tdp===0?'No closed trades today':tdp>0?'Profitable today':'Loss today';

  const up=d.unrealized_pnl;
  $('mUnreal').textContent=(up>=0?'+':'-')+'\$'+Math.abs(up).toFixed(2);
  $('mUnreal').style.color = colorOf(up);
  $('mUnrealSub').textContent=d.open_count>0?d.open_count+' open position'+(d.open_count>1?'s':''):'No open positions';

  allEquity=d.equity;
  renderEquity(filterEquityPts(allEquity,curPeriod));
  renderOpen(d.open_trades);
  renderClosed(d.closed_trades);
  // Round 7: replaced renderStats(...) with two new panels
  renderSignalJournal(d.signal_journal);
  renderLongShort(d.long_short);
  renderBreakdown(d.breakdown);
  // Round 9f: replaced renderFilters(...) with two new panels
  // Round 9h: added Timeframe Performance below Rating Performance
  renderStrategyBreakdown(d.strategy_breakdown);
  renderRatingPerformance(d.rating_performance);
  renderTimeframePerformance(d.timeframe_performance);
  $('footer').innerHTML='APEX v3.5 &middot; build r9n · Indigo Aurora v2 &nbsp;&middot;&nbsp; Updated '+d.updated+' &nbsp;&middot;&nbsp; Auto-refresh 30s';
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
