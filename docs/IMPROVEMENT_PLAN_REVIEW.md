# Review of IMPROVEMENT_PLAN.md

> **Historical document:** this reviewed plan v1.0; its accepted points
> were folded into plan v1.2 (the version `docs/IMPROVEMENT_PLAN.md` now
> carries), so nothing here is open feedback. Kept as provenance for why
> the plan says what it says.

**Document reviewed:** IMPROVEMENT_PLAN.md (plan version 1.0, 15 July 2026)
**Review date:** 15 July 2026
**Method:** every "current issue" claim in the plan was checked against the actual
code (`model.py`, `risk.py`, `data.py`, `app.py`, `tests/test_model.py`); the
regression suite was executed live; the plan was then reviewed for internal
consistency.

**Overall verdict:** the plan's diagnosis of the codebase is accurate throughout —
no factual errors were found in its descriptions of current behaviour. However,
it contains four internal inconsistencies and misses several gaps, listed below.
These should be corrected in the plan document before Phase 0 begins.

---

## 1. Claims verified against the code (all check out)

| Plan claim | Verification |
|---|---|
| 64 regression checks exist and pass | Confirmed by running the suite: **64/64 pass, all gates green** (`LNG_HISTORY_XLSX="../LNG history.xlsx" python tests/test_model.py`, 15-Jul-2026, post-waterfall commit `d7f8c07`) |
| `risk.py` duplicates route economics | Confirmed: `_vectorized_reprice` (risk.py:492-566) re-derives `eu_margin`/`asia_margin` with separately written numpy formulas parallel to `model.strip()` |
| ETS tonnes are a static input | Confirmed: `co2_eu_ets_tonnes = 4425.9` (model.py:94), hand-derived constant, not linked to fuel burn |
| Congestion charged at full propulsion rates | Confirmed: congestion adds 8 days to `asia_rt_days`, lengthening both legs at full laden/ballast fuel rates; the code's own comment calls it "a documented overstatement" (model.py:24-26) |
| Margin-per-vessel-day verdict is the sole recommendation output | Confirmed: `verdict`/`gap`/`jkm_star` (model.py:287-289) is the only decision output |
| Tests require the private vendor workbook | Confirmed: `tests/test_model.py` exits fatally without `LNG_HISTORY_XLSX` or a conventional path; no synthetic fixture exists |
| NG/TTF month+1 mapping, JKM day-15 roll | Confirmed in `contract_calendar()` (model.py:141-147) |
| Hedge module hedges model-internal indices | Confirmed: `eu_hedge_pnl_vector`/`asia_hedge_pnl_vector` cancel the same indices used in valuation; the 0.4071% hedged-residual ratio in the suite is model-internal, not physical hedge effectiveness |

## 2. Findings that are *stronger* than the plan states

### 2.1 The physically-infeasible 12-cargo portfolio is the DEFAULT

Practices-to-Stop #14 reads as if the nominal 12-cargo strip were one mode among
several. In fact `historical_var(..., portfolio="12cargo")` is the **default
argument** (risk.py:599), and it is the portfolio used by both `run_stress_tests`
(risk.py:762-806) and `backtest_var` (risk.py:813). The headline VaR, stress and
backtest numbers users see today all assume 12 cargoes held concurrently by what
is implicitly one vessel. This raises the urgency of Improvement 10 step 7.

### 2.2 Backtest has a concrete month-rollover bug

`backtest_var` (risk.py:824-834) compares `model.strip(D)` and `model.strip(D_next)`
by **row position** (month index 0-11). Because `contract_calendar()` anchors
row 0 to first-of-month(D)+1, any D/D_next pair straddling a calendar-month
boundary silently compares *different physical delivery months* on each side.
The plan's acceptance criterion "backtest follows the same cargo across dates"
(Improvement 10 step 8) is not merely an abstraction improvement — it fixes a
live defect on every month-roll date in the backtest window.

### 2.3 FX extrapolation bites INSIDE the current 12-month strip

Improvement 6 step 7 frames linear FX extrapolation as a beyond-one-year issue.
Actually M12's mid-month sits ~12.2–12.7 months from the curve date, and
`fx_curve` (model.py:159-175) extrapolates linearly past the 1Y outright for
t > 12. **The last month of every strip shown today already uses extrapolated
FX.** This should be promoted from "approximation to replace" to "known live
defect".

### 2.4 Roll-aligned returns already exist but are dead code

