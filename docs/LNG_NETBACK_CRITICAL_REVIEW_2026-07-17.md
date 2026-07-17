# LNG Forward Netback Project: Critical Logic and Improvement Review

**Review date:** 17 July 2026

**Project reviewed:** current authoritative `LNG_Netback` Drive folder

**Workbook reviewed:** current `LNG history.xlsx` from the project data source

**Review approach:** source-code inspection, documentation cross-check, execution of all available tests, targeted adversarial calculations, and comparison against the master improvement plan

---

## 1. Executive conclusion

The project has progressed well beyond the original v2.2 renewal-rate screen.

The current deterministic decision path now has:

- explicit post-lift, pre-lift and programme modes;
- three current-cargo states;
- a segment-level physical ledger;
- dynamic cargo loss, fuel and EU ETS calculations;
- queue separation;
- heel treatment;
- a discrete one-vessel programme;
- a 36-month forward strip;
- a JKM-versus-TTF spread-option display;
- strong legacy and unit-test coverage.

Those are real improvements, not cosmetic changes.

However, the project is **not yet a production-grade commercial decision engine**. The most important result of this review is:

> **The live risk repricer does not reproduce the base value under a zero market shock when the application uses its current 17-knot / 1.5-day loading operating defaults.**

That is a basic valuation identity failure. It shifts reported scenario P&L and therefore shifts VaR and expected shortfall.

The deterministic decision page is the strongest part of the application. The risk, hedge and production-governance layers remain materially behind it.

My overall classification is:

| Area | Assessment |
|---|---|
| Legacy renewal-rate screen | Stable and well protected |
| Deterministic route valuation | Credible analytical prototype |
| Physical engine | Good core design, insufficient input/state validation |
| One-vessel programme | Useful prototype, not operational scheduling |
| Spread option | Useful price-spread indicator, not a full diversion option |
| Sensitivities | Partly inconsistent under current operating defaults |
| VaR / stress / backtest | Legacy and currently affected by a zero-shock defect |
| Hedging | Model-internal ratio check, not physical hedge effectiveness |
| Data governance | Functional but too permissive |
| Production controls / audit | Incomplete |

The next release should be a **correctness and governance patch**, not another feature release.

> **Editor's note (applied 17-Jul-2026):** the CRITICAL 1 zero-shock defect described below was fixed and independently re-verified the same day. See `docs/RISK_EQUIVALENCE_FIX.md` and the `v2.4.1-risk-equivalence` tag. The rest of this review — including all HIGH/MEDIUM findings and the phase-by-phase assessment — is preserved verbatim as a point-in-time record and remains open work; see `docs/LNG_NETBACK_PROGRESS_TRACEABILITY_2026-07-17.md` for current status.

---

## 2. Evidence and validation performed

### 2.1 Full test execution

I ran the current project rather than relying on the README's test claims.

Results:

```text
Python byte compilation: PASS
Frozen legacy suite:     64/64 PASS
Current pytest suite:    138/138 PASS
Streamlit smoke test:    PASS on all five pages
```

The five smoke-tested pages were:

```text
Decision
Forward strip
Sensitivities
Hedging
VaR & stress
```

These results confirm that the current source tree is executable and internally stable under its existing tests.

They do **not** prove that every current operating path is economically consistent. The most material defect found below sits outside the parameter combination used by the frozen zero-shock regression.

### 2.2 Current workbook identity

The workbook used in this review had:

```text
SHA-256: 4e51a1e6b92d004836c8cfe18f118006686ef19ad2b9813c77c53035591c5497
Size: 6,463,970 bytes
```

This differs from the frozen baseline record:

```text
SHA-256: 27249ec6778a61974690bc63ceb4530c508614c88a3f8c5df59a061b0e4abad5
Size: 6,461,805 bytes
```

The current workbook can still pass the frozen 64 checks because the changed content may sit outside those regression fixtures, for example in the volatility sheet. Nevertheless, current recommendations are no longer reproducible solely from the old baseline hash.

A new data snapshot record is required for the current build.

### 2.3 Adversarial checks added during this review

I tested identities not covered by the current suite:

