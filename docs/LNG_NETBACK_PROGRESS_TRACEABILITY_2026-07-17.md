# LNG Netback project progress and traceability register

Version 1.0 | 17 July 2026

> **Editor's note (applied 17-Jul-2026):** this register was originally drafted from Google Drive with R1 marked "VERIFIED LOCALLY; DRIVE SOURCE APPLICATION PENDING", because the Drive connector that produced it could create documentation but had no write access to the actual git repository. R1 has since been independently re-verified (diff review + a from-scratch pytest run: 89 passed, 0 failed, 55 skipped) and applied directly to `main` as commit `103231006c43a37643b816481ab229535d7c6ca0`, tagged `v2.4.1-risk-equivalence`. That commit hash supersedes the placeholder `86ae95db057bbeee5fe82df840f77602b364d80a` referenced below, which does not correspond to any commit in this repository. Status fields for R1 have been updated accordingly; everything else is preserved as originally drafted.

## Purpose

This is the single auditable register for corrective work and later improvements. Every task has a stable Step ID, status, source files, tests, evidence location, commit hash, workbook hash, completion date and unresolved limitations.

## Status values

NOT STARTED | IN PROGRESS | BLOCKED | IMPLEMENTED, NOT VERIFIED | VERIFIED | DEFERRED | REJECTED

## Completion rule

A step is VERIFIED only when code is committed, required tests pass, evidence is saved, documentation is updated and every mandatory acceptance criterion is closed.

## Commit format

`[STEP-ID] concise description`

Example: `[R1.3] Include loading time and loading-port fuel in vectorised repricer`

## Evidence format

`docs/evidence/<release>/<STEP-ID>_<description>.md`
`test_results/<release>/<STEP-ID>_<test_name>.txt`

## Current verified baseline

**B0.1 Frozen legacy baseline — VERIFIED**
Evidence: `docs/BASELINE_RECORD.md`; tag `v2.2-renewal-rate`; commit `d7f8c0761100cba59462442b6d437850f5241bfe`; frozen 64/64 output.

**B0.2 Current deterministic physical build — VERIFIED WITH OPEN LIMITATIONS**
Evidence: `README.md`; `docs/IMPLEMENTATION_STATUS.md`; `docs/PHASE2_PLAN.md`; `physical.py`; `emissions.py`; `decision.py`.
Test position: 64/64 legacy, 144/144 pytest, five-page Streamlit smoke passed.
Open limitations: invalid-ledger validation; ETS year/gas treatment; no commercial outside actions; no feasibility engine; no dated programme cash flows or terminal state; risk remains on legacy rather than physical programme economics.

**B0.3 Independent critical review — VERIFIED**
Evidence: `docs/LNG_NETBACK_CRITICAL_REVIEW_2026-07-17.md`.
Confirmed critical finding: zero-shock P&L defect was reproduced and corrected in R1; see `docs/RISK_EQUIVALENCE_FIX.md`.

## RELEASE R1 — v2.4.1 risk equivalence

Objective: zero market shocks must produce zero P&L, and analytic sensitivities must reconcile with finite-difference repricing under legacy and operating defaults.

**Status: VERIFIED**
Priority: P0
Applied directly to `main` (no long-lived feature branch retained); commit `103231006c43a37643b816481ab229535d7c6ca0`; tag `v2.4.1-risk-equivalence`.
Release note: `docs/RISK_EQUIVALENCE_FIX.md`

**R1.1 Capture pre-fix evidence — VERIFIED**
Record Europe, Asia, diversion-spread and 12-cargo zero-shock P&L plus VaR and expected shortfall.
Known review values: Europe +$151,125; Asia -$48,603; spread -$199,728; 12-cargo +$1,813,500.
Acceptance: values reproduced and saved before modifying code.

**R1.2 Add operating-default zero-shock tests — VERIFIED**
Required tests: Europe, Asia, diversion spread and 12-cargo portfolio.
Acceptance: tests fail before the fix and pass after it. Absolute P&L tolerance <= $0.01 unless a tighter documented tolerance is used.

**R1.3 Correct Europe vectorised route timing — VERIFIED**
Target: `risk._vectorized_reprice()`.
Include loading days in Europe round-trip time and include loading-port fuel. Use the same definitions as `model.strip()`.
Acceptance: zero-shock Europe P&L equals zero under `model.operating_default_params()`.

**R1.4 Correct Asia ballast-time derivation — VERIFIED**
Subtract loading time when deriving Asia ballast days and include loading-port fuel consistently.
Acceptance: zero-shock Asia P&L equals zero under operating defaults.

**R1.5 Reconcile charter sensitivities — VERIFIED**
Acceptance: analytic and finite-difference deltas agree under frozen legacy defaults and 17-knot operating defaults.

