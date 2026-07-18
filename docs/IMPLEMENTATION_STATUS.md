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

- **v2.4.1 risk-equivalence correction (17-Jul-2026):** fixed a live
  operating-default divergence between `risk._vectorized_reprice()` and
  `model.strip()`. The vectorised Europe path had omitted 1.5 loading days
  and loading-port fuel; the Asia path had not subtracted loading time from
  ballast days. `analytic_deltas()` repeated the same omissions for charter
  and VLSFO sensitivities. Four operating-default zero-shock tests and two
  sensitivity-equivalence tests were added. Pre-fix zero-shock P&L was
  +$151,125 Europe, -$48,603 Asia, -$199,728 spread and +$1,813,500 for the
  12-cargo portfolio; all are now below $0.01 absolute. Validation: frozen
  64/64, pytest 144/144 and five-page Streamlit smoke all green. See
  `docs/RISK_EQUIVALENCE_FIX.md`. This corrects base-value equivalence; it
  does not yet migrate risk to the physical engine or a feasible vessel
  programme.

- **Stale `session_state.params` guard (17-Jul-2026):** production crash
  on Streamlit Cloud (`AttributeError` at `p.heel_fraction`, redacted) hit
  by any browser session that stayed open across a deploy adding a new
  `Params` field. The existing module-freshness guard (`app.py`, added
  `a5df274`) reloads stale *module* code but a reload doesn't retroactively
  add fields to an *already-constructed* instance sitting in
  `st.session_state` -- that needed a second, data-side check. `app.py` now
  compares `st.session_state.params` against `dataclasses.fields(model.Params)`
  on every run and resets to `operating_default_params()` (mirroring the
  existing "Reset to operating defaults" button) if any current field is
  missing, with an `st.info` telling the user why their inputs reset.
  `tests/app_smoke_check.py` gained a regression check: a fresh AppTest
  session pre-seeded with a `SimpleNamespace` missing `heel_fraction` (not
  a `Params` instance with the attribute `del`-ed -- fields with plain
  literal defaults leave that default reachable as a *class* attribute, so
  `hasattr` on a same-class instance falls through to it even post-`del`
  and would silently defeat the check; only an unrelated type reproduces a
  real stale instance, which predates the field at the class level too).
  Fully verified with the real workbook (`H:\My Drive\LNG\LNG history.xlsx`,
  SHA-256 `4e51a1e6b9...` matching the v2.4.1 evidence exactly): frozen
  64/64, pytest 144/144, and all six smoke checks including the new one
  green.

- **Hedge-leg VLSFO sizing: third formula copy fixed (17-Jul-2026):** an
  independent logic review of the v2.4.1 release found that
  `risk.europe_hedge_legs()` / `risk.asia_hedge_legs()` carried a *third*
  copy of the route-fuel formula that R1.3/R1.4 didn't reach: the Hedging
  page's VLSFO-swap leg omitted loading-port fuel (Europe and Asia) and
  didn't subtract loading time from Asia ballast days. No valuation, VaR,
  zero-shock or sensitivity impact -- the swap volume is a tonnage sizing
  suggestion that never enters a priced quantity, which is exactly why the
  R1 test battery couldn't see it. Under operating defaults the Europe swap
  was understated by 1.5 d of port fuel and the Asia swap overstated by
  1.5 d of (ballast - port) fuel; at legacy defaults (loading_days = 0) the
  formulas were arithmetically identical, so the frozen suite was blind to
  it too. Fixed to mirror `model.strip()` exactly, with a new
  operating-default regression test pinning both basins' swap tonnage to
  strip's fuel definitions (`test_operating_default_hedge_leg_vlsfo_tonnage_matches_strip_fuel`).
  Validation: pytest 145/145, frozen 64/64, six smoke checks all green
  with the real workbook. The review also re-derived all six analytic
  deltas from strip's formulas by hand and confirmed
  `historical_var()` takes its base from `model.strip()` while scenarios
  go through `_vectorized_reprice()` -- i.e. the zero-shock tests compare
  two independent implementations and are not circular.