- zero-shock risk revaluation under operating defaults;
- analytic versus finite-difference sensitivities under operating defaults;
- negative and excessive physical cargo loss;
- multiple discharge segments;
- invalid voyage segment ordering;
- invalid option volatilities and correlations;
- 36-month strip completeness on historical dates;
- EU ETS gas-scope behaviour for 2024, 2025 and 2026.

These checks produced the critical findings below.

---

# 3. Severity-ranked findings

## CRITICAL 1: Risk revaluation fails the zero-shock identity under the current operating case

### What should happen

For every portfolio:

```text
scenario shocks = 0
therefore scenario value = base value
therefore P&L = 0
```

This is the minimum equivalence test for a risk repricer.

### What actually happens

Using:

```text
Curve date: 8 July 2026
Operating speed: 17 knots
Loading time: 1.5 days
Unloading time: 1.5 days
```

the current risk engine gives:

| Portfolio | Zero-shock P&L |
|---|---:|
| Single Europe cargo | **+$151,125** |
| Single Asia cargo | **-$48,603** |
| Asia-minus-Europe diversion spread | **-$199,728** |
| Legacy 12-cargo portfolio | **+$1,813,500** |

This is not rounding noise.

### Root cause

`risk._vectorized_reprice()` duplicates the legacy route formula and has not been fully updated for `loading_days`.

For Europe it calculates:

```python
europe_rt = eu_laden + eu_ballast + eu_port
```

rather than:

```python
europe_rt = eu_laden + eu_ballast + eu_port + loading_days
```

It also omits loading-port fuel.

For Asia it derives ballast days as:

```python
asia_ballast = asia_rt - asia_laden - asia_port
```

rather than subtracting loading time as well.

The deterministic `model.strip()` path does include loading time. The two paths therefore diverge at the app's current defaults.

### Further evidence

The displayed analytic-versus-finite-difference sensitivity table also disagrees under operating defaults:

| Shock | Analytic | Finite difference | Error |
|---|---:|---:|---:|
| Europe charter +$10,000/day | -$255,196 | -$270,196 | **+$15,000** |
| Europe VLSFO +$50/t | -$61,398 | -$63,273 | **+$1,875** |
| Asia VLSFO +$50/t | -$126,264 | -$121,679 | **-$4,585** |

The $15,000 charter error is exactly 1.5 loading days × $10,000/day.

### Consequence

The risk distribution is shifted before any market shock is applied. VaR and expected shortfall are therefore misstated.

The 12-cargo portfolio is shifted favourably by $1.8135 million in the current snapshot, which can understate downside risk materially.

### Required action

This must be fixed before relying on any VaR, stress or hedge-effectiveness output.

Add mandatory tests:

```python
test_zero_shock_single_europe_operating_defaults_is_zero
test_zero_shock_single_asia_operating_defaults_is_zero
test_zero_shock_spread_operating_defaults_is_zero
test_zero_shock_12cargo_operating_defaults_is_zero
test_analytic_deltas_equal_finite_difference_operating_defaults
```

The proper long-term fix is not another patch to the duplicate formula. Risk should consume a canonical cash-flow representation generated by the deterministic engine.

---

## HIGH 1: The physical engine accepts invalid ledgers instead of rejecting them

The standard Europe and Asia route builders reconcile correctly. The problem is the public `run_voyage()` contract, which is intended to support future routes and operational states.

### Invalid case A: excessive other loss

Input:

```text
Loaded: 100 MMBtu
Other loss: 150 MMBtu
```

Current result:

```text
Delivered: 0
Reconciliation error: -50 MMBtu
No exception
```

### Invalid case B: negative other loss

Input:

```text
Loaded: 100 MMBtu
Other loss: -10 MMBtu
```

Current result:

```text
Delivered: 110 MMBtu
No exception
```

### Invalid case C: multiple discharges

Two discharge segments are accepted. The second discharge overwrites the first cargo delivery state and returns:

```text
Reconciliation error: 100 MMBtu
No exception
```

### Invalid case D: impossible segment order

A voyage containing:

```text
ballast before discharge
discharge
laden sea after discharge
```

