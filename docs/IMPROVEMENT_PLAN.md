# LNG Forward Netback Project Improvement Plan

**Project reviewed:** LNG Forward Netback Streamlit application  
**Current baseline:** v2.2 re-baselined renewal-rate model  
**Plan version:** 1.2  
**Date:** 15 July 2026  
**Review incorporated:** `IMPROVEMENT_PLAN_REVIEW.md`, reviewed against commit `d7f8c07`  
**Regression reference:** 64/64 checks passed against `LNG history.xlsx`, master dates through 8 July 2026

---

## 1. Executive summary

The current project is a well-tested monthly forward-netback and renewal-rate screening tool. Its principal economic question is:

> If a vessel repeatedly cycles from USGC, which basin gives the highest margin per vessel-day?

That screen is useful, but it should not be used as the sole recommendation engine for:

- a cargo that is already loaded;
- a pre-lift cargo where lifting, cancellation or FOB sale remain alternatives;
- a vessel with a finite availability window;
- two NWE voyages versus one Asia voyage;
- a portfolio with actual laycans, slots, redelivery dates and vessel constraints.

The target project should retain the existing monthly strip, sensitivity, hedge and historical-risk functionality, while adding state-based valuation and a discrete vessel-programme engine.

The recommended architecture has four clearly separated analytical modes:

1. **Renewal-rate screen**  
   Existing margin-per-vessel-day and JKM breakeven logic, retained as a labelled screen.

2. **Post-lift diversion**  
   Procurement and loading are sunk. Rank only future route-dependent cash flows.

3. **Pre-lift cargo decision**  
   Include procurement and loading. Compare lift-and-send, FOB sale, cancel/no-lift and float.

4. **Discrete vessel programme**  
   Optimise complete cargo sequences over a common vessel horizon using the forward economics of each actual lifting.

The review of plan version 1.0 confirmed that its diagnosis of the codebase was factually accurate. It also identified live defects and planning inconsistencies that materially change sequencing and acceptance criteria:

- the actual re-baselined route durations are Europe 25.9402 days, Asia base 46.7436 days and Asia congested 54.7436 days;
- the default 12-cargo VaR, stress and backtest portfolio is not physically feasible for one vessel;
- the backtest compares rows by position and therefore changes physical delivery month across month boundaries;
- M12 already uses extrapolated EUR/USD beyond the one-year outright;
- roll-aligned historical returns are already implemented but are not used by the UI or tests;
- the risk rebuild requires an explicit performance architecture rather than a blanket ban on vectorisation;
- VLSFO and EUA require forward curves in base valuation, not only shocks;
- the duplicate unversioned sibling copy of the application must be removed or archived before restructuring.

The immediate priority is therefore split into two levels:

- **P0A: baseline protection and live-defect containment**, which should be completed immediately;
- **P0B: core economic rebuild**, comprising decision modes, physical reconciliation and discrete programme optimisation.

The present 64 regression checks must remain as a legacy compatibility suite throughout migration.

## 1.1 Review incorporation record

This version incorporates every material finding in `IMPROVEMENT_PLAN_REVIEW.md`.

| Review finding | Change made in version 1.1 |
|---|---|
| Worked example used stale 27/53-day durations | Replaced with 25.9402-day Europe, 46.7436-day base Asia and 54.7436-day congested Asia benchmarks |
| Programme formula risked double-discounting | Individual dated cash flows are now authoritative; programme value is their single-discount aggregate |
| Immediate task cut across phase order | Marked as a provisional thin slice using legacy physics, with mandatory re-baselining after the physical rebuild |
| Too many items labelled P0 | Split into P0A immediate controls and P0B core rebuild |
| Default 12-cargo VaR portfolio is infeasible | Elevated to a live defect and Phase 5 entry requirement |
| Backtest changes delivery month across month boundaries | Elevated to a specific defect with a contract-ID acceptance test |
| M12 FX extrapolation is already live | Moved into the current-issue statement and near-term remediation |
| Roll-aligned returns are implemented but unused | Added an early containment step to activate and re-fixture them |
| One-engine principle conflicts with risk performance | Added a cached physical layer and vectorised price-tail architecture |
| No VLSFO or EUA forward curve | Added both to the market-data and contract-calendar workstream |
| Duplicate sibling application copy | Added archive/delete and non-colliding package-name actions to Phase 0 |
| No git tag or frozen audit exists | Retained as explicit outstanding Phase 0 deliverables |

### Version 1.2 changes (second review round)

Version 1.2 applies the seven findings of the review addendum:

| Addendum finding | Change made in version 1.2 |
|---|---|
| Improvement 14 §D retained the stale 27/53-day programme tests | Replaced with the 52/55/54-day benchmark set and explicit margin assertions |
| Improvement 13 preset retained the stale 54-day comparison example | Replaced with 52-day base-Asia and 55-day congested-Asia presets |
| Roll-aligned default switch would break legacy 64/64 fixtures | Scoped to the application default only; function default and legacy fixtures stay pinned to `naive` |
| JKM `c14` missing for 51 master dates (31-Oct-2022 to 9-Jan-2023) | Added a mandatory `build_scenarios` NaN guard before roll-aligned activation |
| 52-day benchmark passes by only 0.1196 days | Benchmark tests must assert remaining-day margins explicitly |
| VLSFO/EUA curves (P1) live inside the P2 contract-calendar workstream | Added a sequencing note splitting Improvement 6 into P1 and P2 subsets |
| Phase 0 froze against commit `d7f8c07` | Freeze reference changed to the `v2.2-renewal-rate` tag |

---

## 2. Design principles

### 2.1 One economic question per mode

The application must state the selected decision state before showing a recommendation.

A result should never silently mix:

- sunk-cost diversion economics;
- full-cargo profitability;
- repeated fleet deployment;
- lift/no-lift optionality.

### 2.2 Feasibility before value

A route or action should enter the ranking only after contractual, operational, terminal, quality, sanctions and credit conditions pass.

### 2.3 One governed valuation engine with a performant scenario path

Deterministic valuation, sensitivities, historical simulation, stress testing and hedging must share one governed economic specification.

This does not require every risk scenario to call a slow Python object graph end to end. The target implementation should separate:

