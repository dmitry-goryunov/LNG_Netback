"""
risk.py -- Sections 6-8 of the spec: sensitivities, hedging, VaR, stress,
backtest.

No Streamlit import anywhere in this module (mirrors model.py): everything
here is importable and runnable head-less.

R6 increment C (plan sect 6.C.5/C.6) adds the PHYSICAL-basis VaR/stress
entry points (historical_var_physical(), _vectorized_reprice_physical(),
run_stress_tests()'s basis= parameter) alongside the untouched legacy
ones -- see each function's own docstring for the split. `decision` is
imported directly (previously reached only transitively through
cashflows.py) for FirstCargoState typing/defaults; decision.py does not
import risk.py, so this introduces no cycle.
"""

from __future__ import annotations

import calendar
import copy
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import decision
import model
from model import Params, contract_calendar, fx_curve, snap, phase_for_year, _month_add
from cashflows import (
    CargoExposure, CashFlow, RiskFactor,
    legacy_cargo_cashflows, legacy_cargo_quantities,
    physical_cargo_cashflows, physical_cargo_quantities,
)

# ===========================================================================
# Section 6 -- Sensitivities
# ===========================================================================


def _base_context(D, tables, params: Params, month_index: int = 0):
    """Shared snap/contract-calendar/fx setup for one load month, used by
    both the analytic and finite-difference sensitivity paths."""
    D = pd.Timestamp(D)
    hh_row = snap(tables.hh, D)
    ttf_row = snap(tables.ttf, D)
    jkm_row = snap(tables.jkm, D)
    fx_row = snap(tables.fx, D)
    ch_row = snap(tables.charter, D)
    charter = params.charter_override if params.charter_override is not None else float(ch_row["rate174"])
    F, s = contract_calendar(D)
    L = _month_add(F, month_index)
    fxfn = fx_curve(float(fx_row["spot"]), float(fx_row["o6"]), float(fx_row["o1"]), D)
    fx_l = fxfn(L)
    hh_l = float(hh_row[f"c{month_index + 1}"])
    ttf_l = float(ttf_row[f"c{month_index + 1}"])
    jkm_idx = month_index + 2 - s
    jkm_l1 = float(jkm_row[f"c{jkm_idx}"])
    return dict(D=D, L=L, charter=charter, fx_l=fx_l, hh_l=hh_l, ttf_l=ttf_l, jkm_l1=jkm_l1,
                spot=float(fx_row["spot"]), o6=float(fx_row["o6"]), o1=float(fx_row["o1"]))


@dataclass
class SensitivityDelta:
    name: str
    eu_cargo_delta: float
    asia_cargo_delta: float
    note: str = ""


def analytic_deltas(D, tables, params: Params, month_index: int = 0) -> list[SensitivityDelta]:
    """Closed-form partial derivatives x shock size, per Section 6.

    The model is linear in every price input except (a) boil-off x
    destination price -- already absorbed analytically below via the
    (1-boil_off_frac) multipliers -- and (b) ETS x FX, so these are exact
    to machine precision, not first-order approximations.

    R6 increment B: derived from cashflows.legacy_cargo_cashflows()'s
    quantities via CargoExposure.quantity_on(), not an independently
    maintained day-count/fuel formula -- single-factor terms use the
    summed quantity directly (times shock size); product terms (TTF x
    FX) multiply by the co-factor's BASE price. This is the exact
    derivation tests/test_cashflows.py::
    test_deltas_derived_from_quantities_match_analytic_deltas re-verifies
    independently against this function.
    """
    eu, asia = legacy_cargo_cashflows(D, tables, params, month_index)
    base_fx = eu.base_prices[RiskFactor.FX]
    base_ttf = eu.base_prices[RiskFactor.TTF]

    out = []

    d_ttf_eu = eu.quantity_on((RiskFactor.TTF, RiskFactor.FX)) * base_fx
    out.append(SensitivityDelta("TTF +1 EUR/MWh", d_ttf_eu * 1.0, 0.0))

    d_jkm_asia = asia.quantity_on((RiskFactor.JKM,))
    out.append(SensitivityDelta("JKM +0.10 $/MMBtu", 0.0, d_jkm_asia * 0.10))

    d_hh_eu = eu.quantity_on((RiskFactor.HH,))
    d_hh_asia = asia.quantity_on((RiskFactor.HH,))
    out.append(SensitivityDelta("HH +0.10 $/MMBtu", d_hh_eu * 0.10, d_hh_asia * 0.10))

    # Parallel FX shift: bump spot AND the corrected outrights o6/o1 by the
    # same absolute delta (equivalently: bump the *stored* 6M/1Y points by
    # the spot change, then re-apply the x10 correction -- algebraically
    # identical, see fx_curve docstring). fx(L) then shifts by exactly the
    # spot delta for every L, so the effect is a clean, exact partial
    # derivative even though it spans two "raw" columns (spot + points).
    # In cash-flow terms that is the TTF x FX product's co-factor (base
    # TTF) plus the FX-linear ETS leg (EUA price folded into its
    # quantity today -- see cashflows.RiskFactor's docstring).
    d_fx_eu = (eu.quantity_on((RiskFactor.TTF, RiskFactor.FX)) * base_ttf
               + eu.quantity_on((RiskFactor.FX,)))
    out.append(SensitivityDelta("EURUSD +0.01 (parallel)", d_fx_eu * 0.01, 0.0))

    d_charter_eu = eu.quantity_on((RiskFactor.CHARTER,))
    d_charter_asia = asia.quantity_on((RiskFactor.CHARTER,))
    out.append(SensitivityDelta(
        "Charter +$10k/day", d_charter_eu * 10_000, d_charter_asia * 10_000,
        note="margin/day: both -10,000 exactly; JKM* unchanged (structural, Step 7.1)",
    ))

    d_vlsfo_eu = eu.quantity_on((RiskFactor.VLSFO,))
    d_vlsfo_asia = asia.quantity_on((RiskFactor.VLSFO,))
    out.append(SensitivityDelta("VLSFO +$50/t", d_vlsfo_eu * 50, d_vlsfo_asia * 50))

    return out


def finite_difference_deltas(D, tables, params: Params, month_index: int = 0,
                              bumps: Optional[dict] = None) -> list[SensitivityDelta]:
    """Bump-and-reprice cross-check for the same shocks, via model.strip
    (full re-evaluation, no shortcuts). Used to validate analytic_deltas
    to ~1e-6 relative where the model is exactly linear."""
    bumps = bumps or {}
    base = model.strip(D, tables, params).iloc[month_index]

    def bumped(**kw):
        t2 = copy.deepcopy(tables)
        p2 = copy.deepcopy(params)
        if "ttf_add" in kw:
            t2.ttf = t2.ttf.copy()
            row = snap(t2.ttf, D)
            t2.ttf.loc[t2.ttf["date"] == row["date"], f"c{month_index + 1}"] += kw["ttf_add"]
        if "jkm_add" in kw:
            t2.jkm = t2.jkm.copy()
            row = snap(t2.jkm, D)
            F, s = contract_calendar(D)
            jkm_idx = month_index + 2 - s
            t2.jkm.loc[t2.jkm["date"] == row["date"], f"c{jkm_idx}"] += kw["jkm_add"]
        if "hh_add" in kw:
            t2.hh = t2.hh.copy()
            row = snap(t2.hh, D)
            t2.hh.loc[t2.hh["date"] == row["date"], f"c{month_index + 1}"] += kw["hh_add"]
        if "fx_add" in kw:
            t2.fx = t2.fx.copy()
            row = snap(t2.fx, D)
            mask = t2.fx["date"] == row["date"]
            t2.fx.loc[mask, "spot"] += kw["fx_add"]
            t2.fx.loc[mask, "o6"] += kw["fx_add"]
            t2.fx.loc[mask, "o1"] += kw["fx_add"]
        if "charter_add" in kw:
            base_charter = params.charter_override if params.charter_override is not None else float(snap(tables.charter, D)["rate174"])
            p2.charter_override = base_charter + kw["charter_add"]
        if "vlsfo_add" in kw:
            p2.vlsfo_price += kw["vlsfo_add"]
        return model.strip(D, t2, p2).iloc[month_index]

    out = []
    r = bumped(ttf_add=1.0)
    out.append(SensitivityDelta("TTF +1 EUR/MWh", r["eu_cargo"] - base["eu_cargo"], r["asia_cargo"] - base["asia_cargo"]))

    r = bumped(jkm_add=0.10)
    out.append(SensitivityDelta("JKM +0.10 $/MMBtu", r["eu_cargo"] - base["eu_cargo"], r["asia_cargo"] - base["asia_cargo"]))

    r = bumped(hh_add=0.10)
    out.append(SensitivityDelta("HH +0.10 $/MMBtu", r["eu_cargo"] - base["eu_cargo"], r["asia_cargo"] - base["asia_cargo"]))

    r = bumped(fx_add=0.01)
    out.append(SensitivityDelta("EURUSD +0.01 (parallel)", r["eu_cargo"] - base["eu_cargo"], r["asia_cargo"] - base["asia_cargo"]))

    r = bumped(charter_add=10_000)
    out.append(SensitivityDelta("Charter +$10k/day", r["eu_cargo"] - base["eu_cargo"], r["asia_cargo"] - base["asia_cargo"]))

    r = bumped(vlsfo_add=50.0)
    out.append(SensitivityDelta("VLSFO +$50/t", r["eu_cargo"] - base["eu_cargo"], r["asia_cargo"] - base["asia_cargo"]))

    return out


