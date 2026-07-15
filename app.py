"""
app.py -- Streamlit UI (spec Section 4).

Pages: 1 Netback, 2 Sensitivities, 3 Hedging, 4 VaR & stress. The sidebar
holds the curve-date picker (restricted to the master date list) and every
Section 2 Step 5 cost parameter, flowing into every page via
st.session_state. model.py and risk.py stay pure (no Streamlit import);
this is the only file that imports streamlit.

Run:  streamlit run app.py
Data: set LNG_HISTORY_XLSX to the workbook path, or upload it in the
      sidebar when the env var / conventional paths aren't found.
"""

from __future__ import annotations

import copy
import os

import altair as alt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data
import decision
import model
import risk

st.set_page_config(page_title="LNG Forward Netback", layout="wide")

# ===========================================================================
# Waterfall / flow chart builders (pure Plotly; read model.waterfall_
# breakdown() output only -- no recalculation here). This is the netback
# app's own equivalent of LNG_Diversion_Waterfall_Flows.html's waterfall +
# Sankey view, built from the live model instead of that file's separate,
# hardcoded JS reimplementation (flagged as a duplicate-logic risk in
# LNG_Diversion_Logic_GPT.md Sec 19.2 / 20.1).
# ===========================================================================


def _plotly_waterfall(title: str, revenue: float, lines: list, margin: float):
    """lines is (name, cost) pairs where a positive cost is subtracted (bar
    goes down) and a negative cost is a credit added back (bar goes up,
    e.g. a sunk-cost add-back) -- text always shows the true signed delta
    (a credit prints as "+x", not the internal "-x" used to flip the bar
    direction), so it never contradicts which way the bar moved."""
    names = ["Revenue"] + [n for n, _ in lines] + ["Margin"]
    measures = ["absolute"] + ["relative"] * len(lines) + ["total"]
    values = [revenue] + [-v for _, v in lines] + [margin]
    line_text = [f"{v:,.2f}" if v >= 0 else f"+{-v:,.2f}" for _, v in lines]
    margin_color = "#3d8a5f" if margin >= 0 else "#b23a3a"
    fig = go.Figure(go.Waterfall(
        x=names, measure=measures, y=values,
        text=[f"{revenue:,.2f}"] + line_text + [f"{margin:,.2f}"],
        textposition="outside",
        increasing={"marker": {"color": "#2f6db3"}},
        decreasing={"marker": {"color": "#c66b4e"}},
        totals={"marker": {"color": margin_color}},
        connector={"line": {"color": "#c9d0d8", "dash": "dot"}},
    ))
    fig.update_layout(title=title, showlegend=False, height=380, yaxis_title="$/MMBtu",
                       margin=dict(t=40, b=10, l=10, r=10))
    return fig


def _flow_buckets(lines: list) -> list:
    """Groups waterfall_breakdown() line items into 5 display buckets for
    the Sankey view, mirroring LNG_Diversion_Waterfall_Flows.html's own
    grouping (Procurement | Shipping | Boil-off | Discharge/Port |
    Loading+Other+ETS). Returns only non-zero buckets."""
    d = dict(lines)
    buckets = [
        ("Procurement", d.get("Procurement", 0.0), "#c66b4e"),
        ("Shipping", d.get("Charter", 0.0) + d.get("Bunkers", 0.0) + d.get("Canal", 0.0), "#b3563a"),
        ("Boil-off", d.get("Boil-off", 0.0), "#d9a05b"),
        ("Discharge/Port", d.get("Discharge", 0.0) + d.get("Port", 0.0), "#8a6d5c"),
        ("Loading + Other + ETS", d.get("Loading", 0.0) + d.get("Other", 0.0) + d.get("ETS", 0.0), "#9aa4ae"),
    ]
    return [(n, v, c) for n, v, c in buckets if v > 1e-9]


def _plotly_flow_sankey(title: str, revenue: float, lines: list, margin: float):
    buckets = _flow_buckets(lines)
    labels = ["Revenue"] + [b[0] for b in buckets]
    colors = ["#2f6db3"] + [b[2] for b in buckets]
    values = [b[1] for b in buckets]
    if margin >= 0:
        labels.append("Margin")
        colors.append("#3d8a5f")
        values.append(margin)
    targets = list(range(1, len(labels)))
    fig = go.Figure(go.Sankey(
        node=dict(label=labels, color=colors, pad=18, thickness=16),
        link=dict(source=[0] * len(targets), target=targets, value=values,
                  color=[colors[t] for t in targets]),
    ))
    fig.update_layout(title=title, height=320, font_size=12, margin=dict(t=40, b=10, l=10, r=10))
    return fig


def _decision_waterfall_lines(bd: dict, sunk: bool) -> tuple[list, float]:
    """Adapts model.waterfall_breakdown()'s $/MMBtu lines/margin to a
    decision-state view. Procurement and loading are always shown as real
    cost bars -- gas was actually bought and loaded, and hiding that cost
    reads as a mistake, not a decision-state simplification. When those
    costs are sunk (an already-loaded current cargo), one extra "Sunk cost
    add-back" bar is appended that exactly cancels them, so the chart foots
    to the *incremental* decision value shown in the metrics above it, not
    the full-cargo P&L, while still showing where that value came from.
    margin + addback == incremental value / cargo_size, matching
    decision.route_value()'s sunk-cost add-back exactly."""
    if not sunk:
        return bd["lines"], bd["margin"]
    addback = sum(v for n, v in bd["lines"] if n in {"Procurement", "Loading"})
    lines = bd["lines"] + [("Sunk cost add-back", -addback)]
    return lines, bd["margin"] + addback


