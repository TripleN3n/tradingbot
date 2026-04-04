#!/usr/bin/env python3
"""
APEX Equity Curve - Complete rebuild matching reference screenshot exactly
- Tab-style filter buttons with blue highlight on selected
- Dark-themed date pickers
- Vertical separator between filters and pickers
- No container border
- Tight y-axis range
- Subtle green fill
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

# CSS for tab-style filter buttons and dark date pickers
st.markdown("""
<style>
/* Tab-style radio buttons for equity curve */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stRadio"]) div[data-testid="stRadio"] > div {
    gap: 0 !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stRadio"]) div[data-testid="stRadio"] label {
    background: transparent !important;
    border: 1px solid #1e2a38 !important;
    border-radius: 4px !important;
    padding: 0.2rem 0.8rem !important;
    margin: 0 0.2rem !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.7rem !important;
    letter-spacing: 0.05em !important;
    color: #6b7a8d !important;
    cursor: pointer !important;
    transition: all 0.15s !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stRadio"]) div[data-testid="stRadio"] label:hover {
    color: #d8e0e8 !important;
    border-color: #58a6ff !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stRadio"]) div[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked),
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stRadio"]) div[data-testid="stRadio"] div:has(input:checked) + div {
    color: white !important;
    border-color: #58a6ff !important;
    background: rgba(88,166,255,0.12) !important;
}
/* Hide radio circles */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stRadio"]) div[data-testid="stRadio"] div[data-testid="stMarkdownContainer"] { display: none !important; }
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stRadio"]) div[data-testid="stRadio"] span[data-baseweb="radio"] { display: none !important; }
/* Dark date pickers */
div[data-testid="stDateInput"] input {
    background: #0d1117 !important;
    border: 1px solid #1e2a38 !important;
    color: #9aa5b4 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.68rem !important;
    border-radius: 4px !important;
}
div[data-testid="stDateInput"] input:focus {
    border-color: #58a6ff !important;
    box-shadow: none !important;
}
div[data-testid="stDateInput"] svg { color: #6b7a8d !important; }
</style>
""", unsafe_allow_html=True)

with st.container(border=False):
    # ── Header row ──────────────────────────────────────────────────────
    h1, h2, h_sep, h3 = st.columns([1.2, 3.0, 0.08, 2.8])
    with h1:
        st.markdown(
            '<div style="font-family:\'JetBrains Mono\',monospace;font-size:0.62rem;'
            'letter-spacing:0.18em;text-transform:uppercase;color:var(--dim);'
            'padding-top:0.35rem;display:flex;align-items:center;gap:0.4rem;">'
            '<span style="color:var(--green)">▸</span> EQUITY CURVE</div>',
            unsafe_allow_html=True
        )
    with h2:
        ec_filter = st.radio("", ["This Week","Last Week","This Month","All"],
            horizontal=True, key="ec_filter", label_visibility="collapsed", index=0)
    with h_sep:
        st.markdown(
            '<div style="height:100%;display:flex;align-items:center;justify-content:center;'
            'color:#1e2a38;font-size:1.2rem;padding-top:0.2rem">|</div>',
            unsafe_allow_html=True
        )
    with h3:
        dp1, dp_sep, dp2 = st.columns([10, 1, 10])
        with dp1:
            date_from = st.date_input("", value=None, key="ec_from",
                label_visibility="collapsed", format="DD/MM/YYYY")
        with dp_sep:
            st.markdown(
                '<div style="text-align:center;padding-top:0.45rem;'
                'font-family:var(--mono);font-size:0.6rem;color:var(--dim)">to</div>',
                unsafe_allow_html=True
            )
        with dp2:
            date_to = st.date_input("", value=None, key="ec_to",
                label_visibility="collapsed", format="DD/MM/YYYY")

    st.markdown('<div style="height:0.4rem"></div>', unsafe_allow_html=True)

    # ── Build equity data ────────────────────────────────────────────────
    now_f    = pd.Timestamp.now(tz="UTC")
    eq_times = []
    eq_vals  = []

    if pnl_ok and total_tr > 0 and "exit_time" in closed_tr.columns:
        df_eq = closed_tr[["exit_time","pnl_usdt"]].dropna().copy()
        df_eq["exit_time"] = pd.to_datetime(df_eq["exit_time"], utc=True, errors="coerce")
        df_eq = df_eq.dropna().sort_values("exit_time")

        if date_from and date_to:
            df_eq = df_eq[(df_eq["exit_time"].dt.date >= date_from) & (df_eq["exit_time"].dt.date <= date_to)]
        elif date_from:
            df_eq = df_eq[df_eq["exit_time"].dt.date >= date_from]
        elif date_to:
            df_eq = df_eq[df_eq["exit_time"].dt.date <= date_to]
        elif ec_filter == "This Week":
            ws = (now_f - pd.Timedelta(days=now_f.weekday())).replace(hour=0,minute=0,second=0)
            df_eq = df_eq[df_eq["exit_time"] >= ws]
        elif ec_filter == "Last Week":
            ls = (now_f - pd.Timedelta(days=now_f.weekday()+7)).replace(hour=0,minute=0,second=0)
            le = (now_f - pd.Timedelta(days=now_f.weekday())).replace(hour=0,minute=0,second=0)
            df_eq = df_eq[(df_eq["exit_time"] >= ls) & (df_eq["exit_time"] < le)]
        elif ec_filter == "This Month":
            df_eq = df_eq[df_eq["exit_time"] >= now_f.replace(day=1,hour=0,minute=0,second=0)]

        eq_times = [now_utc - pd.Timedelta(hours=1)] + df_eq["exit_time"].tolist()
        eq_vals  = [INITIAL_CAPITAL]
        for p in df_eq["pnl_usdt"]:
            eq_vals.append(eq_vals[-1] + p)
    else:
        eq_times = [now_utc - pd.Timedelta(hours=1)]
        eq_vals  = [INITIAL_CAPITAL]

    # Live unrealized point
    live_val = (eq_vals[-1] if eq_vals else INITIAL_CAPITAL) + unreal_pnl
    eq_times.append(pd.Timestamp(now_utc))
    eq_vals.append(live_val)

    # Tight y-axis range matching reference
    y_min     = min(eq_vals)
    y_max     = max(eq_vals)
    y_pad     = max((y_max - y_min) * 0.15, 50)
    y_range   = [y_min - y_pad, y_max + y_pad]

    # ── Build chart ──────────────────────────────────────────────────────
    fig = go.Figure()

    # Realised solid line
    fig.add_trace(go.Scatter(
        x=eq_times[:-1],
        y=eq_vals[:-1],
        mode="lines",
        line=dict(color="#00e5a0", width=2),
        fill="tozeroy",
        fillcolor="rgba(0,229,160,0.07)",
        name="Realised",
        hovertemplate="%{x|%d %b %H:%M}  <b>$%{y:,.2f}</b><extra></extra>",
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
        hovertemplate="Live  <b>$%{y:,.2f}</b><extra></extra>",
    ))

    # Orange live dot
    fig.add_trace(go.Scatter(
        x=[eq_times[-1]],
        y=[eq_vals[-1]],
        mode="markers",
        marker=dict(color="#f5a623", size=9, line=dict(color="#0d1117", width=2)),
        name="Now",
        hovertemplate=f"Now  <b>${live_val:,.2f}</b><extra></extra>",
    ))

    fig.update_layout(
        height=280,
        margin=dict(l=0, r=0, t=4, b=0),
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
            range=y_range,
        ),
        showlegend=False,
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

'''

txt = txt[:start] + new_section + txt[end:]
f = open('/home/opc/tradingbot/dashboard.py', 'w')
f.write(txt)
f.close()
print('Done — complete equity curve rebuilt')
