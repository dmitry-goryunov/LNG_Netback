# Phase 2 implementation plan: unified physical and emissions engine

**Governing plan:** `docs/IMPROVEMENT_PLAN.md` v1.2, Improvements 3-5, Phase 2.
**Status:** in progress -- see `docs/IMPLEMENTATION_STATUS.md` for exactly
what has been built against this plan so far.
**Builds on:** current `main`, which by now includes `v2.3-phase1`
(`0be26cc`) plus later same-day work not reflected in that release's own
docs: the 36-month forward strip and multi-tenor FX curve (`312e7b8`,
`b46a75a`), Decision-page waterfalls with explicit sunk-cost add-back
(`14842f3`, `3707d70`, `45de9ab`), and the two "all months in one place"
graphs with the chronological-axis fix (`cc213ef`). Anyone resuming this
plan should re-run Section 5 ("Step 1: inspect the current code") against
actual `main`, not trust any single commit's own snapshot of "current
state" -- including this document's, which will itself go stale the moment
more work lands.

## 0. Incorporated from `docs/AGENT_TASK_V2_4_PHYSICAL_ENGINE.md`

A separately-authored task brief for the same phase landed in this repo.
Reviewed against this plan; three genuinely better ideas were merged in
(marked inline below with **[v2.4 brief]**): a three-state first-cargo
model instead of two, an explicit terminal-heel reconciliation, and a
mutation-testing requirement. Its single-release, ~70-test, five-new-UI,
ZIP-packaged scope was deliberately **not** adopted -- that's several weeks
of work bundled into one unreviewable cutover, which is exactly the
big-bang risk Section 3 below exists to avoid. This plan keeps the staged,
independently-committable-increment structure instead (Section 8); the
brief's content was mined for requirements, not adopted as a work plan.

---

## 1. Goal and exit criteria (recap)

Replace the disconnected BOG/fuel/ETS assumptions in `model.py` with one
segment-level mass-and-energy balance that every consumer (deterministic
valuation, the programme optimiser, and eventually risk) reads from, so that:

- `Loaded = Delivered + LNGBurned + Vented + OtherLoss + Heel` holds for every
  voyage, within numerical tolerance;
- changing boil-off rate changes delivered quantity, fuel cost, reliquefaction,
  venting, liquid-fuel shortfall, emissions and route value together, not in
  isolation;
- queue/waiting time stops being charged at full propulsion fuel rates;
- ETS emissions are derived from actual fuel burned, not a hand-set constant.

## 2. The concrete disconnect in the current code

`model.strip()` (`model.py:197-318`) computes ship cost from **flat per-day
tonnage constants** that do not reference `boil_off_rate` at runtime:

```python
eu_ship = (charter * europe_rt
           + (residual_laden_vlsfo * eu_laden + ballast_fuel * eu_ballast
              + port_fuel_rate * eu_port) * vlsfo_price) / cargo   # model.py:243-247
```

`residual_laden_vlsfo` (63.6 t/d), `ballast_fuel` (130 t/d) and
`port_fuel_rate` (25 t/d) are user-editable `Params` fields, but they are
independent numbers -- editing `boil_off_rate` changes the boil-off cost line
(`eu_bo_frac * ttf_usd`, a **revenue** deduction) without touching
`residual_laden_vlsfo` (a **fuel-cost** input). `co2_eu_ets_tonnes = 4425.9`
(`model.py:94`) is hand-derived once and never recalculated. This is exactly
Improvement 3's diagnosis, confirmed by reading the code rather than assumed
from the plan text.

**However**, the four legacy constants are not arbitrary -- they were
hand-derived from a single self-consistent physical assumption, which is the
key fact this plan leans on:

```
laden_fuel_requirement   = 150.0   t/d VLSFO-equivalent   (total laden energy demand)
natural_bog_offset_t     =  86.4   t/d VLSFO-equivalent   (natural BOG, energy-normalised)
natural_bog_offset_mmbtu = 3500    MMBtu/d                (= cargo_size * boil_off_rate = 3,500,000 * 0.0010)
residual_laden_vlsfo     =  63.6   t/d = 150.0 - 86.4      (liquid VLSFO make-up)
```

`natural_bog_offset_mmbtu / natural_bog_offset_t = 3500 / 86.4 = 40.5093 MMBtu
per tonne VLSFO-equivalent` is the implicit energy-content conversion factor
already embedded in the workbook's constants. Under it, the legacy laden leg
is precisely a **shortfall** case of the segment balance in Section 4 below:
BOG_natural (86.4) &lt; Demand (150), so the whole `residual_laden_vlsfo` figure
*is* `Shortfall`, satisfied entirely by liquid fuel, with zero surplus,
reliquefaction or venting. That gives this plan an exact, provable
legacy-equivalence target instead of a fuzzy re-baseline (Section 6).

## 3. Key design decision: additive, not a rewrite-in-place

**Recommendation: `model.strip()` is not modified in this phase.** The new
segment engine (`physical.py`, `emissions.py`) is built and tested standalone,
proven equivalent to the legacy constants (Section 6), and wired into
`decision.py`'s route valuation as the *last* step (Section 8, step 8) --
not into `model.strip()` itself.

Reasons:

