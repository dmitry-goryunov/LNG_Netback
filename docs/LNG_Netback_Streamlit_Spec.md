# LNG Forward Netback: Streamlit App Specification (v2, re-baselined)

> **Scope note (17-Jul-2026):** this document defines the FROZEN LEGACY
> SPEC CASE -- the exact assumptions (19.5 kn design speed, 0-day
> loading, 5-day port calls, hand-set fuel/ETS constants) that the
> 64-check regression suite (`tests/test_model.py`) pins forever and
> that `model.Params()` defaults reproduce bit-for-bit. It does NOT
> describe the app's current behavior: the operating case (17 kn,
> 1.5 d loading/unloading, 2% heel, derived fuel/ETS -- see
> `docs/MODEL_ASSUMPTIONS.md`) and the physical decision engine
> (`docs/PHASE2_PLAN.md`) have superseded several sections, and the
> "headline v2 roadmap item" below (the decision-mode switch) shipped
> in v2.3-phase1. Read this as the baseline definition, not the manual.

**Purpose:** implementation-ready description of `LNG_Forward_Netback.xlsx` and the diversion logic behind it (`LNG_Diversion_Logic.md` v2.2), extended with sensitivity, hedging and VaR modules. The reference implementation is `lng_netback_app/` (built 13-Jul-2026, re-baselined the same day after the notes.md review); its test suite encodes every fixture below.

**Decision mode (state this first):** the model implements ONE economic problem: repeated fleet deployment — a vessel permanently cycling USGC round trips, choosing the basin that maximises margin per vessel-day, with procurement incurred for every successive cargo. It does not price post-lift diversion (procurement and loading would then be sunk) nor pre-lift cargo NPV under slack vessel capacity. A `decision_mode` switch (POST_LIFT_DIVERSION / PRE_LIFT_CARGO / FLEET_PROGRAMME) with mode-specific cost inclusion is the headline v2 roadmap item.

**Revision history:** 13-Jul-2026 initial; corrected same day (procurement non-invariance, stale Section 6 baseline — found by the implementing agent); re-baselined same day after `notes.md`: voyage days now derived from distance and speed on BOTH routes, CO2 scope re-derived, VaR-window wording fixed, and the framing corrections below applied. All fixture numbers in Sections 5 to 8 are cross-checked between an independent Python reference and the app's test suite.

**Rounding convention:** fixtures quoted to the precision shown; tolerances per Section 5/9.

---

## 1. Data layer: `LNG history.xlsx`

| Sheet | Content | Layout | Quirks |
|---|---|---|---|
| `HH` | NYMEX Henry Hub futures strip, NGc1..NGc64, $/MMBtu | header rows 1-2, data ascending from 2004 | latest date can lag TTF/JKM by a day |
| `TTF` | ICE TTF futures strip, TFMBMc1..c65, EUR/MWh | same | |
| `JKM` | Platts/ICE JKM futures strip, JKMc1..c44, $/MMBtu | same | complete 13-month strips only from 2017-05 |
| `FX new` | EURUSD: spot, then 6M, 1Y, 2Y..10Y tenors | data DESCENDING | **BUG: forward columns store spot + points x10.** True outright = spot + (stored - spot)/10. Verified via interest parity on 2000/2019/2024 rows |
| `charter` | weekly charter; cols D/E = 174k m3 2-stroke $/day | descending | starts 2017-07-27 |
| `US netbacks` / `US transport` | Platts reference sheets | | sanity cross-check only |

**Loader rules (`data.py`):** parse dates as datetime/serial/string (one FX row is a bare Excel serial — do not feed ints to pd.to_datetime); keep rows complete in the required columns (c1..c13; FX spot/6M/1Y; charter rate); sort ascending; apply the FX x10 correction on load; master date list = TTF dates >= first complete date of every table (**2,335 dates, 2017-07-27 to 2026-07-08** as of the 08-Jul file); validate monotonicity and corrected-FX parity within +-5%; `st.cache_data` keyed on path+mtime, `file_uploader` fallback.

## 2. Model layer, step by step

Given a selected curve date `D` from the master list:

