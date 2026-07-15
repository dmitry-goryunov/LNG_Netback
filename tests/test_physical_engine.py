"""Unit tests for physical.py's segment-balance primitives and route
builders (docs/PHASE2_PLAN.md Section 8 steps 1-2). No workbook
dependency -- physical.py only imports model.py for the Params type used
by the route builders, not data.py, so these run without LNG_HISTORY_XLSX.

The full legacy-equivalence test against model.py's implicit constants,
run through the actual route builders, is in
tests/test_physical_legacy_equivalence.py (Section 8 step 3).
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import physical
from physical import (
    OperatingState, ShortfallSource, VesselPerformance, VoyageSegment,
    run_voyage, simulate_segment,
)


def _vessel(**overrides) -> VesselPerformance:
    return VesselPerformance(**overrides)


# --- simulate_segment: hand-computed single-segment cases -----------------

def test_shortfall_case_matches_hand_computation():
    """opening=1000 MMBtu, BOR=0.01/d, 2 days -> natural_bog=20 MMBtu.
    demand=15 MMBtu/d * 2d = 30 MMBtu > natural_bog -> full BOG burned,
    shortfall=10 MMBtu met by liquid fuel at the vessel's energy factor."""
    vessel = _vessel(
        demand_mmbtu_per_day={OperatingState.LADEN_SEA: 15.0},
        bor_fraction_per_day={OperatingState.LADEN_SEA: 0.01},
        energy_factor_mmbtu_per_t=40.0,
    )
    seg = VoyageSegment("leg", OperatingState.LADEN_SEA, duration_days=2.0)
    r = simulate_segment(seg, vessel, opening_inventory_mmbtu=1000.0)

    assert r.natural_bog_mmbtu == pytest.approx(20.0)
    assert r.demand_mmbtu == pytest.approx(30.0)
    assert r.bog_burned_mmbtu == pytest.approx(20.0)
    assert r.surplus_mmbtu == pytest.approx(0.0)
    assert r.shortfall_mmbtu == pytest.approx(10.0)
    assert r.forced_lng_mmbtu == pytest.approx(0.0)
    assert r.shortfall_liquid_fuel_tonnes == pytest.approx(10.0 / 40.0)
    # closing = opening - bog_burned - vented - forced_lng = 1000 - 20 - 0 - 0
    assert r.closing_inventory_mmbtu == pytest.approx(980.0)


def test_surplus_with_no_reliquefaction_all_vented():
    """demand below natural BOG, zero reliq capacity -> all surplus vented."""
    vessel = _vessel(
        demand_mmbtu_per_day={OperatingState.LADEN_SEA: 5.0},
        bor_fraction_per_day={OperatingState.LADEN_SEA: 0.01},
        reliq_capacity_mmbtu_per_day=0.0,
    )
    seg = VoyageSegment("leg", OperatingState.LADEN_SEA, duration_days=2.0)
    r = simulate_segment(seg, vessel, opening_inventory_mmbtu=1000.0)

    assert r.natural_bog_mmbtu == pytest.approx(20.0)
    assert r.demand_mmbtu == pytest.approx(10.0)
    assert r.bog_burned_mmbtu == pytest.approx(10.0)
    assert r.surplus_mmbtu == pytest.approx(10.0)
    assert r.reliquefied_mmbtu == pytest.approx(0.0)
    assert r.vented_mmbtu == pytest.approx(10.0)
    assert r.shortfall_mmbtu == pytest.approx(0.0)
    assert r.closing_inventory_mmbtu == pytest.approx(1000.0 - 20.0)  # bog_burned + vented = natural_bog