1. a cached physical and contractual layer that is invariant to ordinary market-price shocks;
2. a canonical price-dependent cash-flow representation;
3. a vectorised scenario repricer generated from, or directly consuming, that representation.

The prohibited design is an independently maintained set of economic formulas in `risk.py`. The permitted design is a fast vectorised tail whose coefficients and cash-flow definitions come from the same canonical valuation result as the deterministic engine.

The risk implementation must include an exact zero-shock equivalence test and selected scenario-by-scenario equivalence checks against the full engine.

### 2.4 Physical conservation

Loaded LNG must reconcile to:

- delivered LNG;
- LNG burned;
- vented LNG;
- other cargo loss;
- heel retained.

Fuel cost, cargo loss and emissions must all come from the same segment-level energy balance.

### 2.5 Compare like with like

Alternative programmes must be compared over:

- the same start state;
- the same vessel horizon;
- a clearly defined terminal state.

Any difference in terminal vessel location, remaining days, heel, next employment or redelivery must be represented explicitly.

### 2.6 Preserve the existing model as a screen

The current `strip()` logic should remain available as a legacy-compatible renewal-rate page and regression benchmark.

It should not remain the default recommendation engine.

---

## 3. Target application structure

A directory named `lng_netback_app/` already exists as an unversioned sibling copy of the project. It must be archived or deleted in Phase 0. To avoid collision, the proposed package name is `netback_engine`, while the repository root retains its existing project name.

Recommended structure:

```text
project_root/
    app.py
    pages/
        decision_page.py
        forward_strip_page.py
        voyage_page.py
        programme_page.py
        sensitivities_page.py
        hedging_page.py
        risk_page.py
        audit_page.py

    netback_engine/
        enums.py
        schemas.py
        market_data.py
        contract_calendar.py
        feasibility.py
        physical.py
        emissions.py
        valuation.py
        actions.py
        optimisation.py
        sensitivities.py
        hedging.py
        scenarios.py
        scenario_repricing.py
        reporting.py

        legacy/
            renewal_rate.py

    data/
    docs/
        LNG_Netback_Streamlit_Spec.md
        IMPROVEMENT_PLAN.md
        MODEL_ASSUMPTIONS.md
        DATA_DICTIONARY.md

    tests/
        test_legacy_regression.py
        test_market_data.py
        test_contract_calendar.py
        test_physical_balance.py
        test_emissions.py
        test_decision_modes.py
        test_feasibility.py
        test_programme_optimisation.py
        test_sensitivities.py
        test_risk_revaluation.py
        test_hedging.py
        test_audit.py
```

The existing `model.py` should not be expanded indefinitely. It should be decomposed into explicit market, physical, valuation and optimisation layers. The existing top-level files remain only during a controlled migration and are then removed.

# 4. High-level improvements and implementation steps

## Improvement 1: Introduce explicit decision modes

### Current issue

The current model solves repeated fleet deployment only. Procurement is included in every cargo and the recommendation is based on margin per vessel-day.

That is correct for the current renewal-rate screen but incorrect for a post-lift cargo, where procurement and loading are sunk.

### Target state

Introduce:

```python
class DecisionMode(Enum):
    RENEWAL_RATE_SCREEN = "renewal_rate_screen"
    POST_LIFT_DIVERSION = "post_lift_diversion"
    PRE_LIFT_CARGO = "pre_lift_cargo"
    VESSEL_PROGRAMME = "vessel_programme"
```

Each mode should define:

- decision point;
- costs that are sunk;
- feasible actions;
- recommendation metric;
- terminal-state convention.

### Implementation steps

1. Create `enums.py` and define `DecisionMode`.
2. Create `DecisionState` in `schemas.py`.
3. Move the present `strip()` logic to `legacy/renewal_rate.py`.
4. Add a mode selector at the top of the sidebar.
5. Create a cost-inclusion policy:

   ```python
   cost_policy(mode, cost_type) -> INCLUDED | SUNK | NOT_APPLICABLE
   ```

6. For post-lift mode:
   - exclude procurement;
   - exclude loading already incurred;
   - include remaining freight, fuel, canal, port, terminal, emissions and switching costs.
7. For pre-lift mode:
   - include procurement and loading;
   - include cancel/no-lift, FOB resale and floating alternatives.
8. For programme mode:
   - value the current cargo according to its current state;
   - value every later lifting as a new pre-lift cargo.
9. Show the decision mode and cost treatment in every audit export.

### Acceptance criteria

- Procurement is zero in post-lift incremental NPV but remains in full-cargo P&L.
- Procurement is included for every future programme cargo.
- The same input deck can produce different valid recommendations under different modes.
- The UI explains why a cost is included, sunk or excluded.

---

## Improvement 2: Replace continuous voyage ratios with discrete vessel-programme optimisation

### Current issue

Margin per vessel-day treats the number of voyages as continuous. The current re-baselined durations are:

- Europe round trip: 25.9402 days;
- Asia base round trip: 46.7436 days;
- Asia congested round trip: 54.7436 days.

The renewal-rate screen can therefore behave as though an Asia cycle competes with a fractional number of Europe cycles. Actual operations are discrete.

The later Europe cargo also loads at a later date and must use its own HH, TTF, charter, bunker, EUA and physical-basis economics.

### Target state

Optimise complete time-ordered sequences:

```text
Current cargo -> route -> return/terminal state
Future cargo 2 -> route -> return/terminal state
Future cargo 3 -> route -> ...
```

The authoritative objective is the sum of each programme's individually dated and singly discounted cash flows:

\[
V_{\text{programme}}
=
\sum_{c \in \text{cargoes}}
\sum_{k \in \text{cash flows of }c}
DF(t_{c,k})\,CF_{c,k}
+
DF(t_T)\,V_{\text{terminal}}
\]

A programme optimiser must not apply a second discount factor to a cargo value that is already an NPV.

Subject to:

- vessel availability;
- cargo laycans;
- route duration;
- terminal slots;
- canal slots;
- contract rights;
- maximum cargo count;
- programme horizon;
- required terminal state.

### Implementation steps