def _plotly_programme_waterfall(title: str, legs, residual_days: float, residual_value: float,
                                 total_value: float):
    """How the programme's total $ value is assembled: one relative bar per
    scheduled cargo's incremental value, one for residual vessel-day value,
    and a total bar. Reads decision.ProgrammeLeg.value only -- no
    recalculation."""
    names = [f"Cargo {leg.cargo_number}: {leg.route} ({leg.load_month.strftime('%b-%y')})" for leg in legs]
    values = [leg.value for leg in legs]
    if abs(residual_value) > 1e-6 or residual_days > 1e-6:
        names.append(f"Residual ({residual_days:,.2f} d)")
        values.append(residual_value)
    names.append("Programme value")
    measures = ["relative"] * len(values) + ["total"]
    values_full = values + [total_value]
    fig = go.Figure(go.Waterfall(
        x=names, measure=measures, y=values_full,
        text=[f"{v:,.0f}" for v in values_full],
        textposition="outside",
        increasing={"marker": {"color": "#2f6db3"}},
        decreasing={"marker": {"color": "#c66b4e"}},
        totals={"marker": {"color": "#3d8a5f" if total_value >= 0 else "#b23a3a"}},
        connector={"line": {"color": "#c9d0d8", "dash": "dot"}},
    ))
    fig.update_layout(title=title, showlegend=False, height=380, yaxis_title="$",
                       margin=dict(t=40, b=10, l=10, r=10))
    return fig

# ===========================================================================
# Data loading (spec 1.7: env var path, cached on path+mtime; else
# file_uploader fallback so the app runs without the Drive path mounted)
# ===========================================================================


@st.cache_data(show_spinner="Loading LNG history.xlsx ...")
def _load_from_path(path: str, mtime: float):
    return data.load_all(path, source_label=path)


def get_tables():
    path = os.environ.get(data.ENV_VAR_NAME) or data.default_data_path()
    if path:
        try:
            return _load_from_path(path, os.path.getmtime(path))
        except Exception as e:
            st.sidebar.error(f"Failed to load {path}: {e}")

    st.sidebar.info(
        f"LNG history.xlsx not found (checked ${data.ENV_VAR_NAME} and conventional paths). "
        "Upload it below."
    )
    uploaded = st.sidebar.file_uploader("LNG history.xlsx", type=["xlsx"])
    if uploaded is None:
        st.title("LNG Forward Netback")
        st.warning(
            f"Waiting for LNG history.xlsx -- set the {data.ENV_VAR_NAME} environment variable "
            "to its path, or upload the file in the sidebar."
        )
        st.stop()
    return data.load_all_from_upload(uploaded)


tables = get_tables()
for w in tables.warnings:
    st.sidebar.warning(w)

# ===========================================================================
# Sidebar: curve date + Section 2 Step 5 parameters
# ===========================================================================

st.sidebar.header("Curve date")
master_desc = list(pd.DatetimeIndex(tables.master_dates).sort_values(ascending=False))
date_labels = [d.strftime("%Y-%m-%d (%a)") for d in master_desc]
sel_label = st.sidebar.selectbox(
    "Date (latest first, master list only)", date_labels, index=0,
    help="Restricted to the master date list (TTF dates >= first complete date of every other table).",
)
D = master_desc[date_labels.index(sel_label)]

if "params" not in st.session_state:
    st.session_state.params = model.Params()
p = st.session_state.params

st.sidebar.header("Cost parameters (Step 5)")

with st.sidebar.expander("Cargo / boil-off"):
    p.cargo_size = st.number_input("Cargo size (MMBtu)", value=float(p.cargo_size), step=50_000.0, format="%.0f")
    p.boil_off_rate = st.number_input("Boil-off rate (fraction/day)", value=float(p.boil_off_rate),
                                       step=0.0001, format="%.4f")

with st.sidebar.expander("Europe route"):
    p.europe_laden_days = st.number_input("Europe laden days", value=float(p.europe_laden_days), step=1.0)
    p.europe_ballast_days = st.number_input("Europe ballast days", value=float(p.europe_ballast_days), step=1.0)
    p.europe_port_days = st.number_input("Europe port days", value=float(p.europe_port_days), step=1.0)
    st.caption(f"Europe RT = {p.europe_laden_days + p.europe_ballast_days + p.europe_port_days:.0f} d")
    p.loading = st.number_input("Loading ($/MMBtu)", value=float(p.loading), step=0.01, format="%.2f")
    p.eu_regas_port = st.number_input("EU regas + port ($/MMBtu)", value=float(p.eu_regas_port), step=0.01, format="%.2f")
    p.other_cost = st.number_input("Other: insurance/LC/brokerage ($/MMBtu)", value=float(p.other_cost),
                                    step=0.01, format="%.2f")

with st.sidebar.expander("Asia route"):
    rt_options = ["Base (46.7d)", "Congestion (54.7d)", "Custom"]
    rt_default = (0 if abs(p.asia_rt_days - model.ASIA_RT_BASE) < 0.01
                  else (1 if abs(p.asia_rt_days - model.ASIA_RT_CONG) < 0.01 else 2))
    rt_choice = st.radio("Asia RT", rt_options, index=rt_default, horizontal=True)
    if rt_choice == "Base (46.7d)":
        p.asia_rt_days = model.ASIA_RT_BASE
    elif rt_choice == "Congestion (54.7d)":
        p.asia_rt_days = model.ASIA_RT_CONG
    else:
        p.asia_rt_days = st.number_input("Asia RT custom (days)", value=float(p.asia_rt_days), step=1.0)
    p.asia_port_days = st.number_input("Asia port days", value=float(p.asia_port_days), step=1.0)
    st.caption(f"Symmetric legs (workbook parity): laden = ballast = "
               f"{p.asia_laden_days:.1f} d. Congestion lengthens both legs "
               f"(more boil-off and laden fuel).")
    if st.checkbox("Override laden days (model waiting as ballast/idle)", value=False):
        p.asia_laden_days_override = st.number_input(
            "Asia laden days (pinned)", value=float(p.asia_laden_days), step=1.0)
    else:
        p.asia_laden_days_override = None
    p.asia_port_cost = st.number_input("Asia port, DES no regas ($/MMBtu)", value=float(p.asia_port_cost),
                                        step=0.01, format="%.2f")
    p.panama_toll_roundtrip = st.number_input("Panama toll x2 ($)", value=float(p.panama_toll_roundtrip),
                                               step=50_000.0, format="%.0f")