def asia_rt_breakeven(D, tables, params: Params, month_index: int = 0,
                       rt_values=(model.ASIA_RT_BASE, model.ASIA_RT_CONG)) -> pd.DataFrame:
    """JKM* recomputed at each Asia RT value (direct exact re-evaluation,
    not a derivative -- Section 6's 'Asia RT 47 -> 55' row). Symmetric-legs
    convention: laden = (RT - port)/2 derives inside model.Params."""
    rows = []
    for rt in rt_values:
        p2 = copy.deepcopy(params)
        p2.asia_rt_days = rt
        row = model.strip(D, tables, p2).iloc[month_index]
        rows.append(dict(asia_rt_days=rt, jkm_star=row["jkm_star"], asia_day=row["asia_day"],
                          gap=row["gap"], verdict=row["verdict"]))
    return pd.DataFrame(rows)


def tornado_data(D, tables, params: Params, month_index: int = 0) -> pd.DataFrame:
    """Long-form table for a tornado chart: one row per shock x basin."""
    deltas = analytic_deltas(D, tables, params, month_index)
    rows = []
    for d in deltas:
        if d.eu_cargo_delta != 0:
            rows.append(dict(shock=d.name, basin="Europe", cargo_pnl=d.eu_cargo_delta, note=d.note))
        if d.asia_cargo_delta != 0:
            rows.append(dict(shock=d.name, basin="Asia", cargo_pnl=d.asia_cargo_delta, note=d.note))
    return pd.DataFrame(rows)


def ttf_x_asiart_grid(D, tables, params: Params, month_index: int = 0,
                       ttf_shocks=None, rt_values=None) -> pd.DataFrame:
    """Breakeven grid: JKM gap (JKM - JKM*) across a TTF-shock x Asia-RT
    grid, holding everything else at base."""
    ttf_shocks = np.array(ttf_shocks if ttf_shocks is not None else [-10, -5, -2, 0, 2, 5, 10], dtype=float)
    rt_values = np.array(rt_values if rt_values is not None else
                          [model.ASIA_RT_BASE, model.ASIA_RT_BASE + 4, model.ASIA_RT_CONG], dtype=float)

    base_ttf_row_date = snap(tables.ttf, D)["date"]
    records = []
    for rt in rt_values:
        for shock in ttf_shocks:
            t2 = copy.deepcopy(tables)
            t2.ttf = t2.ttf.copy()
            t2.ttf.loc[t2.ttf["date"] == base_ttf_row_date, f"c{month_index + 1}"] += shock
            p2 = copy.deepcopy(params)
            p2.asia_rt_days = rt
            row = model.strip(D, t2, p2).iloc[month_index]
            records.append(dict(ttf_shock=shock, asia_rt_days=rt, gap=row["gap"], verdict=row["verdict"],
                                 jkm_star=row["jkm_star"]))
    return pd.DataFrame(records)


def scenario_presets() -> dict:
    """Named what-if presets for the Sensitivities page (Section 6)."""
    return {
        "Base case": dict(),
        "Panama congestion (Asia RT 54.7d)": dict(asia_rt_days=model.ASIA_RT_CONG),
        "Hormuz shock (JKM strip +2.0, VLSFO +80)": dict(jkm_shift=2.0, vlsfo_price_add=80.0),
        "2022 replay (load 2022-08-26 curves)": dict(curve_date=pd.Timestamp("2022-08-26")),
    }


def apply_preset(D, tables, params: Params, preset: dict):
    """Returns (D_used, params_used, jkm_shift) ready to feed into
    model.strip; jkm_shift (if any) must be applied to the JKM table."""
    D_used = preset.get("curve_date", D)
    p2 = copy.deepcopy(params)
    if "asia_rt_days" in preset:
        p2.asia_rt_days = preset["asia_rt_days"]
    if "vlsfo_price_add" in preset:
        p2.vlsfo_price += preset["vlsfo_price_add"]
    t2 = tables
    if "jkm_shift" in preset:
        t2 = copy.deepcopy(tables)
        t2.jkm = t2.jkm.copy()
        jkm_cols = [c for c in t2.jkm.columns if c.startswith("c")]
        row_date = snap(t2.jkm, D_used)["date"]
        t2.jkm.loc[t2.jkm["date"] == row_date, jkm_cols] += preset["jkm_shift"]
    return D_used, t2, p2


# ===========================================================================
# Section 7 -- Hedging
# ===========================================================================

# Contract sizes as understood at spec-write time -- VERIFY AGAINST CURRENT
# EXCHANGE SPECS BEFORE GO-LIVE (spec Section 7 explicit instruction: "do
# not hardcode silently"). TTF's lot is variable (1 MW x hours in the
# delivery month), everything else is a fixed MMBtu lot.
CONTRACT_SPECS = {
    "NYMEX Henry Hub (NG)": {"unit": "MMBtu/lot", "size": 10_000, "verified": False},
    "ICE JKM": {"unit": "MMBtu/lot", "size": 10_000, "verified": False},
    "ICE TTF": {"unit": "MWh/lot", "size": "1 MW x hours in delivery month (~720-744 MWh/lot)", "verified": False},
    "EURUSD": {"unit": "OTC forward, notional EUR", "size": None, "verified": False},
}


def hours_in_month(L: pd.Timestamp) -> int:
    return calendar.monthrange(L.year, L.month)[1] * 24


def europe_hedge_legs(D, tables, params: Params, month_index: int = 0) -> pd.DataFrame:
    """Section 7 Europe-committed cargo hedge legs, for the given load
    month L."""
    df = model.strip(D, tables, params)
    row = df.iloc[month_index]
    snap_info = df.attrs["snap"]
    L = row["load_month"]
    cargo = params.cargo_size
    eu_laden = params.europe_laden_days

    delivered_mmbtu = cargo * (1 - params.boil_off_rate * eu_laden)
    delivered_mwh = delivered_mmbtu / 3.412
    ttf_lots = delivered_mwh / hours_in_month(L)

    eur_notional = row["TTF"] * delivered_mwh
    ng_mmbtu = cargo * params.hh_grossup
    ng_lots = ng_mmbtu / CONTRACT_SPECS["NYMEX Henry Hub (NG)"]["size"]

    vlsfo_laden = params.residual_laden_vlsfo * eu_laden
    vlsfo_ballast = params.ballast_fuel * params.europe_ballast_days
    vlsfo_port = params.port_fuel_rate * (params.europe_port_days + params.loading_days)
    eua_tonnes = params.co2_eu_ets_tonnes * phase_for_year(L.year)

    legs = [
        dict(leg="Short TTF future", month=row["month_label"], volume=delivered_mwh, unit="MWh",
             lots=ttf_lots, lot_unit="TTF lots (1MW x hrs)", required=True),
        dict(leg="Short EUR forward", month=row["month_label"], volume=eur_notional, unit="EUR notional",
             lots=None, lot_unit="OTC", required=True),
        dict(leg="Long NG future (gross-up 1.15x)", month=row["month_label"], volume=ng_mmbtu, unit="MMBtu",
             lots=ng_lots, lot_unit="NG lots", required=True),
        dict(leg="VLSFO swap (optional)", month=row["month_label"], volume=vlsfo_laden + vlsfo_ballast + vlsfo_port,
             unit="t", lots=None, lot_unit=f"laden {vlsfo_laden:.0f}t + ballast {vlsfo_ballast:.0f}t + port {vlsfo_port:.0f}t", required=False),
        dict(leg="EUA futures (optional)", month=row["month_label"], volume=eua_tonnes, unit="t CO2",
             lots=None, lot_unit="OTC/ICE EUA", required=False),
        dict(leg="Freight FFA (no liquid contract)", month=row["month_label"], volume=None, unit="-",
             lots=None, lot_unit="Spark/Baltic OTC only", required=False),
    ]
    out = pd.DataFrame(legs)
    out.attrs["snap"] = snap_info
    out.attrs["fx_L"] = row["fx"]
    out.attrs["ttf_L"] = row["TTF"]
    out.attrs["hh_L"] = row["HH"]
    return out


