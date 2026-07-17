# Risk equivalence fix

**Release:** `v2.4.1-risk-equivalence`  
**Branch:** `feature/v2.4.1-risk-equivalence`  
**Date:** 17 July 2026

## Scope

Correct the duplicated legacy risk repricer and analytic sensitivity formulas so that they use the same loading-time and loading-port-fuel definitions as `model.strip()` under both frozen legacy defaults and the current 17-knot operating defaults.

This is a correctness patch. It does not convert the risk module to the segment-level physical engine or make the legacy 12-cargo portfolio operationally feasible.

## Traceability

- Imported Drive snapshot commit: `fd303715611e8d6afa4ba7b0fbbcf2c96c6b4837`
- R1.1 pre-fix evidence commit: `3c09b4714b981126c2d0b2596098bc0db79e0e6b`
- R1.2 tests and expected failures commit: `c305fd8df842b2e84579c1a16c60828dd5ccaa97`
- R1.3-R1.6 code-fix commit: `4d2b4b908bf2e392853ad2b5fe3b0053260a121a`
- Workbook SHA-256: `4e51a1e6b92d004836c8cfe18f118006686ef19ad2b9813c77c53035591c5497`
- Workbook size: `6,463,970` bytes
- Curve date: `2026-07-08`

## Root cause

`risk._vectorized_reprice()` omitted `params.loading_days` from Europe round-trip duration, omitted loading-port fuel on Europe, and failed to subtract loading time when deriving Asia ballast days. `risk.analytic_deltas()` repeated the same omissions for charter and VLSFO sensitivities.

The frozen legacy defaults use `loading_days = 0`, so the existing zero-shock test remained green. The application uses `loading_days = 1.5`, exposing the divergence.

## Formula changes

Europe:

```python
europe_rt = laden_days + ballast_days + unloading_days + loading_days
port_fuel_days = unloading_days + loading_days
```

Asia:

```python
ballast_days = round_trip_days - laden_days - unloading_days - loading_days
port_fuel_days = unloading_days + loading_days
```

The formulas were changed in both `_vectorized_reprice()` and `analytic_deltas()`.

## Zero-shock results

| Portfolio | Before, USD | After, USD | Acceptance |
|---|---:|---:|---|
| Europe single | 151,125.000000 | 0.000000003725 | PASS, absolute P&L <= $0.01 |
| Asia single | -48,603.369494 | 0.000000000000 | PASS, absolute P&L <= $0.01 |
| Diversion spread | -199,728.369494 | -0.000000003725 | PASS, absolute P&L <= $0.01 |
| 12-cargo | 1,813,500.000000 | 0.000000000000 | PASS, absolute P&L <= $0.01 |

## VaR and expected-shortfall impact

500-day roll-aligned historical simulation, current operating defaults.

| Portfolio | Metric | Before, USD | After, USD | Change, USD |
|---|---|---:|---:|---:|
| Europe single | VaR95 | -2,818,419.26 | -2,969,544.26 | -151,125.00 |
| Europe single | VaR99 | -4,793,276.13 | -4,944,401.13 | -151,125.00 |
| Europe single | ES95 | -4,094,547.53 | -4,245,672.53 | -151,125.00 |
| Europe single | ES99 | -6,162,349.90 | -6,313,474.90 | -151,125.00 |
| Europe single | SD | 2,113,709.00 | 2,113,709.00 | 0.00 |
| Asia single | VaR95 | -2,856,001.82 | -2,807,398.45 | 48,603.37 |
| Asia single | VaR99 | -4,957,671.90 | -4,909,068.53 | 48,603.37 |
| Asia single | ES95 | -4,319,178.55 | -4,270,575.18 | 48,603.37 |
| Asia single | ES99 | -6,399,014.62 | -6,350,411.25 | 48,603.37 |
| Asia single | SD | 2,338,830.94 | 2,338,830.94 | 0.00 |
| Diversion spread | VaR95 | -1,528,988.64 | -1,329,260.27 | 199,728.37 |
| Diversion spread | VaR99 | -2,347,957.58 | -2,148,229.21 | 199,728.37 |
| Diversion spread | ES95 | -2,241,837.60 | -2,042,109.23 | 199,728.37 |
| Diversion spread | ES99 | -3,423,746.86 | -3,224,018.49 | 199,728.37 |
| Diversion spread | SD | 964,398.13 | 964,398.13 | 0.00 |
| 12-cargo | VaR95 | -24,005,174.79 | -25,818,674.79 | -1,813,500.00 |
| 12-cargo | VaR99 | -45,820,638.16 | -47,634,138.16 | -1,813,500.00 |
| 12-cargo | ES95 | -38,442,346.44 | -40,255,846.44 | -1,813,500.00 |
| 12-cargo | ES99 | -62,409,557.83 | -64,223,057.83 | -1,813,500.00 |
| 12-cargo | SD | 18,616,978.24 | 18,616,978.24 | 0.00 |

The standard deviation is unchanged because the defect was a deterministic base-value offset. VaR and ES move by the corresponding zero-shock offset.

## Sensitivity reconciliation

| Shock | Basin | Analytic, USD | Finite difference, USD | Difference, USD |
|---|---|---:|---:|---:|
| Charter +$10k/day | EU | -270,196.078431 | -270,196.078431 | -0.000000000931 |
| Charter +$10k/day | Asia | -505,882.352941 | -505,882.352941 | -0.000000002387 |
| VLSFO +$50/t | EU | -63,273.211074 | -63,273.211074 | -0.000000000728 |
| VLSFO +$50/t | Asia | -121,678.843490 | -121,678.843490 | 0.000000000378 |

## Validation evidence

- Python byte compilation: PASS.
- Frozen legacy regression: **64/64 PASS**.
- Current pytest suite: **144/144 PASS**.
- Operating-default targeted suite: **10/10 PASS**.
- Streamlit smoke: Decision, VaR & stress, Forward strip, Sensitivities and Hedging pages all PASS.

Evidence files:

- `test_results/v2.4.1/R1.1_pre_fix_zero_shock_and_risk.txt`
- `test_results/v2.4.1/R1.2_expected_failures_before_fix.txt`
- `test_results/v2.4.1/R1.3_R1.6_targeted_tests_after_fix.txt`
- `test_results/v2.4.1/R1_post_fix_zero_shock_and_risk.txt`
- `test_results/v2.4.1/R1.7_compileall.txt`
- `test_results/v2.4.1/R1.7_legacy_64.txt`
- `test_results/v2.4.1/R1.7_pytest_full.txt`
- `test_results/v2.4.1/R1.7_streamlit_smoke.txt`

## Files changed

- `risk.py`
- `tests/test_risk_containment.py`
- `README.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/RISK_EQUIVALENCE_FIX.md`
- traceability register

## Remaining limitations

- `risk.py` is still a vectorised copy of the legacy monthly formula rather than a repricer generated from canonical physical cash flows.
- The default 12-cargo risk portfolio is not a feasible one-vessel programme.
- VLSFO, EUA and physical basis risks are not included as stochastic factors.
- Roll alignment remains an interim continuation-series treatment rather than permanent contract-ID history.
- The deterministic decision modes use the physical engine, while the risk module remains on the legacy 12-month basis.

## Closure criteria

R1 is verified because all zero-shock P&Ls are within $0.01, analytic charter and VLSFO deltas match finite differences, the frozen suite remains 64/64, all 144 pytest tests pass and all five Streamlit pages load.