is accepted.

### Why this matters

Current route builders happen to construct valid sequences. Phase 4 intends to add new routes, terminals and operational alternatives. Without a state machine and hard reconciliation checks, future route configuration can create plausible-looking but physically invalid valuations.

### Required action

`run_voyage()` should enforce:

1. `other_loss_mmbtu >= 0`;
2. other loss plus heel cannot exceed available cargo;
3. exactly one discharge;
4. no ballast state before discharge;
5. no laden state after discharge;
6. reconciliation within tolerance before returning;
7. terminal-heel reconciliation within tolerance;
8. missing per-state shortfall configuration raises rather than silently defaults.

Add negative tests for every rule.

---

## HIGH 2: The decision model still lacks the commercial outside option

The application now distinguishes:

- already loaded;
- procured but not loaded;
- fully pre-lift.

That is a strong improvement.

But a fully pre-lift cargo still has only Europe and Asia as actions. It cannot choose:

- do not lift;
- cancel;
- release the slot;
- sell FOB;
- resell the cargo;
- defer;
- float;
- sublet the vessel.

The programme's first cargo is mandatory. If both routes destroy value, the optimiser still chooses the less bad route.

The Forward-strip page warns that the verdict is relative. The Decision page needs the same economic protection in executable form, not only prose.

### Consequence

`PRE_LIFT_CARGO` is not yet a true pre-lift decision mode. It is a route-choice mode conditional on lifting.

### Required action

Create explicit actions:

```text
LIFT_TO_EUROPE
LIFT_TO_ASIA
CANCEL_OR_NO_LIFT
FOB_RESALE
FLOAT
DEFER
```

Every action needs:

- eligibility;
- incremental cash flows;
- terminal state;
- audit explanation.

Until then, label pre-lift outputs "Best destination conditional on lifting", not "Recommended action".

---

## HIGH 3: No contractual or operational feasibility gate precedes ranking

The optimiser supports only a manual `route_feasible` Boolean mapping. The application does not implement executable gates for:

- destination rights;
- buyer consent;
- laycan;
- delivery window;
- terminal slot;
- Panama slot;
- vessel compatibility;
- LNG quality;
- sanctions;
- credit;
- weather;
- maintenance;
- redelivery obligations.

The governing plan explicitly requires feasibility before value.

### Consequence

The model may rank an unavailable route as best.

### Required action

Add a `FeasibilityResult` with:

```text
PASS
FAIL
CONDITIONAL
UNKNOWN
```

and named reasons. `FAIL` routes must be excluded. `UNKNOWN` routes must never be presented as unqualified recommendations.

---

## HIGH 4: EU ETS gas treatment is not year-accurate

The physical engine is an improvement because it calculates CO2, methane and N2O by segment.

However, the economic ETS charge multiplies total covered CO2e by one phase factor based on the load month's year.

This creates two issues.

### 2024 and 2025

EU ETS maritime included only CO2 in the surrender obligation in 2024 and 2025. Methane and N2O enter ETS scope from 2026.

The current model charges its methane-slip CO2e in 2024 and 2025.

At current defaults, the overstatement was approximately:

| Load year | Excess ETS cost from methane inclusion |
|---|---:|
| 2024 | $1,966 per Europe cargo |
| 2025 | $3,730 per Europe cargo |

The amount is not large at the placeholder slip rate, but the logic is wrong.

### 2026 onward

Methane and N2O are in scope, but:

```text
Methane slip = unverified 0.3% placeholder
N2O factor = 0
```

N2O is therefore economically omitted despite being in ETS scope.

### Year crossing

A December load can have voyage and port segments in the following calendar year. The model assigns one load-month phase and gas scope to the entire route instead of dating segments.

### Required action

Emissions results should retain physical gases separately. ETS valuation should apply:

- gas eligibility by compliance year;
- phase-in by year;
- segment date;
- legal geographic scope;
- contractual payer.

Do not convert to one undifferentiated CO2e amount before deciding legal scope.

---

## HIGH 5: Programme value is not a fully comparable programme NPV

The programme is correctly discrete and later cargoes use later forward months. That is a major improvement.