def asia_hedge_legs(D, tables, params: Params, month_index: int = 0) -> pd.DataFrame:
    """Section 7 Asia-committed cargo hedge legs, for the given load
    month L (delivery L+1)."""
    df = model.strip(D, tables, params)
    row = df.iloc[month_index]
    snap_info = df.attrs["snap"]
    cargo = params.cargo_size
    asia_laden = params.asia_laden_days

    delivered_mmbtu = cargo * (1 - params.boil_off_rate * asia_laden)
    jkm_lots = delivered_mmbtu / CONTRACT_SPECS["ICE JKM"]["size"]
    ng_mmbtu = cargo * params.hh_grossup
    ng_lots = ng_mmbtu / CONTRACT_SPECS["NYMEX Henry Hub (NG)"]["size"]

    asia_ballast = params.asia_rt_days - asia_laden - params.asia_port_days - params.loading_days
    vlsfo_laden = params.residual_laden_vlsfo * asia_laden
    vlsfo_ballast = params.ballast_fuel * asia_ballast
    vlsfo_port = params.port_fuel_rate * (params.asia_port_days + params.loading_days)

    legs = [
        dict(leg="Short JKM future (month L+1)", month=row["month_label"], volume=delivered_mmbtu, unit="MMBtu",
             lots=jkm_lots, lot_unit="JKM lots", required=True),
        dict(leg="Long NG future (gross-up 1.15x)", month=row["month_label"], volume=ng_mmbtu, unit="MMBtu",
             lots=ng_lots, lot_unit="NG lots", required=True),
        dict(leg="VLSFO swap (optional, larger fuel leg)", month=row["month_label"],
             volume=vlsfo_laden + vlsfo_ballast + vlsfo_port, unit="t", lots=None,
             lot_unit=f"laden {vlsfo_laden:.0f}t + ballast {vlsfo_ballast:.0f}t + port {vlsfo_port:.0f}t", required=False),
        dict(leg="No FX leg (JKM is USD)", month=row["month_label"], volume=None, unit="-", lots=None,
             lot_unit="n/a", required=False),
    ]
    out = pd.DataFrame(legs)
    out.attrs["snap"] = snap_info
    out.attrs["jkm_L1"] = row["JKM"]
    out.attrs["hh_L"] = row["HH"]
    return out


def eu_hedge_pnl_vector(scen_ttf_L, scen_hh_L, scen_fx_L, base_ttf_L, base_hh_L, base_fx_L,
                         cargo, hh_grossup, boil_off_rate, eu_laden_days) -> np.ndarray:
    """Vectorised USD P&L of the Europe mechanical hedge (short TTF + short
    EUR fwd + long NG), Section 7.

    The short-TTF leg's EUR P&L is converted to USD at the *base* FX rate
    (not the scenario rate) -- validated against the Section 7 fixture
    (M1 EU cargo hedged HS-VaR95/99/sd): converting at the scenario FX
    rate instead reproduces a residual an order of magnitude too small,
    confirming base-FX conversion is what the spec's fixture assumes.
    """
    V = cargo * (1 - boil_off_rate * eu_laden_days) / 3.412
    eur_notional = base_ttf_L * V
    ng_notional = cargo * hh_grossup

    d_ttf = scen_ttf_L - base_ttf_L
    d_hh = scen_hh_L - base_hh_L
    d_fx = scen_fx_L - base_fx_L

    hedge_ttf = -V * d_ttf * base_fx_L
    hedge_fx = -eur_notional * d_fx
    hedge_ng = ng_notional * d_hh
    return hedge_ttf + hedge_fx + hedge_ng


def asia_hedge_pnl_vector(scen_jkm_L1, scen_hh_L, base_jkm_L1, base_hh_L,
                           cargo, hh_grossup, boil_off_rate, asia_laden_days) -> np.ndarray:
    """USD P&L of the Asia mechanical hedge (short JKM + long NG). No FX
    leg -- JKM is USD-denominated, so (unlike Europe) there is no
    bilinear cross term and this hedge is exact to machine precision."""
    delivered = cargo * (1 - boil_off_rate * asia_laden_days)
    ng_notional = cargo * hh_grossup
    d_jkm = scen_jkm_L1 - base_jkm_L1
    d_hh = scen_hh_L - base_hh_L
    hedge_jkm = -delivered * d_jkm
    hedge_ng = ng_notional * d_hh
    return hedge_jkm + hedge_ng


# ===========================================================================
# Section 8 -- Historical simulation VaR
# ===========================================================================

N_STRIP_COLS = 13


@dataclass
class ScenarioSet:
    dates: list
    hh_ret: np.ndarray     # (n, 13)
    ttf_ret: np.ndarray    # (n, 13)
    jkm_ret: np.ndarray    # (n, 13)
    fx_ret: np.ndarray     # (n,)
    end_date: pd.Timestamp
    lookback: int
    method: str


def _intersection_dates(tables) -> list:
    return sorted(set(tables.hh["date"]) & set(tables.ttf["date"]) & set(tables.jkm["date"]) & set(tables.fx["date"]))


def build_scenarios(tables, D, lookback: int = 500, method: str = "naive") -> ScenarioSet:
    """Section 8.1: joint daily log-returns of NG/TTF/JKM (c1..c13) and FX
    spot, on the intersection of dates across the four tables.

    method="naive": plain same-index-column continuation returns (what
    the spec's fixture numbers use).
    method="roll_aligned": on an NG/TTF month-boundary or a JKM 15th/16th
    boundary, compare today's contract k against *yesterday's* contract
    k+1 (same underlying delivery month) instead of yesterday's contract
    k. Toggle-able, default OFF, not used by the gated fixtures (Section
    8's own text: implement this after reproducing the naive numbers).
    """
    D = pd.Timestamp(D)
    inter = _intersection_dates(tables)
    end_idx = max(i for i, d in enumerate(inter) if d <= D)
    start_idx = end_idx - lookback
    if start_idx < 0:
        raise ValueError(f"only {end_idx} intersection dates available before {D.date()}, need {lookback}")
    scen_dates = inter[start_idx:end_idx + 1]

    n_cols = N_STRIP_COLS + (1 if method == "roll_aligned" else 0)
    hh_cols = [f"c{i}" for i in range(1, n_cols + 1)]
    hh_px = tables.hh.set_index("date").loc[scen_dates, hh_cols].to_numpy(dtype=float)
    ttf_px = tables.ttf.set_index("date").loc[scen_dates, hh_cols].to_numpy(dtype=float)
    jkm_px = tables.jkm.set_index("date").loc[scen_dates, hh_cols].to_numpy(dtype=float)
    fx_px = tables.fx.set_index("date").loc[scen_dates, "spot"].to_numpy(dtype=float)

    # Roll alignment requires one additional continuation (c14) so that
    # today's c13 can be compared with yesterday's c14 on a roll date.
    # The source workbook has known historical gaps in JKM c14.  Failing
    # explicitly is safer than propagating NaNs into VaR or silently dropping
    # scenarios. Legacy naive fixtures are unaffected.
    if method == "roll_aligned":
        missing = []
        for market, values in (("HH", hh_px), ("TTF", ttf_px), ("JKM", jkm_px)):
            bad_rows = np.flatnonzero(~np.isfinite(values).all(axis=1))
            if len(bad_rows):
                sample = ", ".join(str(pd.Timestamp(scen_dates[i]).date()) for i in bad_rows[:5])
                missing.append(f"{market}: {len(bad_rows)} row(s), e.g. {sample}")
        if missing:
            raise ValueError(
                "roll-aligned scenarios require complete c1..c14 history; "
                + "; ".join(missing)
            )

    if method == "naive":
        hh_ret = np.diff(np.log(hh_px), axis=0)
        ttf_ret = np.diff(np.log(ttf_px), axis=0)
        jkm_ret = np.diff(np.log(jkm_px), axis=0)
    elif method == "roll_aligned":
        hh_ret = _roll_aligned_returns(hh_px, scen_dates, _is_month_roll)
        ttf_ret = _roll_aligned_returns(ttf_px, scen_dates, _is_month_roll)
        jkm_ret = _roll_aligned_returns(jkm_px, scen_dates, _is_jkm_roll)
    else:
        raise ValueError(f"unknown method {method!r}")

    fx_ret = np.diff(np.log(fx_px))

    return ScenarioSet(dates=scen_dates[1:], hh_ret=hh_ret, ttf_ret=ttf_ret, jkm_ret=jkm_ret,
                        fx_ret=fx_ret, end_date=scen_dates[-1], lookback=lookback, method=method)


