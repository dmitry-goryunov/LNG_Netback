"""
spread_option.py -- intrinsic/extrinsic value of the JKM-vs-TTF
diversion optionality, for the Forward-strip page.

Framing: a cargo is delivered to TTF (Europe) as the base case; the
"option" is the value of diverting to JKM (Asia) instead when JKM is
higher. This is a zero-strike spread option (a Margrabe exchange
option): the claim pays max(JKM - TTF, 0) at expiry.

    intrinsic = max(JKM - TTF, 0)         -- today's forward view, no uncertainty
    extrinsic = option_value - intrinsic  -- time value of the diversion option (>= 0 always)

JKM and TTF here are the same $/MMBtu revenue references model.strip()
already computes (row["JKM"], row["ttf_usd"]) -- not the full netback
margins (no procurement/shipping/ETS/etc. netted out). JKM and TTF are
both strictly positive market prices (unlike a netback margin, which can
go negative), so this is priced with Margrabe's original 1978 lognormal
exchange-option formula, the standard textbook approach for a zero-strike
option to exchange one asset for another.

Two vol/correlation sources (user-selected in the UI):

  - "historical": realized vol/correlation from tables.ttf/jkm's actual
    daily price history, over a rolling window measured in CALENDAR days
    (default 60) ending at the valuation date.
  - "tab": data.load_volatilities()'s pre-supplied vol/correlation table
    (LNG history.xlsx's 'volatilities' sheet), indexed by tenor (spot,
    M+1, M+2, ...) -- Volatility TTF, Volatility JKM and Correlation
    TTF/JKM are exactly what this formula needs, and the sheet has all
    three.

FX volatility is deliberately not modelled as a separate risk factor
(neither source has FX data): "TTF" here means the already-USD-converted
ttf_usd revenue reference, and its fractional vol is approximated as
equal to raw TTF's own fractional vol (FX vol is assumed small relative
to TTF/JKM vol) -- a stated simplification, not an oversight. JKM needs
no such approximation; it is already USD.

Historical mode's constant-maturity tenor convention (c{months_forward})
matches model.strip()'s own c{i+1} indexing for TTF exactly; for JKM it
does *not* replicate model.strip()'s contract-calendar shift
(model.contract_calendar's `s`) -- a deliberate simplification so
historical and tab-mode tenors share one convention throughout this
module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252.0
DEFAULT_HISTORICAL_WINDOW_DAYS = 60

VolSource = Literal["historical", "tab"]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _representative_load_date(load_month) -> pd.Timestamp:
    m = pd.Timestamp(load_month)
    return pd.Timestamp(year=m.year, month=m.month, day=15)


@dataclass(frozen=True)
class VolCorrInputs:
    """JKM's and TTF's own (fractional, annualised) volatility and their
    correlation, for one forward month. None fields mean "could not be
    determined from the selected source" -- callers must treat that as
    "cannot price the option," not silently substitute a default."""
    vol_jkm: Optional[float]
    vol_ttf: Optional[float]
    correlation: Optional[float]
    source_detail: str


@dataclass(frozen=True)
class SpreadOptionResult:
    jkm_0: float
    ttf_0: float
    intrinsic: float
    option_value: Optional[float]
    extrinsic: Optional[float]
    time_to_expiry_years: float
    detail: str


def price_exchange_option(
    jkm_0: float, ttf_0: float, time_to_expiry_years: float, vc: VolCorrInputs,
) -> SpreadOptionResult:
    """Margrabe (1978) price of a claim paying max(JKM - TTF, 0) at
    expiry: a zero-strike option to exchange a TTF-priced unit for a
    JKM-priced one. JKM/TTF are assumed lognormal (both strictly
    positive market prices) with the given fractional vols and
    correlation:

        sigma^2 = vol_jkm^2 + vol_ttf^2 - 2*correlation*vol_jkm*vol_ttf
        d1 = [ln(jkm_0/ttf_0) + 0.5*sigma^2*T] / (sigma*sqrt(T))
        d2 = d1 - sigma*sqrt(T)
        option_value = jkm_0*N(d1) - ttf_0*N(d2)
    """
    if jkm_0 <= 0 or ttf_0 <= 0:
        raise ValueError(f"jkm_0 and ttf_0 must be > 0 for a lognormal exchange option, got {jkm_0}, {ttf_0}")
    if time_to_expiry_years < 0:
        raise ValueError(f"time_to_expiry_years must be >= 0, got {time_to_expiry_years}")

    intrinsic = max(jkm_0 - ttf_0, 0.0)

    if vc.vol_jkm is None or vc.vol_ttf is None or vc.correlation is None:
        return SpreadOptionResult(
            jkm_0=jkm_0, ttf_0=ttf_0, intrinsic=intrinsic,
            option_value=None, extrinsic=None,
            time_to_expiry_years=time_to_expiry_years,
            detail=f"{vc.source_detail}: incomplete vol/correlation inputs",
        )

    sigma_sq = vc.vol_jkm ** 2 + vc.vol_ttf ** 2 - 2.0 * vc.correlation * vc.vol_jkm * vc.vol_ttf
    sigma_sq = max(sigma_sq, 0.0)  # guards float noise when |correlation| ~ 1
    sigma_t = math.sqrt(sigma_sq * time_to_expiry_years) if time_to_expiry_years > 0 else 0.0

    if sigma_t <= 0.0:
        option_value = intrinsic
    else:
        d1 = (math.log(jkm_0 / ttf_0) + 0.5 * sigma_sq * time_to_expiry_years) / sigma_t
        d2 = d1 - sigma_t
        option_value = jkm_0 * _norm_cdf(d1) - ttf_0 * _norm_cdf(d2)
        option_value = max(option_value, intrinsic)  # Margrabe price >= intrinsic; guards float noise

    return SpreadOptionResult(
        jkm_0=jkm_0, ttf_0=ttf_0, intrinsic=intrinsic,
        option_value=option_value, extrinsic=option_value - intrinsic,
        time_to_expiry_years=time_to_expiry_years, detail=vc.source_detail,
    )


def tab_vol_corr(vol_table: Optional[pd.DataFrame], months_forward: int) -> VolCorrInputs:
    """Reads data.load_volatilities()'s table at one tenor. Clamps
    months_forward to the table's available tenor range (flat
    extrapolation beyond the last quoted point, the same convention used
    elsewhere in this app for curves shorter than the 36-month strip)."""
    if vol_table is None or len(vol_table) == 0:
        return VolCorrInputs(None, None, None, "volatilities tab: sheet not loaded")

    tenor = min(max(months_forward, vol_table.index.min()), vol_table.index.max())
    row = vol_table.loc[tenor]

    def _clean(x):
        return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

    vol_jkm = _clean(row.get("vol_JKM"))
    vol_ttf = _clean(row.get("vol_TTF"))
    corr = row.get("corr_TTF_JKM")
    corr = corr if corr is not None else row.get("corr_JKM_TTF")
    corr = _clean(corr)

    detail = f"volatilities tab, M+{tenor}"
    if vol_jkm is None or vol_ttf is None or corr is None:
        detail += " (missing Volatility JKM / Volatility TTF / Correlation TTF-JKM column)"
    return VolCorrInputs(vol_jkm, vol_ttf, corr, detail)


def _historical_window_dates(tables, D, window_days: int) -> list:
    """Dates in the intersection of tables.ttf/jkm's own date columns,
    within the last `window_days` CALENDAR days up to and including D."""
    D = pd.Timestamp(D)
    start = D - pd.Timedelta(days=window_days)
    inter = sorted(set(tables.ttf["date"]) & set(tables.jkm["date"]))
    return [d for d in inter if start <= d <= D]


def _returns_for_column(tables, dates: list, col: str) -> dict:
    if len(dates) < 3 or col not in tables.ttf.columns or col not in tables.jkm.columns:
        return {"ttf": np.array([]), "jkm": np.array([])}

    ttf_px = tables.ttf.set_index("date").reindex(dates)[col].to_numpy(dtype=float)
    jkm_px = tables.jkm.set_index("date").reindex(dates)[col].to_numpy(dtype=float)
    valid = np.isfinite(ttf_px) & np.isfinite(jkm_px)
    ttf_px, jkm_px = ttf_px[valid], jkm_px[valid]
    if len(ttf_px) < 2:
        return {"ttf": np.array([]), "jkm": np.array([])}

    return {"ttf": np.diff(np.log(ttf_px)), "jkm": np.diff(np.log(jkm_px))}


def historical_vol_corr(
    tables, D, months_forward: int,
    window_days: int = DEFAULT_HISTORICAL_WINDOW_DAYS,
    _dates: Optional[list] = None,
) -> VolCorrInputs:
    """Realized (annualised, sqrt(252)) vol/correlation of JKM vs TTF,
    from the last `window_days` calendar days of actual price history at
    the M+{months_forward} tenor column.

    _dates: pre-computed _historical_window_dates(tables, D, window_days)
    for callers pricing many tenors at once (avoids recomputing the 2-way
    date intersection on every call); computed fresh if omitted.
    """
    col = f"c{max(months_forward, 1)}"
    dates = _dates if _dates is not None else _historical_window_dates(tables, D, window_days)
    rets = _returns_for_column(tables, dates, col)
    n = len(rets["ttf"])
    detail = f"historical, {window_days}d window, {col}, n={n} return(s)"
    if n < 2:
        return VolCorrInputs(None, None, None, detail + " (insufficient history)")

    ttf_ret, jkm_ret = rets["ttf"], rets["jkm"]
    vol_ttf = float(np.std(ttf_ret, ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR)
    vol_jkm = float(np.std(jkm_ret, ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR)
    if np.std(ttf_ret) == 0.0 or np.std(jkm_ret) == 0.0:
        corr = 0.0
    else:
        corr = float(np.corrcoef(jkm_ret, ttf_ret)[0, 1])
    return VolCorrInputs(vol_jkm, vol_ttf, corr, detail)


def month_spread_option(
    row, tables, D, source: VolSource,
    window_days: int = DEFAULT_HISTORICAL_WINDOW_DAYS,
    _historical_dates: Optional[list] = None,
) -> SpreadOptionResult:
    """One strip_df row's SpreadOptionResult: JKM delivered vs TTF
    delivered (as base), zero-strike exchange option.

    _historical_dates: only used when source="historical"; pass
    _historical_window_dates(tables, D, window_days) when pricing many
    rows at the same (tables, D, window_days) to avoid recomputing that
    date intersection on every call (see intrinsic_extrinsic_strip).
    """
    jkm_0 = float(row["JKM"])
    ttf_0 = float(row["ttf_usd"])

    D = pd.Timestamp(D)
    load_month = pd.Timestamp(row["load_month"])
    months_forward = max((load_month.year - D.year) * 12 + (load_month.month - D.month), 0)
    t = max((_representative_load_date(load_month) - D).days / 365.25, 0.0)

    if source == "tab":
        vc = tab_vol_corr(tables.vol, months_forward)
    elif source == "historical":
        vc = historical_vol_corr(tables, D, months_forward, window_days=window_days, _dates=_historical_dates)
    else:
        raise ValueError(f"unknown vol/correlation source {source!r}")

    return price_exchange_option(jkm_0, ttf_0, t, vc)


def intrinsic_extrinsic_strip(
    strip_df: pd.DataFrame, tables, D,
    source: VolSource, window_days: int = DEFAULT_HISTORICAL_WINDOW_DAYS,
) -> pd.DataFrame:
    """One row per strip_df row: load_month/month_label plus
    intrinsic/extrinsic ($/MMBtu, the JKM-vs-TTF diversion option's
    value). extrinsic is NaN wherever the selected source could not
    supply complete vol/correlation inputs, rather than silently zero."""
    historical_dates = _historical_window_dates(tables, D, window_days) if source == "historical" else None
    rows = []
    for _, row in strip_df.iterrows():
        result = month_spread_option(row, tables, D, source, window_days, historical_dates)
        rows.append(dict(
            load_month=row["load_month"], month_label=row["month_label"],
            intrinsic=result.intrinsic, extrinsic=result.extrinsic,
        ))
    return pd.DataFrame(rows)