It is still incomplete in several respects:

- no dated cash-flow objects;
- no payment timing;
- no discounting by cash-flow date;
- no vessel location in terminal state;
- terminal heel is not valued;
- no next-employment value;
- no redelivery penalty;
- no maintenance state;
- no cargo laycans;
- no terminal or canal schedules.

Residual value is a flat user-entered $/day adjustment.

### Misleading day labels

`ProgrammePlan.used_days` sums voyage durations only.

Turnaround days are excluded from `used_days` and included within `residual_days`, even though the vessel is occupied by the programme during the turnaround gap.

The UI therefore needs three separate metrics:

```text
Voyage days
Turnaround / positioning days
Uncommitted tail days
```

A single "used vessel-days" metric can mislead.

### Required action

Introduce:

```python
CashFlow
CargoOpportunity
VesselState
TerminalState
ProgrammeCargoResult
```

and compare programmes over matched terminal states.

---

## HIGH 6: The risk and hedge architecture remains a separate legacy model

Even after fixing the loading-day defect, `risk.py` remains an independently maintained copy of route economics.

It does not use the physical decision engine for:

- delivered cargo;
- segment fuel;
- queue consumption;
- heel;
- methane;
- dynamic ETS;
- later programme state.

It also omits material factors:

- VLSFO market risk;
- EUA risk;
- NWE physical basis;
- JKM physical basis;
- terminal access;
- Panama cost and delay;
- FuelEU;
- operational feasibility.

The current 12-cargo VaR portfolio is explicitly infeasible for one vessel.

Hedge effectiveness is primarily a check that indices reverse the same model formula. The UI states this honestly, but the result is not physical hedge effectiveness.

### Required action

Fix the zero-shock defect immediately, then implement the plan's canonical architecture:

```text
cached physical/contractual layer
canonical price-dependent cash flows
vectorised scenario repricer generated from those cash flows
```

---

## MEDIUM 1: The Margrabe display is not a full diversion-option value

The implemented formula is mathematically recognisable as a zero-strike exchange option on JKM and TTF.

The UI now discloses that it is a pure price spread. That disclosure is good.

It still has important limitations:

1. JKM and TTF are different delivery-month exposures in the strip.
2. Freight, boil-off, Panama, ETS, terminal cost and basis are absent.
3. There is no exercise notice, route feasibility or vessel availability.
4. `T` is the 15th of the load month, not a contractual exercise date.
5. Forward prices are used without an explicit discount factor.
6. A 60-calendar-day realised volatility is projected across a 36-month strip.
7. Workbook volatilities and correlations are flat placeholder values: volatility 0.6 at every tenor; correlation 0.5 at every tenor.

Therefore it should be labelled "JKM–TTF price-spread option indicator", not a standalone commercial diversion-option value.

### Input validation defect

The option function accepts:

- negative volatility;
- correlation above 1;
- correlation below -1.

For example, a correlation of 1.5 is silently clamped through a negative variance to a zero-volatility intrinsic result.

Required validation:

```text
volatility >= 0
-1 <= correlation <= 1
finite inputs only
```

---

## MEDIUM 2: The 36-month strip silently contains missing prices on some historical dates

The master date list requires only c1–c13 completeness.

The Decision and Forward-strip pages request 36 months.

In the current workbook:

```text
HH c1-c37 complete:  2,335 / 2,335 master dates
TTF c1-c37 complete: 2,335 / 2,335 master dates
JKM c1-c37 complete: 1,348 / 2,335 master dates
```

Examples:

```text
27-Jul-2017: seven JKM NaNs in the 36-month strip
15-Nov-2022: 24 JKM NaNs
09-Jan-2023: 26 JKM NaNs
```

`model.strip(..., n_months=36)` returns successfully with these NaNs. The app's 12-month fallback is not triggered because no exception occurs.

### Required action

The selected horizon must have its own completeness gate.

Options:

- restrict the displayed horizon to the complete common tenor;
- warn and shade unavailable months;
- block programme optimisation where required prices are missing.

Never allow NaN values to enter ranking.

---