1. Create `CargoOpportunity` with:
   - cargo ID;
   - load window;
   - quantity;
   - procurement formula;
   - permitted destinations;
   - delivery windows;
   - market-pricing rules.
2. Create `VesselState` with:
   - current location;
   - available date;
   - heel;
   - compatibility;
   - charter/redelivery constraints.
3. Create `RouteOption` with:
   - origin;
   - destination;
   - segments;
   - terminal state;
   - duration;
   - feasibility requirements.
4. Implement a discrete dynamic-programming or label-setting optimiser.
5. Start with one vessel and deterministic durations.
6. Price every later cargo using the forward curves applicable to its own loading, delivery and payment dates.
7. Add residual vessel value for unused days.
8. Add a programme comparison table:
   - sequence;
   - cargo values;
   - used days;
   - residual days;
   - terminal value;
   - total NPV.
9. Retain value per vessel-day as a diagnostic, not the programme objective.
10. Later extend to multiple vessels using mixed-integer programming or min-cost flow.

### Acceptance criteria

Two explicit benchmark cases must be tested against the actual re-baselined constants:

**Base-Asia benchmark, 52-day horizon**

- two Europe cycles fit: \(2 \times 25.9402 = 51.8804\) days;
- one base-Asia cycle fits: 46.7436 days;
- no fractional cargoes are permitted.

**Congested-Asia benchmark, 55-day horizon**

- two Europe cycles fit: 51.8804 days;
- one congested-Asia cycle fits: 54.7436 days;
- each later cargo uses its own forward economics.

A separate 54-day test must confirm that a 54.7436-day congested-Asia route is infeasible.

The two-Europe fit at 52 days succeeds by only 0.1196 days (about 2.9 hours). Benchmark tests must therefore assert the remaining-day margin explicitly, not merely the fit/no-fit outcome, so that any later re-derivation of voyage durations — in particular after the Phase 2 physical rebuild — fails visibly rather than silently flipping a knife-edge fixture.

## Improvement 3: Build one segment-level physical voyage engine

### Current issue

The current model contains physically disconnected inputs:

- boil-off changes delivered volume;
- residual laden VLSFO independently changes fuel cost;
- laden fuel requirement is mainly a reference value;
- natural BOG offset is mainly a reference value;
- changing BOG does not automatically change fuel savings;
- changing engine demand does not automatically change forced LNG or liquid-fuel use.

This can create internally inconsistent combinations.

### Target state

Use one segment-level mass and energy balance.

Each route contains segments such as:

- loading;
- laden sea;
- canal transit;
- queue;
- discharge;
- ballast sea;
- positioning;
- floating storage.

Each segment should specify:

- duration;
- operating state;
- LNG inventory entering the segment;
- natural BOG rate;
- propulsion and auxiliary energy demand;
- reliquefaction capacity;
- liquid-fuel mode;
- emissions scope.

### Implementation steps

1. Create `SegmentType` and `VoyageSegment`.
2. Create `VesselPerformance` with:
   - LNG demand by operating state;
   - liquid-fuel demand;
   - BOR by operating state;
   - reliquefaction capacity;
   - methane slip;
   - pilot fuel;
   - energy factors.
3. Implement segment calculation:

   \[
   BOG^{natural} = Inventory \times BOR \times Days
   \]

   \[
   Demand = EnergyDemandPerDay \times Days
   \]

   \[
   BOG^{burn} = \min(BOG^{natural}, Demand)
   \]

   \[
   Surplus = \max(BOG^{natural}-Demand,0)
   \]

   \[
   Reliquefied = \min(Surplus, ReliqCapacity\times Days)
   \]

   \[
   Vented = Surplus-Reliquefied
   \]

   \[
   Shortfall = \max(Demand-BOG^{natural},0)
   \]

4. Supply shortfall using:
   - liquid fuel; or
   - forced LNG vaporisation.
5. Update cargo inventory after each laden segment.
6. Treat ballast fuel separately from delivered cargo.
7. Model heel as an explicit inventory and terminal asset.
8. Return a complete physical ledger by segment.
9. Make valuation consume this physical result rather than recomputing BOG and fuel separately.
10. Add a reconciliation report.

### Acceptance criteria

For every voyage:

\[
Loaded =
Delivered + LNGBurned + Vented + OtherLoss + Heel
\]

within numerical tolerance.

Changing BOR must affect:

- delivered quantity;
- LNG burned;
- reliquefaction;
- venting;
- liquid-fuel shortfall;
- emissions;
- route value.

No fuel or BOG input should be display-only unless explicitly labelled as diagnostic.

---

## Improvement 4: Separate queue, canal, port and steaming states

### Current issue

The congestion case increases laden and ballast leg days and charges waiting days at full propulsion rates.

That overstates fuel demand and can overstate cargo loss and emissions.

### Target state

Queue time should have:

- zero nautical miles;
- no full-speed propulsion;
- auxiliary demand;
- natural BOG;
- possible reliquefaction;
- anchorage or demurrage cost;
- emissions appropriate to the operating state.

### Implementation steps

1. Remove congestion from `asia_laden_days`.
2. Add separate:
   - laden queue days;
   - ballast queue days;
   - canal transit days;
   - port days.
3. Add operating-state consumption rates for each.
4. Enter Panama:
   - toll;
   - booking/reservation cost;
   - auction premium;
   - services;
   - queue duration,
   as separate fields.
5. Add queue scenarios on the Sensitivities page.
6. Update voyage duration as the sum of segments.

### Acceptance criteria

- Adding five queue days does not add five full-speed steaming days.
- Queue days still increase BOG, auxiliaries, time charter and possible demurrage.
- Canal cost and canal delay can be stressed independently.
- Segment totals reconcile to total voyage days.

---

## Improvement 5: Derive emissions and regulatory costs from the physical engine

### Current issue

EU ETS emissions are entered as a fixed tonnes-per-round-trip parameter. They do not automatically change when route duration, fuel use or vessel performance changes.

FuelEU is not modelled quantitatively.

### Target state

Emissions should be derived from fuel consumed in each segment:

\[
CO2e_s =
CO2_s+
CH4_s\times GWP_{CH4}+
N2O_s\times GWP_{N2O}
\]

