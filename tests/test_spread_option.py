from __future__ import annotations

import math
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

import data
import model
import spread_option as so


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


# --- price_spread_option(): pure math, no workbook needed ---


def test_at_the_money_extrinsic_matches_bachelier_closed_form():
    """At the money (forward_margin == 0), Bachelier's option value has a
    simple closed form: sigma_t / sqrt(2*pi) (since N(0)=0.5, phi(0) =
    1/sqrt(2*pi), and the (F-K)*N(d) term vanishes). Verified against that
    closed form computed independently of price_spread_option()'s own
    internals -- a check on the formula, not just the code path."""
    revenue_0, cost_0, strike = 10.0, 4.0, 6.0  # forward_margin = 0 exactly
    vol_revenue, vol_cost, correlation = 0.5, 0.3, 0.2
    T = 1.0
    vc = so.VolCorrInputs(vol_revenue, vol_cost, correlation, "test")

    result = so.price_spread_option(revenue_0, cost_0, strike, T, vc)

    dollar_vol_revenue = revenue_0 * vol_revenue
    dollar_vol_cost = cost_0 * vol_cost
    spread_variance = (
        dollar_vol_revenue ** 2 + dollar_vol_cost ** 2
        - 2 * correlation * dollar_vol_revenue * dollar_vol_cost
    )
    sigma_t = math.sqrt(spread_variance * T)
    expected_extrinsic = sigma_t / math.sqrt(2 * math.pi)

    assert result.forward_margin == pytest.approx(0.0)
    assert result.intrinsic == pytest.approx(0.0)
    assert result.extrinsic == pytest.approx(expected_extrinsic, rel=1e-9)
    assert result.option_value == pytest.approx(expected_extrinsic, rel=1e-9)


def test_zero_volatility_gives_zero_extrinsic():
    vc = so.VolCorrInputs(vol_revenue=0.0, vol_cost=0.0, correlation=0.5, source_detail="test")
    result = so.price_spread_option(revenue_0=10.0, cost_0=4.0, strike=3.0, time_to_expiry_years=2.0, vc=vc)
    assert result.forward_margin == pytest.approx(3.0)
    assert result.intrinsic == pytest.approx(3.0)
    assert result.extrinsic == pytest.approx(0.0, abs=1e-12)
    assert result.option_value == pytest.approx(result.intrinsic)


def test_zero_time_to_expiry_gives_zero_extrinsic():
    vc = so.VolCorrInputs(vol_revenue=0.6, vol_cost=0.6, correlation=0.5, source_detail="test")
    result = so.price_spread_option(revenue_0=10.0, cost_0=4.0, strike=3.0, time_to_expiry_years=0.0, vc=vc)
    assert result.extrinsic == pytest.approx(0.0, abs=1e-12)
    assert result.intrinsic == pytest.approx(3.0)


def test_negative_margin_still_has_positive_extrinsic():
    """Deep out-of-the-money still has positive time value -- correct
    option-pricing behaviour, not a bug: there is always some chance the
    spread turns positive before expiry."""
    vc = so.VolCorrInputs(vol_revenue=0.6, vol_cost=0.6, correlation=0.3, source_detail="test")
    result = so.price_spread_option(revenue_0=8.0, cost_0=4.0, strike=10.0, time_to_expiry_years=1.5, vc=vc)
    assert result.forward_margin < 0
    assert result.intrinsic == pytest.approx(0.0)
    assert result.extrinsic > 0.0
    assert result.option_value == pytest.approx(result.extrinsic)


def test_extrinsic_grows_with_time_to_expiry():
    vc = so.VolCorrInputs(vol_revenue=0.5, vol_cost=0.3, correlation=0.2, source_detail="test")
    short = so.price_spread_option(10.0, 4.0, 6.0, 0.1, vc)
    long = so.price_spread_option(10.0, 4.0, 6.0, 2.0, vc)
    assert long.extrinsic > short.extrinsic


def test_missing_vol_or_correlation_yields_none_option_value_but_intrinsic_still_computed():
    for vc in (
        so.VolCorrInputs(None, 0.3, 0.2, "test"),
        so.VolCorrInputs(0.5, None, 0.2, "test"),
        so.VolCorrInputs(0.5, 0.3, None, "test"),
    ):
        result = so.price_spread_option(10.0, 4.0, 3.0, 1.0, vc)
        assert result.forward_margin == pytest.approx(3.0)
        assert result.intrinsic == pytest.approx(3.0)
        assert result.option_value is None
        assert result.extrinsic is None


