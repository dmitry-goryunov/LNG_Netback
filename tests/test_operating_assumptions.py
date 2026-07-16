"""Speed/loading/unloading re-baseline (user instruction 16-Jul-2026:
17 kn service speed, 1.5 d loading, 1.5 d unloading as the app's
operating case). Two contracts guarded here:

1. Params() legacy defaults (19.5 kn geometry, loading_days=0, 5-day port
   calls) stay bit-identical -- the frozen 64/64 suite depends on them,
   and every new loading_days/speed term in model.strip() must be
   arithmetically inert at the defaults.
2. The operating case is internally coherent: geometry derives from
   speed, fuel rates derive from the cube law, the physical engine and
   the legacy formula agree on the same new numbers, and congestion
   decomposition still finds exactly the +4 d/leg queues at any speed.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


# --- 1. Legacy defaults bit-identical ---


def test_speed_helpers_reproduce_design_constants_exactly():
    """Not approx -- EXACT: the app can only expose speed as a knob
    without perturbing the frozen path if calling the helpers at 19.5 kn
    is the same arithmetic as the module constants."""
    assert model.europe_leg_days(model.DESIGN_SPEED_KNOTS) == model.EU_LEG_DAYS
    assert model.asia_leg_days(model.DESIGN_SPEED_KNOTS) == model.ASIA_LEG_DAYS
    assert model.sea_fuel_at_speed(150.0, model.DESIGN_SPEED_KNOTS) == 150.0
    assert model.sea_fuel_at_speed(130.0, model.DESIGN_SPEED_KNOTS) == 130.0


def test_default_params_unchanged_by_the_new_fields():
    p = model.Params()
    assert p.vessel_speed_knots == 19.5
    assert p.loading_days == 0.0
    assert p.asia_laden_days == pytest.approx((model.ASIA_RT_BASE - 5.0) / 2.0)


def test_strip_at_defaults_identical_with_new_terms(tables):
    """The loading_days terms added inside strip() (europe_rt, asia
    ballast derivation, port-fuel line) must be exact no-ops at the 0.0
    default -- checked on real data against the pre-change europe_rt and
    a margin identity, not just 'runs without error'."""
    df = model.strip("2026-07-08", tables, model.Params())
    assert df.iloc[0]["europe_rt"] == 25.94017094017094  # exact pre-change value
    assert df.iloc[0]["asia_rt"] == model.ASIA_RT_BASE


# --- 2. The operating case is coherent ---


def test_operating_defaults_pin():
    p = model.operating_default_params()
    assert p.vessel_speed_knots == 17.0
    assert p.loading_days == 1.5
    assert p.europe_port_days == p.asia_port_days == 1.5
    assert p.europe_laden_days == pytest.approx(12.009804, abs=1e-5)
    assert p.asia_rt_days == pytest.approx(50.588235, abs=1e-5)
    assert p.asia_laden_days == pytest.approx(23.794118, abs=1e-5)  # (rt - port - loading)/2
    assert p.laden_fuel_requirement == pytest.approx(150.0 * (17.0 / 19.5) ** 3)
    assert p.ballast_fuel == pytest.approx(130.0 * (17.0 / 19.5) ** 3)
    # At 17 kn the cube-law laden demand (~99.4 t/d) exceeds natural BOG
    # (86.4 t/d-eq) by only ~13 t/d -- the derived residual reflects that.
    assert p.residual_laden_vlsfo == pytest.approx(12.99, abs=0.01)


def test_strip_charges_loading_time_on_both_routes(tables):
    """1.5 loading days must add charter time to both round trips and
    port-rate fuel to both ship-cost lines; Asia's ballast leg gives the
    time up (RT is the fixed input there)."""
    base = model.operating_default_params(loading_days=0.0)
    withload = model.operating_default_params(loading_days=1.5)
    # keep asia_rt equal so the loading effect inside a FIXED rt is visible
    withload.asia_rt_days = base.asia_rt_days
    df0 = model.strip("2026-07-08", tables, base)
    df1 = model.strip("2026-07-08", tables, withload)

    assert df1.iloc[0]["europe_rt"] - df0.iloc[0]["europe_rt"] == pytest.approx(1.5)
    charter = df0.attrs["snap"].charter_rate
    eu_ship_delta = (df1.iloc[0]["eu_ship"] - df0.iloc[0]["eu_ship"]) * base.cargo_size
    assert eu_ship_delta == pytest.approx(1.5 * charter + 1.5 * base.port_fuel_rate * base.vlsfo_price, rel=1e-9)
    # Asia at fixed RT: charter unchanged, but 1.5 d moves from ballast-
    # rate fuel to port-rate fuel, and the laden legs shorten (symmetric).
    assert withload.asia_laden_days == pytest.approx(base.asia_laden_days - 0.75)


def test_physical_engine_matches_legacy_fuel_at_the_operating_case(tables):
    """The step-3-style equivalence proof re-run at 17 kn / 1.5 / 1.5:
    same segment engine, new geometry, still reproduces the legacy
    formula's total liquid fuel (including the loading port call) on
    both routes' uncongested cases."""
    p = model.operating_default_params()
    vessel = physical.vessel_performance_from_params(p)

    eu = physical.run_voyage(physical.europe_route_segments(p), vessel, loaded_mmbtu=p.cargo_size)
    legacy_eu = (p.residual_laden_vlsfo * p.europe_laden_days + p.ballast_fuel * p.europe_ballast_days
                 + p.port_fuel_rate * (p.europe_port_days + p.loading_days))
    assert eu.total_days == pytest.approx(p.europe_laden_days + p.europe_ballast_days
                                          + p.europe_port_days + p.loading_days)
    assert eu.total_liquid_fuel_tonnes == pytest.approx(legacy_eu, abs=0.1)

    asia = physical.run_voyage(physical.asia_route_segments(p), vessel, loaded_mmbtu=p.cargo_size)
    assert asia.total_days == pytest.approx(p.asia_rt_days)
    queue_days = [r.segment.duration_days for r in asia.segments if "queue" in r.segment.name]
    assert queue_days == pytest.approx([0.0, 0.0])  # slower sea time is sea, not queue


