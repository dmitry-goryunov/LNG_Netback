"""
spread_option.py -- intrinsic/extrinsic value of each route's margin,
priced as a spread option on (revenue index) vs Henry-Hub-linked
procurement cost, for the Forward-strip page.

Not a model.py rewrite: every input is read directly from an existing
model.strip() row (proc, ttf_usd, HH, JKM, eu_boiloff_cost,
asia_boiloff_cost, eu_margin, asia_margin) or a Params field
(hh_grossup) -- the strike for each route's option is derived
residually (forward_spread - stochastic_cost - margin), not
reconstructed cost-line by cost-line, so this can never drift from
model.strip()'s own margin formula even if that formula's fixed cost
lines change later.

Pricing model: Bachelier (normal), not Black-76/lognormal, because
eu_margin/asia_margin can be negative (a real, displayed state in this
tool already) and Black-76 requires a strictly positive underlying. The
"underlying" is the forward spread (revenue minus the HH-linked
stochastic cost component); its dollar volatility is built from each
leg's own fractional (lognormal-style) volatility scaled by its current
dollar level -- the standard approximation used for commodity spread
options (crack spreads, spark spreads) when a closed form is wanted:

    forward_margin = revenue_0 - cost_0 - strike   (== model.strip()'s
                                                       eu_margin/asia_margin)
    intrinsic       = max(forward_margin, 0)
    extrinsic       = option_value - intrinsic      (>= 0 always)

Two vol/correlation sources (user-selected in the UI):

  - "historical": realized vol/correlation from tables.hh/ttf/jkm's
    actual daily price history, over a rolling window measured in
    CALENDAR days (default 60) ending at the valuation date. Fully
    self-contained.
  - "tab": data.load_volatilities()'s pre-supplied vol/correlation
    table (LNG history.xlsx's 'volatilities' sheet), indexed by tenor
    (spot, M+1, M+2, ...). As of this module's authoring the sheet has
    Volatility TTF/HH/JKM and Correlation TTF/HH, TTF/JKM but *not*
    Correlation JKM/HH, which Asia's spread option needs (TTF and JKM
    never appear in the same route's margin, so TTF/JKM correlation
    isn't consumed here). Asia's tab-mode result is None until that
    column exists; Europe is unaffected, and historical mode is
    unaffected for both routes since it computes JKM/HH correlation
    directly from price history.

FX volatility is deliberately not modelled as a separate risk factor
(neither vol/correlation source has FX data): "TTF" here means the
already-USD-converted ttf_usd revenue reference, and its fractional
vol is approximated as equal to raw TTF's own fractional vol (FX vol
is assumed small relative to TTF/JKM/HH vol) -- a stated simplification,
not an oversight. JKM needs no such approximation; it is already USD.

Historical mode's constant-maturity tenor convention (c{months_forward})
matches model.strip()'s own c{i+1} indexing for HH/TTF exactly; for JKM
it does *not* replicate model.strip()'s contract-calendar shift
(model.contract_calendar's `s`) -- a deliberate simplification so
historical and tab-mode tenors share one convention throughout this
module, at the cost of a small, unquantified misalignment against
model.strip()'s own JKM contract selection on some valuation dates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

import model

TRADING_DAYS_PER_YEAR = 252.0
DEFAULT_HISTORICAL_WINDOW_DAYS = 60

VolSource = Literal["historical", "tab"]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _representative_load_date(load_month) -> pd.Timestamp:
    m = pd.Timestamp(load_month)
    return pd.Timestamp(year=m.year, month=m.month, day=15)


@dataclass(frozen=True)
class VolCorrInputs:
    """One route's revenue-vs-cost vol/correlation for one forward month.
    None fields mean "could not be determined from the selected source"
    -- callers must treat that as "cannot price the option," not
    silently substitute zero."""
    vol_revenue: Optional[float]
    vol_cost: Optional[float]
    correlation: Optional[float]
    source_detail: str


@dataclass(frozen=True)
class SpreadOptionResult:
    forward_margin: float
    intrinsic: float
    option_value: Optional[float]
    extrinsic: Optional[float]
    time_to_expiry_years: float
    detail: str


def price_spread_option(
    revenue_0: float,
    cost_0: float,
    strike: float,
    time_to_expiry_years: float,
    vc: VolCorrInputs,
) -> SpreadOptionResult:
    """Bachelier price of a claim paying max((revenue - cost) - strike, 0)
    at expiry, given today's forward revenue_0/cost_0/strike and the
    (fractional, annualised) vol/correlation of revenue vs cost.
    """
    forward_margin = revenue_0 - cost_0 - strike
    intrinsic = max(forward_margin, 0.0)

    if vc.vol_revenue is None or vc.vol_cost is None or vc.correlation is None:
        return SpreadOptionResult(
            forward_margin=forward_margin, intrinsic=intrinsic,
            option_value=None, extrinsic=None,
            time_to_expiry_years=time_to_expiry_years,
            detail=f"{vc.source_detail}: incomplete vol/correlation inputs",
        )
    if time_to_expiry_years < 0:
        raise ValueError(f"time_to_expiry_years must be >= 0, got {time_to_expiry_years}")

    dollar_vol_revenue = revenue_0 * vc.vol_revenue
    dollar_vol_cost = cost_0 * vc.vol_cost
    spread_variance = (
        dollar_vol_revenue ** 2 + dollar_vol_cost ** 2
        - 2.0 * vc.correlation * dollar_vol_revenue * dollar_vol_cost
    )
    spread_variance = max(spread_variance, 0.0)  # guards float noise when |correlation| ~ 1
    sigma_t = math.sqrt(spread_variance * time_to_expiry_years) if time_to_expiry_years > 0 else 0.0

    if sigma_t <= 0.0:
        option_value = intrinsic
    else:
        d = forward_margin / sigma_t
        option_value = forward_margin * _norm_cdf(d) + sigma_t * _norm_pdf(d)
        option_value = max(option_value, intrinsic)  # Bachelier price >= intrinsic; guards float noise

    return SpreadOptionResult(
        forward_margin=forward_margin, intrinsic=intrinsic,
        option_value=option_value, extrinsic=option_value - intrinsic,
        time_to_expiry_years=time_to_expiry_years, detail=vc.source_detail,
    )


def tab_vol_corr(vol_table: Optional[pd.DataFrame], months_forward: int, route: str) -> VolCorrInputs:
    """Reads data.load_volatilities()'s table for one route at one tenor.
    Clamps months_forward to the table's available tenor range (flat
    extrapolation beyond the last quoted point, the same convention used
    elsewhere in this app for curves shorter than the 36-month strip)."""
    route = route.strip().lower()
    if vol_table is None or len(vol_table) == 0:
        return VolCorrInputs(None, None, None, "volatilities tab: sheet not loaded")

    tenor = min(max(months_forward, vol_table.index.min()), vol_table.index.max())
    row = vol_table.loc[tenor]

    def _clean(x):
        return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

    vol_cost = _clean(row.get("vol_HH"))
    if route == "europe":
        vol_revenue = _clean(row.get("vol_TTF"))
        corr = row.get("corr_TTF_HH")
        corr = corr if corr is not None else row.get("corr_HH_TTF")
        missing_name = "Correlation TTF/HH"
    else:
        vol_revenue = _clean(row.get("vol_JKM"))
        corr = row.get("corr_JKM_HH")
        corr = corr if corr is not None else row.get("corr_HH_JKM")
        missing_name = "Correlation JKM/HH"
    corr = _clean(corr)

    detail = f"volatilities tab, M+{tenor}"
    if corr is None:
        detail += f" (missing '{missing_name}' column)"
    return VolCorrInputs(vol_revenue, vol_cost, corr, detail)


def _historical_window_dates(tables, D, window_days: int) -> list:
    """Dates in the intersection of tables.hh/ttf/jkm's own date columns,
    within the last `window_days` CALENDAR days up to and including D.
    Split out from the per-column returns extraction below because this
    part (a 3-way intersection over potentially thousands of rows) is
    identical for every tenor/route pairing at one (tables, D,
    window_days) -- callers pricing a whole 36-month x 2-route strip
    should compute this once and reuse it (see intrinsic_extrinsic_strip),
    not recompute it 72 times."""
    D = pd.Timestamp(D)
    start = D - pd.Timedelta(days=window_days)
    inter = sorted(set(tables.hh["date"]) & set(tables.ttf["date"]) & set(tables.jkm["date"]))
    return [d for d in inter if start <= d <= D]


def _returns_for_column(tables, dates: list, col: str) -> dict:
    """Log returns of tables.hh/ttf/jkm's `col` (e.g. 'c3') on the given
    (already date-windowed) dates, paired so every series' returns line
    up on the same trading days for a valid correlation."""
    if len(dates) < 3 or col not in tables.hh.columns or col not in tables.ttf.columns or col not in tables.jkm.columns:
        return {"hh": np.array([]), "ttf": np.array([]), "jkm": np.array([])}

    hh_px = tables.hh.set_index("date").reindex(dates)[col].to_numpy(dtype=float)
    ttf_px = tables.ttf.set_index("date").reindex(dates)[col].to_numpy(dtype=float)
    jkm_px = tables.jkm.set_index("date").reindex(dates)[col].to_numpy(dtype=float)
    valid = np.isfinite(hh_px) & np.isfinite(ttf_px) & np.isfinite(jkm_px)
    hh_px, ttf_px, jkm_px = hh_px[valid], ttf_px[valid], jkm_px[valid]
    if len(hh_px) < 2:
        return {"hh": np.array([]), "ttf": np.array([]), "jkm": np.array([])}

    return {
        "hh": np.diff(np.log(hh_px)),
        "ttf": np.diff(np.log(ttf_px)),
        "jkm": np.diff(np.log(jkm_px)),
    }


def historical_vol_corr(
    tables, D, months_forward: int, route: str,
    window_days: int = DEFAULT_HISTORICAL_WINDOW_DAYS,
    _dates: Optional[list] = None,
) -> VolCorrInputs:
    """Realized (annualised, sqrt(252)) vol/correlation of the route's
    revenue index vs HH, from the last `window_days` calendar days of
    actual price history at the M+{months_forward} tenor column.

    _dates: pre-computed _historical_window_dates(tables, D, window_days)
    for callers pricing many tenors/routes at once (avoids recomputing
    the 3-way date intersection on every call); computed fresh if omitted.
    """
    route = route.strip().lower()
    col = f"c{max(months_forward, 1)}"
    dates = _dates if _dates is not None else _historical_window_dates(tables, D, window_days)
    rets = _returns_for_column(tables, dates, col)
    n = len(rets["hh"])
    detail = f"historical, {window_days}d window, {col}, n={n} return(s)"
    if n < 2:
        return VolCorrInputs(None, None, None, detail + " (insufficient history)")

    hh_ret = rets["hh"]
    revenue_ret = rets["ttf"] if route == "europe" else rets["jkm"]

    vol_cost = float(np.std(hh_ret, ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR)
    vol_revenue = float(np.std(revenue_ret, ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR)
    if np.std(hh_ret) == 0.0 or np.std(revenue_ret) == 0.0:
        corr = 0.0
    else:
        corr = float(np.corrcoef(revenue_ret, hh_ret)[0, 1])
    return VolCorrInputs(vol_revenue, vol_cost, corr, detail)


def route_spread_option(
    row, params: model.Params, tables, D, route: str,
    source: VolSource, window_days: int = DEFAULT_HISTORICAL_WINDOW_DAYS,
    _historical_dates: Optional[list] = None,
) -> SpreadOptionResult:
    """One route's SpreadOptionResult for one strip_df row.

    _historical_dates: only used when source="historical"; pass
    _historical_window_dates(tables, D, window_days) when pricing many
    rows/routes at the same (tables, D, window_days) to avoid recomputing
    that 3-way date intersection on every call (see
    intrinsic_extrinsic_strip). Computed fresh if omitted.
    """
    route = route.strip().lower()
    if route not in ("europe", "asia"):
        raise ValueError(f"unknown route {route!r}")

    if route == "europe":
        revenue_0 = float(row["ttf_usd"]) - float(row["eu_boiloff_cost"])
        margin = float(row["eu_margin"])
    else:
        revenue_0 = float(row["JKM"]) - float(row["asia_boiloff_cost"])
        margin = float(row["asia_margin"])
    cost_0 = float(row["HH"]) * params.hh_grossup
    strike = revenue_0 - cost_0 - margin

    D = pd.Timestamp(D)
    load_month = pd.Timestamp(row["load_month"])
    months_forward = max((load_month.year - D.year) * 12 + (load_month.month - D.month), 0)
    t = max((_representative_load_date(load_month) - D).days / 365.25, 0.0)

    if source == "tab":
        vc = tab_vol_corr(tables.vol, months_forward, route)
    elif source == "historical":
        vc = historical_vol_corr(
            tables, D, months_forward, route, window_days=window_days, _dates=_historical_dates
        )
    else:
        raise ValueError(f"unknown vol/correlation source {source!r}")

    return price_spread_option(revenue_0, cost_0, strike, t, vc)


def intrinsic_extrinsic_strip(
    strip_df: pd.DataFrame, params: model.Params, tables, D,
    source: VolSource, window_days: int = DEFAULT_HISTORICAL_WINDOW_DAYS,
) -> pd.DataFrame:
    """One row per strip_df row: load_month/month_label plus
    eu_intrinsic/eu_extrinsic/asia_intrinsic/asia_extrinsic ($/MMBtu).
    extrinsic is NaN wherever the selected source could not supply
    complete vol/correlation inputs (Asia in "tab" mode today -- see
    this module's docstring) rather than silently zero."""
    historical_dates = _historical_window_dates(tables, D, window_days) if source == "historical" else None
    rows = []
    for _, row in strip_df.iterrows():
        eu = route_spread_option(row, params, tables, D, "europe", source, window_days, historical_dates)
        asia = route_spread_option(row, params, tables, D, "asia", source, window_days, historical_dates)
        rows.append(dict(
            load_month=row["load_month"], month_label=row["month_label"],
            eu_intrinsic=eu.intrinsic, eu_extrinsic=eu.extrinsic,
            asia_intrinsic=asia.intrinsic, asia_extrinsic=asia.extrinsic,
        ))
    return pd.DataFrame(rows)