**Step 1 - snap.** Per table independently, last row with date <= D. Display snapped dates.

**Step 2 - contract calendar.** NG/TTF front month `F` = month(D)+1. JKM shift `s` = 0 if day(D) <= 15 else 1. Known approximations, both documented: NG/TTF labels are off by one in the last 2-3 calendar days of a month (real expiries precede month-end); the day-15 JKM rule is +-1 day around actual expiry. Acceptable for this monthly-strip tool; a production system must use actual contract metadata and an explicit cargo schedule (see Section 10).

**Step 3 - FX curve.** Corrected outrights `o6, o1`; linear interpolation on `t = (mid-month - D)/30.44`, extrapolating linearly past 1Y.

**Step 4 - charter and ETS phase.** Charter = 174k 2-stroke rate at snap(D), user-overridable. ETS phase factor 0 / 0.4 / 0.7 / 1.0 by the delivery month's year (2024/2025/2026+).

**Step 5 - voyage geometry (DERIVED, not asserted) and cost parameters:**

| Parameter | Value | Parameter | Value |
|---|---|---|---|
| Speed | 19.5 kn = 468 nm/day | Loading | 0.06 $/MMBtu |
| Europe leg | 4,900 nm -> **10.4701 d**; RT **25.9402 d** (5 port d) | EU regas + port | 0.41 $/MMBtu |
| Asia leg | 9,300 nm + 1 d canal -> **20.8718 d**; RT **46.7436 d** | Asia port (DES: no regas) | 0.05 $/MMBtu |
| Congestion case | +4 waiting d per leg -> RT **54.7436 d** (waiting charged at full propulsion rates — a known overstatement; queue days burn ~BOG + auxiliaries only) | Other (insurance, LC, brokerage) | 0.07 $/MMBtu |
| Cargo | 3,500,000 MMBtu | Panama toll x2 | $1,600,000 placeholder — compute from the vessel-specific ACP schedule; keep auction premium and queue days as separate inputs |
| Boil-off | 0.10%/day | HH gross-up / toll / pipe | 1.15 / 3.00 / 0.20 |
| Laden fuel requirement | 150 t/d | EUA | EUR 70/t, static, unverified |
| Natural BOG offset | 3,500 MMBtu/d ~ 86.4 t/d VLSFO-eq on an HHV (~42.7 GJ/t) basis. Basis was previously unstated; a consistent LHV/LHV or GCV-adjusted comparison spans ~82-91 t/d, cost effect <= $0.01/MMBtu. Parameter; state basis and engine efficiency when refining | CO2 in ETS scope per EU RT | **~4,426 t** (derived from the fuel balance at 10.4701-d legs; was 4,243 under the old 10-d legs) |
| Residual laden VLSFO | 63.6 t/d | VLSFO | $530/t static for ALL dates |
| Ballast / port fuel | 130 / 25 t/d | | |

**Step 6 - per load month L = F..F+11:** as before with the derived days:

```
HH(L) = NG c(i+1);  TTF(L) = TFMBM c(i+1);  JKM(L+1) = JKM c(i+2-s);  fx(L) from Step 3
proc      = HH*1.15 + 3.20
ttf_usd   = TTF * fx / 3.412
eu_ship   = (charter*25.9402 + (63.6*10.4701 + 130*10.4701 + 25*5)*VLSFO) / cargo
as_ship   = (charter*46.7436 + (63.6*20.8718 + 130*20.8718 + 25*5)*VLSFO + 1.6m) / cargo
ets       = 4426 * EUA * phase * fx / cargo                      [Europe leg only]
EU margin = ttf_usd*(1 - 0.001*10.4701) - proc - 0.06 - eu_ship - 0.41 - 0.07 - ets
Asia margin = JKM(L+1)*(1 - 0.001*20.8718) - (proc + 0.06 + as_ship + 0.05 + 0.07)
$/day     = margin * cargo / RT;   JKM* solved on delivered volume as before
```

Load-date convention: mid-month representative loading; TTF(L) and JKM(L+1) are monthly approximations of the true delivery windows (late-month loads deliver L+1 / L+2 respectively).