def test_full_reliquefaction_capacity_eliminates_venting():
    vessel = _vessel(
        demand_mmbtu_per_day={OperatingState.LADEN_SEA: 5.0},
        bor_fraction_per_day={OperatingState.LADEN_SEA: 0.01},
        reliq_capacity_mmbtu_per_day=100.0,  # far more than any plausible surplus
    )
    seg = VoyageSegment("leg", OperatingState.LADEN_SEA, duration_days=2.0)
    r = simulate_segment(seg, vessel, opening_inventory_mmbtu=1000.0)

    assert r.surplus_mmbtu == pytest.approx(10.0)
    assert r.reliquefied_mmbtu == pytest.approx(10.0)
    assert r.vented_mmbtu == pytest.approx(0.0)
    # closing = opening - natural_bog + reliquefied = 1000 - 20 + 10
    assert r.closing_inventory_mmbtu == pytest.approx(990.0)


def test_partial_reliquefaction_capacity_caps_reliquefied_not_vented():
    vessel = _vessel(
        demand_mmbtu_per_day={OperatingState.LADEN_SEA: 5.0},
        bor_fraction_per_day={OperatingState.LADEN_SEA: 0.01},
        reliq_capacity_mmbtu_per_day=3.0,  # 6 MMBtu over 2 days, less than the 10 MMBtu surplus
    )
    seg = VoyageSegment("leg", OperatingState.LADEN_SEA, duration_days=2.0)
    r = simulate_segment(seg, vessel, opening_inventory_mmbtu=1000.0)

    assert r.surplus_mmbtu == pytest.approx(10.0)
    assert r.reliquefied_mmbtu == pytest.approx(6.0)
    assert r.vented_mmbtu == pytest.approx(4.0)


def test_forced_vaporisation_shortfall_mode_draws_down_inventory_not_liquid_fuel():
    vessel = _vessel(
        demand_mmbtu_per_day={OperatingState.LADEN_SEA: 15.0},
        bor_fraction_per_day={OperatingState.LADEN_SEA: 0.01},
        shortfall_source=ShortfallSource.FORCED_VAPORISATION,
    )
    seg = VoyageSegment("leg", OperatingState.LADEN_SEA, duration_days=2.0)
    r = simulate_segment(seg, vessel, opening_inventory_mmbtu=1000.0)

    assert r.shortfall_mmbtu == pytest.approx(10.0)
    assert r.forced_lng_mmbtu == pytest.approx(10.0)
    assert r.shortfall_liquid_fuel_tonnes == pytest.approx(0.0)
    # closing = opening - natural_bog - forced_lng = 1000 - 20 - 10
    assert r.closing_inventory_mmbtu == pytest.approx(970.0)


def test_negative_opening_inventory_raises():
    vessel = _vessel()
    seg = VoyageSegment("leg", OperatingState.LADEN_SEA, duration_days=1.0)
    with pytest.raises(ValueError, match="negative opening inventory"):
        simulate_segment(seg, vessel, opening_inventory_mmbtu=-1.0)


def test_demand_exceeding_available_cargo_raises_rather_than_going_negative():
    """FORCED_VAPORISATION with demand the inventory can never cover."""
    vessel = _vessel(
        demand_mmbtu_per_day={OperatingState.LADEN_SEA: 1000.0},
        bor_fraction_per_day={OperatingState.LADEN_SEA: 0.0},
        shortfall_source=ShortfallSource.FORCED_VAPORISATION,
    )
    seg = VoyageSegment("leg", OperatingState.LADEN_SEA, duration_days=5.0)
    with pytest.raises(ValueError, match="closing inventory would go negative"):
        simulate_segment(seg, vessel, opening_inventory_mmbtu=10.0)


def test_bor_for_unconfigured_state_raises_clear_error():
    vessel = _vessel(bor_fraction_per_day={OperatingState.LADEN_SEA: 0.001})
    with pytest.raises(ValueError, match="no boil-off rate configured"):
        vessel.bor_for(OperatingState.BALLAST_SEA)


# --- VoyageSegment validation ----------------------------------------------

def test_segment_rejects_negative_duration():
    with pytest.raises(ValueError, match="duration_days must be"):
        VoyageSegment("leg", OperatingState.LADEN_SEA, duration_days=-1.0)


def test_segment_rejects_out_of_range_ets_scope():
    with pytest.raises(ValueError, match="ets_scope_fraction must be"):
        VoyageSegment("leg", OperatingState.LADEN_SEA, duration_days=1.0, ets_scope_fraction=1.5)


