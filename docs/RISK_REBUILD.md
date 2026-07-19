# Risk and hedging rebuild (R6)

**Release:** `v2.6-risk-rebuild`
**Dates:** 17–19 July 2026
**Plan:** `docs/R6_RISK_REBUILD_PLAN.md`
**Register:** `docs/LNG_NETBACK_PROGRESS_TRACEABILITY_2026-07-17.md` (R6.1–R6.11)

## Why this release

`risk.py` was an independently maintained *duplicate* of the legacy 12-month
route economics. v2.4.1 made it numerically equal to `model.strip()`, but
equality-by-duplication is exactly what let three copies of the same
route-fuel formula drift apart: the v2.4.1 zero-shock defect, and the
hedge-leg VLSFO tonnage found in post-release review. This rebuild replaces
"duplicate the formula, then prove equality" with **"generate the exposures
once, evaluate them everywhere."**

## Architecture

The key insight: every physical quantity in the model — delivered MMBtu,
fuel tonnes by leg, EUA tonnes, boil-off, heel — is **price-independent**.
Prices enter valuation only through linear and bilinear terms. So:

```
Layer 1  physical/contractual quantities   (Params-only; cached)
Layer 2  canonical price-dependent cash flows   (factor tuple, month, quantity)
Layer 3  generic scenario evaluation       (Σ qty · Π(prices) — no route math)
```

`cashflows.py` is Layer 1/2 (`CashFlow` carries a *tuple* of `RiskFactor`s
so a bilinear `(TTF, FX)` term is first-class, and `(EUA, FX)` needs no
schema change when EUA history arrives). `risk.py` is Layer 3. The route
formula now exists in exactly **one** place — `cashflows.legacy_cargo_quantities`
/ `physical_cargo_quantities` — beside the frozen `model.strip()` it is
pinned against, instead of the three-plus drifting copies before.

Because quantities are `Params`-only, the physical engine runs **once per
parameter set**, not per scenario or per date. That is why physical-basis
VaR is *faster* than the old duplicate, and why the same-cargo backtest
reprices a whole historical window with the voyage engine running exactly
once (cache misses=1, hits=N).

## Increments

| | Commit | Delivered |
|---|---|---|
| **A** | `80bd7e0` | Canonical cash-flow layer (`cashflows.py`); legacy decomposition pinned to `model.strip()` (~1e-8) |
| **B** | `22ca9cc` | `risk._vectorized_reprice()` / `analytic_deltas()` consume the layer; ~40 lines of duplicated route arithmetic deleted |
| **C** | `b5ed4bc` | Physical-engine decomposition from the voyage ledger; Params-hash quantity cache; VaR-page **Value basis** toggle; **exposure follows first-cargo state** (sunk procurement → zero HH risk) |
| **D** | `0605525` | **Committed-programme** portfolio (the optimiser's feasible plan) is the physical-basis VaR default, retiring the infeasible 12-cargo strip |
| **E** | `5f22aca` | New factors, honestly: charter stress rows + optional labelled overlay; VLSFO/EUA data-gated loaders + live-factor path; factor-coverage disclosure |
| **F** | `f34f2d8` | Hedges derived from exposures (`hedge_legs_from_exposure`); lots + tx-cost; physical hedged-VaR — hedge sizing can no longer drift from what's priced |
| **G** | `482b22f` | Contract-ID (delivery-month) scenario labelling; same-cargo-through-time backtest on cached coefficients; bootstrap VaR/ES uncertainty bands |

## What changed for the user

**VaR & stress page** — a **Value basis** toggle (Legacy strip / Physical
engine, legacy default); under the physical basis, portfolios are Committed
programme (default) / single / hedged residual / spread, with a
first-cargo-state selector; an optional charter overlay (off by default); a
factor-coverage disclosure line; and **bootstrap 90% uncertainty bands**
beside every VaR/ES point estimate.

**Hedging page** — an exposure-derived hedge table (both bases) that reads
its leg sizing straight off the priced exposure.

The **legacy basis is unchanged and stays the default everywhere** —
nothing an existing workflow relies on moved.

## Base-value gaps (characterised, not reconciled)

The physical basis prices what the Decision page prices, which differs from
the legacy strip by design (same gap the Decision page already discloses):

| Params | Route | Physical vs legacy | Driver |
|---|---|---|---|
| legacy defaults | Europe | -0.075% | per-segment ETS scope |
| operating | Europe | -1.70% | heel + per-segment ETS |
| operating | Asia | -1.08% | heel + ETS |
| legacy defaults | Asia | ~$1 | (base RT, zero heel) |

Programme VaR95 at operating defaults ≈ **-$5.88M** vs single-Europe
**-$3.01M** (SD ratio ~1.94× — two adjacent-month Europe cargoes under
near-perfect correlation, slightly sub-additive, as expected).

## Factor coverage (disclosed each run by `risk.factor_coverage_line()`)

| Factor | Treatment |
|---|---|
| HH / TTF / JKM / FX | stochastic (daily joint-historical) |
| Charter | deterministic in HS-VaR (weekly data); stress rows + optional independent overlay |
| VLSFO / EUA | deterministic today; data-gated — go live if `LNG history.xlsx` gains those sheets |
| NWE / JKM physical basis | **excluded**, disclosed (no history; not silently proxied) |

## Structural guarantees

- **Drift-proof hedges** — the VLSFO hedge tonnage is read off the same
  `quantity_on()` the repricer prices (pinned to `physical.run_voyage()`'s
  real fuel *and* asserted to differ from the legacy estimate), so the
  drift class that started this rebuild cannot recur.
- **Frozen 64/64 held at every commit.** `model.strip()`, the Gate-4 VaR
  fixtures, the `naive` scenario path, and the legacy Section-7 mechanical
  hedge are byte-unchanged throughout.
- **Uncertainty is visible.** The ES99 band is ~3.7× wider than VaR95's and
  discloses its effective tail count (~5 of 500 scenarios).

## Not in scope (still open)

This release rebuilds risk/hedging. It does **not** deliver the other
deferred correctness/feature releases the critical review flagged: **R2**
(physical-ledger validation — `run_voyage()` still accepts impossible
ledgers), **R3** (year-aware ETS gas scope), **R4/R5** (commercial outside
options, dated cash flows), **R7** (production controls). The overlapping
10-day VaR and the app's other backtest expander remain legacy-basis-only.
See the register for the full R2–R7 status.

## Validation

Independently re-verified at each increment's review (with the real
workbook): **frozen legacy 64/64**, full **pytest 415/415**, CI-simulation
pure subset **207 passed** (workbook-gated tests self-skip), **17-check
Streamlit smoke**.

## Process note

Each increment was implemented by a Sonnet subagent under a no-commit
brief, then independently reviewed before commit — for F and G via a 6-lens
multi-agent adversarial review workflow (hedge signs, bilinear/FX netting,
frozen-path integrity, statistics, test rigor), each finding adversarially
verified. Real findings were fixed before merge: F's FX tx-cost tier
mis-keying and a vacuous sunk-cost test; G's same-cargo var-leg
test-coverage gap. The reviewer re-ran the full battery from scratch for
every increment.
