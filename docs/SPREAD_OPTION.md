# Intrinsic / extrinsic value (JKM vs TTF diversion option) -- Forward-strip page

**Module:** `spread_option.py`. **Data:** `data.load_volatilities()` /
`CurveTables.vol` (the workbook's `volatilities` sheet). **UI:** Forward-strip
page, "Intrinsic / extrinsic value (JKM vs TTF diversion option)" section.
**Tests:** `tests/test_spread_option.py` (19 tests).

## What this adds

For each of the 36 forward-strip months, two new numbers:

- **Intrinsic** = `max(JKM - TTF, 0)` -- today's forward view, no uncertainty.
- **Extrinsic** = the time value of the option to divert to JKM instead
  of delivering to TTF, from the volatility/correlation source selected
  in the UI.

Framing: a cargo is delivered to TTF (Europe) as the base case; the
option is diverting to JKM (Asia) instead when JKM is higher. JKM and
TTF are the same `$/MMBtu` revenue references `model.strip()` already
computes (`row["JKM"]`, `row["ttf_usd"]`) -- not the full netback
margins (no procurement/shipping/ETS/etc. netted out).

## History: superseded design

An earlier version of this feature treated each route's own margin
(revenue minus Henry-Hub-linked procurement cost) as its own spread
option, giving four columns (`eu_intrinsic`/`eu_extrinsic`/
`asia_intrinsic`/`asia_extrinsic`) and using a Bachelier (normal)
pricing model because netback margins can be negative. That design was
**replaced** by direct, explicit user instruction: "put intrinsic as
max(JKM-TTF,0)... put extrinsic as spread option between JKM and TTF
with a strike of 0" -- an exchange-option framing between the two
routes' revenue indices, not a per-route revenue-vs-cost framing.

Worth noting for anyone re-deriving this: the "Correlation TTF/JKM"
column the user added to the `volatilities` sheet before this
redesign didn't fit the earlier per-route design (which needed
`Correlation JKM/HH` instead) -- it fits this one exactly. In
hindsight the column the user chose to add was a signal worth weighing
more heavily at the time.

## Formula: Margrabe (1978), not Bachelier

JKM and TTF (delivered ex-ship gas prices) are strictly positive market
prices -- unlike a netback margin, they don't realistically go negative
-- so this is priced with Margrabe's original lognormal exchange-option
formula: the standard, textbook-correct closed form for a zero-strike
option to exchange one asset for another.

```
intrinsic  = max(JKM_0 - TTF_0, 0)
sigma^2    = vol_jkm^2 + vol_ttf^2 - 2*correlation*vol_jkm*vol_ttf
d1         = [ln(JKM_0/TTF_0) + 0.5*sigma^2*T] / (sigma*sqrt(T))
d2         = d1 - sigma*sqrt(T)
option_value = JKM_0*N(d1) - TTF_0*N(d2)
extrinsic  = option_value - intrinsic                    (>= 0 always)
```

`JKM_0` and `TTF_0` are read directly from an existing `model.strip()`
row (`row["JKM"]`, `row["ttf_usd"]`) -- no procurement/shipping/ETS
netting, no residual-strike derivation; the strike is explicitly 0 by
construction, not derived.

`T` (years to expiry) uses the same "15th of the load month" convention
already used elsewhere in this codebase (`decision._representative_load_date`).

## Two vol/correlation sources (user-selected toggle)

- **Historical**: realized vol/correlation of JKM vs TTF from
  `tables.ttf`/`jkm`'s actual daily price history, over a rolling window
  in **calendar days** (default 60), annualized with the standard
  trading-day convention (`sqrt(252)`). Fully self-contained.
- **Volatilities tab**: `data.load_volatilities()`'s reading of the
  workbook's `volatilities` sheet, indexed by tenor (`spot`, `M+1`,
  `M+2`, ...) -- `Volatility TTF`, `Volatility JKM` and
  `Correlation TTF/JKM` are exactly what this formula needs, and the
  sheet has all three, so both sources are fully populated for every
  month with no gaps (unlike the superseded per-route design, which had
  a real gap for Asia in tab mode). Flat-extrapolated beyond the sheet's
  last tenor row.

## Stated simplifications (not oversights)

- **FX volatility is not modelled as a separate risk factor.** Neither
  vol/correlation source has FX data. "TTF" here means the
  already-USD-converted `ttf_usd` revenue reference; its fractional vol
  is approximated as equal to raw TTF's own fractional vol (FX vol is
  assumed small relative to TTF/JKM vol). JKM needs no such
  approximation -- it is already USD-denominated.
- **This compares raw revenue indices, not full netback margins.**
  Procurement, shipping, regas/port, other costs, ETS and boil-off are
  not part of this calculation at all (contrast the earlier per-route
  design, which netted HH-linked cost out of the revenue index). This
  is a deliberate simplification matching the user's explicit formula,
  not an incomplete port of the old design.
- **Historical mode's tenor mapping (`c{months_forward}`) does not
  replicate `model.strip()`'s JKM contract-calendar shift** (`s` from
  `model.contract_calendar`). A deliberate simplification so historical
  and tab-mode tenors share one convention throughout this module, at
  the cost of a small, unquantified misalignment against
  `model.strip()`'s own JKM contract selection on some valuation dates.

## Performance

`intrinsic_extrinsic_strip()` prices 36 options per page load (one per
month, not two per month as in the superseded per-route design). In
"historical" mode this involves a 2-way date intersection over the
workbook's full price history, computed once per page load and reused
across all 36 pricings -- well under 0.3s for the full strip on the real
workbook.
