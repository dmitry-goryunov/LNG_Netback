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
- Phase 2 (`docs/PHASE2_PLAN.md`), all of steps 1-9 except Section 3a
  (three-state first-cargo model, independently open, not started):
  `physical.py`'s segment-level mass-balance engine, route builders
  (including queue separation for Asia congestion), `emissions.py`, a
  Decision-page "Physical reconciliation" preview expander, and, as of
  step 8, `decision.py`'s `route_value()` now actually valuing
  `POST_LIFT_DIVERSION`/`PRE_LIFT_CARGO`/`VESSEL_PROGRAMME` from this
  engine instead of `model.strip()`'s static formula --
  `RENEWAL_RATE_SCREEN` still reads `model.strip()` unchanged, by design
  (Section 3). The legacy-equivalence test
  (`tests/test_physical_legacy_equivalence.py`) passes for Europe and both
  Asia cases. Step 4 caught and fixed a real bug: nothing actually
  connected `model.Params` to the engine's `VesselPerformance` (a bare
  `VesselPerformance()`'s defaults only coincidentally matched
  `model.Params()`'s), so editing boil-off rate or fuel-requirement fields
  would have silently done nothing -- exactly the Improvement 3 defect,
  reproduced inside the new engine. Fixed with
  `physical.vessel_performance_from_params()`.

  **Three real, quantified findings from wiring this into actual decision
  values (step 8), all verified against the real workbook:**
  1. **Europe:** the legacy `co2_eu_ets_tonnes` constant's uniform 50%
     ETS-scope factor (vs. the actual EU ETS Directive's 100% for time at
     berth) now costs real money: `full_cargo_value` comes in ~0.04-0.08%
     *lower* than `model.strip()`'s `eu_cargo` (~$18.0k on a
     ~$24-48.6M cargo at this date), and that gap matches the ETS-cost gap
     alone to 0.003% -- fuel/delivered differences are noise, exactly as
     predicted when this was first found (step 3/6). Not a bug, a
     documented, intentional divergence landing in a real number for the
     first time.
  2. **Asia, base case:** unchanged to floating-point noise (rel. 3.7e-8)
     -- confirms the engine is a strict refinement of the legacy formula
     here, not a new assumption set.
  3. **Asia, congested case:** `full_cargo_value` comes in ~1.9% *higher*
     than the legacy flat-rate assumption -- the step-6 queue-rate/
     reliquefaction finding (below) landing in a real number: cheaper
     queue-rate fuel plus BOG surplus that's reliquefied (stays in cargo)
     rather than assumed lost.

  Also caught and fixed while verifying step 8 live, not by inspection:
  the Decision page's waterfall/Sankey charts still read
  `model.waterfall_breakdown()`'s static `model.strip()` columns after
  `route_value()` had already moved on, so the chart's own "matching the
  value above" caption silently went false (off by exactly the Europe ETS
  delta). Fixed with `decision.physical_waterfall_breakdown()`, built on
  the same core function as the route valuation itself so the two cannot
  drift apart again by construction, plus a reconciliation test.

  **Two real, quantified findings from step 6 (queue separation), both
  since resolved:**
  1. Splitting Asia's congestion allowance into proper queue segments
     revealed that, at zero reliquefaction capacity (the engine's original
     default), the laden queue's boil-off exceeds its (lower) demand and
     the surplus is vented outright -- ~148.7 t LNG-equivalent per
     congested round trip, ~3,717 t CO2e as raw methane, larger than the
     rest of that route's combustion emissions combined. Two things closed
     this the same day: `emissions.py` now counts vented gas as raw CH4
     (previously silently zero); and, after asking directly rather than
     guessing, confirmed this vessel class does carry reliquefaction
     capacity and set an informed-estimate default (`physical.
     DEFAULT_RELIQ_CAPACITY_MMBTU_PER_DAY = 3500` MMBtu/day, ~72 t
     LNG/day) that fully absorbs the congested queue's surplus -- verified
     live, zero vented at the new default. The zero-reliq finding stays
     independently reproducible via an explicit test fixture, not deleted.
  2. Net fuel-cost effect of queue separation alone: ~87% of the legacy
     flat-rate total for the congested round trip (8 congestion days move
     from full-sea to lower queue rates) -- this is the fuel-side
     component of finding 3 above once wired into a real value.

  83 tests total (up from 77 through the reliq-capacity fix, then 62
  through step 4), across five files (`test_physical_engine.py`,
  `test_physical_legacy_equivalence.py`, `test_emissions.py`, the
  extended-strip suite, and `test_decision_programme.py` -- 16 tests now,
  up from 10: 3 pre-existing dollar-comparison assertions rewritten to
  check engine-agnostic invariants now that non-screen modes use a
  different formula, duration/feasibility/ranking assertions unchanged, 6
  new tests added), all passing alongside an unaffected legacy 64/64.
  `tests/app_smoke_check.py` re-verified live after step 8: best programme
  still "Europe -> Europe", still 51.8803 used vessel-days -- step 8
  changed the $ values, not (at least at this snapshot) which discrete
  schedule wins.