ETS scope should be applied by segment and geography.

FuelEU should be represented either as:

- a vessel/fleet compliance shadow price; or
- an explicit unresolved exposure.

### Implementation steps

1. Create `emissions.py`.
2. Store CO₂ factors for LNG and liquid fuel.
3. Store methane slip and N₂O factors by vessel/engine mode.
4. Calculate segment emissions from actual fuel use.
5. Apply EU ETS scope:
   - 50% extra-EU voyage;
   - 100% intra-EU and EU port;
   - appropriate treatment for ballast and route direction.
6. Apply the relevant compliance year and allowance price.
7. Add contractual allocation:
   - owner;
   - charterer;
   - seller;
   - buyer;
   - unresolved.
8. Add FuelEU as a vessel-level compliance input rather than a universal flat voyage charge.
9. Display both physical emissions and economic cost.
10. Remove the static `co2_eu_ets_tonnes` input from the main valuation path.

### Acceptance criteria

- Increasing fuel consumption increases ETS emissions automatically.
- Queue days produce lower emissions than full-speed sea days.
- CH₄ and N₂O are included where applicable.
- The audit identifies both legal scope and contractual payer.
- FuelEU is never described as a future regulation.

---

## Improvement 6: Add contract-accurate market and pricing calendars, including bunker and EUA curves

### Current issue

The current model maps:

- NG/TTF front month to calendar month plus one;
- JKM roll to a day-15 rule;
- Europe delivery to the load month;
- Asia delivery to the following month.

It also uses static VLSFO and EUA prices for all forward cargoes.

The EUR/USD curve contains only spot, six-month and one-year corrected outrights in the present implementation. M12's representative mid-month is already approximately 12.2 to 12.7 months from the curve date, so the last month displayed today uses linear extrapolation beyond the one-year point. This is a live model defect, not merely a distant production enhancement.

### Target state

Create explicit market contracts, physical pricing windows and forward curves for all material deterministic valuation inputs.

### Implementation steps

1. Create `contract_calendar.py`.
2. Store exchange expiry metadata by contract.
3. Map continuation series to contract IDs before calculating returns.
4. Define physical cargo dates:
   - load laycan;
   - expected bill-of-lading date;
   - arrival window;
   - discharge date.
5. Define price rules:
   - index;
   - quotation period;
   - averaging days;
   - holiday calendar;
   - lag;
   - month relation.
6. Map TTF, JKM, HH and FX cash flows to their actual pricing periods.
7. Add a VLSFO or LNG-bunker forward curve for base valuation.
8. Add an EUA forward curve aligned to expected surrender/payment timing.
9. Replace the current M12 FX extrapolation with:
   - available longer outright tenors;
   - interpolation in discount-factor or forward-point space;
   - an explicit blocking warning where required tenor data are absent.
10. Retain the current continuation mapping only as `SCREENING_CALENDAR`.
11. Display the exact contracts, tenors, snapped dates and pricing windows in the audit.

### Acceptance criteria

- The same physical delivery month remains aligned across adjacent historical dates.
- NG/TTF expiry is not assumed to occur at calendar month-end.
- JKM pricing is not controlled solely by the 15th-day rule.
- Future cargo 2 uses later contracts than cargo 1.
- M12 does not silently extrapolate beyond the available FX curve.
- A future cargo does not use today's flat VLSFO or spot EUA by default.
- The audit shows contract IDs, quotation dates, FX conversion dates and forward tenors.

## Improvement 7: Add contractual and operational feasibility gates

### Current issue

The existing route verdict assumes both routes can be executed.

### Target state

A route or action enters the ranking only if it passes defined gates.

### Implementation steps

1. Create `feasibility.py`.
2. Add fields for:
   - destination rights;
   - diversion rights;
   - buyer consent;
   - terminal slot;
   - vessel-terminal compatibility;
   - quality/specification;
   - delivery window;
   - sanctions;
   - origin restrictions;
   - credit;
   - canal slot;
   - minimum heel;
   - redelivery restrictions.
3. Use three states:
   - feasible;
   - conditionally feasible;
   - infeasible.
4. For conditional feasibility, store:
   - expected cost;
   - probability;
   - required approval;
   - decision deadline.
5. Exclude infeasible routes.
6. Flag conditional routes and optionally risk-adjust their value.
7. Show gate failures before showing a recommendation.
8. Add missing-data handling:

   ```text
   NO_DECISION_INSUFFICIENT_DATA
   ```

### Acceptance criteria

- An infeasible route cannot win because of a high market netback.
- Every recommendation lists the gates passed and unresolved.
- Conditional approval costs are visible.
- The user can distinguish “economically inferior” from “not executable”.

---

## Improvement 8: Improve cash-flow timing and terminal value

### Current issue

The current model calculates a per-cargo margin and margin per day. It does not explicitly time all cash flows and assumes both routes return to a common USGC state.

A programme-level discount factor applied to an already discounted cargo NPV would double-discount the cargo.

### Target state

Represent each cash flow with:

- amount;
- currency;
- payment date;
- discount curve;
- risk factor;
- contractual payer.

Represent terminal state with:

- vessel location;
- availability date;
- heel;
- next employment;
- redelivery value;
- maintenance status.

Individual dated cash flows are authoritative. Cargo NPV and programme NPV are aggregations of those cash flows and are never discounted again after aggregation.

### Implementation steps

1. Create a `CashFlow` schema.
2. Generate separate cash flows for:
   - procurement;
   - liquefaction;
   - loading;
   - charter;
   - fuel;
   - canal;
   - port;
   - ETS;
   - terminal;
   - sale revenue.
3. Discount each flow once on its own date.
4. Convert each currency using the appropriate forward FX date.
5. Create `CargoValuation` containing dated cash flows and their aggregate NPV.
6. Create `TerminalState`.
7. Add:
   - vessel-location value;
   - residual vessel-day value;
   - heel value;
   - backhaul/sublet value;
   - redelivery penalty.
8. Require programme alternatives to finish in comparable states or explicitly value the difference.
9. Keep margin per day as an explanatory metric only.
10. Add tests that reject double-discounting.

### Acceptance criteria