def _is_month_roll(d_prev, d) -> bool:
    return d.month != d_prev.month or d.year != d_prev.year


def _is_jkm_roll(d_prev, d) -> bool:
    return _is_month_roll(d_prev, d) or (d_prev.day <= 15 < d.day)


def _roll_aligned_returns(px: np.ndarray, dates: list, roll_fn) -> np.ndarray:
    """px has N_STRIP_COLS+1 columns; returns array is (len(dates)-1,
    N_STRIP_COLS). On a roll date, index k's return compares today's c_k
    against yesterday's c_{k+1} (Section 8 'Required refinement')."""
    n = px.shape[0] - 1
    k = N_STRIP_COLS
    out = np.empty((n, k))
    for i in range(n):
        d_prev, d = dates[i], dates[i + 1]
        today = px[i + 1, :k]
        if roll_fn(d_prev, d):
            yesterday = px[i, 1:k + 1]   # shifted: c_{k+1} of yesterday aligns with c_k of today
        else:
            yesterday = px[i, :k]
        out[i, :] = np.log(today / yesterday)
    return out


def _assert_finite_quantities(cash_flows: list[CashFlow], label: str) -> None:
    """R6 increment B minimal input-sanity guard (plan sect 4, B.3): fail
    loud rather than let a non-finite Params-derived quantity silently
    propagate into VaR. Cheap (12 numbers) -- not full R2 validation
    (reconciliation, sign checks, cross-field consistency), which stays
    unclaimed by this release."""
    q = np.array([cf.quantity for cf in cash_flows], dtype=float)
    if not np.isfinite(q).all():
        bad = [cf.label for cf, finite in zip(cash_flows, np.isfinite(q)) if not finite]
        raise ValueError(f"non-finite cash-flow quantity in {label}: {bad}")


def _assert_finite_prices(prices: dict, label: str) -> None:
    """Same boundary guard as _assert_finite_quantities, for the scenario
    price arrays fed into CargoExposure.value_matrix() -- vectorised
    (one isfinite pass per factor array), so cheap at backtest scale."""
    for factor, arr in prices.items():
        a = np.asarray(arr, dtype=float)
        if not np.isfinite(a).all():
            raise ValueError(f"non-finite {factor.value} price(s) in {label}")


def _prepare_scenario_price_arrays(scen: ScenarioSet, D, base_hh, base_ttf, base_jkm,
                                    base_spot, base_o6, base_o1) -> dict:
    """Scenario PRICE PREPARATION shared by _vectorized_reprice() (legacy
    basis) and _vectorized_reprice_physical() (physical basis, R6
    increment C.5): exp-of-log-returns, the FX curve interpolation
    producing fx_l, the months/years/phases arrays, and the JKM column
    mapping producing jkm_L1. Plan sect 2's boundary keeps ALL of this
    here, never in the cash-flow layer, which only ever consumes
    already-prepared price matrices.

    Extracted verbatim out of _vectorized_reprice() (unchanged from
    increment B) so the physical variant reuses the SAME price
    preparation instead of a second, independently-drifting copy --
    increment C.5's explicit instruction: "reuse _vectorized_reprice's
    price-preparation output against physical quantities". Byte-identical
    arithmetic to before the extraction; covered by the same tests that
    already pinned _vectorized_reprice() (frozen 64/64, Gate 4 VaR
    fixtures, the batched-vs-scalar cross-check in
    tests/test_risk_containment.py)."""
    D = pd.Timestamp(D)
    F, s = contract_calendar(D)
    n = scen.hh_ret.shape[0]

    hh_scen = base_hh[None, :] * np.exp(scen.hh_ret)          # (n,13)
    ttf_scen = base_ttf[None, :] * np.exp(scen.ttf_ret)       # (n,13)
    jkm_scen = base_jkm[None, :] * np.exp(scen.jkm_ret)       # (n,13)
    spot_scen = base_spot * np.exp(scen.fx_ret)                # (n,)
    dspot = spot_scen - base_spot
    o6_scen = base_o6 + dspot
    o1_scen = base_o1 + dspot

    months = model.load_months(F, 12)
    mids = np.array([(pd.Timestamp(year=m.year, month=m.month, day=15) - D).days / 30.44 for m in months])
    years = np.array([m.year for m in months])
    phases = np.array([phase_for_year(y) for y in years])

    # fx(L) per scenario x month: (n,12)
    t_col = mids[None, :]
    spot_col = spot_scen[:, None]
    o6_col = o6_scen[:, None]
    o1_col = o1_scen[:, None]
    fx_near = spot_col + (o6_col - spot_col) * (t_col / 6.0)
    fx_far = o6_col + (o1_col - o6_col) * ((t_col - 6.0) / 6.0)
    fx_l = np.where(t_col <= 6.0, fx_near, fx_far)   # (n,12)

    # HH(L), TTF(L) for month i use column i; JKM(L+1) uses column i+2-s-1 (0-based)
    hh_L = hh_scen[:, 0:12] if hh_scen.shape[1] >= 12 else np.pad(hh_scen, ((0, 0), (0, 12 - hh_scen.shape[1])))
    ttf_L = ttf_scen[:, 0:12] if ttf_scen.shape[1] >= 12 else np.pad(ttf_scen, ((0, 0), (0, 12 - ttf_scen.shape[1])))
    jkm_cols_idx = np.array([i + 1 - s for i in range(12)])  # 0-based
    jkm_L1 = jkm_scen[:, jkm_cols_idx]

    return dict(n=n, years=years, phases=phases, hh_L=hh_L, ttf_L=ttf_L, jkm_L1=jkm_L1, fx_l=fx_l)


