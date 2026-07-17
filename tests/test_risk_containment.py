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