**R1.6 Reconcile VLSFO sensitivities — VERIFIED**
Acceptance: Europe and Asia analytic and finite-difference VLSFO deltas agree under both parameter sets.

**R1.7 Run full validation — VERIFIED**
Required: compileall; frozen 64 suite; complete pytest suite; all zero-shock identities; sensitivity reconciliation; all five Streamlit pages.
Independently re-run in full once the workbook location was confirmed (`H:\My Drive\LNG\LNG history.xlsx`, SHA-256 matching this register's recorded hash exactly): frozen legacy 64/64 PASS, complete pytest suite 144/144 PASS with zero skips, five-page Streamlit smoke PASS. (An initial re-run before the workbook was located covered only the 89 pure tests, with the 55 workbook-gated tests skipping.)

**R1.8 Record impact and close release — VERIFIED**
Required file: `docs/RISK_EQUIVALENCE_FIX.md`.
Records cause, formula changes, pre/post zero-shock values, pre/post VaR and ES, tests, commit hash, workbook hash and remaining limitations.
Gate: R1 cannot be VERIFIED while any zero-shock P&L remains outside tolerance — satisfied.

## RELEASE R2 — v2.4.2 physical validation

Objective: `physical.run_voyage()` must reject impossible ledgers.

Status: NOT STARTED | Dependency: R1 VERIFIED (met)

- R2.1 Add `PhysicalValidationError` and `PhysicalReconciliationError`.
- R2.2 Reject negative or excessive cargo loss and negative inventory.
- R2.3 Enforce exactly one discharge, no ballast before discharge and no laden state after discharge.
- R2.4 Enforce cargo and terminal-heel reconciliations before returning.
- R2.5 Reject missing demand, shortfall or fuel configuration.
- R2.6 Add adversarial tests for every rule and prove valid Europe/Asia routes still reconcile.
- R2.7 Run full validation and issue `docs/PHYSICAL_VALIDATION_FIX.md`.

All R2 steps: NOT STARTED.

## RELEASE R3 — v2.4.3 emissions governance

Objective: preserve gases separately and make ETS economics year-aware.

Status: NOT STARTED | Dependency: R2 VERIFIED

- R3.1 Preserve CO2, CH4, N2O and physical CO2e separately.
- R3.2 Apply 2024/2025 CO2-only ETS scope and governed 2026-onward gas scope.
- R3.3 Date voyage segments for year-crossing voyages.
- R3.4 Govern engine type, methane slip, N2O factor, GWP source and effective date.
- R3.5 Separate FuelEU from EU ETS.
- R3.6 Add year, gas and year-crossing tests.

All R3 steps: NOT STARTED.

## RELEASE R4 — v2.5 commercial decision completion

Status: NOT STARTED | Dependency: R1-R3 VERIFIED

- R4.1 Rename current pre-lift output to "Best destination conditional on lifting".
- R4.2 Add lift Europe, lift Asia, no-lift/cancel, FOB resale, defer and floating-storage actions.
- R4.3 Add `FeasibilityResult`: PASS, FAIL, CONDITIONAL, UNKNOWN.
- R4.4 Check rights, consent, laycan, discharge window, terminal slot, Panama slot, compatibility, quality, sanctions and credit.
- R4.5 Exclude FAIL actions and qualify UNKNOWN actions before ranking.

## RELEASE R5 — programme valuation completion

Status: NOT STARTED | Dependency: R4 VERIFIED

- R5.1 Dated cash flows.
- R5.2 Payment timing and discounting.
- R5.3 Cargo opportunities and laycans.
- R5.4 Terminal and canal schedules.
- R5.5 Vessel start/end location.
- R5.6 Terminal heel value.
- R5.7 Redelivery and maintenance obligations.
- R5.8 Next-employment value.
- R5.9 Separate voyage days, turnaround/positioning days and genuinely uncommitted tail days.
- R5.10 Matched terminal-state tests.

## RELEASE R6 — risk and hedging rebuild

Status: NOT STARTED | Dependency: stable deterministic commercial engine
**Prioritisation (owner decision, 17-Jul-2026):** R6 is pulled ahead of
R2–R5. Implementation plan, deferred-dependency mitigations (minimal
input-sanity guard in lieu of full R2; undated/undiscounted cash flows
pending R5; hold-plan-fixed programme VaR pending R4) and data gating
(workbook has charter history but no VLSFO/EUA/basis sheets):
`docs/R6_RISK_REBUILD_PLAN.md`. Target version `v2.6-risk-rebuild`.
Increment order: A cash-flow layer → B formula-free repricer →
C physical coefficients → D programme portfolio → E new factors →
F exposure-derived hedges → G contract-ID backtest + uncertainty →
H close-out.

- R6.1 Generate canonical cash flows from deterministic valuation. —
  **IN PROGRESS:** increment A landed 17-Jul-2026 (`cashflows.py` +
  16 pinning tests; legacy-basis decomposition parity vs `model.strip()`
  ~1e-8 worst error, all six deltas derived from quantities; built by a
  Sonnet implementation agent, independently reviewed and full battery
  re-run before commit). Physical-basis decomposition lands in
  increment C.
- R6.2 Cache physical coefficients.
- R6.3 Vectorise only the price-dependent tail.
- R6.4 Use delivery-contract IDs.
- R6.5 Add VLSFO and EUA risk factors.
- R6.6 Add physical basis factors.
- R6.7 Revalue feasible programmes.
- R6.8 Derive hedges from contractual exposures.
- R6.9 Add execution lots, liquidity and transaction costs.
- R6.10 Backtest the same cargo through time.
- R6.11 Add VaR/ES uncertainty and model-risk reporting.

## RELEASE R7 — production controls

Status: NOT STARTED

- R7.1 Split `app.py` into governed pages/modules.
- R7.2 Display model version and commit.
- R7.3 Display workbook hash and market-data date.
- R7.4 Add downloadable audit package.
- R7.5 Add saved cases and approvals.
- R7.6 Add synthetic workbook fixture.
- R7.7 Run workbook-backed tests in CI.
- R7.8 Package immutable releases.
- R7.9 Remove cache/build artefacts.
- R7.10 Archive obsolete copies and resolve naming collisions.

## Current next action

Start R6 increment A (canonical cash-flow layer) per
`docs/R6_RISK_REBUILD_PLAN.md` — R6 prioritised ahead of R2–R5 by owner
decision, 17-Jul-2026. R2–R5 remain open and unclaimed. Owner action
that unblocks R6.5b: add VLSFO and EUA daily-history sheets to
`LNG history.xlsx` (same layout as `charter`).

(Superseded: the previous next action was R2.1 physical-ledger
validation. R1 is fully VERIFIED and applied to `main`.)

## Mandatory agent completion report

Step IDs worked; status changes; files changed; branch and commit hash; workbook SHA-256 and curve date; test evidence (legacy, pytest, zero-shock, finite difference, Streamlit smoke); numerical impact (before and after); open issues; tracker section updated.

Code changes alone do not complete a step. The tracker, implementation status, test evidence and release note must agree.

---

## R1 completion report — 17 July 2026 (superseding update below)

**Step IDs worked:** R1.1 through R1.8.

**Status changes:** R1.1-R1.8 VERIFIED. Source applied directly to the git repository on `main`.

**Files changed in validated release:** `risk.py`; `tests/test_risk_containment.py`; `README.md`; `docs/IMPLEMENTATION_STATUS.md`; `docs/RISK_EQUIVALENCE_FIX.md`.

**Branch and commit hash:** `main`, commit `103231006c43a37643b816481ab229535d7c6ca0`, annotated tag `v2.4.1-risk-equivalence` (tag object `148bd8ec1e904db340b184f4150f14a38a0197dd`). Pushed to `origin/main`.

**Workbook SHA-256 and curve date:** `4e51a1e6b92d004836c8cfe18f118006686ef19ad2b9813c77c53035591c5497`; curve date 2026-07-08.

**Test evidence:** Fully independently re-verified once the workbook was located at `H:\My Drive\LNG\LNG history.xlsx` (SHA-256 `4e51a1e6b9...` matching the recorded hash exactly): frozen legacy 64/64 PASS, complete pytest suite 144/144 PASS with zero skips, five-page Streamlit smoke PASS.

**Post-release finding (17-Jul-2026):** an independent logic review after R1 closed found a third copy of the same route-fuel formula that R1.3/R1.4 did not reach: the VLSFO-swap sizing in `risk.europe_hedge_legs()` / `risk.asia_hedge_legs()` (Hedging page) omitted loading-port fuel and did not net loading time out of Asia ballast days. Display/hedge-sizing only — no valuation, VaR, zero-shock or sensitivity impact, which is precisely why the R1 test battery could not see it. Fixed with a dedicated operating-default regression test pinning the swap tonnage to `model.strip()`'s fuel definitions (pytest suite now 145).

**Numerical impact:** Zero-shock P&L corrected from Europe +$151,125, Asia -$48,603, spread -$199,728 and 12-cargo +$1,813,500 to less than $0.01 absolute for every portfolio. Analytic charter and VLSFO sensitivities reconcile to finite difference within numerical noise.

**Open issues:** The risk module remains on legacy 12-month economics, the 12-cargo portfolio is not a feasible one-vessel programme, and VLSFO/EUA/basis stochastic factors remain absent. All R2-R7 items above remain NOT STARTED.

**Tracker section updated:** R1 statuses, test count, current next action and this completion report updated to reflect application to the git repository. Release note stored as `docs/RISK_EQUIVALENCE_FIX.md`.