- Route duration affects payment timing and discounting.
- Every cash flow is discounted exactly once.
- A vessel ending in a more valuable location receives explicit credit.
- Residual days in a programme are valued consistently.
- Two programmes with different terminal states are not compared without an adjustment.

## Improvement 9: Expand destination and route architecture

### Current issue

The model contains one Europe route and one Asia route.

### Target state

Routes should be data objects rather than hardcoded formula branches.

### Implementation steps

1. Define `RouteDefinition` in configuration.
2. Move route distances, segments and costs to YAML/JSON or typed Python configuration.
3. Add potential routes:
   - NWE;
   - Mediterranean;
   - Japan/Korea via Panama;
   - Asia via Suez;
   - Asia via Cape;
   - India;
   - Brazil;
   - reload/backhaul.
4. Add route-specific:
   - price basis;
   - terminal cost;
   - canal;
   - sanctions;
   - quality;
   - seasonality.
5. Allow users to add or disable routes.
6. Add route comparison maps/tables only after valuation is route-generic.

### Acceptance criteria

- Adding a route requires configuration, not edits to core formulas.
- Every route uses the same physical and valuation engine.
- Route-specific basis and terminal value remain visible.

---

## Improvement 10: Rebuild risk as full revaluation through a common, performant scenario architecture

### Current issue

`risk.py` contains a separate vectorised implementation of route economics.

The following are live defects or material limitations:

1. `historical_var(..., portfolio="12cargo")` defaults to a nominal 12-cargo portfolio that is not physically time-feasible for one vessel. The same default feeds headline stress and backtest outputs.
2. `backtest_var` compares monthly rows by position. Across a month boundary, row M1 refers to a different physical delivery month, so the realised P&L is wrong on every month-roll date.
3. Roll-aligned scenario construction already exists but is default-off and unused by the UI and regression path.
4. Current scenarios omit bunker, EUA, physical basis, terminal access, canal and FuelEU risks.
5. Calling a full Python physical engine tens of thousands of times would make the risk pages unusably slow.

### Target state

Historical simulation and stress testing should share one governed economic representation with deterministic valuation, while retaining a fast vectorised price-dependent repricing layer.

The architecture should be:

```text
Full deterministic engine
    -> cached physical/contractual result
    -> canonical dated cash-flow coefficients
    -> vectorised scenario repricer
```

Market shocks generally change price-dependent cash flows without changing voyage physics. Operational stresses that alter voyage duration, route or feasibility must trigger a fuller revaluation.

### Immediate containment steps

1. Change the **application-level** default scenario method to the already implemented `roll_aligned`, subject to both of the following:
   - the `risk.py` function-level default remains `naive` and the legacy regression suite pins `method="naive"` explicitly, because the single- and hedged-cargo fixtures currently inherit the function default and would otherwise break the frozen 64/64;
   - `build_scenarios` first gains a hard NaN guard: roll-aligned construction requires a 14th strip column, and JKM `c14` is missing for 51 master dates (31 October 2022 to 9 January 2023), so any curve date whose lookback window includes that gap must fail loudly or fall back to `naive` with a visible warning rather than produce NaN-poisoned VaR.
2. Add a UI label and audit field showing the scenario method.
3. Stop presenting the default 12-cargo result as a feasible one-vessel portfolio.
4. Add a warning or temporarily default to a single committed cargo until the discrete programme portfolio exists.
5. Fix backtest alignment by physical contract ID before interpreting new backtest statistics.

### Full implementation steps

1. Split scenario generation from valuation.
2. Build scenarios by contract ID rather than continuation column.
3. Use actual exchange expiries.
4. Cache physical voyage and contractual results.
5. Generate canonical price-dependent cash-flow coefficients from the deterministic engine.
6. Vectorise only the market-price tail of the calculation.
7. Add factors for:
   - VLSFO or LNG bunker price;
   - EUA;
   - charter;
   - DES NWE basis;
   - JKM physical basis;
   - Panama cost/delay;
   - terminal access;
   - FuelEU shadow price.
8. Revalue:
   - committed Europe cargo;
   - committed Asia cargo;
   - flexible current cargo;
   - complete vessel programme.
9. Replace the nominal 12-cargo strip with a time-feasible cargo portfolio.
10. Correct backtesting so the realised next-day value refers to the same physical contract and cargo.
11. Add expected-shortfall confidence intervals and tail-observation counts.
12. Separate:
    - market VaR;
    - operational stress;
    - contractual-event stress.
13. Keep old fixture VaR as a legacy regression suite.
14. Benchmark runtime and set a maximum acceptable interactive latency.

### Acceptance criteria

- No independently maintained route formula remains in `risk.py`.
- Zero-shock repricing exactly reproduces deterministic valuation.
- Selected scenarios match the full engine within defined tolerance.
- Portfolio VaR contains only cargoes that can physically coexist.
- Backtesting follows the same contract and cargo across dates.
- Roll-aligned scenarios are the default.
- The risk page remains within the agreed runtime target.
- The UI labels the result as partial or full according to included factors.

## Improvement 11: Make hedging derive from contractual cash flows

### Current issue

The existing hedge module is useful as a ratio and sign check, but it hedges the same model indices used to value the cargo. This produces unrealistically small residual risk when basis and timing mismatches are absent.

### Target state

Hedges should derive from actual indexed cash flows and pricing windows.

### Implementation steps

1. For every contractual price cash flow, identify:
   - underlying;
   - notional;
   - pricing dates;
   - currency;
   - sign.
2. Generate hedge candidates:
   - HH/NG;
   - TTF;
   - JKM;
   - EUR/USD;
   - VLSFO/LNG bunker;
   - EUA;
   - freight where available.
3. Apply:
   - contract sizes;
   - lot rounding;
   - liquidity limits;
   - transaction costs;
   - hedge dates.
4. Add basis-risk factors:
   - USGC terminal to HH;
   - DES NWE to TTF;
   - physical JKM to JKM index.
5. Revalue hedged and unhedged positions through the same scenario engine.
6. Report:
   - index risk reduction;
   - basis residual;
   - FX residual;
   - roll residual;
   - transaction costs.
7. Retain existing analytic hedge calculations as a model-internal regression test.