- Phase 2 Section 3a: the three-state first-cargo model
  (`decision.FirstCargoState`: `ALREADY_LOADED`/
  `PROCUREMENT_COMMITTED_LOADING_REQUIRED`/`FULLY_PRE_LIFT`), letting
  `cost_policy()` represent a cargo that is procured (sunk) but not yet
  loaded (still avoidable) -- a real commercial state the prior two-state
  model (both sunk or both included) couldn't express. Additive: an
  optional `first_cargo_state` parameter on `cost_policy()`/
  `route_value()`/`isolated_route_values()`, and `current_first_cargo_state`
  on `optimise_programme()` (applied to the first/current leg only,
  proven by test), all default to `None`/unset and reproduce prior
  behaviour exactly when omitted. Surfaces as a "Current cargo state"
  radio on the Decision page (isolated post-lift view and vessel-
  programme view), defaulting to "Already loaded." Verified live:
  switching to "Procured, not yet loaded" dropped the programme value by
  exactly `loading * cargo_size` ($210,000 at defaults) and the
  waterfall's sunk-cost add-back bar from +7.01 to +6.95/MMBtu
  (procurement only); "Fully pre-lift" reproduced `PRE_LIFT_CARGO`
  mode's value for the same row exactly, with zero add-back.

  **Bug caught and fixed while wiring this in:** `app.py`'s
  `_decision_waterfall_lines()` took one combined `sunk` bool, assuming
  procurement and loading are always sunk together -- true under the old
  two-state model, false for the new middle state. Fixed to take two
  independent flags before it could ever silently misstate a mixed-state
  decision value in the waterfall.

  93 tests total (10 new), full pytest suite and legacy 64/64 both green.

- **Intrinsic/extrinsic value (JKM vs TTF diversion option) on the
  Forward-strip page** (`spread_option.py`, `docs/SPREAD_OPTION.md` has
  the full design writeup, including a superseded-design section).
  Framing: a cargo is delivered to TTF (Europe) as the base case; the
  option is diverting to JKM (Asia) instead when JKM is higher.
  Intrinsic = `max(JKM - TTF, 0)`; extrinsic = that diversion option's
  time value, priced as a zero-strike Margrabe (1978) exchange option
  (lognormal, since JKM/TTF are strictly positive market prices, unlike
  a netback margin). Two vol/correlation sources, user-toggled:
  "Historical" (realized vol/correlation of JKM vs TTF from the
  workbook's actual daily price history, rolling 60-calendar-day window
  by default) and "Volatilities tab" (the workbook's `volatilities`
  sheet, indexed by tenor) -- `Volatility TTF`/`Volatility JKM`/
  `Correlation TTF/JKM` are exactly what this formula needs, and the
  sheet has all three, so both sources are fully populated for every
  month with no gaps.

  This replaced an initial per-route revenue-vs-cost design (four
  columns, Bachelier pricing, and a real gap in "Volatilities tab" mode
  for Asia) by direct, explicit user correction after reviewing it --
  the `Correlation TTF/JKM` column the user had already added to the
  sheet before that correction didn't fit the per-route design (which
  needed `Correlation JKM/HH` instead) but fits this one exactly; worth
  weighing a user's own data changes as a signal earlier next time.

  19 tests (112 total), full pytest suite and legacy 64/64 both green;
  verified live in-browser in both vol/correlation modes, matching a
  standalone reference computation exactly.

