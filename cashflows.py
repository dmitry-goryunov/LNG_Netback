"""
cashflows.py -- canonical price-dependent cash-flow layer (R6.1, increment
A of docs/R6_RISK_REBUILD_PLAN.md).

Pure module: no Streamlit import, no risk.py import. Importing model.py
(and pandas/numpy) is fine -- model.py is itself pure, headless code.

This module replaces "duplicate model.strip()'s route formula, then prove
equality" with "generate the exposures once, evaluate them everywhere"
(plan sect 1-2). `legacy_cargo_cashflows()` decomposes model.strip()'s
Step 6 Europe/Asia margin arithmetic for one load month into a list of
per-factor CashFlow terms whose sum, evaluated at that month's snapped
base prices, reproduces strip()'s eu_cargo/asia_cargo EXACTLY (to
floating-point reassociation error -- see tests/test_cashflows.py group b,
which pins this against the real workbook for both legacy and
operating-default Params, worst observed error ~1e-8).

Nothing in strip() or risk.py changes here: this is an additive, parallel
decomposition only. Increment B is what makes the vectorised repricer
actually consume this layer instead of model.strip().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from model import Params, contract_calendar, fx_curve, phase_for_year, snap, _month_add


class RiskFactor(str, Enum):
    """Price factors the cash-flow layer can reference. No composite
    members: a bilinear term (e.g. TTF x FX) is expressed structurally as
    a two-element CashFlow.factors tuple, not as a combined enum value.
    This is what lets a term change shape later without a schema change
    -- e.g. ETS moves from FX-linear today (the EUA price is folded into
    the quantity, since EUA has no history yet) to an (EUA, FX) bilinear
    product once R6.5b gives EUA its own price series (plan sect 2)."""

    TTF = "TTF"
    JKM = "JKM"
    HH = "HH"
    FX = "FX"
    CHARTER = "CHARTER"
    VLSFO = "VLSFO"
    EUA = "EUA"


@dataclass(frozen=True)
class CashFlow:
    """One price-dependent term: quantity x price[f1] x price[f2] x ...
    over `factors`. An empty `factors` tuple is a price-independent
    constant (quantity alone, no multiplication); a single-element tuple
    is linear; two elements is the bilinear product form Europe revenue
    and today's ETS term need (plan sect 2). Quantities are
    price-INDEPENDENT -- functions of Params (and, for ETS, the load
    month's calendar year) only, never of a scenario price -- by
    construction of this module's producer functions.

    `settle_date` is reserved for R5 (dated cash flows / discounting,
    not built yet -- plan sect 4); always None until then."""

    factors: tuple[RiskFactor, ...]
    month_index: int
    quantity: float
    label: str
    settle_date: Optional[pd.Timestamp] = None


@dataclass
class CargoExposure:
    """One route's one load month, as a bundle of CashFlow terms plus the
    metadata needed to evaluate or describe them: `route` is a display
    label ("Europe"/"Asia"), `month_index` is the same 0-based load-month
    index every one of its cash flows carries, and `base_prices` is the
    snapped price for every factor referenced by those cash flows --
    what parity tests (and any caller wanting the deterministic,
    zero-shock value) evaluate `value()` at."""

    route: str
    month_index: int
    cash_flows: list[CashFlow] = field(default_factory=list)
    base_prices: dict[RiskFactor, float] = field(default_factory=dict)

    def value(self, prices: dict[RiskFactor, float]) -> float:
        """Sum of quantity x product(prices[f] for f in factors) over
        every cash flow. A factor referenced by some cash flow but
        missing from `prices` raises KeyError -- fail loud, no silent
        defaulting to base price or to zero."""
        total = 0.0
        for cf in self.cash_flows:
            term = cf.quantity
            for f in cf.factors:
                term *= prices[f]
            total += term
        return total

    def value_matrix(self, prices: dict[RiskFactor, np.ndarray]) -> np.ndarray:
        """Vectorised form of `value`: every array in `prices` is 1-D of
        the same length n (one entry per scenario/date), returns shape
        (n,). Month-agnostic by design -- this exposure carries
        month_index only as metadata; the CALLER slices the right
        month's column out of a larger scenario price matrix before
        calling this (increment B does that slicing; no 12-month matrix
        logic lives here)."""
        n = len(next(iter(prices.values()))) if prices else 1
        total = np.zeros(n, dtype=float)
        for cf in self.cash_flows:
            term = np.full(n, cf.quantity, dtype=float)
            for f in cf.factors:
                term = term * np.asarray(prices[f], dtype=float)
            total = total + term
        return total

    def quantity_on(self, factors: tuple[RiskFactor, ...]) -> float:
        """Summed quantity across cash flows whose factor tuple matches
        `factors` exactly, order-insensitive (e.g. (TTF, FX) and
        (FX, TTF) address the same term). This is what delta derivation
        reads: for a single-factor term the result IS the analytic delta
        coefficient (multiply by shock size); for a product term the
        caller must still multiply by the co-factor's base price (see
        tests/test_cashflows.py group c, and risk.analytic_deltas, which
        this is pinned against)."""
        key = _normalized(factors)
        return sum(cf.quantity for cf in self.cash_flows if _normalized(cf.factors) == key)


def _normalized(factors: tuple[RiskFactor, ...]) -> tuple[RiskFactor, ...]:
    return tuple(sorted(factors, key=lambda f: f.name))


def _snap_month_prices(D, tables, params: Params, month_index: int) -> dict:
    """Step 1-3 snap/contract-calendar/fx setup for ONE load month, built
    from model.py's own public snap()/contract_calendar()/fx_curve()/
    phase_for_year() plus _month_add() (a module-private helper risk.py's
    own _base_context() also reaches into model.py for -- same
    established pattern, reused here). Mirrors risk._base_context()
    without importing risk, per plan sect 6.A. Always uses fx_curve()
    (the 3-point spot/o6/o1 curve), matching strip()'s n_months<=12 path
    exactly -- the 12 legacy load months this module targets never take
    strip()'s n_months>12 / fx_curve_multi() branch."""
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
    return dict(L=L, charter=charter, fx_l=fx_l, hh_l=hh_l, ttf_l=ttf_l, jkm_l1=jkm_l1)


