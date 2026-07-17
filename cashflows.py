"""
cashflows.py -- canonical price-dependent cash-flow layer (R6.1/R6.3,
increments A-B of docs/R6_RISK_REBUILD_PLAN.md).

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

Two-layer split (plan sect 2, increment B): `legacy_cargo_quantities()`
produces the PRICE-INDEPENDENT quantities -- a pure function of Params
plus the load month's calendar year (needed only for the ETS phase).
`legacy_cargo_cashflows()` is a thin D-dependent ASSEMBLY wrapper around
it: snap prices, resolve the load month's year, attach base_prices. The
split matters because quantities are Params-hash-cacheable while
assembly (phase-by-year, JKM tenor shift, snapped bases) is cheap
D-dependent arithmetic that must be redone every call, never cached --
this is what lets risk._vectorized_reprice (called ~2,000x by the
backtest) build a month's quantities ONCE and reuse them across every
scenario, instead of re-snapping tables per scenario.

As of increment B (R6.3), risk.py consumes this layer:
risk._vectorized_reprice() evaluates legacy_cargo_quantities()'s output
against scenario price arrays via CargoExposure.value_matrix(), and
risk.analytic_deltas() derives its six sensitivities from
legacy_cargo_cashflows()'s quantity_on() calls -- both replacing what
used to be independently duplicated day-count/fuel arithmetic in
risk.py. model.strip() itself is untouched (still the ground truth
legacy_cargo_cashflows() is pinned against).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    label ("Europe"/"Asia"), `month_index` is this exposure's own
    0-based load-month index, and `base_prices` is the snapped price for
    every factor referenced by those cash flows -- what parity tests
    (and any caller wanting the deterministic, zero-shock value)
    evaluate `value()` at.

    Individual CashFlow.month_index fields are NOT guaranteed to match
    this exposure's month_index: legacy_cargo_quantities() (Params-only,
    no month_index available -- see its docstring) tags its output with
    a placeholder, and a caller assembling many exposures per call (e.g.
    risk._vectorized_reprice, one per load month per call) may reuse
    that output as-is rather than re-tagging every CashFlow, since
    evaluation never reads the per-cash-flow field. legacy_cargo_cashflows()
    re-tags it anyway, for callers that do care."""

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