**Step 7 - structural properties (assert in tests):**

1. JKM* is invariant to a charter day-rate **common to both routes under one charter contract, with no route-specific redelivery, extension, off-hire or terminal-location effects**. Outside those conditions (term charter with redelivery constraints, sublet/idle alternatives, differing redelivery basins) the cancellation can fail.
2. JKM* is NOT invariant to per-cargo costs: dJKM*/dHH = 1.15 x (1 - rtA/rtE)/(1 - BOR x ladenA) ~ **-0.94** at the derived voyage times. Assert magnitude, not zero. Economically: in fleet mode Europe processes ~1.8 cargoes per Asia voyage, so common per-cargo costs matter; in post-lift mode they are sunk and should be excluded (decision_mode roadmap).
3. Boil-off at destination = revenue on delivered volume; breakeven solved on delivered volume.
4. JKM is DES (no Asia regas); TTF is a hub price (Europe regas netted).
5. The margin/day comparison prices ~1.8 successive Europe voyages **at the selected load month's margin, as a proxy**. A forward-curve-consistent comparison would price each voyage slot separately (second voyage loads ~L+1); out of v1 scope, stated in the UI caveats.
6. Verdict is RELATIVE. When both margins are negative the verdict only ranks losses; the UI must warn and point to the unmodelled lift/don't-lift decision.

## 3. Diversion framing

Per `LNG_Diversion_Logic.md` v2.2. Keep visible in the UI: fleet-deployment mode assumptions, static VLSFO/EUA across all dates, FuelEU **in force since 1-Jan-2025 but unquantified** (use a vessel-specific compliance shadow price when added — not a flat per-voyage charge), CH4/N2O legally in ETS scope from 2026 but not quantified (model covers CO2 only), heel/demurrage/backhaul/Suez omitted, both routes artificially return to USGC (no terminal value for vessel position).

## 4. App architecture

`lng_netback_app/`: `app.py` (UI, 4 pages), `data.py`, `model.py`, `risk.py` (all pure), `tests/test_model.py`. Sidebar: date picker over master dates + all Step 5 parameters (voyage days editable; Asia laden derived symmetric with pin-override).

## 5. Regression fixtures (re-baselined; +-0.01 $/MMBtu, +-0.5% on $/day)

| Curve date | Month | TTF $/MMBtu | EU $/day | Asia $/day | JKM* | Gap | Verdict |
|---|---|---|---|---|---|---|---|
| 2026-07-08 | M1 = Aug-26 | 15.61 | 927,752 | 550,482 | 22.25 | -5.15 | Europe |
| 2026-07-08 | M12 = Jul-27 | 11.16 | 342,182 | 171,764 | 14.19 | -2.32 | Europe |
| 2022-08-26 (s=1, charter 105k) | M1 = Sep-22 | 99.17 | 11,144,025 | 3,803,986 | 168.92 | -100.12 | Europe |
| 2019-10-01 (charter 105k, phase 0) | M1 = Nov-19 | 5.39 | -288,595 | -158,486 | 4.74 | +1.77 | **Asia** |

2019 remains the critical test: verdict flips with both margins negative (the UI warning must fire).

## 6. Sensitivities (per-cargo deltas at D = 2026-07-08, M1; analytic, FD-cross-checked)

| Shock | EU cargo P&L | Asia cargo P&L |
|---|---|---|
| TTF +1 EUR/MWh | +1,160,730 | 0 |
| JKM +0.10 $/MMBtu | 0 | +342,695 |
| HH +0.10 $/MMBtu | -402,500 | -402,500 (and JKM* moves ~ -0.094) |
| EURUSD +0.01 (parallel shift of spot and outrights) | +469,672 | 0 |
| Charter +$10k/day | -259,402 | -467,436 (margin/day both -10,000; JKM* unchanged) |
| VLSFO +$50/t | -107,590 | -208,268 |
| Asia RT 46.74 -> 54.74 d (symmetric legs) | 0 | JKM* 22.25 -> 24.84 |

UI: tornado, TTF x RT gap grid, what-if sliders, presets (Panama 54.7d, Hormuz JKM +2.0 / VLSFO +80, 2022-08-26 replay).

