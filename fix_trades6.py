#!/usr/bin/env python3
"""Add Entry Time and Exit Time columns to Recent Closed Trades"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# Update header
old1 = '''            <div class="tr-h">
                <span>TOKEN</span><span>SIDE</span><span>RATING</span><span>TIER</span>
                <span>STRATEGY</span><span>INDICATORS</span>
                <span>ENTRY</span><span>EXIT</span><span>REASON</span>
                <span>P&amp;L</span><span>RETURN</span>
            </div>'''

new1 = '''            <div class="tr-h">
                <span>TOKEN</span><span>SIDE</span><span>RATING</span><span>TIER</span>
                <span>STRATEGY</span><span>INDICATORS</span>
                <span>ENTRY</span><span>EXIT</span><span>OPENED</span><span>CLOSED</span>
                <span>REASON</span><span>P&amp;L</span><span>RETURN</span>
            </div>'''

if old1 in txt:
    txt = txt.replace(old1, new1, 1)
    print('FIX1 header done')
else:
    print('FIX1 not found')

# Update grid columns to add 2 more
old2 = '''        .tr-h, .tr-r {
            display: grid;
            grid-template-columns: 1.1fr 0.7fr 0.6fr 0.5fr 1.3fr 1.8fr 0.9fr 0.9fr 1fr 0.9fr 0.8fr;'''

new2 = '''        .tr-h, .tr-r {
            display: grid;
            grid-template-columns: 0.9fr 0.6fr 0.5fr 0.4fr 1.1fr 1.5fr 0.8fr 0.8fr 1.1fr 1.1fr 0.9fr 0.8fr 0.7fr;'''

if old2 in txt:
    txt = txt.replace(old2, new2, 1)
    print('FIX2 grid done')
else:
    print('FIX2 not found')

# Update row data to include entry_time and exit_time
old3 = '''                f\'<span style="color:#c8d8e8;font-size:0.64rem;font-weight:600">${e:,.4f}</span>\' +
                f\'<span style="color:#a8bfd0;font-size:0.64rem">${ex:,.4f}</span>\' +
                f\'{reason_badge}\' +
                f\'<span class="{pc2}" style="font-weight:600">{sg}${pnl:,.2f}</span>\' +
                f\'<span class="{pc2}">{sg}{pct:.2f}%</span>\' +'''

new3 = '''                f\'<span style="color:#c8d8e8;font-size:0.64rem;font-weight:600">${e:,.4f}</span>\' +
                f\'<span style="color:#a8bfd0;font-size:0.64rem">${ex:,.4f}</span>\' +
                f\'<span style="color:var(--mid);font-size:0.55rem;line-height:1.4">{entry_dt}</span>\' +
                f\'<span style="color:var(--dim);font-size:0.55rem;line-height:1.4">{exit_dt}</span>\' +
                f\'{reason_badge}\' +
                f\'<span class="{pc2}" style="font-weight:600">{sg}${pnl:,.2f}</span>\' +
                f\'<span class="{pc2}">{sg}{pct:.2f}%</span>\' +'''

if old3 in txt:
    txt = txt.replace(old3, new3, 1)
    print('FIX3 row data done')
else:
    print('FIX3 not found')

# Add entry_dt and exit_dt variables before the rows += block
old4 = '''            reason_badge = (f\'<span style="color:{rc};background:{rbg};\' +'''

new4 = '''            # Format entry/exit times
            try:
                from datetime import datetime, timezone
                et_raw = str(row.get("entry_time",""))
                xt_raw = str(row.get("exit_time",""))
                def fmt_dt(s):
                    if not s or s == "None": return "—"
                    try:
                        dt = datetime.fromisoformat(s.replace("Z","+00:00"))
                        return dt.strftime("%d %b %H:%M")
                    except: return s[:13]
                entry_dt = fmt_dt(et_raw)
                exit_dt  = fmt_dt(xt_raw)
            except:
                entry_dt = "—"
                exit_dt  = "—"

            reason_badge = (f\'<span style="color:{rc};background:{rbg};\' +'''

if old4 in txt:
    txt = txt.replace(old4, new4, 1)
    print('FIX4 datetime vars done')
else:
    print('FIX4 not found')

f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done')