# --- run_voyage: multi-segment chaining and reconciliation -----------------

def _simple_voyage_vessel(bor: float = 0.01) -> VesselPerformance:
    return VesselPerformance(
        demand_mmbtu_per_day={
            OperatingState.LADEN_SEA: 15.0,
            OperatingState.DISCHARGE: 0.0,
            OperatingState.BALLAST_SEA: 8.0,
        },
        # DISCHARGE fixed at 0 (vapour-return -- see physical._default_bor_table),
        # laden/ballast sea share the same rate unless a test varies it.
        bor_fraction_per_day={
            OperatingState.LADEN_SEA: bor,
            OperatingState.DISCHARGE: 0.0,
            OperatingState.BALLAST_SEA: bor,
        },
        energy_factor_mmbtu_per_t=40.0,
    )


def test_run_voyage_reconciles_exactly():
    vessel = _simple_voyage_vessel()
    segments = (
        VoyageSegment("laden", OperatingState.LADEN_SEA, duration_days=2.0),
        VoyageSegment("discharge", OperatingState.DISCHARGE, duration_days=0.5),
        VoyageSegment("ballast", OperatingState.BALLAST_SEA, duration_days=2.0),
    )
    ledger = run_voyage(segments, vessel, loaded_mmbtu=1000.0, heel_target_mmbtu=0.0)

    assert ledger.reconciliation_error_mmbtu == pytest.approx(0.0, abs=1e-9)
    assert ledger.loaded_mmbtu == pytest.approx(
        ledger.delivered_mmbtu + ledger.lng_burned_mmbtu + ledger.vented_mmbtu
        + ledger.other_loss_mmbtu + ledger.heel_at_discharge_mmbtu
    )


def test_ballast_consumption_does_not_reduce_delivered_cargo():
    """The defining invariant this engine exists to fix: today's model has
    no concept of ballast BOG touching delivered cargo at all. Confirms
    delivered is fixed at discharge and untouched by whatever the ballast
    leg needs, even a large ballast demand that draws heel to zero."""
    vessel = _simple_voyage_vessel()
    light_ballast = (
        VoyageSegment("laden", OperatingState.LADEN_SEA, duration_days=2.0),
        VoyageSegment("discharge", OperatingState.DISCHARGE, duration_days=0.5),
        VoyageSegment("ballast", OperatingState.BALLAST_SEA, duration_days=1.0),
    )
    heavy_ballast = (
        VoyageSegment("laden", OperatingState.LADEN_SEA, duration_days=2.0),
        VoyageSegment("discharge", OperatingState.DISCHARGE, duration_days=0.5),
        VoyageSegment("ballast", OperatingState.BALLAST_SEA, duration_days=50.0),
    )
    heel = 5.0
    ledger_light = run_voyage(light_ballast, vessel, loaded_mmbtu=1000.0, heel_target_mmbtu=heel)
    ledger_heavy = run_voyage(heavy_ballast, vessel, loaded_mmbtu=1000.0, heel_target_mmbtu=heel)

    assert ledger_light.delivered_mmbtu == pytest.approx(ledger_heavy.delivered_mmbtu)
    assert ledger_light.heel_at_discharge_mmbtu == pytest.approx(ledger_heavy.heel_at_discharge_mmbtu)
    # but terminal heel (post-ballast) does differ -- the heavy ballast leg
    # draws it down further, exactly the thing that must not leak backward.
    assert ledger_heavy.terminal_heel_mmbtu <= ledger_light.terminal_heel_mmbtu


def test_higher_bor_cannot_increase_delivered_cargo():
    segments_for = lambda: (
        VoyageSegment("laden", OperatingState.LADEN_SEA, duration_days=2.0),
        VoyageSegment("discharge", OperatingState.DISCHARGE, duration_days=0.5),
        VoyageSegment("ballast", OperatingState.BALLAST_SEA, duration_days=2.0),
    )
    low_bor = _simple_voyage_vessel(bor=0.001)
    high_bor = _simple_voyage_vessel(bor=0.05)
    delivered_low = run_voyage(segments_for(), low_bor, loaded_mmbtu=1000.0).delivered_mmbtu
    delivered_high = run_voyage(segments_for(), high_bor, loaded_mmbtu=1000.0).delivered_mmbtu

    assert delivered_high <= delivered_low


