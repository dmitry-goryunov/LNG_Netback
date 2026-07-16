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
import importlib
import os

import altair as alt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data
import decision
import emissions
import model
import physical
import risk
import spread_option

st.set_page_config(page_title="LNG Forward Netback", layout="wide")

# --- Module-freshness guard ------------------------------------------------
# Streamlit hot-reload re-executes THIS script on every deploy/rerun, but
# modules it imports stay cached in the running process. After a deploy
# that adds a new function to a first-party module, this (fresh) script can
# reference a symbol the (stale) cached module lacks -- observed twice as an
# AttributeError: once locally (decision.physical_waterfall_breakdown) and
# once in production (model.derived_residual_laden_vlsfo, redacted crash on
# Streamlit Cloud). Each sentinel below is the newest app.py-referenced
# symbol of its module; if any is missing, every first-party module is
# reloaded IN DEPENDENCY ORDER (importlib.reload mutates the module object
# in place, so cross-module references pick up the new code too). Add a
# sentinel entry whenever app.py starts using a newly added symbol.
_FRESHNESS_SENTINELS = [
    (data, "load_volatilities"),
    (model, "derived_residual_laden_vlsfo"),
    (physical, "vessel_performance_from_params"),
    (emissions, "voyage_emissions"),
    (decision, "physical_waterfall_breakdown"),
    (spread_option, "month_spread_option"),
    (risk, "run_stress_tests"),
]
if any(not hasattr(_mod, _attr) for _mod, _attr in _FRESHNESS_SENTINELS):
    for _mod, _ in _FRESHNESS_SENTINELS:
        importlib.reload(_mod)
    _still_stale = [f"{_mod.__name__}.{_attr}" for _mod, _attr in _FRESHNESS_SENTINELS
                    if not hasattr(_mod, _attr)]
    if _still_stale:
        st.error(
            "The running process has stale copies of: " + ", ".join(_still_stale) +
            ". Reload did not resolve it -- restart the app (Streamlit Cloud: "
            "Manage app -> Reboot)."
        )
        st.stop()

# Decision and Forward-strip pages show this many forward months. HH/TTF
# have 64 forward columns and JKM 44 in the real workbook (comfortably
# covers 36); the FX curve now interpolates through real 2Y-10Y anchors
# instead of extrapolating past 1Y for anything beyond month 12
# (model.fx_curve_multi). Sensitivities/Hedging/VaR & stress deliberately
# stay at 12 months -- they call risk.py functions that independently
# call model.strip() with its 12-month default internally; extending
# those is a separate change to risk.py, not just this constant.
STRIP_MONTHS = 36

# Label -> model.strip() column, for the Forward-strip page's "any line
# item across all months" chart. Grouped by basin; a few (proc, HH, TTF,
# FX, jkm_star, gap) are shared/market-level rather than basin-specific.
STRIP_METRIC_OPTIONS = {
    "Europe: Margin ($/MMBtu)": "eu_margin",
    "Asia: Margin ($/MMBtu)": "asia_margin",
    "Europe: Revenue, TTF ($/MMBtu)": "ttf_usd",
    "Asia: Revenue, JKM(L+1) ($/MMBtu)": "JKM",
    "Procurement, both routes ($/MMBtu)": "proc",
    "Europe: Charter ($/MMBtu)": "eu_charter_cost",
    "Asia: Charter ($/MMBtu)": "asia_charter_cost",
    "Europe: Bunkers ($/MMBtu)": "eu_bunker_cost",
    "Asia: Bunkers ($/MMBtu)": "asia_bunker_cost",
    "Europe: Boil-off cost ($/MMBtu)": "eu_boiloff_cost",
    "Asia: Boil-off cost ($/MMBtu)": "asia_boiloff_cost",
    "Asia: Canal ($/MMBtu)": "asia_canal_cost",
    "Europe: ETS ($/MMBtu)": "ets",
    "Europe: $/vessel-day": "eu_day",
    "Asia: $/vessel-day": "asia_day",
    "Europe: Total cargo value ($)": "eu_cargo",
    "Asia: Total cargo value ($)": "asia_cargo",
    "HH ($/MMBtu)": "HH",
    "TTF (EUR/MWh)": "TTF",
    "FX (EUR/USD)": "fx",
    "JKM* breakeven ($/MMBtu)": "jkm_star",
    "Gap: JKM - JKM* ($/MMBtu)": "gap",
}

# Current-cargo commercial state (decision.FirstCargoState, Section 3a of
# docs/PHASE2_PLAN.md) -- a finer-grained refinement of POST_LIFT_DIVERSION/
# VESSEL_PROGRAMME's current cargo only. "Already loaded" reproduces this
# app's original behaviour exactly (both procurement and loading sunk);
# it stays the default so nothing changes unless a user picks otherwise.
FIRST_CARGO_STATE_LABELS = {
    "Already loaded": decision.FirstCargoState.ALREADY_LOADED,
    "Procured, not yet loaded": decision.FirstCargoState.PROCUREMENT_COMMITTED_LOADING_REQUIRED,
    "Fully pre-lift": decision.FirstCargoState.FULLY_PRE_LIFT,
}

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


def _decision_waterfall_lines(bd: dict, proc_sunk: bool, loading_sunk: bool) -> tuple[list, float]:
    """Adapts model.waterfall_breakdown()'s $/MMBtu lines/margin to a
    decision-state view. Procurement and loading are always shown as real
    cost bars -- gas was actually bought and/or loaded, and hiding that
    cost reads as a mistake, not a decision-state simplification. Either
    can be sunk independently (decision.FirstCargoState, Section 3a: a
    cargo can be procured but not yet loaded), so this takes two
    independent flags rather than one combined bool -- a single bool would
    silently show the wrong add-back amount for that mixed state. When
    either is sunk, one "Sunk cost add-back" bar is appended that exactly
    cancels the sunk line(s), so the chart foots to the *incremental*
    decision value shown in the metrics above it, not the full-cargo P&L,
    while still showing where that value came from. margin + addback ==
    incremental value / cargo_size, matching decision.route_value()'s
    sunk-cost add-back exactly."""
    addback_names = {n for n, sunk in (("Procurement", proc_sunk), ("Loading", loading_sunk)) if sunk}
    if not addback_names:
        return bd["lines"], bd["margin"]
    addback = sum(v for n, v in bd["lines"] if n in addback_names)
    lines = bd["lines"] + [("Sunk cost add-back", -addback)]
    return lines, bd["margin"] + addback


