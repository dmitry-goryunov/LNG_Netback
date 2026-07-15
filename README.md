# LNG Forward Netback

Streamlit implementation of `LNG_Netback_Streamlit_Spec.md`: a per-load-month
netback / diversion model (Europe vs Asia) built on `LNG history.xlsx`, plus
sensitivities, hedging and historical-simulation VaR modules.

## Layout

```
lng_netback_app/
  app.py             page routing, sidebar (date picker + Step 5 parameter editor)
  data.py            Section 1 loader + validation (Streamlit only in the cached wrapper)
  model.py           Sections 2-3 as pure functions: strip(D, tables, params) -> DataFrame
  risk.py            Sections 6-8: sensitivities, hedging, VaR, stress, backtest
  tests/test_model.py   Section 5/6/7/8 fixtures, plain asserts, pass/fail summary
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

```bash
export LNG_HISTORY_XLSX="/path/to/LNG history.xlsx"
python3 tests/test_model.py
```

Runs head-less against the real workbook (no synthetic data, no pytest
dependency) and prints a PASS/FAIL line per check plus a summary. Encodes:

- Gate 1 -- master date list (2,335 dates, 2017-07-27 to 2026-07-08) and FX x10 correction sanity.
- Gate 2 -- all four Section 5 regression fixtures (TTF $/MMBtu, EU/Asia $/day, JKM*, gap, verdict), plus the Step 7 structural charter-invariance assertion.
- Gate 3 -- Section 6 sensitivities: analytic deltas vs finite-difference cross-check (agree to 1e-6 relative), plus a printed comparison against the spec's own numbers.
- Gate 4 -- Section 7 hedge-effectiveness fixture and Section 8 VaR fixtures (12-cargo strip, M1 diversion spread), 500 naive-return scenarios, +-1%.
- An internal consistency check: the numpy-vectorised repricer risk.py uses for VaR/backtest performance matches `model.strip` exactly on a zero-shock scenario.

As of the 08-Jul-2026 workbook: **64/64 checks pass, zero failures**
(v2.2 re-baselined build; earlier builds passed their own gates against the
pre-rebaseline spec).

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
checkbox, **default off**. Per the spec, the fixtures in Sections 7/8 are
reproduced with the naive method first (`method="naive"`, the default) and
are not gated on the roll-aligned variant. Note the alignment uses calendar
month-boundaries for NG/TTF, consistent with the model's own front-month
convention; real NG/TTF expiries fall 2-3 business days before month-end, so
+-2-3 day artefacts around expiry remain even in this mode.

## Out of scope (v1, per spec Section 9)

Margrabe option valuation for the diversion optionality, forward-curve-
consistent multi-voyage optimisation, Suez/Cape routing, FuelEU, live data
feeds (the xlsx is the only source; refresh by re-saving it and letting the
mtime-keyed cache pick it up).
