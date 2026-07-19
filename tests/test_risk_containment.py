from __future__ import annotations

import copy
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

import cashflows
import data
import model
import risk


@pytest.fixture(scope="module")
def tables():
    path = os.environ.get(data.ENV_VAR_NAME)
    if not path:
        local = Path(__file__).resolve().parents[1] / "LNG history.xlsx"
        path = str(local) if local.exists() else data.default_data_path()
    if not path:
        pytest.skip(f"set {data.ENV_VAR_NAME} to run workbook-backed tests")
    return data.load_all(str(path))


def test_roll_aligned_scenarios_work_for_current_window(tables):
    scen = risk.build_scenarios(tables, "2026-07-08", lookback=500, method="roll_aligned")
    assert scen.method == "roll_aligned"
    assert scen.hh_ret.shape == (500, 13)
    assert scen.ttf_ret.shape == (500, 13)
    assert scen.jkm_ret.shape == (500, 13)
    assert np.isfinite(scen.jkm_ret).all()


def test_roll_aligned_nan_guard_is_explicit_but_naive_path_survives(tables):
    broken = copy.deepcopy(tables)
    broken.jkm = broken.jkm.copy()
    target = pd.Timestamp("2026-06-15")
    idx = broken.jkm.index[broken.jkm["date"] == target]
    if len(idx) == 0:
        target = broken.jkm.loc[broken.jkm["date"] <= "2026-07-08", "date"].iloc[-20]
        idx = broken.jkm.index[broken.jkm["date"] == target]
    broken.jkm.loc[idx, "c14"] = np.nan

    with pytest.raises(ValueError, match="complete c1..c14 history"):
        risk.build_scenarios(broken, "2026-07-08", lookback=500, method="roll_aligned")

    naive = risk.build_scenarios(broken, "2026-07-08", lookback=500, method="naive")
    assert naive.jkm_ret.shape == (500, 13)
    assert np.isfinite(naive.jkm_ret).all()


# ===========================================================================
# R6 increment G.1 (plan sect 6.G.1, R6.4): contract-ID (delivery-month)
# return labelling. `naive` must stay byte-unchanged (verified below by
# independent re-derivation, not just "the method tag still says naive");
# `contract_id` must agree with `roll_aligned` wherever roll_aligned's
# shift-by-one heuristic is exact (HH/TTF, always), and correct it where
# it is not (the one JKM transition where a month-roll and the day-15/16
# tenor reset cancel to a net zero shift).
# ===========================================================================


def test_naive_scenarios_unchanged_by_the_contract_id_addition(tables):
    """Byte-frozen check, done by independent re-derivation rather than
    trusting the `method` tag: build naive scenarios through
    build_scenarios(), then recompute the SAME log-returns directly off
    the raw tables (the exact pre-increment-G naive arithmetic) and
    assert exact equality -- not just close -- for every one of the four
    return arrays."""
    scen = risk.build_scenarios(tables, "2026-07-07", lookback=250, method="naive")
    inter = risk._intersection_dates(tables)
    end_idx = max(i for i, d in enumerate(inter) if d <= pd.Timestamp("2026-07-07"))
    scen_dates = inter[end_idx - 250:end_idx + 1]
    cols = [f"c{i}" for i in range(1, risk.N_STRIP_COLS + 1)]

    hh_px = tables.hh.set_index("date").loc[scen_dates, cols].to_numpy(dtype=float)
    ttf_px = tables.ttf.set_index("date").loc[scen_dates, cols].to_numpy(dtype=float)
    jkm_px = tables.jkm.set_index("date").loc[scen_dates, cols].to_numpy(dtype=float)
    fx_px = tables.fx.set_index("date").loc[scen_dates, "spot"].to_numpy(dtype=float)

    assert np.array_equal(scen.hh_ret, np.diff(np.log(hh_px), axis=0))
    assert np.array_equal(scen.ttf_ret, np.diff(np.log(ttf_px), axis=0))
    assert np.array_equal(scen.jkm_ret, np.diff(np.log(jkm_px), axis=0))
    assert np.array_equal(scen.fx_ret, np.diff(np.log(fx_px)))