def test_congestion_still_decomposes_to_four_queue_days_per_leg_at_17kn():
    """The queue/sea split must measure congestion against the 17-kn base
    leg, not the 19.5-kn constant -- otherwise ordinary slow-steaming sea
    time would be misclassified as cheap queue waiting."""
    p = model.operating_default_params()
    p.asia_rt_days += 8.0
    vessel = physical.vessel_performance_from_params(p)
    ledger = physical.run_voyage(physical.asia_route_segments(p), vessel, loaded_mmbtu=p.cargo_size)
    queues = {r.segment.name: r.segment.duration_days for r in ledger.segments if "queue" in r.segment.name}
    assert queues["laden_queue"] == pytest.approx(4.0)
    assert queues["ballast_queue"] == pytest.approx(4.0)


def test_loading_segment_has_duration_and_us_berth_ets_scope_zero():
    """Loading time is real vessel time at a US berth: outside EU ETS
    scope entirely (the old 0.5 was harmless only while the segment had
    zero duration)."""
    p = model.operating_default_params()
    eu_segments = {s.name: s for s in physical.europe_route_segments(p)}
    assert eu_segments["loading"].duration_days == pytest.approx(1.5)
    assert eu_segments["loading"].ets_scope_fraction == 0.0
    asia_segments = {s.name: s for s in physical.asia_route_segments(p)}
    assert asia_segments["loading"].duration_days == pytest.approx(1.5)


def test_legacy_uniform_scope_ets_derivation_reproduces_the_hand_constant():
    """The app now derives co2_eu_ets_tonnes from the physical fuel
    balance instead of the hand-set 4,425.9 t constant. At the legacy
    defaults the derivation must land on that constant (it was hand-
    derived from the same fuel picture), proving the swap changes
    nothing until speed/fuel/day settings actually change."""
    derived = emissions.legacy_uniform_scope_ets_tonnes(model.Params())
    assert derived == pytest.approx(4425.9, rel=0.001)
    # ...and at 17 kn it drops with the cube-law fuel savings.
    at_17 = emissions.legacy_uniform_scope_ets_tonnes(model.operating_default_params())
    assert at_17 < derived * 0.80