def _safe_strip(D, tables, params, n_months: int):
    """Try model.strip() at the requested length; on any failure, fall back
    to the well-tested 12-month default instead of crashing the whole page.

    Extending past 12 months depends on the connected workbook actually
    having that many forward columns (and, for FX, the 2Y-10Y tenor
    columns) -- both are properties of *this deployment's* data file, which
    can differ from whatever was used in development. A hard crash on the
    Decision or Forward-strip page is worse than silently-narrower coverage,
    so this always returns something usable and surfaces what happened
    instead of hiding it.
    """
    try:
        return model.strip(D, tables, params, n_months=n_months), None
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any failure here must not crash the page
        return model.strip(D, tables, params, n_months=12), exc


def _fx_warning(strip_df) -> None:
    """Shows the FX extrapolation warning, if any rows need it, naming the
    actual anchor boundary used (1Y for a 12-month strip's fx_curve(), or
    the last real 2Y-10Y anchor for a longer strip's fx_curve_multi()) --
    not a hardcoded "1Y" that would be wrong once strips can run past it."""
    fx_rows = model.fx_extrapolated_rows(strip_df)
    if fx_rows.empty:
        return
    boundary = strip_df.attrs.get("fx_extrap_boundary_months", 12.0)
    boundary_label = "1Y" if boundary <= 12.0 else f"{boundary / 12.0:,.0f}Y"
    labels = ", ".join(fx_rows["month_label"].astype(str))
    st.warning(
        f"FX curve warning: {labels} lie beyond the available {boundary_label} outright and "
        "are linearly extrapolated in the current screening model."
    )


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


def _num_input(label: str, *, container=None, **kwargs):
    """st.number_input that can never hand back None: on some Streamlit
    versions an emptied field returns None mid-edit rather than the
    widget default, and every consumer here does arithmetic on the value
    immediately -- a None reaching model.strip() crashed the deployed
    app with a redacted TypeError once already (the vol-window input got
    a one-off guard then; this is the systematic version, since the same
    class applies to every one of the ~25 inputs below). Falls back to
    the call's own value= default."""
    out = (container or st).number_input(label, **kwargs)
    return kwargs.get("value") if out is None else out


st.sidebar.header("Curve date")
master_desc = list(pd.DatetimeIndex(tables.master_dates).sort_values(ascending=False))
date_labels = [d.strftime("%Y-%m-%d (%a)") for d in master_desc]
sel_label = st.sidebar.selectbox(
    "Date (latest first, master list only)", date_labels, index=0,
    help="Restricted to the master date list (TTF dates >= first complete date of every other table).",
)
D = master_desc[date_labels.index(sel_label)]

if "params" not in st.session_state:
    # Operating case (17 kn / 1.5 d loading / 1.5 d unloading), NOT the
    # frozen legacy spec defaults (19.5 kn / 0 / 5) that Params() itself
    # keeps for the 64/64 regression suite -- see model.operating_default_params.
    st.session_state.params = model.operating_default_params()
p = st.session_state.params

st.sidebar.header("Cost parameters (Step 5)")

with st.sidebar.expander("Vessel schedule"):
    _new_speed = _num_input("Service speed (knots)", value=float(p.vessel_speed_knots),
                            min_value=8.0, max_value=21.0, step=0.5, format="%.1f")
    if abs(_new_speed - p.vessel_speed_knots) > 1e-9:
        # Re-derive everything that was calibrated to the old speed. Fuel
        # rates rescale from the 19.5-kn design constants (cube law), not
        # from their current values, so repeated changes never compound.
        p.vessel_speed_knots = _new_speed
        p.europe_laden_days = p.europe_ballast_days = model.europe_leg_days(_new_speed)
        p.laden_fuel_requirement = model.sea_fuel_at_speed(model.Params.laden_fuel_requirement, _new_speed)
        p.ballast_fuel = model.sea_fuel_at_speed(model.Params.ballast_fuel, _new_speed)
    p.loading_days = _num_input("Loading days (both routes)", value=float(p.loading_days),
                                min_value=0.0, step=0.5)
    st.caption(
        f"Sea legs at {p.vessel_speed_knots:.1f} kn: Europe "
        f"{model.europe_leg_days(p.vessel_speed_knots):.2f} d/leg, Asia "
        f"{model.asia_leg_days(p.vessel_speed_knots):.2f} d/leg (incl. 1 d canal). "
        "Changing speed re-derives leg days and cube-law sea fuel rates together "
        "(slower = longer voyages but ~speed-cubed less fuel/day); each derived "
        "field below stays individually editable afterwards. Legacy spec case: "
        "19.5 kn, 0 d loading, 5 d unloading."
    )

with st.sidebar.expander("Cargo / boil-off"):
    p.cargo_size = _num_input("Cargo size (MMBtu)", value=float(p.cargo_size), min_value=1_000.0,
                              step=50_000.0, format="%.0f")
    p.boil_off_rate = _num_input("Boil-off rate (fraction/day)", value=float(p.boil_off_rate),
                                 min_value=0.0, max_value=0.1, step=0.0001, format="%.4f")

