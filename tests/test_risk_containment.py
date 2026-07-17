from __future__ import annotations

import copy
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

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
