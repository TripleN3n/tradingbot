#!/usr/bin/env python3
"""Fix: Separate Entry/Exit into 2 columns, add Reason badge back"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

old = '''        h = (\'<div class="trow th" style="grid-template-columns:1fr 0.6fr 0.5fr 0.5fr 1.2fr 1.8fr 1fr 1fr 1fr;">\' +
             \'<span>TOKEN</span><span>SIDE</span><span>RATING</span><span>TIER</span>\' +
             \'<span>STRATEGY</span><span>INDICATORS</span>\' +
             \'<span>ENTRY / EXIT</span><span>P&L</span><span>RETURN</span></div>\')'''

new = '''        h = (\'<div class="trow th" style="grid-template-columns:1fr 0.6fr 0.5fr 0.5fr 1.2fr 1.5fr 0.9fr 0.9fr 1.1fr 0.9fr 0.9fr;">\' +
             \'<span>TOKEN</span><span>SIDE</span><span>RATING</span><span>TIER</span>\' +
             \'<span>STRATEGY</span><span>INDICATORS</span>\' +
             \'<span>ENTRY</span><span>EXIT</span><span>REASON</span><span>P&L</span><span>RETURN</span></div>\')'''

if old in txt:
    txt = txt.replace(old, new, 1)
    print('FIX1 header done')
else:
    print('FIX1 not found')

old2 = '''            rows += (
                f\'<div class="trow" style="grid-template-columns:1fr 0.6fr 0.5fr 0.5fr 1.2fr 1.8fr 1fr 1fr 1fr;">\' +
                f\'<span style="color:var(--text);font-weight:700">{sym}</span>\' +
                f\'<span><span class="{dc}" style="padding:0.1rem 0.4rem;border-radius:3px;font-size:0.58rem">{d.title()}</span></span>\' +
                f\'<span style="color:{rating_col};font-weight:800;font-size:0.85rem">{rating}</span>\' +
                f\'<span style="color:{tier_col};font-weight:600;font-size:0.62rem">{tier_label}</span>\' +
                f\'<span style="color:var(--blue);font-size:0.6rem;font-weight:600">{strat_name}</span>\' +
                f\'<span style="color:var(--mid);font-size:0.52rem">{indicators}</span>\' +
                f\'<span style="color:#c8d8e8;font-size:0.62rem">${e:,.4f}<br>\' +
                f\'<span style="color:#a0b4c8">${ex:,.4f}</span></span>\' +
                f\'<span class="{pc2}" style="font-weight:600">{sg}${pnl:,.2f}</span>\' +
                f\'<span class="{pc2}">{sg}{pct:.2f}%</span>\' +
                f\'</div>\'
            )'''

new2 = '''            rows += (
                f\'<div class="trow" style="grid-template-columns:1fr 0.6fr 0.5fr 0.5fr 1.2fr 1.5fr 0.9fr 0.9fr 1.1fr 0.9fr 0.9fr;">\' +
                f\'<span style="color:var(--text);font-weight:700">{sym}</span>\' +
                f\'<span><span class="{dc}" style="padding:0.1rem 0.4rem;border-radius:3px;font-size:0.58rem">{d.title()}</span></span>\' +
                f\'<span style="color:{rating_col};font-weight:800;font-size:0.85rem">{rating}</span>\' +
                f\'<span style="color:{tier_col};font-weight:600;font-size:0.62rem">{tier_label}</span>\' +
                f\'<span style="color:var(--blue);font-size:0.6rem;font-weight:600">{strat_name}</span>\' +
                f\'<span style="color:var(--mid);font-size:0.52rem">{indicators}</span>\' +
                f\'<span style="color:#c8d8e8;font-size:0.64rem;font-weight:600">${e:,.4f}</span>\' +
                f\'<span style="color:#a8bfd0;font-size:0.64rem">${ex:,.4f}</span>\' +
                f\'{reason_badge}\' +
                f\'<span class="{pc2}" style="font-weight:600">{sg}${pnl:,.2f}</span>\' +
                f\'<span class="{pc2}">{sg}{pct:.2f}%</span>\' +
                f\'</div>\'
            )'''

if old2 in txt:
    txt = txt.replace(old2, new2, 1)
    print('FIX2 rows done')
else:
    print('FIX2 not found')

f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done')