def test_contract_id_scenarios_work_for_current_window(tables):
    scen = risk.build_scenarios(tables, "2026-07-07", lookback=500, method="contract_id")
    assert scen.method == "contract_id"
    assert scen.hh_ret.shape == (500, 13)
    assert scen.ttf_ret.shape == (500, 13)
    assert scen.jkm_ret.shape == (500, 13)
    assert np.isfinite(scen.hh_ret).all()
    assert np.isfinite(scen.ttf_ret).all()
    assert np.isfinite(scen.jkm_ret).all()


def test_contract_id_nan_guard_is_explicit_but_naive_path_survives(tables):
    """Mirrors test_roll_aligned_nan_guard_is_explicit_but_naive_path_
    survives -- contract_id needs the same c1..c14 completeness roll_
    aligned does (same 14-column buffer), so it must fail loud on the
    same gap, by the same error family."""
    broken = copy.deepcopy(tables)
    broken.jkm = broken.jkm.copy()
    target = pd.Timestamp("2026-06-15")
    idx = broken.jkm.index[broken.jkm["date"] == target]
    if len(idx) == 0:
        target = broken.jkm.loc[broken.jkm["date"] <= "2026-07-08", "date"].iloc[-20]
        idx = broken.jkm.index[broken.jkm["date"] == target]
    broken.jkm.loc[idx, "c14"] = np.nan

    with pytest.raises(ValueError, match="complete c1..c14 history"):
        risk.build_scenarios(broken, "2026-07-08", lookback=500, method="contract_id")

    naive = risk.build_scenarios(broken, "2026-07-08", lookback=500, method="naive")
    assert np.isfinite(naive.jkm_ret).all()


def test_contract_id_matches_roll_aligned_for_hh_ttf(tables):
    """HH/TTF have no JKM-style tenor shift -- every delivery-month shift
    is uniform across all 13 columns, so contract_id's exact lookup and
    roll_aligned's shift-by-one heuristic must always agree for these two
    markets (empirically verified against the live workbook: identical to
    full float precision over a 500-day window)."""
    cid = risk.build_scenarios(tables, "2026-07-07", lookback=500, method="contract_id")
    roll = risk.build_scenarios(tables, "2026-07-07", lookback=500, method="roll_aligned")
    np.testing.assert_array_equal(cid.hh_ret, roll.hh_ret)
    np.testing.assert_array_equal(cid.ttf_ret, roll.ttf_ret)


def test_contract_id_disagrees_with_roll_aligned_on_some_jkm_transitions(tables):
    """roll_aligned's heuristic is only approximate for JKM (day-15/16
    tenor resets can coincide with a month roll and cancel) -- over the
    live workbook's current 500-day window there ARE real transitions
    where contract_id and roll_aligned disagree, but they remain a small
    minority (most JKM transitions have no cancellation, so the two
    methods still mostly agree)."""
    cid = risk.build_scenarios(tables, "2026-07-07", lookback=500, method="contract_id")
    roll = risk.build_scenarios(tables, "2026-07-07", lookback=500, method="roll_aligned")
    diff_mask = ~np.isclose(cid.jkm_ret, roll.jkm_ret)
    n_diff = int(diff_mask.sum())
    assert 0 < n_diff < cid.jkm_ret.size * 0.10, \
        f"expected a small minority of JKM entries to differ, got {n_diff}/{cid.jkm_ret.size}"