def test_run_voyage_requires_a_discharge_segment():
    vessel = _simple_voyage_vessel()
    segments = (VoyageSegment("laden", OperatingState.LADEN_SEA, duration_days=2.0),)
    with pytest.raises(ValueError, match="no DISCHARGE segment"):
        run_voyage(segments, vessel, loaded_mmbtu=1000.0)


def test_queue_demand_below_sea_demand_at_default_rates():
    """docs/PHASE2_PLAN.md Section 4.2's default demand table: queue states
    must use less energy than the corresponding sea-passage state at equal
    duration (Improvement 4's acceptance criterion)."""
    vessel = VesselPerformance()
    assert vessel.demand_for(OperatingState.LADEN_QUEUE) < vessel.demand_for(OperatingState.LADEN_SEA)
    assert vessel.demand_for(OperatingState.BALLAST_QUEUE) < vessel.demand_for(OperatingState.BALLAST_SEA)


def test_loading_and_discharge_default_to_zero_bor():
    """Vapour-return assumption (physical._default_bor_table): while
    alongside, boil-off is handled shoreside, not ship-retained."""
    vessel = VesselPerformance()
    assert vessel.bor_for(OperatingState.LOADING) == 0.0
    assert vessel.bor_for(OperatingState.DISCHARGE) == 0.0
    assert vessel.bor_for(OperatingState.LADEN_SEA) > 0.0


# --- Preview of the legacy-equivalence proof (full version needs route builders) --

def test_legacy_laden_leg_shortfall_matches_residual_laden_vlsfo():
    """Hand-built single-segment voyage reproducing model.py's laden leg
    under its own default Params (europe_laden_days=10.4701, cargo_size=
    3,500,000, boil_off_rate=0.0010, laden_fuel_requirement=150 t/d) using
    the energy_factor_mmbtu_per_t=40.5093 derived in docs/PHASE2_PLAN.md
    Section 2. Expected: residual_laden_vlsfo=63.6 t/d exactly, reliquefied
    and vented both zero (today's model has neither concept)."""
    eu_laden_days = 4900.0 / (19.5 * 24.0)  # model.EU_LEG_DAYS, duplicated to avoid importing model.py here
    vessel = VesselPerformance(
        demand_mmbtu_per_day={OperatingState.LADEN_SEA: 150.0 * 40.5093, OperatingState.DISCHARGE: 0.0},
        bor_fraction_per_day={OperatingState.LADEN_SEA: 0.0010, OperatingState.DISCHARGE: 0.0},
        energy_factor_mmbtu_per_t=40.5093,
        reliq_capacity_mmbtu_per_day=0.0,
    )
    segments = (
        VoyageSegment("laden", OperatingState.LADEN_SEA, duration_days=eu_laden_days),
        VoyageSegment("discharge", OperatingState.DISCHARGE, duration_days=0.0),
    )
    ledger = run_voyage(segments, vessel, loaded_mmbtu=3_500_000.0, heel_target_mmbtu=0.0)

    laden_result = ledger.segments[0]
    assert laden_result.reliquefied_mmbtu == pytest.approx(0.0)
    assert laden_result.vented_mmbtu == pytest.approx(0.0)
    implied_vlsfo_t_per_day = ledger.total_liquid_fuel_tonnes / eu_laden_days
    assert implied_vlsfo_t_per_day == pytest.approx(63.6, abs=0.01)


# --- Route builders (Section 8 step 2) --------------------------------------

def test_europe_route_segments_total_duration_matches_europe_rt():
    import model
    params = model.Params()
    segments = physical.europe_route_segments(params)
    total = sum(s.duration_days for s in segments)
    assert total == pytest.approx(params.europe_laden_days + params.europe_port_days + params.europe_ballast_days)
    assert [s.state for s in segments] == [
        OperatingState.LOADING, OperatingState.LADEN_SEA, OperatingState.DISCHARGE, OperatingState.BALLAST_SEA,
    ]