with st.sidebar.expander("Europe route"):
    p.europe_laden_days = _num_input("Europe laden days", value=float(p.europe_laden_days), min_value=0.0, step=1.0)
    p.europe_ballast_days = _num_input("Europe ballast days", value=float(p.europe_ballast_days), min_value=0.0, step=1.0)
    p.europe_port_days = _num_input("Europe unloading days", value=float(p.europe_port_days), min_value=0.0, step=0.5)
    st.caption(f"Europe RT = {p.europe_laden_days + p.europe_ballast_days + p.europe_port_days + p.loading_days:.1f} d "
               "(laden + ballast + unloading + loading)")
    p.loading = _num_input("Loading ($/MMBtu)", value=float(p.loading), min_value=0.0, step=0.01, format="%.2f")
    p.eu_regas_port = _num_input("EU regas + port ($/MMBtu)", value=float(p.eu_regas_port), min_value=0.0,
                                 step=0.01, format="%.2f")
    p.other_cost = _num_input("Other: insurance/LC/brokerage ($/MMBtu)", value=float(p.other_cost),
                              min_value=0.0, step=0.01, format="%.2f")

with st.sidebar.expander("Asia route"):
    # Base/Congestion round trips are DERIVED from the current speed,
    # loading and unloading days -- not the 19.5-kn module constants,
    # which stay pinned for the frozen legacy suite only.
    _det_base = 2.0 * model.asia_leg_days(p.vessel_speed_knots) + p.asia_port_days + p.loading_days
    rt_options = [f"Base ({_det_base:.1f}d)", f"Congestion ({_det_base + 8.0:.1f}d)", "Custom"]
    rt_default = (0 if abs(p.asia_rt_days - _det_base) < 0.01
                  else (1 if abs(p.asia_rt_days - _det_base - 8.0) < 0.01 else 2))
    rt_choice = st.radio("Asia RT", rt_options, index=rt_default, horizontal=True)
    if rt_choice == "Custom":
        p.asia_rt_days = _num_input("Asia RT custom (days)", value=float(p.asia_rt_days), min_value=1.0, step=1.0)
    p.asia_port_days = _num_input("Asia unloading days", value=float(p.asia_port_days), min_value=0.0, step=0.5)
    if rt_choice != "Custom":
        # Re-derive AFTER the unloading input so its edits flow through
        # in the same rerun instead of lagging one.
        _base_rt = 2.0 * model.asia_leg_days(p.vessel_speed_knots) + p.asia_port_days + p.loading_days
        p.asia_rt_days = _base_rt if rt_choice.startswith("Base") else _base_rt + 8.0
    st.caption(f"Symmetric legs (workbook parity): laden = ballast = "
               f"{p.asia_laden_days:.1f} d. Congestion (+8 d) lengthens both legs "
               f"(more boil-off; queue days burn at the lower queue rate in the physical engine).")
    if st.checkbox("Override laden days (model waiting as ballast/idle)", value=False):
        p.asia_laden_days_override = _num_input(
            "Asia laden days (pinned)", value=float(p.asia_laden_days), min_value=0.0, step=1.0)
    else:
        p.asia_laden_days_override = None
    p.asia_port_cost = _num_input("Asia port, DES no regas ($/MMBtu)", value=float(p.asia_port_cost),
                                  min_value=0.0, step=0.01, format="%.2f")
    p.panama_toll_roundtrip = _num_input("Panama toll x2 ($)", value=float(p.panama_toll_roundtrip),
                                         min_value=0.0, step=50_000.0, format="%.0f")

with st.sidebar.expander("Fuel"):
    p.laden_fuel_requirement = _num_input("Laden fuel requirement (t/d, total laden energy demand)",
                                          value=float(p.laden_fuel_requirement), min_value=0.0, step=1.0)
    # Residual laden VLSFO and the natural BOG offset are DERIVED, not
    # set: an earlier version exposed residual_laden_vlsfo (feeding only
    # the legacy ship-cost formula) and laden_fuel_requirement (feeding
    # only the physical engine) as two independent inputs that coincided
    # at defaults -- editing either silently moved just one of the two
    # valuation paths (review finding). One knob now drives both.
    p.residual_laden_vlsfo = model.derived_residual_laden_vlsfo(p)
    _bog_offset = model.natural_bog_offset_t_per_day(p)
    st.caption(
        f"Natural BOG offset (boil-off x cargo): {_bog_offset:.1f} t/d VLSFO-eq -> "
        f"residual laden VLSFO {p.residual_laden_vlsfo:.1f} t/d (derived; feeds the "
        "legacy screening formula, while the Decision page's physical engine uses "
        "the laden requirement and boil-off rate directly -- both move together)."
    )
    p.ballast_fuel = _num_input("Ballast fuel (t/d, used in ship cost)", value=float(p.ballast_fuel),
                                min_value=0.0, step=1.0)
    p.port_fuel_rate = _num_input("Port fuel (t/d, used in ship cost)", value=float(p.port_fuel_rate),
                                  min_value=0.0, step=1.0)
    p.vlsfo_price = _num_input("VLSFO ($/t, static for ALL dates)", value=float(p.vlsfo_price),
                               min_value=0.0, step=5.0)

with st.sidebar.expander("Gas cost chain"):
    p.hh_grossup = _num_input("HH gross-up", value=float(p.hh_grossup), min_value=0.0, step=0.01, format="%.2f")
    p.liquefaction_toll = _num_input("Liquefaction toll ($/MMBtu)", value=float(p.liquefaction_toll),
                                     min_value=0.0, step=0.05, format="%.2f")
    p.pipeline = _num_input("Pipeline ($/MMBtu)", value=float(p.pipeline), min_value=0.0, step=0.01, format="%.2f")

