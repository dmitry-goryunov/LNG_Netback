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

Increment C (R6.1/R6.2/R6.4, plan sect 6.C) adds the PHYSICAL-engine
counterpart: `physical_cargo_quantities()`/`physical_cargo_cashflows()`
decompose decision.py's physical route valuation (decision._physical_
route_breakdown(), the same function that already drives the Decision
page) onto this module's factor set, one route at a time (unlike the
legacy pair-returning functions -- decision.py's own physical machinery
is one-route-at-a-time, e.g. _physical_route_value(row, params, route),
and this mirrors that shape). Importing decision.py (and, transitively,
physical.py/emissions.py) is fine here for the same reason importing
model.py always was: all four are pure, headless modules with no
Streamlit import -- decision.py does not import risk.py or this module,
so there is no import cycle. Both quantity producers now share a
Params-hash-keyed cache (see _BoundedParamsCache) -- this module stays
free of Streamlit, but is no longer free of module-level mutable state:
the cache is a pure performance optimisation (same inputs always produce
equal outputs, cached or not), not a source of nondeterminism.
"""

from __future__ import annotations

import dataclasses
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

import decision
import emissions
import physical
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


# ===========================================================================
# Increment C (plan sect 2, C.4): Params-hash-keyed QUANTITY-layer cache,
# shared by legacy_cargo_quantities() and physical_cargo_quantities(). Not
# used by the D-dependent ASSEMBLY wrappers (legacy_cargo_cashflows(),
# physical_cargo_cashflows()) -- assembly (snap, phase-by-year selection,
# base_prices, tenor) is cheap arithmetic that must be redone every call,
# never cached (plan sect 2's "backtest-poison" warning: a single
# Params-only cache of ASSEMBLED cash flows would serve a stale ETS phase
# as a backtest walks D across a year boundary).
# ===========================================================================

_QUANTITY_CACHE_MAXSIZE = 256


class _BoundedParamsCache:
    """LRU-eviction cache keyed by hashable tuples built from Params FIELD
    VALUES (see _params_field_key()), never object identity -- app.py
    mutates one Params instance in place across Streamlit reruns, so an
    identity-/id()-keyed cache would keep serving quantities computed
    before the mutation. `functools.lru_cache` can't decorate
    legacy_cargo_quantities()/physical_cargo_quantities() directly because
    Params is a plain (unfrozen) dataclass with no __hash__; the caller
    builds the hashable key explicitly instead and looks it up here.

    Bounded (not a plain unbounded dict) so a long-lived session that
    sweeps through many sidebar edits -- each producing a new field-value
    key -- doesn't grow this without limit; old entries are evicted
    least-recently-used. hits/misses mirror functools.lru_cache's own
    cache_info() convention, for test/debug introspection only (see
    quantity_cache_info() below) -- production code never reads them."""

    def __init__(self, maxsize: int = _QUANTITY_CACHE_MAXSIZE):
        self._maxsize = maxsize
        self._store: "OrderedDict[tuple, object]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple):
        try:
            value = self._store[key]
        except KeyError:
            self.misses += 1
            return None
        self._store.move_to_end(key)
        self.hits += 1
        return value

    def set(self, key: tuple, value) -> None:
        self._store[key] = value
        self._store.move_to_end(key)
        if len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()
        self.hits = 0
        self.misses = 0

    def info(self) -> dict:
        return dict(hits=self.hits, misses=self.misses, maxsize=self._maxsize, currsize=len(self._store))


_legacy_quantity_cache = _BoundedParamsCache()
_physical_quantity_cache = _BoundedParamsCache()


def _params_field_key(params: Params) -> tuple:
    """Hashable cache key built from Params FIELD VALUES, in
    dataclasses.fields() order -- never object identity (see
    _BoundedParamsCache's docstring: app.py mutates a single Params
    instance in place, so identity would serve stale quantities the
    instant any field changed without the object itself being replaced).
    Every Params field is a plain float/str/None scalar (verified against
    model.Params' current definition -- no list/dict fields), so the
    tuple is directly hashable with no further normalisation."""
    return tuple(getattr(params, f.name) for f in dataclasses.fields(params))


def quantity_cache_info() -> dict:
    """cache_info()-style introspection (mirrors functools.lru_cache's own
    convention) for both Params-hash-keyed quantity caches. Test/debug
    visibility only -- no production code path reads this."""
    return dict(legacy=_legacy_quantity_cache.info(), physical=_physical_quantity_cache.info())


def clear_quantity_caches() -> None:
    """Empties both quantity caches and resets their hit/miss counters.
    Not needed in production (bounded LRU eviction keeps memory in check
    on its own); exists so a test can start from a guaranteed-cold cache."""
    _legacy_quantity_cache.clear()
    _physical_quantity_cache.clear()


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

    Increment C (plan sect 2/C.4): Params-hash-keyed cache (recovers
    increment B's isolated ~2.5x repricer regression -- this function used
    to be rebuilt from scratch on every call regardless of whether the
    same (params, year) pair had already been seen; a backtest calls
    risk.historical_var() -- and hence, via _vectorized_reprice(), this --
    up to ~2,000 times per run against an UNCHANGED params, most of them
    revisiting one of at most 12 distinct years). The key includes
    load_month_year alongside the Params field values (_params_field_key())
    so a backtest walking D across a year boundary still recomputes on the
    NEW year's ETS phase instead of serving a stale phase from a warm
    cache keyed on params alone (the "backtest-poison" scenario the plan
    flags -- tests/test_physical_cashflows.py proves this holds for both
    this function and physical_cargo_quantities()). Returns fresh list
    objects on every call (a shallow copy of the cached lists) so a
    caller mutating its own returned list in place can never corrupt a
    cache entry; the CashFlow objects themselves are frozen, so sharing
    references to THEM across calls is safe.
    """
    key = (_params_field_key(params), load_month_year)
    cached = _legacy_quantity_cache.get(key)
    if cached is not None:
        return list(cached[0]), list(cached[1])
    result = _legacy_cargo_quantities_impl(params, load_month_year)
    _legacy_quantity_cache.set(key, result)
    return list(result[0]), list(result[1])


def _legacy_cargo_quantities_impl(params: Params, load_month_year: int) -> tuple[list[CashFlow], list[CashFlow]]:
    """Uncached body of legacy_cargo_quantities() -- see that function's
    docstring for the full per-factor derivation and the caching
    contract; this is the arithmetic increment B moved here verbatim."""
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


# ===========================================================================
# Increment C (R6.1/R6.2, plan sect 6.C): the PHYSICAL-engine counterpart.
# decision.py's own physical machinery is one-route-at-a-time
# (route_value(), _physical_route_value(), _physical_route_breakdown()
# all take a single `route` string), so physical_cargo_quantities()/
# physical_cargo_cashflows() mirror THAT shape rather than the legacy
# pair-returning functions above.
# ===========================================================================


def physical_cargo_quantities(
    params: Params, route: str, load_month_year: int,
    first_cargo_state: Optional["decision.FirstCargoState"] = None,
) -> list[CashFlow]:
    """Price-independent half of the PHYSICAL decomposition (plan sect
    6.C.1/C.2/C.4): a pure function of `params`, `route` ("Europe"/"Asia",
    aliases accepted via decision._normalise_route()) and the load
    month's calendar YEAR (ETS phase, same reason as
    legacy_cargo_quantities()) and `first_cargo_state` (C.2, sunk-cost
    zeroing) only. No `tables`, no `D`, no month_index -- same
    Params-hash-cacheable shape as legacy_cargo_quantities(), so
    risk.py's physical repricer gets the identical "build once per call,
    reuse across every scenario" performance property (plan sect 2).

    SOURCE: reconstructs the SAME physical.py/emissions.py call sequence
    decision._physical_route_breakdown() makes internally
    (vessel_performance_from_params() -> europe_route_segments()/
    asia_route_segments() -> run_voyage() -> voyage_emissions()) --
    verbatim, not an independent re-derivation -- because
    _physical_route_breakdown() itself is ROW-coupled (it takes a priced
    strip_df row) and therefore cannot be Params-only cacheable; this
    function exists to give the physical valuation the SAME two-layer
    split legacy already has. Numerically verified (not just derived on
    paper) against decision.route_value(...).incremental_value across
    both parameter sets, both routes, congested Asia (queue/
    reliquefaction) and all three first_cargo_state values --
    tests/test_physical_cashflows.py group b.

    Decomposition (cargo = params.cargo_size; ledger =
    physical.run_voyage() on the route's segments, heel_target_mmbtu =
    params.heel_fraction * cargo):

      - Revenue: physical.VoyageLedger.delivered_mmbtu already nets out
        BOTH the heel retained at discharge AND whatever boiled off/
        vented/was force-vaporised in transit (run_voyage()'s own
        definition: delivered = available - other_loss - heel_at_discharge,
        where `available` is the cargo mass that survives transit to
        reach the discharge segment). This decomposition instead uses
        `available` itself (= delivered_mmbtu + heel_at_discharge_mmbtu +
        other_loss_mmbtu -- reconstructed here since VoyageLedger has no
        field for it directly; decision.py's own call to run_voyage()
        never passes other_loss_mmbtu, so it is always 0.0 for every
        route this module supports, but the term is included so this
        stays correct if that ever changes) as the "delivered MMBtu"
        revenue quantity, and heel_at_discharge_mmbtu as its OWN,
        separately-labelled, negative-quantity cash flow on the same
        factor -- both economically real and independently inspectable
        (e.g. quantity_on() can report how much value is at stake from
        heel policy alone), rather than silently pre-netting them into
        one number. Transit boil-off/venting gets NO cash flow at all: it
        is neither delivered nor retained, so it is priced at zero by
        omission, the same way it is for the legacy formula. Numerically
        the two cash flows' sum is IDENTICAL to using delivered_mmbtu
        alone (available - heel == delivered by definition) -- this is a
        presentation/inspectability choice, not a different valuation.
        Europe: (TTF, FX)-bilinear, quantity/3.412 (MWh<->MMBtu, matching
        strip()'s ttf_usd conversion). Asia: JKM-linear, no conversion
        (JKM is already $/MMBtu).
      - HH procurement: quantity -cargo * hh_grossup (HH-linear) plus a
        constant -cargo * (liquefaction_toll + pipeline) -- IDENTICAL
        formula to legacy_cargo_quantities()'s procurement term (both
        ultimately read the same model.strip() `proc` field / Params
        fields; this is a 2-line shared-field formula, not a duplicated
        ROUTE-economics risk, so re-expressing it here rather than
        importing legacy's cash flows is deliberate -- it keeps this
        function's quantities independently traceable to
        _physical_route_breakdown()'s own `proc` line without a
        cross-decomposition dependency).
      - Charter: CHARTER-linear, quantity -ledger.total_days -- the
        engine's own summed segment duration (loading + laden [+ queue]
        + discharge + ballast [+ queue]), which equals europe_rt/
        asia_rt_days by construction of the route builders (verified:
        congested Asia's ledger.total_days == params.asia_rt_days
        exactly, queue segments included).
      - Bunkers: VLSFO-linear, quantity -ledger.total_liquid_fuel_tonnes
        -- the engine's REAL net purchased fuel (post-reliquefaction,
        post-heel-substitution on ballast legs), not the legacy flat
        formula's estimate. This is the headline physical-engine
        difference from legacy on the fuel side.
      - ETS (Europe only; Asia is entirely outside EU ETS scope in both
        models): FX-linear, quantity -voyage_em.ets_covered_co2e_tonnes *
        eua_price * phase -- EUA price and phase folded into the
        quantity, mirroring legacy's FX-linear ETS convention exactly
        (see RiskFactor's docstring: R6.5b re-splits ETS into an
        (EUA, FX) bilinear product for BOTH bases together, once EUA has
        its own price history). ets_covered_co2e_tonnes uses the route
        builder's real PER-SEGMENT scope (0.5 sea / 1.0 at-berth /
        0.0 loading-berth for Europe), not legacy's uniform-0.5 constant
        -- the ~4.45% base-value gap test_physical_legacy_equivalence.py
        documents.
      - Fees/tolls: constants, matching _physical_route_breakdown()'s own
        remaining lines (Loading, Discharge/regas [Europe] or Canal/Port
        [Asia], Other).

    first_cargo_state (C.2, "a sunk leg is a constant, not an exposure"):
    mapped through decision.cost_policy() exactly, anchored on
    DecisionMode.PRE_LIFT_CARGO -- cost_policy() only consults the mode
    argument when first_cargo_state is None (a state argument always
    short-circuits the mode dispatch), so PRE_LIFT_CARGO here is read
    ONLY as the None-fallback: cost_policy()'s own "nothing sunk yet"
    anchor, i.e. first_cargo_state=None means FULLY EXPOSED here -- the
    conservative choice for a risk engine (never silently understating
    risk), which is DIFFERENT from decision.py's own POST_LIFT_DIVERSION-
    mode default (sinks both procurement and loading). A caller that
    wants that post-lift default must pass
    FirstCargoState.ALREADY_LOADED explicitly. SUNK ZEROES the affected
    quantity (equivalent to decision.route_value()'s "add back the sunk
    cost", since the cash flow's un-zeroed quantity is exactly what would
    need to be added back) rather than dropping the CashFlow object, so
    the returned list has the IDENTICAL shape (same factor tuples, same
    length) in every state -- only quantities differ -- which is what
    tests/test_physical_cashflows.py's per-state zero-quantity assertions
    rely on. Exactly two quantities are state-sensitive: the HH-linear
    procurement term and its constant (liquefaction+pipeline) remainder
    both zero when procurement is SUNK; the loading constant zeroes when
    loading is SUNK. Every other quantity (revenue, heel, charter,
    bunkers, ETS, other fees) is state-invariant -- cost_policy() only
    ever treats "procurement"/"loading" as state-sensitive cost types,
    matching the economic reality that a not-yet-burned fuel/charter/ETS
    cost is not "sunk" merely because the gas itself was already bought.

    NOT part of the factor set (reported, not forced): dynamic
    reliquefaction/queue physics change the QUANTITIES (fuel tonnes,
    EUA tonnes, delivered MMBtu) but never the PRICE-linearity of the
    valuation -- verified against congested Asia (queue segments,
    non-zero reliquefaction) reproducing decision.route_value() exactly,
    so no special-casing was needed. Vented gas never arises on any path
    decision.py itself exercises (it always builds vessels via
    vessel_performance_from_params(), whose default reliquefaction
    capacity absorbs every surplus this module's tests hit), so it is
    not a live edge case here even though physical.py supports it in
    principle.
    """
    route = decision._normalise_route(route)
    key = (_params_field_key(params), route, load_month_year, first_cargo_state)
    cached = _physical_quantity_cache.get(key)
    if cached is not None:
        return list(cached)
    result = _physical_cargo_quantities_impl(params, route, load_month_year, first_cargo_state)
    _physical_quantity_cache.set(key, result)
    return list(result)


def _physical_cargo_quantities_impl(
    params: Params, route: str, load_month_year: int,
    first_cargo_state: Optional["decision.FirstCargoState"],
) -> list[CashFlow]:
    """Uncached body of physical_cargo_quantities() -- see that function's
    docstring for the full derivation and the caching/state contract.
    `route` is assumed already normalised (physical_cargo_quantities()
    does that before computing the cache key, so it must not re-normalise
    here -- the key and the computation have to agree on the same
    string)."""
    cargo = params.cargo_size
    vessel = physical.vessel_performance_from_params(params)
    segments = (physical.europe_route_segments(params) if route == "Europe"
                else physical.asia_route_segments(params))
    heel_target = params.heel_fraction * cargo
    ledger = physical.run_voyage(segments, vessel, loaded_mmbtu=cargo, heel_target_mmbtu=heel_target)

    # `available` = cargo net of transit boil-off/venting, BEFORE heel is
    # carved out at discharge -- see this function's public docstring.
    available_mmbtu = ledger.delivered_mmbtu + ledger.heel_at_discharge_mmbtu + ledger.other_loss_mmbtu

    proc_treatment = decision.cost_policy(decision.DecisionMode.PRE_LIFT_CARGO, "procurement",
                                          first_cargo_state=first_cargo_state)
    loading_treatment = decision.cost_policy(decision.DecisionMode.PRE_LIFT_CARGO, "loading",
                                             first_cargo_state=first_cargo_state)
    proc_sunk = proc_treatment == decision.CostTreatment.SUNK
    loading_sunk = loading_treatment == decision.CostTreatment.SUNK

    hh_qty = 0.0 if proc_sunk else -cargo * params.hh_grossup
    proc_const_qty = 0.0 if proc_sunk else -cargo * (params.liquefaction_toll + params.pipeline)
    loading_qty = 0.0 if loading_sunk else -cargo * params.loading

    MI = 0  # placeholder month_index -- see legacy_cargo_quantities()'s docstring for why

    flows: list[CashFlow] = []
    if route == "Europe":
        flows.append(CashFlow((RiskFactor.TTF, RiskFactor.FX), MI, available_mmbtu / 3.412,
                               "delivered MMBtu (net of transit boil-off, pre-heel) x destination price"))
        flows.append(CashFlow((RiskFactor.TTF, RiskFactor.FX), MI, -ledger.heel_at_discharge_mmbtu / 3.412,
                               "heel retained at discharge, foregone at destination price"))
    else:
        flows.append(CashFlow((RiskFactor.JKM,), MI, available_mmbtu,
                               "delivered MMBtu (net of transit boil-off, pre-heel) x destination price"))
        flows.append(CashFlow((RiskFactor.JKM,), MI, -ledger.heel_at_discharge_mmbtu,
                               "heel retained at discharge, foregone at destination price"))

    flows.append(CashFlow((RiskFactor.HH,), MI, hh_qty,
                           "HH procurement (grossed up)" + (" -- SUNK, zeroed" if proc_sunk else "")))
    flows.append(CashFlow((), MI, proc_const_qty,
                           "procurement fixed fees: liquefaction + pipeline"
                           + (" -- SUNK, zeroed" if proc_sunk else "")))
    flows.append(CashFlow((), MI, loading_qty,
                           "loading fee" + (" -- SUNK, zeroed" if loading_sunk else "")))
    flows.append(CashFlow((RiskFactor.CHARTER,), MI, -ledger.total_days,
                           "charter hire (physical ledger total days)"))
    flows.append(CashFlow((RiskFactor.VLSFO,), MI, -ledger.total_liquid_fuel_tonnes,
                           "bunker fuel (physical ledger net purchased tonnes)"))

    if route == "Europe":
        voyage_em = emissions.voyage_emissions(ledger)
        phase = phase_for_year(load_month_year)
        flows.append(CashFlow((RiskFactor.FX,), MI,
                               -voyage_em.ets_covered_co2e_tonnes * params.eua_price * phase,
                               "ETS, per-segment scope (EUA price folded into the quantity -- see "
                               "RiskFactor's docstring; R6.5b re-splits into an (EUA, FX) product)"))
        flows.append(CashFlow((), MI, -cargo * params.eu_regas_port, "discharge/regas"))
    else:
        flows.append(CashFlow((), MI, -params.panama_toll_roundtrip, "Panama toll roundtrip"))
        flows.append(CashFlow((), MI, -cargo * params.asia_port_cost, "port cost"))

    flows.append(CashFlow((), MI, -cargo * params.other_cost, "other cost"))

    return flows


def physical_cargo_cashflows(
    D, tables, params: Params, month_index: int, route: str,
    first_cargo_state: Optional["decision.FirstCargoState"] = None,
) -> CargoExposure:
    """D-dependent ASSEMBLY wrapper around physical_cargo_quantities()
    (plan sect 6.C.1), mirroring legacy_cargo_cashflows()'s split: snaps
    prices for ONE load month via _snap_month_prices() -- the SAME snap/
    contract-calendar/fx machinery decision.py's own row-based prices
    ultimately come from too (row["ttf_usd"]/row["JKM"]/row["proc"] etc.
    are read off a strip_df built by this exact snap logic), so reusing
    it here rather than re-snapping independently keeps the two bases
    reading identical prices for the identical D/month_index. Resolves
    the load month's calendar year for the ETS phase, then attaches
    base_prices.

    Returns a single CargoExposure (not an (europe, asia) pair like
    legacy_cargo_cashflows() -- decision.py's own physical machinery is
    one-route-at-a-time, e.g. _physical_route_value(row, params, route),
    and this mirrors that shape rather than legacy's).

    CargoExposure.value(base_prices) reproduces
    decision.route_value(row, params, route, ..., first_cargo_state=
    first_cargo_state).incremental_value for this route/month to
    floating-point precision (tests/test_physical_cashflows.py group b
    pins this against the real workbook, per route, per parameter set,
    per first_cargo_state -- see physical_cargo_quantities()'s docstring
    for the derivation). This is the PHYSICAL basis's own base value, NOT
    model.strip()'s eu_cargo/asia_cargo -- the legacy-vs-physical gap is
    characterised, not reconciled (plan sect 8.5), same policy the
    Decision page already discloses.
    """
    ctx = _snap_month_prices(D, tables, params, month_index)
    route_norm = decision._normalise_route(route)
    flows = physical_cargo_quantities(params, route_norm, ctx["L"].year, first_cargo_state=first_cargo_state)
    flows = [replace(cf, month_index=month_index) for cf in flows]

    if route_norm == "Europe":
        base_prices = {
            RiskFactor.TTF: ctx["ttf_l"],
            RiskFactor.HH: ctx["hh_l"],
            RiskFactor.FX: ctx["fx_l"],
            RiskFactor.CHARTER: ctx["charter"],
            RiskFactor.VLSFO: params.vlsfo_price,
        }
    else:
        base_prices = {
            RiskFactor.JKM: ctx["jkm_l1"],
            RiskFactor.HH: ctx["hh_l"],
            RiskFactor.CHARTER: ctx["charter"],
            RiskFactor.VLSFO: params.vlsfo_price,
        }

    return CargoExposure(route=route_norm, month_index=month_index, cash_flows=flows, base_prices=base_prices)
