#!/usr/bin/env python3
"""
APEX Ultra-Pro Equity Curve Replacement
Replaces the entire CHARTS section with a stunning full-width equity curve
"""

f = open('/home/opc/tradingbot/dashboard.py', 'r')
txt = f.read()
f.close()

# ── Find the CHARTS section start and OPEN POSITIONS start ────────────────
charts_start = txt.find('# =============================================================================\n# CHARTS')
open_pos_start = txt.find('# =============================================================================\n# OPEN POSITIONS')

if charts_start == -1 or open_pos_start == -1:
    print(f'ERROR: charts_start={charts_start}, open_pos_start={open_pos_start}')
    exit(1)

old_section = txt[charts_start:open_pos_start]

new_section = '''# =============================================================================
# EQUITY CURVE — Ultra Pro
# =============================================================================

with st.container(border=True):
    # ── Header row ──────────────────────────────────────────────────────
    h1, h2, h3 = st.columns([1.2, 3.0, 2.8])
    with h1:
        st.markdown('<div class="sec-title" style="padding-bottom:0;margin-bottom:0">Equity Curve</div>', unsafe_allow_html=True)
    with h2:
        ec_filter = st.radio("", ["This Week", "Last Week", "This Month", "All"],
            horizontal=True, key="ec_filter", label_visibility="collapsed", index=0)
    with h3:
        dr1, dr2 = st.columns(2)
        with dr1:
            date_from = st.date_input("From", value=None, key="ec_from", label_visibility="collapsed")
        with dr2:
            date_to = st.date_input("To", value=None, key="ec_to", label_visibility="collapsed")

    # ── Build equity data ────────────────────────────────────────────────
    now_f  = pd.Timestamp.now(tz="UTC")
    eq_times = []
    eq_vals  = []
    eq_types = []   # "realised" or "unrealized"

    if pnl_ok and total_tr > 0 and "exit_time" in closed_tr.columns:
        df_eq = closed_tr[["exit_time", "pnl_usdt"]].dropna().copy()
        df_eq["exit_time"] = pd.to_datetime(df_eq["exit_time"], utc=True, errors="coerce")
        df_eq = df_eq.dropna().sort_values("exit_time")

        # Apply date filter
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
        eq_types = ["realised"] * len(eq_times)
    else:
        eq_times = [now_utc - pd.Timedelta(hours=1)]
        eq_vals  = [INITIAL_CAPITAL]
        eq_types = ["realised"]

    # Live unrealized dot
    live_val = (eq_vals[-1] if eq_vals else INITIAL_CAPITAL) + unreal_pnl
    eq_times.append(pd.Timestamp(now_utc))
    eq_vals.append(live_val)
    eq_types.append("live")

    # ── Stats bar ────────────────────────────────────────────────────────
    peak_val    = max(eq_vals) if eq_vals else INITIAL_CAPITAL
    trough_val  = min(eq_vals) if eq_vals else INITIAL_CAPITAL
    total_ret   = (live_val - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    max_dd_val  = (peak_val - trough_val) / peak_val * 100 if peak_val > 0 else 0
    ret_color   = "#00e5a0" if total_ret >= 0 else "#ff3d6b"
    ret_sign    = "+" if total_ret >= 0 else ""
    dd_color    = "#f5a623" if max_dd_val < 10 else "#ff3d6b"
    live_color  = "#00e5a0" if unreal_pnl >= 0 else "#ff3d6b"
    live_sign   = "+" if unreal_pnl >= 0 else ""

    st.markdown(f"""
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.5rem;
        margin:0.4rem 0 0.8rem 0;padding:0.5rem 0.2rem;
        border-top:1px solid var(--border);border-bottom:1px solid var(--border);">
      <div style="text-align:center">
        <div style="font-family:var(--mono);font-size:0.46rem;color:var(--dim);
            letter-spacing:0.15em;text-transform:uppercase;margin-bottom:0.2rem">Total Return</div>
        <div style="font-family:var(--mono);font-size:0.9rem;font-weight:700;color:{ret_color}">
            {ret_sign}{total_ret:.2f}%</div>
      </div>
      <div style="text-align:center">
        <div style="font-family:var(--mono);font-size:0.46rem;color:var(--dim);
            letter-spacing:0.15em;text-transform:uppercase;margin-bottom:0.2rem">Portfolio Value</div>
        <div style="font-family:var(--mono);font-size:0.9rem;font-weight:700;color:var(--text)">
            ${live_val:,.2f}</div>
      </div>
      <div style="text-align:center">
        <div style="font-family:var(--mono);font-size:0.46rem;color:var(--dim);
            letter-spacing:0.15em;text-transform:uppercase;margin-bottom:0.2rem">Peak</div>
        <div style="font-family:var(--mono);font-size:0.9rem;font-weight:700;color:var(--green)">
            ${peak_val:,.2f}</div>
      </div>
      <div style="text-align:center">
        <div style="font-family:var(--mono);font-size:0.46rem;color:var(--dim);
            letter-spacing:0.15em;text-transform:uppercase;margin-bottom:0.2rem">Max Drawdown</div>
        <div style="font-family:var(--mono);font-size:0.9rem;font-weight:700;color:{dd_color}">
            -{max_dd_val:.2f}%</div>
      </div>
      <div style="text-align:center">
        <div style="font-family:var(--mono);font-size:0.46rem;color:var(--dim);
            letter-spacing:0.15em;text-transform:uppercase;margin-bottom:0.2rem">Unrealized</div>
        <div style="font-family:var(--mono);font-size:0.9rem;font-weight:700;color:{live_color}">
            {live_sign}${unreal_pnl:,.2f}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Build chart ──────────────────────────────────────────────────────
    fig = go.Figure()

    # Gradient fill under realised line
    fig.add_trace(go.Scatter(
        x=eq_times[:-1], y=eq_vals[:-1],
        mode="lines",
        line=dict(color="#00e5a0", width=2, shape="spline", smoothing=0.3),
        fill="tozeroy",
        fillgradient=dict(
            type="vertical",
            colorscale=[[0, "rgba(0,229,160,0.0)"], [1, "rgba(0,229,160,0.12)"]],
        ) if hasattr(go.Scatter, 'fillgradient') else None,
        fillcolor="rgba(0,229,160,0.08)",
        name="Realised",
        hovertemplate="<b>%{x|%d %b %H:%M}</b><br>Portfolio: <b>$%{y:,.2f}</b><br><extra></extra>",
    ))

    # Live unrealized segment — dotted
    live_seg_color = "#00e5a0" if unreal_pnl >= 0 else "#ff3d6b"
    live_fill_col  = "rgba(0,229,160,0.04)" if unreal_pnl >= 0 else "rgba(255,61,107,0.04)"
    fig.add_trace(go.Scatter(
        x=[eq_times[-2], eq_times[-1]],
        y=[eq_vals[-2],  eq_vals[-1]],
        mode="lines",
        line=dict(color=live_seg_color, width=2, dash="dot"),
        fill="tozeroy",
        fillcolor=live_fill_col,
        name="Live",
        hovertemplate="<b>Live Now</b><br>$%{y:,.2f}<extra></extra>",
    ))

    # Initial capital baseline
    fig.add_hline(
        y=INITIAL_CAPITAL,
        line_dash="dot",
        line_color="rgba(255,255,255,0.1)",
        line_width=1,
        annotation_text=f"  Start ${INITIAL_CAPITAL:,.0f}",
        annotation_font_size=9,
        annotation_font_color="rgba(255,255,255,0.2)",
        annotation_position="bottom left",
    )

    # Peak line
    if peak_val > INITIAL_CAPITAL:
        fig.add_hline(
            y=peak_val,
            line_dash="dot",
            line_color="rgba(0,229,160,0.2)",
            line_width=1,
            annotation_text=f"  Peak ${peak_val:,.0f}",
            annotation_font_size=9,
            annotation_font_color="rgba(0,229,160,0.4)",
            annotation_position="top left",
        )

    # Live orange dot
    fig.add_trace(go.Scatter(
        x=[eq_times[-1]], y=[eq_vals[-1]],
        mode="markers",
        marker=dict(
            color="#f5a623", size=10, symbol="circle",
            line=dict(color="#080b0f", width=2),
        ),
        name="Now",
        hovertemplate=f"<b>Now: ${live_val:,.2f}</b><extra></extra>",
    ))

    fig.update_layout(
        height=300,
        margin=dict(l=0, r=0, t=5, b=0),
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
            spikecolor="rgba(255,255,255,0.1)",
            spikethickness=1,
            spikedash="dot",
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(family="JetBrains Mono", size=9, color="#6b7a8d"),
            tickformat="$,.0f",
            zeroline=False,
            showspikes=True,
            spikecolor="rgba(255,255,255,0.1)",
            spikethickness=1,
            spikedash="dot",
        ),
        showlegend=False,
    )

    st.plotly_chart(fig, use_container_width=True, config={
        "displayModeBar": True,
        "displaylogo": False,
        "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
        "toImageButtonOptions": {"format": "png", "filename": "apex_equity_curve"},
    })

'''

txt = txt[:charts_start] + new_section + txt[open_pos_start:]

f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done — Ultra-pro equity curve installed')