### Acceptance criteria

- Hedge ratios are traceable to contract cash flows.
- The app does not claim that a physical cargo can be hedged to a 0.4% residual solely because model indices cancel.
- Lot rounding and basis risk remain visible.
- Hedge P&L uses documented FX conventions.

---

## Improvement 12: Improve data governance, repository governance and reproducibility

### Current issue

The workbook is external to the repository and the test suite depends on an environment path. Results are reproducible only if the user has the same workbook version.

A second unversioned directory named `lng_netback_app/` contains a byte-identical copy of the application beside the repository. This creates whole-project drift risk and conflicts with the package name proposed in plan version 1.0.

No Git tag, workbook hash record or frozen 64/64 output has yet been created.

### Target state

Every run should record exactly which code, data and assumptions produced the result. There should be one authoritative repository and one authoritative source tree.

### Implementation steps

1. Archive or delete the duplicate sibling application directory, or formally make it the repository, but do not maintain two manual copies.
2. Use the non-colliding package name `netback_engine`.
3. Tag the current baseline as `v2.2-renewal-rate`.
4. Record the vendor workbook:
   - cryptographic hash;
   - file size;
   - modified time;
   - master date range;
   - row counts.
5. Save the 64/64 regression output and commit reference.
6. Add workbook hash, size and modified time to audit output.
7. Add a small synthetic or anonymised fixture workbook to the repository.
8. Keep vendor data outside version control.
9. Add schema validation for every required sheet and column.
10. Add a data dictionary.
11. Record:
    - source;
    - snapped date;
    - contract ID;
    - units;
    - transformation;
    - stale-data warning.
12. Add deterministic model version and Git commit to every export.
13. Add configuration version and route-definition version.
14. Add CI using the synthetic fixture.
15. Run the vendor-workbook regression suite in a controlled internal environment.

### Acceptance criteria

- Only one authoritative source tree exists.
- The baseline tag and workbook hash are recorded.
- The project can run core tests without proprietary data.
- A production audit identifies the exact vendor workbook and code commit used.
- Unit and transformation errors are caught before valuation.
- Results are reproducible from the exported audit package.

## Improvement 13: Redesign the Streamlit interface around decisions

### Current issue

The current app is organised around model sections and a large parameter editor. It can be difficult to distinguish:

- input;
- assumption;
- result;
- limitation;
- recommendation basis.

### Target state

Use a decision workflow.

### Implementation steps

1. Page 1: **Decision setup**
   - mode;
   - current cargo state;
   - vessel state;
   - candidate actions.
2. Page 2: **Market and contract**
   - curve date;
   - pricing windows;
   - procurement;
   - destination basis.
3. Page 3: **Voyage and feasibility**
   - route segments;
   - vessel performance;
   - gates.
4. Page 4: **Recommendation**
   - best action/programme;
   - next best;
   - value advantage;
   - reasons;
   - unresolved gates.
5. Page 5: **Physical reconciliation**
   - cargo;
   - fuel;
   - BOG;
   - emissions.
6. Page 6: **Sensitivities and breakevens**
7. Page 7: **Risk and hedging**
8. Page 8: **Audit**
9. Add presets:
   - legacy benchmark;
   - post-lift at USGC;
   - current vessel mid-voyage;
   - 52-day two-Europe-versus-one-base-Asia example;
   - 55-day two-Europe-versus-one-congested-Asia example.
10. Use readable number formatting:
    - USD/MMBtu with decimals;
    - large USD and MMBtu with commas and no unnecessary decimals.
11. Rename ambiguous labels:
    - “decision buffer” to “advantage versus next best”;
    - “verdict” to “renewal-rate screen result” on the legacy page.
12. Display warnings prominently:
    - both routes negative;
    - missing feasibility;
    - stale market data;
    - partial VaR;
    - approximate pricing calendar.

### Acceptance criteria

- The user can identify the decision mode without opening methodology.
- Sunk costs are explained rather than displayed as unexplained zeroes.
- The principal recommendation states whether it is:
   - isolated cargo;
   - renewal-rate screen;
   - discrete programme.
- All large numbers are readable and consistently formatted.

---

## Improvement 14: Strengthen testing and validation

### Current issue

The existing tests are strong regression checks for the present formulas. They do not cover the economic states introduced by the target architecture.

### Target state

Retain all legacy fixtures and add invariant, state and programme tests.

### Implementation steps

#### A. Preserve legacy tests

1. Rename the current suite to `test_legacy_regression.py`.
2. Preserve all 64 fixture checks.
3. Freeze the current expected values and document the workbook hash.

#### B. Add physical invariants

1. Loaded-energy conservation.
2. No negative inventory.
3. Queue does not consume full-speed propulsion fuel.
4. Increasing BOR cannot increase delivered quantity.
5. Reliquefaction cannot exceed surplus BOG or capacity.
6. Ballast consumption cannot reduce already-delivered cargo.

#### C. Add decision-mode tests

1. Post-lift procurement excluded.
2. Pre-lift procurement included.
3. Future programme cargo procurement included once.
4. Full-cargo P&L restores sunk costs.
5. Both-negative routes do not imply lift.

#### D. Add programme tests

1. A 52-day horizon permits two 25.9402-day Europe cycles (51.8804 days used).
2. A 52-day horizon permits one 46.7436-day base-Asia cycle.
3. A 55-day horizon permits one 54.7436-day congested-Asia cycle.
4. A 54-day horizon rejects the 54.7436-day congested-Asia cycle as infeasible.
5. Feasibility tests assert their remaining-day margins explicitly.
6. No fractional voyage.
7. Cargo 2 uses later forward months.
8. Residual days receive terminal value.
9. Infeasible cargo or slot is not scheduled.

#### E. Add emissions tests

1. Fuel increase raises emissions.
2. ETS scope follows segment geography.
3. CH₄ and N₂O enter CO₂e.
4. EUA bump changes only applicable routes.

#### F. Add risk tests

1. Same engine used for base and scenario valuation.
2. Same delivery contract aligned through rolls.
3. Portfolio is time-feasible.
4. VaR and ES sign conventions.
5. Tail-observation and confidence-interval checks.
6. Backtest follows the same cargo across dates.