def test_negative_time_to_expiry_rejected():
    vc = so.VolCorrInputs(0.5, 0.3, 0.2, "test")
    with pytest.raises(ValueError):
        so.price_spread_option(10.0, 4.0, 3.0, -0.1, vc)


# --- tab_vol_corr(): reading data.load_volatilities()'s table ---


def _make_vol_table():
    idx = pd.Index([0, 1, 2, 5, 44], name="months_forward")
    return pd.DataFrame({
        "vol_TTF": [0.6] * 5,
        "vol_HH": [0.4] * 5,
        "vol_JKM": [0.55] * 5,
        "corr_TTF_HH": [0.5] * 5,
        "corr_TTF_JKM": [0.3] * 5,
    }, index=idx)


def test_tab_vol_corr_europe_reads_ttf_hh_columns():
    vc = so.tab_vol_corr(_make_vol_table(), months_forward=1, route="Europe")
    assert vc.vol_revenue == pytest.approx(0.6)
    assert vc.vol_cost == pytest.approx(0.4)
    assert vc.correlation == pytest.approx(0.5)


def test_tab_vol_corr_asia_missing_jkm_hh_correlation_returns_none():
    """The whole point of this module's documented gap: TTF/JKM
    correlation exists in the sheet but isn't what Asia's spread option
    (JKM revenue vs HH cost) needs."""
    vc = so.tab_vol_corr(_make_vol_table(), months_forward=1, route="Asia")
    assert vc.vol_revenue == pytest.approx(0.55)
    assert vc.vol_cost == pytest.approx(0.4)
    assert vc.correlation is None
    assert "Correlation JKM/HH" in vc.source_detail


def test_tab_vol_corr_clamps_beyond_available_tenor():
    vc = so.tab_vol_corr(_make_vol_table(), months_forward=100, route="Europe")
    assert vc.vol_revenue == pytest.approx(0.6)  # flat-extrapolated from the M+44 row


def test_tab_vol_corr_none_table_returns_all_none():
    vc = so.tab_vol_corr(None, months_forward=1, route="Europe")
    assert vc.vol_revenue is None and vc.vol_cost is None and vc.correlation is None


# --- historical_vol_corr(): realized vol/correlation from price history ---


class _FakeTables:
    def __init__(self, hh, ttf, jkm):
        self.hh, self.ttf, self.jkm = hh, ttf, jkm