- The frozen 64/64 legacy suite (`tests/test_model.py`, pinned to the
  `v2.2-renewal-rate` tag) asserts `model.strip()`'s output values directly.
  Refactoring `strip()` internals risks silently drifting those fixtures even
  when the new formula is "more correct" -- Design Principle 2.6 in the
  improvement plan exists precisely to prevent this.
- `decision.py` already isolates the "renewal-rate screen" legacy mode from
  the new decision modes (`v2.3-phase1`). The same seam lets the programme
  optimiser switch to physical-engine-derived cargo values while
  `RENEWAL_RATE_SCREEN` keeps calling `model.strip()` unchanged, satisfying
  Phase 2's exit criterion ("one engine determines cargo loss, fuel and
  emissions") for every *new* decision mode without breaking the legacy
  screen the plan explicitly says must be preserved (Section 2.6, Section 7).
- It keeps this phase reviewable in independently-committable increments
  (engine standalone -> equivalence-tested -> wired into programme) instead
  of one large, high-risk cutover commit.

This is a decision worth confirming before implementation starts, since it
determines whether `co2_eu_ets_tonnes` is literally deleted from `model.py`
this phase (it is not, under this recommendation -- it becomes unused by the
new decision modes but stays as the legacy screen's input) or removed
outright.

## 3a. Related small increment: three-state first-cargo model **[v2.4 brief]**

Independent of the physical engine and safe to do first (`decision.py`
only, no `model.py`/physical dependency, small diff): `decision.py`'s
`RouteValue` already carries `procurement_treatment` and `loading_treatment`
as *separate* `CostTreatment` fields, but `cost_policy()` currently only
ever produces two combinations -- both `SUNK` (`POST_LIFT_DIVERSION`,
`VESSEL_PROGRAMME`'s current cargo) or both `INCLUDED` (`PRE_LIFT_CARGO`,
any `future_cargo=True`). There is no way to represent a cargo that is
already bought (procurement sunk) but not yet loaded (loading still
avoidable) -- a real, distinct commercial state (DES/FOB-purchased cargo
sitting pre-loading).

Add:

```python
class FirstCargoState(str, Enum):
    ALREADY_LOADED = "already_loaded"                                    # procurement + loading both sunk
    PROCUREMENT_COMMITTED_LOADING_REQUIRED = "procurement_committed_loading_required"  # procurement sunk, loading avoidable
    FULLY_PRE_LIFT = "fully_pre_lift"                                    # both avoidable (== today's PRE_LIFT_CARGO)
```

`cost_policy()` gains an optional `first_cargo_state` parameter (default
`None`, preserving today's two-combination behaviour exactly when omitted
-- this is additive, not a breaking change to the existing signature or
any existing call site); when supplied it independently sets
`procurement_treatment`/`loading_treatment` per the state. Surfaces as a
third radio option (or extends the existing mode selector) on the Decision
page for `POST_LIFT_DIVERSION`/`VESSEL_PROGRAMME`'s *current* cargo only --
future programme cargoes stay `FULLY_PRE_LIFT` always, per existing
`route_value(..., future_cargo=True)` semantics.

## 4. New module: `physical.py`

No Streamlit import, pure functions + dataclasses, same style as `model.py` /
`decision.py`. Lives at repo root next to them (the `netback_engine/` package
layout in `IMPROVEMENT_PLAN.md` Section 3 is a later Improvement-12
restructuring, not a Phase 2 prerequisite -- introducing a package now for two
new files would be premature).

### 4.1 Enums (defined in `physical.py`; no separate `enums.py` yet)

```python
class OperatingState(str, Enum):
    LOADING = "loading"
    LADEN_SEA = "laden_sea"
    LADEN_QUEUE = "laden_queue"       # Improvement 4: queue is its own state
    CANAL_TRANSIT = "canal_transit"
    BALLAST_SEA = "ballast_sea"
    BALLAST_QUEUE = "ballast_queue"
    DISCHARGE = "discharge"
    PORT = "port"                     # non-discharge port/idle time

class ShortfallSource(str, Enum):
    LIQUID_FUEL = "liquid_fuel"
    FORCED_VAPORISATION = "forced_vaporisation"
```

### 4.2 `VesselPerformance` (Params-like, user-editable, one instance per vessel class)

```python
@dataclass
class VesselPerformance:
    energy_factor_mmbtu_per_t: float = 40.5093   # VLSFO-equiv conversion; see Section 2 derivation
    demand_mmbtu_per_day: dict[OperatingState, float] = field(default_factory=lambda: {
        OperatingState.LADEN_SEA: 150.0 * 40.5093,
        OperatingState.LADEN_QUEUE: 40.0 * 40.5093,   # queue: aux load only, no propulsion
        OperatingState.CANAL_TRANSIT: 150.0 * 40.5093,
        OperatingState.BALLAST_SEA: 130.0 * 40.5093,
        OperatingState.BALLAST_QUEUE: 35.0 * 40.5093,
        OperatingState.DISCHARGE: 25.0 * 40.5093,
        OperatingState.PORT: 25.0 * 40.5093,
        OperatingState.LOADING: 25.0 * 40.5093,
    })
    bor_fraction_per_day: float = 0.0010          # overridable per state later; one figure for now
    reliq_capacity_mmbtu_per_day: float = 0.0      # 0 = no reliquefaction plant (conservative default)
    shortfall_source: ShortfallSource = ShortfallSource.LIQUID_FUEL
    methane_slip_pct_of_fuel_energy: float = 0.003  # 0.3%: 2-stroke low-pressure dual-fuel default: CONFIRM against actual engine spec (2-stroke DFDE/MEGI/X-DF slip factors differ materially; do not ship this default un-reviewed)
    pilot_fuel_fraction: float = 0.0               # diesel pilot fuel as a fraction of gas energy, if applicable
```

The `LADEN_QUEUE`/`BALLAST_QUEUE`/`CANAL_TRANSIT`/`PORT`/`DISCHARGE` numbers
above are **placeholders**, not sourced from the workbook (there is no vessel
performance sheet in `LNG history.xlsx` -- confirmed by reading `data.py`;
only HH/TTF/JKM/FX/charter/US-netbacks/US-transport are loaded). They need
sign-off from someone with the vessel's actual SFOC/aux-load curve before
this ships; until then they are clearly-labelled placeholders exposed as
editable `Params`-style sidebar fields, exactly like `laden_fuel_requirement`
is today.

### 4.3 `VoyageSegment` and route construction

```python
@dataclass(frozen=True)
class VoyageSegment:
    state: OperatingState
    duration_days: float
    emissions_scope_fraction: float   # 0.0 / 0.5 / 1.0 -- see emissions.py Section 5.2
```

Route-builder functions replace the flat day-multipliers in `strip()`:

```python
def europe_route_segments(params: model.Params) -> list[VoyageSegment]: ...
def asia_route_segments(params: model.Params, congested: bool) -> list[VoyageSegment]: ...
```

For Improvement 4 (queue separation), congestion stops being "+4 days added
to each leg's laden/ballast total" (today's `ASIA_RT_CONG` in `model.py:31`)
and becomes explicit `LADEN_QUEUE`/`BALLAST_QUEUE` segments appended to the
base route, at the queue demand rate instead of the full sea-passage rate.
**This changes `ASIA_RT_CONG`'s total-day arithmetic not at all** (still
`2*(ASIA_LEG_DAYS+4.0)+5.0` if queue days are modelled as added time) but
changes its *fuel and emissions* consequence, which is precisely Improvement
4's point and acceptance criterion ("adding five queue days does not add five
full-speed steaming days"). The Phase-3 programme benchmark tests (52/55/54
day horizons) are duration-only and are unaffected by this -- confirmed by
inspection of `tests/test_decision_programme.py`, which asserts on
`used_days`/`residual_days`, never on fuel or emissions figures.

