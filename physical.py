"""
physical.py -- segment-level physical voyage engine (Phase 2, Improvement 3
of docs/IMPROVEMENT_PLAN.md; see docs/PHASE2_PLAN.md for the full design).

Pure functions + dataclasses, no Streamlit import, same style as model.py /
decision.py. Not wired into model.strip(), decision.py or app.py yet --
existing pages and the frozen legacy 64/64 suite are completely unaffected
by this file's existence (per docs/PHASE2_PLAN.md Section 3, the physical
engine is built and equivalence-tested standalone before anything wires
into it).

This increment (docs/PHASE2_PLAN.md Section 8 step 1) is the segment
balance primitives and run_voyage() only. Route builders
(europe_route_segments/asia_route_segments) and the legacy-equivalence
test against model.py's implicit constants are the next increment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

import model

ABS_TOL_MMBTU = 1e-6


class OperatingState(str, Enum):
    LOADING = "loading"
    LADEN_SEA = "laden_sea"
    LADEN_QUEUE = "laden_queue"
    CANAL_TRANSIT = "canal_transit"
    DISCHARGE = "discharge"
    BALLAST_SEA = "ballast_sea"
    BALLAST_QUEUE = "ballast_queue"
    PORT = "port"


# Laden-state segments draw down the loaded cargo inventory; ballast-state
# segments (after discharge) draw down heel only and must never be able to
# reduce already-delivered cargo -- see run_voyage()'s docstring.
LADEN_STATES = frozenset({
    OperatingState.LOADING, OperatingState.LADEN_SEA, OperatingState.LADEN_QUEUE,
    OperatingState.CANAL_TRANSIT, OperatingState.DISCHARGE,
})
BALLAST_STATES = frozenset({
    OperatingState.BALLAST_SEA, OperatingState.BALLAST_QUEUE, OperatingState.PORT,
})


class ShortfallSource(str, Enum):
    LIQUID_FUEL = "liquid_fuel"
    FORCED_VAPORISATION = "forced_vaporisation"


def _default_demand_table() -> dict:
    # VLSFO-equivalent t/d figures (matching model.Params' legacy constants)
    # converted via the back-solved 40.5093 MMBtu/t factor -- see
    # docs/PHASE2_PLAN.md Section 2 for the derivation and Section 4.2 for
    # why the non-laden-sea/non-ballast-sea rates below are placeholders.
    f = 40.5093
    return {
        OperatingState.LOADING: 25.0 * f,
        OperatingState.LADEN_SEA: 150.0 * f,
        OperatingState.LADEN_QUEUE: 40.0 * f,
        OperatingState.CANAL_TRANSIT: 150.0 * f,
        OperatingState.DISCHARGE: 25.0 * f,
        OperatingState.BALLAST_SEA: 130.0 * f,
        OperatingState.BALLAST_QUEUE: 35.0 * f,
        OperatingState.PORT: 25.0 * f,
    }


def _default_bor_table() -> dict:
    # Boil-off rate is per-state, not one vessel-wide constant: while
    # alongside for loading or discharge, an LNGC is connected to the
    # terminal's vapour-return line, so boil-off is handled shoreside and
    # does not accrue as ship-retained BOG -- a real, independently
    # justified operational practice, not just a convenience for matching
    # the legacy model. Sea/queue states use the legacy boil_off_rate
    # (0.0010/day) uniformly, matching model.Params.boil_off_rate.
    rate = 0.0010
    return {
        OperatingState.LOADING: 0.0,
        OperatingState.LADEN_SEA: rate,
        OperatingState.LADEN_QUEUE: rate,
        OperatingState.CANAL_TRANSIT: rate,
        OperatingState.DISCHARGE: 0.0,
        OperatingState.BALLAST_SEA: rate,
        OperatingState.BALLAST_QUEUE: rate,
        OperatingState.PORT: rate,
    }


@dataclass(frozen=True)
class VesselPerformance:
    """User-editable, one instance per vessel class.

    energy_factor_mmbtu_per_t is the VLSFO-equivalent conversion already
    implicit in model.Params' legacy fuel constants (3500 / 86.4 =
    40.5093...; docs/PHASE2_PLAN.md Section 2). demand_mmbtu_per_day's
    LADEN_SEA/BALLAST_SEA entries reproduce today's laden_fuel_requirement
    (150 t/d) and ballast_fuel (130 t/d); every other entry is a
    placeholder not sourced from any workbook data (there is no vessel
    performance sheet in LNG history.xlsx) and needs sign-off before it
    drives a real valuation -- see docs/PHASE2_PLAN.md Section 10 item 2.
    """

    energy_factor_mmbtu_per_t: float = 40.5093
    demand_mmbtu_per_day: Mapping[OperatingState, float] = field(default_factory=_default_demand_table)
    bor_fraction_per_day: Mapping[OperatingState, float] = field(default_factory=_default_bor_table)
    reliq_capacity_mmbtu_per_day: float = 0.0
    shortfall_source: ShortfallSource = ShortfallSource.LIQUID_FUEL

    def demand_for(self, state: OperatingState) -> float:
        try:
            return self.demand_mmbtu_per_day[state]
        except KeyError as exc:
            raise ValueError(f"no demand rate configured for operating state {state!r}") from exc

    def bor_for(self, state: OperatingState) -> float:
        try:
            return self.bor_fraction_per_day[state]
        except KeyError as exc:
            raise ValueError(f"no boil-off rate configured for operating state {state!r}") from exc


@dataclass(frozen=True)
class VoyageSegment:
    name: str
    state: OperatingState
    duration_days: float
    ets_scope_fraction: float = 0.0

    def __post_init__(self):
        if self.duration_days < 0:
            raise ValueError(f"segment {self.name!r}: duration_days must be >= 0, got {self.duration_days}")
        if not 0.0 <= self.ets_scope_fraction <= 1.0:
            raise ValueError(
                f"segment {self.name!r}: ets_scope_fraction must be within [0, 1], got {self.ets_scope_fraction}"
            )
        if self.state not in LADEN_STATES and self.state not in BALLAST_STATES:
            raise ValueError(f"segment {self.name!r}: unclassified operating state {self.state!r}")


@dataclass(frozen=True)
class SegmentResult:
    segment: VoyageSegment
    opening_inventory_mmbtu: float
    natural_bog_mmbtu: float
    demand_mmbtu: float
    bog_burned_mmbtu: float
    surplus_mmbtu: float
    reliquefied_mmbtu: float
    vented_mmbtu: float
    shortfall_mmbtu: float
    forced_lng_mmbtu: float
    shortfall_liquid_fuel_tonnes: float
    closing_inventory_mmbtu: float


@dataclass(frozen=True)
class VoyageLedger:
    loaded_mmbtu: float
    heel_at_discharge_mmbtu: float
    terminal_heel_mmbtu: float
    delivered_mmbtu: float
    other_loss_mmbtu: float
    lng_burned_mmbtu: float  # natural BOG burned + forced LNG, laden segments only
    vented_mmbtu: float  # laden segments only -- see reconciliation_error_mmbtu
    reliquefied_mmbtu: float  # all segments; informational only, stays in inventory by construction
    total_liquid_fuel_tonnes: float
    total_days: float
    segments: tuple

    @property
    def reconciliation_error_mmbtu(self) -> float:
        """Should be ~0 by construction (see run_voyage()'s docstring for
        the derivation) -- this is a runtime check, not a tautology, since
        it is computed independently from the segment chain's actual
        closing inventories rather than assumed."""
        return self.loaded_mmbtu - (
            self.delivered_mmbtu + self.lng_burned_mmbtu + self.vented_mmbtu
            + self.other_loss_mmbtu + self.heel_at_discharge_mmbtu
        )


def simulate_segment(segment: VoyageSegment, vessel: VesselPerformance, opening_inventory_mmbtu: float) -> SegmentResult:
    """One segment's mass balance (docs/PHASE2_PLAN.md Section 4.4):

        BOG_natural = inventory_in * BOR[state] * days
        Demand      = DemandPerDay[state] * days
        BOG_burn    = min(BOG_natural, Demand)
        Surplus     = max(BOG_natural - Demand, 0)
        Reliquefied = min(Surplus, ReliqCapacity * days)
        Vented      = Surplus - Reliquefied
        Shortfall   = max(Demand - BOG_natural, 0)

    Shortfall is met by liquid fuel (tonnes = Shortfall / energy_factor) or
    forced vaporisation of cargo, per vessel.shortfall_source.
    """
    if opening_inventory_mmbtu < -ABS_TOL_MMBTU:
        raise ValueError(f"segment {segment.name!r}: negative opening inventory {opening_inventory_mmbtu}")
    opening_inventory_mmbtu = max(opening_inventory_mmbtu, 0.0)

    natural_bog = opening_inventory_mmbtu * vessel.bor_for(segment.state) * segment.duration_days
    demand = vessel.demand_for(segment.state) * segment.duration_days
    bog_burned = min(natural_bog, demand)
    surplus = max(natural_bog - demand, 0.0)
    reliq_capacity = vessel.reliq_capacity_mmbtu_per_day * segment.duration_days
    reliquefied = min(surplus, reliq_capacity)
    vented = surplus - reliquefied
    shortfall = max(demand - natural_bog, 0.0)

    if vessel.shortfall_source == ShortfallSource.FORCED_VAPORISATION:
        forced_lng = shortfall
        liquid_fuel_t = 0.0
    else:
        forced_lng = 0.0
        liquid_fuel_t = shortfall / vessel.energy_factor_mmbtu_per_t if shortfall else 0.0

    # natural_bog == bog_burned + reliquefied + vented (by construction above),
    # so this is equivalent to opening - bog_burned - vented - forced_lng;
    # reliquefied LNG returns to inventory and is never deducted twice.
    closing_inventory = opening_inventory_mmbtu - natural_bog + reliquefied - forced_lng
    if closing_inventory < -ABS_TOL_MMBTU:
        raise ValueError(
            f"segment {segment.name!r}: closing inventory would go negative "
            f"({closing_inventory:.6f} MMBtu) -- demand exceeds available cargo/heel "
            "for the configured shortfall_source"
        )
    closing_inventory = max(closing_inventory, 0.0)

    return SegmentResult(
        segment=segment, opening_inventory_mmbtu=opening_inventory_mmbtu,
        natural_bog_mmbtu=natural_bog, demand_mmbtu=demand, bog_burned_mmbtu=bog_burned,
        surplus_mmbtu=surplus, reliquefied_mmbtu=reliquefied, vented_mmbtu=vented,
        shortfall_mmbtu=shortfall, forced_lng_mmbtu=forced_lng,
        shortfall_liquid_fuel_tonnes=liquid_fuel_t, closing_inventory_mmbtu=closing_inventory,
    )


def run_voyage(
    segments,
    vessel: VesselPerformance,
    loaded_mmbtu: float,
    heel_target_mmbtu: float = 0.0,
    other_loss_mmbtu: float = 0.0,
) -> VoyageLedger:
    """Chains simulate_segment() across a full voyage.

    Laden-state segments (LADEN_STATES) draw from the loaded cargo
    inventory. At the DISCHARGE segment, heel_target_mmbtu is retained (capped
    at whatever is actually available) and the remainder, less
    other_loss_mmbtu, becomes delivered cargo. Ballast-state segments
    (BALLAST_STATES) after discharge draw only from that retained heel --
    never from delivered cargo, since delivered is fixed at the discharge
    segment and the loop only ever touches `inventory` afterward, which by
    then holds heel, not cargo.

    Reconciliation identity (derived, not asserted -- see
    VoyageLedger.reconciliation_error_mmbtu):

        loaded == delivered + lng_burned + vented + other_loss + heel_at_discharge

    holds because each laden segment's closing inventory is
    opening - bog_burned - vented - forced_lng (reliquefied nets out), so
    chained across every laden segment, the cargo available at discharge is
    exactly loaded - lng_burned - vented; delivered is then that available
    amount minus other_loss minus heel_at_discharge by definition.
    """
    if loaded_mmbtu < 0:
        raise ValueError("loaded_mmbtu must be >= 0")
    if heel_target_mmbtu < 0:
        raise ValueError("heel_target_mmbtu must be >= 0")
    if not segments:
        raise ValueError("a voyage must have at least one segment")

    results = []
    inventory = loaded_mmbtu
    delivered = None
    heel_at_discharge = None

    for seg in segments:
        result = simulate_segment(seg, vessel, inventory)
        inventory = result.closing_inventory_mmbtu
        results.append(result)
        if seg.state == OperatingState.DISCHARGE:
            available = inventory
            heel_at_discharge = min(heel_target_mmbtu, available)
            delivered = max(available - other_loss_mmbtu - heel_at_discharge, 0.0)
            inventory = heel_at_discharge  # only heel continues into any ballast segments

    if delivered is None:
        raise ValueError("voyage has no DISCHARGE segment; cannot determine delivered cargo")

    terminal_heel = inventory
    laden_results = [r for r in results if r.segment.state in LADEN_STATES]
    lng_burned = sum(r.bog_burned_mmbtu + r.forced_lng_mmbtu for r in laden_results)
    vented = sum(r.vented_mmbtu for r in laden_results)
    reliquefied = sum(r.reliquefied_mmbtu for r in results)
    liquid_fuel = sum(r.shortfall_liquid_fuel_tonnes for r in results)
    total_days = sum(r.segment.duration_days for r in results)

    return VoyageLedger(
        loaded_mmbtu=loaded_mmbtu, heel_at_discharge_mmbtu=heel_at_discharge,
        terminal_heel_mmbtu=terminal_heel, delivered_mmbtu=delivered,
        other_loss_mmbtu=other_loss_mmbtu, lng_burned_mmbtu=lng_burned, vented_mmbtu=vented,
        reliquefied_mmbtu=reliquefied, total_liquid_fuel_tonnes=liquid_fuel,
        total_days=total_days, segments=tuple(results),
    )


# ---------------------------------------------------------------------------
# Route builders (docs/PHASE2_PLAN.md Section 8 step 2)
#
# These deliberately reproduce today's model.strip() route structure
# exactly -- one laden-sea leg, one discharge/port call, one ballast-sea
# leg -- with no separate canal or queue segments yet, because the legacy
# model doesn't isolate canal transit or congestion time from ordinary
# laden/ballast sea time either (ASIA_LEG_DAYS already folds in a 1-day
# canal allowance; ASIA_RT_CONG folds in 4 waiting days per leg, both at
# the full sea-passage rate). Queue/canal separation is step 6, done only
# after the legacy-equivalence test below passes -- introducing it now
# would make this route diverge from the number it needs to reproduce.
# ---------------------------------------------------------------------------


def europe_route_segments(params: model.Params) -> tuple[VoyageSegment, ...]:
    """Europe round trip: loading (zero duration -- the legacy model has no
    separate loading time or loading fuel, only a commercial $/MMBtu
    loading cost applied elsewhere) -> laden sea -> discharge/port ->
    ballast sea. Total duration equals params.europe_laden_days +
    europe_port_days + europe_ballast_days, i.e. model.py's europe_rt."""
    return (
        VoyageSegment("loading", OperatingState.LOADING, duration_days=0.0, ets_scope_fraction=0.5),
        VoyageSegment("laden_sea", OperatingState.LADEN_SEA, duration_days=params.europe_laden_days,
                      ets_scope_fraction=0.5),
        VoyageSegment("discharge", OperatingState.DISCHARGE, duration_days=params.europe_port_days,
                      ets_scope_fraction=1.0),
        VoyageSegment("ballast_sea", OperatingState.BALLAST_SEA, duration_days=params.europe_ballast_days,
                      ets_scope_fraction=0.5),
    )


def asia_route_segments(params: model.Params) -> tuple[VoyageSegment, ...]:
    """Asia round trip (base or congested, according to params.asia_rt_days
    /params.asia_laden_days as already configured on params -- this
    function does not itself choose between base and congested). Same
    structural simplification as europe_route_segments: no separate canal
    or queue segments yet. Every segment is outside EU ETS scope, matching
    the legacy model's ets line only ever being added to eu_margin."""
    asia_laden = params.asia_laden_days
    asia_port = params.asia_port_days
    asia_ballast = params.asia_rt_days - asia_laden - asia_port
    return (
        VoyageSegment("loading", OperatingState.LOADING, duration_days=0.0, ets_scope_fraction=0.0),
        VoyageSegment("laden_sea", OperatingState.LADEN_SEA, duration_days=asia_laden, ets_scope_fraction=0.0),
        VoyageSegment("discharge", OperatingState.DISCHARGE, duration_days=asia_port, ets_scope_fraction=0.0),
        VoyageSegment("ballast_sea", OperatingState.BALLAST_SEA, duration_days=asia_ballast, ets_scope_fraction=0.0),
    )
