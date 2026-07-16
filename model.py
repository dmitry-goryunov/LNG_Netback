"""
model.py -- Sections 2-3 of the spec, as pure functions.

No Streamlit import anywhere in this module: it must be importable and
runnable head-less (tests/test_model.py exercises it directly).

Public entry point: strip(D, tables, params) -> pandas.DataFrame of the
12 load-month rows (Step 6), plus the Step 1 snapshot info attached as
DataFrame.attrs so the UI can show "snapped-date" captions.
"""

from __future__ import annotations

import bisect
import datetime as dt
from dataclasses import dataclass, asdict

import pandas as pd

# ---------------------------------------------------------------------------
# Voyage geometry (re-baselined 13-Jul-2026, notes.md review pt 4): leg days
# are DERIVED from distance / speed, not asserted. 19.5 kn = 468 nm/day.
#   Europe:  4,900 nm each way            -> 10.4701 d/leg, RT 25.9402 d
#   Asia:    9,300 nm + 1 d canal transit -> 20.8718 d/leg, RT 46.7436 d
#   Congestion: +4 waiting d per leg      -> RT 54.7436 d (waiting charged at
#   full propulsion rates -- a documented overstatement, see README)
# ---------------------------------------------------------------------------
SPEED_NM_PER_DAY = 19.5 * 24.0
EU_LEG_DAYS = 4900.0 / SPEED_NM_PER_DAY            # 10.470085...
ASIA_LEG_DAYS = 9300.0 / SPEED_NM_PER_DAY + 1.0    # 20.871794...
ASIA_RT_BASE = 2 * ASIA_LEG_DAYS + 5.0             # 46.743590...
ASIA_RT_CONG = 2 * (ASIA_LEG_DAYS + 4.0) + 5.0     # 54.743590...

# ---------------------------------------------------------------------------
# Step 5 -- static cost parameters (all user-editable in the sidebar)
# ---------------------------------------------------------------------------


@dataclass
class Params:
    # cargo / boil-off
    cargo_size: float = 3_500_000.0          # MMBtu
    boil_off_rate: float = 0.0010            # fraction/day (0.10%/day)

    # Europe route (distance-derived: 10.47 + 10.47 + 5 = 25.94 d RT)
    europe_laden_days: float = EU_LEG_DAYS
    europe_ballast_days: float = EU_LEG_DAYS
    europe_port_days: float = 5.0
    loading: float = 0.06                    # $/MMBtu
    eu_regas_port: float = 0.41              # $/MMBtu (DES has no regas; hub has cost)
    other_cost: float = 0.07                 # $/MMBtu (insurance, LC, brokerage)

    # Asia route (base 46.74 d / congestion 54.74 d). WORKBOOK PARITY: laden days
    # default to symmetric legs, laden = (RT - port)/2, exactly as the xlsx
    # Assumptions sheet derives B19 = (B20 - B15)/2. Congestion (RT 55)
    # therefore lengthens BOTH legs (laden 25 d: more boil-off, more laden
    # fuel). Set asia_laden_days_override to pin the laden leg instead
    # (e.g. to model waiting as pure ballast/idle time).
    asia_rt_days: float = ASIA_RT_BASE       # 46.74 base / 54.74 congestion
    asia_laden_days_override: float | None = None

    @property
    def asia_laden_days(self) -> float:
        if self.asia_laden_days_override is not None:
            return self.asia_laden_days_override
        return (self.asia_rt_days - self.asia_port_days) / 2.0

    @asia_laden_days.setter
    def asia_laden_days(self, v: float | None) -> None:
        self.asia_laden_days_override = v
    asia_port_days: float = 5.0
    asia_port_cost: float = 0.05             # $/MMBtu (DES: no regas)
    panama_toll_roundtrip: float = 1_600_000.0  # $ (Panama toll x2)

    # fuel
    laden_fuel_requirement: float = 150.0    # t/d (reference; = bog offset + residual)
    natural_bog_offset_mmbtu: float = 3_500.0  # MMBtu/d (reference)
    natural_bog_offset_t: float = 86.4       # t/d VLSFO-eq (reference)
    residual_laden_vlsfo: float = 63.6       # t/d -- used in ship-cost formula
    ballast_fuel: float = 130.0              # t/d -- used in ship-cost formula
    port_fuel_rate: float = 25.0             # t/d -- used in ship-cost formula
    vlsfo_price: float = 530.0               # $/t, static for ALL dates

    # gas cost chain
    hh_grossup: float = 1.15
    liquefaction_toll: float = 3.00          # $/MMBtu
    pipeline: float = 0.20                   # $/MMBtu

    # ETS
    eua_price: float = 70.0                  # EUR/t, static, unverified
    # t CO2 in ETS scope per EU RT, derived from the voyage fuel balance:
    # 0.5*((resVLSFO*3.15 + cargo*BOR/48.6*2.75)*eu_laden + 130*3.15*eu_ballast)
    #   + 0.5*25*3.15*port_days, at 10.4701-day legs = ~4,426 t
    # (was 4,243 t under the pre-rebaseline 10-day legs)
    co2_eu_ets_tonnes: float = 4425.9

    # charter override: None => use snap(D) rate from the charter sheet
    charter_override: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def phase_for_year(year: int) -> float:
    """EU ETS maritime phase-in factor (Step 4): 0 before 2024, 0.4 in
    2024, 0.7 in 2025, 1.0 from 2026. Applied by the delivery month's
    year (the year the EU-leg voyage/emission actually occurs)."""
    if year < 2024:
        return 0.0
    if year == 2024:
        return 0.4
    if year == 2025:
        return 0.7
    return 1.0