## 7. Hedging

Legs as before (short TTF delivered MWh + short EUR notional + long NG x1.15 for Europe; short JKM delivered + long NG for Asia; optional VLSFO/EUA/FFA). Contract sizes unverified against current exchange specs — the UI shows a blocking warning. Conventions: TTF-leg P&L converted at base FX; FX shocks are parallel shifts.

**Effectiveness fixture (and its honest interpretation):**

| Portfolio | HS-VaR95 (1d) | HS-VaR99 (1d) | sd |
|---|---|---|---|
| M1 EU cargo, unhedged | -3,054,920 | -4,951,928 | 2,098,992 |
| M1 EU cargo, hedged | -12,437 | -35,599 | 13,681 |

The 0.4% residual is **model-internal**: hedge and cargo revalue off the same index curves, so this is primarily a ratio-bug guard proving the legs invert the model's own formula. It is NOT evidence a physical cargo hedges to 0.4%: NWE DES-TTF basis, JKM index-physical basis, USGC terminal basis to HH, pricing-window mismatch, roll risk, lot rounding and transaction costs are all absent. Production hedge effectiveness requires those basis curves.

## 8. VaR

Historical simulation, **full revaluation with respect to the four included factor groups** (NG, TTF, JKM strips + FX spot) — not of the full economics: VLSFO, EUA, charter, freight/physical basis, canal costs and FuelEU are held static (charter delta reported separately; statics stress-tested only). A "2022 replay" therefore shocks 2026 curves; it is not a replay of 2022 economics.

Scenario set: 500 joint daily log-returns on the table intersection; **the 500-day window is 06-Aug-2024 to 07-Jul-2026 and contains no 2021-22 observations** — the crisis enters only via the deterministic stress replays (2021-12-21, 2022-08-26, 2022-09-26) or with lookbacks of ~1,000+ days. (Corrected: an earlier draft wrongly said 2021-22 dominates this window's tail.)

Fixtures (naive continuation returns, deliberately, as reproducibility gates — production numbers should use the roll-aligned method plus holiday/staleness hygiene):

| Portfolio | VaR95 | VaR99 | sd |
|---|---|---|---|
| 12-cargo strip (verdict-optimal) | -26,093,953 | -47,706,721 | 18,447,468 |
| M1 diversion spread | -1,351,610 | -2,256,879 | 996,909 |

Tail-estimate health: at 500 scenarios the 99% tail rests on ~5 observations — report ES alongside VaR, disclose tail counts, prefer overlapping 10-day returns over sqrt(10) (kept only as a labelled comparison), and add bootstrap CIs and 97.5% ES as near-term extensions. Backtest: exception count + Kupiec LR (traffic-light thresholds are Basel 250d/99% conventions, indicative only on short 95% windows); Christoffersen independence is roadmap. Note the "spread" portfolio is the linear Asia-minus-Europe P&L; the risk of an actual flexible cargo is the change in max(Europe, Asia) — optionality via constrained simulation (LSMC), not Margrabe, is the defensible extension.

## 9. Build order & out of scope

Built and green (64 checks) as of 13-Jul-2026. Out of scope for v1: decision_mode split (headline v2 item), terminal value/backhaul, BOG mass-and-energy balance with engine-specific slip (X-DF vs ME-GI), procurement contract archetypes (the 1.15xHH+3.20 formula is one tolling structure, not generic), financing by actual payment dates, physical basis curves, FuelEU shadow price, ACP tariff engine, Suez/Cape, multi-voyage optimisation, live feeds.

## 10. Production-hardening checklist (from the 13-Jul-2026 notes.md review, accepted)

Explicit loading/delivery/payment dates and contract metadata instead of positional continuation labels; delivery-aligned and timestamp-aligned VaR scenarios; basis curves and terminal-slot value; vessel-specific fuel/BOG balance and voyage states (sea, canal, queue, manoeuvring, berth — each with own fuel/emissions); ETS tonnage from the fuel balance including CH4/N2O; FuelEU compliance economics; financing/insurance allocated by time and value at risk rather than flat per-cargo amounts.
