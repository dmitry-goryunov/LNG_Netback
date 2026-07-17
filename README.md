# LNG Forward Netback

Streamlit implementation of a per-load-month netback / diversion model
(Europe vs Asia) built on `LNG history.xlsx`: a 36-month forward strip,
explicit decision modes (post-lift diversion, pre-lift cargo, discrete
one-vessel programme) valued by a segment-level physical voyage engine
(fuel/BOG/heel/emissions/EU-ETS), a Margrabe JKM-vs-TTF diversion option,
plus sensitivities, hedging and historical-simulation VaR modules.

**Where to look:**
- `docs/IMPLEMENTATION_STATUS.md` -- the live project tracker (what is
  built, with measured impacts, entry per increment). Git history carries
  the same record at commit granularity.
- `docs/MODEL_ASSUMPTIONS.md` -- current operating assumptions (17 kn,
  1.5 d loading/unloading, 2% heel, cube-law fuel) vs the frozen legacy
  spec case (19.5 kn / 0 / 5) that the 64-check regression suite pins.
- `docs/IMPROVEMENT_PLAN.md` -- the master roadmap;
  `docs/PHASE2_PLAN.md` -- the (complete) physical-engine phase, whose
  Section 10 holds the open parameter decisions.
- `docs/LNG_Netback_Streamlit_Spec.md` -- the frozen baseline definition
  (not a manual for current behavior; see its scope banner).
- Frozen v2.2 baseline: tag `v2.2-renewal-rate`, `docs/BASELINE_RECORD.md`.

## Layout

```
app.py                 page routing, sidebar (date picker + parameter editor incl. vessel schedule)
data.py                loader + validation (HH/TTF/JKM/FX incl. 2Y-10Y tenors/charter/volatilities)
model.py               legacy strip(D, tables, params) as pure functions; speed/fuel/geometry helpers
decision.py            decision modes, cost-inclusion policy, physical route valuation, programme optimiser
physical.py            segment-level voyage engine (BOG/reliquefaction/heel/queue mass balance)
emissions.py           CO2/CH4/CO2e per segment, EU-ETS scope and cost, legacy-ETS derivation
spread_option.py       Margrabe JKM-vs-TTF exchange option (intrinsic/extrinsic per strip month)
risk.py                sensitivities, hedging, VaR, stress, backtest (legacy 12-month basis)
tests/test_model.py                     frozen legacy 64-check suite (no pytest; needs the workbook)
tests/test_decision_programme.py        decision modes, programme optimiser, physical wiring (pytest)
tests/test_physical_engine.py           segment mass-balance unit tests
tests/test_physical_legacy_equivalence.py  proof the engine reproduces the legacy constants
tests/test_emissions.py                 combustion/ETS factor tests
tests/test_extended_strip.py            36-month strip + multi-tenor FX tests
tests/test_spread_option.py             Margrabe formula + tenor-alignment tests
tests/test_operating_assumptions.py     17-kn operating case, loading/heel/speed-helper guards
tests/test_risk_containment.py          roll-aligned scenario / backtest containment tests
tests/app_smoke_check.py                headless five-page Streamlit smoke check
.github/workflows/tests.yml             CI: pure-test subset on every push (workbook tests self-skip)
requirements.txt
```

`model.py` and `risk.py` have no Streamlit import, so they (and the tests)
run head-less with plain `python3`.

## Run

```bash
pip install -r requirements.txt
export LNG_HISTORY_XLSX="/path/to/LNG history.xlsx"     # see Data file below
streamlit run app.py
```

## Data file

The loader looks for the workbook in this order (`data.py::default_data_path`):

1. `$LNG_HISTORY_XLSX` environment variable (exact path to `LNG history.xlsx`)
2. `./LNG history.xlsx` or `./data/LNG history.xlsx` (relative to the working directory)

If none of those resolve, the app falls back to an `st.file_uploader` in the
sidebar so it still runs without the Drive path mounted (spec Section 1.7).
The cache is keyed on `(path, mtime)`, so re-saving the workbook and
refreshing the page picks up new data automatically when loaded from a path;
uploads are cached per Streamlit session.