def test_contract_id_picks_correct_delivery_month_naive_does_not(tables):
    """Constructed example (real workbook dates): a plain HH month-
    boundary roll (2026-01-30 Fri -> 2026-02-02 Mon, F: Feb-2026 ->
    Mar-2026, no weekend gap) where continuation-column (naive)
    labelling silently compares TWO DIFFERENT delivery months while
    contract_id compares the SAME one -- the exact defect G.1 formalises
    away."""
    d_prev, d = pd.Timestamp("2026-01-30"), pd.Timestamp("2026-02-02")
    inter = risk._intersection_dates(tables)
    assert inter.index(d) == inter.index(d_prev) + 1, "test assumes adjacent trading days, no gap"

    F_prev, _ = model.contract_calendar(d_prev)
    F, _ = model.contract_calendar(d)
    assert F == model._month_add(F_prev, 1), "test assumes a plain month roll"
    # s (the JKM-only tenor shift) is irrelevant here -- HH has no L+1
    # shift, so its delivery-month mapping never reads s at all.

    row_prev = tables.hh.set_index("date").loc[d_prev]
    row = tables.hh.set_index("date").loc[d]
    naive_c1 = np.log(row["c1"] / row_prev["c1"])                # WRONG: different delivery months
    correct_c1 = np.log(row["c1"] / row_prev["c2"])              # RIGHT: today's c1 == yesterday's c2's month
    assert not np.isclose(naive_c1, correct_c1)

    px = tables.hh.set_index("date").loc[[d_prev, d], [f"c{i}" for i in range(1, 15)]].to_numpy(dtype=float)
    cid = risk._contract_id_returns(px, [d_prev, d], jkm=False)
    assert cid[0, 0] == pytest.approx(correct_c1)
    assert not np.isclose(cid[0, 0], naive_c1)


def test_contract_id_fixes_jkm_month_and_tenor_cancellation():
    """Real transition (verified against the live workbook): 2024-08-30
    (day>15, s=1) -> 2024-09-02 (day<=15 of the NEXT month, s=0). Both a
    calendar-month roll AND a JKM tenor reset occur on this SAME
    day-pair and CANCEL to a net ZERO delivery-month shift -- but
    _is_jkm_roll() (and hence roll_aligned) fires anyway, since it
    treats the two triggers as independent. contract_id looks up the
    actual delivery month instead of assuming a shift size, so it gets
    this transition right; roll_aligned mis-shifts by one column. Pure
    synthetic price construction -- no workbook dependency, deterministic."""
    d_prev, d = pd.Timestamp("2024-08-30"), pd.Timestamp("2024-09-02")
    F_prev, s_prev = model.contract_calendar(d_prev)
    F, s = model.contract_calendar(d)
    assert F_prev != F, "test assumes a genuine calendar-month roll"
    assert (s_prev, s) == (1, 0), "test assumes a genuine JKM tenor reset"
    assert risk._is_jkm_roll(d_prev, d) is True, "roll_aligned's heuristic must fire on this pair"

    m = 14
    for k in range(1, m):
        assert risk._delivery_month(F, s, k, jkm=True) == risk._delivery_month(F_prev, s_prev, k, jkm=True), \
            "ground truth: the month-roll and tenor-reset shifts must cancel for every column"

    dates = [d_prev, d]
    px = np.empty((2, m))
    px[0, :] = [100.0 + k for k in range(1, m + 1)]
    px[1, :] = [100.0 + k + 0.01 * k for k in range(1, m + 1)]   # distinct per-column shock

    cid = risk._contract_id_returns(px, dates, jkm=True)
    roll = risk._roll_aligned_returns(px, dates, risk._is_jkm_roll)

    expected_no_shift = np.log(px[1, :13] / px[0, :13])
    np.testing.assert_allclose(cid[0], expected_no_shift)
    assert not np.allclose(cid[0], roll[0]), \
        "roll_aligned's unconditional shift-by-one must disagree with the correct zero-shift answer here"


def test_interim_backtest_skips_roll_pairs(tables):
    bt = risk.backtest_var(
        tables, model.Params(), portfolio="single", basin="Europe", month_index=0,
        lookback=250, window_days=45, method="naive",
    )
    assert not bt.empty
    assert bt.attrs.get("skipped_roll_pairs", 0) >= 1
    for row in bt.itertuples(index=False):
        F, s = model.contract_calendar(row.date)
        Fn, sn = model.contract_calendar(row.next_date)
        assert F == Fn
        assert s == sn


def test_legacy_function_default_remains_naive(tables):
    scen = risk.build_scenarios(tables, "2026-07-08", lookback=10)
    assert scen.method == "naive"


def test_historical_var_physical_defaults_to_contract_id(tables):
    """R6 increment G.1 (plan sect 6.G.1): the roll-safe/contract-ID
    method is the default for the physical basis's OWN scenario-building
    (only reachable when a caller omits `scen`) -- historical_var()
    itself (the frozen legacy path) is untouched, still "naive"."""
    import inspect
    assert inspect.signature(risk.historical_var).parameters["method"].default == "naive"
    assert inspect.signature(risk.historical_var_physical).parameters["method"].default == "contract_id"

    r = risk.historical_var_physical("2026-07-08", tables, model.operating_default_params(),
                                      portfolio="single", basin="Europe", month_index=0, lookback=250)
    assert r.scen.method == "contract_id"


