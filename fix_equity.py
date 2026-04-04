#!/usr/bin/env python3
"""
APEX Equity Curve Redesign
- Removes P&L Distribution section
- Makes Equity Curve full width
- Adds date range pickers
- Live tracking with unrealized PnL movement
"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# ── Find and replace the entire CHARTS section ────────────────────────────
old = '''# =============================================================================
# CHARTS — Change 2: use st.container(border=True)
# =============================================================================

cl, cr = st.columns([3,2])
with cl:
    with st.container(border=True):
        hc1, hc2 = st.columns([1.2, 2.8])
        with hc1:
            st.markdown('<div class="sec-title" style="padding-bottom:0;margin-bottom:0">Equity Curve</div>', unsafe_allow_html=True)
        with hc2:
            ec_filter = st.radio("", ["This Week","Last Week","This Month","All"],
                horizontal=True, key="ec_filter", label_visibility="collapsed", index=0)'''

new = '''# =============================================================================
# CHARTS — Full width equity curve with live tracking
# =============================================================================

with st.container(border=True):
        hc1, hc2, hc3 = st.columns([1.5, 2.5, 2.0])
        with hc1:
            st.markdown('<div class="sec-title" style="padding-bottom:0;margin-bottom:0">Equity Curve</div>', unsafe_allow_html=True)
        with hc2:
            ec_filter = st.radio("", ["This Week","Last Week","This Month","All"],
                horizontal=True, key="ec_filter", label_visibility="collapsed", index=0)
        with hc3:
            dc1, dc2 = st.columns(2)
            with dc1:
                date_from = st.date_input("", value=None, key="ec_from", label_visibility="collapsed")
            with dc2:
                date_to = st.date_input("", value=None, key="ec_to", label_visibility="collapsed")'''

if old in txt:
    txt = txt.replace(old, new, 1)
    print('STEP1 ✓ Chart header replaced')
else:
    print('STEP1 SKIP - header pattern not found')

# ── Remove the closing of cl column and cr column with P&L distribution ───
old2 = '''        fig.update_layout(height=220, margin=dict(l=0,r=0,t=5,b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False, zeroline=False,
                tickfont=dict(family="JetBrains Mono", size=9, color="#6b7a8d"),
                tickformat="%m-%d %H:%M"),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(family="JetBrains Mono", size=9, color="#6b7a8d"),
                tickformat="$,.0f", zeroline=False),
            showlegend=False)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

with cr:
    with st.container(border=True):
        st.markdown('<div class="sec-title">P&L Distribution</div>', unsafe_allow_html=True)
        if pnl_ok and total_tr>0:
            pnls=closed_tr["pnl_usdt"].tolist()[::-1]
            fig2=go.Figure()
            fig2.add_trace(go.Bar(x=list(range(len(pnls))),y=pnls,
                marker_color=["#00e5a0" if p>0 else "#ff3d6b" for p in pnls],
                hovertemplate="Trade #%{x}<br>$%{y:,.2f}<extra></extra>"))
            fig2.update_layout(height=200,margin=dict(l=0,r=0,t=0,b=0),
                paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=False,showticklabels=False,zeroline=False),
                yaxis=dict(showgrid=True,gridcolor="rgba(255,255,255,0.04)",
                    tickfont=dict(family="JetBrains Mono",size=9,color="#4a5568"),
                    tickformat="$,.0f",zeroline=False),showlegend=False)
            st.plotly_chart(fig2,use_container_width=True,config={"displayModeBar":False})
        else:
            st.markdown('<div class="empty" style="height:180px;display:flex;align-items:center;justify-content:center">NO TRADES YET</div>',unsafe_allow_html=True)'''

new2 = '''        fig.update_layout(height=280, margin=dict(l=0,r=0,t=5,b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False, zeroline=False,
                tickfont=dict(family="JetBrains Mono", size=9, color="#6b7a8d"),
                tickformat="%m-%d %H:%M"),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(family="JetBrains Mono", size=9, color="#6b7a8d"),
                tickformat="$,.0f", zeroline=False),
            showlegend=False)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})'''

if old2 in txt:
    txt = txt.replace(old2, new2, 1)
    print('STEP2 ✓ P&L Distribution removed, equity curve full width')
else:
    print('STEP2 SKIP - chart/distribution pattern not found')

# ── Update equity curve data to use date range pickers ────────────────────
old3 = '''        now_f = pd.Timestamp.now(tz="UTC")

        if pnl_ok and total_tr > 0 and "exit_time" in closed_tr.columns:
            df_eq = closed_tr[["exit_time","pnl_usdt"]].dropna().copy()
            df_eq["exit_time"] = pd.to_datetime(df_eq["exit_time"], utc=True, errors="coerce")
            df_eq = df_eq.dropna().sort_values("exit_time")
            if ec_filter == "This Week":
                wstart = (now_f - pd.Timedelta(days=now_f.weekday())).replace(hour=0,minute=0,second=0)
                df_eq  = df_eq[df_eq["exit_time"] >= wstart]
            elif ec_filter == "Last Week":
                ls = (now_f - pd.Timedelta(days=now_f.weekday()+7)).replace(hour=0,minute=0,second=0)
                le = (now_f - pd.Timedelta(days=now_f.weekday())).replace(hour=0,minute=0,second=0)
                df_eq = df_eq[(df_eq["exit_time"]>=ls)&(df_eq["exit_time"]<le)]
            elif ec_filter == "This Month":
                df_eq = df_eq[df_eq["exit_time"] >= now_f.replace(day=1,hour=0,minute=0,second=0)]
            eq_times = [now_utc - pd.Timedelta(hours=1)] + df_eq["exit_time"].tolist()
            eq_vals  = [INITIAL_CAPITAL]
            for p in df_eq["pnl_usdt"]:
                eq_vals.append(eq_vals[-1] + p)
        else:
            eq_times = [now_utc - pd.Timedelta(hours=1)]
            eq_vals  = [INITIAL_CAPITAL]'''

new3 = '''        now_f = pd.Timestamp.now(tz="UTC")

        if pnl_ok and total_tr > 0 and "exit_time" in closed_tr.columns:
            df_eq = closed_tr[["exit_time","pnl_usdt"]].dropna().copy()
            df_eq["exit_time"] = pd.to_datetime(df_eq["exit_time"], utc=True, errors="coerce")
            df_eq = df_eq.dropna().sort_values("exit_time")
            # Date range picker overrides radio filter
            if date_from and date_to:
                df_eq = df_eq[(df_eq["exit_time"].dt.date >= date_from) & (df_eq["exit_time"].dt.date <= date_to)]
            elif date_from:
                df_eq = df_eq[df_eq["exit_time"].dt.date >= date_from]
            elif date_to:
                df_eq = df_eq[df_eq["exit_time"].dt.date <= date_to]
            elif ec_filter == "This Week":
                wstart = (now_f - pd.Timedelta(days=now_f.weekday())).replace(hour=0,minute=0,second=0)
                df_eq  = df_eq[df_eq["exit_time"] >= wstart]
            elif ec_filter == "Last Week":
                ls = (now_f - pd.Timedelta(days=now_f.weekday()+7)).replace(hour=0,minute=0,second=0)
                le = (now_f - pd.Timedelta(days=now_f.weekday())).replace(hour=0,minute=0,second=0)
                df_eq = df_eq[(df_eq["exit_time"]>=ls)&(df_eq["exit_time"]<le)]
            elif ec_filter == "This Month":
                df_eq = df_eq[df_eq["exit_time"] >= now_f.replace(day=1,hour=0,minute=0,second=0)]
            eq_times = [now_utc - pd.Timedelta(hours=1)] + df_eq["exit_time"].tolist()
            eq_vals  = [INITIAL_CAPITAL]
            for p in df_eq["pnl_usdt"]:
                eq_vals.append(eq_vals[-1] + p)
        else:
            eq_times = [now_utc - pd.Timedelta(hours=1)]
            eq_vals  = [INITIAL_CAPITAL]'''

if old3 in txt:
    txt = txt.replace(old3, new3, 1)
    print('STEP3 ✓ Date range picker logic added')
else:
    print('STEP3 SKIP - equity data pattern not found')

f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done — all patches applied')
