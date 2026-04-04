#!/usr/bin/env python3
"""
Open Positions redesign:
- Add Tier, Indicators, Entry DateTime, Remaining Bars columns
- Full width, equal column spacing
- Visible scrollbar
- Show 6 positions at a time
"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# Find the open positions section
start = txt.find('# =============================================================================\n# OPEN POSITIONS')
end   = txt.find('# =============================================================================\n# RECENT TRADES')

if start == -1 or end == -1:
    print(f'ERROR: start={start}, end={end}')
    exit(1)

new_section = '''# =============================================================================
# OPEN POSITIONS
# =============================================================================

with st.container(border=True):
    _track_count = len(strategies) if not strategies.empty else 0
    st.markdown(f\'<div class="sec-title">Open Positions <span style="color:var(--dim);font-size:0.52rem;font-weight:400;margin-left:0.5rem">· Tracking {_track_count} Tokens</span></div>\', unsafe_allow_html=True)

    if not open_tr.empty:
        try:
            cur_prices = cur_prices_cached if cur_prices_cached else {}
        except:
            cur_prices = {}

        strat_map = {}
        if not strategies.empty:
            for _, sr in strategies.iterrows():
                strat_map[sr["symbol"]] = sr

        rows = ""
        total_unreal = 0.0

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

            # P&L
            if d == "LONG":
                up = (cur - e) * qty * lev
            else:
                up = (e - cur) * qty * lev
            total_unreal += up
            up_pct = (up / (e * qty)) * 100 if e > 0 and qty > 0 else 0
            uc = "val-green" if up >= 0 else "val-red"
            us = "+" if up >= 0 else ""

            # Rating
            rating_map = {"tier1":"A","tier2":"B","tier3":"C"}
            rating     = rating_map.get(tier,"B")
            rating_col = {"A":"var(--green)","B":"var(--blue)","C":"var(--gold)"}.get(rating,"var(--mid)")

            # Tier label
            tier_label = tier.replace("tier","T")
            tier_col   = {"tier1":"var(--green)","tier2":"var(--blue)","tier3":"var(--purple)"}.get(tier,"var(--mid)")

            # Strategy + indicators
            sr_info    = strat_map.get(full_sym, {})
            if hasattr(sr_info, "get"):
                strat_name = str(sr_info.get("strategy","—")).split(" + ")[0][:16]
                indicators = str(sr_info.get("indicator_combo","—"))[:28]
            else:
                strat_name = "—"
                indicators = "—"

            # Stop % distance
            sl_pct = abs(sl - e) / e * 100 if sl > 0 and e > 0 else 0

            # Entry datetime
            try:
                from datetime import datetime, timezone
                et_raw = str(row.get("entry_time",""))
                if et_raw and et_raw != "None":
                    dt = datetime.fromisoformat(et_raw.replace("Z","+00:00"))
                    entry_dt = dt.strftime("%d %b %H:%M")
                else:
                    entry_dt = "—"
            except:
                entry_dt = "—"

            # Remaining bars
            from bot.config import TIME_STOP_CANDLES
            tf_lower   = tf.lower()
            time_limit = TIME_STOP_CANDLES.get(tf_lower, 24)
            bars_left  = max(0, time_limit - bars)
            bl_color   = "var(--red)" if bars_left <= 3 else "var(--gold)" if bars_left <= 8 else "var(--dim)"

            rows += f"""<div class="op-row">
                <span style="color:var(--text);font-weight:700">{sym}</span>
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
                <span style="color:var(--dim)">{bars}</span>
            </div>"""

        tu_c = "val-green" if total_unreal >= 0 else "val-red"
        tu_s = "+" if total_unreal >= 0 else ""

        st.markdown(f"""
        <style>
        .op-wrap {{
            width: 100%;
            height: 295px;
            overflow-y: auto;
            overflow-x: hidden;
            border: 1px solid #1e2a38;
            border-radius: 4px;
            background: #0d1117;
        }}
        .op-wrap::-webkit-scrollbar {{ width: 8px; }}
        .op-wrap::-webkit-scrollbar-track {{ background: #0d1117; }}
        .op-wrap::-webkit-scrollbar-thumb {{
            background: #3a4a5a;
            border-radius: 4px;
        }}
        .op-wrap::-webkit-scrollbar-thumb:hover {{ background: #58a6ff; }}
        .op-head, .op-row {{
            display: grid;
            grid-template-columns: 0.8fr 0.6fr 0.5fr 0.4fr 1.1fr 1.5fr 0.8fr 0.9fr 0.8fr 1.1fr 0.9fr 0.5fr 0.5fr;
            padding: 0.5rem 0.8rem;
            border-bottom: 1px solid #1e2a38;
            font-family: 'JetBrains Mono', monospace;
            align-items: center;
            width: 100%;
            box-sizing: border-box;
        }}
        .op-head {{
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
        .op-row {{ font-size: 0.64rem; }}
        .op-row:hover {{ background: #111820; }}
        </style>
        <div class="op-wrap">
            <div class="op-head">
                <span>COIN</span><span>SIDE</span><span>RATING</span><span>TIER</span>
                <span>STRATEGY</span><span>INDICATORS</span>
                <span>ENTRY</span><span>OPENED</span><span>CURRENT</span>
                <span>P&amp;L</span><span>STOP</span>
                <span>LEFT</span><span>BARS</span>
            </div>
            {rows}
        </div>
        <div style="text-align:right;padding:0.4rem 0.8rem;font-family:var(--mono);
            font-size:0.58rem;color:var(--dim)">
            Unrealized: <span class="{tu_c}" style="font-weight:700">{tu_s}${total_unreal:,.2f}</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(\'<div class="empty">NO OPEN POSITIONS — WAITING FOR SIGNALS</div>\', unsafe_allow_html=True)

'''

txt = txt[:start] + new_section + txt[end:]
f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done — Open Positions redesigned')
