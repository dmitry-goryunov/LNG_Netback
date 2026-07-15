"""The anchor test for Phase 2 (docs/PHASE2_PLAN.md Section 6 and Section 8
step 3): proves physical.py's segment engine, run through the actual route
builders (not a hand-built segment list), reproduces model.py's implicit
legacy constants under model.Params()' own defaults.

This is the same kind of proof risk.py's "zero-shock scenario reprices to
~0 P&L vs model.strip() base" check exists for: it demonstrates the new
engine is a strict generalisation of the old constants, not a new,
unreconciled set of assumptions. If any FUEL/BOG assertion here needs a
tolerance wider than floating-point noise, that is a stop-and-report
condition per the plan, not something to quietly loosen.

One genuine, verified divergence is documented and asserted explicitly
rather than hidden: model.py's co2_eu_ets_tonnes=4425.9 was hand-derived
using a *uniform* 50% ETS-scope factor applied to every segment including
the discharge/at-berth segment (see model.py's own comment on the
constant). The actual EU ETS Directive (2003/87/EC as amended by
2023/959) uses 50% only for the sea *voyage* leg between a non-EU and an
EU port; time genuinely at berth in an EU port is a separate 100%-scope
provision. physical.europe_route_segments() implements the textbook rule
(0.5 laden sea / 1.0 discharge / 0.5 ballast sea), which is *correct*, not
equivalent to the legacy approximation -- verified below to diverge from
the legacy constant by ~4.45%, not a rounding-sized gap. This means EU
ETS cost will go up by that much once emissions.py/route valuation wire
into this engine (Section 8 steps 4/8), which is a real, expected
consequence of fixing a genuine understatement in the current screening
model, not a defect in this engine.
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import model
import physical

# Combustion factors already used (as hand-derived constants) in model.py's
# own comment deriving co2_eu_ets_tonnes -- carried forward unchanged, only
# their application moves from one hand-computed number to a per-segment
# sum. See docs/PHASE2_PLAN.md Section 5.1 for why these two must not be
# confused with VesselPerformance.energy_factor_mmbtu_per_t (a different
# fuel, a different conversion).
CO2_T_PER_T_VLSFO = 3.15
CO2_T_PER_T_LNG = 2.75
LNG_MMBTU_PER_T = 48.6


def _co2_tonnes(ledger: physical.VoyageLedger, scope_by_segment: bool) -> float:
    total = 0.0
    for r in ledger.segments:
        seg_lng_t = r.bog_burned_mmbtu / LNG_MMBTU_PER_T if r.segment.state in physical.LADEN_STATES else 0.0
        seg_co2 = r.shortfall_liquid_fuel_tonnes * CO2_T_PER_T_VLSFO + seg_lng_t * CO2_T_PER_T_LNG
        scope = r.segment.ets_scope_fraction if scope_by_segment else 0.5
        total += seg_co2 * scope
    return total


def _fuel_rate_t_per_day(ledger: physical.VoyageLedger, segment_name: str, days: float) -> float:
    if days == 0:
        return 0.0
    r = next(s for s in ledger.segments if s.segment.name == segment_name)
    return r.shortfall_liquid_fuel_tonnes / days


class TestEuropeRouteEquivalence:
    """model.Params() defaults, europe_route_segments()."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.params = model.Params()
        self.vessel = physical.VesselPerformance()
        self.segments = physical.europe_route_segments(self.params)
        self.ledger = physical.run_voyage(
            self.segments, self.vessel, loaded_mmbtu=self.params.cargo_size, heel_target_mmbtu=0.0
        )

    def test_no_reliquefaction_or_venting(self):
        assert self.ledger.reliquefied_mmbtu == pytest.approx(0.0)
        assert self.ledger.vented_mmbtu == pytest.approx(0.0)

    def test_laden_leg_reproduces_residual_laden_vlsfo(self):
        rate = _fuel_rate_t_per_day(self.ledger, "laden_sea", self.params.europe_laden_days)
        assert rate == pytest.approx(self.params.residual_laden_vlsfo, abs=0.01)

    def test_ballast_leg_reproduces_ballast_fuel(self):
        rate = _fuel_rate_t_per_day(self.ledger, "ballast_sea", self.params.europe_ballast_days)
        assert rate == pytest.approx(self.params.ballast_fuel, abs=0.01)

    def test_discharge_reproduces_port_fuel_rate(self):
        rate = _fuel_rate_t_per_day(self.ledger, "discharge", self.params.europe_port_days)
        assert rate == pytest.approx(self.params.port_fuel_rate, abs=0.01)

    def test_total_fuel_tonnes_matches_eu_ship_fuel_component_exactly(self):
        legacy_tonnes = (
            self.params.residual_laden_vlsfo * self.params.europe_laden_days
            + self.params.ballast_fuel * self.params.europe_ballast_days
            + self.params.port_fuel_rate * self.params.europe_port_days
        )
        assert self.ledger.total_liquid_fuel_tonnes == pytest.approx(legacy_tonnes, rel=1e-6)

    def test_co2_at_legacy_uniform_scope_matches_co2_eu_ets_tonnes(self):
        """Sanity check on the combustion arithmetic itself, isolated from
        the ETS-scope question below: reproducing the legacy constant's own
        (uniform 0.5) scope convention should match almost exactly."""
        co2 = _co2_tonnes(self.ledger, scope_by_segment=False)
        assert co2 == pytest.approx(self.params.co2_eu_ets_tonnes, rel=0.001)

    def test_co2_at_correct_differentiated_scope_is_materially_higher_than_legacy(self):
        """Documents, rather than hides, the ~4.45% divergence explained in
        this module's docstring. If this ratio drifts outside the pinned
        band, something about the route/scope/combustion assumptions
        changed and needs re-verifying -- not silently re-pinned."""
        co2_differentiated = _co2_tonnes(self.ledger, scope_by_segment=True)
        ratio = co2_differentiated / self.params.co2_eu_ets_tonnes
        assert 1.04 < ratio < 1.05, (
            f"expected the textbook-scoped CO2e ({co2_differentiated:.1f} t) to exceed the legacy "
            f"uniform-0.5-scope constant ({self.params.co2_eu_ets_tonnes} t) by ~4.45%; got ratio {ratio:.4f}"
        )