def legacy_cargo_quantities(params: Params, load_month_year: int) -> tuple[list[CashFlow], list[CashFlow]]:
    """Price-independent half of the Step 6 decomposition (plan sect 2's
    Layer 1/2 split): every quantity below is a pure function of `params`
    plus the load month's calendar YEAR -- the year is needed only for
    phase_for_year(), which the ETS term folds into its FX-linear
    quantity (see RiskFactor's docstring for why ETS is FX-linear today
    rather than an (EUA, FX) bilinear product). No `tables`, no `D`, no
    snapping, no month_index: this is what lets a caller build a
    Params-hash-keyable quantity cache and, in risk._vectorized_reprice,
    build the 12 months' worth of quantities ONCE per call regardless of
    scenario count, instead of rebuilding them per scenario (plan sect 2).

    `CashFlow.month_index` on every returned term is a placeholder (0):
    this function has no load-month index to attach (only a calendar
    year -- multiple load months can share a year). Evaluation
    (CargoExposure.value/value_matrix/quantity_on) never reads
    CashFlow.month_index, so the placeholder is inert, not lossy (see
    CargoExposure's docstring). legacy_cargo_cashflows() below re-tags
    it to the real month_index for callers that do care.

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

    Returns (europe_cash_flows, asia_cash_flows) -- bare lists, not yet
    wrapped in a CargoExposure (no route/month_index/base_prices
    metadata attached; see legacy_cargo_cashflows()).
    """
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

    phase = phase_for_year(load_month_year)
    MI = 0  # placeholder month_index -- see docstring above

    europe = [
        CashFlow((RiskFactor.TTF, RiskFactor.FX), MI,
                 cargo * (1 - eu_bo_frac) / 3.412,
                 "TTF revenue net of boil-off, x FX"),
        CashFlow((RiskFactor.HH,), MI,
                 -cargo * params.hh_grossup, "HH procurement (grossed up)"),
        CashFlow((RiskFactor.FX,), MI,
                 -params.co2_eu_ets_tonnes * params.eua_price * phase,
                 "ETS (EUA price folded into the quantity -- see docstring)"),
        CashFlow((RiskFactor.CHARTER,), MI, -europe_rt, "charter hire"),
        CashFlow((RiskFactor.VLSFO,), MI, -fixed_fuel_eu, "bunker fuel"),
        CashFlow((), MI,
                 -cargo * (params.liquefaction_toll + params.pipeline + params.loading
                           + params.eu_regas_port + params.other_cost),
                 "fixed fees: liquefaction + pipeline + loading + regas + other"),
    ]

    asia = [
        CashFlow((RiskFactor.JKM,), MI,
                 cargo * (1 - asia_bo_frac), "JKM revenue net of boil-off"),
        CashFlow((RiskFactor.HH,), MI,
                 -cargo * params.hh_grossup, "HH procurement (grossed up)"),
        CashFlow((RiskFactor.CHARTER,), MI, -asia_rt, "charter hire"),
        CashFlow((RiskFactor.VLSFO,), MI, -fixed_fuel_asia, "bunker fuel"),
        CashFlow((), MI,
                 -cargo * (params.liquefaction_toll + params.pipeline + params.loading
                           + params.asia_port_cost + params.other_cost)
                 - params.panama_toll_roundtrip,
                 "fixed fees + Panama toll roundtrip"),
    ]

    return europe, asia


def legacy_cargo_cashflows(D, tables, params: Params,
                           month_index: int) -> tuple[CargoExposure, CargoExposure]:
    """D-dependent ASSEMBLY wrapper around legacy_cargo_quantities()
    (plan sect 2's Layer 1/2 split): snaps prices for ONE load month via
    _snap_month_prices() (Step 1-3 of model.strip()), calls
    legacy_cargo_quantities() with that load month's calendar year to
    get the Params-only quantities, then attaches base_prices -- the
    snapped bases parity tests (and any caller wanting the
    deterministic, zero-shock value) evaluate `value()` at.

    Returns (europe_exposure, asia_exposure); CargoExposure.value(base_prices)
    reproduces strip()'s eu_cargo/asia_cargo for this month to
    floating-point reassociation error (tests/test_cashflows.py group b
    pins this against the real workbook for both legacy and
    operating-default Params; worst observed error ~1e-8, target ~1e-9
    per the plan) -- unaffected by the quantities/assembly split, since
    the underlying arithmetic did not move, only which function performs
    it. See legacy_cargo_quantities()'s docstring for the full per-factor
    derivation.

    Each returned CashFlow has its month_index re-tagged (via
    dataclasses.replace) from legacy_cargo_quantities()'s placeholder to
    this call's actual month_index, matching CargoExposure.month_index --
    legacy_cargo_quantities() itself cannot do this (it is never given a
    month_index, only a calendar year, which is not the same thing).
    """
    ctx = _snap_month_prices(D, tables, params, month_index)
    europe_flows, asia_flows = legacy_cargo_quantities(params, ctx["L"].year)

    europe = CargoExposure(
        route="Europe",
        month_index=month_index,
        cash_flows=[replace(cf, month_index=month_index) for cf in europe_flows],
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
        cash_flows=[replace(cf, month_index=month_index) for cf in asia_flows],
        base_prices={
            RiskFactor.JKM: ctx["jkm_l1"],
            RiskFactor.HH: ctx["hh_l"],
            RiskFactor.CHARTER: ctx["charter"],
            RiskFactor.VLSFO: params.vlsfo_price,
        },
    )

    return europe, asia
