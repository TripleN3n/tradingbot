#!/usr/bin/env python3
"""
APEX Dashboard Fix Patch
Fixes 6 issues in dashboard.py
"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

issues = []

# ── FIX 1 — Portfolio value reads wrong table ──────────────────────────────
old1 = '''def get_capital(conn):
    try:
        r = conn.execute("SELECT capital FROM bot_state ORDER BY id DESC LIMIT 1").fetchone()
        return float(r[0]) if r else INITIAL_CAPITAL
    except: return INITIAL_CAPITAL'''

new1 = '''def get_capital(conn):
    try:
        r = conn.execute("SELECT capital FROM portfolio ORDER BY id DESC LIMIT 1").fetchone()
        if r: return float(r[0])
        r = conn.execute("SELECT capital FROM bot_state ORDER BY id DESC LIMIT 1").fetchone()
        return float(r[0]) if r else INITIAL_CAPITAL
    except: return INITIAL_CAPITAL'''

if old1 in txt:
    txt = txt.replace(old1, new1, 1)
    issues.append('FIX1 ✓ Portfolio value — reads portfolio table now')
else:
    issues.append('FIX1 SKIP — pattern not found (may already be fixed)')

# ── FIX 2 — P&L Distribution transparent bgcolor error ────────────────────
old2 = '                paper_bgcolor="transparent",plot_bgcolor="transparent",'
new2 = '                paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)",'

if old2 in txt:
    txt = txt.replace(old2, new2, 1)
    issues.append('FIX2 ✓ P&L Distribution — paper_bgcolor fixed')
else:
    issues.append('FIX2 SKIP — pattern not found (may already be fixed)')

# ── FIX 3 — Live Profit Factor shows 0.00 when no losses ──────────────────
old3 = 'live_pf  = (avg_win*len(wins))/(avg_loss*len(losses)) if avg_loss>0 and len(losses)>0 else 0.0'
new3 = 'live_pf  = (avg_win*len(wins))/(avg_loss*len(losses)) if avg_loss>0 and len(losses)>0 else (999.0 if len(wins)>0 else 0.0)'

if old3 in txt:
    txt = txt.replace(old3, new3, 1)
    issues.append('FIX3 ✓ Profit Factor — infinity when no losses')
else:
    issues.append('FIX3 SKIP — pattern not found')

# ── FIX 4 — Display infinity symbol in PF card ────────────────────────────
old4 = "pfc  = \"val-green\" if live_pf>=1.5 else \"val-gold\" if live_pf>=1 else \"val-red\""
new4 = """pfc  = "val-green" if live_pf>=1.5 else "val-gold" if live_pf>=1 else "val-red"
live_pf_str = "∞" if live_pf>=999 else f"{live_pf:.2f}" """

if old4 in txt:
    txt = txt.replace(old4, new4, 1)
    issues.append('FIX4 ✓ live_pf_str variable added')
else:
    issues.append('FIX4 SKIP — pattern not found')

# ── FIX 5 — Use live_pf_str in metric card ────────────────────────────────
old5 = '    <div class="mcard-value {pfc}">{live_pf:.2f}</div>'
new5 = '    <div class="mcard-value {pfc}">{live_pf_str}</div>'

if old5 in txt:
    txt = txt.replace(old5, new5, 1)
    issues.append('FIX5 ✓ Metric card uses live_pf_str')
else:
    issues.append('FIX5 SKIP — pattern not found')

# ── FIX 6 — Open Positions tracking count ────────────────────────────────
old6 = "        st.markdown('<div class=\"sec-title\">Open Positions</div>', unsafe_allow_html=True)"
new6 = """        _track_count = len(strategies) if not strategies.empty else 0
        st.markdown(f'<div class="sec-title">Open Positions <span style="color:var(--dim);font-size:0.52rem;font-weight:400;margin-left:0.5rem">· Tracking {_track_count} Tokens</span></div>', unsafe_allow_html=True)"""

if old6 in txt:
    txt = txt.replace(old6, new6, 1)
    issues.append('FIX6 ✓ Tracking token count added')
else:
    issues.append('FIX6 SKIP — pattern not found')

# ── Write file ─────────────────────────────────────────────────────────────
f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()

print('\n=== APEX Dashboard Fix Results ===')
for issue in issues:
    print(f'  {issue}')
print('==================================\n')
