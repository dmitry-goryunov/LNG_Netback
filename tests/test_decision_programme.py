from __future__ import annotations

import math
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

import data
import decision
import model


@pytest.fixture(scope="module")
def tables():
    path = os.environ.get(data.ENV_VAR_NAME)
    if not path:
        local = Path(__file__).resolve().parents[1] / "LNG history.xlsx"
        path = str(local) if local.exists() else data.default_data_path()
    if not path:
        pytest.skip(f"set {data.ENV_VAR_NAME} to run workbook-backed tests")
    return data.load_all(str(path))


@pytest.fixture()
def params():
    return model.Params()


def test_decision_mode_cost_policy():
    assert decision.cost_policy(decision.DecisionMode.POST_LIFT_DIVERSION, "procurement") == decision.CostTreatment.SUNK
    assert decision.cost_policy(decision.DecisionMode.POST_LIFT_DIVERSION, "loading") == decision.CostTreatment.SUNK
    assert decision.cost_policy(decision.DecisionMode.PRE_LIFT_CARGO, "procurement") == decision.CostTreatment.INCLUDED
    assert decision.cost_policy(decision.DecisionMode.VESSEL_PROGRAMME, "procurement", future_cargo=True) == decision.CostTreatment.INCLUDED
    assert decision.cost_policy(decision.DecisionMode.VESSEL_PROGRAMME, "charter") == decision.CostTreatment.INCLUDED


def test_post_lift_adds_back_only_sunk_procurement_and_loading(tables, params):
    df = model.strip("2026-07-08", tables, params)
    row = df.iloc[0]
    values = {v.route: v for v in decision.isolated_route_values(
        df, params, decision.DecisionMode.POST_LIFT_DIVERSION, month_index=0
    )}
    sunk = (row["proc"] + params.loading) * params.cargo_size
    assert values["Europe"].incremental_value == pytest.approx(row["eu_cargo"] + sunk)
    assert values["Asia"].incremental_value == pytest.approx(row["asia_cargo"] + sunk)
    assert values["Europe"].full_cargo_value == pytest.approx(row["eu_cargo"])


def test_pre_lift_value_remains_full_cargo_margin(tables, params):
    df = model.strip("2026-07-08", tables, params)
    row = df.iloc[0]
    values = {v.route: v for v in decision.isolated_route_values(
        df, params, decision.DecisionMode.PRE_LIFT_CARGO, month_index=0
    )}
    assert values["Europe"].incremental_value == pytest.approx(row["eu_cargo"])
    assert values["Asia"].incremental_value == pytest.approx(row["asia_cargo"])


def test_52_day_base_case_has_two_europe_cycles_and_one_asia_cycle(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_BASE)
    df = model.strip("2026-07-08", tables, params)
    result = decision.optimise_programme(
        df, params, horizon_days=52.0, max_additional_cargoes=1
    )
    plans = {p.sequence: p for p in result.alternatives}

    assert "Europe -> Europe" in plans
    assert "Asia" in plans
    assert plans["Europe -> Europe"].used_days == pytest.approx(2 * (model.EU_LEG_DAYS * 2 + 5.0))
    assert plans["Europe -> Europe"].residual_days == pytest.approx(0.11965811965811923)
    assert plans["Asia"].used_days == pytest.approx(model.ASIA_RT_BASE)
    assert result.best.sequence == "Europe -> Europe"


def test_future_cargo_uses_later_forward_month_and_pre_lift_value(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_BASE)
    df = model.strip("2026-07-08", tables, params)
    result = decision.optimise_programme(
        df, params, horizon_days=52.0, max_additional_cargoes=1
    )
    plan = next(p for p in result.alternatives if p.sequence == "Europe -> Europe")
    first, second = plan.legs

    assert first.month_index == 0
    assert second.month_index == 1
    assert second.load_month == pd.Timestamp("2026-09-01")
    assert second.decision_mode == decision.DecisionMode.PRE_LIFT_CARGO
    assert second.value == pytest.approx(df.iloc[1]["eu_cargo"])
    assert first.value > df.iloc[0]["eu_cargo"]  # procurement/loading are sunk for cargo 1


def test_54_day_congested_asia_is_infeasible(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_CONG)
    df = model.strip("2026-07-08", tables, params)
    result = decision.optimise_programme(
        df, params, horizon_days=54.0, max_additional_cargoes=1
    )
    assert all(plan.legs[0].route != "Asia" for plan in result.alternatives)
    assert "Europe -> Europe" in {p.sequence for p in result.alternatives}


def test_55_day_congested_asia_and_two_europe_cycles_both_fit(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_CONG)
    df = model.strip("2026-07-08", tables, params)
    result = decision.optimise_programme(
        df, params, horizon_days=55.0, max_additional_cargoes=1
    )
    plans = {p.sequence: p for p in result.alternatives}
    assert "Asia" in plans
    assert "Europe -> Europe" in plans
    assert plans["Asia"].residual_days == pytest.approx(55.0 - model.ASIA_RT_CONG)
    assert plans["Europe -> Europe"].residual_days == pytest.approx(55.0 - 2 * (2 * model.EU_LEG_DAYS + 5.0))


def test_no_fractional_or_partial_voyage_is_scheduled(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_BASE)
    df = model.strip("2026-07-08", tables, params)
    result = decision.optimise_programme(
        df, params, horizon_days=51.8, max_additional_cargoes=1
    )
    assert "Europe -> Europe" not in {p.sequence for p in result.alternatives}
    assert all(p.used_days <= 51.8 + 1e-9 for p in result.alternatives)


def test_residual_vessel_value_is_added_once(tables):
    params = model.Params(asia_rt_days=model.ASIA_RT_BASE)
    df = model.strip("2026-07-08", tables, params)
    rate = 100_000.0
    result = decision.optimise_programme(
        df, params, horizon_days=52.0, max_additional_cargoes=1,
        residual_value_per_day=rate,
    )
    plan = next(p for p in result.alternatives if p.sequence == "Asia")
    leg_sum = sum(leg.value for leg in plan.legs)
    assert plan.residual_value == pytest.approx(plan.residual_days * rate)
    assert plan.total_value == pytest.approx(leg_sum + plan.residual_value)


def test_fx_extrapolation_is_explicit_on_current_m12(tables, params):
    df = model.strip("2026-07-08", tables, params)
    flagged = model.fx_extrapolated_rows(df)
    assert not flagged.empty
    assert 11 in flagged.index
    assert bool(df.iloc[11]["fx_extrapolated"])
    assert df.iloc[11]["fx_tenor_months"] > 12.0