with st.sidebar.expander("EU ETS"):
    p.eua_price = _num_input("EUA price (EUR/t, static, unverified)", value=float(p.eua_price),
                             min_value=0.0, step=5.0)
    # DERIVED, not set (same philosophy as the residual-VLSFO fix): the
    # old hand-set 4,425.9 t constant was calibrated to the 19.5-kn fuel
    # picture and silently went stale whenever speed, fuel rates or day
    # counts changed. Recomputed every run from the physical fuel balance
    # at the legacy uniform-50% scope; the Decision page's physical path
    # applies proper per-segment scope on its own and never reads this.
    p.co2_eu_ets_tonnes = emissions.legacy_uniform_scope_ets_tonnes(p)
    st.caption(
        f"CO2 in legacy ETS scope per EU RT: {p.co2_eu_ets_tonnes:,.1f} t (derived from the "
        "current speed/fuel/day-count settings at uniform 50% scope -- replaces the hand-set "
        "4,425.9 t constant, which was only valid at 19.5 kn)."
    )
    snap_L = model.contract_calendar(D)[0]
    st.caption(f"Phase factor for {snap_L.strftime('%b-%y')} (M1): {model.phase_for_year(snap_L.year)}  "
               "(0 before 2024, 0.4 in 2024, 0.7 in 2025, 1.0 from 2026)")

with st.sidebar.expander("Charter", expanded=True):
    snap_ch = model.snap(tables.charter, D)
    st.caption(f"Snapped charter (174k 2-stroke) at {snap_ch['date'].date()}: ${snap_ch['rate174']:,.0f}/day")
    override_on = st.checkbox("Override charter rate", value=p.charter_override is not None)
    if override_on:
        default_val = p.charter_override if p.charter_override is not None else float(snap_ch["rate174"])
        p.charter_override = _num_input("Charter override ($/day)", value=float(default_val),
                                        min_value=0.0, step=5_000.0, format="%.0f")
    else:
        p.charter_override = None

if st.sidebar.button("Reset to operating defaults (17 kn / 1.5 d)"):
    st.session_state.params = model.operating_default_params()
    st.rerun()

params = st.session_state.params

# Day-count sanity gate (review finding: custom Asia RT + pinned laden
# days can imply a NEGATIVE ballast leg -- the legacy formula silently
# booked it as negative fuel, a phantom credit, and the physical engine
# raised an uncaught ValueError that crashed the Decision page). Fail
# loudly at the source instead of downstream in either engine.
_asia_ballast_implied = (params.asia_rt_days - params.asia_laden_days
                         - params.asia_port_days - params.loading_days)
if _asia_ballast_implied < 0:
    st.sidebar.error(
        f"Asia day-counts are inconsistent: laden ({params.asia_laden_days:.1f}d) + unloading "
        f"({params.asia_port_days:.1f}d) + loading ({params.loading_days:.1f}d) exceed the round "
        f"trip ({params.asia_rt_days:.1f}d) by {-_asia_ballast_implied:.1f}d, so the implied "
        "ballast leg is negative. Fix the Asia route / vessel schedule inputs to continue."
    )
    st.stop()
if params.europe_laden_days + params.europe_ballast_days + params.europe_port_days <= 0:
    st.sidebar.error("Europe round-trip days must be positive. Fix the Europe route inputs to continue.")
    st.stop()

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
    "Caveats (LNG_Diversion_Logic.md v2): voyage days derive from 4,900/9,300 nm at the selected "
    "service speed plus a 1-day canal transit (congestion = +4 waiting days per Asia leg); sea "
    "fuel rates rescale from the 19.5-kn design constants by the cube law; FuelEU, CH4 slip, "
    "heel, demurrage, backhaul are not modelled; VLSFO and EUA are static for all dates including "
    "historical ones; margin/day comparison assumes the vessel is the binding constraint."
)

# ===========================================================================
# Page 1 -- Netback
# ===========================================================================

