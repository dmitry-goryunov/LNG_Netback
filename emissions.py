"""
emissions.py -- combustion emissions and EU ETS cost from physical.py's
voyage ledger (Phase 2, Improvement 5 of docs/IMPROVEMENT_PLAN.md; see
docs/PHASE2_PLAN.md Section 5 and Section 8 step 4).

Pure functions + dataclasses, no Streamlit import, same style as
physical.py. Not wired into model.py, decision.py or app.py yet.

Vented gas (physical.SegmentResult.vented_mmbtu -- surplus natural BOG
exceeding both demand and reliquefaction capacity) is counted as raw,
uncombusted methane: the full mass, not a small slip fraction of it, since
none of it passed through an engine. This was a KNOWN LIMITATION (silently
zero) until docs/PHASE2_PLAN.md Section 8 step 6 found it was no longer
hypothetical -- the congested Asia route vents ~148.7 t LNG-equivalent per
round trip at the engine's default zero reliquefaction capacity, ~3,717 t
CO2e if left uncounted, larger than the rest of that route's combustion
emissions combined. ch4_slip_tonnes (from combustion) and ch4_vented_tonnes
(never combusted) are exposed separately on SegmentEmissions/
VoyageEmissions specifically so this doesn't get quietly re-buried inside
one aggregate "ch4_tonnes" figure -- see docs/PHASE2_PLAN.md Section 10
items 1-2 for the still-open questions this doesn't resolve (what
reliquefaction capacity a real vessel actually has, and whether vented gas
is flared rather than released raw in practice).

Simplification, not yet refined: vented mass is converted to tonnes via
LNG_MMBTU_PER_T and treated as 100% methane by mass. Pipeline-quality LNG
boil-off is predominantly but not exactly pure methane; no compositional
breakdown exists anywhere in this codebase to do better than that yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import physical

# Combustion factors -- already used (as hand-derived constants) in
# model.py's own comment deriving co2_eu_ets_tonnes; carried forward
# unchanged, only their application moves from one hand-computed constant
# to a per-segment sum. See docs/PHASE2_PLAN.md Section 5.1: do not
# conflate LNG_MMBTU_PER_T (LNG's own calorific value) with
# physical.VesselPerformance.energy_factor_mmbtu_per_t (a back-solved
# VLSFO-equivalent ratio for a different fuel) -- they are ~1.2x apart.
CO2_T_PER_T_VLSFO = 3.15
CO2_T_PER_T_LNG = 2.75
LNG_MMBTU_PER_T = 48.6

# GWP100 figures commonly used in EU regulatory text (MRV/FuelEU); confirm
# against the in-force text before relying on these for actual compliance
# reporting -- docs/PHASE2_PLAN.md Section 10 item 3.
GWP_CH4_100YR = 25.0
GWP_N2O_100YR = 298.0

# UNCONFIRMED placeholders, not sourced from this vessel's actual engine
# spec -- docs/PHASE2_PLAN.md Section 10 item 3. The charter sheet implies
# a "174k 2-stroke" vessel; modern 2-stroke low-pressure dual-fuel engines
# (e.g. WinGD X-DF) typically report lower methane slip than 4-stroke
# DFDE, but the exact figure needs this vessel's actual spec, not a
# plausible guess shipped as if verified. N2O has no defensible default at
# all without one, so it ships as zero (an explicit "not modelled yet",
# not an invented number) rather than a guessed non-zero placeholder.
DEFAULT_METHANE_SLIP_FRACTION = 0.003  # UNCONFIRMED: 0.3% of LNG energy burned
DEFAULT_N2O_KG_PER_T_LNG = 0.0  # not modelled: no defensible default exists yet


@dataclass(frozen=True)
class SegmentEmissions:
    segment_name: str
    co2_tonnes: float
    ch4_slip_tonnes: float
    ch4_vented_tonnes: float
    ch4_tonnes: float  # == ch4_slip_tonnes + ch4_vented_tonnes
    n2o_tonnes: float
    co2e_tonnes: float
    ets_scope_fraction: float
    ets_covered_co2e_tonnes: float


@dataclass(frozen=True)
class VoyageEmissions:
    total_co2_tonnes: float
    total_ch4_slip_tonnes: float
    total_ch4_vented_tonnes: float
    total_ch4_tonnes: float
    total_n2o_tonnes: float
    total_co2e_tonnes: float
    ets_covered_co2e_tonnes: float
    fueleu_exposure: Literal["NOT_PRICED"]
    ets_payer: Literal["unresolved"]
    segments: tuple


def segment_emissions(
    result: physical.SegmentResult,
    methane_slip_fraction: float = DEFAULT_METHANE_SLIP_FRACTION,
    n2o_kg_per_t_lng: float = DEFAULT_N2O_KG_PER_T_LNG,
) -> SegmentEmissions:
    """CO2/CH4/N2O/CO2e for one segment result, derived from actual fuel
    consumed (and gas vented) in that segment -- not a static per-voyage
    constant.

    LNG is only counted as burned in laden-state segments
    (physical.LADEN_STATES): physical.py never burns LNG during ballast,
    only heel BOG, which is either vented or met by liquid fuel under the
    same shortfall rules as any other segment. Vented gas is not
    state-gated the same way: physical.py's own reconciliation only ever
    produces a nonzero vented_mmbtu on a laden-state segment today (ballast
    starts at zero/near-zero heel, so has nothing to vent), but this
    function does not assume that will always hold -- it reads whatever
    vented_mmbtu the ledger actually reports.
    """
    is_laden = result.segment.state in physical.LADEN_STATES
    lng_burned_tonnes = (result.bog_burned_mmbtu + result.forced_lng_mmbtu) / LNG_MMBTU_PER_T if is_laden else 0.0
    vlsfo_tonnes = result.shortfall_liquid_fuel_tonnes
    vented_tonnes = result.vented_mmbtu / LNG_MMBTU_PER_T

    co2 = lng_burned_tonnes * CO2_T_PER_T_LNG + vlsfo_tonnes * CO2_T_PER_T_VLSFO
    ch4_slip = lng_burned_tonnes * methane_slip_fraction
    ch4_vented = vented_tonnes  # full mass, not a slip fraction -- never combusted at all
    ch4_total = ch4_slip + ch4_vented
    n2o = lng_burned_tonnes * n2o_kg_per_t_lng / 1000.0
    co2e = co2 + ch4_total * GWP_CH4_100YR + n2o * GWP_N2O_100YR
    scope = result.segment.ets_scope_fraction

    return SegmentEmissions(
        segment_name=result.segment.name, co2_tonnes=co2,
        ch4_slip_tonnes=ch4_slip, ch4_vented_tonnes=ch4_vented, ch4_tonnes=ch4_total,
        n2o_tonnes=n2o, co2e_tonnes=co2e, ets_scope_fraction=scope, ets_covered_co2e_tonnes=co2e * scope,
    )


def voyage_emissions(
    ledger: physical.VoyageLedger,
    methane_slip_fraction: float = DEFAULT_METHANE_SLIP_FRACTION,
    n2o_kg_per_t_lng: float = DEFAULT_N2O_KG_PER_T_LNG,
) -> VoyageEmissions:
    """Sums segment_emissions() over every segment in the ledger. ETS scope
    is read directly from each segment (set by the route builder, e.g.
    physical.europe_route_segments' 0.5 sea / 1.0 at-berth split) -- this
    function does not decide scope, only aggregates what the route already
    specified.

    fueleu_exposure and ets_payer are placeholders per
    docs/PHASE2_PLAN.md Section 5.3/5.4: an honest "not yet resolved"
    marker, not a silently-invented shadow price or payer allocation.
    """
    segments = tuple(
        segment_emissions(r, methane_slip_fraction, n2o_kg_per_t_lng) for r in ledger.segments
    )
    return VoyageEmissions(
        total_co2_tonnes=sum(s.co2_tonnes for s in segments),
        total_ch4_slip_tonnes=sum(s.ch4_slip_tonnes for s in segments),
        total_ch4_vented_tonnes=sum(s.ch4_vented_tonnes for s in segments),
        total_ch4_tonnes=sum(s.ch4_tonnes for s in segments),
        total_n2o_tonnes=sum(s.n2o_tonnes for s in segments),
        total_co2e_tonnes=sum(s.co2e_tonnes for s in segments),
        ets_covered_co2e_tonnes=sum(s.ets_covered_co2e_tonnes for s in segments),
        fueleu_exposure="NOT_PRICED",
        ets_payer="unresolved",
        segments=segments,
    )


def ets_cost_usd(
    voyage: VoyageEmissions,
    eua_price_eur_per_t: float,
    eur_usd_fx: float,
    contractual_share: float = 1.0,
) -> float:
    """ETS cost in USD from ETS-covered CO2e. contractual_share defaults to
    1.0 (full liability), matching the legacy model's implicit assumption
    (co2_eu_ets_tonnes * eua_price * fx / cargo has no allocation split);
    docs/PHASE2_PLAN.md Section 5.4 exposes this so a future contract-terms
    phase can split it without changing this function's contract."""
    if not 0.0 <= contractual_share <= 1.0:
        raise ValueError(f"contractual_share must be within [0, 1], got {contractual_share}")
    return voyage.ets_covered_co2e_tonnes * eua_price_eur_per_t * eur_usd_fx * contractual_share
