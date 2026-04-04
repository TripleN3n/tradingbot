#!/usr/bin/env python3
"""
APEX Equity Curve - Clean Rebuild
Removes everything and rebuilds with a clean, simple, working chart
"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# Find section boundaries
start = txt.find('# =============================================================================\n# EQUITY CURVE — Ultra Pro')
end   = txt.find('# =============================================================================\n# OPEN POSITIONS')

if start == -1:
    start = txt.find('# =============================================================================\n# CHARTS')

if start == -1 or end == -1:
    print(f'ERROR: start={start}, end={end}')
    exit(1)

new_section = '''# =============================================================================
# EQUITY CURVE
# =============================================================================

with st.container(border=True):
    r1, r2 = st.columns([1.5, 4.5])
    with r1:
        st.markdown('<div class="sec-title" style="padding-bottom:0;margin-bottom:0">Equity Curve</div>', unsafe_allow_html=True)
    with r2:
        ec_filter = st.radio("", ["This Week","Last Week","This Month","All"],
            horizontal=True, key="ec_filter", label_visibility="collapsed", index=0)

    # Build data
    now_f = pd.Timestamp.now(tz="UTC")
    eq_times = []
    eq_vals  = []

    if pnl_ok and total_tr > 0 and "exit_time" in closed_tr.columns:
        df_eq = closed_tr[["exit_time","pnl_usdt"]].dropna().copy()
        df_eq["exit_time"] = pd.to_datetime(df_eq["exit_time"], utc=True, errors="coerce")
        df_eq = df_eq.dropna().sort_values("exit_time")
        if ec_filter == "This Week":
            ws = (now_f - pd.Timedelta(days=now_f.weekday())).replace(hour=0,minute=0,second=0)
            df_eq = df_eq[df_eq["exit_time"] >= ws]
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
        eq_vals  = [INITIAL_CAPITAL]

    # Live point
    live_val = (eq_vals[-1] if eq_vals else INITIAL_CAPITAL) + unreal_pnl
    eq_times.append(pd.Timestamp(now_utc))
    eq_vals.append(live_val)

    # Stats row
    peak_val   = max(eq_vals) if eq_vals else INITIAL_CAPITAL
    total_ret  = (live_val - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    max_dd     = 0.0
    running_pk = INITIAL_CAPITAL
    for v in eq_vals:
        if v > running_pk: running_pk = v
        dd = (running_pk - v) / running_pk * 100
        if dd > max_dd: max_dd = dd

    rc = "#00e5a0" if total_ret >= 0 else "#ff3d6b"
    rs = "+" if total_ret >= 0 else ""
    uc = "#00e5a0" if unreal_pnl >= 0 else "#ff3d6b"
    us = "+" if unreal_pnl >= 0 else ""

    st.markdown(f"""
    <div style="display:flex;gap:2rem;padding:0.5rem 0.2rem 0.8rem 0.2rem;
        border-bottom:1px solid var(--border);margin-bottom:0.5rem;">
      <div>
        <div style="font-family:var(--mono);font-size:0.46rem;color:var(--dim);letter-spacing:0.15em;text-transform:uppercase">Total Return</div>
        <div style="font-family:var(--mono);font-size:1rem;font-weight:700;color:{rc}">{rs}{total_ret:.2f}%</div>
      </div>
      <div>
        <div style="font-family:var(--mono);font-size:0.46rem;color:var(--dim);letter-spacing:0.15em;text-transform:uppercase">Portfolio</div>
        <div style="font-family:var(--mono);font-size:1rem;font-weight:700;color:var(--text)">${live_val:,.2f}</div>
      </div>
      <div>
        <div style="font-family:var(--mono);font-size:0.46rem;color:var(--dim);letter-spacing:0.15em;text-transform:uppercase">Peak</div>
        <div style="font-family:var(--mono);font-size:1rem;font-weight:700;color:var(--green)">${peak_val:,.2f}</div>
      </div>
      <div>
        <div style="font-family:var(--mono);font-size:0.46rem;color:var(--dim);letter-spacing:0.15em;text-transform:uppercase">Max Drawdown</div>
        <div style="font-family:var(--mono);font-size:1rem;font-weight:700;color:{"var(--gold)" if max_dd < 10 else "var(--red)"}">-{max_dd:.2f}%</div>
      </div>
      <div>
        <div style="font-family:var(--mono);font-size:0.46rem;color:var(--dim);letter-spacing:0.15em;text-transform:uppercase">Unrealized</div>
        <div style="font-family:var(--mono);font-size:1rem;font-weight:700;color:{uc}">{us}${unreal_pnl:,.2f}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Chart
    fig = go.Figure()

    # Realised line
    fig.add_trace(go.Scatter(
        x=eq_times[:-1], y=eq_vals[:-1],
        mode="lines",
        line=dict(color="#00e5a0", width=2),
        fill="tozeroy",
        fillcolor="rgba(0,229,160,0.07)",
        name="Realised",
        hovertemplate="%{x|%d %b %H:%M} · $%{y:,.2f}<extra></extra>",
    ))

    # Live dotted segment
    lc = "#00e5a0" if unreal_pnl >= 0 else "#ff3d6b"
    lf = "rgba(0,229,160,0.03)" if unreal_pnl >= 0 else "rgba(255,61,107,0.03)"
    fig.add_trace(go.Scatter(
        x=[eq_times[-2], eq_times[-1]],
        y=[eq_vals[-2],  eq_vals[-1]],
        mode="lines",
        line=dict(color=lc, width=2, dash="dot"),
        fill="tozeroy",
        fillcolor=lf,
        name="Live",
        hovertemplate="Live · $%{y:,.2f}<extra></extra>",
    ))

    # Orange dot
    fig.add_trace(go.Scatter(
        x=[eq_times[-1]], y=[eq_vals[-1]],
        mode="markers",
        marker=dict(color="#f5a623", size=9, line=dict(color="#080b0f", width=2)),
        name="Now",
        hovertemplate=f"Now · ${live_val:,.2f}<extra></extra>",
    ))

    # Baseline
    fig.add_hline(y=INITIAL_CAPITAL,
        line_dash="dash", line_color="rgba(255,255,255,0.08)", line_width=1)

    fig.update_layout(
        height=260,
        margin=dict(l=0, r=0, t=4, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#0d1117", bordercolor="#1e2a38",
            font=dict(family="JetBrains Mono", size=11, color="#d8e0e8"),
        ),
        xaxis=dict(
            showgrid=False, zeroline=False,
            tickfont=dict(family="JetBrains Mono", size=9, color="#6b7a8d"),
            tickformat="%m-%d %H:%M",
            showspikes=True, spikecolor="rgba(255,255,255,0.1)",
            spikethickness=1, spikedash="dot",
        ),
        yaxis=dict(
            showgrid=True, gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(family="JetBrains Mono", size=9, color="#6b7a8d"),
            tickformat="$,.0f", zeroline=False,
        ),
        showlegend=False,
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

'''

txt = txt[:start] + new_section + txt[end:]
f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done — clean equity curve installed')
