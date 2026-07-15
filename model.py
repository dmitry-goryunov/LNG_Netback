"""
model.py -- Sections 2-3 of the spec, as pure functions.

No Streamlit import anywhere in this module: it must be importable and
runnable head-less (tests/test_model.py exercises it directly).

Public entry point: strip(D, tables, params) -> pandas.DataFrame of the
12 load-month rows (Step 6), plus the Step 1 snapshot info attached as
DataFrame.attrs so the UI can show "snapped-date" captions.
"""

from __future__ import annotations

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


def strip(D, tables, params: Params = Params()) -> pd.DataFrame:
    """Section 2-3 Step 1-6, vectorised over the 12 load months F..F+11.

    `tables` is a data.CurveTables (or any object exposing .hh/.ttf/.jkm/
    .fx/.charter DataFrames with the schema produced by data.py's
    loaders). Returns a 12-row DataFrame; snap/contract-calendar info is
    attached at df.attrs["snap"] (a SnapInfo).
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
    fxfn = fx_curve(float(fx_row["spot"]), float(fx_row["o6"]), float(fx_row["o1"]), D)

    months = load_months(F, 12)

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

        jkm_star = (eu_day * asia_rt / cargo + asia_cost_exbo) / (1 - asia_bo_frac)
        gap = jkm_l1 - jkm_star
        verdict = "Asia" if gap >= 0 else "Europe"

        rows.append(dict(
            load_month=L, month_label=L.strftime("%b-%y"),
            HH=hh_l, TTF=ttf_l, JKM=jkm_l1, fx=fx_l,
            proc=proc, ttf_usd=ttf_usd,
            eu_ship=eu_ship, as_ship=as_ship, ets=ets,
            eu_margin=eu_margin, eu_day=eu_day, eu_cargo=eu_cargo,
            asia_cost_exbo=asia_cost_exbo, asia_margin=asia_margin,
            asia_day=asia_day, asia_cargo=asia_cargo,
            jkm_star=jkm_star, gap=gap, verdict=verdict,
            asia_rt=asia_rt, europe_rt=europe_rt, charter=charter,
        ))

    out = pd.DataFrame(rows)
    out.attrs["snap"] = SnapInfo(
        D=D,
        hh_date=hh_row["date"], ttf_date=ttf_row["date"], jkm_date=jkm_row["date"],
        fx_date=fx_row["date"], charter_date=ch_row["date"], charter_rate=charter,
        charter_overridden=charter_overridden, F=F, s=s,
    )
    return out


def strip_month(D, tables, params: Params, month_index: int) -> dict:
    """Convenience: Step 6 output for a single load month (0=M1..11=M12)."""
    df = strip(D, tables, params)
    row = df.iloc[month_index].to_dict()
    row["snap"] = df.attrs["snap"]
    return row