#### G. Add property-based testing

Use Hypothesis or generated grids for:

- conservation;
- monotonicity;
- feasibility exclusion;
- discounting;
- non-negative durations;
- route sequence capacity.

### Acceptance criteria

- Legacy 64/64 remains green.
- New invariant tests fail when physical or state logic is deliberately broken.
- Every production recommendation has at least one regression fixture.
- CI runs without proprietary workbook data.

---

# 5. Recommended implementation phases

## Phase 0: Freeze, de-duplicate and protect the current baseline

**Priority:** P0A, immediate  
**Objective:** preserve the present tool and contain known live defects before restructuring.

### Steps

1. Archive or delete the duplicate unversioned sibling copy.
2. Confirm the authoritative repository root.
3. Tag the current code as `v2.2-renewal-rate`.
4. Record the vendor workbook hash, size, modified time and date range.
5. Save the 64/64 regression output referenced to the `v2.2-renewal-rate` tag. P0A itself adds commits (warnings, defaults, fixture pinning), so the tag — not a specific commit hash — is the freeze reference; the 15-Jul-2026 green run against commit `d7f8c07` serves as the interim record until the tag exists.
6. Add `MODEL_ASSUMPTIONS.md`.
7. Add a synthetic or anonymised CI fixture.
8. Activate the already implemented roll-aligned scenario method as the application-level default only: the `risk.py` function default and the legacy regression fixtures stay pinned to `naive`, and `build_scenarios` first gains a hard NaN guard for the JKM `c14` data gap (51 master dates, 31 October 2022 to 9 January 2023).
9. Add a visible warning that the default 12-cargo risk portfolio is not a feasible one-vessel programme.
10. Add a blocking or prominent warning for M12 FX extrapolation until the curve is extended.
11. Rename future package and page modules to avoid collisions, including `risk_page.py`.

### Exit criteria

- One authoritative source tree exists.
- The Git tag, workbook hash and frozen regression output exist.
- Current outputs remain reproducible.
- Known live risk and FX limitations are visible to users.
- No new development changes the legacy screen unintentionally.

## Phase 1: Decision-state and valuation architecture

**Objective:** separate post-lift, pre-lift and renewal-rate economics.

### Steps

1. Add schemas and enums.
2. Add cost-inclusion policy.
3. Add feasibility gate objects.
4. Add explicit cash-flow objects.
5. Add isolated route NPV.
6. Add full-cargo P&L.
7. Update Streamlit mode selection.
8. Add decision-state tests.

### Exit criteria

- The app can correctly explain why procurement is sunk or included.
- Current cargo and full-cargo values reconcile.

---

## Phase 2: Unified physical and emissions engine

**Objective:** eliminate disconnected BOG, fuel and ETS assumptions.

### Steps

1. Add voyage segments.
2. Implement mass-energy balance.
3. Separate queue, canal and port.
4. Derive delivered cargo.
5. Derive fuel cost.
6. Derive CO₂, CH₄, N₂O and ETS.
7. Add conservation and monotonicity tests.
8. Connect route valuation to physical outputs.

### Exit criteria

- One engine determines cargo loss, fuel and emissions.
- Static `co2_eu_ets_tonnes` is removed from the main path.

---

## Phase 3: Discrete vessel-programme optimiser

**Priority:** P0B  
**Objective:** solve repeated-voyage alternatives as discrete, forward-curve-consistent programmes.

### Steps

1. Add vessel state and cargo opportunities.
2. Add route terminal states.
3. Add a deterministic one-vessel optimiser.
4. Price cargo 2 and later cargoes from their own forward months.
5. Add residual vessel value.
6. Add programme UI and audit.
7. Add programme regression fixtures based on:
   - 52-day base-Asia benchmark;
   - 55-day congested-Asia benchmark;
   - 54-day congested-Asia infeasibility.

### Exit criteria

- No fractional voyages.
- Programme choices are forward-curve consistent.
- Two Europe cycles and one Asia cycle are compared only where each complete programme fits.
- All cargo cash flows are discounted exactly once.

## Phase 4: Contract calendar, feasibility and route expansion

**Objective:** move from screening dates to operationally executable actions.

### Steps

1. Add actual exchange calendars.
2. Add physical laycans and delivery windows.
3. Add terminal and canal slots.
4. Add rights, consent, quality, sanctions and credit gates.
5. Add route configuration architecture.
6. Add additional routes.

### Exit criteria

- Infeasible routes are excluded.
- Price windows correspond to actual contractual dates.

---

## Phase 5: Risk and hedging rebuild

**Priority:** P1 after the deterministic architecture is stable  
**Objective:** make risk consistent with the new deterministic engine without making interactive runs unusably slow.

### Steps

1. Separate scenarios from valuation.
2. Use contract-ID historical returns.
3. Cache physical and contractual results.
4. Generate a canonical price-dependent cash-flow representation.
5. Vectorise the price-dependent scenario tail.
6. Add omitted risk factors.
7. Revalue isolated cargoes and programmes.
8. Correct portfolio feasibility.
9. Correct roll-aware backtesting.
10. Derive hedge legs from cash flows.
11. Add basis and execution risk.
12. Benchmark runtime and memory.

### Exit criteria

- No independently maintained duplicate economic formula remains in `risk.py`.
- Risk results correspond to the same economic state as the recommendation.
- Zero-shock and sampled-scenario equivalence tests pass.
- Interactive risk pages meet the agreed runtime target.

## Phase 6: Production controls and interface refinement

**Objective:** make the tool reviewable and operationally usable.

### Steps

1. Add model and data versioning.
2. Add audit bundles.
3. Add approval and sign-off fields.
4. Improve warnings and terminology.
5. Add saved scenarios.
6. Add exportable decision reports.
7. Add controlled deployment and access.

### Exit criteria

- A decision can be reproduced from an audit package.
- The UI clearly distinguishes model output, assumption and unresolved data.

---

# 6. Priority matrix

The priority scale distinguishes immediate containment from structural rebuilds.

