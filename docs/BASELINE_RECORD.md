# Baseline record

Per `IMPROVEMENT_PLAN.md` Phase 0 step 4-5 and priority-matrix item "Record
tag, workbook hash and 64/64 output".

## Git baseline

- **Tag:** `v2.2-renewal-rate`
- **Commit:** `d7f8c0761100cba59462442b6d437850f5241bfe` ("Add waterfall and
  Sankey flow charts to netback view")
- **Committed:** 2026-07-15 10:12:58 +0100
- **Regression status at tag:** 64/64 checks passed, all gates green
  (`test_results/legacy_regression_64_checks_baseline.txt`)

## Vendor workbook (`LNG history.xlsx`)

Located outside the repository at `../LNG history.xlsx` (relative to the
repo root) and never committed to version control.

| Field | Value |
|---|---|
| SHA-256 | `27249ec6778a61974690bc63ceb4530c508614c88a3f8c5df59a061b0e4abad5` |
| File size | 6,461,805 bytes |
| Last modified | 2026-07-13 20:33:58 +0100 |
| HH rows | 5,671 (2004-10-12 .. 2026-07-07) |
| TTF rows | 4,259 (2010-03-12 .. 2026-07-08) |
| JKM rows | 3,117 (2014-07-29 .. 2026-07-08) |
| FX rows | 6,911 (2000-01-10 .. 2026-07-08) |

Master curve date used for the frozen regression run: **2026-07-08**.

To re-verify this workbook, compute its hash:

```sh
sha256sum "LNG history.xlsx"
# Windows: certutil -hashfile "LNG history.xlsx" SHA256
```

A result matching the value above confirms the workbook is byte-identical
to the one that produced the frozen `v2.2-renewal-rate` regression output.

## Outstanding Phase 0 governance items

Not yet completed as of this record:

- synthetic/anonymised CI fixture workbook (Improvement 12 step 7);
- schema validation for required sheets/columns (Improvement 12 step 9);
- CI pipeline running the synthetic fixture (Improvement 12 step 14).

These require a fixture-design decision (how much of the real workbook
structure to fabricate) and are deferred to a follow-up increment.