def _vectorized_reprice(scen: ScenarioSet, D, base_hh, base_ttf, base_jkm, base_spot, base_o6, base_o1,
                         charter, params: Params) -> dict:
    """Numpy-vectorised re-implementation of model.strip's Step 6 maths,
    batched over all scenarios x all 12 load months at once. Kept
    numerically identical to model.strip for both frozen legacy and current
    operating defaults (cross-checked by zero-shock tests) -- exists purely for VaR/backtest
    performance, since a pure-Python model.strip call per scenario would
    be ~500x (or, for the backtest tab, ~50,000x) slower.

    R6 increment B: route valuation (eu_cargo/asia_cargo) is delegated to
    cashflows.legacy_cargo_quantities()/CargoExposure.value_matrix() --
    the same per-factor decomposition legacy_cargo_cashflows() is pinned
    against model.strip() in tests/test_cashflows.py -- instead of a
    third independent copy of the eu_ship/as_ship/margin arithmetic.
    Scenario PRICE PREPARATION is UNCHANGED -- plan sect 2's boundary
    keeps that here, not in the cash-flow layer, which only consumes
    already-prepared price matrices.

    R6 increment C.5: the price-preparation block now lives in the shared
    _prepare_scenario_price_arrays() helper (also used by
    _vectorized_reprice_physical()) -- this function's OWN signature,
    behaviour and every returned number are unchanged by that extraction
    (verified: frozen 64/64, Gate 4 VaR fixtures, and the batched-vs-
    scalar cross-check all stayed green with no tolerance change).
    """
    prep = _prepare_scenario_price_arrays(scen, D, base_hh, base_ttf, base_jkm, base_spot, base_o6, base_o1)
    n, years = prep["n"], prep["years"]
    hh_L, ttf_L, jkm_L1, fx_l = prep["hh_L"], prep["ttf_L"], prep["jkm_L1"], prep["fx_l"]

    cargo = params.cargo_size
    asia_rt = params.asia_rt_days
    asia_bo = params.boil_off_rate * params.asia_laden_days

    # --- Route valuation (R6 increment B): the proc/ttf_usd/eu_ship/
    # as_ship/ets/eu_margin/asia_margin arithmetic that used to live here
    # is GONE -- eu_cargo/asia_cargo now come from evaluating the same
    # per-factor CashFlow quantities legacy_cargo_cashflows() decomposes
    # model.strip()'s Step 6 into (tests/test_cashflows.py group b pins
    # that decomposition against model.strip() to ~1e-8). `phases` above
    # is computed but not consumed here: phase_for_year() is applied
    # INSIDE legacy_cargo_quantities() itself, keyed off each month's
    # calendar year, folded into the ETS cash flow's quantity -- kept
    # computed above anyway because the scenario-preparation block it
    # lives in is not restructured by this increment (plan sect 2).
    #
    # Quantities are Params-only (plan sect 2), so they are built ONCE
    # per call -- 12 months x 2 routes -- not once per scenario; the
    # n-scenario cost is confined to value_matrix's array arithmetic.
    charter_arr = np.full(n, charter, dtype=float)
    vlsfo_arr = np.full(n, params.vlsfo_price, dtype=float)

    eu_cargo = np.empty((n, 12), dtype=float)
    asia_cargo = np.empty((n, 12), dtype=float)
    europe_rt = np.empty(12, dtype=float)

    for i in range(12):
        eu_flows, asia_flows = legacy_cargo_quantities(params, int(years[i]))
        _assert_finite_quantities(eu_flows, f"Europe month {i} cash-flow quantities")
        _assert_finite_quantities(asia_flows, f"Asia month {i} cash-flow quantities")

        eu_exposure = CargoExposure(route="Europe", month_index=i, cash_flows=eu_flows)
        asia_exposure = CargoExposure(route="Asia", month_index=i, cash_flows=asia_flows)

        eu_prices = {
            RiskFactor.TTF: ttf_L[:, i],
            RiskFactor.FX: fx_l[:, i],
            RiskFactor.HH: hh_L[:, i],
            RiskFactor.CHARTER: charter_arr,
            RiskFactor.VLSFO: vlsfo_arr,
        }
        asia_prices = {
            RiskFactor.JKM: jkm_L1[:, i],
            RiskFactor.HH: hh_L[:, i],
            RiskFactor.CHARTER: charter_arr,
            RiskFactor.VLSFO: vlsfo_arr,
        }
        _assert_finite_prices(eu_prices, f"Europe month {i} scenario prices")
        _assert_finite_prices(asia_prices, f"Asia month {i} scenario prices")

        eu_cargo[:, i] = eu_exposure.value_matrix(eu_prices)
        asia_cargo[:, i] = asia_exposure.value_matrix(asia_prices)
        # The CHARTER quantity IS -europe_rt (single source of truth,
        # plan sect 6.B) -- constant across months since the charter
        # cash flow has no phase/year dependence -- recovered here
        # rather than re-deriving eu_laden+eu_ballast+eu_port+load_days
        # independently a second time.
        europe_rt[i] = -eu_exposure.quantity_on((RiskFactor.CHARTER,))

    eu_day = eu_cargo / europe_rt[None, :]

    # asia_cost_exbo isn't a single factor's quantity (it's strip's
    # bundled ex-boil-off cost: proc + loading + as_ship + asia_port_cost
    # + other_cost), so the cash-flow layer doesn't expose it directly.
    # Exact algebraic rearrangement of strip's asia_margin = jkm_L1 *
    # (1 - asia_bo) - asia_cost_exbo, asia_cargo = asia_margin * cargo
    # (see legacy_cargo_quantities docstring for the forward derivation)
    # recovers it from the already-evaluated asia_cargo instead of a
    # third independent copy of the proc/as_ship formula.
    asia_cost_exbo = jkm_L1 * (1 - asia_bo) - asia_cargo / cargo
    jkm_star = (eu_day * asia_rt / cargo + asia_cost_exbo) / (1 - asia_bo)
    verdict_asia = jkm_L1 >= jkm_star

    return dict(eu_cargo=eu_cargo, asia_cargo=asia_cargo, jkm_star=jkm_star, jkm_L1=jkm_L1,
                verdict_asia=verdict_asia, hh_L=hh_L, ttf_L=ttf_L, fx_l=fx_l)


def _vectorized_reprice_physical(scen: ScenarioSet, D, base_hh, base_ttf, base_jkm, base_spot, base_o6, base_o1,
                                  charter, params: Params,
                                  first_cargo_state: Optional[decision.FirstCargoState] = None) -> dict:
    """Physical-basis counterpart of _vectorized_reprice() (R6 increment
    C.5, plan sect 6.C.5): identical scenario PRICE PREPARATION
    (delegated to the SAME _prepare_scenario_price_arrays() helper --
    "reuse _vectorized_reprice's price-preparation output against
    physical quantities" is the plan's own wording), but each month's
    exposure comes from cashflows.physical_cargo_quantities() instead of
    legacy_cargo_quantities(), so first_cargo_state's sunk-cost zeroing
    (C.2) is live and fuel/delivered/EUA quantities are the physical
    engine's real per-segment mass balance rather than the flat legacy
    formula. _vectorized_reprice() itself is untouched by this function's
    existence -- same signature, same behaviour (frozen 64/64 green).

    Returns only eu_cargo/asia_cargo/hh_L/ttf_L/jkm_L1/fx_l -- no
    jkm_star/verdict_asia: 12-cargo verdict-switching is a LEGACY-basis-
    only concept (plan sect 8.3, "12cargo stays legacy-basis-only");
    physical basis supports single/spread only for now, neither of which
    needs a verdict."""
    prep = _prepare_scenario_price_arrays(scen, D, base_hh, base_ttf, base_jkm, base_spot, base_o6, base_o1)
    n, years = prep["n"], prep["years"]
    hh_L, ttf_L, jkm_L1, fx_l = prep["hh_L"], prep["ttf_L"], prep["jkm_L1"], prep["fx_l"]

    charter_arr = np.full(n, charter, dtype=float)
    vlsfo_arr = np.full(n, params.vlsfo_price, dtype=float)

    eu_cargo = np.empty((n, 12), dtype=float)
    asia_cargo = np.empty((n, 12), dtype=float)

    for i in range(12):
        eu_flows = physical_cargo_quantities(params, "Europe", int(years[i]), first_cargo_state=first_cargo_state)
        asia_flows = physical_cargo_quantities(params, "Asia", int(years[i]), first_cargo_state=first_cargo_state)
        _assert_finite_quantities(eu_flows, f"Europe month {i} physical cash-flow quantities")
        _assert_finite_quantities(asia_flows, f"Asia month {i} physical cash-flow quantities")

        eu_exposure = CargoExposure(route="Europe", month_index=i, cash_flows=eu_flows)
        asia_exposure = CargoExposure(route="Asia", month_index=i, cash_flows=asia_flows)

        eu_prices = {
            RiskFactor.TTF: ttf_L[:, i],
            RiskFactor.FX: fx_l[:, i],
            RiskFactor.HH: hh_L[:, i],
            RiskFactor.CHARTER: charter_arr,
            RiskFactor.VLSFO: vlsfo_arr,
        }
        asia_prices = {
            RiskFactor.JKM: jkm_L1[:, i],
            RiskFactor.HH: hh_L[:, i],
            RiskFactor.CHARTER: charter_arr,
            RiskFactor.VLSFO: vlsfo_arr,
        }
        _assert_finite_prices(eu_prices, f"Europe month {i} physical scenario prices")
        _assert_finite_prices(asia_prices, f"Asia month {i} physical scenario prices")

        eu_cargo[:, i] = eu_exposure.value_matrix(eu_prices)
        asia_cargo[:, i] = asia_exposure.value_matrix(asia_prices)

    return dict(eu_cargo=eu_cargo, asia_cargo=asia_cargo, hh_L=hh_L, ttf_L=ttf_L, jkm_L1=jkm_L1, fx_l=fx_l)


@dataclass
class VarResult:
    pnl: np.ndarray
    var95: float
    var99: float
    es95: float
    es99: float
    sd: float
    n: int
    portfolio: str
    scen: ScenarioSet

    def summary(self) -> dict:
        return dict(portfolio=self.portfolio, n=self.n, var95=self.var95, var99=self.var99,
                    es95=self.es95, es99=self.es99, sd=self.sd)


def _var_es(pnl: np.ndarray) -> tuple:
    n = len(pnl)
    s = np.sort(pnl)
    i95 = int(n * 0.05)
    i99 = int(n * 0.01)
    var95 = s[i95]
    var99 = s[i99]
    es95 = s[:max(i95, 1)].mean()
    es99 = s[:max(i99, 1)].mean()
    sd = pnl.std(ddof=0)
    return var95, var99, es95, es99, sd


