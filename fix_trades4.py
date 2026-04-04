#!/usr/bin/env python3
"""
Fix column widths and make scrollbar visible
Key issues:
1. REASON column too wide — badge stretching it
2. Scrollbar not visible — CSS not being applied
"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# Replace the entire CSS + container block
old = '''        st.markdown(f"""
        <style>
        .scr-trades {{ max-height:440px; overflow-y:auto;
            border:1px solid var(--border); border-radius:4px; }}
        .scr-trades::-webkit-scrollbar {{ width:8px; }}
        .scr-trades::-webkit-scrollbar-track {{ background:#0d1117; border-radius:4px; }}
        .scr-trades::-webkit-scrollbar-thumb {{ background:#2a3a4a; border-radius:4px;
            border:2px solid #0d1117; }}
        .scr-trades::-webkit-scrollbar-thumb:hover {{ background:#58a6ff; }}
        .scr-trades {{ scrollbar-width:thin; scrollbar-color:#2a3a4a #0d1117; }}
        .trow-trades {{ display:grid;
            grid-template-columns:1.2fr 0.7fr 0.6fr 0.6fr 1.4fr 2fr 1fr 1fr 1.3fr 1fr 0.9fr;
            gap:0.5rem; padding:0.55rem 0.8rem;
            border-bottom:1px solid var(--border);
            font-family:var(--mono); font-size:0.66rem; align-items:center; }}
        .trow-trades:hover {{ background:var(--bg3); }}
        .trow-trades-h {{ background:rgba(30,42,56,0.7)!important;
            color:var(--mid)!important; font-size:0.54rem!important;
            letter-spacing:0.12em; text-transform:uppercase;
            border-bottom:2px solid var(--border2)!important;
            padding:0.5rem 0.8rem!important; position:sticky; top:0; z-index:1; }}
        </style>
        <div class="scr-trades">
            <div class="trow-trades trow-trades-h">
                <span>TOKEN</span><span>SIDE</span><span>RATING</span><span>TIER</span>
                <span>STRATEGY</span><span>INDICATORS</span>
                <span>ENTRY</span><span>EXIT</span><span>REASON</span>
                <span>P&amp;L</span><span>RETURN</span>
            </div>
            {rows}
        </div>
        """, unsafe_allow_html=True)'''

new = '''        trades_html = f"""
        <style>
        #trades-wrap {{
            max-height: 440px;
            overflow-y: scroll;
            border: 1px solid #1e2a38;
            border-radius: 4px;
            background: #0d1117;
        }}
        #trades-wrap::-webkit-scrollbar {{
            width: 8px;
            display: block;
        }}
        #trades-wrap::-webkit-scrollbar-track {{
            background: #111820;
        }}
        #trades-wrap::-webkit-scrollbar-thumb {{
            background: #2a3a4a;
            border-radius: 4px;
        }}
        #trades-wrap::-webkit-scrollbar-thumb:hover {{
            background: #58a6ff;
        }}
        .tr-h, .tr-r {{
            display: grid;
            grid-template-columns: 90px 60px 55px 45px 130px 180px 90px 90px 100px 80px 70px;
            gap: 0;
            padding: 0.5rem 0.8rem;
            border-bottom: 1px solid #1e2a38;
            font-family: 'JetBrains Mono', monospace;
            align-items: center;
        }}
        .tr-h {{
            background: rgba(30,42,56,0.9);
            color: #6b7a8d;
            font-size: 0.52rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            border-bottom: 2px solid #243040;
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        .tr-r {{
            font-size: 0.64rem;
        }}
        .tr-r:hover {{ background: #111820; }}
        </style>
        <div id="trades-wrap">
            <div class="tr-h">
                <span>TOKEN</span>
                <span>SIDE</span>
                <span>RATING</span>
                <span>TIER</span>
                <span>STRATEGY</span>
                <span>INDICATORS</span>
                <span>ENTRY</span>
                <span>EXIT</span>
                <span>REASON</span>
                <span>P&amp;L</span>
                <span>RETURN</span>
            </div>
            {rows}
        </div>
        """
        st.markdown(trades_html, unsafe_allow_html=True)'''

if old in txt:
    txt = txt.replace(old, new, 1)
    print('FIX1 container done')
else:
    print('FIX1 not found')

# Fix row div to use tr-r class
old2 = "f'<div class=\"trow-trades\">'"
new2 = "f'<div class=\"tr-r\">'"
if old2 in txt:
    txt = txt.replace(old2, new2, 1)
    print('FIX2 row class done')
else:
    print('FIX2 not found')

# Fix reason badge — make it compact, no border stretch
old3 = '''            reason_badge = (f\'<span style="color:{rc};background:{rbg};padding:0.12rem 0.4rem;\' +
                           f\'border-radius:2px;font-size:0.5rem;font-weight:700;\' +
                           f\'letter-spacing:0.06em;border:1px solid {rc};white-space:nowrap">{rlbl}</span>\')'''
new3 = '''            reason_badge = (f\'<span style="color:{rc};background:{rbg};\' +
                           f\'padding:0.1rem 0.35rem;border-radius:2px;\' +
                           f\'font-size:0.48rem;font-weight:700;\' +
                           f\'letter-spacing:0.05em;border:1px solid {rc};\' +
                           f\'white-space:nowrap;display:inline-block;max-width:95px;\' +
                           f\'overflow:hidden;text-overflow:ellipsis">{rlbl}</span>\')'''
if old3 in txt:
    txt = txt.replace(old3, new3, 1)
    print('FIX3 badge done')
else:
    print('FIX3 not found')

f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done')
