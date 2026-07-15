# Implementation status

**Build:** v2.3-phase1  
**Date:** 15 July 2026  
**Governing plan:** `docs/IMPROVEMENT_PLAN.md`, version 1.2

## Implemented in this build

- Added explicit decision modes:
  - renewal-rate screen;
  - post-lift diversion;
  - pre-lift cargo;
  - vessel programme.
- Added mode-specific cost treatment for procurement and loading.
- Added a deterministic discrete one-vessel programme optimiser.
- Current cargo is valued post-lift in programme mode.
- Later cargoes are valued pre-lift using the forward-strip month matching their start date.
- Added full-voyage horizon checks. Fractional or partially completed cargoes are not admitted.
- Added residual vessel-day value.
- Added 52-day base-Asia, 54-day congested-Asia infeasibility and 55-day congested-Asia tests.
- Added explicit FX tenor fields and an M12 extrapolation warning.
- Kept `build_scenarios()` defaulted to `naive` for frozen legacy compatibility.
- Changed the Streamlit risk-page default to roll-aligned scenarios.
- Added a c1..c14 completeness guard for roll-aligned scenarios.
- Added a warning that the legacy 12-cargo VaR portfolio is not a feasible one-vessel programme.
- Added an interim backtest containment fix that skips NG/TTF or JKM roll pairs rather than comparing different physical delivery months.
- Added Streamlit headless smoke checks.

## Deliberately not represented as complete

The following plan items remain future phases:

- unified segment-level cargo, BOG, fuel and emissions engine;
- dynamically derived ETS tonnes;
- queue/steaming separation;
- exact contract and pricing calendars;
- VLSFO and EUA forward curves;
- contract and operational feasibility gates;
- individually dated cash-flow discounting;
- terminal-state valuation beyond residual vessel-days;
- programme-based VaR and full contract-ID backtesting;
- multi-vessel optimisation;
- basis-aware hedge effectiveness.

The programme optimiser in this build uses legacy voyage physics and legacy monthly netback formulae. Its architecture and discrete scheduling tests are valid, but its absolute programme values must be re-baselined after the physical-engine rebuild.

## Validation completed

- Frozen legacy regression suite: **64/64 passed**.
- New decision/programme/risk-containment suite: **14/14 passed**.
- Python compile check: passed.
- Streamlit server startup and HTTP response: passed.
- Streamlit headless decision and risk-page smoke checks: passed.

See `test_results/` for captured output and hashes.

## Progress since v2.3-phase1

Not part of the build recorded above; listed here so this file stays an
accurate index rather than going stale. See individual commit messages for
full detail; `docs/PHASE2_PLAN.md` is the governing plan for the physical
engine work specifically.

- 36-month forward strip (was 12) on the Decision and Forward-strip pages,
  with a multi-tenor FX curve replacing linear extrapolation past 1Y.
  Sensitivities/Hedging/VaR & stress deliberately stay at 12 months.
- Decision-page waterfalls showing programme value build-up, per-cargo
  cost breakdowns, and an explicit sunk-cost add-back bar (procurement and
  loading are shown as real costs, then reversed, not just omitted).
- Two "all months in one place" graphs (any cost/revenue line across the
  full strip; programme value across every possible start month), and a
  fix for a pre-existing alphabetical- vs chronological-sort bug these
  exposed in the two original forward-strip charts.
- Phase 2 (`docs/PHASE2_PLAN.md`) steps 1-6 of 9: `physical.py`'s
  segment-level mass-balance engine, route builders (including queue
  separation for Asia congestion), and `emissions.py`; standalone and not
  yet wired into `model.py`/`decision.py`/`app.py`. The legacy-equivalence
  test (`tests/test_physical_legacy_equivalence.py`) passes for Europe and
  both Asia cases. Step 4 caught and fixed a real bug: nothing actually
  connected `model.Params` to the engine's `VesselPerformance` (a bare
  `VesselPerformance()`'s defaults only coincidentally matched
  `model.Params()`'s), so editing boil-off rate or fuel-requirement fields
  would have silently done nothing -- exactly the Improvement 3 defect,
  reproduced inside the new engine. Fixed with
  `physical.vessel_performance_from_params()`.

  **Two real, quantified findings, both since resolved:**
  1. The legacy `co2_eu_ets_tonnes` constant used a uniform 50% ETS-scope
     factor where the actual EU ETS Directive requires 100% for time at
     berth -- correcting this raises EU-bound ETS cost by ~4.45% (~5.09%
     with methane slip included) once wired into route valuation (step 8,
     not yet done). Not a bug to fix, a documented, intentional divergence.
  2. **Larger finding, from step 6 (queue separation):** splitting Asia's
     congestion allowance into proper queue segments revealed that, at
     zero reliquefaction capacity (the engine's original default), the
     laden queue's boil-off exceeds its (lower) demand and the surplus is
     vented outright -- ~148.7 t LNG-equivalent per congested round trip,
     ~3,717 t CO2e as raw methane, larger than the rest of that route's
     combustion emissions combined. Two things closed this the same day:
     `emissions.py` now counts vented gas as raw CH4 (previously silently
     zero); and, after asking directly rather than guessing, confirmed
     this vessel class does carry reliquefaction capacity and set an
     informed-estimate default (`physical.
     DEFAULT_RELIQ_CAPACITY_MMBTU_PER_DAY = 3500` MMBtu/day, ~72 t
     LNG/day) that fully absorbs the congested queue's surplus -- verified
     live, zero vented at the new default. The zero-reliq finding stays
     independently reproducible via an explicit test fixture, not deleted.

  62 tests through step 4, growing to 77 through the reliq-capacity fix,
  across four files (`test_physical_engine.py`,
  `test_physical_legacy_equivalence.py`, `test_emissions.py`, and the
  extended-strip suite), all passing alongside an unaffected legacy 64/64
  and the unmodified 10/10 programme benchmark suite
  (`tests/test_decision_programme.py`, confirming duration-only
  arithmetic is untouched by queue separation).