def historical_var(D, tables, params: Params, portfolio: str = "12cargo", month_index: int = 0,
                    basin: str = "Europe", lookback: int = 500, method: str = "naive",
                    scen: Optional[ScenarioSet] = None) -> VarResult:
    """Section 8: historical simulation, full revaluation.

    portfolio:
      "single"  -- one cargo, one basin, one load month (basin=Europe/Asia)
      "hedged"  -- same, minus the Section 7 mechanical hedge legs
      "12cargo" -- 12 successive cargoes, each routed per that month's
                   verdict (Asia if gap>=0 else Europe) -- "today's
                   diversion strategy" priced forward
      "spread"  -- Asia minus Europe cargo P&L for one load month (the
                   risk of the diversion decision itself)
    """
    D = pd.Timestamp(D)
    if scen is None:
        scen = build_scenarios(tables, D, lookback=lookback, method=method)

    hh_row = snap(tables.hh, D)
    ttf_row = snap(tables.ttf, D)
    jkm_row = snap(tables.jkm, D)
    fx_row = snap(tables.fx, D)
    ch_row = snap(tables.charter, D)
    charter = params.charter_override if params.charter_override is not None else float(ch_row["rate174"])

    base_hh = hh_row[[f"c{i}" for i in range(1, N_STRIP_COLS + 1)]].to_numpy(dtype=float)
    base_ttf = ttf_row[[f"c{i}" for i in range(1, N_STRIP_COLS + 1)]].to_numpy(dtype=float)
    base_jkm = jkm_row[[f"c{i}" for i in range(1, N_STRIP_COLS + 1)]].to_numpy(dtype=float)
    base_spot, base_o6, base_o1 = float(fx_row["spot"]), float(fx_row["o6"]), float(fx_row["o1"])

    base = model.strip(D, tables, params)
    scen_vals = _vectorized_reprice(scen, D, base_hh, base_ttf, base_jkm, base_spot, base_o6, base_o1, charter, params)

    if portfolio == "single":
        col = "eu_cargo" if basin == "Europe" else "asia_cargo"
        base_val = base.iloc[month_index][col]
        scen_val = scen_vals[col][:, month_index]
        pnl = scen_val - base_val

    elif portfolio == "hedged":
        if basin == "Europe":
            eu_cargo_base = float(base.iloc[month_index]["eu_cargo"])
            eu_cargo_scen = scen_vals["eu_cargo"][:, month_index]
            full_pnl = eu_cargo_scen - eu_cargo_base

            ttf_L_scen = scen_vals["ttf_L"][:, month_index]
            hh_L_scen = scen_vals["hh_L"][:, month_index]
            fx_L_scen = scen_vals["fx_l"][:, month_index]
            ttf_L_base = float(base_ttf[month_index])
            hh_L_base = float(base_hh[month_index])
            fx_L_base = float(base.iloc[month_index]["fx"])

            hedge_pnl = eu_hedge_pnl_vector(
                ttf_L_scen, hh_L_scen, fx_L_scen,
                ttf_L_base, hh_L_base, fx_L_base,
                params.cargo_size, params.hh_grossup, params.boil_off_rate, params.europe_laden_days,
            )
            pnl = full_pnl + hedge_pnl
        else:
            asia_cargo_base = float(base.iloc[month_index]["asia_cargo"])
            asia_cargo_scen = scen_vals["asia_cargo"][:, month_index]
            full_pnl = asia_cargo_scen - asia_cargo_base

            _, s_local = contract_calendar(D)
            jkm_base_idx = month_index + 1 - s_local   # 0-based, matches strip()'s (i+2-s)-1
            jkm_L1_scen = scen_vals["jkm_L1"][:, month_index]
            hh_L_scen = scen_vals["hh_L"][:, month_index]
            jkm_L1_base = float(base_jkm[jkm_base_idx])
            hh_L_base = float(base_hh[month_index])

            hedge_pnl = asia_hedge_pnl_vector(
                jkm_L1_scen, hh_L_scen,
                jkm_L1_base, hh_L_base,
                params.cargo_size, params.hh_grossup, params.boil_off_rate, params.asia_laden_days,
            )
            pnl = full_pnl + hedge_pnl

    elif portfolio == "12cargo":
        base_verdict_asia = (base["verdict"] == "Asia").to_numpy()
        base_val = np.where(base_verdict_asia, base["asia_cargo"], base["eu_cargo"]).sum()
        scen_selected = np.where(base_verdict_asia[None, :], scen_vals["asia_cargo"], scen_vals["eu_cargo"])
        pnl = scen_selected.sum(axis=1) - base_val

    elif portfolio == "spread":
        base_val = base.iloc[month_index]["asia_cargo"] - base.iloc[month_index]["eu_cargo"]
        scen_val = scen_vals["asia_cargo"][:, month_index] - scen_vals["eu_cargo"][:, month_index]
        pnl = scen_val - base_val

    else:
        raise ValueError(f"unknown portfolio {portfolio!r}")

    var95, var99, es95, es99, sd = _var_es(pnl)
    return VarResult(pnl=pnl, var95=var95, var99=var99, es95=es95, es99=es99, sd=sd, n=len(pnl),
                      portfolio=portfolio, scen=scen)


def historical_var_physical(D, tables, params: Params, portfolio: str = "single", month_index: int = 0,
                             basin: str = "Europe", lookback: int = 500, method: str = "naive",
                             scen: Optional[ScenarioSet] = None,
                             first_cargo_state: Optional[decision.FirstCargoState] = None) -> VarResult:
    """Physical-basis variant of historical_var() (R6 increment C.5, plan
    sect 6.C.5): same historical-simulation machinery (build/reuse a
    ScenarioSet, reprice every scenario, VaR/ES/sd off the resulting P&L
    vector), but exposures come from
    cashflows.physical_cargo_quantities()/physical_cargo_cashflows()
    instead of the legacy formula, so first_cargo_state's sunk-cost
    zeroing (C.2) is live and the BASE value matches
    decision.route_value(..., first_cargo_state=first_cargo_state)
    .incremental_value -- NOT model.strip()'s eu_cargo/asia_cargo -- to
    <= $0.01 (tests/test_physical_cashflows.py's zero-shock test, same
    R1-style construction: a zero-return ScenarioSet must reprice to
    ~$0 P&L against this function's own base).

    portfolio: "single" or "spread" ONLY (plan sect 8.3: 12cargo has no
    physical-basis equivalent -- it is both an infeasible one-vessel
    portfolio and fixture-bound to the legacy basis; programme arrives in
    increment D). "hedged" is not supported either -- the Section 7
    mechanical hedge legs are themselves legacy-formula-derived
    (hedge_legs_from_exposure() is increment F).

    A NEW function rather than a `basis=` parameter bolted onto
    historical_var() itself, per the plan's explicit instruction to
    extend risk.py "without changing the legacy path's behaviour or
    signatures" -- historical_var() is untouched by this increment,
    byte-for-byte (same source, same tests, same frozen 64/64).
    """
    if portfolio not in ("single", "spread"):
        raise ValueError(
            f"physical-basis VaR supports portfolio 'single' or 'spread' only (got {portfolio!r}); "
            "12cargo is legacy-basis-only (infeasible one-vessel portfolio, fixture-bound -- plan "
            "sect 8.3) and hedged/programme are not yet wired to the physical basis"
        )
    D = pd.Timestamp(D)
    if scen is None:
        scen = build_scenarios(tables, D, lookback=lookback, method=method)

    hh_row = snap(tables.hh, D)
    ttf_row = snap(tables.ttf, D)
    jkm_row = snap(tables.jkm, D)
    fx_row = snap(tables.fx, D)
    ch_row = snap(tables.charter, D)
    charter = params.charter_override if params.charter_override is not None else float(ch_row["rate174"])

    base_hh = hh_row[[f"c{i}" for i in range(1, N_STRIP_COLS + 1)]].to_numpy(dtype=float)
    base_ttf = ttf_row[[f"c{i}" for i in range(1, N_STRIP_COLS + 1)]].to_numpy(dtype=float)
    base_jkm = jkm_row[[f"c{i}" for i in range(1, N_STRIP_COLS + 1)]].to_numpy(dtype=float)
    base_spot, base_o6, base_o1 = float(fx_row["spot"]), float(fx_row["o6"]), float(fx_row["o1"])

    base_eu = physical_cargo_cashflows(D, tables, params, month_index, "Europe", first_cargo_state)
    base_asia = physical_cargo_cashflows(D, tables, params, month_index, "Asia", first_cargo_state)
    base_eu_val = base_eu.value(base_eu.base_prices)
    base_asia_val = base_asia.value(base_asia.base_prices)

    scen_vals = _vectorized_reprice_physical(scen, D, base_hh, base_ttf, base_jkm, base_spot, base_o6, base_o1,
                                              charter, params, first_cargo_state=first_cargo_state)

    if portfolio == "single":
        base_val = base_eu_val if basin == "Europe" else base_asia_val
        col = "eu_cargo" if basin == "Europe" else "asia_cargo"
        scen_val = scen_vals[col][:, month_index]
        pnl = scen_val - base_val

    else:  # spread
        base_val = base_asia_val - base_eu_val
        scen_val = scen_vals["asia_cargo"][:, month_index] - scen_vals["eu_cargo"][:, month_index]
        pnl = scen_val - base_val

    var95, var99, es95, es99, sd = _var_es(pnl)
    return VarResult(pnl=pnl, var95=var95, var99=var99, es95=es95, es99=es99, sd=sd, n=len(pnl),
                      portfolio=portfolio, scen=scen)


