#!/usr/bin/env python3
"""Reorder Open Positions columns to: COIN SIDE RATING TIER STRATEGY INDICATORS OPENED ENTRY STOP CURRENT P&L BARS LEFT"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# Fix header order
old1 = '''            <div class="op-head">
                <span>COIN</span><span>SIDE</span><span>RATING</span><span>TIER</span>
                <span>STRATEGY</span><span>INDICATORS</span>
                <span>ENTRY</span><span>OPENED</span><span>CURRENT</span>
                <span>P&amp;L</span><span>STOP</span>
                <span>LEFT</span><span>BARS</span>
            </div>'''

new1 = '''            <div class="op-head">
                <span>COIN</span><span>SIDE</span><span>RATING</span><span>TIER</span>
                <span>STRATEGY</span><span>INDICATORS</span>
                <span>OPENED</span><span>ENTRY</span><span>STOP</span>
                <span>CURRENT</span><span>P&amp;L</span>
                <span>BARS</span><span>LEFT</span>
            </div>'''

if old1 in txt:
    txt = txt.replace(old1, new1, 1)
    print('FIX1 header done')
else:
    print('FIX1 not found')

# Fix row data order
old2 = '''                <span style="color:var(--text);font-weight:700">{sym}</span>
                <span><span class="{dc}" style="padding:0.1rem 0.4rem;border-radius:3px;font-size:0.58rem">{d.title()}</span></span>
                <span style="color:{rating_col};font-weight:800;font-size:0.85rem">{rating}</span>
                <span style="color:{tier_col};font-weight:600;font-size:0.62rem">{tier_label}</span>
                <span style="color:var(--blue);font-size:0.6rem;font-weight:600">{strat_name} <span style="color:var(--blue);opacity:0.7">{tf}</span></span>
                <span style="color:var(--mid);font-size:0.52rem">{indicators}</span>
                <span style="color:#c8d8e8;font-size:0.64rem;font-weight:600">${e:,.4f}</span>
                <span style="color:var(--mid);font-size:0.62rem">{entry_dt}</span>
                <span style="color:var(--mid)">${cur:,.4f}</span>
                <span class="{uc}" style="font-weight:600">{us}${up:,.2f} <span style="font-size:0.55rem">({us}{up_pct:.1f}%)</span></span>
                <span style="color:var(--mid)">${sl:,.4f} <span style="font-size:0.52rem;color:var(--dim)">({sl_pct:.0f}%)</span></span>
                <span style="color:{bl_color};font-weight:600">{bars_left}</span>
                <span style="color:var(--dim)">{bars}</span>'''

new2 = '''                <span style="color:var(--text);font-weight:700">{sym}</span>
                <span><span class="{dc}" style="padding:0.1rem 0.4rem;border-radius:3px;font-size:0.58rem">{d.title()}</span></span>
                <span style="color:{rating_col};font-weight:800;font-size:0.85rem">{rating}</span>
                <span style="color:{tier_col};font-weight:600;font-size:0.62rem">{tier_label}</span>
                <span style="color:var(--blue);font-size:0.6rem;font-weight:600">{strat_name} <span style="color:var(--blue);opacity:0.7">{tf}</span></span>
                <span style="color:var(--mid);font-size:0.52rem">{indicators}</span>
                <span style="color:var(--mid);font-size:0.62rem">{entry_dt}</span>
                <span style="color:#c8d8e8;font-size:0.64rem;font-weight:600">${e:,.4f}</span>
                <span style="color:var(--mid)">${sl:,.4f} <span style="font-size:0.52rem;color:var(--dim)">({sl_pct:.0f}%)</span></span>
                <span style="color:var(--mid)">${cur:,.4f}</span>
                <span class="{uc}" style="font-weight:600">{us}${up:,.2f} <span style="font-size:0.55rem">({us}{up_pct:.1f}%)</span></span>
                <span style="color:var(--dim)">{bars}</span>
                <span style="color:{bl_color};font-weight:600">{bars_left}</span>'''

if old2 in txt:
    txt = txt.replace(old2, new2, 1)
    print('FIX2 row order done')
else:
    print('FIX2 not found')

f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done')
