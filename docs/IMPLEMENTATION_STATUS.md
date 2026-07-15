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