def scale_to_horizon(var_1d: float, days: int = 10, method: str = "sqrt", scen: Optional[ScenarioSet] = None,
                      tables=None, D=None, params=None, portfolio="12cargo", month_index=0, basin="Europe",
                      scenario_method: str = "naive") -> float:
    """Section 8.3: 10-day figure. method="sqrt" is a caveated
    sqrt(time)-scaling of the 1-day VaR (assumes iid returns -- flagged as
    approximate). method="overlapping" instead rebuilds the scenario set
    from overlapping N-day log-returns and reprices (slower, no iid
    assumption, but overlapping windows are autocorrelated by
    construction)."""
    if method == "sqrt":
        return var_1d * np.sqrt(days)
    if method == "overlapping":
        if scen is None:
            scen = build_scenarios(tables, D, lookback=500 + days, method=scenario_method)
        hh_ret = _rolling_sum(scen.hh_ret, days)
        ttf_ret = _rolling_sum(scen.ttf_ret, days)
        jkm_ret = _rolling_sum(scen.jkm_ret, days)
        fx_ret = _rolling_sum(scen.fx_ret[:, None], days)[:, 0]
        scen10 = ScenarioSet(dates=scen.dates[days - 1:], hh_ret=hh_ret, ttf_ret=ttf_ret, jkm_ret=jkm_ret,
                              fx_ret=fx_ret, end_date=scen.end_date, lookback=len(hh_ret), method="overlapping")
        r = historical_var(D, tables, params, portfolio=portfolio, month_index=month_index, basin=basin,
                            scen=scen10)
        return r.var95
    raise ValueError(f"unknown method {method!r}")


def _rolling_sum(x: np.ndarray, window: int) -> np.ndarray:
    n = x.shape[0] - window + 1
    out = np.empty((n,) + x.shape[1:])
    csum = np.cumsum(x, axis=0)
    out[0] = csum[window - 1]
    out[1:] = csum[window:] - csum[:-window]
    return out


# ===========================================================================
# Stress tests (deterministic, Section 8 "Stress tests")
# ===========================================================================

STRESS_REPLAY_DATES = ["2021-12-21", "2022-08-26", "2022-09-26"]


def stress_historical_replay(D, tables, params: Params, replay_date: str) -> dict:
    """Apply the single-day move (naive log-return) realised on
    `replay_date` onto today's (D's) curve, reprice the full strip."""
    D = pd.Timestamp(D)
    replay_date = pd.Timestamp(replay_date)
    inter = _intersection_dates(tables)
    if replay_date not in inter:
        idx = max(i for i, d in enumerate(inter) if d <= replay_date)
        replay_date = inter[idx]
    ridx = inter.index(replay_date)
    if ridx == 0:
        raise ValueError("replay date has no prior day in the intersection")
    d_prev = inter[ridx - 1]

    cols = [f"c{i}" for i in range(1, N_STRIP_COLS + 1)]
    hh_ret = np.log(tables.hh.set_index("date").loc[replay_date, cols].to_numpy(dtype=float)
                     / tables.hh.set_index("date").loc[d_prev, cols].to_numpy(dtype=float))
    ttf_ret = np.log(tables.ttf.set_index("date").loc[replay_date, cols].to_numpy(dtype=float)
                      / tables.ttf.set_index("date").loc[d_prev, cols].to_numpy(dtype=float))
    jkm_ret = np.log(tables.jkm.set_index("date").loc[replay_date, cols].to_numpy(dtype=float)
                      / tables.jkm.set_index("date").loc[d_prev, cols].to_numpy(dtype=float))
    fx_ret = float(np.log(tables.fx.set_index("date").loc[replay_date, "spot"]
                           / tables.fx.set_index("date").loc[d_prev, "spot"]))

    scen = ScenarioSet(dates=[replay_date], hh_ret=hh_ret[None, :], ttf_ret=ttf_ret[None, :],
                        jkm_ret=jkm_ret[None, :], fx_ret=np.array([fx_ret]), end_date=D, lookback=1, method="naive")
    r12 = historical_var(D, tables, params, portfolio="12cargo", scen=scen)
    rspread = historical_var(D, tables, params, portfolio="spread", month_index=0, scen=scen)
    return dict(name=f"Replay {replay_date.date()} move", pnl_12cargo=r12.pnl[0], pnl_m1_spread=rspread.pnl[0])


