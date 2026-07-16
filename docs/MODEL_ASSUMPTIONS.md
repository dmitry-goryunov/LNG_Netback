# Model assumptions for v2.3-phase1

## Operating case vs legacy spec case (re-baselined 16-Jul-2026)

Two named parameter sets now exist:

- **Legacy spec case** (`model.Params()` defaults, frozen forever for the
  64/64 regression suite): 19.5 kn design speed, 0 d loading time,
  5 d unloading, flat 150/130/25 t/d fuel rates, hand-set 4,425.9 t
  legacy ETS constant. Europe RT 25.94 d, Asia base RT 46.74 d.
- **Operating case** (`model.operating_default_params()`, what the app
  shows by default, set by user instruction): **17 kn service speed,
  1.5 d loading, 1.5 d unloading.** Everything downstream is DERIVED,
  not asserted: sea legs from distance/speed (Europe 12.01 d/leg, Asia
  23.79 d/leg incl. canal), Europe RT 27.02 d, Asia base RT 50.59 d
  (congested 58.59 d); sea fuel rates from the cube law
  (laden 99.4 t/d, ballast 86.1 t/d -- at 17 kn the vessel sails almost
  entirely on natural boil-off, residual purchased fuel ~13 t/d); the
  legacy ETS tonnes from the physical fuel balance at uniform 50% scope
  (~3,182 t at 17 kn, vs 4,425.9 t at 19.5 kn).

The default programme horizon is DERIVED as two Europe round trips at
the current geometry, rounded up to 0.1 d (user instruction, 16-Jul-2026:
"make it fit 2x Europe") -- 54.1 d at 17 kn, since one Europe RT is
27.0196 d, not a clean 27 (a literal 54.0 would exclude the second
voyage by ~56 minutes). At that horizon the best programme is
"Europe -> Europe" again; at the earlier fixed 52-day horizon the same
geometry produced a single "Asia" voyage instead. Loading-berth time is
charged charter + port-rate fuel on both routes and sits OUTSIDE EU ETS
scope (US berth); it is inert at the legacy 0.0-day default.

## Decision-state assumptions

- Post-lift incremental value excludes procurement and completed loading because they are sunk.
- Full-cargo value retains procurement and loading.
- Every later programme cargo is a new pre-lift cargo and includes procurement and loading once.
- The current cargo is mandatory in the programme optimiser. Later cargoes are optional.

## Programme assumptions

- One vessel only.
- Deterministic route durations.
- Europe round trip: 25.94017094 days under default inputs.
- Asia base round trip: 46.74358974 days.
- Asia congested round trip: 54.74358974 days.
- A voyage must fit fully within the horizon.
- Representative loading date is the 15th of the selected monthly strip row.
- A later cargo uses the strip row matching its representative start month.
- No exact laycan, terminal-slot, canal-slot or vessel-compatibility constraints are yet enforced.
- Residual vessel value is a user-entered dollar amount per unused day.
- Legacy cargo margins are not separately discounted. No additional programme-level discount factor is applied.

## Risk containment assumptions

- The pure risk function default remains `naive` to preserve the 64 frozen fixtures.
- Streamlit defaults to `roll_aligned`.
- Roll-aligned scenarios require complete c1..c14 observations and fail explicitly if unavailable.
- The interim backtest skips roll pairs. It does not yet replace continuation series with permanent contract IDs.
- The 12-cargo portfolio remains available only as a labelled legacy regression portfolio.

## Known live limitations retained

- Physical BOG, fuel and ETS calculations remain the legacy simplified model.
- VLSFO and EUA remain static inputs.
- M12 may lie beyond the available 1Y FX outright. The app now warns explicitly.
- The current programme optimiser uses monthly, not contract-accurate, pricing calendars.