def natural_bog_offset_t_per_day(params: Params) -> float:
    """Natural boil-off gas generation implied by the physical inputs
    (boil_off_rate x cargo_size), expressed in t/d VLSFO-equivalent via
    the reference ratio already embedded in Params (3,500 MMBtu/d ==
    86.4 t/d). At Params() defaults this reproduces the hand-set
    natural_bog_offset_t reference constant (86.4) exactly."""
    return params.boil_off_rate * params.cargo_size * (
        params.natural_bog_offset_t / params.natural_bog_offset_mmbtu
    )


def derived_residual_laden_vlsfo(params: Params) -> float:
    """Laden-leg purchased-fuel rate implied by the physical fuel
    picture: total laden energy demand (laden_fuel_requirement) minus
    the natural BOG offset from boil_off_rate x cargo_size. At Params()
    defaults this reproduces the legacy residual_laden_vlsfo constant
    (150 - 86.4 = 63.6 t/d) exactly, so deriving it changes nothing
    until a user edits one of the physical inputs -- at which point the
    legacy ship-cost formula and the segment-level physical engine
    finally move together, instead of reading two independent sidebar
    fields that only coincided at defaults (a review finding: editing
    one silently moved only one of the two valuation paths).

    Clamped at zero: if natural BOG alone exceeds the laden demand, the
    vessel buys no liquid fuel (the physical engine reliquefies or vents
    the surplus; a negative purchased-fuel rate would be a phantom
    credit in the legacy formula). Does not touch strip() itself -- the
    frozen legacy path still reads params.residual_laden_vlsfo, whatever
    the caller set it to; this is the coherent way for a UI to set it."""
    residual = params.laden_fuel_requirement - natural_bog_offset_t_per_day(params)
    return max(round(residual, 6), 0.0)


# ---------------------------------------------------------------------------
# Step 1 -- snap
# ---------------------------------------------------------------------------


def snap(df: pd.DataFrame, D) -> pd.Series:
    """Last row with date <= D (Excel MATCH(D, dates, 1) equivalent)."""
    D = pd.Timestamp(D)
    sub = df.loc[df["date"] <= D]
    if sub.empty:
        raise ValueError(f"no rows on or before {D.date()}")
    return sub.iloc[-1]


# ---------------------------------------------------------------------------
# Step 2 -- contract calendar
# ---------------------------------------------------------------------------


def _month_add(ts: pd.Timestamp, n: int) -> pd.Timestamp:
    y = ts.year + (ts.month - 1 + n) // 12
    mo = (ts.month - 1 + n) % 12 + 1
    return pd.Timestamp(year=y, month=mo, day=1)


def contract_calendar(D) -> tuple[pd.Timestamp, int]:
    """F = first day of month(D) + 1 month. JKM roll shift s: 0 if
    day(D) <= 15 else 1."""
    D = pd.Timestamp(D)
    F = _month_add(pd.Timestamp(year=D.year, month=D.month, day=1), 1)
    s = 0 if D.day <= 15 else 1
    return F, s


def load_months(F: pd.Timestamp, n: int = 12) -> list[pd.Timestamp]:
    return [_month_add(F, i) for i in range(n)]


# ---------------------------------------------------------------------------
# Step 3 -- FX curve
# ---------------------------------------------------------------------------


