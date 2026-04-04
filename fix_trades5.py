#!/usr/bin/env python3
"""Fix trades table: full width, even columns, visible scrollbar"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

old = '''        trades_html = f"""
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

new = '''        trades_html = f"""
        <style>
        .tw {{
            width: 100%;
            height: 440px;
            overflow-y: auto;
            overflow-x: hidden;
            border: 1px solid #1e2a38;
            border-radius: 4px;
            background: #0d1117;
            box-sizing: border-box;
        }}
        .tw::-webkit-scrollbar {{ width: 8px; }}
        .tw::-webkit-scrollbar-track {{ background: #0d1117; }}
        .tw::-webkit-scrollbar-thumb {{
            background: #3a4a5a;
            border-radius: 4px;
        }}
        .tw::-webkit-scrollbar-thumb:hover {{ background: #58a6ff; }}
        .tr-h, .tr-r {{
            display: grid;
            grid-template-columns: 1.1fr 0.7fr 0.6fr 0.5fr 1.3fr 1.8fr 0.9fr 0.9fr 1fr 0.9fr 0.8fr;
            padding: 0.5rem 0.8rem;
            border-bottom: 1px solid #1e2a38;
            font-family: 'JetBrains Mono', monospace;
            align-items: center;
            width: 100%;
            box-sizing: border-box;
        }}
        .tr-h {{
            background: #0f1923;
            color: #6b7a8d;
            font-size: 0.52rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            border-bottom: 2px solid #243040;
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        .tr-r {{ font-size: 0.64rem; }}
        .tr-r:hover {{ background: #111820; }}
        </style>
        <div class="tw">
            <div class="tr-h">
                <span>TOKEN</span><span>SIDE</span><span>RATING</span><span>TIER</span>
                <span>STRATEGY</span><span>INDICATORS</span>
                <span>ENTRY</span><span>EXIT</span><span>REASON</span>
                <span>P&amp;L</span><span>RETURN</span>
            </div>
            {rows}
        </div>
        """
        st.markdown(trades_html, unsafe_allow_html=True)'''

if old in txt:
    txt = txt.replace(old, new, 1)
    print('Done')
else:
    print('Pattern not found')

f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
