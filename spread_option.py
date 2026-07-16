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

Tenor alignment (fixed after review -- an earlier version used the same
c{months_forward} column for BOTH legs, which overstated front-month
extrinsic ~4x): the vol/correlation inputs are taken from the SAME
contracts the strip's prices come from. model.strip() prices TTF at
contract c{i+1} (delivery month L) and JKM at c{i+2-s} (delivery L+1,
with model.contract_calendar's mid-month roll shift s), so historical
mode computes TTF returns on c{months_forward}, JKM returns on
c{months_forward + 1 - s}, and the correlation BETWEEN those two series
-- not two copies of the same column. This matters most at the front of
the curve, where the expiring JKM c1 is noisy and its correlation to TTF
is far lower than the correctly-paired contracts' (measured 0.25 vs 0.80
on the 2026-07-08 snapshot; extrinsic $0.74 -> $0.18/MMBtu for M1). Tab
mode reads Volatility JKM at delivery tenor months_forward+1 and
Volatility TTF at months_forward for the same reason; Correlation
TTF/JKM is read at months_forward (the sheet quotes one correlation per
tenor row -- the cross-delivery-month pairing doesn't exist as its own
column, so the nearer tenor's figure is used and this choice is
documented rather than hidden).
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
    vol_jkm: Optional[float]
    vol_ttf: Optional[float]
    correlation: Optional[float]
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
            vol_jkm=vc.vol_jkm, vol_ttf=vc.vol_ttf, correlation=vc.correlation,
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
        vol_jkm=vc.vol_jkm, vol_ttf=vc.vol_ttf, correlation=vc.correlation,
        option_value=option_value, extrinsic=option_value - intrinsic,
        time_to_expiry_years=time_to_expiry_years, detail=vc.source_detail,
    )


def tab_vol_corr(vol_table: Optional[pd.DataFrame], months_forward: int) -> VolCorrInputs:
    """Reads data.load_volatilities()'s table for a load month
    `months_forward` calendar months out. The TTF leg delivers that month
    (tenor = months_forward) but the JKM leg the strip prices delivers
    L+1 (tenor = months_forward + 1) -- each vol is read at its own leg's
    delivery tenor, not one shared row (see module docstring). The
    sheet's Correlation TTF/JKM is one column per tenor row with no
    cross-delivery-month pairing available, so it is read at the nearer
    (TTF) tenor. Tenors clamp to the table's available range (flat
    extrapolation beyond the last quoted point, the same convention used
    elsewhere in this app for curves shorter than the 36-month strip)."""
    if vol_table is None or len(vol_table) == 0:
        return VolCorrInputs(None, None, None, "volatilities tab: sheet not loaded")

    def _tenor(m):
        return min(max(m, vol_table.index.min()), vol_table.index.max())

    ttf_tenor = _tenor(months_forward)
    jkm_tenor = _tenor(months_forward + 1)
    ttf_row = vol_table.loc[ttf_tenor]
    jkm_row = vol_table.loc[jkm_tenor]

    def _clean(x):
        return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

    vol_jkm = _clean(jkm_row.get("vol_JKM"))
    vol_ttf = _clean(ttf_row.get("vol_TTF"))
    corr = ttf_row.get("corr_TTF_JKM")
    corr = corr if corr is not None else ttf_row.get("corr_JKM_TTF")
    corr = _clean(corr)

    detail = f"volatilities tab, TTF M+{ttf_tenor} / JKM M+{jkm_tenor}"
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