def fx_curve(spot: float, o6: float, o1: float, D):
    """Returns fx(m) callable: linear interpolation spot -> o6 -> o1 on
    t = (mid-month(m) - D) / 30.44 months. A uniform bump of spot, o6 and
    o1 by the same delta always shifts fx(m) by exactly that delta for
    every m (affine-weights property) -- this is what makes the
    "parallel shift" FX shock convention (Sections 6 & 8) well defined.
    """
    D = pd.Timestamp(D)

    def fx(m: pd.Timestamp) -> float:
        mid = pd.Timestamp(year=m.year, month=m.month, day=15)
        t = (mid - D).days / 30.44
        if t <= 6:
            return spot + (o6 - spot) * t / 6.0
        return o6 + (o1 - o6) * (t - 6) / 6.0

    return fx


FX_YEAR_TENORS = (2, 3, 4, 5, 6, 7, 8, 9, 10)


def fx_curve_multi(fx_row, D):
    """Returns fx(m) callable: piecewise-linear interpolation through every
    real anchor available on fx_row -- spot (t=0), 6M (t=6), 1Y (t=12),
    then 2Y..10Y (t=24..120) from data.load_fx()'s o_y2..o_y10 columns --
    extrapolating only beyond the single last available anchor.

    This exists only for strips requested beyond 12 months
    (strip(..., n_months>12)). fx_curve() above is unchanged and remains
    what every n_months<=12 call uses, including every frozen legacy
    fixture, so this function can never affect them.

    Degrades gracefully: any missing o_y* column is skipped, so with only
    legacy (pre-Improvement-6) data this reduces to the same three anchors
    as fx_curve() -- but still extrapolates past 1Y rather than 10Y in
    that case, since there is nothing further to interpolate through. The
    resulting fx function exposes `.max_anchor_months` (the last anchor's
    t) so callers can flag rows that fall beyond it as extrapolated,
    exactly as they already do for the 12-month curve's 1Y boundary.
    """
    D = pd.Timestamp(D)
    anchors: list[tuple[float, float]] = [
        (0.0, float(fx_row["spot"])),
        (6.0, float(fx_row["o6"])),
        (12.0, float(fx_row["o1"])),
    ]
    for years in FX_YEAR_TENORS:
        v = fx_row.get(f"o_y{years}")
        if v is not None and pd.notna(v):
            anchors.append((years * 12.0, float(v)))
    anchors.sort(key=lambda a: a[0])
    tenors = [a[0] for a in anchors]

    def fx(m: pd.Timestamp) -> float:
        mid = pd.Timestamp(year=m.year, month=m.month, day=15)
        t = (mid - D).days / 30.44
        i = bisect.bisect_right(tenors, t)
        i = max(1, min(i, len(anchors) - 1))
        t0, v0 = anchors[i - 1]
        t1, v1 = anchors[i]
        return v0 + (v1 - v0) * (t - t0) / (t1 - t0)

    fx.max_anchor_months = tenors[-1]
    return fx


# ---------------------------------------------------------------------------
# Step 6 -- per load month
# ---------------------------------------------------------------------------


@dataclass
class SnapInfo:
    D: pd.Timestamp
    hh_date: pd.Timestamp
    ttf_date: pd.Timestamp
    jkm_date: pd.Timestamp
    fx_date: pd.Timestamp
    charter_date: pd.Timestamp
    charter_rate: float
    charter_overridden: bool
    F: pd.Timestamp
    s: int