## MEDIUM 3: Data validation is too permissive

### Python assertions

Hard workbook checks use `assert`.

Running Python with optimisation (`python -O`) disables them.

Use explicit exceptions.

### Silent optional-sheet failure

`load_all()` catches every exception from US netbacks, US transport, and volatilities, and silently returns `None`.

A malformed volatility sheet is operationally different from an absent sheet and should produce a visible warning with the exception.

### Environment-variable capture

The environment-variable path is read when `data.py` is imported, not each time `default_data_path()` runs.

This is minor in ordinary deployment but makes dynamic configuration brittle.

### Missing schema/version control

The workbook has no explicit schema version, unit registry or validated column lineage.

---

## MEDIUM 4: Key vessel assumptions are decision-critical placeholders

Current operating assumptions include:

- 17-knot service speed;
- cube-law scaling of the entire sea fuel rate;
- 2% heel;
- 3,500 MMBtu/day reliquefaction;
- 40/35 t/day queue demand;
- 0.3% methane slip;
- zero N2O;
- fixed route distances;
- fixed one-day Panama transit embedded in sea time.

The application discloses several of these. Disclosure is not calibration.

### Specific logic issues

- The cube law is applied to the entire sea fuel rate. Hotel and auxiliary load should not scale with speed cubed.
- Panama transit remains embedded in sea duration and sea demand. The engine defines a canal state but the route builder does not use it.
- Reliquefaction materially changes congested-Asia economics, yet its value is an informed estimate rather than vessel data.
- Heel is charged at destination sale price and terminal heel receives no residual credit.
- Queue demand is a placeholder.

### Required action

Create a versioned vessel profile with:

```text
source
effective date
engine type
speed-consumption curve
auxiliary demand
BOR
reliquefaction capacity
methane slip
N2O factor
heel policy
```

Run low/base/high cases until actual vessel data are available.

---

## MEDIUM 5: Documentation and version labels have drifted

Examples:

- `IMPLEMENTATION_STATUS.md` still identifies the build as `v2.3-phase1`, then records substantial later work.
- `decision.py` says the programme still uses legacy physical economics, although it now uses the physical engine.
- `emissions.py` says it is not wired into decision/app, although it is.
- `MODEL_ASSUMPTIONS.md` still says physical BOG/fuel/ETS remain legacy simplified and M12 may extrapolate beyond 1Y, despite the completed physical path and multi-tenor FX.
- App caveats say CH4 slip is not modelled, while the Decision page applies a 0.3% placeholder.

This is more than editorial untidiness. In a governed valuation model, contradictory documentation makes it unclear which statement is authoritative.

### Required action

Create one current release identifier and regenerate README, IMPLEMENTATION_STATUS, MODEL_ASSUMPTIONS, CHANGELOG, VALIDATION_REPORT and DATA_SNAPSHOT from the same release.

---

## MEDIUM 6: Architecture is becoming difficult to govern

Current sizes include:

```text
app.py       1,571 lines
risk.py        922 lines
decision.py    609 lines
model.py       575 lines
physical.py    522 lines
```

`app.py` mixes state management, input validation, economics, plotting, page routing, hot-reload controls and error containment.

The plan proposed separate pages and engine modules. That refactor has not happened.

The Drive source also contains development artefacts such as `.git`, cache directories and compiled caches. These should not sit in the deployment/source package.

---

# 4. Phase-by-phase assessment against the improvement plan

## Phase 0: Freeze, de-duplicate and protect the baseline

### Completed

- `v2.2-renewal-rate` tag recorded.
- Baseline commit recorded.
- Frozen workbook hash recorded.
- 64/64 output preserved.
- Current legacy suite still passes.
- Roll-aligned method is the VaR-page default.
- Warning exists for the infeasible 12-cargo portfolio.
- CI exists for the pure-test subset.

### Incomplete

- no synthetic/anonymised workbook fixture;
- no current-build workbook hash after the workbook changed;
- CI does not run the workbook-backed tests;
- CI does not run the frozen 64 suite;
- deployment artefact hygiene is weak;
- old copies and naming collisions need formal archival status.

### Verdict

