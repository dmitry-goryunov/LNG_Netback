# Intrinsic / extrinsic value (JKM vs TTF diversion option) -- Forward-strip page

**Module:** `spread_option.py`. **Data:** `data.load_volatilities()` /
`CurveTables.vol` (the workbook's `volatilities` sheet). **UI:** Forward-strip
page, "Intrinsic / extrinsic value (JKM vs TTF diversion option)" section.
**Tests:** `tests/test_spread_option.py` (21 tests).

## What this adds

For each of the 36 forward-strip months, five numbers:

- **Vol JKM**, **Vol TTF**, **Correlation** -- the exact inputs
  `price_exchange_option()` used to price that month's option (not an
  independently recomputed or approximated figure -- `intrinsic_extrinsic_strip()`
  reads them straight off the same `SpreadOptionResult` its intrinsic/
  extrinsic columns come from, so they can never drift apart). Shown so
  the table is self-explanatory without opening the single-month detail
  expander for every row.
- **Intrinsic** = `max(JKM - TTF, 0)` -- today's forward view, no uncertainty.
- **Extrinsic** = the time value of the option to divert to JKM instead
  of delivering to TTF, from the volatility/correlation source selected
  in the UI.

All three vol/correlation columns and extrinsic go `NaN` together
whenever the selected source could not supply complete inputs for that
tenor -- intrinsic is the only column that never depends on them (it is
a pure function of today's forward JKM/TTF levels).

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
- ~~Historical mode's tenor mapping does not replicate the JKM
  contract-calendar shift~~ **FIXED after a logic review quantified it
  as anything but small**: the earlier version computed both legs' vol
  and their correlation on the same `c{months_forward}` column, but the
  strip's JKM price is contract `c{i+2-s}` (delivery L+1) while TTF is
  `c{i+1}` (delivery L). At the front of the curve JKM c1 is the noisy
  expiring contract: on the 2026-07-08 snapshot its correlation to TTF
  c1 was 0.25 where the correctly-paired contracts' was 0.80, and since
  spread variance is dominated by the correlation term, M1 extrinsic was
  overstated ~4x ($0.74 vs $0.18/MMBtu, ~$2.6M vs ~$0.6M per cargo).
  Historical mode now takes TTF returns from `c{months_forward}`, JKM
  returns from `c{months_forward + 1 - s}` (`s` from
  `model.contract_calendar`, exactly as `model.strip()` selects the
  priced contract), and correlates those two series. Tab mode reads
  `Volatility JKM` at delivery tenor `months_forward + 1` and
  `Volatility TTF` at `months_forward`; the sheet's single
  `Correlation TTF/JKM` column has no cross-delivery pairing, so it is
  read at the nearer (TTF) tenor — a documented residual approximation,
  immaterial while the sheet holds flat placeholder values. Guarded by
  `test_month_spread_option_uses_strips_jkm_contract_not_ttf_tenor`.

## Performance

`intrinsic_extrinsic_strip()` prices 36 options per page load (one per
month, not two per month as in the superseded per-route design). In
"historical" mode this involves a 2-way date intersection over the
workbook's full price history, computed once per page load and reused
across all 36 pricings -- well under 0.3s for the full strip on the real
workbook.