def run_stress_tests(D, tables, params: Params, basis: str = "legacy",
                      first_cargo_state: Optional[decision.FirstCargoState] = None) -> pd.DataFrame:
    """Section 8 'Stress tests': deterministic scenarios, reported as
    12-cargo strip and M1 diversion-spread P&L versus base.

    R6 increment C.6 (plan sect 6.C.6): basis-aware.

    basis="legacy" (the default -- every call site that predates this
    increment keeps it implicitly) reproduces every number BYTE-FOR-BYTE
    from before this increment: the three deterministic-shock branches
    below are the exact code that was here previously, untouched, just
    reached via an if/else instead of unconditionally. Regression-pinned
    in tests/test_physical_cashflows.py (stress is not part of the frozen
    64, so this increment pins it itself, per the plan's instruction).

    basis="physical" re-evaluates the THREE DETERMINISTIC shocks (Panama
    congestion, JKM +2.6, EUA bump) against
    cashflows.physical_cargo_cashflows() instead of model.strip(), with
    first_cargo_state's sunk-cost zeroing (C.2) applied to month 0's
    cargo. pnl_m1_spread reflects the physical Asia-minus-Europe spread
    under each shock; pnl_12cargo is NaN ("n/a" -- 12cargo has no
    physical-basis equivalent, plan sect 8.3: it is both an infeasible
    one-vessel portfolio and fixture-bound to the legacy basis).

    The THREE historical-REPLAY rows stay legacy/strip-based on EITHER
    basis -- both pnl columns come back NaN with a "n/a" note under
    basis="physical" rather than silently showing a legacy number under
    a "Physical" toggle. Judged disproportionate to port for this
    increment (decision recorded in the implementation report): each
    replay row's pnl_12cargo has no physical analogue either (same
    reason as the deterministic shocks), and porting just the spread half
    would mean duplicating stress_historical_replay()'s one-scenario
    ScenarioSet construction into a second, physical-basis code path for
    a single extra column across three rows out of six -- a
    disproportionate amount of new repricing machinery for this
    increment's marginal value; historical_var_physical() (C.5) already
    gives a caller who wants a physical-basis historical-replay number
    the building blocks to construct one directly.
    """
    if basis not in ("legacy", "physical"):
        raise ValueError(f"unknown basis {basis!r} (expected 'legacy' or 'physical')")

    base = model.strip(D, tables, params)
    base_12 = np.where(base["verdict"] == "Asia", base["asia_cargo"], base["eu_cargo"]).sum()
    base_spread_m1 = base.iloc[0]["asia_cargo"] - base.iloc[0]["eu_cargo"]

    def _physical_spread(shock_params: Params) -> float:
        eu = physical_cargo_cashflows(D, tables, shock_params, 0, "Europe", first_cargo_state)
        asia = physical_cargo_cashflows(D, tables, shock_params, 0, "Asia", first_cargo_state)
        return asia.value(asia.base_prices) - eu.value(eu.base_prices)

    base_spread_phys = _physical_spread(params) if basis == "physical" else None

    rows = []
    for rd in STRESS_REPLAY_DATES:
        if basis == "physical":
            rows.append(dict(
                scenario=f"Replay {rd} move", pnl_12cargo=np.nan, pnl_m1_spread=np.nan,
                note="n/a under physical basis -- historical-replay rows stay legacy/strip-based (plan sect 6.C.6)",
            ))
            continue
        try:
            r = stress_historical_replay(D, tables, params, rd)
            rows.append(dict(scenario=r["name"], pnl_12cargo=r["pnl_12cargo"], pnl_m1_spread=r["pnl_m1_spread"]))
        except Exception as e:
            rows.append(dict(scenario=f"Replay {rd} move", pnl_12cargo=np.nan, pnl_m1_spread=np.nan, note=str(e)))

    # --- Panama congestion (Asia RT 54.7d) ---
    p2 = copy.deepcopy(params)
    p2.asia_rt_days = model.ASIA_RT_CONG
    if basis == "legacy":
        s = model.strip(D, tables, p2)
        val12 = np.where(s["verdict"] == "Asia", s["asia_cargo"], s["eu_cargo"]).sum()
        rows.append(dict(scenario="Panama congestion (Asia RT 54.7d)", pnl_12cargo=val12 - base_12,
                          pnl_m1_spread=(s.iloc[0]["asia_cargo"] - s.iloc[0]["eu_cargo"]) - base_spread_m1))
    else:
        rows.append(dict(scenario="Panama congestion (Asia RT 54.7d)", pnl_12cargo=np.nan,
                          pnl_m1_spread=_physical_spread(p2) - base_spread_phys,
                          note="pnl_12cargo n/a under physical basis (plan sect 8.3)"))

    # --- JKM +2.6 $/MMBtu ---
    if basis == "legacy":
        t2 = copy.deepcopy(tables)
        t2.jkm = t2.jkm.copy()
        jkm_cols = [c for c in t2.jkm.columns if c.startswith("c")]
        row_date = snap(t2.jkm, D)["date"]
        t2.jkm.loc[t2.jkm["date"] == row_date, jkm_cols] += 2.6
        s = model.strip(D, t2, params)
        val12 = np.where(s["verdict"] == "Asia", s["asia_cargo"], s["eu_cargo"]).sum()
        rows.append(dict(scenario="JKM +2.6 $/MMBtu (13-Jul-2026 Hormuz repricing)", pnl_12cargo=val12 - base_12,
                          pnl_m1_spread=(s.iloc[0]["asia_cargo"] - s.iloc[0]["eu_cargo"]) - base_spread_m1))
    else:
        # A genuine factor-PRICE bump (unlike the other two shocks, which
        # are QUANTITY-level Params changes): the same base exposures,
        # evaluated at JKM+2.6 instead of base JKM. Europe never
        # references JKM, so it is algebraically unaffected -- matching
        # the legacy shock's own Europe-invariance (eu_cargo is untouched
        # by a JKM-only table bump there too).
        base_eu = physical_cargo_cashflows(D, tables, params, 0, "Europe", first_cargo_state)
        base_asia = physical_cargo_cashflows(D, tables, params, 0, "Asia", first_cargo_state)
        bumped_asia_prices = dict(base_asia.base_prices)
        bumped_asia_prices[RiskFactor.JKM] = bumped_asia_prices[RiskFactor.JKM] + 2.6
        new_spread = base_asia.value(bumped_asia_prices) - base_eu.value(base_eu.base_prices)
        rows.append(dict(scenario="JKM +2.6 $/MMBtu (13-Jul-2026 Hormuz repricing)", pnl_12cargo=np.nan,
                          pnl_m1_spread=new_spread - base_spread_phys,
                          note="pnl_12cargo n/a under physical basis (plan sect 8.3)"))

    # --- EUA EUR70 -> EUR120/t ---
    p2 = copy.deepcopy(params)
    p2.eua_price = 120.0
    if basis == "legacy":
        s = model.strip(D, tables, p2)
        val12 = np.where(s["verdict"] == "Asia", s["asia_cargo"], s["eu_cargo"]).sum()
        rows.append(dict(scenario="EUA EUR70 -> EUR120/t", pnl_12cargo=val12 - base_12,
                          pnl_m1_spread=(s.iloc[0]["asia_cargo"] - s.iloc[0]["eu_cargo"]) - base_spread_m1))
    else:
        rows.append(dict(scenario="EUA EUR70 -> EUR120/t", pnl_12cargo=np.nan,
                          pnl_m1_spread=_physical_spread(p2) - base_spread_phys,
                          note="pnl_12cargo n/a under physical basis (plan sect 8.3)"))

    return pd.DataFrame(rows)


# ===========================================================================
# Backtest (rolling 1-day VaR vs realised P&L, Kupiec traffic light)
# ===========================================================================

def backtest_var(tables, params: Params, portfolio: str = "12cargo", lookback: int = 500,
                  window_days: int = 60, alpha: float = 0.05, method: str = "naive",
                  month_index: int = 0, basin: str = "Europe") -> pd.DataFrame:
    """Rolling one-day VaR versus realised next-day P&L.

    Interim roll-safety rule: pairs that cross the NG/TTF calendar roll or the
    JKM 15th/16th roll are skipped.  This prevents the previous live defect in
    which row M1 at ``D`` was compared with a different physical delivery month
    at ``D_next``.  The production replacement is contract-ID backtesting.

    ``method`` controls the historical scenario construction.  The function
    default remains ``naive`` to preserve the frozen legacy fixture; the
    Streamlit application defaults to ``roll_aligned``.
    """
    if portfolio == "hedged":
        raise ValueError("hedged backtest is not implemented in the interim roll-safe path")

    inter = _intersection_dates(tables)
    n = len(inter)
    start = max(lookback + 1, n - window_days)
    rows = []
    skipped_roll_pairs = 0

    for i in range(start, n - 1):
        D = inter[i]
        D_next = inter[i + 1]
        F, s = contract_calendar(D)
        F_next, s_next = contract_calendar(D_next)
        if F != F_next or s != s_next:
            skipped_roll_pairs += 1
            continue

        r = historical_var(
            D, tables, params, portfolio=portfolio, lookback=lookback,
            method=method, month_index=month_index, basin=basin,
        )
        var_level = r.var95 if abs(alpha - 0.05) < 1e-9 else r.var99

        base_D = model.strip(D, tables, params)
        base_Dn = model.strip(D_next, tables, params)

        if portfolio == "12cargo":
            selection = base_D["verdict"].to_numpy() == "Asia"
            base_val = np.where(selection, base_D["asia_cargo"], base_D["eu_cargo"]).sum()
            next_val = np.where(selection, base_Dn["asia_cargo"], base_Dn["eu_cargo"]).sum()
        elif portfolio == "single":
            col = "eu_cargo" if basin == "Europe" else "asia_cargo"
            base_val = float(base_D.iloc[month_index][col])
            next_val = float(base_Dn.iloc[month_index][col])
        elif portfolio == "spread":
            base_val = float(base_D.iloc[month_index]["asia_cargo"] - base_D.iloc[month_index]["eu_cargo"])
            next_val = float(base_Dn.iloc[month_index]["asia_cargo"] - base_Dn.iloc[month_index]["eu_cargo"])
        else:
            raise ValueError(f"backtest portfolio {portfolio!r} is not supported")

        realised = next_val - base_val
        rows.append(dict(date=D, next_date=D_next, var=var_level, realised_pnl=realised,
                         exception=realised < var_level, method=method))

    out = pd.DataFrame(rows)
    out.attrs["skipped_roll_pairs"] = skipped_roll_pairs
    return out


def kupiec_test(exceptions: int, n: int, alpha: float = 0.05) -> dict:
    """Kupiec (1995) proportion-of-failures likelihood-ratio test, with a
    Basel-style traffic-light classification on the exception count.
    (Note: the green/yellow/red thresholds follow the Basel 250-day / 99%
    convention; applied here to shorter 95% windows they are indicative
    only -- the LR statistic and p-value are the rigorous outputs.)"""
    if n == 0:
        return dict(exceptions=0, n=0, rate=np.nan, lr_stat=np.nan, p_value=np.nan, light="n/a")
    x, p = exceptions, alpha
    rate = x / n
    with np.errstate(divide="ignore", invalid="ignore"):
        if x == 0:
            lr = -2 * n * np.log(1 - p)
        elif x == n:
            lr = -2 * n * np.log(p)
        else:
            lr = -2 * (np.log(((1 - p) ** (n - x)) * (p ** x)) - np.log(((1 - rate) ** (n - x)) * (rate ** x)))
    from math import erf, sqrt
    # chi2(1) survival function via complementary error function
    p_value = 1 - erf(np.sqrt(max(lr, 0) / 2))
    if x <= 4:
        light = "green"
    elif x <= 9:
        light = "yellow"
    else:
        light = "red"
    return dict(exceptions=x, n=n, rate=rate, lr_stat=float(lr), p_value=float(p_value), light=light)