**Mostly complete, not closed.**

---

## Phase 1: Decision-state and valuation architecture

### Completed

- decision modes;
- cost policy;
- three first-cargo states;
- route-level full value and incremental value;
- UI explanation of sunk procurement/loading;
- decision-mode tests.

### Incomplete

- no explicit dated cash flows;
- no discounting by payment date;
- no contractual payer fields;
- no outside actions;
- no feasibility object;
- no complete terminal state;
- programme does not store full-P&L and sunk adjustment as first-class aggregate fields.

### Verdict

**Materially implemented, structurally incomplete.**

---

## Phase 2: Unified physical and emissions engine

### Completed

- segment ledger;
- physical cargo balance;
- queue separation;
- BOG burn, surplus, reliquefaction and venting;
- heel and ballast logic;
- liquid fuel;
- segment emissions;
- dynamic ETS in non-screen decision values;
- physical reconciliation UI;
- extensive tests.

### Incomplete or defective

- canal remains embedded in sea time;
- public voyage API accepts invalid sequences and losses;
- methane/N2O assumptions not production-ready;
- year-specific ETS gas scope is wrong for 2024–25;
- FuelEU not priced;
- terminal heel not valued;
- input assumptions not tied to a verified vessel profile.

### Verdict

**Strong prototype implementation, not compliance-grade or route-generic.**

Calling the phase "complete" is reasonable only under the narrow internal Phase 2 scope, not under the master project's production definition of done.

---

## Phase 3: Discrete vessel-programme optimiser

### Completed

- whole voyages only;
- one vessel;
- later cargoes use later forward months;
- turnaround input;
- residual-day value;
- base/congested Asia cases;
- programme schedule and ranking;
- physical route values.

### Incomplete

- current cargo mandatory;
- no actual cargo opportunities;
- no laycans or slots;
- no terminal state;
- no dated cash flows;
- no multi-vessel extension;
- turnaround counted as residual rather than occupied programme time;
- no programme-based risk.

### Verdict

**Useful deterministic scheduling prototype, not an operational optimiser.**

---

## Phase 4: Contract calendar, feasibility and route expansion

### Completed

- screening continuation mapping;
- 36-month price strip;
- longer FX tenors.

### Not completed

- exchange-expiry calendars;
- contractual quotation windows;
- laycans;
- discharge windows;
- terminal and canal schedules;
- rights and consent;
- quality, sanctions and credit;
- route configuration;
- VLSFO forward curve;
- EUA forward curve;
- additional routes.

### Verdict

**Largely not implemented.**

---

## Phase 5: Risk and hedging rebuild

### Completed

- roll-aligned scenario option;
- temporary roll-pair skipping;
- historical VaR/stress/backtest pages;
- model-internal hedge legs;
- disclosure of major exclusions.

### Not completed

- common valuation representation;
- physical programme VaR;
- contract-ID histories;
- current-factor set;
- basis risk;
- execution/lot risk;
- expected-shortfall uncertainty;
- full roll-safe backtesting;
- programme feasibility.

### Live defect

Zero-shock P&L is non-zero under the application's current operating defaults.

### Verdict

**Not rebuilt. Legacy module retained, with a current correctness defect.**

---

## Phase 6: Production controls and interface refinement

### Completed

- useful captions and warnings;
- five-page smoke check;
- some visible assumption disclosure;
- CI for pure tests.

### Not completed

- downloadable audit package;
- model/data/version bundle;
- saved cases;
- approvals;
- sign-off;
- access control;
- controlled deployment;
- immutable release packaging;
- full provenance;
- page/module refactor.

### Verdict

**Early-stage only.**

---

# 5. Module-by-module logic review

## `data.py`

### Strong points

- robust date parsing;
- explicit FX x10 correction;
- longer FX tenor support;
- source snapping;
- stale-data warning;
- pure headless loader.

### Weak points

- hard validation uses `assert`;
- optional sheets fail silently;
- c13 master-date gate conflicts with c36 UI;
- no finite-price gate by requested horizon;
- no workbook schema version;
- current source hash not propagated into outputs.

---

## `model.py`

### Strong points