def strip(D, tables, params: Params = Params(), n_months: int = 12) -> pd.DataFrame:
    """Section 2-3 Step 1-6, vectorised over `n_months` load months F..F+n-1.

    `tables` is a data.CurveTables (or any object exposing .hh/.ttf/.jkm/
    .fx/.charter DataFrames with the schema produced by data.py's
    loaders). Returns an n_months-row DataFrame; snap/contract-calendar
    info is attached at df.attrs["snap"] (a SnapInfo).

    n_months defaults to 12 and, at that default, is byte-identical to
    every prior version of this function -- it uses fx_curve() exactly as
    before (spot/6M/1Y, extrapolated past 1Y). This is what the frozen
    legacy regression suite calls and must never change. n_months>12 uses
    fx_curve_multi() instead, which interpolates through real 2Y-10Y FX
    anchors rather than extrapolating past 1Y; it can extend up to
    whatever HH/TTF/JKM forward columns tables actually has (this real
    workbook: HH/TTF 64 columns, JKM 44 columns, comfortably covering 36
    months) and raises ValueError rather than a confusing KeyError if
    n_months exceeds what's available.
    """
    D = pd.Timestamp(D)

    hh_row = snap(tables.hh, D)
    ttf_row = snap(tables.ttf, D)
    jkm_row = snap(tables.jkm, D)
    fx_row = snap(tables.fx, D)
    ch_row = snap(tables.charter, D)

    charter_overridden = params.charter_override is not None
    charter = params.charter_override if charter_overridden else float(ch_row["rate174"])

    F, s = contract_calendar(D)

    max_jkm_idx = n_months + 1 - s
    hh_cols = sum(1 for c in hh_row.index if c.startswith("c"))
    ttf_cols = sum(1 for c in ttf_row.index if c.startswith("c"))
    jkm_cols = sum(1 for c in jkm_row.index if c.startswith("c"))
    if n_months > hh_cols or n_months > ttf_cols or max_jkm_idx > jkm_cols:
        raise ValueError(
            f"n_months={n_months} exceeds available forward columns on {D.date()}: "
            f"HH has {hh_cols}, TTF has {ttf_cols}, JKM has {jkm_cols} (needs {max_jkm_idx})"
        )

    if n_months <= 12:
        fxfn = fx_curve(float(fx_row["spot"]), float(fx_row["o6"]), float(fx_row["o1"]), D)
        fx_extrap_boundary = 12.0
    else:
        fxfn = fx_curve_multi(fx_row, D)
        fx_extrap_boundary = fxfn.max_anchor_months

    months = load_months(F, n_months)

    cargo = params.cargo_size
    bo = params.boil_off_rate
    eu_laden, eu_ballast, eu_port = params.europe_laden_days, params.europe_ballast_days, params.europe_port_days
    europe_rt = eu_laden + eu_ballast + eu_port
    asia_rt = params.asia_rt_days
    asia_laden, asia_port = params.asia_laden_days, params.asia_port_days
    asia_ballast = asia_rt - asia_laden - asia_port

    rows = []
    for i, L in enumerate(months):
        hh_l = float(hh_row[f"c{i + 1}"])
        ttf_l = float(ttf_row[f"c{i + 1}"])
        jkm_idx = i + 2 - s
        jkm_l1 = float(jkm_row[f"c{jkm_idx}"])
        fx_l = fxfn(L)
        fx_mid = pd.Timestamp(year=L.year, month=L.month, day=15)
        fx_tenor_months = (fx_mid - D).days / 30.44
        fx_extrapolated = fx_tenor_months > fx_extrap_boundary

        proc = hh_l * params.hh_grossup + params.liquefaction_toll + params.pipeline
        ttf_usd = ttf_l * fx_l / 3.412

        eu_ship = (
            charter * europe_rt
            + (params.residual_laden_vlsfo * eu_laden + params.ballast_fuel * eu_ballast
               + params.port_fuel_rate * eu_port) * params.vlsfo_price
        ) / cargo
        as_ship = (
            charter * asia_rt
            + (params.residual_laden_vlsfo * asia_laden + params.ballast_fuel * asia_ballast
               + params.port_fuel_rate * asia_port) * params.vlsfo_price
            + params.panama_toll_roundtrip
        ) / cargo

        phase = phase_for_year(L.year)
        ets = params.co2_eu_ets_tonnes * params.eua_price * phase * fx_l / cargo

        eu_bo_frac = bo * eu_laden
        asia_bo_frac = bo * asia_laden

        eu_margin = ttf_usd - (
            proc + params.loading + eu_ship + eu_bo_frac * ttf_usd
            + params.eu_regas_port + params.other_cost + ets
        )
        eu_day = eu_margin * cargo / europe_rt
        eu_cargo = eu_margin * cargo

        asia_cost_exbo = proc + params.loading + as_ship + params.asia_port_cost + params.other_cost
        asia_margin = jkm_l1 * (1 - asia_bo_frac) - asia_cost_exbo
        asia_day = asia_margin * cargo / asia_rt
        asia_cargo = asia_margin * cargo

        # --- Waterfall/flow-chart decomposition (additive only: nothing
        # above this block changes). eu_ship/as_ship/eu_margin/asia_margin
        # keep their original formulas untouched; these lines just split
        # the already-correct bundled totals into named sub-costs by
        # subtraction, so they reconcile to eu_ship/as_ship/the margins
        # exactly (to floating-point precision), with zero new independent
        # model logic. Feeds app.py's waterfall_breakdown() UI instead of
        # porting LNG_Diversion_Waterfall_Flows.html's separate JS model. ---
        eu_charter_cost = charter * europe_rt / cargo
        eu_bunker_cost = eu_ship - eu_charter_cost
        eu_boiloff_cost = eu_bo_frac * ttf_usd

        asia_charter_cost = charter * asia_rt / cargo
        asia_canal_cost = params.panama_toll_roundtrip / cargo
        asia_bunker_cost = as_ship - asia_charter_cost - asia_canal_cost
        asia_boiloff_cost = asia_bo_frac * jkm_l1

        jkm_star = (eu_day * asia_rt / cargo + asia_cost_exbo) / (1 - asia_bo_frac)
        gap = jkm_l1 - jkm_star
        verdict = "Asia" if gap >= 0 else "Europe"

        rows.append(dict(
            load_month=L, month_label=L.strftime("%b-%y"),
            HH=hh_l, TTF=ttf_l, JKM=jkm_l1, fx=fx_l,
            fx_tenor_months=fx_tenor_months, fx_extrapolated=fx_extrapolated,
            proc=proc, ttf_usd=ttf_usd,
            eu_ship=eu_ship, as_ship=as_ship, ets=ets,
            eu_margin=eu_margin, eu_day=eu_day, eu_cargo=eu_cargo,
            asia_cost_exbo=asia_cost_exbo, asia_margin=asia_margin,
            asia_day=asia_day, asia_cargo=asia_cargo,
            jkm_star=jkm_star, gap=gap, verdict=verdict,
            asia_rt=asia_rt, europe_rt=europe_rt, charter=charter,
            eu_charter_cost=eu_charter_cost, eu_bunker_cost=eu_bunker_cost,
            eu_boiloff_cost=eu_boiloff_cost,
            asia_charter_cost=asia_charter_cost, asia_bunker_cost=asia_bunker_cost,
            asia_canal_cost=asia_canal_cost, asia_boiloff_cost=asia_boiloff_cost,
        ))

    out = pd.DataFrame(rows)
    out.attrs["snap"] = SnapInfo(
        D=D,
        hh_date=hh_row["date"], ttf_date=ttf_row["date"], jkm_date=jkm_row["date"],
        fx_date=fx_row["date"], charter_date=ch_row["date"], charter_rate=charter,
        charter_overridden=charter_overridden, F=F, s=s,
    )
    out.attrs["fx_extrap_boundary_months"] = fx_extrap_boundary
    return out