| Priority | Improvement | Reason |
|---|---|---|
| P0A | Freeze and de-duplicate baseline | Hours-to-days task that protects the only tested reference |
| P0A | Record tag, workbook hash and 64/64 output | Required before any migration |
| P0A | Activate roll-aligned risk method | Existing code, but requires legacy-fixture pinning and a NaN guard for the 2022-23 JKM `c14` gap |
| P0A | Warn on infeasible 12-cargo risk portfolio | Prevents misinterpretation of headline VaR and backtest |
| P0A | Warn on M12 FX extrapolation | Existing live defect |
| P0B | Decision modes | Current verdict is not valid for all commercial states |
| P0B | Unified physical and emissions engine | Removes cargo, fuel, BOG and ETS inconsistencies |
| P0B | Discrete programme optimiser | Required for complete two-Europe-versus-one-Asia comparison |
| P1 | Queue segmentation | Current congestion treatment overstates propulsion |
| P1 | Forward pricing of future cargoes | Required for correct programme economics |
| P1 | VLSFO and EUA forward curves | Prevents flat spot assumptions across the strip |
| P1 | Feasibility gates | Prevents recommending an unavailable route |
| P1 | Cash-flow timing and terminal state | Required for like-for-like comparison |
| P1 | Common performant risk architecture | Prevents formula drift without losing usability |
| P2 | Contract calendars | Removes continuation and roll approximations |
| P2 | Basis-aware hedging | Makes hedge residuals commercially credible |
| P2 | Additional routes | Extends scope after the core engine is stable |
| P2 | Production audit controls | Required before controlled business use |

**Sequencing note:** the VLSFO and EUA forward curves (P1) are implemented inside the Improvement 6 contract-calendar workstream (P2). The curve-loading and tenor-mapping subset of Improvement 6 must therefore be brought forward with the P1 work; only the exchange-expiry metadata and exact pricing-window refinements remain P2.

# 7. Items to retain unchanged until migration is complete

The following current functions remain useful and should not be discarded:

- workbook parsers and validation;
- FX x10 correction;
- snapped market-date display;
- the 12-month forward strip;
- current JKM breakeven screen;
- analytic sensitivity checks;
- historical fixture dates;
- current regression values;
- hedge sign and ratio checks;
- existing model caveats.

They should be reclassified as the **legacy renewal-rate analytical layer**.

---

# 8. Practices to stop

The improved project should not:

1. Present the renewal-rate verdict as a universal diversion recommendation.
2. Multiply a current post-lift cargo value by an assumed number of future cargoes.
3. Treat cargo count as continuous.
4. Price cargo 2 using cargo 1's forward month without disclosure.
5. Treat queue days as full-speed steaming.
6. Maintain independent BOG-loss and bunker-offset assumptions.
7. Hold ETS tonnes fixed while changing fuel or voyage duration.
8. Price every future cargo with one static VLSFO or EUA value.
9. Rank infeasible routes.
10. Compare different terminal vessel states without a terminal value.
11. Discount an already discounted cargo NPV again at programme level.
12. Maintain independent deterministic and risk valuation formulas.
13. Describe partial historical simulation as full economic VaR.
14. Treat near-zero model-internal hedge residual as physical hedge effectiveness.
15. Use naive continuation-column returns through rolls by default.
16. Backtest different delivery months as though they were the same cargo.
17. Use a nominal 12-cargo strip that is not physically time-feasible.
18. Maintain duplicate unversioned copies of the application.
19. Hide approximations in code comments only.

# 9. Definition of done for the improved project

The project can be considered substantially improved when:

- one authoritative repository and source tree exist;
- the baseline tag, workbook hash and frozen 64/64 output exist;
- the user selects a decision mode before valuation;
- post-lift and pre-lift cost treatment is correct;
- route feasibility is applied before ranking;
- cargo, fuel, BOG and emissions reconcile physically;
- ETS changes with actual fuel consumption;
- queue and steaming are separate;
- two Europe voyages versus one Asia voyage is solved discretely using actual route durations;
- later cargoes use later HH, destination, bunker, EUA and FX curves;
- each cash flow is discounted exactly once;
- terminal vessel state is valued;
- M12 does not silently extrapolate FX;
- risk portfolios are physically time-feasible;
- backtesting follows the same contract and cargo across dates;
- roll-aligned scenarios are the default;
- deterministic and risk valuation share a canonical economic representation;
- risk performance remains operationally usable;
- the existing 64 legacy checks remain green;
- new physical, state, programme and roll-alignment tests pass;
- every recommendation is reproducible from an audit export;
- the UI clearly labels:
  - renewal-rate screen;
  - isolated current-cargo recommendation;
  - complete vessel-programme recommendation.

## 10. Immediate next development task

Implementation begins with Phase 0, not with the optimiser.

### Increment A: baseline freeze and live-defect containment

1. Remove or archive the duplicate sibling application.
2. Tag `v2.2-renewal-rate`.
3. Record workbook hash and frozen 64/64 output.
4. Activate roll-aligned scenarios as the application default (function default and legacy fixtures stay pinned to `naive`; add the `build_scenarios` NaN guard for the JKM `c14` gap first).
5. Warn that the current 12-cargo VaR portfolio is not a feasible one-vessel programme.
6. Warn on M12 FX extrapolation.

### Increment B: provisional decision-state thin slice

After Increment A:

> Add `DecisionMode`, preserve `strip()` as `RENEWAL_RATE_SCREEN`, and implement a provisional deterministic one-vessel programme using the existing legacy voyage economics.

This thin slice should include:

1. current-cargo post-lift value;
2. second-cargo pre-lift value;
3. later HH and TTF/JKM contract selection;
4. no fractional cargoes;
5. residual vessel days;
6. a programme audit table;
7. benchmark tests using:
   - 52 days for two Europe cycles versus one base-Asia cycle;
   - 55 days for two Europe cycles versus one congested-Asia cycle;
   - 54 days to prove congested Asia is infeasible.

The thin-slice fixtures are explicitly provisional. Voyage duration, BOG, fuel, emissions and resulting values must be re-derived after the Phase 2 physical-engine rebuild. They must not be frozen as permanent production regression values.

Once the thin slice establishes the correct decision-state and discrete scheduling architecture, complete the physical and emissions rebuild before adding further routes or elaborate risk functionality.