- frozen legacy defaults separated from operating defaults;
- route days derived from distance and speed;
- speed-dependent fuel;
- loading time threaded through legacy formula;
- 36-month FX interpolation;
- stable legacy regression.

### Weak points

- entire sea consumption scales by cube law;
- static VLSFO and EUA;
- screening calendar rather than contract calendar;
- hardcoded Europe/Asia routes;
- no dated cash flows;
- legacy logic remains a large production-visible code path.

---

## `physical.py`

### Strong points

- clear segment results;
- BOG/demand/reliquefaction/shortfall split;
- ballast heel isolated from delivered cargo;
- queue demand separated from sea demand;
- route builders preserve legacy equivalence where intended;
- useful reconciliation properties.

### Weak points

- no voyage state machine;
- invalid other losses accepted;
- no final reconciliation exception;
- multiple discharge accepted;
- missing shortfall mapping silently defaults;
- canal state unused in route builder;
- several rates are placeholders.

---

## `emissions.py`

### Strong points

- fuel-derived rather than static emissions;
- vented methane separated from combustion slip;
- per-segment ETS scope;
- payer/FuelEU uncertainty made explicit.

### Weak points

- stale module docstring;
- methane slip unverified;
- N2O zero;
- GWP values ungoverned;
- vented LNG treated as pure methane;
- ETS gas eligibility not year-specific;
- one load-year phase applied to whole voyage.

---

## `decision.py`

### Strong points

- clear decision modes;
- three first-cargo states;
- procurement/loading treatment centralised;
- physical route breakdown and waterfall share one calculation;
- later cargoes use later forward months;
- no fractional voyages.

### Weak points

- stale top docstring;
- RouteValue lacks explicit sunk-cost fields and cash flows;
- ProgrammeLeg stores one ambiguous `value`;
- first cargo is mandatory;
- no outside actions;
- no feasibility objects;
- no terminal state;
- heel has no terminal value;
- residual $/day is too blunt.

---

## `spread_option.py`

### Strong points

- correct closed-form structure for a simplified exchange option;
- corrected JKM/TTF tenor alignment;
- clear distinction from JKM* in the UI;
- strong dedicated tests;
- efficient implementation.

### Weak points

- not a full diversion option;
- no explicit discounting;
- different delivery months;
- approximate exercise date;
- flat placeholder term vol/correlation;
- no range validation for vol/correlation;
- FX and route costs excluded.

---

## `risk.py`

### Strong points

- fast vectorised scenarios;
- separate scenario construction;
- roll-aligned option;
- frozen fixture compatibility;
- disclosures for infeasible portfolio and model-internal hedging.

### Weak points

- live zero-shock defect;
- duplicate economics;
- no physical engine;
- only 12 months;
- omitted material factors;
- infeasible default portfolio;
- approximate roll rules;
- no uncertainty around VaR/ES;
- hedge results exclude basis and execution.

This is the weakest core module relative to the rest of the project.

---

## `app.py`

### Strong points

- comprehensive interface;
- clear distinction between legacy and physical value bases;
- strong error containment;
- first-cargo selector;
- physical reconciliation display;
- useful programme audit tables;
- all pages smoke-tested.

### Weak points

- 1,571-line monolith;
- inconsistent caveats;
- broad exception handlers can conceal defects;
- "programme value" remains the headline rather than separate programme decision value/full P&L;
- "used vessel-days" excludes turnaround;
- no audit export;
- no build/data identity displayed;
- hot-reload sentinel mechanism is brittle.

---

# 6. Testing review

## What is good

The project's testing is materially better than a typical analytical prototype.

It protects:

- frozen legacy outputs;
- physical conservation on normal routes;
- decision-state add-backs;
- queue logic;
- heel logic;
- spread-option closed form;
- tenor alignment;
- speed assumptions;
- UI page startup.

The project history also shows that tests and live checks have caught genuine bugs.

## What remains missing

Mandatory additions:

