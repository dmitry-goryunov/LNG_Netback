# Model assumptions for v2.3-phase1

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