- **R6 increment A: canonical cash-flow layer (17-Jul-2026):** first
  increment of the risk rebuild (`docs/R6_RISK_REBUILD_PLAN.md`), built
  by a Sonnet implementation agent and independently reviewed
  line-by-line before commit (delegation workflow: implementer has no
  commit rights; reviewer hand-checks the math against `model.strip()`
  and re-runs the full battery). New pure module `cashflows.py`:
  `RiskFactor` enum, `CashFlow` with factor-TUPLE products (bilinear
  TTF x FX today, (EUA, FX)-ready for R6.5b), `CargoExposure` with
  scalar/vectorised evaluation and `quantity_on()` for delta derivation,
  and `legacy_cargo_cashflows()` decomposing `model.strip()`'s Step 6
  into per-factor terms. 16 new tests (10 pure -> CI, 6 workbook-gated):
  parity vs strip for BOTH parameter sets across all 12 months (worst
  observed error ~1.1e-8 dollars), and all six analytic deltas re-derived
  purely from quantities matching `risk.analytic_deltas()` exactly.
  Purely additive -- no existing file changed by the increment itself;
  CI compileall/count lines updated at review. Validation: pytest
  161/161 with workbook, 10 passed/6 skipped without (CI simulation),
  frozen 64/64, six smoke checks green. Increment B (repricer consumes
  this layer, duplicate formulas deleted) is next.

- **R6 increment B: risk.py consumes the cash-flow layer (18-Jul-2026):**
  the highest-risk increment of the rebuild, same Sonnet-implements /
  independent-review workflow. `cashflows.py` gained the plan sect-2
  two-layer split: `legacy_cargo_quantities(params, load_month_year)` is
  the pure Params-only quantity producer (arithmetic moved verbatim);
  `legacy_cargo_cashflows()` is now a thin D-dependent assembly wrapper
  (snap, phase year, base_prices, month re-tag). `risk.analytic_deltas()`
  and `risk._vectorized_reprice()` both consume the layer: ~40 lines of
  duplicated europe_rt/fixed-fuel/eu_ship/as_ship/margin arithmetic
  DELETED -- that formula now exists exactly once (`cashflows.py`) plus
  the untouched `model.strip()` ground truth. Scenario price preparation
  (exp-of-returns, FX interpolation, JKM tenor mapping) unchanged
  byte-for-byte per the plan boundary; `jkm_star`'s `asia_cost_exbo`
  intermediate is recovered by exact algebraic rearrangement from the
  evaluated `asia_cargo` (verified against bumped-table `model.strip()`
  oracles to ~1e-14, including a verdict-flip case); `europe_rt` is read
  off the CHARTER quantity rather than re-derived. Minimal finite-value
  guards added at the evaluation boundary (R2 remains unclaimed). One
  new parametrized test: batched repricer vs per-scenario scalar
  `CargoExposure.value()` loop under non-zero random shocks (exact
  agreement). Review verified independently: full diff read, frozen
  64/64 (the gate that matters here), pytest 163/163 with workbook,
  99/64-skip CI simulation, six smoke checks, backtest runtime same
  order of magnitude (isolated repricer ~2.5x slower pending the
  increment-C quantities cache; dominated in practice by untouched
  scenario-building/pandas costs).

- **R6 increment C: physical basis for VaR/stress (18-Jul-2026):** same
  Sonnet-implements / independent-review workflow (this increment
  survived a mid-run session-limit interruption and was resumed from
  transcript). `cashflows.py` gains the physical counterpart:
  `physical_cargo_quantities(params, route, year, first_cargo_state)`
  decomposes `decision.py`'s physical route valuation onto the factor
  set from the engine's own ledger (charter = `total_days`, bunkers =
  net purchased `total_liquid_fuel_tonnes` post-reliquefaction/heel
  substitution, ETS = per-segment-scope CO2e, heel as its own
  negative cash flow at destination price), pinned against
  `decision.route_value().incremental_value` across a 144-case sweep
  (2 params x 12 months x 2 routes x 3 states) at worst ~2.2e-8.
  Headline: **exposure follows first-cargo state** -- sunk procurement
  zeroes the HH quantity (plus liquefaction/pipeline constant; sunk
  loading zeroes the loading fee), with a semantic test proving sunk
  procurement genuinely narrows VaR. Both quantity producers now sit
  behind a bounded, field-value-keyed (never identity) LRU cache with
  hit/miss/in-place-mutation/year-boundary-poison tests. `risk.py`
  extracts `_prepare_scenario_price_arrays()` (shared price prep, no
  second drifting copy -- extraction pinned by a dedicated test), adds
  `_vectorized_reprice_physical()` and `historical_var_physical()`
  (single/spread only; 12cargo stays legacy-basis-only per plan
  sect 8.3), and `run_stress_tests()` gains an additive `basis=` param
  whose legacy default is bit-compatible (pinned to captured
  pre-increment constants). VaR page gets the "Value basis" radio
  (legacy default), a first-cargo-state selector, basis captions, and
  honest disabling of backtest/overlapping-10d under the physical
  basis. Legacy-vs-physical base gaps characterised, not reconciled:
  legacy params Europe -0.075% (per-segment ETS scope), operating
  Europe -1.70% / Asia -1.08% (heel + ETS), legacy Asia ~$1.
  **Correction to the increment-B note above:** C's measurements show
  the quantity cache is performance-neutral at backtest level (~0.99s
  vs ~0.98s uncached; B's 3.47s baseline was not reproducible, ~1.5s
  measured pre-cache) -- the isolated repricer cost lives in
  `value_matrix`'s per-CashFlow Python evaluation, not in quantity
  building, so the cache (still structurally required by the plan and
  fully tested) does not recover it. Validation: pytest 274/274 with
  workbook (163 + 111 new), frozen 64/64, CI simulation 162/112-skip,
  seven smoke checks (new basis-toggle check added) -- all
  independently re-run at review.

