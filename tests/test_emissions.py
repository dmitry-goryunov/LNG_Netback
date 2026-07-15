"""Tests for emissions.py (docs/PHASE2_PLAN.md Section 8 step 4,
Improvement 14 section E). No workbook dependency."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import emissions
import model
import physical


def _europe_ledger(params=None):
    # vessel_performance_from_params(), not bare VesselPerformance() --
    # the latter ignores params entirely (see
    # physical.vessel_performance_from_params's docstring), which would
    # make every test below that varies boil_off_rate silently test
    # nothing.
    params = params or model.Params()
    vessel = physical.vessel_performance_from_params(params)
    segments = physical.europe_route_segments(params)
    return physical.run_voyage(segments, vessel, loaded_mmbtu=params.cargo_size, heel_target_mmbtu=0.0)


def _asia_ledger(asia_rt_days=None):
    params = model.Params(asia_rt_days=asia_rt_days or model.ASIA_RT_BASE)
    vessel = physical.vessel_performance_from_params(params)
    segments = physical.asia_route_segments(params)
    return physical.run_voyage(segments, vessel, loaded_mmbtu=params.cargo_size, heel_target_mmbtu=0.0)


def test_zero_fuel_gives_zero_emissions():
    seg = physical.VoyageSegment("idle", physical.OperatingState.LOADING, duration_days=0.0)
    vessel = physical.VesselPerformance(demand_mmbtu_per_day={physical.OperatingState.LOADING: 0.0,
                                                               physical.OperatingState.DISCHARGE: 0.0},
                                         bor_fraction_per_day={physical.OperatingState.LOADING: 0.0,
                                                                physical.OperatingState.DISCHARGE: 0.0})
    discharge = physical.VoyageSegment("discharge", physical.OperatingState.DISCHARGE, duration_days=0.0)
    ledger = physical.run_voyage((seg, discharge), vessel, loaded_mmbtu=1000.0)
    voyage = emissions.voyage_emissions(ledger)

    assert voyage.total_co2_tonnes == pytest.approx(0.0)
    assert voyage.total_ch4_tonnes == pytest.approx(0.0)
    assert voyage.total_co2e_tonnes == pytest.approx(0.0)
    assert voyage.ets_covered_co2e_tonnes == pytest.approx(0.0)


def test_higher_fuel_demand_increases_co2e():
    """Improvement 14 section E item 1: increasing any fuel demand rate
    strictly increases summed CO2e, because total energy burned only ever
    goes up.

    Deliberately varies laden_fuel_requirement (total demand), not BOR.
    BOR only changes the *source mix* (LNG vs liquid fuel) for the same
    fixed demand, not the total -- an earlier version of this test varied
    BOR instead and failed, correctly: in this model LNG has a *lower*
    CO2 factor per unit energy than VLSFO (2.75/48.6 ~= 0.0566 t/MMBtu vs
    3.15/40.5093 ~= 0.0778 t/MMBtu), so more BOG-sourced LNG for the same
    demand means *less* CO2e, not more. That's test_higher_liquid_fuel_
    burn_increases_co2e below, the correct framing of that comparison."""
    low = _europe_ledger(model.Params(laden_fuel_requirement=100.0))
    high = _europe_ledger(model.Params(laden_fuel_requirement=200.0))
    assert emissions.voyage_emissions(high).total_co2e_tonnes > emissions.voyage_emissions(low).total_co2e_tonnes


def test_higher_liquid_fuel_burn_increases_co2e():
    """Same breakeven-avoidance reasoning as test_higher_lng_burn_
    increases_co2e above, just read the other direction: less BOG offset
    means more of the same total demand must be met by liquid fuel."""
    less_liquid_fuel = _europe_ledger(model.Params(boil_off_rate=0.0015))  # more BOG -> less liquid-fuel shortfall
    more_liquid_fuel = _europe_ledger(model.Params(boil_off_rate=0.0003))  # less BOG -> more liquid-fuel shortfall
    assert (
        emissions.voyage_emissions(more_liquid_fuel).total_co2_tonnes
        > emissions.voyage_emissions(less_liquid_fuel).total_co2_tonnes
    )


def test_vented_methane_is_counted_as_raw_ch4_not_ignored():
    """Was test_vented_methane_is_not_yet_counted until the gap it
    documented was closed (docs/PHASE2_PLAN.md Section 8 step 6 found the
    congested Asia route already triggers this for real, not just in a
    synthetic high-BOR case). A BOR high enough to push natural BOG past
    demand (here, with reliq_capacity=0, so 100% of the surplus is vented)
    now produces a materially higher total_co2e_tonnes than an equivalent
    no-venting case, via total_ch4_vented_tonnes -- not silently absorbed
    into a combustion-only figure."""
    params_no_vent = model.Params(boil_off_rate=0.0015)  # below breakeven, zero venting
    params_with_vent = model.Params(boil_off_rate=0.01)  # well above breakeven, large venting
    ledger_no_vent = _europe_ledger(params_no_vent)
    ledger_with_vent = _europe_ledger(params_with_vent)
    assert ledger_no_vent.vented_mmbtu == pytest.approx(0.0)
    assert ledger_with_vent.vented_mmbtu > 100_000  # confirms this case genuinely exercises venting

    voyage_no_vent = emissions.voyage_emissions(ledger_no_vent)
    voyage_with_vent = emissions.voyage_emissions(ledger_with_vent)
    assert voyage_no_vent.total_ch4_vented_tonnes == pytest.approx(0.0)
    assert voyage_with_vent.total_ch4_vented_tonnes > 0.0
    assert voyage_with_vent.total_co2e_tonnes > voyage_no_vent.total_co2e_tonnes
    assert voyage_with_vent.total_co2e_tonnes == pytest.approx(
        voyage_with_vent.total_co2_tonnes
        + (voyage_with_vent.total_ch4_slip_tonnes + voyage_with_vent.total_ch4_vented_tonnes) * emissions.GWP_CH4_100YR
        + voyage_with_vent.total_n2o_tonnes * emissions.GWP_N2O_100YR
    )


def test_methane_slip_contributes_to_co2e_only_when_nonzero():
    ledger = _europe_ledger()
    without_slip = emissions.voyage_emissions(ledger, methane_slip_fraction=0.0)
    with_slip = emissions.voyage_emissions(ledger, methane_slip_fraction=0.003)

    assert without_slip.total_ch4_tonnes == pytest.approx(0.0)
    assert without_slip.total_co2e_tonnes == pytest.approx(without_slip.total_co2_tonnes)
    assert with_slip.total_ch4_tonnes > 0.0
    assert with_slip.total_co2e_tonnes > with_slip.total_co2_tonnes
    assert with_slip.total_co2e_tonnes == pytest.approx(
        with_slip.total_co2_tonnes + with_slip.total_ch4_tonnes * emissions.GWP_CH4_100YR
    )


def test_n2o_contributes_to_co2e_only_when_nonzero():
    ledger = _europe_ledger()
    without = emissions.voyage_emissions(ledger, n2o_kg_per_t_lng=0.0)
    with_n2o = emissions.voyage_emissions(ledger, n2o_kg_per_t_lng=0.1)

    assert without.total_n2o_tonnes == pytest.approx(0.0)
    assert with_n2o.total_n2o_tonnes > 0.0
    assert with_n2o.total_co2e_tonnes > without.total_co2e_tonnes


def test_ets_covered_equals_total_times_scope_per_segment_and_aggregate():
    voyage = emissions.voyage_emissions(_europe_ledger())
    for seg in voyage.segments:
        assert seg.ets_covered_co2e_tonnes == pytest.approx(seg.co2e_tonnes * seg.ets_scope_fraction)
    assert voyage.ets_covered_co2e_tonnes == pytest.approx(sum(s.ets_covered_co2e_tonnes for s in voyage.segments))


def test_europe_route_has_positive_ets_cost():
    voyage = emissions.voyage_emissions(_europe_ledger())
    cost = emissions.ets_cost_usd(voyage, eua_price_eur_per_t=70.0, eur_usd_fx=1.10)
    assert cost > 0.0


def test_asia_route_has_zero_ets_cost_under_current_scope():
    """Matches the legacy model's implicit assumption (model.py's ets line
    is only ever added to eu_margin, never asia_margin) -- see
    docs/PHASE2_PLAN.md Section 5.2."""
    voyage = emissions.voyage_emissions(_asia_ledger())
    assert voyage.ets_covered_co2e_tonnes == pytest.approx(0.0)
    cost = emissions.ets_cost_usd(voyage, eua_price_eur_per_t=70.0, eur_usd_fx=1.10)
    assert cost == pytest.approx(0.0)


def test_eua_price_change_affects_europe_cost_only():
    europe_voyage = emissions.voyage_emissions(_europe_ledger())
    asia_voyage = emissions.voyage_emissions(_asia_ledger())

    cost_low = emissions.ets_cost_usd(europe_voyage, eua_price_eur_per_t=50.0, eur_usd_fx=1.10)
    cost_high = emissions.ets_cost_usd(europe_voyage, eua_price_eur_per_t=90.0, eur_usd_fx=1.10)
    assert cost_high > cost_low
    assert cost_high / cost_low == pytest.approx(90.0 / 50.0)

    asia_low = emissions.ets_cost_usd(asia_voyage, eua_price_eur_per_t=50.0, eur_usd_fx=1.10)
    asia_high = emissions.ets_cost_usd(asia_voyage, eua_price_eur_per_t=90.0, eur_usd_fx=1.10)
    assert asia_low == pytest.approx(0.0)
    assert asia_high == pytest.approx(0.0)


def test_ets_cost_rejects_invalid_contractual_share():
    voyage = emissions.voyage_emissions(_europe_ledger())
    with pytest.raises(ValueError, match="contractual_share must be"):
        emissions.ets_cost_usd(voyage, eua_price_eur_per_t=70.0, eur_usd_fx=1.10, contractual_share=1.5)


def test_queue_state_combustion_co2_below_sea_state_at_equal_duration():
    """Half of Improvement 4's acceptance criterion: a LADEN_QUEUE segment's
    own fuel/combustion CO2 (excluding vented gas -- see the next two tests
    for why that exclusion matters) is lower than an equal-duration
    LADEN_SEA segment's, matching the lower queue demand rate."""
    vessel = physical.VesselPerformance()
    sea = physical.VoyageSegment("sea", physical.OperatingState.LADEN_SEA, duration_days=5.0)
    queue = physical.VoyageSegment("queue", physical.OperatingState.LADEN_QUEUE, duration_days=5.0)
    discharge = physical.VoyageSegment("discharge", physical.OperatingState.DISCHARGE, duration_days=0.0)

    sea_ledger = physical.run_voyage((sea, discharge), vessel, loaded_mmbtu=3_500_000.0)
    queue_ledger = physical.run_voyage((queue, discharge), vessel, loaded_mmbtu=3_500_000.0)

    assert (
        emissions.voyage_emissions(queue_ledger).total_co2_tonnes
        < emissions.voyage_emissions(sea_ledger).total_co2_tonnes
    )


