# R6 implementation plan — risk and hedging rebuild

**Drafted:** 17 July 2026
**Register section:** RELEASE R6 (`docs/LNG_NETBACK_PROGRESS_TRACEABILITY_2026-07-17.md`)
**Target version:** v2.6-risk-rebuild
**Prioritisation note:** the register lists R6's dependency as "stable
deterministic commercial engine" (R2–R5). The owner has prioritised R6
ahead of R2–R5. Consequences accepted and mitigated below (see
"Deferred-dependency handling").

## 1. Why this release

`risk.py` is an independently maintained duplicate of the legacy 12-month
route economics. v2.4.1 made it *numerically equal* to `model.strip()`,
but equality-by-duplication is exactly what allowed three copies of the
same formula to drift apart in the first place (the third copy — hedge-leg
VLSFO sizing — was found only in post-release review). The rebuild
replaces "duplicate the formula, then prove equality" with "generate the
exposures once, evaluate them everywhere":

```
Layer 1  physical/contractual coefficients   (price-INDEPENDENT quantities)
Layer 2  canonical price-dependent cash flows (factor, month, quantity)
Layer 3  vectorised scenario evaluation       (generic Σ qty·price, no route math)
```

## 2. The central design insight

Every physical quantity in the model — delivered MMBtu, fuel tonnes by
leg, EUA tonnes, boil-off fractions, heel — is **price-independent**. It
depends only on `Params` (speed, days, rates), never on HH/TTF/JKM/FX.
Prices enter valuation strictly through linear and bilinear terms:

| Cash-flow term | Form |
|---|---|
| Europe revenue | `qty × TTF_L × FX_L` (bilinear) |
| Asia revenue | `qty × JKM_L+1` (linear) |
| Procurement | `qty × HH_L` (linear) |
| ETS | `qty × FX_L` (linear; phase-scaled tonnes in qty) |
| Fuel / charter / tolls / fees | constant (today) or `qty × VLSFO`, `qty × charter` (if made stochastic) |

So the physical engine needs to run **once per `Params`** — not per
scenario and not per date — to produce a coefficient set. Scenario
revaluation and the whole backtest reduce to array products. This is what
makes physical-engine-based VaR *faster* than the current duplicate, not
slower, and it is why the coefficient cache (R6.2) is keyed on a `Params`
hash, not on the curve date.

Known intentional nonlinearity stays OUTSIDE the cash-flow layer, exactly
as today: verdict switching (12-cargo routes by base-date verdict),
`jkm_star`, and hedged-portfolio leg selection are computed from the
already-evaluated route values.

## 3. Hard compatibility constraints (every increment, no exceptions)

1. **Frozen legacy suite 64/64** (`tests/test_model.py`) — green at every
   commit. It pins Gate 3 sensitivities, Gate 4 hedge/VaR fixtures and
   the zero-shock identities under legacy defaults through the CURRENT
   public API (`risk.historical_var`, `risk.build_scenarios`, ...), so
   the public API survives the rebuild.
2. **R1 operating-default tests** (zero-shock ≤ $0.01, analytic = FD to
   1e-10, hedge-leg tonnage) — green at every commit.
3. **Full pytest suite** (145 and growing) + **six-check Streamlit
   smoke** — green before every push.
4. Register discipline: `[R6.x]` commit prefixes, evidence files under
   `test_results/v2.6/`, tracker updated with each verified step.

## 4. Deferred-dependency handling (R2–R5 jumped)