- **R6 increment D: committed-programme VaR (18-Jul-2026):** the
  feasible optimiser plan replaces the infeasible 12-cargo strip as the
  physical basis's flagship portfolio. `risk.build_committed_programme()`
  reproduces the Decision page's programme defaults exactly (shared
  module constants app.py reads back; horizon 54.1 d = two Europe RTs at
  operating defaults, pinned against the smoke test's independent
  Decision-page figure). One physical exposure per leg using
  `ProgrammeLeg.month_index` verbatim (already the strip's own 0-based
  convention -- no re-derivation); first leg takes the page's
  first-cargo-state selector, later legs are always fully exposed;
  hold-plan-fixed under scenarios (no per-scenario re-optimisation --
  R4-deferred decision, disclosed). Price-independent residual days are
  excluded (cancel exactly in scen - base); a tail leg landing beyond
  the 12-month scenario window (only reachable at month_index=11) is
  dropped from base AND scenarios together, proven exact, with a
  disclosure caption. Sum-of-legs identity vs independent single-cargo
  calls: ~7.45e-9 over 500 real scenarios. Programme VaR95 at operating
  defaults ~-$5.88M vs single-Europe -$3.01M (SD ratio ~1.94x --
  coherent near-perfect adjacent-month correlation). `VarResult` gains
  `basis` metadata. Implementer also caught its own stale-widget-
  reference bug in the smoke test (the exact gotcha the file documents)
  and fixed the now-contradictory 12-cargo warning text. Validation
  (independently re-run at review): pytest 293/293 with workbook, frozen
  64/64, CI simulation 164/129-skip, nine smoke checks.

- **R6 increment E: new risk factors, honestly (18-Jul-2026):** same
  Sonnet-implements / Opus-reviews workflow (this increment's implementer
  was interrupted three times by session/model limits mid-run, then
  completed cleanly on the fourth; its final self-report was lost to the
  last interruption, so the review worked from the code alone). Three
  strands: **(E.1) charter** cannot join the 500-day daily
  joint-historical scenario set (the series is ~459 weekly rows), so it
  is NOT added to `ScenarioSet` -- instead `run_stress_tests()` gains
  three deterministic charter rows (+$25k/day, -$25k/day, +50%) on BOTH
  bases, and an OPTIONAL independent overlay (`apply_charter_overlay()`,
  off by default) draws seeded delta-normal shocks at a vol calibrated
  from the weekly series and scaled DOWN to the 1-day horizon
  (`weekly_vol / sqrt(business_days_per_week)` -- the honest direction:
  scale the magnitude, never fabricate a daily series by forward-fill),
  added as an independent (zero-assumed-correlation) P&L overlay with
  both assumptions printed and the result labelled a model overlay, not
  historical simulation. **(E.2) VLSFO/EUA** get data-gated loaders
  (`data.load_vlsfo()`/`load_eua()`, tolerate-absence like
  `load_volatilities`; `CurveTables.vlsfo`/`.eua` default None) and a
  live-factor code path wired now and tested against synthetic in-memory
  tables (no sheet exists yet); the EUA path additionally re-splits the
  ETS cash flow from FX-linear-folded to `(EUA, FX)` bilinear -- PHYSICAL
  BASIS ONLY and only when `eua_live=True`, since the legacy basis must
  stay byte-identical to `model.strip()`'s hardcoded `eua_price`. The
  default `eua_live=False` path is byte-identical to increment D and the
  re-split is value-preserving at base prices. **(E.3)** a
  `factor_coverage_line()` states exactly which factors are stochastic
  vs deterministic each run (basis risk excluded-with-disclosure, no
  silent proxy). Validation (independently re-run at review): pytest
  353/353 with workbook (293 + 60 new), frozen 64/64, CI simulation
  181/172-skip, twelve smoke checks. Existing stress-row pins extended
  additively (six pre-existing rows byte-unchanged, three charter rows
  pinned), no test weakened.