def _zero_scenario() -> risk.ScenarioSet:
    return risk.ScenarioSet(
        dates=[pd.Timestamp("2026-07-07")],
        hh_ret=np.zeros((1, 13)),
        ttf_ret=np.zeros((1, 13)),
        jkm_ret=np.zeros((1, 13)),
        fx_ret=np.zeros(1),
        end_date=pd.Timestamp("2026-07-07"),
        lookback=1,
        method="naive",
    )


@pytest.mark.parametrize(
    ("portfolio", "basin"),
    [
        ("single", "Europe"),
        ("single", "Asia"),
        ("spread", "Europe"),
        ("12cargo", "Europe"),
    ],
)
def test_operating_default_zero_shock_pnl_is_zero(tables, portfolio, basin):
    """R1.2: the risk repricer must reproduce the deterministic base exactly."""
    result = risk.historical_var(
        "2026-07-08",
        tables,
        model.operating_default_params(),
        portfolio=portfolio,
        basin=basin,
        month_index=0,
        scen=_zero_scenario(),
    )
    assert abs(float(result.pnl[0])) <= 0.01


@pytest.mark.parametrize("shock_name", ["Charter +$10k/day", "VLSFO +$50/t"])
def test_operating_default_analytic_sensitivities_match_finite_difference(tables, shock_name):
    """R1.5/R1.6: operating-case analytic deltas must match full repricing."""
    params = model.operating_default_params()
    analytic = {d.name: d for d in risk.analytic_deltas("2026-07-08", tables, params)}[shock_name]
    finite = {d.name: d for d in risk.finite_difference_deltas("2026-07-08", tables, params)}[shock_name]
    assert analytic.eu_cargo_delta == pytest.approx(finite.eu_cargo_delta, rel=1e-10, abs=0.01)
    assert analytic.asia_cargo_delta == pytest.approx(finite.asia_cargo_delta, rel=1e-10, abs=0.01)


def test_operating_default_hedge_leg_vlsfo_tonnage_matches_strip_fuel(tables):
    """The VLSFO-swap hedge leg must size against the same fuel tonnage
    model.strip() actually prices. europe/asia_hedge_legs() carried a third
    copy of the route-fuel formula that the v2.4.1 fix missed (found in
    post-release review): no loading-port fuel, and Asia ballast days not
    net of loading time -- invisible to the zero-shock tests because the
    swap volume never enters a priced, cross-checked quantity. Legacy
    defaults (loading_days = 0) can't distinguish the formulas, so this
    pins the operating case."""
    params = model.operating_default_params()

    eu = risk.europe_hedge_legs("2026-07-08", tables, params)
    eu_swap = float(eu.loc[eu["leg"].str.startswith("VLSFO swap"), "volume"].iloc[0])
    eu_expected = (params.residual_laden_vlsfo * params.europe_laden_days
                   + params.ballast_fuel * params.europe_ballast_days
                   + params.port_fuel_rate * (params.europe_port_days + params.loading_days))
    assert eu_swap == pytest.approx(eu_expected, rel=1e-12)

    asia = risk.asia_hedge_legs("2026-07-08", tables, params)
    asia_swap = float(asia.loc[asia["leg"].str.startswith("VLSFO swap"), "volume"].iloc[0])
    asia_ballast = (params.asia_rt_days - params.asia_laden_days
                    - params.asia_port_days - params.loading_days)
    asia_expected = (params.residual_laden_vlsfo * params.asia_laden_days
                     + params.ballast_fuel * asia_ballast
                     + params.port_fuel_rate * (params.asia_port_days + params.loading_days))
    assert asia_swap == pytest.approx(asia_expected, rel=1e-12)