if PAGE == "0 Decision":
    st.title("LNG cargo and vessel decision")
    strip_df, strip_fallback_error = _safe_strip(D, tables, params, STRIP_MONTHS)
    if strip_fallback_error is not None:
        st.warning(
            f"Could not build a {STRIP_MONTHS}-month strip from the connected workbook "
            f"({type(strip_fallback_error).__name__}: {strip_fallback_error}); showing "
            "the standard 12-month strip instead."
        )
    snap_info = strip_df.attrs["snap"]
    months = list(strip_df["month_label"])
    month_index = st.selectbox(
        "Current cargo load month",
        options=list(range(len(strip_df))),
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
        isolated_first_cargo_state = None
        if decision_mode == decision.DecisionMode.POST_LIFT_DIVERSION:
            state_label = st.radio(
                "Current cargo state", list(FIRST_CARGO_STATE_LABELS),
                horizontal=True, key="isolated_first_cargo_state",
            )
            isolated_first_cargo_state = FIRST_CARGO_STATE_LABELS[state_label]
        try:
            values = decision.isolated_route_values(
                strip_df, params, decision_mode, month_index=month_index,
                first_cargo_state=isolated_first_cargo_state,
            )
        except ValueError as exc:
            # Same containment the programme branch already has: a params
            # combination the physical engine rejects must read as an
            # input problem, not crash the page (review finding).
            st.error(str(exc))
            st.stop()
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
            st.caption({
                decision.FirstCargoState.ALREADY_LOADED:
                    "Procurement and loading are both sunk in incremental value (cargo already "
                    "loaded), but remain in full-cargo P&L.",
                decision.FirstCargoState.PROCUREMENT_COMMITTED_LOADING_REQUIRED:
                    "Procurement is sunk (gas already committed/bought); loading is still "
                    "included since it has not been incurred and remains avoidable.",
                decision.FirstCargoState.FULLY_PRE_LIFT:
                    "Procurement and loading are both included -- treated as fully pre-lift "
                    "despite the post-lift diversion mode.",
            }[isolated_first_cargo_state])

        st.subheader("How the decision value is calculated")
        wf_route = st.radio("Route", [v.route for v in ranked], horizontal=True, key="isolated_wf_route")
        wf_value = next(v for v in ranked if v.route == wf_route)
        wf_proc_sunk = wf_value.procurement_treatment == decision.CostTreatment.SUNK
        wf_loading_sunk = wf_value.loading_treatment == decision.CostTreatment.SUNK
        wf_any_sunk = wf_proc_sunk or wf_loading_sunk
        wf_bd_all = decision.physical_waterfall_breakdown(strip_df.iloc[month_index], params)
        wf_lines, wf_margin = _decision_waterfall_lines(wf_bd_all[wf_route], wf_proc_sunk, wf_loading_sunk)
        st.plotly_chart(
            _plotly_waterfall(
                f"{wf_route} {'post-lift decision' if wf_any_sunk else 'full-cargo'} "
                f"waterfall ({wf_value.load_month.strftime('%b-%y')})",
                wf_bd_all[wf_route]["revenue"], wf_lines, wf_margin,
            ),
            width="stretch",
        )
        wf_addback_names = [n for n, s in (("Procurement", wf_proc_sunk), ("Loading", wf_loading_sunk)) if s]
        st.caption(
            f"$/MMBtu margin x cargo size ({params.cargo_size:,.0f} MMBtu) = "
            f"${wf_margin * params.cargo_size:,.0f}, matching the decision value above "
            "(subject to rounding)." + (
                f" {' and '.join(wf_addback_names)} {'is' if len(wf_addback_names) == 1 else 'are'} "
                "shown as real cost(s) (already incurred), then reversed on the Sunk cost "
                "add-back bar because they were incurred before this decision point -- that is "
                "the incremental view, not the full-cargo P&L."
                if wf_addback_names else ""
            )
        )

    else:
        st.subheader("Discrete one-vessel programme")
        c1, c2, c3 = st.columns(3)
        horizon = _num_input("Programme horizon (days)", container=c1, min_value=1.0, value=52.0, step=1.0)
        max_additional = _num_input("Additional cargoes available", container=c2, min_value=0,
                                    max_value=STRIP_MONTHS - 1, value=1, step=1)
        residual_value = _num_input("Residual vessel value ($/day)", container=c3, value=0.0,
                                    step=10_000.0, format="%.0f")
        # Base/Congested derived from the sidebar's speed + loading/unloading
        # days, not the 19.5-kn module constants (frozen-suite-only now).
        _prog_base_rt = (2.0 * model.asia_leg_days(params.vessel_speed_knots)
                         + params.asia_port_days + params.loading_days)
        asia_case = st.radio(
            "Asia route case for programme",
            ["Use sidebar route", f"Base {_prog_base_rt:.1f} days", f"Congested {_prog_base_rt + 8.0:.1f} days"],
            horizontal=True,
        )
        programme_state_label = st.radio(
            "Current cargo state", list(FIRST_CARGO_STATE_LABELS),
            horizontal=True, key="programme_first_cargo_state",
        )
        programme_first_cargo_state = FIRST_CARGO_STATE_LABELS[programme_state_label]
        programme_params = copy.deepcopy(params)
        if asia_case.startswith("Base"):
            programme_params.asia_rt_days = _prog_base_rt
        elif asia_case.startswith("Congested"):
            programme_params.asia_rt_days = _prog_base_rt + 8.0
        # Rebuild strip because Asia value and laden duration depend on the selected RT.
        programme_strip, programme_fallback_error = _safe_strip(D, tables, programme_params, STRIP_MONTHS)
        if programme_fallback_error is not None:
            st.warning(
                f"Could not build a {STRIP_MONTHS}-month strip from the connected workbook "
                f"({type(programme_fallback_error).__name__}: {programme_fallback_error}); "
                "showing the standard 12-month strip instead. Additional cargoes and horizons "
                "beyond ~12 months may not find a matching forward month."
            )
        try:
            result = decision.optimise_programme(
                programme_strip, programme_params, horizon_days=float(horizon),
                current_month_index=month_index,
                current_mode=decision.DecisionMode.POST_LIFT_DIVERSION,
                current_first_cargo_state=programme_first_cargo_state,
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
                "Cargo values come from the segment-level physical engine "
                "(physical.py/emissions.py, docs/PHASE2_PLAN.md step 8) -- fuel, "
                "boil-off and EU ETS cost are derived per voyage segment rather "
                "than the flat legacy constants. Voyage durations are unchanged "
                "(the engine reproduces the legacy day-count fields exactly)."
            )
            st.caption(
                "Charter and every other running cost are only charged for the "
                f"{best.used_days:,.4f} used_days above -- the "
                f"{best.residual_days:,.4f} residual/idle day(s) are not charged "
                "ongoing hire. If this is a real time charter where hire continues "
                "during idle time, enter a negative Residual vessel value (e.g. "
                f"-\\${snap_info.charter_rate:,.0f}/day, this curve's snapped charter "
                "rate) instead of the default \\$0 credit to model that cost."
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
                    first_cargo_state=(programme_first_cargo_state if leg.cargo_number == 1 else None),
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
            sel_proc_sunk = sel_rv.procurement_treatment == decision.CostTreatment.SUNK
            sel_loading_sunk = sel_rv.loading_treatment == decision.CostTreatment.SUNK
            sel_any_sunk = sel_proc_sunk or sel_loading_sunk
            sel_bd_all = decision.physical_waterfall_breakdown(
                programme_strip.iloc[sel_leg.month_index], programme_params
            )
            sel_lines, sel_margin = _decision_waterfall_lines(sel_bd_all[sel_leg.route], sel_proc_sunk, sel_loading_sunk)
            st.plotly_chart(
                _plotly_waterfall(
                    f"Cargo {sel_leg.cargo_number}: {sel_leg.route} "
                    f"{'post-lift decision' if sel_any_sunk else 'pre-lift'} "
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

            st.subheader(f"Programme value across all {len(programme_strip)} start months")
            show_sweep = st.checkbox(
                f"Re-run the optimiser for every possible start month (up to {len(programme_strip)}x "
                "the work of the single result above)",
                key="programme_sweep_toggle",
            )
            if show_sweep:
                sweep_metric = st.selectbox(
                    "Metric",
                    ["Programme value ($)", "Advantage vs next best ($)", "Used vessel-days"],
                    key="programme_sweep_metric",
                )
                with st.spinner(f"Computing the best programme for each of {len(programme_strip)} start months..."):
                    sweep_rows = []
                    for i in range(len(programme_strip)):
                        row = {
                            "load_month": programme_strip.iloc[i]["load_month"],
                            "month_label": programme_strip.iloc[i]["month_label"],
                        }
                        try:
                            r = decision.optimise_programme(
                                programme_strip, programme_params, horizon_days=float(horizon),
                                current_month_index=i,
                                current_mode=decision.DecisionMode.POST_LIFT_DIVERSION,
                                current_first_cargo_state=programme_first_cargo_state,
                                max_additional_cargoes=int(max_additional),
                                residual_value_per_day=float(residual_value),
                            )
                        except ValueError:
                            row.update(sequence="(infeasible)", programme_value=None,
                                       advantage=None, used_days=None)
                        else:
                            row.update(sequence=r.best.sequence, programme_value=r.best.total_value,
                                       advantage=r.advantage, used_days=r.best.used_days)
                        sweep_rows.append(row)
                sweep_df = pd.DataFrame(sweep_rows)
                metric_col = {
                    "Programme value ($)": "programme_value",
                    "Advantage vs next best ($)": "advantage",
                    "Used vessel-days": "used_days",
                }[sweep_metric]
                st.line_chart(
                    sweep_df.set_index("load_month")[[metric_col]].rename(columns={metric_col: sweep_metric})
                )
                st.dataframe(
                    sweep_df.drop(columns=["load_month"]).rename(columns={
                        "month_label": "Start month", "sequence": "Best sequence",
                        "programme_value": "Programme value", "advantage": "Advantage vs next best",
                        "used_days": "Used vessel-days",
                    }).style.format({
                        "Programme value": "${:,.0f}", "Advantage vs next best": "${:,.0f}",
                        "Used vessel-days": "{:,.4f}",
                    }, na_rep="(infeasible)"),
                    width="stretch", hide_index=True,
                )
                st.caption(
                    "Re-runs the programme optimiser once per possible start month, holding the "
                    "horizon, additional-cargo, residual-value and Asia-case settings above fixed. "
                    "\"(infeasible)\" means no route fits the current cargo within the horizon "
                    "starting that month."
                )

    with st.expander("Physical reconciliation (segment-level detail)"):
        st.caption(
            "Segment-level physical engine (physical.py/emissions.py, docs/PHASE2_PLAN.md). "
            "This is the same engine now valuing the Post-lift/Pre-lift/Vessel-programme "
            "decision modes above (Phase 2 step 8) -- shown here as an independent, "
            "audit-only ledger view for any load month/route you pick, not tied to the "
            "currently selected cargo. Renewal-rate screen mode still uses the legacy "
            "model.strip() formula unchanged (Section 3 of the plan)."
        )
        recon_month_index = st.selectbox(
            "Load month", options=list(range(len(strip_df))),
            format_func=lambda i: f"M{i + 1} = {strip_df.iloc[i]['month_label']}",
            key="recon_month",
        )
        _recon_base_rt = (2.0 * model.asia_leg_days(params.vessel_speed_knots)
                          + params.asia_port_days + params.loading_days)
        recon_route_choice = st.radio(
            "Route",
            ["Europe", f"Asia (base, {_recon_base_rt:.1f} days)",
             f"Asia (congested, {_recon_base_rt + 8.0:.1f} days)"],
            horizontal=True, key="recon_route",
        )
        recon_params = copy.deepcopy(params)
        if recon_route_choice.startswith("Asia"):
            recon_params.asia_rt_days = (
                _recon_base_rt + 8.0 if "congested" in recon_route_choice else _recon_base_rt
            )
        try:
            recon_vessel = physical.vessel_performance_from_params(recon_params)
            recon_segments = (
                physical.europe_route_segments(recon_params) if recon_route_choice == "Europe"
                else physical.asia_route_segments(recon_params)
            )
            recon_ledger = physical.run_voyage(recon_segments, recon_vessel, loaded_mmbtu=recon_params.cargo_size)
            recon_emissions = emissions.voyage_emissions(recon_ledger)
        except ValueError as exc:
            # This expander overrides asia_rt_days to Base/Congested while
            # keeping any pinned laden-days override, so it can produce a
            # day-count combination the sidebar gate never saw (e.g. pinned
            # laden 50d against the Base 46.7d round trip). Contain it here.
            st.error(f"Cannot simulate this route with the current day-count overrides: {exc}")
            st.stop()

        rc1, rc2, rc3, rc4 = st.columns(4)
        rc1.metric("Loaded", f"{recon_ledger.loaded_mmbtu:,.0f} MMBtu")
        rc2.metric("Delivered", f"{recon_ledger.delivered_mmbtu:,.0f} MMBtu")
        rc3.metric("LNG burned", f"{recon_ledger.lng_burned_mmbtu:,.0f} MMBtu")
        rc4.metric("Vented", f"{recon_ledger.vented_mmbtu:,.0f} MMBtu")
        rc5, rc6, rc7, rc8 = st.columns(4)
        rc5.metric("Reliquefied", f"{recon_ledger.reliquefied_mmbtu:,.0f} MMBtu")
        rc6.metric("Heel at discharge", f"{recon_ledger.heel_at_discharge_mmbtu:,.0f} MMBtu")
        rc7.metric("Terminal heel", f"{recon_ledger.terminal_heel_mmbtu:,.0f} MMBtu")
        rc8.metric("Reconciliation error", f"{recon_ledger.reconciliation_error_mmbtu:,.6f} MMBtu")

        ec1, ec2, ec3, ec4 = st.columns(4)
        ec1.metric("CO2", f"{recon_emissions.total_co2_tonnes:,.1f} t")
        ec2.metric("CH4 (slip + vented)", f"{recon_emissions.total_ch4_tonnes:,.2f} t")
        ec3.metric("CO2e", f"{recon_emissions.total_co2e_tonnes:,.1f} t")
        ec4.metric("ETS-covered CO2e", f"{recon_emissions.ets_covered_co2e_tonnes:,.1f} t")
        if recon_emissions.total_ch4_vented_tonnes > 0.01:
            st.warning(
                f"{recon_emissions.total_ch4_vented_tonnes:,.1f} t of raw methane vented -- "
                f"reliquefaction capacity ({recon_vessel.reliq_capacity_mmbtu_per_day:,.0f} MMBtu/day) "
                "was insufficient to absorb the surplus at this route/month."
            )

        st.dataframe(
            pd.DataFrame([
                {
                    "segment": r.segment.name, "state": r.segment.state.value,
                    "days": r.segment.duration_days, "opening_inv": r.opening_inventory_mmbtu,
                    "natural_bog": r.natural_bog_mmbtu, "demand": r.demand_mmbtu,
                    "bog_burned": r.bog_burned_mmbtu, "surplus": r.surplus_mmbtu,
                    "reliquefied": r.reliquefied_mmbtu, "vented": r.vented_mmbtu,
                    "shortfall": r.shortfall_mmbtu, "liquid_fuel_t": r.shortfall_liquid_fuel_tonnes,
                    "closing_inv": r.closing_inventory_mmbtu,
                }
                for r in recon_ledger.segments
            ]).style.format({
                "days": "{:,.4f}", "opening_inv": "{:,.0f}", "natural_bog": "{:,.0f}",
                "demand": "{:,.0f}", "bog_burned": "{:,.0f}", "surplus": "{:,.0f}",
                "reliquefied": "{:,.0f}", "vented": "{:,.0f}", "shortfall": "{:,.0f}",
                "liquid_fuel_t": "{:,.2f}", "closing_inv": "{:,.0f}",
            }),
            width="stretch", hide_index=True,
        )
        st.caption(
            f"Reliquefaction capacity assumed: {recon_vessel.reliq_capacity_mmbtu_per_day:,.0f} MMBtu/day "
            "(physical.DEFAULT_RELIQ_CAPACITY_MMBTU_PER_DAY, an informed estimate, not a vendor spec -- "
            "see docs/PHASE2_PLAN.md Section 10 item 2). Methane slip assumed "
            f"{emissions.DEFAULT_METHANE_SLIP_FRACTION:.1%} of LNG burned (UNCONFIRMED placeholder -- "
            "Section 10 item 5)."
        )

    _fx_warning(strip_df)

elif PAGE == "1 Forward strip":
    st.title("LNG Forward Netback")
    strip_df, strip_fallback_error = _safe_strip(D, tables, params, STRIP_MONTHS)
    if strip_fallback_error is not None:
        st.warning(
            f"Could not build a {STRIP_MONTHS}-month strip from the connected workbook "
            f"({type(strip_fallback_error).__name__}: {strip_fallback_error}); showing "
            "the standard 12-month strip instead."
        )
    snap_info = strip_df.attrs["snap"]
    _fx_warning(strip_df)

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
    st.caption(
        "Value basis: everything on this page (and the Sensitivities/Hedging/VaR pages) uses the "
        "legacy screening formula -- flat fuel rates and the uniform-scope ETS constant. The "
        "Decision page's Post-lift/Pre-lift/Programme values use the segment-level physical "
        "engine instead, so its dollar figures differ slightly by design (about -0.05% for "
        "Europe from the corrected EU ETS berth scope, and about +1.9% for congested Asia from "
        "queue-rate fuel and reliquefaction; docs/PHASE2_PLAN.md step 8 quantifies both)."
    )

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
        # Indexed on load_month (a real Timestamp), not month_label -- a
        # plain "Aug-26" string sorts alphabetically ("Apr-27" < "Aug-26"),
        # which silently scrambles chronological order once the strip runs
        # past 12 months and month names repeat across years.
        chart_df = strip_df.set_index("load_month")[["JKM", "jkm_star"]].rename(
            columns={"JKM": "JKM (L+1)", "jkm_star": "JKM* (breakeven)"})
        st.line_chart(chart_df)
    with col2:
        st.subheader("Margin per vessel-day")
        chart_df2 = strip_df.set_index("load_month")[["eu_day", "asia_day"]].rename(
            columns={"eu_day": "Europe $/day", "asia_day": "Asia $/day"})
        st.bar_chart(chart_df2)

    st.subheader(f"Any line item across all {len(strip_df)} months")
    metric_labels = st.multiselect(
        "Lines to plot",
        options=list(STRIP_METRIC_OPTIONS),
        default=["Europe: Margin ($/MMBtu)", "Asia: Margin ($/MMBtu)"],
    )
    if metric_labels:
        metric_cols = {label: STRIP_METRIC_OPTIONS[label] for label in metric_labels}
        metric_df = strip_df.set_index("load_month")[list(metric_cols.values())]
        metric_df.columns = list(metric_cols.keys())
        st.line_chart(metric_df)
        if len({c.split("(")[-1] for c in metric_labels}) > 1:
            st.caption(
                "Selected lines don't all share the same unit (e.g. $/MMBtu vs total $) -- "
                "one may render as a flat line at this scale."
            )
    else:
        st.caption("Pick at least one line to plot.")

    st.subheader("Intrinsic / extrinsic value (JKM vs TTF diversion option)")
    st.caption(
        "Cargo delivered to TTF (Europe) as the base case; the option is diverting to JKM "
        "(Asia) instead when JKM is higher. Intrinsic = max(JKM - TTF, 0) -- today's forward "
        "view, no uncertainty. Extrinsic = that diversion option's time value (a zero-strike "
        "Margrabe exchange option between JKM and TTF), from the volatility/correlation "
        "source below. FX volatility is not modelled as a separate risk factor "
        "(see spread_option.py)."
    )
    st.caption(
        "This is a pure PRICE spread (strike 0): it does not net the extra shipping, canal and "
        "port cost of actually diverting -- the JKM* breakeven in the table above does. The two "
        "can legitimately disagree: a month can show positive intrinsic here (JKM above TTF) "
        "while the verdict column still says Europe, because JKM hasn't cleared the full "
        "diversion cost. Read this section as the value of destination flexibility in the "
        "price pair, not as a route recommendation."
    )
    vol_source_label = st.radio(
        "Volatility / correlation source",
        ["Historical", "Volatilities tab"],
        horizontal=True, key="vol_source",
    )
    vol_source = "historical" if vol_source_label == "Historical" else "tab"
    window_days = spread_option.DEFAULT_HISTORICAL_WINDOW_DAYS
    if vol_source == "historical":
        # _num_input handles the None-mid-edit case that crashed the
        # deployed app at exactly this call site once already.
        window_days = _num_input(
            "Historical window (calendar days)", min_value=10, max_value=1000,
            value=spread_option.DEFAULT_HISTORICAL_WINDOW_DAYS, step=10, key="vol_window_days",
        )
    elif tables.vol is None:
        st.warning(
            "This workbook has no 'volatilities' sheet -- switch to Historical, or add one "
            "(tenor rows 'spot'/'M+1'/'M+2'/... in column A; 'Volatility TTF'/'Volatility JKM' "
            "and 'Correlation TTF/JKM' headers)."
        )

    try:
        ie_df = spread_option.intrinsic_extrinsic_strip(
            strip_df, tables, D, vol_source, window_days=int(window_days)
        )
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: a bad/unusual workbook must not crash the page
        st.warning(f"Could not compute intrinsic/extrinsic value ({type(exc).__name__}: {exc}); skipping this section.")
        ie_df = None

    if ie_df is not None:
        ie_display = ie_df[["month_label", "vol_jkm", "vol_ttf", "correlation", "intrinsic", "extrinsic"]].copy()
        ie_display.columns = ["Month", "Vol JKM", "Vol TTF", "Correlation", "Intrinsic", "Extrinsic"]
        st.dataframe(
            ie_display.style.format({
                "Vol JKM": "{:.3f}", "Vol TTF": "{:.3f}", "Correlation": "{:.3f}",
                "Intrinsic": "{:.3f}", "Extrinsic": "{:.3f}",
            }, na_rep="N/A"),
            width="stretch", hide_index=True,
        )
        if ie_df["extrinsic"].isna().any():
            st.caption(
                "Extrinsic is N/A for one or more months: the selected source could not supply "
                "complete volatility/correlation inputs for that tenor -- see the detail expander "
                "below for the specific reason."
            )
        ie_chart_df = ie_df.set_index("load_month")[["intrinsic", "extrinsic"]].rename(
            columns={"intrinsic": "Intrinsic", "extrinsic": "Extrinsic"}
        )
        st.line_chart(ie_chart_df)

        with st.expander("How intrinsic/extrinsic is calculated (one month)"):
            ie_mi = st.selectbox(
                "Load month", options=list(range(len(strip_df))),
                format_func=lambda i: f"M{i + 1} = {strip_df.iloc[i]['month_label']}",
                key="ie_detail_month",
            )
            try:
                ie_result = spread_option.month_spread_option(
                    strip_df.iloc[ie_mi], tables, D, vol_source, window_days=int(window_days),
                )
            except Exception as exc:  # noqa: BLE001 -- same rationale as above
                st.warning(f"Could not compute this month's detail ({type(exc).__name__}: {exc}).")
            else:
                vcol1, vcol2, vcol3 = st.columns(3)
                vcol1.metric("Vol JKM", "N/A" if ie_result.vol_jkm is None else f"{ie_result.vol_jkm:.3f}")
                vcol2.metric("Vol TTF", "N/A" if ie_result.vol_ttf is None else f"{ie_result.vol_ttf:.3f}")
                vcol3.metric("Correlation", "N/A" if ie_result.correlation is None else f"{ie_result.correlation:.3f}")
                dcol1, dcol2, dcol3, dcol4 = st.columns(4)
                dcol1.metric("JKM / TTF", f"{ie_result.jkm_0:.3f} / {ie_result.ttf_0:.3f} $/MMBtu")
                dcol2.metric("Intrinsic", f"{ie_result.intrinsic:.3f} $/MMBtu")
                dcol3.metric(
                    "Option value", "N/A" if ie_result.option_value is None else f"{ie_result.option_value:.3f} $/MMBtu"
                )
                dcol4.metric("Extrinsic", "N/A" if ie_result.extrinsic is None else f"{ie_result.extrinsic:.3f} $/MMBtu")
                st.caption(f"Time to expiry: {ie_result.time_to_expiry_years:.3f} years. Source: {ie_result.detail}")

    st.subheader("Waterfall & flows (single load month)")
    wf_mi = st.selectbox("Load month for waterfall/flow view", options=list(range(len(strip_df))),
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
    _sens_base_rt = (2.0 * model.asia_leg_days(params.vessel_speed_knots)
                     + params.asia_port_days + params.loading_days)
    rt_df = risk.asia_rt_breakeven(D, tables, params, month_index=mi,
                                    rt_values=(_sens_base_rt, _sens_base_rt + 4, _sens_base_rt + 8))
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
