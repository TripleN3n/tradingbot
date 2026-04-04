#!/usr/bin/env python3
"""
APEX Equity Curve - Exact match to screenshot
- Filter buttons: This Week | Last Week | This Month | All
- Date pickers: dd/mm/yyyy to dd/mm/yyyy
- Full width smooth green line with gradient fill
- No stats bar
- Clean dark theme
"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# Find section boundaries
start = None
for marker in [
    '# =============================================================================\n# EQUITY CURVE',
    '# =============================================================================\n# CHARTS',
]:
    idx = txt.find(marker)
    if idx != -1:
        start = idx
        break

end = txt.find('# =============================================================================\n# OPEN POSITIONS')

if start is None or end == -1:
    print(f'ERROR: start={start}, end={end}')
    exit(1)

new_section = '''# =============================================================================
# EQUITY CURVE
# =============================================================================

with st.container(border=True):
    # Header row — title | filters | date pickers
    h1, h2, h3 = st.columns([1.2, 3.2, 2.6])
    with h1:
        st.markdown('<div class="sec-title" style="padding-bottom:0;margin-bottom:0">Equity Curve</div>', unsafe_allow_html=True)
    with h2:
        ec_filter = st.radio("", ["This Week", "Last Week", "This Month", "All"],
            horizontal=True, key="ec_filter", label_visibility="collapsed", index=0)
    with h3:
        dp1, dp_sep, dp2 = st.columns([5, 0.5, 5])
        with dp1:
            date_from = st.date_input("", value=None, key="ec_from",
                label_visibility="collapsed", format="DD/MM/YYYY")
        with dp_sep:
            st.markdown('<div style="text-align:center;padding-top:0.5rem;color:var(--dim);font-family:var(--mono);font-size:0.7rem">to</div>', unsafe_allow_html=True)
        with dp2:
            date_to = st.date_input("", value=None, key="ec_to",
                label_visibility="collapsed", format="DD/MM/YYYY")

    # Build equity data points
    now_f    = pd.Timestamp.now(tz="UTC")
    eq_times = []
    eq_vals  = []

    if pnl_ok and total_tr > 0 and "exit_time" in closed_tr.columns:
        df_eq = closed_tr[["exit_time", "pnl_usdt"]].dropna().copy()
        df_eq["exit_time"] = pd.to_datetime(df_eq["exit_time"], utc=True, errors="coerce")
        df_eq = df_eq.dropna().sort_values("exit_time")

        # Date picker overrides radio
        if date_from and date_to:
            df_eq = df_eq[(df_eq["exit_time"].dt.date >= date_from) & (df_eq["exit_time"].dt.date <= date_to)]
        elif date_from:
            df_eq = df_eq[df_eq["exit_time"].dt.date >= date_from]
        elif date_to:
            df_eq = df_eq[df_eq["exit_time"].dt.date <= date_to]
        elif ec_filter == "This Week":
            ws = (now_f - pd.Timedelta(days=now_f.weekday())).replace(hour=0, minute=0, second=0)
            df_eq = df_eq[df_eq["exit_time"] >= ws]
        elif ec_filter == "Last Week":
            ls = (now_f - pd.Timedelta(days=now_f.weekday()+7)).replace(hour=0, minute=0, second=0)
            le = (now_f - pd.Timedelta(days=now_f.weekday())).replace(hour=0, minute=0, second=0)
            df_eq = df_eq[(df_eq["exit_time"] >= ls) & (df_eq["exit_time"] < le)]
        elif ec_filter == "This Month":
            df_eq = df_eq[df_eq["exit_time"] >= now_f.replace(day=1, hour=0, minute=0, second=0)]

        eq_times = [now_utc - pd.Timedelta(hours=1)] + df_eq["exit_time"].tolist()
        eq_vals  = [INITIAL_CAPITAL]
        for p in df_eq["pnl_usdt"]:
            eq_vals.append(eq_vals[-1] + p)
    else:
        eq_times = [now_utc - pd.Timedelta(hours=1)]
        eq_vals  = [INITIAL_CAPITAL]

    # Append live unrealized point
    live_val = (eq_vals[-1] if eq_vals else INITIAL_CAPITAL) + unreal_pnl
    eq_times.append(pd.Timestamp(now_utc))
    eq_vals.append(live_val)

    # Build chart
    fig = go.Figure()

    # Realised solid line with fill
    fig.add_trace(go.Scatter(
        x=eq_times[:-1],
        y=eq_vals[:-1],
        mode="lines",
        line=dict(color="#00e5a0", width=2),
        fill="tozeroy",
        fillcolor="rgba(0,229,160,0.08)",
        name="Realised",
        hovertemplate="%{x|%d %b %H:%M}<br><b>$%{y:,.2f}</b><extra></extra>",
    ))

    # Live dotted segment (green if profit, red if loss)
    live_line_color = "#00e5a0" if unreal_pnl >= 0 else "#ff3d6b"
    live_fill_color = "rgba(0,229,160,0.03)" if unreal_pnl >= 0 else "rgba(255,61,107,0.03)"
    fig.add_trace(go.Scatter(
        x=[eq_times[-2], eq_times[-1]],
        y=[eq_vals[-2],  eq_vals[-1]],
        mode="lines",
        line=dict(color=live_line_color, width=2, dash="dot"),
        fill="tozeroy",
        fillcolor=live_fill_color,
        name="Live",
        hovertemplate="Live: <b>$%{y:,.2f}</b><extra></extra>",
    ))

    # Orange live dot
    fig.add_trace(go.Scatter(
        x=[eq_times[-1]],
        y=[eq_vals[-1]],
        mode="markers",
        marker=dict(color="#f5a623", size=9, line=dict(color="#080b0f", width=2)),
        name="Now",
        hovertemplate=f"Now: <b>${live_val:,.2f}</b><extra></extra>",
    ))

    # Initial capital dashed baseline
    fig.add_hline(
        y=INITIAL_CAPITAL,
        line_dash="dash",
        line_color="rgba(255,255,255,0.08)",
        line_width=1,
    )

    fig.update_layout(
        height=270,
        margin=dict(l=0, r=0, t=8, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#0d1117",
            bordercolor="#1e2a38",
            font=dict(family="JetBrains Mono", size=11, color="#d8e0e8"),
        ),
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            tickfont=dict(family="JetBrains Mono", size=9, color="#6b7a8d"),
            tickformat="%m-%d %H:%M",
            showspikes=True,
            spikecolor="rgba(255,255,255,0.08)",
            spikethickness=1,
            spikedash="dot",
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(family="JetBrains Mono", size=9, color="#6b7a8d"),
            tickformat="$,.0f",
            zeroline=False,
            showspikes=False,
        ),
        showlegend=False,
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

'''

txt = txt[:start] + new_section + txt[end:]
f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done — equity curve matches screenshot')