This workbook is not included in this repository (vendor-sourced Henry Hub /
TTF / JKM / EUR-USD / charter series; see `docs/` for the governing spec).
Supply your own via the env var, relative path, or the sidebar uploader.

## Tests

Frozen legacy suite (no pytest dependency):

```bash
export LNG_HISTORY_XLSX="/path/to/LNG history.xlsx"
python3 tests/test_model.py
```

Full pytest suite (decision modes, physical engine, emissions, spread
option, operating assumptions, risk containment, cash-flow layer -- 163
tests; ~64 of them need the workbook and self-skip without it):

```bash
export LNG_HISTORY_XLSX="/path/to/LNG history.xlsx"
python -m pytest tests/ --ignore=tests/test_model.py
```

Headless Streamlit smoke check (all five pages):

```bash
python tests/app_smoke_check.py
```

CI (`.github/workflows/tests.yml`) runs the pure-test subset (99 tests)
plus byte-compilation on every push; the workbook-backed tests and the
frozen 64/64 suite run locally only, since the workbook is proprietary
and not committed.

`tests/test_model.py` runs head-less against the real workbook (no synthetic
data, no pytest dependency) and prints a PASS/FAIL line per check plus a
summary. Encodes:

- Gate 1 -- master date list (2,335 dates, 2017-07-27 to 2026-07-08) and FX x10 correction sanity.
- Gate 2 -- all four Section 5 regression fixtures (TTF $/MMBtu, EU/Asia $/day, JKM*, gap, verdict), plus the Step 7 structural charter-invariance assertion.
- Gate 3 -- Section 6 sensitivities: analytic deltas vs finite-difference cross-check (agree to 1e-6 relative), plus a printed comparison against the spec's own numbers.
- Gate 4 -- Section 7 hedge-effectiveness fixture and Section 8 VaR fixtures (12-cargo strip, M1 diversion spread), 500 naive-return scenarios, +-1%.
- An internal consistency check: the numpy-vectorised repricer risk.py uses for VaR/backtest performance matches `model.strip` exactly on a zero-shock scenario.

As of the 08-Jul-2026 workbook: **64/64 checks pass, zero failures**
(v2.2 re-baselined build; earlier builds passed their own gates against the
pre-rebaseline spec). These 64 checks are frozen exactly as-is under the
`v2.2-renewal-rate` tag and must remain green throughout the migration
described in `docs/IMPROVEMENT_PLAN.md`.


## v2.4.1 risk-equivalence correction (17-Jul-2026)

The vectorised risk repricer and analytic charter/VLSFO sensitivities now include loading time and loading-port fuel consistently with `model.strip()`. Under the 17-kn operating defaults, zero shocks now produce zero P&L for Europe, Asia, the diversion spread and the legacy 12-cargo portfolio. The frozen 64-check suite remains unchanged; the full pytest suite is 144/144. See `docs/RISK_EQUIVALENCE_FIX.md`. This fixes base-value equivalence only; the risk module remains on the legacy 12-month economics and is not yet programme-based.

## v2.3-phase1: decision modes and vessel programme (15-Jul-2026)

