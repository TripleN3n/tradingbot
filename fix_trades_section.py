#!/usr/bin/env python3
"""
APEX Dashboard - Recent Closed Trades Redesign
1. Full width, Strategy Breakdown moved below
2. New columns: Rating, Tier, Strategy, Indicators
3. Brighter entry/exit colors
4. Visible scrollbar
5. Show 15 trades minimum
"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# ── Find the RECENT TRADES + STRATEGY section ─────────────────────────────
start = txt.find('# =============================================================================\n# RECENT TRADES')
end   = txt.find('# =============================================================================\n# RATING PERFORMANCE')

if start == -1 or end == -1:
    print(f'ERROR: start={start}, end={end}')
    exit(1)

new_section = '''# =============================================================================
# RECENT TRADES + STRATEGY BREAKDOWN
# =============================================================================

# ── Recent Closed Trades — full width ────────────────────────────────────
with st.container(border=True):
    st.markdown('<div class="sec-title">Recent Closed Trades</div>', unsafe_allow_html=True)
    if pnl_ok and total_tr > 0:
        # Build strategy map for extra columns
        strat_map = {}
        if not strategies.empty:
            for _, sr in strategies.iterrows():
                strat_map[sr["symbol"]] = sr

        h = (\'<div class="trow th" style="grid-template-columns:1fr 0.6fr 0.5fr 0.5fr 1.2fr 1.8fr 1fr 1fr 1fr;">\' +
             \'<span>TOKEN</span><span>SIDE</span><span>RATING</span><span>TIER</span>\' +
             \'<span>STRATEGY</span><span>INDICATORS</span>\' +
             \'<span>ENTRY / EXIT</span><span>P&L</span><span>RETURN</span></div>\')

        reason_styles = {
            "stop_loss":       ("#ff3d6b", "rgba(255,61,107,0.12)",  "SL HIT"),
            "take_profit":     ("#00e5a0", "rgba(0,229,160,0.12)",   "TP HIT"),
            "partial_tp":      ("#00e5a0", "rgba(0,229,160,0.08)",   "PARTIAL TP"),
            "time_stop":       ("#f5a623", "rgba(245,166,35,0.12)",  "TIME STOP"),
            "circuit_breaker": ("#ff3d6b", "rgba(255,61,107,0.12)",  "CIRCUIT BRK"),
            "emergency_stop":  ("#ff3d6b", "rgba(255,61,107,0.12)",  "EMERGENCY"),
            "manual":          ("#58a6ff", "rgba(88,166,255,0.12)",  "MANUAL"),
        }

        rows = ""
        for _, row in closed_tr.head(15).iterrows():
            sym      = str(row.get("symbol","")).replace("/USDT:USDT","")
            full_sym = str(row.get("symbol",""))
            d        = str(row.get("direction","")).upper()
            dc       = "dl" if d == "LONG" else "ds"
            tier     = str(row.get("tier","tier2"))
            e        = float(row.get("avg_entry_price", row.get("entry_price", 0)) or 0)
            ex       = float(row.get("exit_price", 0) or 0)
            rsn_raw  = str(row.get("exit_reason","")).lower().strip()
            rc, rbg, rlbl = reason_styles.get(rsn_raw, ("#6b7a8d","rgba(107,122,141,0.1)", rsn_raw.replace("_"," ").upper()))
            pnl      = float(row.get("pnl_usdt", 0) or 0)
            pct      = float(row.get("pnl_pct",  0) or 0)
            pc2      = "pp" if pnl >= 0 else "pn"
            sg       = "+" if pnl >= 0 else ""

            # Rating
            rating_map = {"tier1": "A", "tier2": "B", "tier3": "C"}
            rating     = rating_map.get(tier, "B")
            rating_col = {"A": "var(--green)", "B": "var(--blue)", "C": "var(--gold)"}.get(rating, "var(--mid)")

            # Tier label
            tier_label = tier.replace("tier", "T")
            tier_col   = {"tier1": "var(--green)", "tier2": "var(--blue)", "tier3": "var(--purple)"}.get(tier, "var(--mid)")

            # Strategy + indicators from strat_map
            sr_info    = strat_map.get(full_sym, {})
            if hasattr(sr_info, "get"):
                strat_name = str(sr_info.get("strategy", "—")).split(" + ")[0][:18]
                indicators = str(sr_info.get("indicator_combo", "—"))[:30]
            else:
                strat_name = "—"
                indicators = "—"

            reason_badge = (f\'<span style="color:{rc};background:{rbg};padding:0.12rem 0.4rem;\' +
                           f\'border-radius:2px;font-size:0.5rem;font-weight:700;\' +
                           f\'letter-spacing:0.06em;border:1px solid {rc};white-space:nowrap">{rlbl}</span>\')

            rows += (
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
            )

        st.markdown(f"""
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
        """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty">NO CLOSED TRADES YET</div>', unsafe_allow_html=True)

# ── Strategy Breakdown — full width below ────────────────────────────────
with st.container(border=True):
    st.markdown('<div class="sec-title">Strategy Breakdown</div>', unsafe_allow_html=True)
    if not strategies.empty:
        if pnl_ok and total_tr > 0:
            try:
                mg  = closed_tr.merge(strategies[["symbol","strategy"]], on="symbol", how="left")
                grp = mg.groupby("strategy").agg(
                    coins=("symbol","nunique"), trades=("pnl_usdt","count"),
                    pnl=("pnl_usdt","sum"), wins=("pnl_usdt",lambda x:(x>0).sum())
                ).reset_index()
                grp["wr"]  = (grp["wins"]/grp["trades"]*100).round(1)
                grp["epf"] = grp["strategy"].map(strategies.groupby("strategy")["profit_factor"].mean()).fillna(0)
                grp["lpf"] = (grp["pnl"]/(grp["trades"].replace(0,np.nan))).fillna(0)
                grp = grp.sort_values("pnl", ascending=False)
                use_grp = True
            except: use_grp = False
        else: use_grp = False

        if not use_grp:
            grp = strategies.groupby("strategy").agg(
                coins=("symbol","count"), avg_wr=("win_rate","mean"), avg_pf=("profit_factor","mean")
            ).reset_index().sort_values("avg_pf", ascending=False)

        h = \'<div class="trow sb th"><span>Strategy</span><span>Coins</span><span>Trades</span><span>Win%</span><span>P&L</span><span>Exp PF</span><span>Live PF</span></div>\'
        rows = ""
        for i, (_, row) in enumerate(grp.iterrows()):
            dc2   = COLORS[i % len(COLORS)]
            strat = str(row.get("strategy",""))
            coins = int(row.get("coins",0))
            if use_grp:
                trd=int(row.get("trades",0)); wr2=float(row.get("wr",0))
                pnl=float(row.get("pnl",0)); epf=float(row.get("epf",0)); lpf=float(row.get("lpf",0))
                wstr=f"{wr2:.1f}%" if trd>0 else "--"
                pstr=f\'{"+" if pnl>=0 else ""}${pnl:,.2f}\'
                lstr=f"{lpf:.2f}" if trd>0 else "--"
                wc2="pp" if wr2>=50 else "pn"; pc3="pp" if pnl>=0 else "pn"; lc="pp" if lpf>=1 else "pn"
            else:
                trd=0; wstr="--"; pstr="--"; lstr="--"
                epf=float(row.get("avg_pf",0)); wc2=""; pc3=""; lc=""
            rows += (f\'<div class="trow sb">\' +
                    f\'<span style="display:flex;align-items:center"><span class="sdot" style="background:{dc2}"></span>\' +
                    f\'<span style="color:var(--text);font-size:0.65rem">{strat}</span></span>\' +
                    f\'<span style="color:var(--mid)">{coins}</span>\' +
                    f\'<span style="color:var(--mid)">{trd}</span>\' +
                    f\'<span class="{wc2}">{wstr}</span>\' +
                    f\'<span class="{pc3}">{pstr}</span>\' +
                    f\'<span style="color:var(--mid)">{epf:.1f}</span>\' +
                    f\'<span class="{lc}">{lstr}</span></div>\')
        st.markdown(f\'<div class="scr">{h}{rows}</div>\', unsafe_allow_html=True)
    else:
        st.markdown(\'<div class="empty">NO STRATEGY DATA</div>\', unsafe_allow_html=True)

'''

txt = txt[:start] + new_section + txt[end:]
f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done — Recent Closed Trades redesigned')