### 4.4 Segment balance (Improvement 3's equations, bound to the fields above)

For each segment, given `inventory_in` (MMBtu):

```
BOG_natural = inventory_in * vessel.bor_fraction_per_day * segment.duration_days
Demand      = vessel.demand_mmbtu_per_day[segment.state] * segment.duration_days
BOG_burn    = min(BOG_natural, Demand)
Surplus     = max(BOG_natural - Demand, 0)
Reliquefied = min(Surplus, vessel.reliq_capacity_mmbtu_per_day * segment.duration_days)
Vented      = Surplus - Reliquefied
Shortfall   = max(Demand - BOG_natural, 0)
inventory_out = inventory_in - BOG_natural + Reliquefied   # reliquefied gas returns to tank
```

`Shortfall` is met either by liquid fuel purchase (t = `Shortfall /
energy_factor_mmbtu_per_t`, costed at `vlsfo_price`) or by forced vaporisation
of cargo (further reduces `inventory_out`), per
`vessel.shortfall_source`. Only `LADING`-state segments (`LOADING`,
`LADEN_SEA`, `LADEN_QUEUE`, `CANAL_TRANSIT` while laden, `DISCHARGE`) draw
down the laden cargo inventory; ballast-leg segments draw down a separate,
much smaller **heel** inventory that starts at `params.heel_fraction *
cargo_size` (new `Params` field, default introduced in this phase) and cannot
go negative -- if ballast demand exceeds heel BOG, the rest is `Shortfall`
exactly as above (this is the ballast case's realistic behaviour; today's
model has no heel concept at all and silently assumes infinite/unmodelled
ballast BOG availability, which does not exist since ballast tanks are
essentially empty).

`run_voyage(segments, vessel, initial_cargo_mmbtu, initial_heel_mmbtu) ->
VoyageLedger` chains these, returning per-segment results plus totals:
`delivered_mmbtu`, `lng_burned_mmbtu`, `vented_mmbtu`, `reliquefied_mmbtu`,
`shortfall_mmbtu`, `shortfall_cost_usd`, `heel_out_mmbtu`, and the
reconciliation identity `loaded == delivered + lng_burned + vented +
other_loss + heel_out` (± floating-point tolerance; `other_loss` is 0 until a
concrete loss mechanism is specified -- do not invent one).

**[v2.4 brief] Two separate reconciliations, not one.** The main identity
above covers only the laden-period disposition of the loaded cargo (ending
at `heel_out_mmbtu` = heel *as retained at discharge*). Ballast-leg BOG and
any forced vaporisation during the return leg draw down that heel further
before the vessel is available again; report this as a second, explicit
reconciliation --