def fx_extrapolated_rows(strip_df: pd.DataFrame) -> pd.DataFrame:
    """Rows whose representative mid-month lies beyond the 1Y FX outright.

    The current screening FX curve contains spot, 6M and 1Y only.  Values
    beyond 12 months are linearly extrapolated by :func:`fx_curve` and must be
    surfaced as a live model warning rather than silently accepted.
    """
    if "fx_extrapolated" not in strip_df.columns:
        return strip_df.iloc[0:0].copy()
    return strip_df.loc[strip_df["fx_extrapolated"].astype(bool)].copy()


def strip_month(D, tables, params: Params, month_index: int) -> dict:
    """Convenience: Step 6 output for a single load month (0=M1..11=M12)."""
    df = strip(D, tables, params)
    row = df.iloc[month_index].to_dict()
    row["snap"] = df.attrs["snap"]
    return row


def waterfall_breakdown(row: dict, params: Params) -> dict:
    """Ordered cost-line breakdown for one load-month row (a dict from
    strip_month() or strip_df.iloc[i].to_dict()), for waterfall and flow
    charts (app.py). Reads already-computed row fields plus the constant
    Params fields only -- no recomputation of the model. Each basin's
    lines sum to that basin's existing eu_margin / asia_margin to within
    floating-point precision:

        revenue - sum(v for _, v in lines) == margin

    This exists so the netback app can show its own live waterfall/flow
    view instead of relying on the separately-maintained JS model in
    LNG_Diversion_Waterfall_Flows.html (flagged in LNG_Diversion_Logic_GPT.md
    as a duplicate-logic risk to regenerate from the production model,
    not to copy)."""
    europe_lines = [
        ("Procurement", row["proc"]),
        ("Loading", params.loading),
        ("Charter", row["eu_charter_cost"]),
        ("Bunkers", row["eu_bunker_cost"]),
        ("Boil-off", row["eu_boiloff_cost"]),
        ("Discharge", params.eu_regas_port),
        ("ETS", row["ets"]),
        ("Other", params.other_cost),
    ]
    asia_lines = [
        ("Procurement", row["proc"]),
        ("Loading", params.loading),
        ("Charter", row["asia_charter_cost"]),
        ("Bunkers", row["asia_bunker_cost"]),
        ("Canal", row["asia_canal_cost"]),
        ("Boil-off", row["asia_boiloff_cost"]),
        ("Port", params.asia_port_cost),
        ("Other", params.other_cost),
    ]
    return {
        "Europe": dict(revenue=row["ttf_usd"], lines=europe_lines, margin=row["eu_margin"]),
        "Asia": dict(revenue=row["JKM"], lines=asia_lines, margin=row["asia_margin"]),
    }