- **R2 (ledger validation) not done:** risk will now consume physical
  outputs. Mitigation: increment B adds a *minimal* sanity guard at the
  coefficient boundary only (delivered > 0, fuel ≥ 0, reconciliation
  within tolerance → raise, don't propagate garbage into VaR). Full R2
  remains open and is NOT claimed by this release.
- **R5 (dated cash flows / discounting) not done:** canonical cash flows
  carry a `month` label but no settlement date and no discounting —
  consistent with the rest of the model, which nowhere discounts today.
  The dataclass reserves an optional `settle_date` field so R5 can
  populate it without schema change.
- **R4 (outside options) not done:** programme VaR is the risk of the
  COMMITTED programme (hold-plan-fixed under scenarios). That is the
  honest number for a committed plan; re-optimising per scenario would
  mix in option value R4 hasn't built yet. Disclosed in the UI caption.

## 5. Data reality (checked 17-Jul-2026 against the live workbook)

Workbook sheets: HH, TTF, JKM, FX new, US netbacks, US transport,
charter, volatilities.

| Factor | History available? | R6 treatment |
|---|---|---|
| HH / TTF / JKM / FX | yes (existing) | as today |
| Charter (rate174) | **yes** — daily series already loaded | NEW stochastic factor (R6.5a), feasible immediately |
| VLSFO | **no** | R6.5b — data-gated: add loader that tolerates absence (pattern: `load_volatilities`); until a sheet exists, expose as deterministic stress knob only, disclosed in UI |
| EUA | **no** | same as VLSFO (R6.5b) |
| NWE / JKM physical basis | **no** | R6.6 — config-based proxy vol with explicit "not market-calibrated" disclosure, or excluded with disclosure; decide at increment E |

**Owner action that unblocks R6.5b fully:** add `VLSFO` and `EUA` daily
history sheets to `LNG history.xlsx` (date + price columns, same layout
as `charter`). The code path will be built ready to consume them.

## 6. Increments

Each increment is a commit (or small series), gated by the constraint
battery in §3, with evidence saved to `test_results/v2.6/`.

### A. Canonical cash-flow layer, legacy coefficients first — [R6.1]

**Goal:** kill the duplicated route formula without changing a single
number.

1. New pure module `cashflows.py` (no Streamlit, no risk imports):
   - `RiskFactor` enum: `TTF`, `JKM`, `HH`, `FX`, `TTF_X_FX` (bilinear),
     `CHARTER`, `VLSFO`, `EUA`, `CONST`.
   - `@dataclass CashFlow`: `factor`, `month_index`, `quantity`,
     `label`, `settle_date: Optional[...] = None` (reserved for R5).
   - `CargoExposure`: list of CashFlows + route/month metadata;
     `value(prices) -> float` and a vectorised
     `value_matrix(price_arrays) -> np.ndarray`.
2. `legacy_cargo_cashflows(D, tables, params, month_index) -> CargoExposure`
   generating coefficients that reproduce `model.strip()`'s Step 6
   Europe and Asia cargo values EXACTLY (the affine decomposition in §2).
3. **Pinning tests (write first):** for legacy AND operating defaults,
   all 12 months: `CargoExposure.value(base prices) == strip's
   eu_cargo/asia_cargo` to ≤ $0.01 (target: ~1e-9 like R1). Also pin
   the four analytic deltas (charter, VLSFO, TTF, JKM) as *derived from
   cash-flow quantities* — the delta IS the summed quantity on that
   factor, which becomes the single source of truth later.

**Acceptance:** new tests green; nothing else touched yet.

### B. Reimplement the vectorised repricer on cash flows — [R6.3]

**Goal:** `_vectorized_reprice()` stops containing route arithmetic.

1. Rebuild `_vectorized_reprice()` internals: evaluate the 12
   `CargoExposure` sets under the scenario price arrays (generic sum of
   `qty × factor-price` products). Outputs (`eu_cargo`, `asia_cargo`,
   `jkm_star`, `verdict_asia`, ...) keep their exact shapes and
   semantics — `historical_var`, backtest and stress consume them
   unchanged.
2. Delete the duplicated day-count/fuel formulas from `risk.py`
   (`analytic_deltas` switches to reading quantities off the cash-flow
   layer; `finite_difference_deltas` stays as the independent
   cross-check — it reprices via `model.strip` and must keep agreeing).
3. Minimal input-sanity guard at the boundary (see §4 R2 note).

**Acceptance:** frozen 64/64 (this is the increment most likely to break
it — the Gate 4 VaR fixtures and internal zero-shock consistency checks
must pass with the repricer now formula-free); R1 tests; full battery.
After this increment there are exactly TWO implementations of route
economics in the codebase (model.strip legacy + physical engine) instead
of three-plus.

### C. Physical-engine coefficients — [R6.1, R6.2]

**Goal:** risk can finally price what the decision page prices.

1. `physical_cargo_cashflows(D, tables, params, month_index, route,
   first_cargo_state) -> CargoExposure`: run `decision.py`'s physical
   route valuation ONCE, decompose its cash flows onto the factor set
   (delivered MMBtu × destination price, HH procurement, fuel tonnes ×
   VLSFO const, EUA tonnes × phase, heel × destination price, fees).
2. Parity characterisation (not equality — the bases legitimately
   differ): a test asserting `CargoExposure.value(base) ==` the decision
   page's physical route value to ≤ $0.01, per route, both parameter
   sets. Document the legacy-vs-physical base-value gap in the release
   note (it is a feature: same gap the Decision page already discloses).
3. Coefficient cache keyed on a stable hash of (`Params` fields, route,
   first_cargo_state) — NOT on `D`. Add a cache-hit test and a
   staleness test (changing any Params field must miss).
4. VaR page: "Value basis" radio — `Legacy strip (frozen)` /
   `Physical engine` — defaulting to legacy until R6 closes, reusing the
   existing basis-disclosure caption pattern from the Decision page.

**Acceptance:** full battery; physical-basis VaR renders on the page and
its zero-shock P&L is ≤ $0.01 by the same construction as R1.

### D. Feasible programme portfolio — [R6.7]

**Goal:** retire the infeasible 12-cargo default.

1. New portfolio `"programme"`: take the CURRENT optimiser output (e.g.
   Europe → Europe over the derived horizon), build one `CargoExposure`
   per leg with that leg's load month, sum under scenarios
   (hold-plan-fixed; §4 R4 note).
2. Make `"programme"` the VaR-page default. Keep `"12cargo"` selectable
   with its existing infeasibility warning for legacy comparison — the
   frozen fixtures depend on it, so it cannot be deleted.
3. Tests: programme zero-shock ≤ $0.01; programme VaR monotonicity
   sanity (2-leg programme VaR between 1× and 2× single-cargo VaR under
   identical shocks is NOT generally true — instead assert the exact
   sum-of-legs identity under zero shocks and shape/finiteness under
   real scenarios).

### E. New stochastic factors — [R6.5, R6.6]

**Goal:** the factor set stops silently excluding what the workbook can
support.

1. **Charter (R6.5a, feasible now):** log-return scenarios from
   `tables.charter` rate174, same lookback/method machinery; quantity =
   RT days per cargo (already on the cash-flow layer). Frozen fixtures:
   the legacy path must pass ZERO charter shocks — `build_scenarios`
   grows the factor additively, with the naive/legacy method emitting
   zeros for new factors. Add a fixture-freeze test proving Gate 4
   numbers are unchanged with the extended ScenarioSet.
2. **VLSFO / EUA (R6.5b, data-gated):** loaders tolerant of absent
   sheets (return None → factor excluded → UI shows "deterministic, no
   history in workbook"). Wire quantity sides now (they already exist as
   fuel tonnes / EUA tonnes on the cash flows). If/when the owner adds
   sheets (§5), the factors go live without code change.
3. **Basis (R6.6):** decision point — config proxy vol vs. exclusion.
   Either way: explicit UI disclosure line listing exactly which factors
   are stochastic vs deterministic in the current run. No silent
   omissions.

### F. Hedges derived from exposures — [R6.8, R6.9]

**Goal:** hedge legs come from the same object that prices the risk.

1. `hedge_legs_from_exposure(CargoExposure) -> DataFrame`: net quantity
   per (factor, month) IS the hedge. The VLSFO-swap tonnage that drifted
   in v2.4.1's blind spot becomes structurally incapable of drifting —
   it reads the same quantities the repricer prices. Existing hedge-leg
   test keeps passing, now tautologically.
2. Lot rounding via `CONTRACT_SPECS` (round-to-lot with residual shown),
   `verified: False` flags surfaced in the UI as the spec demands, and a
   transaction-cost haircut column (config, default conservative).
3. Keep the Section 7 mechanical hedge path byte-intact for the frozen
   fixture; the exposure-derived hedge renders alongside it.
4. Hedged-VaR wiring: hedge P&L evaluated on the SAME scenario arrays
   (short the netted quantities), replacing the bespoke
   `eu_hedge_pnl_vector`/`asia_hedge_pnl_vector` for the new basis while
   the legacy basis keeps the originals (fixture-pinned).

### G. Backtest on contract IDs + uncertainty — [R6.4, R6.10, R6.11]

1. **Contract-ID returns (R6.4):** label scenario returns by delivery
   month rather than continuation column; the roll_aligned method
   already approximates this — formalise, keep `naive` frozen for
   fixtures, make roll-safe the default for new bases.
2. **Same-cargo backtest (R6.10):** track one delivery month's cargo
   value through time using contract-ID prices and the (cached, price-
   independent) coefficients — this is where the §2 insight pays off:
   ~2,000 revaluations are array lookups.
3. **VaR/ES uncertainty (R6.11):** bootstrap resample the scenario set
   (e.g. 1,000 resamples), report 90% bands beside VaR95/99 and ES95/99
   on the page, plus effective-sample-size note for the ES99 tail.

### H. Close-out

1. Full battery + fresh evidence under `test_results/v2.6/`.
2. Release note `docs/RISK_REBUILD.md` (cause, architecture, base-value
   gap characterisation, factor coverage table, before/after VaR).
3. Register: R6.1–R6.11 statuses, completion report, next action.
4. README + IMPLEMENTATION_STATUS + MODEL_ASSUMPTIONS updated from the
   same state; commit, tag `v2.6-risk-rebuild`, push.

## 7. Sequencing and sizing

A → B → C are strictly ordered (each depends on the last). D–F depend on
C. G depends on E (factor set) but G.1 can start after B. Rough relative
weight: B and C are the heavy increments (B carries the frozen-fixture
risk, C carries the physical-decomposition subtlety — heel, queue fuel,
reliquefaction, ETS phase all have to land on the right factor with the
right sign). A, D, F are small. E depends on the owner's data decision
for its second half. G.3 is small; G.1/G.2 medium.

Suggested first session: A complete + B started (pinning tests written
and red against a stub, then B lands them green).

## 8. Decisions taken in this plan (challenge before A starts)

1. Programme VaR is hold-plan-fixed (no per-scenario re-optimisation)
   until R4 exists.
2. No discounting anywhere in R6 (matches the whole model today; R5's
   job).
3. `"12cargo"` and the Section 7 mechanical hedge survive as
   fixture-pinned legacy paths — nothing frozen is deleted.
4. Charter becomes a stochastic factor in R6.5a; VLSFO/EUA ship
   data-gated; basis ships disclosed-or-excluded, not silently proxied.
5. The legacy-vs-physical base-value difference is characterised and
   disclosed, not reconciled away — same policy as the Decision page.