```
heel_at_discharge = delivered_leg's heel_out_mmbtu
terminal_heel = heel_at_discharge - ballast_lng_consumed_mmbtu
```

-- so `VoyageLedger` exposes both `heel_at_discharge_mmbtu` and
`terminal_heel_mmbtu`, and the acceptance criterion "ballast consumption
does not reduce delivered cargo" is checked against the *first* identity
(which never includes ballast terms), not conflated with the second.

## 5. New module: `emissions.py`

### 5.1 Combustion factors

```python
@dataclass
class FuelEmissionFactors:
    co2_t_per_t_lng: float = 2.75       # already used implicitly in model.py:91's comment
    co2_t_per_t_vlsfo: float = 3.15     # already used in model.py:91's comment
    lng_mmbtu_per_t: float = 48.6       # model.py:91's comment: cargo*BOR/48.6 -- LNG calorific value
    ch4_g_per_mmbtu_fuel: float = ...   # TODO: derive from methane_slip_pct_of_fuel_energy, not a bare constant
    n2o_g_per_mmbtu_fuel: float = ...
GWP_CH4_100YR = 25     # commonly-used regulatory GWP100 figure; confirm against the in-force MRV/FuelEU text before relying on it for compliance
GWP_N2O_100YR = 298
```

`model.py:91`'s existing comment already hand-derives `co2_eu_ets_tonnes`
from `resVLSFO*3.15` and `cargo*BOR/48.6*2.75` -- those two combustion factors
(3.15 t CO2/t VLSFO, 2.75 t CO2/t LNG) are the ones to carry forward
unchanged; only their *application* moves from a single hand-computed
constant to a per-segment sum over `VoyageLedger`.

**Do not conflate `lng_mmbtu_per_t` (48.6, LNG's own calorific value, used
only to turn MMBtu of LNG actually burned into tonnes of LNG for the 2.75
t/t factor) with `VesselPerformance.energy_factor_mmbtu_per_t` (40.5093,
Section 4.2's back-solved "VLSFO-equivalent" ratio, used to turn MMBtu of
*demand* into tonnes of *VLSFO* for costing and for the 3.15 t/t factor).**
They are two distinct constants for two distinct fuels, both already latent
in `model.py`'s comments; using one where the other belongs would misstate
both fuel cost and CO2 by a material amount (48.6 / 40.5093 ~= 1.2x). CO2
from `LNGBurned` (BOG actually combusted, from `run_voyage`'s `lng_burned_mmbtu`)
uses `lng_mmbtu_per_t` + `co2_t_per_t_lng`; CO2 from `Shortfall` met by
liquid fuel uses `energy_factor_mmbtu_per_t` + `co2_t_per_t_vlsfo`.

### 5.2 ETS scope

```python
def ets_scope_fraction(segment: VoyageSegment, route: Literal["europe", "asia"]) -> float:
    ...  # 1.0 for intra-EU / EU-port segments, 0.5 for the extra-EU voyage, 0.0 for non-EU (Asia) legs
```

This is a direct function of which route/segment we're in, not a new input --
`europe_route_segments()` marks its EU-port segments as scope 1.0 and its
open-sea legs as scope 0.5 (per the ETS Directive's 50%-extra-EU /
100%-intra-EU-and-at-berth rule, already correctly described in
`IMPROVEMENT_PLAN.md` Improvement 5); Asia-route segments are scope 0.0
throughout, matching the legacy model's implicit assumption that ETS applies
to the Europe route only (`model.py`'s `ets` line is only added to
`eu_margin`, never to `asia_margin`).

### 5.3 FuelEU

Ship this phase as `fueleu_exposure: NOT_PRICED`, an explicit, visible,
unresolved-exposure marker on the audit output -- **not** a shadow price.
`IMPROVEMENT_PLAN.md` Improvement 5 offers a shadow-price or an
explicit-exposure representation as equally acceptable; a wrong shadow price
silently baked into a valuation is worse than an honest gap, and there is no
FuelEU compliance-balance data source available in this workbook to calibrate
one against. Revisit once a real compliance-balance input exists.

### 5.4 Contractual allocation

`emissions.py` returns physical CO2e and ETS cost; it does **not** yet decide
who pays (owner/charterer/seller/buyer/unresolved -- that's a `feasibility.py`
/ contract-terms concern, Phase 4). This phase adds the field
`ets_payer: Literal["unresolved"] = "unresolved"` to the ledger output so the
audit always shows the question was considered, without answering it
prematurely.

## 6. Legacy-equivalence test (the anchor test for this phase) -- **DONE, verified**

Implemented in `tests/test_physical_legacy_equivalence.py`, run through the
actual `europe_route_segments()`/`asia_route_segments()` (not a hand-built
segment list -- that was Section 4.4's preview test only). All pass for
Europe, Asia base and Asia congested at `model.Params()` defaults:

- laden/ballast/discharge fuel rates each reproduce `residual_laden_vlsfo`
  (63.6 t/d), `ballast_fuel` (130 t/d) and `port_fuel_rate` (25 t/d) exactly
  (within 0.01 t/d, i.e. floating-point noise);
- `reliquefied_mmbtu == 0` and `vented_mmbtu == 0` throughout, as expected;
- total liquid-fuel tonnage matches `eu_ship`/`as_ship`'s fuel-only
  component (excluding charter) to 1e-6 relative tolerance.

**The CO2/ETS check surfaced a real, verified finding, not a rounding
question.** At the legacy constant's *own* scope convention (a uniform 50%
applied to every segment, confirmed by reading `model.py:91`'s derivation
comment literally rather than assuming the textbook rule), the new engine
reproduces `co2_eu_ets_tonnes = 4425.9` to within **0.007%** (4426.19 t
computed) -- strong confirmation the combustion arithmetic and the two
factors (2.75 t CO2/t LNG, 3.15 t CO2/t VLSFO) are right. But
`europe_route_segments()` implements the *actual* EU ETS Directive
(2003/87/EC as amended by 2023/959: 50% for the sea voyage between a
non-EU and an EU port, but a separate 100% provision for time genuinely at
berth in an EU port) -- and at that correct, differentiated scope, total
CO2e is **4623.1 t, 4.45% higher than the legacy constant**, because the
legacy model never separately captured the 100%-at-berth provision. This
is not a bug to reconcile away: it means EU-bound cargo's ETS cost will
genuinely increase by ~4.45% once this engine is wired into route
valuation (step 8), as a direct, correct consequence of fixing a real
understatement in the current screening model. Both figures are pinned as
explicit, separately-named tests (`test_co2_at_legacy_uniform_scope_...`
and `test_co2_at_correct_differentiated_scope_...`) so this stays visible
rather than silently absorbed into "close enough."

This test is the equivalent of `risk.py`'s "zero-shock scenario reprices to
~0 P&L vs `model.strip()` base" check (`model.py`/`risk.py` internal
consistency gate) -- it proves the new engine is a strict generalisation of
the old constants rather than a new, unreconciled set of assumptions. If it
cannot be made to pass within a stated tolerance, that is a **stop-and-report**
condition, not a "close enough, ship it" one, because it would mean the
current production numbers and the new physical engine disagree about what
the vessel actually burns.