```text
risk zero shock under operating defaults
analytic vs finite difference under operating defaults
invalid physical sequence rejection
negative/excessive other loss rejection
exactly one discharge
ETS gas eligibility by year
year-crossing voyage emissions
36-month finite-price gate
invalid vol/correlation rejection
programme decision value vs full-P&L aggregate
outside-option tests
feasibility exclusion tests
cash-flow no-double-discount tests
terminal-state comparability tests
```

CI should run a synthetic workbook fixture. At present, the most important market-data-backed paths are not protected on every push.

Mutation evidence requested by the implementation brief is not present in the current `test_results` folder.

---

# 7. Immediate remediation sequence

## P0.1: Correct the risk engine before adding features

1. Add the four zero-shock operating-default tests.
2. Fix loading-time/fuel handling in `_vectorized_reprice`.
3. Fix analytic charter/VLSFO deltas.
4. Make zero-shock equality an unconditional runtime-development invariant.
5. Record before/after VaR impact.
6. Do not alter frozen legacy expected values.

This is the first concrete task.

## P0.2: Harden the physical engine API

1. Validate other loss.
2. Enforce one discharge.
3. Enforce segment ordering.
4. Reject non-zero reconciliation.
5. Reject missing state configuration.
6. Add adversarial tests.

## P0.3: Correct emissions governance

1. Preserve CO2, CH4 and N2O separately through valuation.
2. Apply gas scope by compliance year.
3. Date segments.
4. Confirm methane slip and N2O by engine.
5. Update GWP source.
6. Add FuelEU exposure or a quantified scenario range.

## P0.4: Repair documentation and release identity

1. Choose a current version.
2. Record current commit and workbook hash.
3. Update stale module docstrings.
4. Rewrite `MODEL_ASSUMPTIONS.md`.
5. Save current 64, 138 and smoke outputs.
6. Remove caches from the release package.

---

## P1: Complete the deterministic commercial decision engine

1. Add no-lift/cancel/FOB resale alternatives.
2. Add feasibility gates.
3. Add cargo opportunities and vessel state.
4. Add dated cash flows.
5. Add terminal heel/location/redelivery value.
6. Split voyage, turnaround and free-tail days.
7. Add VLSFO and EUA forward curves.
8. Add actual pricing calendars.

Only after these steps should the app describe a result as a complete pre-lift recommendation.

---

## P2: Rebuild risk and hedging from canonical cash flows

1. Cache physical coefficients.
2. Generate canonical price-dependent cash flows.
3. Vectorise only the market-price tail.
4. Use delivery-contract IDs.
5. Add bunker, EUA and basis factors.
6. Revalue feasible programmes.
7. Derive hedge legs from contractual exposures.
8. Add lot, liquidity and transaction costs.
9. Backtest the same cargo through time.

---

## P3: Production controls

1. Split the Streamlit app into pages.
2. Add audit exports.
3. Add model and data versions.
4. Add saved cases.
5. Add approval/sign-off fields.
6. Add deployment packaging.
7. Add synthetic-data CI and smoke tests.
8. Archive obsolete copies.

---

# 8. Proposed release gates

The next release must not ship unless all of the following hold:

```text
Zero-shock P&L = 0 for every portfolio and current operating default
Analytic deltas = finite-difference deltas
Invalid physical ledgers raise
Every returned physical ledger reconciles
2024/25 ETS excludes CH4 and N2O
2026 ETS includes governed CH4 and N2O factors
36-month displayed prices are finite or explicitly unavailable
Current workbook hash and commit are recorded
64/64 legacy tests pass
All current pytest tests pass
New adversarial tests pass
All five Streamlit pages pass
```

---

# 9. Final assessment

The project is substantially improved and the deterministic core is now worth continuing.

The critical mistake would be to interpret the number of passing tests or the completed Phase 2 checklist as evidence that the entire system is finished.

The correct position is:

- the **legacy screen is stable**;
- the **deterministic physical decision path is a good prototype**;
- the **programme is a useful but simplified scheduler**;
- the **risk and hedge pages are still legacy tools**;
- the **risk repricer currently contains a live base-value defect**;
- the **commercial, feasibility, calendar, terminal-state and governance layers remain incomplete**.

The first next step is the P0 risk-equivalence patch. No further feature should take priority over it.