def _paired_returns(tables, dates: list, ttf_col: str, jkm_col: str) -> dict:
    """Log returns of tables.ttf's `ttf_col` and tables.jkm's `jkm_col`
    (independent columns -- the two legs live on different contracts, see
    module docstring), paired on the same trading days so the correlation
    between them is valid."""
    if len(dates) < 3 or ttf_col not in tables.ttf.columns or jkm_col not in tables.jkm.columns:
        return {"ttf": np.array([]), "jkm": np.array([])}

    ttf_px = tables.ttf.set_index("date").reindex(dates)[ttf_col].to_numpy(dtype=float)
    jkm_px = tables.jkm.set_index("date").reindex(dates)[jkm_col].to_numpy(dtype=float)
    valid = np.isfinite(ttf_px) & np.isfinite(jkm_px)
    ttf_px, jkm_px = ttf_px[valid], jkm_px[valid]
    if len(ttf_px) < 2:
        return {"ttf": np.array([]), "jkm": np.array([])}

    return {"ttf": np.diff(np.log(ttf_px)), "jkm": np.diff(np.log(jkm_px))}


def historical_vol_corr(
    tables, D, ttf_contract: int, jkm_contract: int,
    window_days: int = DEFAULT_HISTORICAL_WINDOW_DAYS,
    _dates: Optional[list] = None,
) -> VolCorrInputs:
    """Realized (annualised, sqrt(252)) vol of TTF contract c{ttf_contract}
    and JKM contract c{jkm_contract}, plus the correlation BETWEEN those
    two return series, from the last `window_days` calendar days of actual
    price history. Callers pass the same contract indices model.strip()
    prices (TTF c{i+1}, JKM c{i+2-s}) -- see month_spread_option().

    _dates: pre-computed _historical_window_dates(tables, D, window_days)
    for callers pricing many tenors at once (avoids recomputing the 2-way
    date intersection on every call); computed fresh if omitted.
    """
    ttf_col = f"c{max(ttf_contract, 1)}"
    jkm_col = f"c{max(jkm_contract, 1)}"
    dates = _dates if _dates is not None else _historical_window_dates(tables, D, window_days)
    rets = _paired_returns(tables, dates, ttf_col, jkm_col)
    n = len(rets["ttf"])
    detail = f"historical, {window_days}d window, TTF {ttf_col} / JKM {jkm_col}, n={n} return(s)"
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
        # The same contract indices model.strip() prices this row from:
        # strip row i (0-based, load month L = F + i) uses TTF c{i+1} and
        # JKM c{i+2-s}. F is always month(D)+1 (model.contract_calendar),
        # so i = months_forward - 1.
        _, s = model.contract_calendar(D)
        i = max(months_forward - 1, 0)
        vc = historical_vol_corr(
            tables, D, ttf_contract=i + 1, jkm_contract=i + 2 - s,
            window_days=window_days, _dates=_historical_dates,
        )
    else:
        raise ValueError(f"unknown vol/correlation source {source!r}")

    return price_exchange_option(jkm_0, ttf_0, t, vc)


def intrinsic_extrinsic_strip(
    strip_df: pd.DataFrame, tables, D,
    source: VolSource, window_days: int = DEFAULT_HISTORICAL_WINDOW_DAYS,
) -> pd.DataFrame:
    """One row per strip_df row: load_month/month_label, the vol_jkm/
    vol_ttf/correlation actually used to price that month's option (the
    inputs, not just the outputs -- so a caller can see *why* extrinsic
    is what it is without opening the single-month detail view), and
    intrinsic/extrinsic ($/MMBtu, the JKM-vs-TTF diversion option's
    value). vol_jkm/vol_ttf/correlation/extrinsic are all NaN together
    wherever the selected source could not supply complete inputs for
    that tenor, rather than silently zero."""
    historical_dates = _historical_window_dates(tables, D, window_days) if source == "historical" else None
    rows = []
    for _, row in strip_df.iterrows():
        result = month_spread_option(row, tables, D, source, window_days, historical_dates)
        rows.append(dict(
            load_month=row["load_month"], month_label=row["month_label"],
            vol_jkm=result.vol_jkm, vol_ttf=result.vol_ttf, correlation=result.correlation,
            intrinsic=result.intrinsic, extrinsic=result.extrinsic,
        ))
    return pd.DataFrame(rows)