## 7. New tests (Improvement 14 sections B and E)

All in a new `tests/test_physical_engine.py` (pytest, alongside
`test_decision_programme.py`), plus property-style tests using `hypothesis`
if it's added to `requirements.txt` (optional for this phase; plain
parametrised cases are sufficient to start and match this repo's existing
test style, which favours explicit fixtures over property frameworks).

| # | Test | Improvement 14 ref | Status |
|---|---|---|---|
| 1 | `loaded == delivered + burned + vented + other_loss + heel` for Europe, Asia base, Asia congested | §B.1 | done as `test_run_voyage_reconciles_exactly` (generic) + the equivalence suite (real routes); not yet a dedicated per-route reconciliation assertion for all three named cases in one place |
| 2 | Inventory never goes negative across any segment sequence | §B.2 | done (`simulate_segment` raises rather than allowing it; tested) |
| 3 | A `LADEN_QUEUE`/`BALLAST_QUEUE` segment's fuel burn uses the queue demand rate, strictly less than the sea-passage rate at the same duration | §B.3, Improvement 4 | partially done -- `test_queue_demand_below_sea_demand_at_default_rates` and `test_queue_state_emissions_below_sea_state_at_equal_duration` prove it at the `VesselPerformance` level; no route yet emits a queue segment (step 6) |
| 4 | Increasing `bor_fraction_per_day` strictly cannot increase `delivered_mmbtu` | §B.4 | done (`test_higher_bor_cannot_increase_delivered_cargo`) |
| 5 | `reliquefied_mmbtu <= surplus` and `<= reliq_capacity * days` jointly | §B.5 | done (`test_full_/test_partial_reliquefaction_capacity_...`) |
| 6 | Ballast-leg heel shortfall never reduces already-computed laden `delivered_mmbtu` (segments are one-directional) | §B.6 | done (`test_ballast_consumption_does_not_reduce_delivered_cargo`) |
| 7 | Increasing any fuel demand rate strictly increases summed CO2e | §E.1 | done (`test_higher_fuel_demand_increases_co2e` -- see step 4's writeup for why this needed a premise fix first) |
| 8 | An Asia-route segment always has `ets_scope_fraction == 0`; a Europe at-berth segment always has `1.0` | §E.2 | done (`test_asia_route_segments_are_all_outside_ets_scope`, `test_europe_route_segments_discharge_is_full_ets_scope`) |
| 9 | CH4/N2O contribute to `co2e` whenever `methane_slip_pct_of_fuel_energy > 0` | §E.3 | done (`test_methane_slip_.../test_n2o_contributes_...`) |
| 10 | Changing `eua_price` changes only EU-scope segments' cost, never Asia's | §E.4 | done (`test_eua_price_change_affects_europe_cost_only`) |
| 11 | Legacy-equivalence test (Section 6 above) | new, this plan | done -- see Section 6 |

**[v2.4 brief] Mutation check, not committed:** for at least the
conservation test (#1) and one emissions test (#7), deliberately break the
implementation locally (e.g. double-count a segment's burn, or hardcode
`ets_scope_fraction=0`), confirm the corresponding test actually fails, then
revert before committing. A test that can't be made to fail by breaking the
thing it claims to check is not verifying anything. Record what was broken
and that the test caught it in the commit message rather than a separate
file -- this repo doesn't otherwise keep a `test_results/` directory of
prose evidence, and introducing one just for this would be inconsistent
with everything else in Sections 3/9.

## 8. Sequencing

0. Section 3a (three-state first-cargo model) can land independently, any
   time, before or in parallel with the rest of this sequence -- it touches
   only `decision.py` and is additive to `cost_policy()`'s signature.
   **Not yet done** (independent of 1-3 below; still open).
1. **DONE** (`physical.py`, `tests/test_physical_engine.py`): enums,
   `VesselPerformance`, `VoyageSegment`, segment-balance function,
   `run_voyage`, unit-tested against hand-computed numbers. One design
   change made during this step, not anticipated in Section 4.2: BOR turned
   out to need to be per-state, not one vessel-wide float --
   `bor_fraction_per_day` is a `Mapping[OperatingState, float]`
   (`bor_for(state)`), defaulting `LOADING`/`DISCHARGE` to 0.0 (vapour-return
   to shore while alongside -- a real operational practice, not invented for
   convenience) and every sea/queue state to the legacy 0.0010/day. Without
   this, the discharge segment's full remaining cargo inventory would accrue
   BOG for `europe_port_days`, venting cargo the legacy model never assumed
   was vented at all.
2. **DONE** (`physical.py`'s `europe_route_segments`/`asia_route_segments`):
   built from `model.Params`' existing day-count fields, no `Params` field
   renamed. Deliberately no `congested` parameter -- congestion is already
   selected via `params.asia_rt_days` (`ASIA_RT_BASE`/`ASIA_RT_CONG`),
   exactly how `app.py`'s programme page already does it; a separate flag
   would be a second way to say the same thing.
3. **DONE** (`tests/test_physical_legacy_equivalence.py`). Passed for
   Europe, Asia base and Asia congested -- see Section 6's updated writeup,
   including the verified (not hidden) 4.45% ETS-scope divergence.
4. **DONE** (`emissions.py`, `tests/test_emissions.py`, 13 tests): combustion
   factors, CO2/CH4/N2O/CO2e aggregation, `ets_cost_usd()` with a
   `contractual_share` input, and the `NOT_PRICED`/`unresolved` FuelEU/
   payer placeholders from Section 5.3/5.4. ETS scope itself is not a
   separate function -- it was already fully implemented inline in the
   route builders back in step 2 (`ets_scope_fraction` on each
   `VoyageSegment`); `emissions.py` just reads it.

   Two real gaps found and handled while building this, not implementation
   busywork:
   - **`vessel_performance_from_params()` didn't exist, and without it
     `params.boil_off_rate`/`laden_fuel_requirement`/`ballast_fuel`/
     `port_fuel_rate` had zero effect on the physical engine.** A bare
     `VesselPerformance()`'s hardcoded defaults only *coincidentally*
     matched `model.Params()`'s defaults; nothing actually read from
     `params`. Confirmed live before fixing: two `europe_route_segments()`
     runs at `boil_off_rate=0.0001` vs `0.0015`, vesseled with a bare
     `VesselPerformance()`, produced byte-identical fuel tonnage. This
     reproduced Improvement 3's exact defect *inside the new engine*. Added
     `physical.vessel_performance_from_params()` and rewired every test
     (including the already-"passing" step 3 equivalence tests, which had
     been silently exercising this same coincidence) to use it.
   - A test asserting "higher LNG burn increases CO2e" failed -- correctly.
     Total fuel *demand* is fixed regardless of BOR; more boil-off only
     changes the *source mix* (LNG vs liquid fuel) for that same fixed
     demand, and LNG has a lower CO2 factor per unit energy than VLSFO in
     this model's own factors (2.75/48.6 ~= 0.0566 t/MMBtu vs
     3.15/40.5093 ~= 0.0778 t/MMBtu) -- so more BOG-sourced LNG correctly
     *reduces* CO2e. The test's premise was wrong, not the code; fixed to
     test what Improvement 14 section E item 1 actually specifies
     (increasing a demand *rate*, not shifting source mix).

   One limitation deliberately shipped and explicitly tested rather than
   silently absent: `vented_mmbtu` (surplus BOG exceeding both demand and
   reliquefaction capacity) is not counted as an emission at all yet. At
   the time step 4 landed this was exactly zero in every scenario
   exercised -- **step 6 below changed that, and the gap was closed the
   same day** (see step 6's writeup and Section 10 item 1).
5. Physical/emissions invariant tests (Section 7, items 1-10) -- partially
   covered already by `test_emissions.py` (items 7-10) and
   `test_physical_engine.py` (items 2, 4, 6); items 1, 3, 5 remain.
6. **DONE** (`physical.asia_route_segments`, 7 tests added/rewritten in
   `tests/test_physical_legacy_equivalence.py`). No `congested` parameter,
   per Section 4.3/8-step-2's note -- congestion is derived as whatever
   `params.asia_laden_days`/implied ballast days exceed `model.ASIA_LEG_DAYS`
   (the base one-way sea time), which is exactly 0 at `ASIA_RT_BASE` and
   exactly 4.0 days/leg at `ASIA_RT_CONG`, matching the legacy "+4 waiting
   days per leg" convention without needing a flag. `tests/
   test_decision_programme.py` (the Phase-3 programme benchmark suite)
   re-run unmodified: still 10/10, confirming duration-only arithmetic is
   genuinely untouched.

   **This step's fuel/emissions consequence turned out to be substantially
   larger than expected, and matters more than the Section 6 ETS-scope
   finding.** At the engine's default zero reliquefaction capacity, the
   laden queue segment's natural BOG -- generated from the still-full cargo
   inventory, independent of which operating state consumes it -- exceeds
   the queue's own *lower* demand (the queue burns less energy than full
   sea passage, but boil-off doesn't slow down to match). That is a
   surplus-BOG regime this engine had not exercised anywhere else covered
   in step 3 (every other laden segment tested is a liquid-fuel
   *shortfall*). The result: the entire surplus, ~148.7 t LNG-equivalent
   per congested round trip, is vented outright -- not reliquefied, not
   burned. `emissions.py`'s step-4 KNOWN LIMITATION (vented gas isn't
   counted) is no longer a hypothetical edge case: verified via
   `emissions.LNG_MMBTU_PER_T`, that vented mass, if counted as raw
   methane at GWP 25, is **~3,717 t CO2e -- larger than the entire rest of
   the round trip's combustion emissions combined** (Europe's unscoped
   total was ~8,852 t CO2 in Section 6; Asia is the same order of
   magnitude). The ballast queue segment does *not* show this effect (it
   starts at zero inventory/heel, so has no BOG to vent, and its full
   demand becomes an ordinary liquid-fuel shortfall at the lower queue
   rate) -- the asymmetry itself is informative: congestion is cheap in
   liquid-fuel terms on the ballast leg but creates a real venting problem
   on the laden leg specifically.

   Net effect on fuel cost alone (ignoring the uncounted venting): total
   liquid fuel for the congested round trip comes in at **~87% of the
   legacy flat-rate total** (verified, not estimated), since 8 of the
   congestion days move from full-sea rates to lower queue rates. Fuel
   cost goes *down*; a currently-unpriced methane liability goes up by an
   amount that dwarfs it. Reporting only the fuel-cost effect without the
   venting would be actively misleading.

   **This escalated two of Section 10's open decisions from "needs
   sign-off eventually" to "needs sign-off before step 8 (route-valuation
   wiring) or before any congested-Asia number is shown to a real user":**
   - closing the vented-methane emissions gap (Section 4.4/emissions.py) --
     **done the same day**, see Section 10 item 1;
   - `reliq_capacity_mmbtu_per_day = 0.0` was the *cause* of the entire
     vented amount -- **done the same day**: asked the user directly
     rather than guessing, confirmed this vessel class does carry
     reliquefaction capacity, and set an informed-estimate default
     (Section 10 item 2) that fully absorbs the congested queue's surplus,
     verified live.
7. **DONE.** "Physical reconciliation" expander on the existing Decision
   page (`app.py` page `"0 Decision"`) -- not a new top-level page yet
   (`IMPROVEMENT_PLAN.md`'s "Page 5: Physical reconciliation" is a Phase 6
   interface concern). Independent load-month/route pickers (Europe, Asia
   base, Asia congested), showing the full `VoyageLedger` (loaded/
   delivered/burned/vented/reliquefied/heel/reconciliation error), the
   `VoyageEmissions` summary (CO2/CH4/CO2e/ETS-covered), a per-segment
   detail table, and the reliq-capacity/methane-slip assumptions inline so
   they're not hidden in code. Explicitly labelled "preview -- not yet
   used in the decision values above," since step 8 (below) hasn't landed.
   Verified live: Europe and Asia-congested both reproduce the exact
   figures already proven in the test suite (e.g. congested Asia: 0 vented,
   7,226 MMBtu reliquefied, 0 ETS-covered CO2e).
8. Wire `decision.py`'s `route_value()` to read `duration_days` and
   ship/BOG/ETS cost from a `physical.py` voyage ledger instead of
   `row["europe_rt"]`/`row["eu_cargo"]`/`row["asia_cargo"]`, **for the three
   new decision modes only** (`POST_LIFT_DIVERSION`, `PRE_LIFT_CARGO`,
   `VESSEL_PROGRAMME`); `RENEWAL_RATE_SCREEN` keeps calling `model.strip()`
   unchanged (Section 3). This step re-baselines the programme optimiser's
   absolute values, which `docs/IMPLEMENTATION_STATUS.md` already documents
   as provisional and expected to change here.
9. Re-run the full suite: legacy 64/64 (must be unaffected --
   `RENEWAL_RATE_SCREEN`/`model.strip()` untouched), the existing 14
   decision/programme/risk-containment tests (values will change for
   programme tests per step 8; re-baseline those specific assertions, not
   the pass/fail architecture), plus the ~11 new physical/emissions tests.

## 9. Explicit non-goals for this phase

Deferred to later phases, per `IMPROVEMENT_PLAN.md`'s own sequencing --
listed here so scope creep is visible if it happens:

- multi-vessel optimisation (Phase 3 extension);
- exact contract/pricing calendars, VLSFO/EUA forward curves (Phase 4,
  Improvement 6);
- feasibility gates -- sanctions, credit, terminal slots (Phase 4,
  Improvement 7);
- individually dated cash-flow objects and terminal-state valuation beyond
  today's flat residual $/day (Phase 1 completion / Improvement 8);
- FuelEU shadow pricing (deliberately left as `NOT_PRICED`, Section 5.3);
- feeding the physical engine into `risk.py`'s vectorised scenario tail
  (Phase 5) -- but `run_voyage`'s split between a *price-independent* physical
  ledger (fuel/BOG/emissions masses) and *price-dependent* valuation
  (multiplying by `vlsfo_price`/`eua_price`/cargo netback) is deliberately
  kept as two separate steps in Section 4.4/5 so that Phase 5 can later cache
  the physical ledger and vectorise only the price multiplication, per
  Design Principle 2.3. No Phase 5 code is written now.

## 10. Open decisions needing sign-off

Reordered after step 6's finding. Items 1 and 2 are now both **closed**.

1. **CLOSED**, same day as escalation. Vented methane is now counted as raw
   CH4 in `emissions.py` (`ch4_vented_tonnes`, distinct from combustion
   `ch4_slip_tonnes`, both feeding `total_co2e_tonnes`). A further, related
   finding while closing this: a queue segment's *combustion-only* CO2 is
   lower than an equal-duration sea segment's (Improvement 4 holds for fuel
   cost), but at zero reliquefaction capacity its *total* CO2e (including
   the now-counted vent) can come out *higher* than the sea segment's --
   "queue costs less fuel" and "queue emits less" are not the same claim
   once venting is priced, and only converge back with adequate
   reliquefaction (verified both ways in `tests/test_emissions.py`).
2. **CLOSED**, same day. Asked the user directly rather than guessing:
   confirmed this vessel class does carry reliquefaction capacity, with
   the exact figure left to an informed estimate. `physical.
   DEFAULT_RELIQ_CAPACITY_MMBTU_PER_DAY = 3500` MMBtu/day (~72 t LNG/day,
   ~3 t/hour), now `vessel_performance_from_params()`'s default (not
   `VesselPerformance()`'s own dataclass default, which stays 0.0 for
   tests that want that baseline explicitly). Two independent estimates
   converge on this figure: (a) sizing to fully absorb this model's own
   natural BOG rate at rest (cargo_size * boil_off_rate = 3500 MMBtu/day,
   already the model's pre-existing `natural_bog_offset_mmbtu` reference
   constant) and (b) a generally-recalled real-world PRS capacity range
   for a ~170-180k m3 2-stroke LNGC, ~3-6 t LNG/hour -- 3500 MMBtu/day sits
   at the conservative (low) end of that range, not the generous end.
   Still an informed estimate, not a vendor spec sheet for this exact
   vessel -- verified consequence: the congested Asia route's queue
   surplus (~1,806.6 MMBtu/day) is now fully reliquefied, zero vented,
   confirmed live (`tests/test_physical_legacy_equivalence.py::
   test_congested_laden_queue_surplus_is_reliquefied_not_vented_at_the_
   default_reliq_capacity`). The original zero-reliq finding (~148.7 t
   vented, ~3,717 t CO2e) stays independently verified and reproducible
   via an explicit zero-reliq `VesselPerformance`
   (`test_congested_laden_queue_vents_without_reliquefaction`), not
   deleted just because the default changed.
3. **Section 3**: confirm `model.strip()` / `RENEWAL_RATE_SCREEN` stays
   permanently on the old formula (recommended), vs. a future phase
   eventually retiring it in favour of the physical engine with
   legacy-equivalent parameters. Affects whether `co2_eu_ets_tonnes` and the
   flat fuel constants are ever deleted from `model.py`, or just superseded
   for new decision modes.
4. **Section 4.2**: the queue/canal/discharge/port demand-rate placeholders
   not covered by items 1-2 above (e.g. `LADEN_QUEUE`'s 40 t/d, unused by
   any route builder yet since only Asia's queue segments exist so far) are
   not sourced from any workbook data and need a vessel-performance figure
   from someone with the actual SFOC curve, or an explicit acceptance that
   they ship as rough placeholders labelled as such in the UI.
5. **Section 4.2**: `methane_slip_pct_of_fuel_energy` default (0.3%) is a
   plausible modern 2-stroke low-pressure dual-fuel figure but is not
   verified against this vessel class's actual engine (the charter sheet
   implies "174k 2-stroke" -- confirm slip factor against that specific
   engine type, e.g. WinGD X-DF vs MAN ME-GI have different slip profiles).
6. **Section 5.3**: confirm shipping FuelEU as an explicit `NOT_PRICED`
   marker (recommended) rather than attempting a shadow price this phase.
7. **Section 4.4**: `heel_fraction` default for the ballast leg -- this phase
   introduces heel as a real, non-zero inventory for the first time. A
   reasonable industry-typical default (commonly cited range is low single-
   digit percent of cargo capacity) needs to be chosen and flagged as an
   assumption, not derived from the workbook (no heel data exists in it).