def test_asia_route_segments_total_duration_matches_asia_rt_base_and_congested():
    import model
    base_params = model.Params(asia_rt_days=model.ASIA_RT_BASE)
    cong_params = model.Params(asia_rt_days=model.ASIA_RT_CONG)
    base_total = sum(s.duration_days for s in physical.asia_route_segments(base_params))
    cong_total = sum(s.duration_days for s in physical.asia_route_segments(cong_params))
    assert base_total == pytest.approx(model.ASIA_RT_BASE)
    assert cong_total == pytest.approx(model.ASIA_RT_CONG)


def test_asia_route_segments_are_all_outside_ets_scope():
    import model
    params = model.Params()
    assert all(s.ets_scope_fraction == 0.0 for s in physical.asia_route_segments(params))


def test_europe_route_segments_discharge_is_full_ets_scope():
    import model
    params = model.Params()
    segments = {s.state: s for s in physical.europe_route_segments(params)}
    assert segments[OperatingState.DISCHARGE].ets_scope_fraction == 1.0
    assert segments[OperatingState.LADEN_SEA].ets_scope_fraction == 0.5


# --- vessel_performance_from_params: the params -> engine data flow --------
#
# Caught while building emissions.py tests: a bare VesselPerformance() has
# its own hardcoded defaults that only *coincidentally* equal
# model.Params()'s defaults (both 0.0010/day, both 150/130/25 t/d) --
# nothing actually reads params.boil_off_rate etc. Confirmed live: two
# europe_route_segments() runs at boil_off_rate=0.0001 vs 0.0015 produced
# byte-identical total_liquid_fuel_tonnes when vesseled with a bare
# VesselPerformance(). vessel_performance_from_params() fixes the actual
# data flow; these tests exist so that connection can never silently break
# again.

def test_vessel_performance_from_params_reflects_boil_off_rate():
    import model
    low = physical.vessel_performance_from_params(model.Params(boil_off_rate=0.0001))
    high = physical.vessel_performance_from_params(model.Params(boil_off_rate=0.0015))
    assert low.bor_for(OperatingState.LADEN_SEA) == pytest.approx(0.0001)
    assert high.bor_for(OperatingState.LADEN_SEA) == pytest.approx(0.0015)

    seg_low = physical.europe_route_segments(model.Params(boil_off_rate=0.0001))
    seg_high = physical.europe_route_segments(model.Params(boil_off_rate=0.0015))
    ledger_low = run_voyage(seg_low, low, loaded_mmbtu=3_500_000.0)
    ledger_high = run_voyage(seg_high, high, loaded_mmbtu=3_500_000.0)
    assert ledger_low.total_liquid_fuel_tonnes != pytest.approx(ledger_high.total_liquid_fuel_tonnes)


def test_vessel_performance_from_params_reflects_fuel_requirement_fields():
    import model
    default = physical.vessel_performance_from_params(model.Params())
    edited = physical.vessel_performance_from_params(
        model.Params(laden_fuel_requirement=200.0, ballast_fuel=160.0, port_fuel_rate=40.0)
    )
    assert edited.demand_for(OperatingState.LADEN_SEA) > default.demand_for(OperatingState.LADEN_SEA)
    assert edited.demand_for(OperatingState.BALLAST_SEA) > default.demand_for(OperatingState.BALLAST_SEA)
    assert edited.demand_for(OperatingState.DISCHARGE) > default.demand_for(OperatingState.DISCHARGE)


def test_vessel_performance_from_params_discharge_and_loading_still_zero_bor():
    import model
    vessel = physical.vessel_performance_from_params(model.Params(boil_off_rate=0.05))
    assert vessel.bor_for(OperatingState.LOADING) == 0.0
    assert vessel.bor_for(OperatingState.DISCHARGE) == 0.0
    assert vessel.bor_for(OperatingState.LADEN_SEA) == pytest.approx(0.05)
