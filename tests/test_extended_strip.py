"""Tests for the n_months>12 strip and the multi-tenor FX curve that
supports it (model.fx_curve_multi, data.load_fx's o_y2..o_y10 columns).

model.strip()'s n_months=12 default must stay byte-identical to every
prior version -- that is what the frozen tests/test_model.py fixtures
pin -- so several tests here exist specifically to prove the two code
paths (n_months<=12 vs n_months>12) never touch the same output for the
default case.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

import data
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


def test_load_fx_year_tenors_are_corrected_and_plausible(tables):
    row = tables.fx[tables.fx["date"] == "2026-07-08"].iloc[0]
    # Hand-derived from the raw sheet: spot=1.1417, raw y2=1.48079 ->
    # corrected = spot + (raw-spot)/10.
    assert row["o_y2"] == pytest.approx(1.175609, abs=1e-6)
    assert row["o_y10"] == pytest.approx(1.290798, abs=1e-6)
    # Monotonic-ish and within a few tens of percent of spot -- the
    # raw/uncorrected 10Y value would be 2.3x spot, which validate()
    # already rejects at load time; this is a second, direct check.
    assert abs(row["o_y10"] - row["spot"]) / row["spot"] < 0.30


def test_n_months_12_default_is_untouched(tables, params):
    """The exact call the frozen legacy suite makes must be unaffected by
    fx_curve_multi/n_months existing at all."""
    D = "2026-07-08"
    default = model.strip(D, tables, params)
    explicit_12 = model.strip(D, tables, params, n_months=12)
    pd.testing.assert_frame_equal(
        default.drop(columns=["load_month"]), explicit_12.drop(columns=["load_month"])
    )
    assert len(default) == 12
    assert default.attrs["fx_extrap_boundary_months"] == 12.0


def test_n_months_36_covers_three_years_with_no_extrapolation(tables, params):
    D = "2026-07-08"
    df = model.strip(D, tables, params, n_months=36)
    assert len(df) == 36
    assert df.iloc[0]["month_label"] == "Aug-26"
    assert df.iloc[-1]["month_label"] == "Jul-29"
    assert model.fx_extrapolated_rows(df).empty
    assert df.attrs["fx_extrap_boundary_months"] > 12.0


def test_first_eleven_months_identical_between_12_and_36_month_strips(tables, params):
    """fx_curve() and fx_curve_multi() must agree exactly everywhere they
    share real anchors (spot/6M/1Y) -- only the extrapolated tail (here,
    just M12) may differ."""
    D = "2026-07-08"
    df12 = model.strip(D, tables, params, n_months=12)
    df36 = model.strip(D, tables, params, n_months=36)
    for i in range(11):
        assert df12.iloc[i]["eu_margin"] == pytest.approx(df36.iloc[i]["eu_margin"], abs=1e-9)
        assert df12.iloc[i]["asia_margin"] == pytest.approx(df36.iloc[i]["asia_margin"], abs=1e-9)
        assert df12.iloc[i]["fx"] == pytest.approx(df36.iloc[i]["fx"], abs=1e-9)


def test_month_12_uses_interpolation_not_extrapolation_when_extended(tables, params):
    D = "2026-07-08"
    df12 = model.strip(D, tables, params, n_months=12)
    df36 = model.strip(D, tables, params, n_months=36)
    assert bool(df12.iloc[11]["fx_extrapolated"]) is True
    assert bool(df36.iloc[11]["fx_extrapolated"]) is False
    # Continuous at the boundary: the two curves agree almost exactly this
    # close to 1Y, even though one extrapolates and the other interpolates
    # toward a real 2Y anchor.
    assert df12.iloc[11]["fx"] == pytest.approx(df36.iloc[11]["fx"], abs=2e-3)


def test_n_months_beyond_available_columns_raises_clear_error(tables, params):
    with pytest.raises(ValueError, match="exceeds available forward columns"):
        model.strip("2026-07-08", tables, params, n_months=100)


def test_fx_curve_multi_interpolates_through_known_anchors(tables):
    D = pd.Timestamp("2026-07-08")
    fx_row = tables.fx[tables.fx["date"] == D].iloc[0]
    fx = model.fx_curve_multi(fx_row, D)
    assert fx.max_anchor_months == pytest.approx(120.0)

    # Exactly at the 1Y and 2Y anchors (mid-month 15th, 12/24 months out).
    one_year = pd.Timestamp(year=2027, month=7, day=15)
    two_year = pd.Timestamp(year=2028, month=7, day=15)
    assert fx(one_year) == pytest.approx(float(fx_row["o1"]), abs=2e-3)
    assert fx(two_year) == pytest.approx(float(fx_row["o_y2"]), abs=2e-3)

    # Midpoint between 1Y and 2Y should sit between the two anchors --
    # this is exactly the segment the old fx_curve() would have
    # extrapolated straight through instead of bending toward.
    mid = pd.Timestamp(year=2028, month=1, day=15)
    lo, hi = sorted([float(fx_row["o1"]), float(fx_row["o_y2"])])
    assert lo <= fx(mid) <= hi


def test_fx_curve_multi_extrapolates_only_past_the_last_anchor(tables):
    """Beyond the 10Y anchor, fx() must continue the 9Y->10Y segment's
    exact slope. Verified against the formula directly (not by comparing
    three arbitrary calendar dates -- calendar years aren't uniformly
    30.44*12 days apart, so that comparison is only approximately linear
    and was a false failure, not a real one)."""
    D = pd.Timestamp("2026-07-08")
    fx_row = tables.fx[tables.fx["date"] == D].iloc[0]
    fx = model.fx_curve_multi(fx_row, D)

    far = pd.Timestamp(year=2040, month=1, day=15)
    t = (far - D).days / 30.44
    assert t > fx.max_anchor_months

    t9, v9 = 108.0, float(fx_row["o_y9"])
    t10, v10 = 120.0, float(fx_row["o_y10"])
    expected = v9 + (v10 - v9) * (t - t9) / (t10 - t9)
    assert fx(far) == pytest.approx(expected, abs=1e-9)