`build_scenarios(method="roll_aligned")` is fully implemented (risk.py:453-456,
474-489) but defaults OFF and is not called by any UI or test path. Every VaR,
stress and backtest number today uses naive continuation-through-rolls returns
(Practices-to-Stop #13). Improvement 10 step 3 ("default to roll-aligned
scenarios") is partially a one-line switch plus re-fixturing, not new
development — worth noting in the phase estimate.

## 3. Internal inconsistencies in the plan

### 3.1 Worked-example durations don't match the re-baselined model

Improvement 2 and Section 10 are built on "a 27-day route twice in 54 days"
versus "a 53-day route once". The actual model constants (re-baselined
13-Jul-2026) are:

- Europe RT: **25.9402 d** (not 27)
- Asia RT base: **46.7436 d** (not 53)
- Asia RT congestion: **54.7436 d**

Under congestion, one Asia voyage does **not** fit the 54-day horizon
(54.74 > 54), flipping the flagship example's answer depending on which RT is
meant. Section 10's regression fixtures ("insufficient time for cargo 2") must
be written against the real constants or they will encode results the model
cannot produce.

### 3.2 Improvement 2 and Improvement 8 disagree on discounting

Improvement 2's objective discounts whole cargo values at e^(−r·t_i) with V₁
undiscounted; Improvement 8 requires every individual cash flow discounted on
its own payment date. If the V_i are already per-cash-flow NPVs, the extra
e^(−r·t_i) wrapper double-discounts. The plan should state explicitly that
Improvement 8 is authoritative and Improvement 2's formula is shorthand for
"sum of date-discounted cash flows per cargo".

### 3.3 Section 10 quietly reorders the phases

The phase sequence puts the physical engine (Phase 2) before the optimiser
(Phase 3), but the "immediate next task" is a mini-optimiser (Phase 1 plus a
slice of Phase 3) built on the **old** physics, with the physical rebuild
afterward. Thin-slice-first is defensible, but the plan should say explicitly
that the mini-programme's voyage durations, BOG and fuel numbers will all need
re-derivation after Phase 2, so its fixtures are not treated as stable.

### 3.4 Priority matrix overloads P0

Five items share P0, including both the trivial baseline freeze (hours) and two
multi-month rebuilds (physical engine, discrete optimiser). Marking them equal
obscures that Phase 0 should ship immediately while the rebuilds need scoping
and resourcing decisions first.

## 4. Gaps the plan does not address

### 4.1 "One engine" vs. VaR/backtest performance (largest open design question)

Design Principle 2.3 demands one valuation engine for deterministic and risk
paths. But `risk.py`'s viability depends on `_vectorized_reprice`, which exists
because — per its own docstring (risk.py:497-499) — a pure-Python `model.strip()`
call per scenario would be "~500x (or, for the backtest tab, ~50,000x) slower".
The default backtest alone is 60 × 500 = 30,000 full repricings. A segment-level
physical engine (Phase 2) plus a DP/MILP programme optimiser (Phase 3) will be
far harder to numpy-batch than the current closed-form linear model. Phase 5
needs a stated strategy, e.g.:

- a cached physical-result layer that price scenarios perturb cheaply (prices
  don't change voyage physics, only revenue/fuel-price lines);
- vectorising only the price-dependent tail of the calculation;
- or an explicit decision to accept much slower risk runs.

Without this, Phase 5 will either fail its own no-duplication acceptance
criterion or make the risk pages unusably slow.

### 4.2 VLSFO has no forward curve in any phase

`vlsfo_price = 530 $/t`, "static for ALL dates" (model.py:81), used by both the
deterministic model and every risk scenario. Improvement 10 adds VLSFO as a risk
*factor* and stress presets *shock* it, but no improvement gives the **base
valuation** a bunker forward curve — a cargo loading in 12 months prices its
fuel at today's flat number. Improvement 6 (contract calendars) is the natural
home.

### 4.3 EUA has the same static-price gap

`eua_price = 70 EUR/t`, "static, unverified" (model.py:89). Improvement 5 makes
ETS *tonnes* dynamic and Improvement 10 adds EUA as a risk factor, but the base
valuation never gets an EUA forward curve — a Dec-2027 cargo prices its ETS
liability at today's spot EUA. Same remedy as 4.2.

### 4.4 Duplicate unversioned copy of the whole app (governance)

A directory named `lng_netback_app/` sits next to this repo containing a
byte-identical copy of all six source files — manually synced, not under git.
Two problems:

1. It is the plan's own "duplicate logic drift" warning at whole-project scale;
   the copies will diverge the first time someone edits the wrong one.
2. Section 3 proposes `lng_netback_app/` as the **new package root name**,
   colliding with this existing directory.

Phase 0 should add: archive or delete the sibling copy (or make it the repo),
and pick a non-colliding package name.

### 4.5 Phase 0 status

No git tags exist yet (`git tag -l` is empty) — the `v2.2-renewal-rate` tag,
workbook hash record, and frozen 64/64 output are all genuinely outstanding.
The 64/64 green run recorded above (workbook `../LNG history.xlsx`, master dates
2017-07-27 to 2026-07-08, 2335 dates) can serve as the freeze reference.

## 5. Minor

- Three files named `risk.py` will exist in the target tree (`pages/risk.py`,
  `lng_netback/risk.py`, plus the legacy top-level during migration) — cosmetic,
  but worth renaming the pages one (e.g. `pages/risk_page.py`).
- The plan's test-count claim ("64 regression checks") maps to 33 `check()` call
  sites, several inside fixture loops — the runtime count is exactly 64, so the
  claim is correct as stated.

---

# Addendum: review of plan version 1.1 (15 July 2026)

Plan version 1.1 was checked against its own incorporation record (Section 1.1
of the plan), the body text, and two new live checks. **All 12 first-round
findings were genuinely incorporated** — the new benchmark arithmetic is
correct (2 × 25.9402 = 51.8804 ≤ 52; 46.7436 ≤ 52; 54.7436 ≤ 55; 54.7436 > 54),
the rewritten principle 2.3 resolves the one-engine/performance tension, and
Improvements 2 and 8 now agree on single discounting.

Seven second-round findings, in severity order:

## A1. Improvement 14 §D still carried the stale duration set

"54 days permits two 27-day cycles" / "54 days permits one 53-day cycle"
survived the v1.1 edit. The second line directly contradicted Section 10's new
"54 days proves congested Asia is infeasible" benchmark. The programme-test
list must use the 52/55/54 benchmark set.

## A2. Improvement 13 step 9 preset was stale

The "54-day two-NWE-versus-one-Asia example" preset now denotes the
*infeasibility* case under the real constants. The comparison presets should be
52-day (base Asia) and 55-day (congested Asia).

## A3. Activating roll-aligned by default breaks the legacy 64/64 unless scoped

Verified live: the legacy suite pins `method="naive"` only for the
12-cargo/spread fixtures (an explicit `scen` built at test_model.py:203); the
single and hedged VaR fixtures (test_model.py:206-208) call `historical_var`
with no method and no scenario set, inheriting the **function** default.
Changing the function default in `risk.py` breaks GATE 4a; only the
application/UI default may change, with the legacy suite pinned to `naive`.

## A4. A data gap partially blocks roll-aligned mode

Roll-aligned construction requires a 14th strip column. Verified live: JKM
`c14` is missing for **51 master dates, 2022-10-31 through 2023-01-09**. At the
current default (D = 2026-07-08, lookback 500, window starts 2024-08-06) the
build is clean — zero NaN returns. But any curve date from roughly November
2022 through late 2024 pulls the gap into a 500-day lookback, producing NaN JKM
returns that silently poison VaR. `build_scenarios` needs a hard NaN guard or a
visible fallback before roll-aligned becomes the default. The priority-matrix
label "low implementation effort" understated this.

## A5. The 52-day benchmark is knife-edge

Two Europe cycles fit the 52-day horizon by only 0.1196 days (~2.9 hours).
Acceptable for a provisional fixture, but the test must assert the remaining
margin explicitly so a Phase 2 duration re-derivation fails loudly rather than
silently flipping the fixture.

## A6. Priority matrix split Improvement 6 across tiers without a sequencing note

"VLSFO and EUA forward curves" is P1 but "contract calendars" is P2, yet the
curves are implemented inside the Improvement 6 contract-calendar workstream.
The curve-loading/tenor subset must be brought forward with the P1 work.

## A7. Phase 0 froze against a commit hash instead of the tag

Phase 0 step 5 referenced commit `d7f8c07`, but P0A itself adds commits
(warnings, defaults, fixture pinning). The `v2.2-renewal-rate` tag, not a
hash, should be the freeze reference.

**Resolution:** all seven findings were applied to the plan as version 1.2 on
15 July 2026.

---

*Reviewed by Claude Code against commit `d7f8c07` (waterfall/Sankey charts),
workbook `LNG history.xlsx` (master dates through 2026-07-08). Addendum
verified with live runs of the regression suite, `build_scenarios`, and
workbook column-completeness checks.*
