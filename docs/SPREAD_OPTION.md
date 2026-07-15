# Intrinsic / extrinsic value (spread option) -- Forward-strip page

**Module:** `spread_option.py`. **Data:** `data.load_volatilities()` /
`CurveTables.vol` (the workbook's `volatilities` sheet). **UI:** Forward-strip
page, "Intrinsic / extrinsic value (spread option)" section.
**Tests:** `tests/test_spread_option.py` (19 tests).

## What this adds

For each of the 36 forward-strip months and both routes, two new numbers
alongside the existing `eu_margin`/`asia_margin`:

- **Intrinsic** = `max(margin, 0)` -- today's forward view, no uncertainty.
- **Extrinsic** = the time value of treating that margin as an option
  rather than a certainty: a route's margin is revenue (TTF or JKM) minus
  Henry-Hub-linked procurement cost, both of which are uncertain between
  now and the load month. Extrinsic is the option value of that
  uncertainty, on top of the intrinsic value.

## Design decision: per-route revenue-vs-cost, not Europe-vs-Asia

The request that produced this feature was ambiguous between two
readings: (a) each route's own margin as a spread option (revenue index
vs HH-linked cost), or (b) an exchange option between Europe's margin and
Asia's margin (intrinsic = `max(eu_margin, asia_margin)`). Resolved via
direct user confirmation in favour of (a) -- it also matches the
workbook's own `volatilities` sheet columns (`Volatility TTF`,
`Volatility HH`, `Correlation TTF/HH`) exactly, which reading (b) does
not use at all.

## Formula: Bachelier (normal), not Black-76

`eu_margin`/`asia_margin` can be negative (a real, displayed state in
this tool already), and Black-76/lognormal option pricing requires a
strictly positive underlying. The spread `revenue - cost` is instead
treated as approximately normal, with dollar volatility built from each
leg's own fractional (lognormal-style) volatility scaled by its current
dollar level -- the standard approximation for commodity spread options
(crack spreads, spark spreads) used industry-wide when a closed form is
wanted instead of Monte Carlo:

```
forward_margin = revenue_0 - cost_0 - strike        (== model.strip()'s eu_margin/asia_margin)
intrinsic      = max(forward_margin, 0)
sigma_dollar^2 = (revenue_0*vol_revenue)^2 + (cost_0*vol_cost)^2
                 - 2*correlation*(revenue_0*vol_revenue)*(cost_0*vol_cost)
sigma_t        = sqrt(sigma_dollar^2 * T)
option_value   = forward_margin*N(forward_margin/sigma_t) + sigma_t*phi(forward_margin/sigma_t)
extrinsic      = option_value - intrinsic             (>= 0 always)
```

`revenue_0`, `cost_0` and `strike` are derived **residually** from an
existing `model.strip()` row (`revenue_0 = ttf_usd - eu_boiloff_cost` or
`JKM - asia_boiloff_cost`; `cost_0 = HH * hh_grossup`; `strike =
revenue_0 - cost_0 - margin`) rather than reconstructed cost-line by
cost-line, so `forward_margin` reproduces `eu_margin`/`asia_margin`
exactly by construction and can never drift from `model.strip()`'s own
formula even if its fixed cost lines change later.

`T` (years to expiry) uses the same "15th of the load month" convention
already used elsewhere in this codebase (`decision._representative_load_date`).

## Two vol/correlation sources (user-selected toggle)

- **Historical**: realized vol/correlation from `tables.hh`/`ttf`/`jkm`'s
  actual daily price history, over a rolling window in **calendar days**
  (default 60), annualized with the standard trading-day convention
  (`sqrt(252)`). Fully self-contained, works for both routes today.
- **Volatilities tab**: `data.load_volatilities()`'s reading of the
  workbook's `volatilities` sheet, indexed by tenor (`spot`, `M+1`,
  `M+2`, ...). Flat-extrapolated beyond the sheet's last tenor row.

## Known gap: `Correlation JKM/HH`

As of this writing the `volatilities` sheet has `Volatility TTF`,
`Volatility HH`, `Volatility JKM`, `Correlation TTF/HH` and
`Correlation TTF/JKM` -- **no `Correlation JKM/HH`**. Asia's spread
option needs JKM-vs-HH correlation (TTF and JKM never both appear in the
same route's margin, so `Correlation TTF/JKM` isn't consumed by this
formula). Consequence: in "Volatilities tab" mode, Asia's `option_value`/
`extrinsic` are `None` (shown as "N/A" in the UI, with an explicit
on-page caption naming the missing column), not a silently wrong number.
Europe is unaffected, and "Historical" mode is unaffected for both routes
(it computes JKM/HH correlation directly from price history). To close
this gap, add a `Correlation JKM/HH` column to the sheet (same
`spot`/`M+1`/`M+2`/... row layout as the existing columns).

## Stated simplifications (not oversights)

- **FX volatility is not modelled as a separate risk factor.** Neither
  vol/correlation source has FX data. "TTF" here means the
  already-USD-converted `ttf_usd` revenue reference; its fractional vol
  is approximated as equal to raw TTF's own fractional vol (FX vol is
  assumed small relative to TTF/JKM/HH vol). JKM needs no such
  approximation -- it is already USD-denominated.
- **Only the HH-linked, revenue-scaled and TTF/JKM-scaled cost/revenue
  terms are treated as stochastic.** All other cost lines (loading,
  charter/bunkers, regas/port, other_cost, ETS, Panama toll) are folded
  into the deterministic `strike` -- reasonable since none of them has
  vol/correlation data in either source, but it means the option value
  understates true uncertainty to the extent those lines are
  themselves volatile (e.g. VLSFO price, EUA price, charter rate).
- **Historical mode's tenor mapping (`c{months_forward}`) does not
  replicate `model.strip()`'s JKM contract-calendar shift** (`s` from
  `model.contract_calendar`). A deliberate simplification so historical
  and tab-mode tenors share one convention throughout this module, at
  the cost of a small, unquantified misalignment against
  `model.strip()`'s own JKM contract selection on some valuation dates.

## Performance

`intrinsic_extrinsic_strip()` prices 36 months x 2 routes = 72 options
per page load. In "historical" mode this involves a 3-way date
intersection over the workbook's full price history, which is computed
once per page load and reused across all 72 pricings (not recomputed per
tenor/route) -- ~0.3s for the full strip on the real workbook, vs ~1.1s
before that optimization.
