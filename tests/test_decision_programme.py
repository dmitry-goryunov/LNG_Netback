from __future__ import annotations

import math
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

import data
import decision
import emissions
import model
import physical


@pytest.fixture(scope="module")
def tables():
    path = os.environ.get(data.ENV_VAR_NAME)
    if not path:
        local = Path(__file__).resolve().parents[1] / "LNG history.xlsx"
        path = str(local) if local.exists() else data.default_data_path()
    if not path:
        pytest.skip(f"set {data.ENV_VAR_NAME} to run workbook-backed tests")
    return data.load_all(str(path))


@pytest.fixture()
def params():
    return model.Params()


def test_decision_mode_cost_policy():
    assert decision.cost_policy(decision.DecisionMode.POST_LIFT_DIVERSION, "procurement") == decision.CostTreatment.SUNK
    assert decision.cost_policy(decision.DecisionMode.POST_LIFT_DIVERSION, "loading") == decision.CostTreatment.SUNK
    assert decision.cost_policy(decision.DecisionMode.PRE_LIFT_CARGO, "procurement") == decision.CostTreatment.INCLUDED
    assert decision.cost_policy(decision.DecisionMode.VESSEL_PROGRAMME, "procurement", future_cargo=True) == decision.CostTreatment.INCLUDED
    assert decision.cost_policy(decision.DecisionMode.VESSEL_PROGRAMME, "charter") == decision.CostTreatment.INCLUDED


def test_post_lift_adds_back_only_sunk_procurement_and_loading(tables, params):
    """Since Phase 2 step 8, full_cargo_value for POST_LIFT_DIVERSION comes
    from the physical engine (decision._physical_route_value()), not the
    legacy row's eu_cargo/asia_cargo -- see
    test_isolated_route_values_use_physical_engine_for_non_screen_modes
    below for that equivalence. This test's own job is narrower and
    engine-agnostic: whatever full_cargo_value is, the sunk-cost add-back
    must be exactly procurement + loading on the loaded-cargo basis."""
    df = model.strip("2026-07-08", tables, params)
    row = df.iloc[0]
    values = {v.route: v for v in decision.isolated_route_values(
        df, params, decision.DecisionMode.POST_LIFT_DIVERSION, month_index=0
    )}
    sunk = (row["proc"] + params.loading) * params.cargo_size
    assert values["Europe"].incremental_value - values["Europe"].full_cargo_value == pytest.approx(sunk)
    assert values["Asia"].incremental_value - values["Asia"].full_cargo_value == pytest.approx(sunk)


def test_pre_lift_value_remains_full_cargo_margin(tables, params):
    df = model.strip("2026-07-08", tables, params)
    values = {v.route: v for v in decision.isolated_route_values(
        df, params, decision.DecisionMode.PRE_LIFT_CARGO, month_index=0
    )}
    assert values["Europe"].incremental_value == pytest.approx(values["Europe"].full_cargo_value)
    assert values["Asia"].incremental_value == pytest.approx(values["Asia"].full_cargo_value)


def test_52_day_base_case_has_two_europe_cycles_and_one_asia_cycle(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_BASE)
    df = model.strip("2026-07-08", tables, params)
    result = decision.optimise_programme(
        df, params, horizon_days=52.0, max_additional_cargoes=1
    )
    plans = {p.sequence: p for p in result.alternatives}

    assert "Europe -> Europe" in plans
    assert "Asia" in plans
    assert plans["Europe -> Europe"].used_days == pytest.approx(2 * (model.EU_LEG_DAYS * 2 + 5.0))
    assert plans["Europe -> Europe"].residual_days == pytest.approx(0.11965811965811923)
    assert plans["Asia"].used_days == pytest.approx(model.ASIA_RT_BASE)
    assert result.best.sequence == "Europe -> Europe"


def test_future_cargo_uses_later_forward_month_and_pre_lift_value(tables):
    """second.value is pre-lift (no sunk-cost add-back), so it must equal
    the physical engine's own full_cargo_value for month 1 -- not the
    legacy row's eu_cargo (see decision._physical_route_value(), Phase 2
    step 8). first.value (post-lift, sunk procurement/loading added back)
    is compared against that same physical-engine month-0 value rather
    than legacy's eu_cargo, for the same reason."""
    params = model.Params(asia_rt_days=model.ASIA_RT_BASE)
    df = model.strip("2026-07-08", tables, params)
    result = decision.optimise_programme(
        df, params, horizon_days=52.0, max_additional_cargoes=1
    )
    plan = next(p for p in result.alternatives if p.sequence == "Europe -> Europe")
    first, second = plan.legs

    assert first.month_index == 0
    assert second.month_index == 1
    assert second.load_month == pd.Timestamp("2026-09-01")
    assert second.decision_mode == decision.DecisionMode.PRE_LIFT_CARGO
    _, expected_month0_full = decision._physical_route_value(df.iloc[0], params, "Europe")
    _, expected_month1_full = decision._physical_route_value(df.iloc[1], params, "Europe")
    assert second.value == pytest.approx(expected_month1_full)
    assert first.value > expected_month0_full  # procurement/loading are sunk for cargo 1