class TestAsiaRouteEquivalence:
    """model.Params(asia_rt_days=...) for both the base and congested case.
    Asia legs are entirely outside EU ETS scope in both the legacy model
    and this engine's route builder, so there is no scope-divergence
    finding here -- only the fuel/BOG equivalence."""

    def _ledger_for(self, asia_rt_days: float) -> tuple:
        params = model.Params(asia_rt_days=asia_rt_days)
        vessel = physical.VesselPerformance()
        segments = physical.asia_route_segments(params)
        ledger = physical.run_voyage(segments, vessel, loaded_mmbtu=params.cargo_size, heel_target_mmbtu=0.0)
        return params, ledger

    @pytest.mark.parametrize("asia_rt_days", [model.ASIA_RT_BASE, model.ASIA_RT_CONG])
    def test_no_reliquefaction_or_venting(self, asia_rt_days):
        _, ledger = self._ledger_for(asia_rt_days)
        assert ledger.reliquefied_mmbtu == pytest.approx(0.0)
        assert ledger.vented_mmbtu == pytest.approx(0.0)

    @pytest.mark.parametrize("asia_rt_days", [model.ASIA_RT_BASE, model.ASIA_RT_CONG])
    def test_laden_leg_reproduces_residual_laden_vlsfo(self, asia_rt_days):
        params, ledger = self._ledger_for(asia_rt_days)
        rate = _fuel_rate_t_per_day(ledger, "laden_sea", params.asia_laden_days)
        assert rate == pytest.approx(params.residual_laden_vlsfo, abs=0.01)

    @pytest.mark.parametrize("asia_rt_days", [model.ASIA_RT_BASE, model.ASIA_RT_CONG])
    def test_ballast_leg_reproduces_ballast_fuel(self, asia_rt_days):
        params, ledger = self._ledger_for(asia_rt_days)
        asia_ballast = asia_rt_days - params.asia_laden_days - params.asia_port_days
        rate = _fuel_rate_t_per_day(ledger, "ballast_sea", asia_ballast)
        assert rate == pytest.approx(params.ballast_fuel, abs=0.01)

    @pytest.mark.parametrize("asia_rt_days", [model.ASIA_RT_BASE, model.ASIA_RT_CONG])
    def test_discharge_reproduces_port_fuel_rate(self, asia_rt_days):
        params, ledger = self._ledger_for(asia_rt_days)
        rate = _fuel_rate_t_per_day(ledger, "discharge", params.asia_port_days)
        assert rate == pytest.approx(params.port_fuel_rate, abs=0.01)

    @pytest.mark.parametrize("asia_rt_days", [model.ASIA_RT_BASE, model.ASIA_RT_CONG])
    def test_total_fuel_tonnes_matches_as_ship_fuel_component_exactly(self, asia_rt_days):
        params, ledger = self._ledger_for(asia_rt_days)
        asia_ballast = asia_rt_days - params.asia_laden_days - params.asia_port_days
        legacy_tonnes = (
            params.residual_laden_vlsfo * params.asia_laden_days
            + params.ballast_fuel * asia_ballast
            + params.port_fuel_rate * params.asia_port_days
        )
        assert ledger.total_liquid_fuel_tonnes == pytest.approx(legacy_tonnes, rel=1e-6)