with st.sidebar.expander("Fuel"):
    p.laden_fuel_requirement = st.number_input("Laden fuel requirement (t/d, reference)",
                                                value=float(p.laden_fuel_requirement), step=1.0)
    p.natural_bog_offset_t = st.number_input("Natural BOG offset (t/d VLSFO-eq, reference)",
                                              value=float(p.natural_bog_offset_t), step=0.1)
    p.residual_laden_vlsfo = st.number_input("Residual laden VLSFO (t/d, used in ship cost)",
                                              value=float(p.residual_laden_vlsfo), step=0.1)
    p.ballast_fuel = st.number_input("Ballast fuel (t/d, used in ship cost)", value=float(p.ballast_fuel), step=1.0)
    p.port_fuel_rate = st.number_input("Port fuel (t/d, used in ship cost)", value=float(p.port_fuel_rate), step=1.0)
    p.vlsfo_price = st.number_input("VLSFO ($/t, static for ALL dates)", value=float(p.vlsfo_price), step=5.0)

with st.sidebar.expander("Gas cost chain"):
    p.hh_grossup = st.number_input("HH gross-up", value=float(p.hh_grossup), step=0.01, format="%.2f")
    p.liquefaction_toll = st.number_input("Liquefaction toll ($/MMBtu)", value=float(p.liquefaction_toll),
                                           step=0.05, format="%.2f")
    p.pipeline = st.number_input("Pipeline ($/MMBtu)", value=float(p.pipeline), step=0.01, format="%.2f")

with st.sidebar.expander("EU ETS"):
    p.eua_price = st.number_input("EUA price (EUR/t, static, unverified)", value=float(p.eua_price), step=5.0)
    p.co2_eu_ets_tonnes = st.number_input("CO2 in ETS scope per EU RT (t)", value=float(p.co2_eu_ets_tonnes), step=10.0)
    snap_L = model.contract_calendar(D)[0]
    st.caption(f"Phase factor for {snap_L.strftime('%b-%y')} (M1): {model.phase_for_year(snap_L.year)}  "
               "(0 before 2024, 0.4 in 2024, 0.7 in 2025, 1.0 from 2026)")

with st.sidebar.expander("Charter", expanded=True):
    snap_ch = model.snap(tables.charter, D)
    st.caption(f"Snapped charter (174k 2-stroke) at {snap_ch['date'].date()}: ${snap_ch['rate174']:,.0f}/day")
    override_on = st.checkbox("Override charter rate", value=p.charter_override is not None)
    if override_on:
        default_val = p.charter_override if p.charter_override is not None else float(snap_ch["rate174"])
        p.charter_override = st.number_input("Charter override ($/day)", value=float(default_val),
                                              step=5_000.0, format="%.0f")
    else:
        p.charter_override = None

if st.sidebar.button("Reset parameters to spec defaults"):
    st.session_state.params = model.Params()
    st.rerun()

params = st.session_state.params
st.sidebar.caption(f"Data source: {tables.source or '(uploaded file)'}")

# ===========================================================================
# Decision state and page routing
# ===========================================================================

MODE_LABELS = {
    "Vessel programme": decision.DecisionMode.VESSEL_PROGRAMME,
    "Post-lift diversion": decision.DecisionMode.POST_LIFT_DIVERSION,
    "Pre-lift cargo": decision.DecisionMode.PRE_LIFT_CARGO,
    "Renewal-rate screen (legacy)": decision.DecisionMode.RENEWAL_RATE_SCREEN,
}
mode_label = st.sidebar.selectbox("Decision mode", list(MODE_LABELS), index=0)
decision_mode = MODE_LABELS[mode_label]
st.sidebar.caption(
    "The decision state controls whether procurement and loading are sunk, "
    "included, or applied only to later cargoes."
)

PAGE = st.sidebar.radio(
    "Page",
    ["0 Decision", "1 Forward strip", "2 Sensitivities", "3 Hedging", "4 VaR & stress"],
)

CAVEATS = (
    "Caveats (LNG_Diversion_Logic.md v2): 47d Asia RT assumes ~19.5 kn and 1-day canal transit "
    "(55d = congestion case); FuelEU, CH4 slip, heel, demurrage, backhaul are not modelled; VLSFO "
    "and EUA are static for all dates including historical ones; margin/day comparison assumes the "
    "vessel is the binding constraint."
)

# ===========================================================================
# Page 1 -- Netback
# ===========================================================================