- **Live production crash fixed**: Streamlit Cloud reported a redacted
  `TypeError` at `app.py`'s `spread_option.intrinsic_extrinsic_strip(...,
  window_days=int(window_days))` call. Root cause: `st.number_input()`
  returns `None` (not the widget's `value=` default) while its field is
  momentarily empty mid-edit -- `int(None)` raises exactly a `TypeError`
  at exactly that line. Fixed with an explicit `None` guard falling back
  to `spread_option.DEFAULT_HISTORICAL_WINDOW_DAYS`, plus the
  `intrinsic_extrinsic_strip()`/`month_spread_option()` calls now wrapped
  in a try/except that shows a warning and skips just that section
  instead of crashing the page (the same fail-safe pattern `_safe_strip()`
  already uses elsewhere on this page). `tests/app_smoke_check.py` now
  also navigates to the Forward-strip page (previously untested by the
  smoke check entirely) and asserts it loads clean.

- **Full logic review of everything above, with fixes** (four commits,
  every finding verified with direct numeric evidence before touching
  code):
  1. *JKM tenor misalignment in the spread option -- front-month
     extrinsic overstated ~4x.* The historical vol/correlation used the
     same `c{months_forward}` column for both legs, but the strip prices
     the JKM leg at contract `c{i+2-s}` (delivery L+1). JKM c1 is the
     noisy expiring contract: measured correlation to TTF 0.25 vs the
     correctly-paired 0.80, M1 extrinsic $0.742 -> $0.183/MMBtu. Fixed
     to use the strip's exact contract selection and cross-contract
     correlation; tab mode reads Volatility JKM one delivery tenor
     further out. What the docs had called "a small, unquantified
     misalignment" was neither -- quantify before waving.
  2. *Two lookalike laden-fuel sidebar fields each silently fed only one
     valuation path* ("Residual laden VLSFO" moved legacy only, -$91k;
     "Laden fuel requirement" moved physical only, -$137k = fuel + ETS
     verified to the dollar). `residual_laden_vlsfo` is now DERIVED
     (`model.derived_residual_laden_vlsfo()`: requirement minus
     boil-off x cargo at the reference 3500-MMBtu/d == 86.4-t/d ratio,
     clamped at zero), reproducing the frozen 63.6 default bit-for-bit;
     one knob moves both engines together.
  3. *Two crash classes hardened*: (a) custom Asia RT + pinned laden
     days implying a negative ballast leg -- legacy silently booked a
     phantom fuel credit, physical crashed the Decision page; now a
     sidebar day-count gate plus ValueError containment in the isolated
     branch and the reconciliation expander. (b) The number_input
     None-mid-edit class generalised from the one patched input to all
     ~25 via a `_num_input()` helper with physical bounds.
  4. *Cross-page value-basis disclosure*: the Forward strip (legacy
     basis) now says so and quantifies the deltas vs the Decision page
     (physical basis); the intrinsic/extrinsic section now states it is
     a pure price spread that can legitimately disagree with the
     cost-netted JKM* verdict. Smoke checks extended to all five pages
     (Sensitivities and Hedging were never visited by any test).

  Verified clean in the same review: physical mass-balance and
  reconciliation identities, decision-breakdown algebra (margin x cargo
  == price x delivered - costs, to the cent; no boil-off/bunkers double
  count), ETS phase/FX wiring vs legacy, Margrabe formula vs closed
  form, FirstCargoState scoping.

- **Module-freshness guard for Streamlit hot-reload staleness**
  (`a5df274`): production crashed with a redacted AttributeError because
  a deploy re-executes app.py while imported modules stay cached from
  the pre-deploy process. app.py now carries a sentinel per first-party
  module (its newest app.py-referenced symbol) and reloads all modules
  in dependency order if any is missing; if reload can't resolve it, a
  clear on-page error names the stale modules instead of a redacted
  traceback. Mechanism verified by simulating the exact production
  state. One post-deploy Reboot was still needed for the incident
  itself.

- **Operating-case re-baseline: 17 kn / 1.5 d loading / 1.5 d unloading**
  (`ccb2875`, `3c28dec`; user instruction 16-Jul-2026). Two named
  parameter sets now exist: `Params()` keeps the frozen legacy spec case
  (19.5 kn / 0 / 5) bit-for-bit for the 64/64 suite;
  `model.operating_default_params()` is what the app shows. Speed is a
  first-class sidebar knob for the first time -- sea legs derive from
  distance/speed (Europe RT 27.02 d, Asia base 50.59 d), sea fuel rates
  rescale by the cube law (laden 99.4 t/d at 17 kn -- the vessel sails
  almost entirely on natural boil-off), the congestion queue split
  measures against the speed-consistent base leg, and the legacy ETS
  tonnes knob is now DERIVED from the physical fuel balance
  (`emissions.legacy_uniform_scope_ets_tonnes`, ~3,182 t at 17 kn vs
  the hand-set 4,425.9 t that was only valid at 19.5 kn).
  `Params.loading_days` threads through strip() (arithmetically inert
  at the 0.0 legacy default), the physical route builders (Europe
  loading berth's ETS scope corrected 0.5 -> 0.0 -- a US berth is
  outside EU ETS scope, observable only once the segment had duration)
  and the day-count gate. An AppTest interaction round-trip then caught
  and fixed two live speed-knob bugs: the Asia RT radio silently
  flipping to "Custom" pinned at the old speed's days, and every other
  speed edit being dropped (widget identity hashed from its own
  mutating value=; fixed with a stable key). 10 new tests
  (tests/test_operating_assumptions.py).

- **Derived programme horizon** (`422226b`; user instruction "make it
  54 days, so to fit in 2x to Europe"): a literal 54.0 would exclude
  the second Europe voyage by ~56 minutes (one RT is 27.0196 d), so the
  default horizon is derived -- two Europe round trips plus one
  turnaround gap, rounded up to 0.1 d (54.1 at current settings), with
  an on-page caption stating the derivation. Best programme back to
  "Europe -> Europe" ($74.17M, 54.0392 used days).

- **Turnaround days between programme voyages** (`2771c3d`):
  `optimise_programme(turnaround_days=0.0)` inserts a gap before every
  additional cargo (never before the first or after the last);
  residual_days redefined as ALL non-sailing days in the horizon (gaps
  + tail), priced uniformly by residual_value_per_day so the
  negative-rate idle-hire convention covers gaps too. Byte-identical at
  the 0.0 default (equality-tested). Fourth input on the programme
  page; the derived horizon includes one gap.

- **Non-zero heel** (`3675c5f`): `Params.heel_fraction` (0.0 frozen
  default; 2% operating case, an industry-typical assumption, not a
  vessel spec). New `physical.ShortfallSource.HEEL_THEN_LIQUID_FUEL`
  (per-state mapping; ballast states burn retained heel before buying
  VLSFO, laden states untouched); ballast LNG combustion now counted in
  emissions (the laden-only gate was correct only while ballast
  inventory was always zero); separate "Heel" line in the decision
  breakdown; reconciliation expander passes the heel target. Stated
  honestly: at current prices heel is a net COST (delivered LNG is
  worth more per MMBtu than VLSFO-equivalent) -- realism the zero-heel
  model omitted, since heel is operationally required to keep tanks
  cold. 5 new tests incl. a hand-checked hybrid segment and a zero-heel
  bit-identity guard. 138 pytest total.

- **GitHub Actions CI** (`46c4220`): every push byte-compiles all
  modules and runs the 89 pure tests; the 49 workbook-backed tests
  self-skip (LNG history.xlsx is proprietary and not in the repo) and
  the frozen 64/64 suite remains local-only. Verified green without the
  workbook before enabling.