def legacy_cargo_cashflows(D, tables, params: Params,
                           month_index: int) -> tuple[CargoExposure, CargoExposure]:
    """Decompose model.strip()'s Step 6 arithmetic for ONE load month
    (model.py's `for i, L in enumerate(months)` body, at i = month_index,
    roughly lines 424-467) into per-factor CashFlow terms. Returns
    (europe_exposure, asia_exposure); CargoExposure.value(base_prices)
    reproduces strip()'s eu_cargo/asia_cargo for this month to
    floating-point reassociation error (tests/test_cashflows.py group b
    pins this against the real workbook for both legacy and
    operating-default Params; worst observed error ~1e-8, target ~1e-9
    per the plan).

    Derivation (algebraic expansion of eu_margin*cargo / asia_margin*cargo;
    every quantity below is `cargo x` a piece of strip()'s bracketed
    expression -- verified numerically against a live strip() call, not
    just derived on paper):

    Europe -- strip() computes `ttf_usd = ttf_l * fx_l / 3.412` once and
    charges it TWICE: once as revenue, once (scaled by eu_bo_frac) as the
    boil-off cost. The two TTF x FX occurrences net to a SINGLE bilinear
    cash flow of quantity `cargo * (1 - eu_bo_frac) / 3.412` -- not two
    separate cash flows on the same factor pair. `proc` contributes the
    HH-linear term (quantity -cargo * hh_grossup) plus part of the
    constant (liquefaction_toll + pipeline); `eu_ship` (already
    `.../cargo` in strip) contributes the CHARTER-linear term (quantity
    -europe_rt, which itself already includes loading_days -- strip()
    line ~419) and the VLSFO-linear term (quantity -fixed_fuel_eu, which
    includes port fuel over europe_port_days + loading_days); `ets`
    (already `.../cargo`) contributes an FX-linear term with the EUA
    price and ETS phase folded INTO the quantity today, since EUA has no
    price history yet (R6.5b re-splits this into an (EUA, FX) product
    once it does -- plan sect 2); loading/eu_regas_port/other_cost are
    the remaining constant pieces. Europe has no Panama toll (Asia-only).

    Asia -- `jkm_l1 * (1 - asia_bo_frac)` is the JKM-linear revenue term.
    `asia_cost_exbo` contributes the same HH-linear + constant fee split
    as Europe's `proc`, plus `as_ship` (already `.../cargo`) contributing
    a CHARTER-linear term (quantity -asia_rt, i.e. params.asia_rt_days
    directly -- unlike Europe's derived europe_rt) and a VLSFO-linear
    term (quantity -fixed_fuel_asia, where asia_ballast = asia_rt -
    asia_laden - asia_port - loading_days) plus the Panama toll as a
    constant; asia_port_cost/other_cost are the remaining constant
    pieces. Asia has no FX and no ETS term in strip() -- verified against
    model.py's source, not assumed from memory.
    """
    ctx = _snap_month_prices(D, tables, params, month_index)
    cargo = params.cargo_size
    bo = params.boil_off_rate
    load_days = params.loading_days

    eu_laden = params.europe_laden_days
    eu_ballast = params.europe_ballast_days
    eu_port = params.europe_port_days
    europe_rt = eu_laden + eu_ballast + eu_port + load_days

    asia_rt = params.asia_rt_days
    asia_laden = params.asia_laden_days
    asia_port = params.asia_port_days
    asia_ballast = asia_rt - asia_laden - asia_port - load_days

    eu_bo_frac = bo * eu_laden
    asia_bo_frac = bo * asia_laden

    fixed_fuel_eu = (params.residual_laden_vlsfo * eu_laden
                      + params.ballast_fuel * eu_ballast
                      + params.port_fuel_rate * (eu_port + load_days))
    fixed_fuel_asia = (params.residual_laden_vlsfo * asia_laden
                        + params.ballast_fuel * asia_ballast
                        + params.port_fuel_rate * (asia_port + load_days))

    phase = phase_for_year(ctx["L"].year)

    europe = CargoExposure(
        route="Europe",
        month_index=month_index,
        cash_flows=[
            CashFlow((RiskFactor.TTF, RiskFactor.FX), month_index,
                     cargo * (1 - eu_bo_frac) / 3.412,
                     "TTF revenue net of boil-off, x FX"),
            CashFlow((RiskFactor.HH,), month_index,
                     -cargo * params.hh_grossup, "HH procurement (grossed up)"),
            CashFlow((RiskFactor.FX,), month_index,
                     -params.co2_eu_ets_tonnes * params.eua_price * phase,
                     "ETS (EUA price folded into the quantity -- see docstring)"),
            CashFlow((RiskFactor.CHARTER,), month_index, -europe_rt, "charter hire"),
            CashFlow((RiskFactor.VLSFO,), month_index, -fixed_fuel_eu, "bunker fuel"),
            CashFlow((), month_index,
                     -cargo * (params.liquefaction_toll + params.pipeline + params.loading
                               + params.eu_regas_port + params.other_cost),
                     "fixed fees: liquefaction + pipeline + loading + regas + other"),
        ],
        base_prices={
            RiskFactor.TTF: ctx["ttf_l"],
            RiskFactor.HH: ctx["hh_l"],
            RiskFactor.FX: ctx["fx_l"],
            RiskFactor.CHARTER: ctx["charter"],
            RiskFactor.VLSFO: params.vlsfo_price,
        },
    )

    asia = CargoExposure(
        route="Asia",
        month_index=month_index,
        cash_flows=[
            CashFlow((RiskFactor.JKM,), month_index,
                     cargo * (1 - asia_bo_frac), "JKM revenue net of boil-off"),
            CashFlow((RiskFactor.HH,), month_index,
                     -cargo * params.hh_grossup, "HH procurement (grossed up)"),
            CashFlow((RiskFactor.CHARTER,), month_index, -asia_rt, "charter hire"),
            CashFlow((RiskFactor.VLSFO,), month_index, -fixed_fuel_asia, "bunker fuel"),
            CashFlow((), month_index,
                     -cargo * (params.liquefaction_toll + params.pipeline + params.loading
                               + params.asia_port_cost + params.other_cost)
                     - params.panama_toll_roundtrip,
                     "fixed fees + Panama toll roundtrip"),
        ],
        base_prices={
            RiskFactor.JKM: ctx["jkm_l1"],
            RiskFactor.HH: ctx["hh_l"],
            RiskFactor.CHARTER: ctx["charter"],
            RiskFactor.VLSFO: params.vlsfo_price,
        },
    )

    return europe, asia