def test_queue_state_total_co2e_can_exceed_sea_state_without_reliquefaction():
    """The other half of the story, and the reason step 6's finding
    matters: at zero reliquefaction capacity (the engine's default),
    natural BOG doesn't slow down just because the vessel is queuing
    instead of steaming, but the queue's own (lower) demand consumes less
    of it -- so a queue segment can vent MORE than an equal-duration sea
    segment burns, and raw vented methane (GWP 25) outweighs the
    combustion CO2 it saved. Total CO2e for the queue case comes out
    *higher* than the sea case here, the reverse of the combustion-only
    comparison above. Do not read Improvement 4's "queue doesn't cost as
    much fuel as full-speed steaming" as "queue is always the lower-
    emissions choice" -- it depends entirely on reliquefaction capacity,
    per the next test."""
    vessel = physical.VesselPerformance()  # reliq_capacity_mmbtu_per_day=0.0 default
    sea = physical.VoyageSegment("sea", physical.OperatingState.LADEN_SEA, duration_days=5.0)
    queue = physical.VoyageSegment("queue", physical.OperatingState.LADEN_QUEUE, duration_days=5.0)
    discharge = physical.VoyageSegment("discharge", physical.OperatingState.DISCHARGE, duration_days=0.0)

    sea_ledger = physical.run_voyage((sea, discharge), vessel, loaded_mmbtu=3_500_000.0)
    queue_ledger = physical.run_voyage((queue, discharge), vessel, loaded_mmbtu=3_500_000.0)
    queue_voyage = emissions.voyage_emissions(queue_ledger)

    assert queue_ledger.vented_mmbtu > 0.0
    assert queue_voyage.total_co2e_tonnes > emissions.voyage_emissions(sea_ledger).total_co2e_tonnes


