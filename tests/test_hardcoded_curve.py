"""Tests for hardcoded_curve.py -- the built-in single-day forward-curve
fallback (app runs one date with no LNG history.xlsx present).

Two groups:
  (a) Pure tests (NO workbook -- run in CI): the snapshot rebuilds into a
      schema-correct CurveTables, and the deterministic engine
      (model.strip, decision route values) runs on it and returns finite
      numbers. These are the FIRST strip-based tests CI can run at all --
      every other strip test is workbook-gated; the committed snapshot
      gives CI real coverage of the pricing path.
  (b) Workbook-gated fidelity: the hardcoded snapshot reproduces the
      workbook's own strip() numbers at the snapshot date EXACTLY (it is
      the workbook's snapped rows for that date), so "no xls" and "xls
      loaded" agree to the last decimal for this one day.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

import data
import decision
import hardcoded_curve
import model


# ===========================================================================
# (a) Pure tests -- no workbook
# ===========================================================================


def test_build_hardcoded_tables_has_single_master_date_at_curve_date():
    tables = hardcoded_curve.build_hardcoded_tables()
    assert len(tables.master_dates) == 1
    assert tables.master_dates.max() == pd.Timestamp(hardcoded_curve.CURVE_DATE)
    assert "hardcoded" in tables.source
    assert tables.warnings and "hardcoded" in tables.warnings[0].lower()


def test_hardcoded_tables_have_loader_schema():
    tables = hardcoded_curve.build_hardcoded_tables()
    for df in (tables.hh, tables.ttf, tables.jkm, tables.fx, tables.charter):
        assert len(df) == 1
        assert "date" in df.columns
        assert pd.api.types.is_datetime64_any_dtype(df["date"])
    # strip needs c1.. on HH/TTF/JKM and the FX outrights.
    assert {"c1", "c13"}.issubset(tables.hh.columns)
    assert {"c1", "c36"}.issubset(tables.ttf.columns)
    assert {"spot", "o6", "o1", "o_y2", "o_y10"}.issubset(tables.fx.columns)
    assert "rate174" in tables.charter.columns
    # each table's own snapped date is on or before the curve date (snap()
    # returns the last row <= D, e.g. weekly charter, day-lagged HH).
    D = pd.Timestamp(hardcoded_curve.CURVE_DATE)
    for df in (tables.hh, tables.ttf, tables.jkm, tables.fx, tables.charter):
        assert df["date"].iloc[0] <= D


def test_volatilities_term_structure_present_and_indexed():
    tables = hardcoded_curve.build_hardcoded_tables()
    assert tables.vol is not None
    assert tables.vol.index.name == "months_forward"
    assert tables.vol.index.is_monotonic_increasing


@pytest.mark.parametrize("params_factory", [model.Params, model.operating_default_params],
                         ids=["legacy", "operating"])
def test_strip_runs_on_hardcoded_curve(params_factory):
    """The deterministic strip prices cleanly on the built-in curve, 36
    months, all finite -- the core 'produce numbers for one day with no
    xls' guarantee, and CI's only strip coverage."""
    tables = hardcoded_curve.build_hardcoded_tables()
    df = model.strip(hardcoded_curve.CURVE_DATE, tables, params_factory(), n_months=36)
    assert len(df) == 36
    for col in ("eu_cargo", "asia_cargo", "jkm_star"):
        assert np.isfinite(df[col].to_numpy(dtype=float)).all()


def test_decision_route_value_runs_on_hardcoded_curve():
    tables = hardcoded_curve.build_hardcoded_tables()
    params = model.operating_default_params()
    strip_df = model.strip(hardcoded_curve.CURVE_DATE, tables, params, n_months=36)
    row = strip_df.iloc[0]
    for route in ("Europe", "Asia"):
        rv = decision.route_value(row, params, route, mode=decision.DecisionMode.POST_LIFT_DIVERSION,
                                  month_index=0)
        assert np.isfinite(rv.incremental_value)


# ===========================================================================
# (b) Workbook-gated fidelity
# ===========================================================================


@pytest.fixture(scope="module")
def workbook_tables():
    path = os.environ.get(data.ENV_VAR_NAME)
    if not path:
        local = Path(__file__).resolve().parents[1] / "LNG history.xlsx"
        path = str(local) if local.exists() else data.default_data_path()
    if not path:
        pytest.skip(f"set {data.ENV_VAR_NAME} to run the workbook-fidelity test")
    return data.load_all(str(path))


@pytest.mark.parametrize("params_factory", [model.Params, model.operating_default_params],
                         ids=["legacy", "operating"])
def test_hardcoded_matches_workbook_strip_exactly(workbook_tables, params_factory):
    """The whole point: with no xls the built-in curve must give the SAME
    numbers the workbook gives for the snapshot date. Snapshot rows ARE the
    workbook's snapped rows for that date, so the match is exact."""
    hc = hardcoded_curve.build_hardcoded_tables()
    params = params_factory()
    D = hardcoded_curve.CURVE_DATE
    s_wb = model.strip(D, workbook_tables, params, n_months=36)
    s_hc = model.strip(D, hc, params, n_months=36)
    for col in ("eu_cargo", "asia_cargo", "jkm_star", "TTF", "JKM", "HH", "fx"):
        wb = s_wb[col].to_numpy(dtype=float)
        hcv = s_hc[col].to_numpy(dtype=float)
        assert np.allclose(wb, hcv, rtol=0, atol=1e-9), f"{col} differs"
    assert (s_wb["verdict"].values == s_hc["verdict"].values).all()