Per `docs/IMPROVEMENT_PLAN.md` Phase 0 ("Increment A: baseline freeze and
live-defect containment") and the provisional thin slice ("Increment B:
provisional decision-state thin slice"):

- Added `decision.py` with four `DecisionMode`s (renewal-rate screen,
  post-lift diversion, pre-lift cargo, vessel programme), an explicit
  procurement/loading cost-inclusion policy, and a deterministic discrete
  one-vessel programme optimiser that schedules only whole voyages within a
  horizon (no fractional cargoes) and prices each later cargo from its own
  forward-strip month.
- Added a **Decision** page (now the app's default page) exposing all four
  modes.
- Added an explicit FX-tenor warning: any strip row whose representative
  mid-month lies beyond the 1-year FX outright (`model.fx_extrapolated_rows`)
  is now surfaced in the UI rather than silently linearly extrapolated.
- Added a hard c1..c14 completeness guard to `risk.build_scenarios(...,
  method="roll_aligned")`, and changed the **Streamlit application default**
  to roll-aligned scenarios. The `risk.py` function default remains `naive`
  and the frozen 64/64 legacy fixtures still pin `method="naive"` explicitly,
  so the legacy suite is unaffected.
- Added an interim backtest containment fix (`risk.backtest_var`) that skips
  NG/TTF or JKM roll pairs instead of comparing different physical delivery
  months across a month boundary.
- Added a UI warning that the legacy 12-cargo VaR/stress/backtest portfolio
  is not a physically feasible one-vessel programme.

This build's programme optimiser still ran on the legacy monthly-strip
voyage physics at release. **Since superseded:** `IMPROVEMENT_PLAN.md`
Phase 2 (the segment-level physical rebuild) is complete, and the three
non-screen decision modes -- including every programme value -- are now
valued by the physical engine (`physical.py`/`emissions.py` via
`decision.py`); the renewal-rate screen alone stays on the legacy formula
by design. Turnaround days, heel and a first-class speed knob followed.
See `docs/IMPLEMENTATION_STATUS.md` for the increment-by-increment record.

## v2.2 re-baseline (13-Jul-2026, after the notes.md external review)

Voyage days are now DERIVED from distance / speed on both routes instead of
rounded: Europe legs 4,900 nm / 468 nm-per-day = 10.4701 d (RT 25.9402, was
25), Asia legs 9,300 nm + 1 d canal = 20.8718 d (RT 46.7436, was 47);
congestion adds 4 waiting d per leg (RT 54.7436, was 55 -- waiting days are
charged at full propulsion rates, a documented overstatement). CO2 in ETS
scope re-derived from the fuel balance: 4,426 t (was 4,243). Consequences:
M1 breakeven on the 08-Jul curves 22.90 -> 22.25; the procurement
sensitivity coefficient is now ~ -0.94 per $1 of HH (was -1.03 at the old
voyage times). All fixtures in tests/test_model.py were re-baselined
accordingly and are green. Additional review items adopted: negative-margin
warning on the Netback page (verdicts are relative, not lift/don't-lift),
model-internal framing of the hedge-effectiveness numbers, conditional
statement of charter invariance, FuelEU noted as in force since 2025, and
the VaR-window wording fixed (the 500-day set, 06-Aug-2024 to 07-Jul-2026,
contains no 2021-22 observations; the crisis enters via stress replays).

## Known deviations from the spec text (found during build, not silently patched)

1. **Section 6's Asia-RT baseline was stale** (23.18 -> 25.81 belonged to the
   diversion doc's illustrative case at TTF 16.04 / charter 80k, not to the
   08-Jul-2026 fixture context). CONFIRMED AND CORRECTED in the spec on
   13-Jul-2026 by the reviewing session: the row now reads JKM* 22.90 ->
   25.59 (+2.69) under the symmetric-legs convention below, and the test
   suite asserts those absolute values.
2. **Step 7.1 overstates JKM* invariance.** It claims JKM* is invariant to
   "the charter rate and to procurement cost." Charter invariance holds
   exactly (verified analytically and numerically, and it's the one Section 6
   flags: "JKM* unchanged"). Procurement-cost (HH-driven) invariance does
   **not** hold for the Step 6 formula as given: `eu_day` normalises Europe's
   margin by Europe's own RT (25d), then JKM* rescales that by Asia's RT
   (47d). Charter is a pure day-rate cost on both legs, so the RT cancels
   perfectly; procurement cost is a flat, non-RT-scaled cost, so it does not
   cancel under that RT mismatch. Confirmed numerically: bumping HH by
   +$1.00/MMBtu moves M1 JKM* from 22.90 to 21.87. Implemented Step 6
   literally (it's what reproduces every fixture); only charter-invariance is
   asserted in tests, with the HH non-invariance called out explicitly as a
   documented finding rather than silently asserted away.
3. **FX shock convention for Section 6/8** ("EURUSD +0.01" and the VaR FX
   scenarios) is a *parallel* shift: spot and the corrected 6M/1Y outrights
   all move by the same absolute delta (equivalent to shifting the raw
   stored 6M/1Y points by the spot change and re-applying the x10
   correction -- algebraically identical, see `model.fx_curve`'s docstring).
   A spot-only bump reproduces roughly 80% of the spec's stated EURUSD
   sensitivity; the parallel-shift convention reproduces it to within 0.001%.
4. **Europe/Asia hedge P&L, "base-FX conversion" convention** (Section 7):
   the short-TTF leg's EUR P&L is converted to USD at the curve date's *base*
   FX rate, not the scenario's shocked FX rate. This was not stated
   explicitly in the spec; it was reverse-engineered by matching the Section
   7 hedge-effectiveness fixture (converting at the scenario FX rate instead
   understates the residual VaR by roughly 5x). Documented in
   `risk.eu_hedge_pnl_vector`'s docstring.
5. **Asia-RT congestion split -- REVERSED ON REVIEW (13-Jul-2026)**: the
   build originally held the laden leg at 21 d and pushed extra congestion
   days into ballast, because that reproduced the spec's (stale) +2.63
   delta. The reference workbook, however, derives laden days symmetrically
   (`Assumptions!B19 = (B20-B15)/2`), so congestion lengthens BOTH legs
   (laden 25 d at RT 55: more boil-off and laden fuel). `model.Params` now
   derives `asia_laden_days = (asia_rt_days - asia_port_days)/2` by default,
   matching the workbook; `asia_laden_days_override` pins the laden leg for
   anyone who wants the waiting-as-idle convention (also exposed as a
   checkbox in the sidebar). The +2.63 match of the original convention was
   a coincidence of compensating differences.
6. **VaR `sd` convention**: population standard deviation (`ddof=0`) was used
   for the P&L standard deviation, matching the spec's fixture numbers
   marginally better than the sample convention (`ddof=1`) across the
   Section 7/8 fixtures; both are within the +-1% gate either way.
7. **US netbacks / US transport** sheets are parsed by `data.py`
   (`load_us_netbacks`, `load_us_transport`) per the spec's documented
   layout, but are not wired into any UI page -- the spec explicitly scopes
   them as "sanity reference" / cross-check only and no page in Section 4
   calls for them.

## Roll-alignment refinement (Section 8)

`risk.build_scenarios(..., method="roll_aligned")` implements the
delivery-month-aligned return calculation described as the "required
refinement before production use": on an NG/TTF month-boundary or a JKM
15th/16th boundary, contract index *k*'s return compares today's price
against *yesterday's* index *k+1* (same underlying delivery month) rather
than yesterday's index *k*. It's wired into the VaR & stress page as a
checkbox, **default on as of v2.3-phase1** (the `risk.py` function default
and the frozen legacy fixtures remain `naive`; see "v2.3-phase1" above).
Roll-aligned construction requires complete c1..c14 history and raises
`ValueError` explicitly rather than propagating NaNs if a lookback window
hits the 2022-10-31..2023-01-09 JKM `c14` gap. Per the spec, the fixtures in
Sections 7/8 are reproduced with the naive method (`method="naive"`) and are
not gated on the roll-aligned variant. Note the alignment uses calendar
month-boundaries for NG/TTF, consistent with the model's own front-month
convention; real NG/TTF expiries fall 2-3 business days before month-end, so
+-2-3 day artefacts around expiry remain even in this mode.

## Out of scope (v1, per spec Section 9) -- updated

Originally: Margrabe option valuation, forward-curve-consistent
multi-voyage optimisation, Suez/Cape routing, FuelEU, live data feeds.
**Since built:** Margrabe valuation of the JKM-vs-TTF diversion
optionality (`spread_option.py`, Forward-strip page). Still out of
scope: multi-vessel/multi-voyage curve-consistent optimisation,
Suez/Cape routing, FuelEU pricing (explicitly marked `NOT_PRICED` in
`emissions.py`), and live data feeds (the xlsx is the only source;
refresh by re-saving it and letting the mtime-keyed cache pick it up).