def test_54_day_congested_asia_is_infeasible(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_CONG)
    df = model.strip("2026-07-08", tables, params)
    result = decision.optimise_programme(
        df, params, horizon_days=54.0, max_additional_cargoes=1
    )
    assert all(plan.legs[0].route != "Asia" for plan in result.alternatives)
    assert "Europe -> Europe" in {p.sequence for p in result.alternatives}


def test_55_day_congested_asia_and_two_europe_cycles_both_fit(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_CONG)
    df = model.strip("2026-07-08", tables, params)
    result = decision.optimise_programme(
        df, params, horizon_days=55.0, max_additional_cargoes=1
    )
    plans = {p.sequence: p for p in result.alternatives}
    assert "Asia" in plans
    assert "Europe -> Europe" in plans
    assert plans["Asia"].residual_days == pytest.approx(55.0 - model.ASIA_RT_CONG)
    assert plans["Europe -> Europe"].residual_days == pytest.approx(55.0 - 2 * (2 * model.EU_LEG_DAYS + 5.0))


def test_no_fractional_or_partial_voyage_is_scheduled(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_BASE)
    df = model.strip("2026-07-08", tables, params)
    result = decision.optimise_programme(
        df, params, horizon_days=51.8, max_additional_cargoes=1
    )
    assert "Europe -> Europe" not in {p.sequence for p in result.alternatives}
    assert all(p.used_days <= 51.8 + 1e-9 for p in result.alternatives)


def test_residual_vessel_value_is_added_once(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_BASE)
    df = model.strip("2026-07-08", tables, params)
    rate = 100_000.0
    result = decision.optimise_programme(
        df, params, horizon_days=52.0, max_additional_cargoes=1,
        residual_value_per_day=rate,
    )
    plan = next(p for p in result.alternatives if p.sequence == "Asia")
    leg_sum = sum(leg.value for leg in plan.legs)
    assert plan.residual_value == pytest.approx(plan.residual_days * rate)
    assert plan.total_value == pytest.approx(leg_sum + plan.residual_value)


def test_fx_extrapolation_is_explicit_on_current_m12(tables, params):
    df = model.strip("2026-07-08", tables, params)
    flagged = model.fx_extrapolated_rows(df)
    assert not flagged.empty
    assert 11 in flagged.index
    assert bool(df.iloc[11]["fx_extrapolated"])
    assert df.iloc[11]["fx_tenor_months"] > 12.0


# --- Phase 2 step 8: route_value()'s dispatch to the physical engine ---
# (docs/PHASE2_PLAN.md Section 8). These pin the wiring itself, separate
# from the cost-treatment invariants above.


def test_renewal_rate_screen_mode_still_matches_legacy_row_exactly(tables, params):
    """RENEWAL_RATE_SCREEN is not reachable through isolated_route_values()
    or optimise_programme() in this codebase today (both always pass one
    of the other three modes) -- this test guards route_value()'s own
    documented contract directly, so a future direct caller of this mode
    is not silently switched onto the physical engine."""
    df = model.strip("2026-07-08", tables, params)
    row = df.iloc[0]
    for route, legacy_value, legacy_days in (
        ("Europe", row["eu_cargo"], row["europe_rt"]),
        ("Asia", row["asia_cargo"], row["asia_rt"]),
    ):
        value = decision.route_value(
            row, params, route, decision.DecisionMode.RENEWAL_RATE_SCREEN, month_index=0
        )
        assert value.full_cargo_value == pytest.approx(legacy_value)
        assert value.duration_days == pytest.approx(legacy_days)


def test_non_screen_modes_delegate_to_physical_route_value(tables, params):
    """route_value() for the three non-screen modes must return exactly
    what decision._physical_route_value() computes -- proves the dispatch
    itself, not just that some plausible-looking number comes out."""
    df = model.strip("2026-07-08", tables, params)
    row = df.iloc[0]
    for mode in (
        decision.DecisionMode.POST_LIFT_DIVERSION,
        decision.DecisionMode.PRE_LIFT_CARGO,
        decision.DecisionMode.VESSEL_PROGRAMME,
    ):
        for route in ("Europe", "Asia"):
            expected_days, expected_full = decision._physical_route_value(row, params, route)
            value = decision.route_value(row, params, route, mode, month_index=0)
            assert value.duration_days == pytest.approx(expected_days)
            assert value.full_cargo_value == pytest.approx(expected_full)