def test_queue_total_co2e_drops_below_sea_with_adequate_reliquefaction():
    """Same two segments as the previous test, but with enough
    reliquefaction capacity to fully absorb the queue's BOG surplus:
    venting goes to zero and the combustion-only ordering (queue lower
    than sea) is restored. This is what makes reliq_capacity_mmbtu_per_day
    a genuinely consequential, not cosmetic, open decision
    (docs/PHASE2_PLAN.md Section 10 item 2)."""
    vessel = physical.VesselPerformance(reliq_capacity_mmbtu_per_day=5000.0)
    sea = physical.VoyageSegment("sea", physical.OperatingState.LADEN_SEA, duration_days=5.0)
    queue = physical.VoyageSegment("queue", physical.OperatingState.LADEN_QUEUE, duration_days=5.0)
    discharge = physical.VoyageSegment("discharge", physical.OperatingState.DISCHARGE, duration_days=0.0)

    sea_ledger = physical.run_voyage((sea, discharge), vessel, loaded_mmbtu=3_500_000.0)
    queue_ledger = physical.run_voyage((queue, discharge), vessel, loaded_mmbtu=3_500_000.0)

    assert queue_ledger.vented_mmbtu == pytest.approx(0.0)
    assert (
        emissions.voyage_emissions(queue_ledger).total_co2e_tonnes
        < emissions.voyage_emissions(sea_ledger).total_co2e_tonnes
    )


def test_fueleu_and_ets_payer_are_explicit_placeholders():
    voyage = emissions.voyage_emissions(_europe_ledger())
    assert voyage.fueleu_exposure == "NOT_PRICED"
    assert voyage.ets_payer == "unresolved"
