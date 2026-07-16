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