if PAGE == "0 Decision":
    st.title("LNG cargo and vessel decision")
    strip_df = model.strip(D, tables, params)
    snap_info = strip_df.attrs["snap"]
    months = list(strip_df["month_label"])
    month_index = st.selectbox(
        "Current cargo load month",
        options=list(range(12)),
        format_func=lambda i: f"M{i + 1} = {months[i]}",
    )

    if decision_mode == decision.DecisionMode.RENEWAL_RATE_SCREEN:
        st.info(
            "This mode preserves the original repeated-deployment screen. "
            "Its recommendation is based on margin per vessel-day, not a post-lift "
            "diversion NPV or a discrete cargo programme."
        )
        row = strip_df.iloc[month_index]
        c1, c2, c3 = st.columns(3)
        c1.metric("Renewal-rate result", row["verdict"])
        c2.metric("Europe value/day", f"${row['eu_day']:,.0f}")
        c3.metric("Asia value/day", f"${row['asia_day']:,.0f}")
        st.caption("Use the Forward strip page for the full 12-month legacy analysis.")

    elif decision_mode in {decision.DecisionMode.POST_LIFT_DIVERSION, decision.DecisionMode.PRE_LIFT_CARGO}:
        values = decision.isolated_route_values(
            strip_df, params, decision_mode, month_index=month_index
        )
        ranked = sorted(values, key=lambda x: x.incremental_value, reverse=True)
        best = ranked[0]
        next_best = ranked[1]
        c1, c2, c3 = st.columns(3)
        c1.metric("Recommended route", best.route)
        c2.metric("Decision value", f"${best.incremental_value:,.0f}")
        c3.metric("Advantage versus next best", f"${best.incremental_value-next_best.incremental_value:,.0f}")
        rows = []
        for value in ranked:
            rows.append({
                "route": value.route,
                "load_month": value.load_month,
                "duration_days": value.duration_days,
                "incremental_value": value.incremental_value,
                "full_cargo_value": value.full_cargo_value,
                "procurement": value.procurement_treatment.value,
                "loading": value.loading_treatment.value,
            })
        st.dataframe(
            pd.DataFrame(rows).style.format({
                "duration_days": "{:,.4f}",
                "incremental_value": "${:,.0f}",
                "full_cargo_value": "${:,.0f}",
            }),
            width="stretch", hide_index=True,
        )
        if decision_mode == decision.DecisionMode.POST_LIFT_DIVERSION:
            st.caption(
                "Procurement and completed loading are sunk in incremental value, "
                "but remain in full-cargo P&L."
            )

        st.subheader("How the decision value is calculated")
        wf_route = st.radio("Route", [v.route for v in ranked], horizontal=True, key="isolated_wf_route")
        wf_value = next(v for v in ranked if v.route == wf_route)
        wf_sunk = wf_value.procurement_treatment == decision.CostTreatment.SUNK
        wf_bd_all = model.waterfall_breakdown(strip_df.iloc[month_index].to_dict(), params)
        wf_lines, wf_margin = _decision_waterfall_lines(wf_bd_all[wf_route], wf_sunk)
        st.plotly_chart(
            _plotly_waterfall(
                f"{wf_route} {'post-lift decision' if wf_sunk else 'full-cargo'} "
                f"waterfall ({wf_value.load_month.strftime('%b-%y')})",
                wf_bd_all[wf_route]["revenue"], wf_lines, wf_margin,
            ),
            width="stretch",
        )
        st.caption(
            f"$/MMBtu margin x cargo size ({params.cargo_size:,.0f} MMBtu) = "
            f"${wf_margin * params.cargo_size:,.0f}, matching the decision value above "
            "(subject to rounding)." + (
                " Procurement and loading are shown as real costs (gas was actually bought "
                "and loaded), then reversed on the Sunk cost add-back bar because they were "
                "incurred before this decision point -- that is the incremental view, not "
                "the full-cargo P&L."
                if wf_sunk else ""
            )
        )

    else:
        st.subheader("Discrete one-vessel programme")
        c1, c2, c3 = st.columns(3)
        horizon = c1.number_input("Programme horizon (days)", min_value=1.0, value=52.0, step=1.0)
        max_additional = c2.number_input("Additional cargoes available", min_value=0, max_value=11, value=1, step=1)
        residual_value = c3.number_input("Residual vessel value ($/day)", value=0.0, step=10_000.0, format="%.0f")
        asia_case = st.radio(
            "Asia route case for programme",
            ["Use sidebar route", "Base 46.7436 days", "Congested 54.7436 days"],
            horizontal=True,
        )
        programme_params = copy.deepcopy(params)
        if asia_case == "Base 46.7436 days":
            programme_params.asia_rt_days = model.ASIA_RT_BASE
        elif asia_case == "Congested 54.7436 days":
            programme_params.asia_rt_days = model.ASIA_RT_CONG
        # Rebuild strip because Asia value and laden duration depend on the selected RT.
        programme_strip = model.strip(D, tables, programme_params)
        try:
            result = decision.optimise_programme(
                programme_strip, programme_params, horizon_days=float(horizon),
                current_month_index=month_index,
                current_mode=decision.DecisionMode.POST_LIFT_DIVERSION,
                max_additional_cargoes=int(max_additional),
                residual_value_per_day=float(residual_value),
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            best = result.best
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Best programme", best.sequence)
            c2.metric("Programme value", f"${best.total_value:,.0f}")
            c3.metric("Used vessel-days", f"{best.used_days:,.4f}")
            advantage = result.advantage
            c4.metric("Advantage versus next best", "n/a" if advantage is None else f"${advantage:,.0f}")
            st.dataframe(
                decision.programme_frame(result).style.format({
                    "used_days": "{:,.4f}",
                    "residual_days": "{:,.4f}",
                    "residual_value": "${:,.0f}",
                    "programme_value": "${:,.0f}",
                }),
                width="stretch", hide_index=True,
            )
            st.subheader("Best programme schedule")
            st.dataframe(
                decision.schedule_frame(best).style.format({
                    "start_day": "{:,.4f}",
                    "duration_days": "{:,.4f}",
                    "end_day": "{:,.4f}",
                    "value": "${:,.0f}",
                }),
                width="stretch", hide_index=True,
            )
            st.caption(
                "Current cargo is valued post-lift. Every later cargo is valued "
                "pre-lift using the forward-strip month matching its start date. "
                "The provisional programme uses legacy voyage physics and must be "
                "re-baselined after the physical-engine rebuild."
            )

            # --- How the programme value is calculated -----------------------
            # Recomputes decision.route_value() per leg for display only; this
            # is exactly what optimise_programme() already computed internally
            # to produce leg.value, so leg_values[i].incremental_value ==
            # best.legs[i].value by construction -- nothing new is modelled.
            st.subheader("How the programme value is calculated")
            leg_values = [
                decision.route_value(
                    programme_strip.iloc[leg.month_index], programme_params, leg.route, leg.decision_mode,
                    month_index=leg.month_index, future_cargo=(leg.cargo_number > 1),
                )
                for leg in best.legs
            ]
            detail_rows = []
            for leg, rv in zip(best.legs, leg_values):
                detail_rows.append({
                    "cargo": str(leg.cargo_number),
                    "route": leg.route,
                    "load_month": leg.load_month.strftime("%b-%y"),
                    "decision_mode": leg.decision_mode.value,
                    "full_cargo_value": rv.full_cargo_value,
                    "procurement": rv.procurement_treatment.value,
                    "loading": rv.loading_treatment.value,
                    "sunk_addback": rv.incremental_value - rv.full_cargo_value,
                    "cargo_value": rv.incremental_value,
                })
            if abs(best.residual_value) > 1e-6 or best.residual_days > 1e-6:
                detail_rows.append({
                    "cargo": "residual", "route": "-", "load_month": "-",
                    "decision_mode": "residual vessel-day value",
                    "full_cargo_value": np.nan, "procurement": "-", "loading": "-",
                    "sunk_addback": np.nan, "cargo_value": best.residual_value,
                })
            st.dataframe(
                pd.DataFrame(detail_rows).style.format({
                    "full_cargo_value": "${:,.0f}", "sunk_addback": "${:,.0f}", "cargo_value": "${:,.0f}",
                }, na_rep="-"),
                width="stretch", hide_index=True,
            )
            st.caption(
                "full_cargo_value is the pre-lift cargo margin from the forward strip. "
                "sunk_addback restores procurement/loading for an already-loaded current "
                "cargo (post-lift) and is zero for future pre-lift cargoes, which already "
                "include procurement once. cargo_value = full_cargo_value + sunk_addback, "
                "and the cargo_value column sums to the programme value above."
            )
            st.plotly_chart(
                _plotly_programme_waterfall(
                    f"Programme value build-up: {best.sequence}",
                    best.legs, best.residual_days, best.residual_value, best.total_value,
                ),
                width="stretch",
            )

            st.subheader("How one cargo's value is calculated")
            leg_labels = [f"Cargo {leg.cargo_number}: {leg.route} ({leg.load_month.strftime('%b-%y')})"
                          for leg in best.legs]
            leg_pick = st.selectbox("Cargo", options=list(range(len(best.legs))),
                                     format_func=lambda i: leg_labels[i], key="programme_leg_pick")
            sel_leg, sel_rv = best.legs[leg_pick], leg_values[leg_pick]
            sel_sunk = sel_rv.procurement_treatment == decision.CostTreatment.SUNK
            sel_bd_all = model.waterfall_breakdown(
                programme_strip.iloc[sel_leg.month_index].to_dict(), programme_params
            )
            sel_lines, sel_margin = _decision_waterfall_lines(sel_bd_all[sel_leg.route], sel_sunk)
            st.plotly_chart(
                _plotly_waterfall(
                    f"Cargo {sel_leg.cargo_number}: {sel_leg.route} "
                    f"{'post-lift decision' if sel_sunk else 'pre-lift'} "
                    f"waterfall ({sel_leg.load_month.strftime('%b-%y')})",
                    sel_bd_all[sel_leg.route]["revenue"], sel_lines, sel_margin,
                ),
                width="stretch",
            )
            st.caption(
                f"$/MMBtu margin x cargo size ({programme_params.cargo_size:,.0f} MMBtu) = "
                f"${sel_margin * programme_params.cargo_size:,.0f}, matching this cargo's value "
                "in the table above (subject to rounding)."
            )

    fx_rows = model.fx_extrapolated_rows(strip_df)
    if not fx_rows.empty:
        labels = ", ".join(fx_rows["month_label"].astype(str))
        st.warning(
            f"FX curve warning: {labels} lie beyond the available 1Y outright and "
            "are linearly extrapolated in the current screening model."
        )

elif PAGE == "1 Forward strip":
    st.title("LNG Forward Netback")
    strip_df = model.strip(D, tables, params)
    snap_info = strip_df.attrs["snap"]
    fx_rows = model.fx_extrapolated_rows(strip_df)
    if not fx_rows.empty:
        labels = ", ".join(fx_rows["month_label"].astype(str))
        st.warning(
            f"FX curve warning: {labels} lie beyond the available 1Y outright and "
            "are linearly extrapolated in the current screening model."
        )

    st.caption(
        f"Curve date D = **{D.date()}**  |  Snapped -- HH: {snap_info.hh_date.date()}, "
        f"TTF: {snap_info.ttf_date.date()}, JKM: {snap_info.jkm_date.date()}, "
        f"FX: {snap_info.fx_date.date()}, Charter: {snap_info.charter_date.date()} "
        f"(${snap_info.charter_rate:,.0f}/day" + (", overridden" if snap_info.charter_overridden else "") + ")  |  "
        f"Front month F = **{snap_info.F.strftime('%b-%y')}**, JKM roll shift s = **{snap_info.s}**"
    )

    display = strip_df[["month_label", "HH", "TTF", "ttf_usd", "JKM", "eu_day", "asia_day",
                         "jkm_star", "gap", "verdict"]].copy()
    display.columns = ["Month", "HH $/MMBtu", "TTF EUR/MWh", "TTF $/MMBtu", "JKM(L+1) $/MMBtu",
                        "EU $/day", "Asia $/day", "JKM* $/MMBtu", "Gap $/MMBtu", "Verdict"]
    display.attrs = {}  # drop the attached SnapInfo (not JSON-serialisable, harmless but noisy)

    def _verdict_color(row):
        color = "#d6f5d6" if row["Verdict"] == "Asia" else "#dbe9fa"
        return [f"background-color: {color}; font-weight: 600" if col == "Verdict" else "" for col in row.index]

    styled = display.style.apply(_verdict_color, axis=1).format({
        "HH $/MMBtu": "{:.3f}", "TTF EUR/MWh": "{:.3f}", "TTF $/MMBtu": "{:.3f}",
        "JKM(L+1) $/MMBtu": "{:.3f}", "EU $/day": "{:,.0f}", "Asia $/day": "{:,.0f}",
        "JKM* $/MMBtu": "{:.3f}", "Gap $/MMBtu": "{:+.3f}",
    })
    st.dataframe(styled, width="stretch", hide_index=True)

    both_neg = strip_df[(strip_df["eu_day"] < 0) & (strip_df["asia_day"] < 0)]
    if not both_neg.empty:
        st.warning(
            "Both routes lose money in: " + ", ".join(both_neg["month_label"]) + ". "
            "The verdict is RELATIVE only (least-bad destination conditional on lifting). "
            "Evaluate lift vs do-not-lift (cancellation value, FOB resale, slot release) "
            "before acting -- this model does not price that decision."
        )

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("JKM vs JKM* (breakeven)")
        chart_df = strip_df.set_index("month_label")[["JKM", "jkm_star"]].rename(
            columns={"JKM": "JKM (L+1)", "jkm_star": "JKM* (breakeven)"})
        st.line_chart(chart_df)
    with col2:
        st.subheader("Margin per vessel-day")
        chart_df2 = strip_df.set_index("month_label")[["eu_day", "asia_day"]].rename(
            columns={"eu_day": "Europe $/day", "asia_day": "Asia $/day"})
        st.bar_chart(chart_df2)

    st.subheader("Waterfall & flows (single load month)")
    wf_mi = st.selectbox("Load month for waterfall/flow view", options=list(range(12)),
                          format_func=lambda i: f"M{i + 1} = {strip_df.iloc[i]['month_label']}",
                          key="wf_month")
    wf_row = strip_df.iloc[wf_mi].to_dict()
    breakdown = model.waterfall_breakdown(wf_row, params)
    eu_bd, as_bd = breakdown["Europe"], breakdown["Asia"]

    wcol1, wcol2 = st.columns(2)
    with wcol1:
        st.plotly_chart(
            _plotly_waterfall(f"Europe netback waterfall ({wf_row['month_label']})",
                               eu_bd["revenue"], eu_bd["lines"], eu_bd["margin"]),
            width="stretch")
    with wcol2:
        st.plotly_chart(
            _plotly_waterfall(f"Asia netback waterfall ({wf_row['month_label']})",
                               as_bd["revenue"], as_bd["lines"], as_bd["margin"]),
            width="stretch")

    fcol1, fcol2 = st.columns(2)
    with fcol1:
        st.plotly_chart(
            _plotly_flow_sankey("Europe: where each revenue dollar goes",
                                 eu_bd["revenue"], eu_bd["lines"], eu_bd["margin"]),
            width="stretch")
        if eu_bd["margin"] < 0:
            st.caption(f"Costs exceed revenue: Europe margin {eu_bd['margin']:+.2f} $/MMBtu -- no Margin flow shown.")
    with fcol2:
        st.plotly_chart(
            _plotly_flow_sankey("Asia: where each revenue dollar goes",
                                 as_bd["revenue"], as_bd["lines"], as_bd["margin"]),
            width="stretch")
        if as_bd["margin"] < 0:
            st.caption(f"Costs exceed revenue: Asia margin {as_bd['margin']:+.2f} $/MMBtu -- no Margin flow shown.")

    st.caption(
        "Waterfall/flow charts read the same model.strip() fields as the table above "
        "(model.waterfall_breakdown) -- not an independent recalculation, unlike "
        "LNG_Diversion_Waterfall_Flows.html's standalone JS model."
    )

    st.caption(CAVEATS)

# ===========================================================================
# Page 2 -- Sensitivities
# ===========================================================================

elif PAGE == "2 Sensitivities":
    st.title("Sensitivities")
    strip_df = model.strip(D, tables, params)
    months = list(strip_df["month_label"])
    mi = st.selectbox("Load month", options=list(range(12)), format_func=lambda i: f"M{i + 1} = {months[i]}")

    st.subheader("Tornado: per-cargo P&L delta (Section 6)")
    tdf = risk.tornado_data(D, tables, params, month_index=mi)
    if not tdf.empty:
        chart = alt.Chart(tdf).mark_bar().encode(
            x=alt.X("cargo_pnl:Q", title="Cargo P&L delta ($)"),
            y=alt.Y("shock:N", sort="-x", title=None),
            color=alt.Color("basin:N", scale=alt.Scale(domain=["Europe", "Asia"], range=["#3b82c4", "#2ca85a"])),
            tooltip=["shock", "basin", alt.Tooltip("cargo_pnl:Q", format=",.0f")],
        ).properties(height=320)
        st.altair_chart(chart, width="stretch")

    with st.expander("Analytic vs finite-difference cross-check"):
        an = risk.analytic_deltas(D, tables, params, month_index=mi)
        fd = risk.finite_difference_deltas(D, tables, params, month_index=mi)
        rows = [dict(shock=a.name, eu_analytic=a.eu_cargo_delta, eu_finite_diff=f.eu_cargo_delta,
                      asia_analytic=a.asia_cargo_delta, asia_finite_diff=f.asia_cargo_delta, note=a.note)
                for a, f in zip(an, fd)]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    st.subheader("Asia RT breakeven")
    rt_df = risk.asia_rt_breakeven(D, tables, params, month_index=mi,
                                    rt_values=(model.ASIA_RT_BASE, model.ASIA_RT_BASE + 4, model.ASIA_RT_CONG))
    st.dataframe(rt_df, width="stretch", hide_index=True)

    st.subheader("TTF shock x Asia-RT breakeven grid (Gap = JKM - JKM*)")
    grid = risk.ttf_x_asiart_grid(D, tables, params, month_index=mi)
    pivot = grid.pivot(index="asia_rt_days", columns="ttf_shock", values="gap")

    def _gap_color(v):
        if pd.isna(v):
            return ""
        return f"background-color: {'#d6f5d6' if v >= 0 else '#f8d7da'}"

    st.dataframe(pivot.style.map(_gap_color).format("{:+.2f}"), width="stretch")
    st.caption("Green = Asia verdict (Gap >= 0) at that TTF shock x Asia-RT combination; red = Europe verdict.")

    st.subheader("What-if sliders (live recompute, this load month)")
    c1, c2, c3 = st.columns(3)
    ttf_shock = c1.slider("TTF shock (EUR/MWh)", -20.0, 20.0, 0.0, 0.5)
    jkm_shock = c1.slider("JKM shock ($/MMBtu)", -5.0, 5.0, 0.0, 0.1)
    hh_shock = c2.slider("HH shock ($/MMBtu)", -3.0, 3.0, 0.0, 0.1)
    fx_shock = c2.slider("EURUSD shock, parallel", -0.10, 0.10, 0.0, 0.005)
    charter_shock = c3.slider("Charter shock ($/day)", -50_000.0, 50_000.0, 0.0, 5_000.0)
    vlsfo_shock = c3.slider("VLSFO shock ($/t)", -200.0, 200.0, 0.0, 10.0)

    t2 = copy.deepcopy(tables)
    p2 = copy.deepcopy(params)
    if ttf_shock:
        t2.ttf = t2.ttf.copy()
        row = model.snap(t2.ttf, D)
        t2.ttf.loc[t2.ttf["date"] == row["date"], f"c{mi + 1}"] += ttf_shock
    if jkm_shock:
        t2.jkm = t2.jkm.copy()
        row = model.snap(t2.jkm, D)
        _, s_ = model.contract_calendar(D)
        jkm_idx = mi + 2 - s_
        t2.jkm.loc[t2.jkm["date"] == row["date"], f"c{jkm_idx}"] += jkm_shock
    if hh_shock:
        t2.hh = t2.hh.copy()
        row = model.snap(t2.hh, D)
        t2.hh.loc[t2.hh["date"] == row["date"], f"c{mi + 1}"] += hh_shock
    if fx_shock:
        t2.fx = t2.fx.copy()
        row = model.snap(t2.fx, D)
        mask = t2.fx["date"] == row["date"]
        t2.fx.loc[mask, "spot"] += fx_shock
        t2.fx.loc[mask, "o6"] += fx_shock
        t2.fx.loc[mask, "o1"] += fx_shock
    if charter_shock:
        base_charter = p2.charter_override if p2.charter_override is not None else float(model.snap(tables.charter, D)["rate174"])
        p2.charter_override = base_charter + charter_shock
    if vlsfo_shock:
        p2.vlsfo_price += vlsfo_shock

    base_row = strip_df.iloc[mi]
    shocked_row = model.strip(D, t2, p2).iloc[mi]
    wc1, wc2, wc3, wc4 = st.columns(4)
    wc1.metric("EU $/day", f"${shocked_row['eu_day']:,.0f}", f"{shocked_row['eu_day'] - base_row['eu_day']:+,.0f}")
    wc2.metric("Asia $/day", f"${shocked_row['asia_day']:,.0f}", f"{shocked_row['asia_day'] - base_row['asia_day']:+,.0f}")
    wc3.metric("JKM*", f"{shocked_row['jkm_star']:.2f}", f"{shocked_row['jkm_star'] - base_row['jkm_star']:+.2f}")
    wc4.metric("Verdict", shocked_row["verdict"],
               "flipped" if shocked_row["verdict"] != base_row["verdict"] else "unchanged")

    st.subheader("Scenario presets")
    presets = risk.scenario_presets()
    preset_name = st.selectbox("Preset", list(presets.keys()))
    if preset_name != "Base case":
        D_p, t_p, p_p = risk.apply_preset(D, tables, params, presets[preset_name])
        strip_p = model.strip(D_p, t_p, p_p)
        preset_display = strip_p[["month_label", "eu_day", "asia_day", "jkm_star", "gap", "verdict"]].copy()
        preset_display.attrs = {}
        st.caption(f"Preset '{preset_name}' -- curve date used: {D_p.date()}")
        st.dataframe(preset_display, width="stretch", hide_index=True)

    st.caption(CAVEATS)

# ===========================================================================
# Page 3 -- Hedging
# ===========================================================================

elif PAGE == "3 Hedging":
    st.title("Hedging")
    strip_df = model.strip(D, tables, params)
    months = list(strip_df["month_label"])

    spec_lines = []
    for k, v in risk.CONTRACT_SPECS.items():
        size_txt = f" = {v['size']}" if v["size"] is not None else " (no fixed lot; OTC notional)"
        spec_lines.append(f"- **{k}**: {v['unit']}{size_txt}")
    st.warning(
        "**Contract sizes are as understood at spec-write time and have NOT been verified against "
        "current exchange specs -- verify before go-live:**\n\n" + "\n".join(spec_lines)
    )

    basin = st.radio("Basin", ["Europe", "Asia"], horizontal=True)
    mi = st.selectbox("Load month", options=list(range(12)), format_func=lambda i: f"M{i + 1} = {months[i]}")

    if basin == "Europe":
        legs = risk.europe_hedge_legs(D, tables, params, month_index=mi)
    else:
        legs = risk.asia_hedge_legs(D, tables, params, month_index=mi)

    display_legs = legs.copy()
    display_legs.attrs = {}  # drop the attached SnapInfo (not JSON-serialisable, harmless but noisy)
    display_legs["volume"] = display_legs["volume"].map(lambda v: f"{v:,.1f}" if pd.notna(v) else "-")
    display_legs["lots"] = display_legs["lots"].map(lambda v: f"{v:,.2f}" if pd.notna(v) else "-")
    st.dataframe(display_legs, width="stretch", hide_index=True)

    st.subheader("Hedge effectiveness (500-scenario historical VaR)")
    lookback = st.select_slider("Lookback (business days)", options=[250, 500, 750], value=500)
    scen = risk.build_scenarios(tables, D, lookback=lookback, method="naive")
    r_un = risk.historical_var(D, tables, params, portfolio="single", month_index=mi, basin=basin, scen=scen)
    r_hd = risk.historical_var(D, tables, params, portfolio="hedged", month_index=mi, basin=basin, scen=scen)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**{basin} M{mi + 1} unhedged**")
        st.metric("VaR 95% (1d)", f"${r_un.var95:,.0f}")
        st.metric("VaR 99% (1d)", f"${r_un.var99:,.0f}")
        st.metric("Daily sd", f"${r_un.sd:,.0f}")
    with c2:
        st.markdown(f"**{basin} M{mi + 1} hedged (mechanical legs above)**")
        st.metric("VaR 95% (1d)", f"${r_hd.var95:,.0f}")
        st.metric("VaR 99% (1d)", f"${r_hd.var99:,.0f}")
        st.metric("Daily sd", f"${r_hd.sd:,.0f}")

    ratio = abs(r_hd.var95) / max(abs(r_un.var95), 1.0)
    if ratio > 0.01:
        st.error(f"Hedged residual VaR95 is {ratio:.2%} of unhedged -- above the ~1% ratio-bug threshold "
                 "the spec flags; check hedge ratios.")
    else:
        st.success(f"Hedged residual VaR95 is {ratio:.2%} of unhedged -- residual is boil-off / ETS / "
                   "second-order FX cross-term risk only, as expected (Section 7).")
    st.caption(
        "This effectiveness is MODEL-INTERNAL: hedge and cargo are revalued off the same index "
        "curves, so it mainly proves the ratios invert the model's own formula. Physical basis "
        "(NWE DES-TTF, JKM index vs physical, USGC terminal basis to HH), pricing-window "
        "mismatch, lot rounding and transaction costs are not modelled; real residuals are larger."
    )

    st.caption(CAVEATS)

# ===========================================================================
# Page 4 -- VaR & stress
# ===========================================================================

else:
    st.title("VaR & stress")
    strip_df = model.strip(D, tables, params)
    months = list(strip_df["month_label"])

    PORTFOLIO_MAP = {
        "Single cargo - Europe": ("single", "Europe"),
        "Single cargo - Asia": ("single", "Asia"),
        "Hedged residual - Europe": ("hedged", "Europe"),
        "Hedged residual - Asia": ("hedged", "Asia"),
        "12-cargo strip (verdict-optimal)": ("12cargo", "Europe"),
        "M1 diversion spread (Asia minus Europe)": ("spread", "Europe"),
    }
    portfolio_choice = st.selectbox("Portfolio", list(PORTFOLIO_MAP.keys()))
    portfolio_kind, basin_kind = PORTFOLIO_MAP[portfolio_choice]

    mi = 0
    if portfolio_kind in ("single", "hedged", "spread"):
        mi = st.selectbox("Load month", options=list(range(12)), format_func=lambda i: f"M{i + 1} = {months[i]}")

    c1, c2 = st.columns(2)
    lookback = c1.select_slider("Lookback (business days)", options=[250, 500, 750], value=500)
    roll_on = c2.checkbox("Delivery-month roll-aligned returns (application default on)", value=True)
    method = "roll_aligned" if roll_on else "naive"

    if portfolio_kind == "12cargo":
        st.warning(
            "Legacy 12-cargo strip is not a physically time-feasible one-vessel "
            "portfolio. It is retained only for regression comparison until the "
            "programme-based risk portfolio is implemented."
        )
    try:
        scen = risk.build_scenarios(tables, D, lookback=lookback, method=method)
    except ValueError as exc:
        st.error(f"Cannot build {method} scenarios: {exc}")
        st.stop()
    r = risk.historical_var(D, tables, params, portfolio=portfolio_kind, month_index=mi,
                             basin=basin_kind, scen=scen)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("VaR 95% (1d)", f"${r.var95:,.0f}")
    m2.metric("VaR 99% (1d)", f"${r.var99:,.0f}")
    m3.metric("Expected shortfall 95%", f"${r.es95:,.0f}")
    m4.metric("Expected shortfall 99%", f"${r.es99:,.0f}")
    st.caption(f"Daily sd: ${r.sd:,.0f}  |  n={r.n} scenarios  |  window {scen.dates[0].date()} to "
               f"{scen.dates[-1].date()}  |  method={method}")

    st.subheader("P&L histogram")
    hist_df = pd.DataFrame({"pnl": r.pnl})
    base_chart = alt.Chart(hist_df).mark_bar(opacity=0.85).encode(
        x=alt.X("pnl:Q", bin=alt.Bin(maxbins=40), title="1-day P&L ($)"),
        y=alt.Y("count()", title="Scenarios"),
    )
    rule95 = alt.Chart(pd.DataFrame({"x": [r.var95], "label": ["VaR95"]})).mark_rule(
        color="#e67e22", strokeDash=[5, 3], size=2).encode(x="x:Q")
    rule99 = alt.Chart(pd.DataFrame({"x": [r.var99], "label": ["VaR99"]})).mark_rule(
        color="#c0392b", strokeDash=[5, 3], size=2).encode(x="x:Q")
    st.altair_chart((base_chart + rule95 + rule99).properties(height=320), width="stretch")

    st.subheader("10-day horizon")
    hc1, hc2 = st.columns(2)
    with hc1:
        var10_sqrt = risk.scale_to_horizon(r.var95, days=10, method="sqrt")
        st.metric("VaR95 (10d, sqrt-scaled)", f"${var10_sqrt:,.0f}")
        st.caption("Caveat: sqrt(10) scaling assumes iid daily returns; gas/LNG curve moves are "
                   "fat-tailed and cluster around events, so this is approximate.")
    with hc2:
        if st.checkbox("Compute overlapping 10-day VaR (slower, rebuilds scenario set)"):
            with st.spinner("Rebuilding overlapping 10-day scenarios..."):
                var10_ov = risk.scale_to_horizon(r.var95, days=10, method="overlapping", tables=tables, D=D,
                                                  params=params, portfolio=portfolio_kind, month_index=mi,
                                                  basin=basin_kind, scenario_method=method)
            st.metric("VaR95 (10d, overlapping returns)", f"${var10_ov:,.0f}")
            st.caption("No iid assumption, but overlapping windows are autocorrelated by construction.")

    st.subheader("Stress tests (deterministic replays, Section 8)")
    with st.spinner("Running stress tests..."):
        stress_df = risk.run_stress_tests(D, tables, params)
    st.dataframe(
        stress_df.style.format({"pnl_12cargo": "{:+,.0f}", "pnl_m1_spread": "{:+,.0f}"}),
        width="stretch", hide_index=True,
    )
    st.caption("Historical replays apply that date's actual single-day curve move onto today's curve. "
               "The pnl_12cargo column is a legacy regression portfolio and is not a feasible one-vessel programme.")

    with st.expander("Backtest: rolling 1-day VaR vs realised P&L (Kupiec traffic light)"):
        window_days = st.slider("Backtest window (business days)", 20, 150, 60, 10)
        st.caption("Each day in the window recomputes a full 500-scenario VaR, so this can take a while.")
        if portfolio_kind == "hedged":
            st.info("Interim roll-safe backtesting is not yet available for the hedged residual portfolio.")
        if st.button("Run backtest", disabled=portfolio_kind == "hedged"):
            with st.spinner(f"Running rolling backtest over {window_days} days..."):
                bt = risk.backtest_var(
                    tables, params, portfolio=portfolio_kind, lookback=lookback,
                    window_days=window_days, method=method, month_index=mi, basin=basin_kind,
                )
            if bt.empty:
                st.warning("No backtest rows produced (window too small relative to lookback).")
            else:
                skipped = int(bt.attrs.get("skipped_roll_pairs", 0))
                if skipped:
                    st.caption(
                        f"Skipped {skipped} month/JKM roll pair(s) to avoid comparing "
                        "different physical delivery months in the interim backtest."
                    )
                st.line_chart(bt.set_index("date")[["var", "realised_pnl"]])
                n_exceptions = int(bt["exception"].sum())
                kt = risk.kupiec_test(n_exceptions, len(bt))
                st.write(f"Exceptions: {n_exceptions} / {len(bt)} ({kt['rate']:.1%} vs 5% expected)  |  "
                         f"Kupiec traffic light: **{kt['light'].upper()}**  |  LR stat={kt['lr_stat']:.2f}, "
                         f"p={kt['p_value']:.3f}")
                st.dataframe(bt, width="stretch", hide_index=True)

    st.caption(CAVEATS)
    st.caption(
        "Known exclusions from this VaR: charter (weekly data -- cover via the Section 6 charter delta "
        "times an assumed weekly move), VLSFO and EUA (static inputs, stress-tested only above), FuelEU."
    )