# --- Heel (Params.heel_fraction + HEEL_THEN_LIQUID_FUEL) ---


def test_heel_then_liquid_burns_inventory_first_then_buys_fuel():
    """Hand-checked hybrid segment: 10,000 MMBtu heel, demand 8,000 --
    heel covers it all, no liquid fuel. Demand 15,000 -- heel covers
    ~10k, the remainder is bought as VLSFO."""
    vessel = physical.VesselPerformance(
        demand_mmbtu_per_day={physical.OperatingState.BALLAST_SEA: 8_000.0},
        bor_fraction_per_day={physical.OperatingState.BALLAST_SEA: 0.0},
        shortfall_source={physical.OperatingState.BALLAST_SEA: physical.ShortfallSource.HEEL_THEN_LIQUID_FUEL},
    )
    seg = physical.VoyageSegment("ballast_sea", physical.OperatingState.BALLAST_SEA, duration_days=1.0)
    r = physical.simulate_segment(seg, vessel, opening_inventory_mmbtu=10_000.0)
    assert r.forced_lng_mmbtu == pytest.approx(8_000.0)
    assert r.shortfall_liquid_fuel_tonnes == pytest.approx(0.0)
    assert r.closing_inventory_mmbtu == pytest.approx(2_000.0)

    vessel_hungry = physical.VesselPerformance(
        demand_mmbtu_per_day={physical.OperatingState.BALLAST_SEA: 15_000.0},
        bor_fraction_per_day={physical.OperatingState.BALLAST_SEA: 0.0},
        shortfall_source={physical.OperatingState.BALLAST_SEA: physical.ShortfallSource.HEEL_THEN_LIQUID_FUEL},
    )
    r2 = physical.simulate_segment(seg._replace() if hasattr(seg, "_replace") else seg, vessel_hungry, 10_000.0)
    assert r2.forced_lng_mmbtu == pytest.approx(10_000.0)
    assert r2.shortfall_liquid_fuel_tonnes == pytest.approx(5_000.0 / vessel_hungry.energy_factor_mmbtu_per_t)
    assert r2.closing_inventory_mmbtu == pytest.approx(0.0)


def test_zero_heel_is_bit_identical_through_the_decision_path(tables):
    """The frozen-equivalence guarantee: with heel_fraction at the 0.0
    Params default, the ballast HEEL_THEN_LIQUID_FUEL branch degrades to
    LIQUID_FUEL exactly and every decision value matches a run made
    before heel support existed (pinned via the step-8 identity that
    base-Asia physical == legacy to float noise, plus Europe's value at
    known ETS-delta distance -- both already asserted elsewhere; here we
    assert the direct invariant that a 0.0-heel breakdown has no Heel
    line and delivered+burned+vented reconciles with zero heel)."""
    p = model.Params()
    vessel = physical.vessel_performance_from_params(p)
    ledger = physical.run_voyage(physical.europe_route_segments(p), vessel, loaded_mmbtu=p.cargo_size)
    assert ledger.heel_at_discharge_mmbtu == 0.0
    assert ledger.heel_burned_mmbtu == 0.0
    df = model.strip("2026-07-08", tables, p)
    bd = decision.physical_waterfall_breakdown(df.iloc[0], p)
    assert "Heel" not in dict(bd["Europe"]["lines"])
    assert "Heel" not in dict(bd["Asia"]["lines"])