def test_base_asia_physical_value_matches_legacy_to_floating_point_noise(tables):
    """Companion to test_physical_legacy_equivalence.py's Asia base-case
    tests: with no queue segments and zero venting/reliquefaction, the
    physical engine's full_cargo_value should reproduce the legacy
    asia_cargo formula almost exactly (no ETS term applies to Asia in
    either model)."""
    params = model.Params(asia_rt_days=model.ASIA_RT_BASE)
    df = model.strip("2026-07-08", tables, params)
    row = df.iloc[0]
    _, physical_full = decision._physical_route_value(row, params, "Asia")
    assert physical_full == pytest.approx(row["asia_cargo"], rel=1e-5)


def test_europe_physical_value_is_lower_than_legacy_by_the_documented_ets_scope_fix(tables, params):
    """Quantifies, rather than just flags a not-equal, the Europe
    divergence introduced by wiring in the physical engine: the correct
    per-segment EU ETS scope (0.5 sea / 1.0 discharge / 0.5 ballast, see
    physical.europe_route_segments) costs more CO2e than the legacy
    uniform-0.5 constant (~4.45%, test_physical_legacy_equivalence.py),
    which shows up here as a reduction in full_cargo_value. Fuel/delivered-
    cargo differences are floating-point noise for the uncongested base
    case (same file's Europe equivalence tests), so the ETS delta alone
    should explain essentially all of the gap -- verified, not assumed."""
    df = model.strip("2026-07-08", tables, params)
    row = df.iloc[0]
    legacy_full = row["eu_cargo"]
    _, physical_full = decision._physical_route_value(row, params, "Europe")

    fx_l = float(row["fx"])
    phase = model.phase_for_year(pd.Timestamp(row["load_month"]).year)
    legacy_ets = params.co2_eu_ets_tonnes * params.eua_price * phase * fx_l

    vessel = physical.vessel_performance_from_params(params)
    ledger = physical.run_voyage(
        physical.europe_route_segments(params), vessel, loaded_mmbtu=params.cargo_size, heel_target_mmbtu=0.0
    )
    physical_ets = emissions.ets_cost_usd(emissions.voyage_emissions(ledger), params.eua_price, fx_l) * phase

    assert physical_full < legacy_full
    gap = legacy_full - physical_full
    ets_delta = physical_ets - legacy_ets
    assert ets_delta > 0
    assert gap == pytest.approx(ets_delta, rel=1e-3)


def test_physical_waterfall_breakdown_reconciles_to_physical_route_value(tables, params):
    """app.py's Decision-page waterfall/Sankey charts call
    decision.physical_waterfall_breakdown() directly (not
    model.waterfall_breakdown(), which reads static model.strip() columns
    the non-screen modes no longer use) -- this pins the reconciliation
    invariant the chart's own caption claims: revenue minus every line
    equals margin, and margin*cargo_size equals _physical_route_value()'s
    full_cargo_value, for both routes."""
    df = model.strip("2026-07-08", tables, params)
    row = df.iloc[0]
    bd = decision.physical_waterfall_breakdown(row, params)
    for route in ("Europe", "Asia"):
        b = bd[route]
        assert b["revenue"] - sum(v for _, v in b["lines"]) == pytest.approx(b["margin"])
        _, expected_full = decision._physical_route_value(row, params, route)
        assert b["margin"] * params.cargo_size == pytest.approx(expected_full)
    # Europe carries an explicit ETS line; Asia (zero ETS scope) does not,
    # matching model.waterfall_breakdown()'s own asia_lines shape.
    assert "ETS" in dict(bd["Europe"]["lines"])
    assert "ETS" not in dict(bd["Asia"]["lines"])


def test_congested_asia_physical_value_exceeds_legacy_flat_rate_fuel_assumption(tables):
    """Asia has zero EU ETS exposure in both models, so the congested-Asia
    divergence is purely the fuel/BOG effect documented in
    test_physical_legacy_equivalence.py: queue time is charged at the
    (lower) queue demand rate rather than the full sea-passage rate, and
    the laden queue's BOG surplus is reliquefied (stays in cargo) rather
    than assumed lost, at the engine's default reliq capacity. Both push
    full_cargo_value up relative to legacy's flat-rate assumption -- a
    real, quantified improvement, not a rounding artifact. The ratio band
    is deliberately wider than the pure-physics fuel-tonnes ratio pinned
    in test_physical_legacy_equivalence.py (0.86-0.88), since this is a
    dollar figure that also moves with the day's JKM price level."""
    params = model.Params(asia_rt_days=model.ASIA_RT_CONG)
    df = model.strip("2026-07-08", tables, params)
    row = df.iloc[0]
    legacy_full = row["asia_cargo"]
    _, physical_full = decision._physical_route_value(row, params, "Asia")
    ratio = physical_full / legacy_full
    assert 1.0 < ratio < 1.05, (
        f"expected the physical engine's congested-Asia full_cargo_value ({physical_full:,.0f}) to exceed "
        f"legacy's flat-rate asia_cargo ({legacy_full:,.0f}) by a modest margin; got ratio {ratio:.4f}"
    )
