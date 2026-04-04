#!/usr/bin/env python3
"""Fix: Restore scrollbar visibility and fix column spacing"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# Fix the scrollbar CSS and container - it was removed when streamlit restarted
old = '''        st.markdown(f"""
        <style>
        .scr-trades::-webkit-scrollbar {{ width: 6px; }}
        .scr-trades::-webkit-scrollbar-track {{ background: #0d1117; border-radius: 3px; }}
        .scr-trades::-webkit-scrollbar-thumb {{ background: #243040; border-radius: 3px; }}
        .scr-trades::-webkit-scrollbar-thumb:hover {{ background: #58a6ff; }}
        .scr-trades {{ scrollbar-width: thin; scrollbar-color: #243040 #0d1117; }}
        </style>
        <div class="scr-trades" style="max-height:420px;overflow-y:auto;
            border:1px solid var(--border);border-radius:4px;">
            {h}{rows}
        </div>
        """, unsafe_allow_html=True)'''

new = '''        st.markdown(f"""
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

if old in txt:
    txt = txt.replace(old, new, 1)
    print('FIX1 scrollbar + container done')
else:
    print('FIX1 not found — trying alternate')
    # Try to find and fix just the container div
    old2 = '''        <div class="scr-trades" style="max-height:420px;overflow-y:auto;
            border:1px solid var(--border);border-radius:4px;">
            {h}{rows}
        </div>'''
    new2 = '''        <div class="scr-trades">
            {rows}
        </div>'''
    if old2 in txt:
        txt = txt.replace(old2, new2, 1)
        print('FIX1 alt done')

# Fix row grid to match new header
old3 = 'f\'<div class="trow" style="grid-template-columns:1fr 0.6fr 0.5fr 0.5fr 1.2fr 1.5fr 0.9fr 0.9fr 1.1fr 0.9fr 0.9fr;">\''
new3 = 'f\'<div class="trow-trades">\''
if old3 in txt:
    txt = txt.replace(old3, new3, 1)
    print('FIX2 row grid done')
else:
    print('FIX2 not found')

# Fix header - remove old h variable since we hardcoded it in the container
old4 = '''        h = (\'<div class="trow th" style="grid-template-columns:1fr 0.6fr 0.5fr 0.5fr 1.2fr 1.5fr 0.9fr 0.9fr 1.1fr 0.9fr 0.9fr;">\' +
             \'<span>TOKEN</span><span>SIDE</span><span>RATING</span><span>TIER</span>\' +
             \'<span>STRATEGY</span><span>INDICATORS</span>\' +
             \'<span>ENTRY</span><span>EXIT</span><span>REASON</span><span>P&L</span><span>RETURN</span></div>\')'''
new4 = '        h = ""  # header now inside scr-trades container'
if old4 in txt:
    txt = txt.replace(old4, new4, 1)
    print('FIX3 header variable done')
else:
    print('FIX3 not found')

f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done')