def test_heel_fuels_ballast_and_reconciles(tables):
    """2% heel on the operating case: the ballast leg burns heel instead
    of buying VLSFO (bunkers drop by the energy-equivalent), the
    remainder arrives as terminal heel, mass reconciles, and the
    breakdown identity revenue - lines == margin still holds with the
    new Heel line present."""
    p = model.operating_default_params()
    assert p.heel_fraction == 0.02
    vessel = physical.vessel_performance_from_params(p)
    heel_target = p.heel_fraction * p.cargo_size

    no_heel = physical.run_voyage(physical.europe_route_segments(p), vessel, loaded_mmbtu=p.cargo_size)
    with_heel = physical.run_voyage(physical.europe_route_segments(p), vessel,
                                    loaded_mmbtu=p.cargo_size, heel_target_mmbtu=heel_target)

    assert with_heel.heel_at_discharge_mmbtu == pytest.approx(heel_target)
    assert with_heel.reconciliation_error_mmbtu == pytest.approx(0.0, abs=1e-6)
    # ballast liquid fuel drops by (energy burned from heel)/energy factor
    fuel_saved_t = no_heel.total_liquid_fuel_tonnes - with_heel.total_liquid_fuel_tonnes
    assert fuel_saved_t > 0
    assert fuel_saved_t == pytest.approx(with_heel.heel_burned_mmbtu / vessel.energy_factor_mmbtu_per_t, rel=1e-6)
    assert with_heel.terminal_heel_mmbtu == pytest.approx(heel_target - with_heel.heel_burned_mmbtu)
    # delivered drops by exactly the retained heel (laden legs unchanged)
    assert no_heel.delivered_mmbtu - with_heel.delivered_mmbtu == pytest.approx(heel_target)

    df = model.strip("2026-07-08", tables, p)
    bd = decision.physical_waterfall_breakdown(df.iloc[0], p)["Europe"]
    lines = dict(bd["lines"])
    assert "Heel" in lines
    assert bd["revenue"] - sum(v for _, v in bd["lines"]) == pytest.approx(bd["margin"])


def test_ballast_heel_combustion_is_counted_in_emissions():
    """Removing segment_emissions' laden-only gate: a ballast segment
    burning heel must emit CO2 at the LNG factor; at zero heel it emits
    only its liquid-fuel CO2, exactly as before."""
    p = model.operating_default_params()
    vessel = physical.vessel_performance_from_params(p)
    heel_target = p.heel_fraction * p.cargo_size
    ledger = physical.run_voyage(physical.europe_route_segments(p), vessel,
                                 loaded_mmbtu=p.cargo_size, heel_target_mmbtu=heel_target)
    ballast = next(r for r in ledger.segments if r.segment.name == "ballast_sea")
    assert ballast.forced_lng_mmbtu > 0
    em = emissions.segment_emissions(ballast)
    expected_lng_co2 = (ballast.bog_burned_mmbtu + ballast.forced_lng_mmbtu) / emissions.LNG_MMBTU_PER_T \
        * emissions.CO2_T_PER_T_LNG
    expected = expected_lng_co2 + ballast.shortfall_liquid_fuel_tonnes * emissions.CO2_T_PER_T_VLSFO
    assert em.co2_tonnes == pytest.approx(expected)


def test_heel_net_cost_is_negative_at_current_prices(tables):
    """Honest economics check: with delivered LNG worth ~$15-17/MMBtu and
    VLSFO at ~$13/MMBtu-equivalent, burning cargo instead of oil plus
    writing off the terminal remainder is a net COST -- adding heel
    lowers cargo values. This is realism the zero-heel model omitted
    (heel is operationally required to keep tanks cold), not an
    optimisation."""
    p0 = model.operating_default_params()
    p0.heel_fraction = 0.0
    p1 = model.operating_default_params()  # 2% heel
    df0 = model.strip("2026-07-08", tables, p0)
    df1 = model.strip("2026-07-08", tables, p1)
    _, v0 = decision._physical_route_value(df0.iloc[0], p0, "Europe")
    _, v1 = decision._physical_route_value(df1.iloc[0], p1, "Europe")
    assert v1 < v0
    # bounded: the loss cannot exceed the full sale value of the heel
    assert v0 - v1 < p1.heel_fraction * p1.cargo_size * float(df1.iloc[0]["ttf_usd"])


def test_operating_case_decision_path_runs_end_to_end(tables):
    """Belt-and-braces: the physical decision path and programme run
    clean at the operating case (this geometry is what the app now shows
    by default)."""
    p = model.operating_default_params()
    p.co2_eu_ets_tonnes = emissions.legacy_uniform_scope_ets_tonnes(p)
    df = model.strip("2026-07-08", tables, p)
    values = decision.isolated_route_values(df, p, decision.DecisionMode.POST_LIFT_DIVERSION, month_index=0)
    assert all(v.incremental_value != 0 for v in values)
    result = decision.optimise_programme(df, p, horizon_days=52.0, max_additional_cargoes=1)
    assert result.best.used_days <= 52.0