@pytest.mark.parametrize(
    "params_factory", [model.Params, model.operating_default_params], ids=["legacy", "operating"]
)
def test_vectorized_reprice_matches_scalar_cargo_exposure_loop(tables, params_factory):
    """R6 increment B: the batched _vectorized_reprice() path (12 months x
    n scenarios, evaluated via CargoExposure.value_matrix()) must agree
    with an independent per-scenario Python loop calling
    CargoExposure.value() at the same shocked prices -- the guard that
    the batched path equals the scalar path through REAL risk wiring
    (unlike test_cashflows.py's value_matrix test, which checks the
    cash-flow layer in isolation on synthetic quantities and never
    touches risk.py). Scenarios are non-zero (small random log-returns,
    fixed seed) -- a zero-shock check alone cannot distinguish a batched
    implementation bug from one that only breaks under real dispersion."""
    D = "2026-07-08"
    params = params_factory()

    hh_row = model.snap(tables.hh, D)
    ttf_row = model.snap(tables.ttf, D)
    jkm_row = model.snap(tables.jkm, D)
    fx_row = model.snap(tables.fx, D)
    ch_row = model.snap(tables.charter, D)
    charter = params.charter_override if params.charter_override is not None else float(ch_row["rate174"])

    cols = [f"c{i}" for i in range(1, risk.N_STRIP_COLS + 1)]
    base_hh = hh_row[cols].to_numpy(dtype=float)
    base_ttf = ttf_row[cols].to_numpy(dtype=float)
    base_jkm = jkm_row[cols].to_numpy(dtype=float)
    base_spot, base_o6, base_o1 = float(fx_row["spot"]), float(fx_row["o6"]), float(fx_row["o1"])

    rng = np.random.default_rng(20260717)
    n = 6
    scen = risk.ScenarioSet(
        dates=[pd.Timestamp("2026-07-07")] * n,
        hh_ret=rng.normal(0, 0.02, (n, risk.N_STRIP_COLS)),
        ttf_ret=rng.normal(0, 0.02, (n, risk.N_STRIP_COLS)),
        jkm_ret=rng.normal(0, 0.02, (n, risk.N_STRIP_COLS)),
        fx_ret=rng.normal(0, 0.01, n),
        end_date=pd.Timestamp("2026-07-07"), lookback=n, method="naive",
    )

    result = risk._vectorized_reprice(scen, D, base_hh, base_ttf, base_jkm,
                                       base_spot, base_o6, base_o1, charter, params)

    F, s = model.contract_calendar(pd.Timestamp(D))
    months = model.load_months(F, 12)

    worst = 0.0
    for i, m in enumerate(months):
        eu_flows, asia_flows = cashflows.legacy_cargo_quantities(params, m.year)
        eu_exp = cashflows.CargoExposure(route="Europe", month_index=i, cash_flows=eu_flows)
        asia_exp = cashflows.CargoExposure(route="Asia", month_index=i, cash_flows=asia_flows)
        for sc in range(n):
            eu_prices = {
                cashflows.RiskFactor.TTF: float(result["ttf_L"][sc, i]),
                cashflows.RiskFactor.FX: float(result["fx_l"][sc, i]),
                cashflows.RiskFactor.HH: float(result["hh_L"][sc, i]),
                cashflows.RiskFactor.CHARTER: charter,
                cashflows.RiskFactor.VLSFO: params.vlsfo_price,
            }
            asia_prices = {
                cashflows.RiskFactor.JKM: float(result["jkm_L1"][sc, i]),
                cashflows.RiskFactor.HH: float(result["hh_L"][sc, i]),
                cashflows.RiskFactor.CHARTER: charter,
                cashflows.RiskFactor.VLSFO: params.vlsfo_price,
            }
            eu_scalar = eu_exp.value(eu_prices)
            asia_scalar = asia_exp.value(asia_prices)
            worst = max(worst, abs(eu_scalar - result["eu_cargo"][sc, i]),
                        abs(asia_scalar - result["asia_cargo"][sc, i]))
            assert eu_scalar == pytest.approx(result["eu_cargo"][sc, i], rel=1e-9, abs=1e-6)
            assert asia_scalar == pytest.approx(result["asia_cargo"][sc, i], rel=1e-9, abs=1e-6)
    print(f"[{params_factory.__name__}] batched-vs-scalar worst abs error: {worst:.3e}")
