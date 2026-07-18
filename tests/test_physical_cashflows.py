"""Tests for the PHYSICAL-engine cash-flow layer (R6.1/R6.2/R6.4,
increment C of docs/R6_RISK_REBUILD_PLAN.md sect 6.C) and its risk.py
wiring (C.5 VaR basis, C.6 stress basis).

Groups:
  (a) Quantity cache (C.4) -- workbook-gated (the quantity producers
      import decision/physical/emissions, which is fine, but a couple of
      these tests want a REAL year/route combination to be meaningful):
      hit on repeated equal-valued Params, miss on ANY single field
      change, and the "backtest-poison" property (a warm cache must not
      serve a stale ETS phase across a year boundary).
  (b) Per-state factor-zeroing map (C.2) -- workbook-gated: exactly which
      quantities are zero in each FirstCargoState, verified against
      independently-computed expected deltas (not string-matching
      labels).
  (c) Parity characterisation (C.3) -- workbook-gated:
      CargoExposure.value(base_prices) matches
      decision.route_value(...).incremental_value to <= $0.01, per
      route, both parameter sets, per first_cargo_state, all 12 months.
  (d) risk.py physical-basis VaR (C.5) -- workbook-gated: zero-shock
      (R1-style), portfolio restriction (12cargo/hedged rejected),
      state-narrows-VaR sanity, and a batched-vs-scalar cross-check
      mirroring test_risk_containment.py's legacy-basis version.
  (e) Stress-tab basis awareness (C.6) -- workbook-gated: a regression
      pin of run_stress_tests()'s LEGACY-basis output (captured before
      this increment touched the function, since stress is not part of
      the frozen 64) plus shape/finite/consistency checks for the
      physical basis.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

import cashflows
import data
import decision
import model
import risk

D = "2026-07-08"


@pytest.fixture(scope="module")
def tables():
    path = os.environ.get(data.ENV_VAR_NAME)
    if not path:
        local = Path(__file__).resolve().parents[1] / "LNG history.xlsx"
        path = str(local) if local.exists() else data.default_data_path()
    if not path:
        pytest.skip(f"set {data.ENV_VAR_NAME} to run workbook-backed tests")
    return data.load_all(str(path))


PARAMS_FACTORIES = [model.Params, model.operating_default_params]
PARAMS_IDS = ["legacy", "operating"]

FIRST_CARGO_STATES = [
    decision.FirstCargoState.ALREADY_LOADED,
    decision.FirstCargoState.PROCUREMENT_COMMITTED_LOADING_REQUIRED,
    decision.FirstCargoState.FULLY_PRE_LIFT,
]


@pytest.fixture(autouse=True)
def _clean_quantity_caches():
    """Every test in this module starts and ends with cold quantity
    caches -- cache STATE must never leak between tests (a hit in one
    test silently masking what would otherwise be a miss in the next)."""
    cashflows.clear_quantity_caches()
    yield
    cashflows.clear_quantity_caches()


# ===========================================================================
# (a) Quantity cache (C.4)
# ===========================================================================


def test_legacy_quantity_cache_hits_on_equal_valued_fresh_instance():
    """C.4: the cache is keyed by Params FIELD VALUES, not identity -- a
    brand-new Params instance with identical field values must hit."""
    p1 = model.operating_default_params()
    cashflows.legacy_cargo_quantities(p1, 2026)
    assert cashflows.quantity_cache_info()["legacy"] == dict(
        hits=0, misses=1, maxsize=cashflows._QUANTITY_CACHE_MAXSIZE, currsize=1
    )

    p2 = model.operating_default_params()  # distinct object, equal field values
    assert p2 is not p1
    cashflows.legacy_cargo_quantities(p2, 2026)
    info = cashflows.quantity_cache_info()["legacy"]
    assert info["hits"] == 1
    assert info["misses"] == 1


def test_physical_quantity_cache_hits_on_equal_valued_fresh_instance():
    p1 = model.operating_default_params()
    cashflows.physical_cargo_quantities(p1, "Europe", 2026)
    assert cashflows.quantity_cache_info()["physical"]["misses"] == 1

    p2 = model.operating_default_params()
    cashflows.physical_cargo_quantities(p2, "Europe", 2026)
    info = cashflows.quantity_cache_info()["physical"]
    assert info["hits"] == 1
    assert info["misses"] == 1


@pytest.mark.parametrize("field", [f.name for f in dataclasses.fields(model.Params)])
def test_legacy_quantity_cache_misses_when_single_field_changes(field):
    """C.4: changing ANY single Params field must miss -- proves the key
    is over FIELD VALUES (dataclasses.fields order), not some coarser
    fingerprint that could alias two different configurations together."""
    base = model.operating_default_params()
    cashflows.legacy_cargo_quantities(base, 2026)
    assert cashflows.quantity_cache_info()["legacy"]["misses"] == 1

    bumped = model.operating_default_params()
    current = getattr(bumped, field)
    # Small delta, not +1.0: the delta must be physically safe for EVERY
    # field (boil_off_rate + 1.0 would be ~100%/day -- the physical twin
    # of this test would then die inside run_voyage()'s own negative-
    # inventory guard instead of testing the cache key) while staying far
    # above float64 spacing even on the largest field (~1.6e6), so the
    # key genuinely changes.
    setattr(bumped, field, 1e-4 if current is None else current + 1e-4)
    cashflows.legacy_cargo_quantities(bumped, 2026)
    info = cashflows.quantity_cache_info()["legacy"]
    assert info["misses"] == 2 and info["hits"] == 0, f"field {field!r} did not change the cache key: {info}"


@pytest.mark.parametrize("field", [f.name for f in dataclasses.fields(model.Params)])
def test_physical_quantity_cache_misses_when_single_field_changes(field):
    base = model.operating_default_params()
    cashflows.physical_cargo_quantities(base, "Europe", 2026)
    assert cashflows.quantity_cache_info()["physical"]["misses"] == 1

    bumped = model.operating_default_params()
    current = getattr(bumped, field)
    # Same small-delta reasoning as the legacy twin above -- and here it
    # is load-bearing, not just consistent: this producer actually RUNS
    # physical.run_voyage(), whose negative-closing-inventory guard
    # correctly rejects a boil_off_rate bumped by +1.0 (~100%/day).
    setattr(bumped, field, 1e-4 if current is None else current + 1e-4)
    cashflows.physical_cargo_quantities(bumped, "Europe", 2026)
    info = cashflows.quantity_cache_info()["physical"]
    assert info["misses"] == 2 and info["hits"] == 0, f"field {field!r} did not change the cache key: {info}"


def test_physical_quantity_cache_misses_on_route_or_state_change_alone():
    """route and first_cargo_state are part of the physical cache key too
    (Params alone isn't enough to identify a physical quantity set --
    C.1's signature includes both)."""
    params = model.operating_default_params()
    cashflows.physical_cargo_quantities(params, "Europe", 2026, first_cargo_state=None)
    assert cashflows.quantity_cache_info()["physical"]["misses"] == 1

    cashflows.physical_cargo_quantities(params, "Asia", 2026, first_cargo_state=None)
    assert cashflows.quantity_cache_info()["physical"]["misses"] == 2

    cashflows.physical_cargo_quantities(
        params, "Europe", 2026, first_cargo_state=decision.FirstCargoState.ALREADY_LOADED
    )
    assert cashflows.quantity_cache_info()["physical"]["misses"] == 3

    # Repeating the very first call now hits.
    cashflows.physical_cargo_quantities(params, "Europe", 2026, first_cargo_state=None)
    info = cashflows.quantity_cache_info()["physical"]
    assert info["hits"] == 1 and info["misses"] == 3


def test_mutating_params_in_place_does_not_serve_a_stale_hit():
    """C.4's core safety property: app.py mutates ONE Params instance in
    place across Streamlit reruns. An identity-keyed cache would keep
    serving the pre-mutation quantities forever; a field-value-keyed
    cache must miss the instant any field changes, even on the SAME
    object."""
    p = model.operating_default_params()
    cashflows.legacy_cargo_quantities(p, 2026)
    assert cashflows.quantity_cache_info()["legacy"]["misses"] == 1

    p.hh_grossup = p.hh_grossup + 0.05  # mutate IN PLACE, same object identity
    cashflows.legacy_cargo_quantities(p, 2026)
    info = cashflows.quantity_cache_info()["legacy"]
    assert info["misses"] == 2 and info["hits"] == 0, "mutation in place must miss, not serve a stale hit"

    # Calling again with the SAME (now-mutated) value hits.
    cashflows.legacy_cargo_quantities(p, 2026)
    info = cashflows.quantity_cache_info()["legacy"]
    assert info["hits"] == 1 and info["misses"] == 2


@pytest.mark.parametrize("params_factory", PARAMS_FACTORIES, ids=PARAMS_IDS)
def test_backtest_poison_year_boundary_changes_ets_phase_with_warm_cache(tables, params_factory):
    """C.4's named acceptance test: walking D across a year boundary with
    a WARM cache must change the ETS phase on the ASSEMBLED cash flows --
    the two-layer split (quantities cached by (params, year); assembly
    never cached) exists precisely so a Params-only cache never serves a
    stale phase mid-backtest. D1's month-0 load month is Jan-2024 (phase
    0.4); D2's is Jan-2025 (phase 0.7) -- ratio 0.7/0.4 = 1.75 exactly."""
    params = params_factory()
    D1 = "2023-12-15"  # F=2024-01-01, s=0 -> month_index=0 load month Jan-2024
    D2 = "2024-12-15"  # F=2025-01-01, s=0 -> month_index=0 load month Jan-2025
    assert model.contract_calendar(pd.Timestamp(D1))[0] == pd.Timestamp("2024-01-01")
    assert model.contract_calendar(pd.Timestamp(D2))[0] == pd.Timestamp("2025-01-01")

    eu1, _ = cashflows.legacy_cargo_cashflows(D1, tables, params, 0)
    eu2, _ = cashflows.legacy_cargo_cashflows(D2, tables, params, 0)
    fx1 = eu1.quantity_on((cashflows.RiskFactor.FX,))
    fx2 = eu2.quantity_on((cashflows.RiskFactor.FX,))
    assert fx1 != 0.0
    assert fx2 / fx1 == pytest.approx(0.7 / 0.4, rel=1e-9)

    phys1 = cashflows.physical_cargo_cashflows(D1, tables, params, 0, "Europe",
                                                decision.FirstCargoState.FULLY_PRE_LIFT)
    phys2 = cashflows.physical_cargo_cashflows(D2, tables, params, 0, "Europe",
                                                decision.FirstCargoState.FULLY_PRE_LIFT)
    pfx1 = phys1.quantity_on((cashflows.RiskFactor.FX,))
    pfx2 = phys2.quantity_on((cashflows.RiskFactor.FX,))
    assert pfx1 != 0.0
    assert pfx2 / pfx1 == pytest.approx(0.7 / 0.4, rel=1e-9)

    # The cache genuinely was warm (both quantity producers were called
    # exactly twice -- once per year -- proving this isn't just "always
    # recomputes", which would trivially also pass).
    assert cashflows.quantity_cache_info()["legacy"]["misses"] == 2
    assert cashflows.quantity_cache_info()["physical"]["misses"] == 2


# ===========================================================================
# (b) Per-state factor-zeroing map (C.2)
# ===========================================================================


@pytest.mark.parametrize("params_factory", PARAMS_FACTORIES, ids=PARAMS_IDS)
@pytest.mark.parametrize("route", ["Europe", "Asia"])
def test_first_cargo_state_zeroes_exactly_hh_and_the_sunk_constants(tables, params_factory, route):
    """C.2's headline property, verified against INDEPENDENTLY computed
    expected deltas (not string-matching CashFlow.label): ALREADY_LOADED
    zeroes the HH-linear procurement term AND both sunk constant pieces
    (liquefaction+pipeline, loading); PROCUREMENT_COMMITTED_LOADING_
    REQUIRED zeroes HH and ONLY the procurement constant (loading stays
    live); FULLY_PRE_LIFT and None (the module's own "fully exposed"
    default -- see physical_cargo_quantities()'s docstring) zero
    neither."""
    params = params_factory()
    cargo = params.cargo_size

    def flows_for(state):
        return cashflows.physical_cargo_quantities(params, route, 2026, first_cargo_state=state)

    def hh_total(flows):
        return sum(cf.quantity for cf in flows if cf.factors == (cashflows.RiskFactor.HH,))

    def constant_total(flows):
        return sum(cf.quantity for cf in flows if cf.factors == ())

    already = flows_for(decision.FirstCargoState.ALREADY_LOADED)
    proc_committed = flows_for(decision.FirstCargoState.PROCUREMENT_COMMITTED_LOADING_REQUIRED)
    fully_pre_lift = flows_for(decision.FirstCargoState.FULLY_PRE_LIFT)
    none_state = flows_for(None)

    expected_hh = -cargo * params.hh_grossup
    expected_proc_const = -cargo * (params.liquefaction_toll + params.pipeline)
    expected_loading_const = -cargo * params.loading

    # HH: zero exactly when procurement is sunk; the documented -cargo *
    # hh_grossup formula otherwise, identical across every non-sunk state.
    assert hh_total(already) == 0.0
    assert hh_total(proc_committed) == 0.0
    assert hh_total(fully_pre_lift) == pytest.approx(expected_hh)
    assert hh_total(none_state) == pytest.approx(expected_hh)

    # Constants (aggregate, since every constant cash flow shares the same
    # empty factor tuple): the SUNK deltas are exactly the independently-
    # computed procurement/loading constants, nothing more, nothing less.
    # Signs: expected_*_const are NEGATIVE (costs); fully_pre_lift still
    # carries them while the sunk state zeroes them, so
    # fully_pre_lift - sunk == the negative constants themselves.
    assert constant_total(fully_pre_lift) - constant_total(already) == pytest.approx(
        expected_proc_const + expected_loading_const
    )
    assert constant_total(fully_pre_lift) - constant_total(proc_committed) == pytest.approx(
        expected_proc_const
    )
    assert constant_total(none_state) == pytest.approx(constant_total(fully_pre_lift))

    # Every OTHER factor (revenue/heel + charter + vlsfo, and ETS's FX
    # leg for Europe) is state-INVARIANT -- cost_policy() only ever
    # treats procurement/loading as state-sensitive cost types.
    other_factor_tuples = {cf.factors for cf in fully_pre_lift} - {(cashflows.RiskFactor.HH,), ()}
    assert other_factor_tuples, "expected at least one revenue/charter/vlsfo factor tuple"
    for factors in other_factor_tuples:
        base_qty = sum(cf.quantity for cf in fully_pre_lift if cf.factors == factors)
        for flows, label in ((already, "ALREADY_LOADED"), (proc_committed, "PROCUREMENT_COMMITTED"),
                              (none_state, "None")):
            qty = sum(cf.quantity for cf in flows if cf.factors == factors)
            assert qty == pytest.approx(base_qty), f"{factors} changed under {label}, expected state-invariant"


def test_none_first_cargo_state_matches_fully_pre_lift_exactly():
    """physical_cargo_quantities(..., first_cargo_state=None) is
    documented as "fully exposed", i.e. numerically identical to
    FULLY_PRE_LIFT -- NOT decision.py's own POST_LIFT_DIVERSION-mode
    default (which sinks both). Pure unit test (no workbook needed --
    load_month_year is just an int)."""
    params = model.operating_default_params()
    none_flows = cashflows.physical_cargo_quantities(params, "Europe", 2026, first_cargo_state=None)
    pre_lift_flows = cashflows.physical_cargo_quantities(
        params, "Europe", 2026, first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT
    )
    assert [cf.quantity for cf in none_flows] == pytest.approx([cf.quantity for cf in pre_lift_flows])


# ===========================================================================
# (c) Parity characterisation (C.3)
# ===========================================================================


@pytest.mark.parametrize("params_factory", PARAMS_FACTORIES, ids=PARAMS_IDS)
def test_physical_exposure_matches_decision_route_value_all_months_fully_pre_lift(tables, params_factory):
    """C.3: CargoExposure.value(base_prices) == decision.route_value(...)
    .incremental_value to <= $0.01, for every one of the 12 load months,
    both routes -- at FULLY_PRE_LIFT (state has no sunk zeroing, so this
    sweep is really exercising the REVENUE/heel/charter/vlsfo/ETS side
    of the decomposition across every month/year the 12-month strip
    touches, not just month 0)."""
    params = params_factory()
    strip_df = model.strip(D, tables, params)
    state = decision.FirstCargoState.FULLY_PRE_LIFT

    worst = 0.0
    for month_index in range(12):
        row = strip_df.iloc[month_index]
        for route in ("Europe", "Asia"):
            exposure = cashflows.physical_cargo_cashflows(D, tables, params, month_index, route, state)
            assert exposure.route == route and exposure.month_index == month_index
            value = exposure.value(exposure.base_prices)
            expected = decision.route_value(
                row, params, route, decision.DecisionMode.POST_LIFT_DIVERSION,
                month_index=month_index, first_cargo_state=state,
            ).incremental_value
            worst = max(worst, abs(value - expected))
            assert value == pytest.approx(expected, abs=0.01)

    print(f"[{params_factory.__name__}] worst abs error (12 months x 2 routes, FULLY_PRE_LIFT): {worst:.3e}")


@pytest.mark.parametrize("params_factory", PARAMS_FACTORIES, ids=PARAMS_IDS)
@pytest.mark.parametrize("route", ["Europe", "Asia"])
@pytest.mark.parametrize("state", FIRST_CARGO_STATES, ids=[s.value for s in FIRST_CARGO_STATES])
def test_physical_exposure_matches_decision_route_value_per_state(tables, params_factory, route, state):
    """C.3's "per first-cargo state" axis, at month 0: the parity holds
    under sunk-cost zeroing too, not just the fully-exposed case above."""
    params = params_factory()
    row = model.strip(D, tables, params).iloc[0]
    exposure = cashflows.physical_cargo_cashflows(D, tables, params, 0, route, state)
    value = exposure.value(exposure.base_prices)
    expected = decision.route_value(
        row, params, route, decision.DecisionMode.POST_LIFT_DIVERSION, month_index=0, first_cargo_state=state,
    ).incremental_value
    assert value == pytest.approx(expected, abs=0.01)


def test_physical_exposure_matches_decision_route_value_congested_asia_with_heel(tables):
    """Edge case the plan calls out by name: congested Asia (queue
    segments, non-zero reliquefaction -- test_physical_legacy_
    equivalence.py's TestAsiaRouteEquivalence) combined with a non-zero
    heel. Both dynamics change QUANTITIES (fuel tonnes, delivered MMBtu)
    but must not break the factor-set decomposition's linearity."""
    params = model.Params(asia_rt_days=model.ASIA_RT_CONG, heel_fraction=0.02)
    row = model.strip(D, tables, params).iloc[0]
    for state in FIRST_CARGO_STATES:
        exposure = cashflows.physical_cargo_cashflows(D, tables, params, 0, "Asia", state)
        value = exposure.value(exposure.base_prices)
        expected = decision.route_value(
            row, params, "Asia", decision.DecisionMode.POST_LIFT_DIVERSION, month_index=0, first_cargo_state=state,
        ).incremental_value
        assert value == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("params_factory", PARAMS_FACTORIES, ids=PARAMS_IDS)
def test_base_prices_cover_every_factor_the_physical_cashflows_reference(tables, params_factory):
    """Mirrors test_cashflows.py's legacy version: base_prices must
    supply a price for every RiskFactor the exposure's own cash flows
    reference. EUA is absent (folded into the ETS quantity, same as
    legacy); Asia has no FX (no ETS, no FX-denominated term at all,
    matching legacy's own Asia decomposition)."""
    params = params_factory()
    for route in ("Europe", "Asia"):
        exposure = cashflows.physical_cargo_cashflows(D, tables, params, 0, route,
                                                        decision.FirstCargoState.FULLY_PRE_LIFT)
        referenced = {f for cf in exposure.cash_flows for f in cf.factors}
        assert referenced.issubset(exposure.base_prices.keys())
        assert cashflows.RiskFactor.EUA not in exposure.base_prices

    asia_exposure = cashflows.physical_cargo_cashflows(D, tables, params, 0, "Asia",
                                                         decision.FirstCargoState.FULLY_PRE_LIFT)
    assert cashflows.RiskFactor.FX not in asia_exposure.base_prices


def test_legacy_vs_physical_base_value_gap_is_nonzero_and_reported(tables):
    """The legacy-vs-physical BASE-VALUE gap is EXPECTED and disclosed,
    never reconciled away (plan sect 8.5) -- this test doesn't assert a
    specific magnitude (that would re-couple this test to whatever the
    live workbook happens to show today), just that a gap exists and
    prints it for the record, proving the two bases are NOT silently
    identical."""
    params = model.operating_default_params()
    row = model.strip(D, tables, params).iloc[0]
    for route in ("Europe", "Asia"):
        legacy_value = float(row["eu_cargo"] if route == "Europe" else row["asia_cargo"])
        exposure = cashflows.physical_cargo_cashflows(D, tables, params, 0, route,
                                                        decision.FirstCargoState.FULLY_PRE_LIFT)
        physical_value = exposure.value(exposure.base_prices)
        gap = physical_value - legacy_value
        gap_pct = gap / legacy_value if legacy_value else float("nan")
        print(f"[{route}] legacy={legacy_value:,.2f} physical={physical_value:,.2f} "
              f"gap={gap:,.2f} ({gap_pct:+.4%})")
        assert abs(gap) > 1.0, f"{route}: expected a non-trivial legacy-vs-physical gap, got {gap}"


# ===========================================================================
# (d) risk.py physical-basis VaR (C.5)
# ===========================================================================


def _zero_scenario() -> risk.ScenarioSet:
    return risk.ScenarioSet(
        dates=[pd.Timestamp("2026-07-07")],
        hh_ret=np.zeros((1, 13)), ttf_ret=np.zeros((1, 13)), jkm_ret=np.zeros((1, 13)),
        fx_ret=np.zeros(1), end_date=pd.Timestamp("2026-07-07"), lookback=1, method="naive",
    )


@pytest.mark.parametrize(
    ("portfolio", "basin"),
    [("single", "Europe"), ("single", "Asia"), ("spread", "Europe")],
)
@pytest.mark.parametrize("state", [None] + FIRST_CARGO_STATES,
                          ids=["none"] + [s.value for s in FIRST_CARGO_STATES])
def test_physical_basis_zero_shock_pnl_is_zero(tables, portfolio, basin, state):
    """C.5's acceptance test, same R1-style construction as the legacy
    zero-shock tests: a zero-return ScenarioSet must reprice to ~$0 P&L
    against historical_var_physical()'s own base, for every supported
    portfolio/basin/first_cargo_state combination."""
    result = risk.historical_var_physical(
        D, tables, model.operating_default_params(), portfolio=portfolio, basin=basin,
        month_index=0, scen=_zero_scenario(), first_cargo_state=state,
    )
    assert abs(float(result.pnl[0])) <= 0.01


@pytest.mark.parametrize("portfolio", ["12cargo", "hedged"])
def test_physical_basis_rejects_unsupported_portfolios(tables, portfolio):
    """Plan sect 8.3: 12cargo is legacy-basis-only (infeasible one-vessel
    portfolio, fixture-bound); hedged's mechanical legs are themselves
    legacy-formula-derived. Both must fail loud, not silently fall back
    to a legacy computation under a physical-basis call."""
    with pytest.raises(ValueError, match="single.*spread|spread.*single"):
        risk.historical_var_physical(D, tables, model.operating_default_params(), portfolio=portfolio, scen=_zero_scenario())


def test_physical_basis_sunk_procurement_narrows_var(tables):
    """C.2's headline claim, proven through the FULL VaR pipeline (not
    just the base-value parity tests above): a sunk procurement leg
    removes HH from the exposure ENTIRELY -- every historical scenario's
    HH move stops contributing to P&L, so VaR under ALREADY_LOADED must
    be strictly narrower (smaller magnitude) than under FULLY_PRE_LIFT
    for the same portfolio/scenarios. Real (non-zero) scenarios are
    required to see this -- a zero-shock test alone cannot distinguish
    "HH is excluded" from "HH happened not to move"."""
    params = model.operating_default_params()
    scen = risk.build_scenarios(tables, D, lookback=250, method="naive")

    exposed = risk.historical_var_physical(
        D, tables, params, portfolio="single", basin="Europe", month_index=0, scen=scen,
        first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT,
    )
    sunk = risk.historical_var_physical(
        D, tables, params, portfolio="single", basin="Europe", month_index=0, scen=scen,
        first_cargo_state=decision.FirstCargoState.ALREADY_LOADED,
    )
    assert abs(sunk.var95) < abs(exposed.var95)
    assert abs(sunk.sd) < abs(exposed.sd)
    # Loading alone (PROCUREMENT_COMMITTED_LOADING_REQUIRED keeps loading
    # live, only sinks procurement) is a pure constant -- it must have NO
    # effect on the P&L distribution's spread, only ALREADY_LOADED's
    # extra HH exclusion does. So PROCUREMENT_COMMITTED and ALREADY_LOADED
    # must produce IDENTICAL VaR/sd for this single-cargo portfolio.
    proc_only = risk.historical_var_physical(
        D, tables, params, portfolio="single", basin="Europe", month_index=0, scen=scen,
        first_cargo_state=decision.FirstCargoState.PROCUREMENT_COMMITTED_LOADING_REQUIRED,
    )
    assert proc_only.var95 == pytest.approx(sunk.var95)
    assert proc_only.sd == pytest.approx(sunk.sd)


@pytest.mark.parametrize(
    "params_factory", [model.Params, model.operating_default_params], ids=["legacy", "operating"]
)
def test_vectorized_reprice_physical_matches_scalar_cargo_exposure_loop(tables, params_factory):
    """Physical-basis counterpart of test_risk_containment.py's
    test_vectorized_reprice_matches_scalar_cargo_exposure_loop: the
    batched _vectorized_reprice_physical() path must agree with an
    independent per-scenario Python loop calling CargoExposure.value()
    at the same shocked prices, under REAL (non-zero) scenarios."""
    params = params_factory()

    hh_row = model.snap(tables.hh, D)
    ttf_row = model.snap(tables.ttf, D)
    jkm_row = model.snap(tables.jkm, D)
    fx_row = model.snap(tables.fx, D)
    ch_row = model.snap(tables.charter, D)
    charter = params.charter_override if params.charter_override is not None else float(ch_row["rate174"])

    cols = [f"c{i}" for i in range(1, risk.N_STRIP_COLS + 1)]
    base_hh = hh_row[cols].to_numpy(dtype=float)
    base_ttf = ttf_row[cols].to_numpy(dtype=float)
    base_jkm = jkm_row[cols].to_numpy(dtype=float)
    base_spot, base_o6, base_o1 = float(fx_row["spot"]), float(fx_row["o6"]), float(fx_row["o1"])

    rng = np.random.default_rng(20260718)
    n = 6
    scen = risk.ScenarioSet(
        dates=[pd.Timestamp("2026-07-07")] * n,
        hh_ret=rng.normal(0, 0.02, (n, risk.N_STRIP_COLS)),
        ttf_ret=rng.normal(0, 0.02, (n, risk.N_STRIP_COLS)),
        jkm_ret=rng.normal(0, 0.02, (n, risk.N_STRIP_COLS)),
        fx_ret=rng.normal(0, 0.01, n),
        end_date=pd.Timestamp("2026-07-07"), lookback=n, method="naive",
    )
    state = decision.FirstCargoState.PROCUREMENT_COMMITTED_LOADING_REQUIRED

    result = risk._vectorized_reprice_physical(scen, D, base_hh, base_ttf, base_jkm,
                                                 base_spot, base_o6, base_o1, charter, params,
                                                 first_cargo_state=state)

    F, s = model.contract_calendar(pd.Timestamp(D))
    months = model.load_months(F, 12)

    worst = 0.0
    for i, m in enumerate(months):
        eu_flows = cashflows.physical_cargo_quantities(params, "Europe", m.year, first_cargo_state=state)
        asia_flows = cashflows.physical_cargo_quantities(params, "Asia", m.year, first_cargo_state=state)
        eu_exp = cashflows.CargoExposure(route="Europe", month_index=i, cash_flows=eu_flows)
        asia_exp = cashflows.CargoExposure(route="Asia", month_index=i, cash_flows=asia_flows)
        for sc in range(n):
            eu_prices = {
                cashflows.RiskFactor.TTF: float(result["ttf_L"][sc, i]),
                cashflows.RiskFactor.FX: float(result["fx_l"][sc, i]),
                cashflows.RiskFactor.HH: float(result["hh_L"][sc, i]),
                cashflows.RiskFactor.CHARTER: charter,
                cashflows.RiskFactor.VLSFO: params.vlsfo_price,
            }
            asia_prices = {
                cashflows.RiskFactor.JKM: float(result["jkm_L1"][sc, i]),
                cashflows.RiskFactor.HH: float(result["hh_L"][sc, i]),
                cashflows.RiskFactor.CHARTER: charter,
                cashflows.RiskFactor.VLSFO: params.vlsfo_price,
            }
            eu_scalar = eu_exp.value(eu_prices)
            asia_scalar = asia_exp.value(asia_prices)
            worst = max(worst, abs(eu_scalar - result["eu_cargo"][sc, i]), abs(asia_scalar - result["asia_cargo"][sc, i]))
            assert eu_scalar == pytest.approx(result["eu_cargo"][sc, i], rel=1e-9, abs=1e-6)
            assert asia_scalar == pytest.approx(result["asia_cargo"][sc, i], rel=1e-9, abs=1e-6)
    print(f"[{params_factory.__name__}] physical batched-vs-scalar worst abs error: {worst:.3e}")


def test_legacy_vectorized_reprice_unaffected_by_shared_helper_extraction(tables):
    """C.5 extracted _vectorized_reprice()'s price-preparation block into
    _prepare_scenario_price_arrays() so _vectorized_reprice_physical()
    could reuse it. This pins that the extraction changed NOTHING about
    _vectorized_reprice()'s own output on a real (non-zero) scenario --
    the frozen 64/64 and test_risk_containment.py's own batched-vs-scalar
    test already cover this at the historical_var()/zero-shock level;
    this test exercises _vectorized_reprice() directly."""
    params = model.operating_default_params()
    hh_row = model.snap(tables.hh, D)
    ttf_row = model.snap(tables.ttf, D)
    jkm_row = model.snap(tables.jkm, D)
    fx_row = model.snap(tables.fx, D)
    ch_row = model.snap(tables.charter, D)
    charter = params.charter_override if params.charter_override is not None else float(ch_row["rate174"])
    cols = [f"c{i}" for i in range(1, risk.N_STRIP_COLS + 1)]
    base_hh = hh_row[cols].to_numpy(dtype=float)
    base_ttf = ttf_row[cols].to_numpy(dtype=float)
    base_jkm = jkm_row[cols].to_numpy(dtype=float)
    base_spot, base_o6, base_o1 = float(fx_row["spot"]), float(fx_row["o6"]), float(fx_row["o1"])

    rng = np.random.default_rng(20260718)
    n = 4
    scen = risk.ScenarioSet(
        dates=[pd.Timestamp("2026-07-07")] * n,
        hh_ret=rng.normal(0, 0.02, (n, risk.N_STRIP_COLS)),
        ttf_ret=rng.normal(0, 0.02, (n, risk.N_STRIP_COLS)),
        jkm_ret=rng.normal(0, 0.02, (n, risk.N_STRIP_COLS)),
        fx_ret=rng.normal(0, 0.01, n),
        end_date=pd.Timestamp("2026-07-07"), lookback=n, method="naive",
    )
    result = risk._vectorized_reprice(scen, D, base_hh, base_ttf, base_jkm, base_spot, base_o6, base_o1,
                                       charter, params)
    # Independent oracle: model.strip() on the bumped tables/prices is
    # already what the legacy zero-shock tests cross-check against;
    # here, cross-check scenario 0 against legacy_cargo_cashflows()
    # evaluated at that scenario's own prices (same pattern as
    # test_vectorized_reprice_matches_scalar_cargo_exposure_loop).
    eu, asia = cashflows.legacy_cargo_cashflows(D, tables, params, 0)
    eu_prices = {
        cashflows.RiskFactor.TTF: float(result["ttf_L"][0, 0]),
        cashflows.RiskFactor.FX: float(result["fx_l"][0, 0]),
        cashflows.RiskFactor.HH: float(result["hh_L"][0, 0]),
        cashflows.RiskFactor.CHARTER: charter,
        cashflows.RiskFactor.VLSFO: params.vlsfo_price,
    }
    assert eu.value(eu_prices) == pytest.approx(result["eu_cargo"][0, 0], rel=1e-9, abs=1e-6)


# ===========================================================================
# (e) Stress-tab basis awareness (C.6)
# ===========================================================================

# Captured from risk.run_stress_tests(D, tables, params) BEFORE this
# increment touched the function (stress is not part of the frozen 64,
# so this increment pins it itself, per the plan's own instruction).
# Values are exact floats from a live run against the real workbook on
# D="2026-07-08"; the tolerance below is deliberately tight (this is a
# deterministic computation -- any drift means the "legacy" branch
# stopped being byte-identical to before the basis= parameter existed).
#
# R6 increment E.1(a) (plan sect 6.E.1): the three "Charter ..." rows
# below are captured AFTER this increment added them -- extending this
# SAME pin list additively (not a parallel one) is the plan's own
# instruction ("extend the pinned regression test additively -- pin the
# NEW rows too"). The six rows ABOVE the charter rows are byte-for-byte
# unchanged from increment C -- this file's pre-existing test doesn't
# need to change to prove that, only to grow to cover the three new ones.
_STRESS_PIN_LEGACY = [
    ("Replay 2021-12-21 move", 193741417.64618623, 982596.1415178888),
    ("Replay 2022-08-26 move", 40837989.65533432, -205840.40491522476),
    ("Replay 2022-09-26 move", -26674486.77191952, 3518292.534549836),
    ("Panama congestion (Asia RT 54.7d)", 0.0, -1349831.9999999963),
    ("JKM +2.6 $/MMBtu (13-Jul-2026 Hormuz repricing)", 8062592.965168893, 8910066.666666668),
    ("EUA EUR70 -> EUR120/t", -3057890.447725475, 253055.00540834293),
    ("Charter +$25k/day", -7782051.282051235, -520085.4700854607),
    ("Charter -$25k/day", 7782051.282051355, 520085.4700854756),
    ("Charter +50%", -13618589.743589759, -910149.5726495571),
]
_STRESS_PIN_OPERATING = [
    ("Replay 2021-12-21 move", 193438466.4333396, 962070.9507334642),
    ("Replay 2022-08-26 move", 40774988.91320619, -209394.2540842332),
    ("Replay 2022-09-26 move", -26632257.495108217, 3513264.288066581),
    ("Panama congestion (Asia RT 54.7d)", 0.0, -597095.1701998226),
    ("JKM +2.6 $/MMBtu (13-Jul-2026 Hormuz repricing)", 8181010.174510777, 8883473.52941177),
    ("EUA EUR70 -> EUR120/t", -3057890.4477255344, 253055.0054083392),
    ("Charter +$25k/day", -8105882.352941126, -589215.6862745099),
    ("Charter -$25k/day", 8105882.352941155, 589215.6862745099),
    ("Charter +50%", -14185294.117647022, -1031127.4509803914),
]


@pytest.mark.parametrize(
    ("params_factory", "pin"),
    [(model.Params, _STRESS_PIN_LEGACY), (model.operating_default_params, _STRESS_PIN_OPERATING)],
    ids=["legacy", "operating"],
)
def test_run_stress_tests_legacy_basis_is_bit_compatible_with_pre_increment_c(tables, params_factory, pin):
    """C.6's explicit instruction: "FIRST PIN current legacy behaviour
    with a regression test (stress is not in the frozen 64), then
    re-express..." -- this is that pin. basis="legacy" (the default) must
    reproduce every number from before this increment's basis= parameter
    existed, to floating-point-noise tolerance."""
    params = params_factory()
    df = risk.run_stress_tests(D, tables, params)  # basis defaults to "legacy"
    assert list(df["scenario"]) == [name for name, _, _ in pin]
    for (name, exp_12, exp_spread), (_, row) in zip(pin, df.iterrows()):
        assert row["pnl_12cargo"] == pytest.approx(exp_12, rel=1e-9, abs=1e-6), name
        assert row["pnl_m1_spread"] == pytest.approx(exp_spread, rel=1e-9, abs=1e-6), name

    # Also equivalent to an explicit basis="legacy" call (the parameter
    # is additive, not a behaviour change to the implicit default).
    df_explicit = risk.run_stress_tests(D, tables, params, basis="legacy")
    pd.testing.assert_frame_equal(df, df_explicit)


def test_run_stress_tests_rejects_unknown_basis(tables):
    with pytest.raises(ValueError, match="basis"):
        risk.run_stress_tests(D, tables, model.Params(), basis="not_a_real_basis")


def test_run_stress_tests_physical_basis_shape_and_finiteness(tables):
    """basis="physical": the three historical-replay rows are n/a on
    EITHER pnl column (NaN, with a note); the six deterministic-shock
    rows (three pre-existing -- Panama/JKM/EUA -- plus the three new
    charter rows, R6 increment E.1(a)) have pnl_12cargo NaN (no
    physical-basis 12cargo) and a FINITE pnl_m1_spread."""
    params = model.operating_default_params()
    df = risk.run_stress_tests(D, tables, params, basis="physical",
                                first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT)
    assert len(df) == 9
    assert df["pnl_12cargo"].isna().all()

    replay_rows = df.iloc[0:3]
    assert replay_rows["pnl_m1_spread"].isna().all()
    assert replay_rows["note"].str.contains("n/a").all()

    shock_rows = df.iloc[3:9]
    assert np.isfinite(shock_rows["pnl_m1_spread"]).all()
    assert list(df["scenario"].iloc[6:9]) == ["Charter +$25k/day", "Charter -$25k/day", "Charter +50%"]


def test_run_stress_tests_physical_jkm_shock_matches_independent_price_bump(tables):
    """Independent cross-check of the JKM+2.6 physical row: recompute the
    SAME shock directly via physical_cargo_cashflows()'s own base_prices
    (a factor-PRICE bump on the unbumped exposure -- Europe untouched, no
    exposure rebuild needed since JKM doesn't change any physical
    quantity), rather than trusting run_stress_tests()'s internal
    arithmetic."""
    params = model.operating_default_params()
    state = decision.FirstCargoState.PROCUREMENT_COMMITTED_LOADING_REQUIRED

    base_eu = cashflows.physical_cargo_cashflows(D, tables, params, 0, "Europe", state)
    base_asia = cashflows.physical_cargo_cashflows(D, tables, params, 0, "Asia", state)
    base_spread = base_asia.value(base_asia.base_prices) - base_eu.value(base_eu.base_prices)

    bumped_prices = dict(base_asia.base_prices)
    bumped_prices[cashflows.RiskFactor.JKM] += 2.6
    bumped_spread = base_asia.value(bumped_prices) - base_eu.value(base_eu.base_prices)
    expected_delta = bumped_spread - base_spread

    df = risk.run_stress_tests(D, tables, params, basis="physical", first_cargo_state=state)
    row = df[df["scenario"].str.startswith("JKM +2.6")].iloc[0]
    assert row["pnl_m1_spread"] == pytest.approx(expected_delta, rel=1e-9, abs=1e-6)


def test_run_stress_tests_physical_spread_pnl_is_first_cargo_state_invariant(tables):
    """None of the six deterministic shocks (Panama congestion, JKM bump,
    EUA bump, and -- R6 increment E.1(a) -- the three charter shocks)
    touches procurement or loading -- the only state-sensitive quantities
    (C.2) -- so pnl_m1_spread (a P&L DELTA, where any state-invariant
    constant term cancels between the bumped and base evaluations) must
    be identical across every first_cargo_state. This is an honest,
    worth-recording consequence of C.2's design, not a bug: sunk-cost
    zeroing changes ABSOLUTE exposure values (the base value tests
    above), never a DELTA between two states-under-the-same-
    first_cargo_state. Charter's quantity (CHARTER-linear, ledger.
    total_days) is likewise never one of C.2's two state-sensitive
    quantities (HH procurement, loading) -- see
    cashflows.physical_cargo_quantities()'s own docstring -- so the same
    invariance is expected to extend to it, and this test proves it does."""
    params = model.operating_default_params()
    spreads = {}
    for state in [None] + FIRST_CARGO_STATES:
        df = risk.run_stress_tests(D, tables, params, basis="physical", first_cargo_state=state)
        spreads[state] = df.iloc[3:9]["pnl_m1_spread"].to_numpy(dtype=float)
    reference = spreads[None]
    for state, values in spreads.items():
        np.testing.assert_allclose(values, reference, rtol=1e-9, atol=1e-6, err_msg=f"state={state}")


# ===========================================================================
# (f) Committed-programme portfolio (R6 increment D, plan sect 6.D)
# ===========================================================================


def _leg(cargo_number, route, month_index, load_month="2026-08-01", duration_days=27.0):
    """A bare decision.ProgrammeLeg, for tests that want to exercise the
    programme-repricing helpers WITHOUT depending on what
    decision.optimise_programme() happens to choose for a given D/params
    (that choice is data-dependent; the code paths tested here are not)."""
    return decision.ProgrammeLeg(
        cargo_number=cargo_number, route=route, month_index=month_index,
        load_month=pd.Timestamp(load_month), start_day=0.0, duration_days=duration_days,
        value=0.0, decision_mode=decision.DecisionMode.POST_LIFT_DIVERSION,
    )


def _hand_built_plan(*legs: decision.ProgrammeLeg) -> decision.ProgrammePlan:
    return decision.ProgrammePlan(
        legs=tuple(legs), horizon_days=200.0, residual_days=0.0, residual_value=0.0, total_value=0.0,
    )


def test_programme_leg_is_priceable_boundary():
    """Pure unit test (no workbook needed): programme_leg_is_priceable()
    is exactly the 0..11 range check on month_index -- the same window
    _prepare_scenario_price_arrays() builds for every other portfolio
    (offsets 0..11 are safe; a 13th month risks running the JKM L+1 tenor
    lookup past the 13-column ScenarioSet return arrays)."""
    assert risk.programme_leg_is_priceable(_leg(1, "Europe", 0))
    assert risk.programme_leg_is_priceable(_leg(2, "Europe", 11))
    assert not risk.programme_leg_is_priceable(_leg(2, "Europe", 12))
    assert not risk.programme_leg_is_priceable(_leg(2, "Asia", 35))


def test_programme_leg_state_first_leg_takes_caller_state_later_legs_none():
    """Pure unit test: leg.cargo_number == 1 takes the caller's state;
    every later leg is always None (fully exposed) -- plan sect
    6.D.1/6.C.2, mirroring app.py's own `... if leg.cargo_number == 1
    else None` idiom (Decision page, "How the programme value is
    calculated" detail table) verbatim."""
    state = decision.FirstCargoState.ALREADY_LOADED
    leg1 = _leg(1, "Europe", 0)
    leg2 = _leg(2, "Europe", 1)
    leg3 = _leg(3, "Asia", 2)
    assert risk._programme_leg_state(leg1, state) == state
    assert risk._programme_leg_state(leg2, state) is None
    assert risk._programme_leg_state(leg3, state) is None
    assert risk._programme_leg_state(leg1, None) is None


def test_build_committed_programme_horizon_matches_decision_page_formula(tables):
    """D.1's explicit instruction: derive the default horizon/turnaround
    the SAME way app.py's Decision page does (two Europe round trips plus
    one turnaround gap, rounded up to 0.1 d) -- pinned here independently
    of app.py so a future edit to either side that breaks the parity is
    caught by pytest, not just by eyeballing the Streamlit page. The
    54.1 d figure also cross-checks tests/app_smoke_check.py's pinned
    Decision-page "Used vessel-days" == "54.0392" (54.1 - 54.0392 =
    0.0608 d residual, the same geometry)."""
    params = model.operating_default_params()
    europe_rt_now = (params.europe_laden_days + params.europe_ballast_days
                      + params.europe_port_days + params.loading_days)
    expected_horizon = float(np.ceil((2.0 * europe_rt_now + risk.PROGRAMME_TURNAROUND_DAYS) * 10.0) / 10.0)
    assert expected_horizon == pytest.approx(54.1, abs=1e-9)

    plan = risk.build_committed_programme(D, tables, params, 0, decision.FirstCargoState.FULLY_PRE_LIFT)
    assert plan.horizon_days == pytest.approx(expected_horizon)
    assert plan.legs[0].month_index == 0, "leg 1 must carry the caller's own month_index verbatim"
    assert plan.legs[0].cargo_number == 1


def test_build_committed_programme_propagates_index_error_for_bad_month_index(tables):
    """month_index outside the strip must raise, not silently clamp --
    the same IndexError decision.optimise_programme() already raises,
    uncaught here (app.py contains it the same way the Decision page's
    own programme branch already does)."""
    params = model.operating_default_params()
    with pytest.raises(IndexError):
        risk.build_committed_programme(D, tables, params, 999, None)


@pytest.mark.parametrize("params_factory", PARAMS_FACTORIES, ids=PARAMS_IDS)
@pytest.mark.parametrize("month_index", [0, 11])
def test_committed_programme_zero_shock_pnl_is_zero(tables, params_factory, month_index):
    """D.1's acceptance test, same R1-style construction as every other
    physical zero-shock test: a zero-return ScenarioSet must reprice to
    ~$0 P&L against historical_var_physical()'s own committed-programme
    base -- checked at month_index=0 (safe) AND month_index=11 (the
    boundary where a second leg, if the optimiser schedules one, lands on
    month 12 and is dropped by programme_leg_is_priceable() -- the
    zero-shock identity must still hold over whichever legs ARE priced)."""
    params = params_factory()
    result = risk.historical_var_physical(
        D, tables, params, portfolio="programme", month_index=month_index,
        scen=_zero_scenario(), first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT,
    )
    assert abs(float(result.pnl[0])) <= 0.01


def test_committed_programme_accepted_where_12cargo_and_hedged_are_not(tables):
    """D.1 positive-side complement to test_physical_basis_rejects_
    unsupported_portfolios: "programme" must NOT raise (it did not exist
    before this increment; 12cargo/hedged still correctly do -- see that
    test, unchanged)."""
    result = risk.historical_var_physical(
        D, tables, model.operating_default_params(), portfolio="programme",
        month_index=0, scen=_zero_scenario(), first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT,
    )
    assert result.portfolio == "programme"
    assert result.basis == "physical"


@pytest.mark.parametrize("params_factory", PARAMS_FACTORIES, ids=PARAMS_IDS)
@pytest.mark.parametrize("state", [None] + FIRST_CARGO_STATES, ids=["none"] + [s.value for s in FIRST_CARGO_STATES])
def test_committed_programme_sum_of_legs_identity_and_shape(tables, params_factory, state):
    """D.1/D.3's core identity, proven through the PUBLIC API rather than
    by peeking at risk.py's internals: the committed-programme portfolio's
    P&L must equal, scenario-by-scenario, the EXACT sum of independent
    "single"-portfolio P&L vectors for each of the plan's legs (leg 1
    under the caller's first_cargo_state, every later leg under None --
    D.1's own per-leg-state rule), using REAL (non-zero, 250-scenario)
    history -- this also exercises "shape/finiteness under real
    scenarios" in the same stroke. "single" is the already-tested,
    independent oracle (increment C); this test does not assume anything
    about which plan the live optimiser prefers for D/params_factory -- it
    reads that plan back and re-derives the expectation from it, so it
    stays meaningful even if workbook data changes which plan is best."""
    params = params_factory()
    month_index = 0
    scen = risk.build_scenarios(tables, D, lookback=250, method="naive")

    plan = risk.build_committed_programme(D, tables, params, month_index, state)
    assert plan.legs[0].month_index == month_index, "leg 1 must carry the caller's own month_index verbatim"

    expected_pnl = np.zeros(len(scen.dates))
    included = 0
    for leg in plan.legs:
        if not risk.programme_leg_is_priceable(leg):
            continue
        included += 1
        leg_state = state if leg.cargo_number == 1 else None
        leg_result = risk.historical_var_physical(
            D, tables, params, portfolio="single", basin=leg.route, month_index=leg.month_index,
            scen=scen, first_cargo_state=leg_state,
        )
        expected_pnl = expected_pnl + leg_result.pnl
    assert included >= 1, "leg 1 must always be included (its month_index is the caller's own, always 0..11)"

    prog = risk.historical_var_physical(
        D, tables, params, portfolio="programme", month_index=month_index, scen=scen, first_cargo_state=state,
    )
    assert prog.pnl.shape == (len(scen.dates),)
    assert np.isfinite(prog.pnl).all()
    np.testing.assert_allclose(prog.pnl, expected_pnl, rtol=1e-9, atol=1e-6)


def test_committed_programme_drops_out_of_range_tail_leg_exactly(tables):
    """Deterministic complement to the identity test above, independent of
    what the live optimiser happens to choose: a HAND-BUILT plan with leg
    2 beyond month 11 must reprice IDENTICALLY (exact, not approximate) to
    a single-leg plan containing only leg 1 -- proving the drop is exact,
    not merely that the function doesn't crash."""
    params = model.operating_default_params()
    state = decision.FirstCargoState.ALREADY_LOADED
    scen = risk.build_scenarios(tables, D, lookback=250, method="naive")

    leg1 = _leg(1, "Europe", 0)
    leg2 = _leg(2, "Asia", 12, load_month="2027-08-01", duration_days=47.0)
    exposures_two = risk._programme_leg_exposures(D, tables, params, _hand_built_plan(leg1, leg2), state)
    exposures_one = risk._programme_leg_exposures(D, tables, params, _hand_built_plan(leg1), state)
    assert [leg.cargo_number for leg, _ in exposures_two] == [1], "leg 2 (month 12) must be dropped"
    assert [leg.cargo_number for leg, _ in exposures_one] == [1]

    hh_row = model.snap(tables.hh, pd.Timestamp(D))
    ttf_row = model.snap(tables.ttf, pd.Timestamp(D))
    jkm_row = model.snap(tables.jkm, pd.Timestamp(D))
    fx_row = model.snap(tables.fx, pd.Timestamp(D))
    ch_row = model.snap(tables.charter, pd.Timestamp(D))
    charter = params.charter_override if params.charter_override is not None else float(ch_row["rate174"])
    cols = [f"c{i}" for i in range(1, risk.N_STRIP_COLS + 1)]
    base_hh = hh_row[cols].to_numpy(dtype=float)
    base_ttf = ttf_row[cols].to_numpy(dtype=float)
    base_jkm = jkm_row[cols].to_numpy(dtype=float)
    base_spot, base_o6, base_o1 = float(fx_row["spot"]), float(fx_row["o6"]), float(fx_row["o1"])

    scen_val_two = risk._vectorized_reprice_physical_programme(
        scen, D, base_hh, base_ttf, base_jkm, base_spot, base_o6, base_o1, charter, params, exposures_two,
    )
    scen_val_one = risk._vectorized_reprice_physical_programme(
        scen, D, base_hh, base_ttf, base_jkm, base_spot, base_o6, base_o1, charter, params, exposures_one,
    )
    np.testing.assert_array_equal(scen_val_two, scen_val_one)


def test_var_result_basis_field(tables):
    """D.3: VarResult gains an optional basis field, default "legacy" --
    every pre-existing keyword-style VarResult(...) construction (the two
    inside risk.py itself; none found elsewhere in the repo) keeps working
    unchanged -- set explicitly by both historical_var() ("legacy") and
    historical_var_physical() ("physical", including for "programme")."""
    params = model.operating_default_params()
    default_result = risk.VarResult(
        pnl=np.zeros(1), var95=0.0, var99=0.0, es95=0.0, es99=0.0, sd=0.0, n=1,
        portfolio="single", scen=_zero_scenario(),
    )
    assert default_result.basis == "legacy"

    legacy = risk.historical_var(D, tables, params, portfolio="single", basin="Europe",
                                  month_index=0, scen=_zero_scenario())
    assert legacy.basis == "legacy"
    assert legacy.summary()["basis"] == "legacy"

    physical = risk.historical_var_physical(D, tables, params, portfolio="single", basin="Europe",
                                             month_index=0, scen=_zero_scenario(),
                                             first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT)
    assert physical.basis == "physical"
    assert physical.summary()["basis"] == "physical"

    programme = risk.historical_var_physical(D, tables, params, portfolio="programme", month_index=0,
                                              scen=_zero_scenario(),
                                              first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT)
    assert programme.basis == "physical"