def _synthetic_tables(n_days=70, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-01", periods=n_days)
    hh = np.cumprod(1 + rng.normal(0, 0.01, n_days)) * 3.0
    ttf = np.cumprod(1 + rng.normal(0, 0.015, n_days)) * 12.0
    jkm = np.cumprod(1 + rng.normal(0, 0.012, n_days)) * 14.0
    mkdf = lambda px: pd.DataFrame({"date": dates, "c1": px, "c2": px * 1.01})
    return _FakeTables(mkdf(hh), mkdf(ttf), mkdf(jkm)), dates


def test_historical_vol_corr_matches_numpy_reference():
    tables, dates = _synthetic_tables()
    D = dates[-1]
    vc = so.historical_vol_corr(tables, D, months_forward=1, route="europe", window_days=60)

    dates_window = [d for d in dates if D - pd.Timedelta(days=60) <= d <= D]
    hh_px = tables.hh.set_index("date").reindex(dates_window)["c1"].to_numpy()
    ttf_px = tables.ttf.set_index("date").reindex(dates_window)["c1"].to_numpy()
    hh_ret = np.diff(np.log(hh_px))
    ttf_ret = np.diff(np.log(ttf_px))

    expected_vol_hh = np.std(hh_ret, ddof=1) * math.sqrt(so.TRADING_DAYS_PER_YEAR)
    expected_vol_ttf = np.std(ttf_ret, ddof=1) * math.sqrt(so.TRADING_DAYS_PER_YEAR)
    expected_corr = np.corrcoef(ttf_ret, hh_ret)[0, 1]

    assert vc.vol_cost == pytest.approx(expected_vol_hh)
    assert vc.vol_revenue == pytest.approx(expected_vol_ttf)
    assert vc.correlation == pytest.approx(expected_corr)


def test_historical_vol_corr_uses_jkm_for_asia_route():
    tables, dates = _synthetic_tables()
    D = dates[-1]
    vc_eu = so.historical_vol_corr(tables, D, months_forward=1, route="europe", window_days=60)
    vc_asia = so.historical_vol_corr(tables, D, months_forward=1, route="asia", window_days=60)
    assert vc_eu.vol_revenue != pytest.approx(vc_asia.vol_revenue)
    assert vc_eu.vol_cost == pytest.approx(vc_asia.vol_cost)  # same HH cost series either route


def test_historical_vol_corr_insufficient_history_returns_none():
    tables, dates = _synthetic_tables(n_days=70)
    D = dates[-1]
    vc = so.historical_vol_corr(tables, D, months_forward=1, route="europe", window_days=1)
    assert vc.vol_revenue is None and vc.correlation is None
    assert "insufficient history" in vc.source_detail


# --- route_spread_option() / intrinsic_extrinsic_strip(): full wiring ---


def test_route_spread_option_forward_margin_matches_legacy_margin_exactly(tables, params):
    """The residual-strike derivation must reproduce model.strip()'s own
    eu_margin/asia_margin exactly, for any month -- this is the identity
    the whole module's intrinsic column depends on."""
    df = model.strip("2026-07-08", tables, params, n_months=12)
    for i in (0, 5, 11):
        row = df.iloc[i]
        eu = so.route_spread_option(row, params, tables, "2026-07-08", "europe", "tab")
        asia = so.route_spread_option(row, params, tables, "2026-07-08", "asia", "tab")
        assert eu.forward_margin == pytest.approx(row["eu_margin"])
        assert asia.forward_margin == pytest.approx(row["asia_margin"])
        assert eu.intrinsic == pytest.approx(max(row["eu_margin"], 0.0))
        assert asia.intrinsic == pytest.approx(max(row["asia_margin"], 0.0))


def test_route_spread_option_rejects_unknown_route(tables, params):
    df = model.strip("2026-07-08", tables, params, n_months=1)
    with pytest.raises(ValueError):
        so.route_spread_option(df.iloc[0], params, tables, "2026-07-08", "atlantis", "tab")


def test_intrinsic_extrinsic_strip_shape_and_asia_gap_in_tab_mode(tables, params):
    df = model.strip("2026-07-08", tables, params, n_months=36)
    out = so.intrinsic_extrinsic_strip(df, params, tables, "2026-07-08", "tab")
    assert len(out) == 36
    assert list(out.columns) == [
        "load_month", "month_label", "eu_intrinsic", "eu_extrinsic", "asia_intrinsic", "asia_extrinsic",
    ]
    assert out["eu_extrinsic"].notna().all()
    assert out["asia_extrinsic"].isna().all()  # missing Correlation JKM/HH in the real workbook today


def test_intrinsic_extrinsic_strip_historical_mode_populates_both_routes(tables, params):
    df = model.strip("2026-07-08", tables, params, n_months=6)
    out = so.intrinsic_extrinsic_strip(df, params, tables, "2026-07-08", "historical", window_days=60)
    assert out["eu_extrinsic"].notna().all()
    assert out["asia_extrinsic"].notna().all()
    assert (out["eu_extrinsic"] >= 0).all()
    assert (out["asia_extrinsic"] >= 0).all()


def test_intrinsic_extrinsic_strip_historical_caching_matches_per_row_calls(tables, params):
    """The _historical_dates cache threaded through intrinsic_extrinsic_strip
    for performance must not change results relative to calling
    route_spread_option() one row at a time."""
    df = model.strip("2026-07-08", tables, params, n_months=4)
    batched = so.intrinsic_extrinsic_strip(df, params, tables, "2026-07-08", "historical", window_days=60)
    for i in range(len(df)):
        eu = so.route_spread_option(df.iloc[i], params, tables, "2026-07-08", "europe", "historical", 60)
        asia = so.route_spread_option(df.iloc[i], params, tables, "2026-07-08", "asia", "historical", 60)
        assert batched.iloc[i]["eu_intrinsic"] == pytest.approx(eu.intrinsic)
        assert batched.iloc[i]["eu_extrinsic"] == pytest.approx(eu.extrinsic)
        assert batched.iloc[i]["asia_intrinsic"] == pytest.approx(asia.intrinsic)
        assert batched.iloc[i]["asia_extrinsic"] == pytest.approx(asia.extrinsic)
