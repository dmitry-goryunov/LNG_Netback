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


# --- price_exchange_option(): pure math, no workbook needed ---


def test_at_the_money_option_value_matches_margrabe_closed_form():
    """At the money (jkm_0 == ttf_0 == X), Margrabe's formula has a
    simple closed form: X*(N(0.5*sigma*sqrt(T)) - N(-0.5*sigma*sqrt(T))),
    computed independently of price_exchange_option()'s own internals --
    a check on the formula, not just the code path."""
    X = 12.0
    vol_jkm, vol_ttf, correlation = 0.5, 0.4, 0.2
    T = 1.5
    vc = so.VolCorrInputs(vol_jkm, vol_ttf, correlation, "test")

    result = so.price_exchange_option(X, X, T, vc)

    sigma_sq = vol_jkm ** 2 + vol_ttf ** 2 - 2 * correlation * vol_jkm * vol_ttf
    sigma_t = math.sqrt(sigma_sq * T)
    norm_cdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    expected_value = X * (norm_cdf(0.5 * sigma_t) - norm_cdf(-0.5 * sigma_t))

    assert result.intrinsic == pytest.approx(0.0)
    assert result.option_value == pytest.approx(expected_value, rel=1e-9)
    assert result.extrinsic == pytest.approx(expected_value, rel=1e-9)


def test_zero_volatility_gives_zero_extrinsic():
    vc = so.VolCorrInputs(vol_jkm=0.0, vol_ttf=0.0, correlation=0.5, source_detail="test")
    result = so.price_exchange_option(jkm_0=17.0, ttf_0=15.0, time_to_expiry_years=2.0, vc=vc)
    assert result.intrinsic == pytest.approx(2.0)
    assert result.extrinsic == pytest.approx(0.0, abs=1e-12)
    assert result.option_value == pytest.approx(result.intrinsic)


def test_zero_time_to_expiry_gives_zero_extrinsic():
    vc = so.VolCorrInputs(vol_jkm=0.6, vol_ttf=0.6, correlation=0.5, source_detail="test")
    result = so.price_exchange_option(jkm_0=17.0, ttf_0=15.0, time_to_expiry_years=0.0, vc=vc)
    assert result.extrinsic == pytest.approx(0.0, abs=1e-12)
    assert result.intrinsic == pytest.approx(2.0)


def test_ttf_above_jkm_still_has_positive_extrinsic():
    """Zero intrinsic (TTF currently the better market) still has positive
    time value -- correct option-pricing behaviour: there is always some
    chance JKM overtakes TTF before expiry."""
    vc = so.VolCorrInputs(vol_jkm=0.6, vol_ttf=0.6, correlation=0.3, source_detail="test")
    result = so.price_exchange_option(jkm_0=13.0, ttf_0=15.0, time_to_expiry_years=1.0, vc=vc)
    assert result.intrinsic == pytest.approx(0.0)
    assert result.extrinsic > 0.0
    assert result.option_value == pytest.approx(result.extrinsic)


def test_extrinsic_grows_with_time_to_expiry():
    vc = so.VolCorrInputs(vol_jkm=0.5, vol_ttf=0.4, correlation=0.2, source_detail="test")
    short = so.price_exchange_option(17.0, 15.0, 0.1, vc)
    long = so.price_exchange_option(17.0, 15.0, 2.0, vc)
    assert long.extrinsic > short.extrinsic


def test_higher_correlation_reduces_extrinsic():
    """JKM and TTF moving together more tightly leaves less room for the
    spread itself to move, so extrinsic (spread option time value) should
    shrink as correlation rises towards 1, all else equal."""
    low_corr = so.price_exchange_option(17.0, 15.0, 1.0, so.VolCorrInputs(0.5, 0.5, 0.0, "test"))
    high_corr = so.price_exchange_option(17.0, 15.0, 1.0, so.VolCorrInputs(0.5, 0.5, 0.9, "test"))
    assert high_corr.extrinsic < low_corr.extrinsic


def test_missing_vol_or_correlation_yields_none_option_value_but_intrinsic_still_computed():
    for vc in (
        so.VolCorrInputs(None, 0.4, 0.2, "test"),
        so.VolCorrInputs(0.5, None, 0.2, "test"),
        so.VolCorrInputs(0.5, 0.4, None, "test"),
    ):
        result = so.price_exchange_option(17.0, 15.0, 1.0, vc)
        assert result.intrinsic == pytest.approx(2.0)
        assert result.option_value is None
        assert result.extrinsic is None


def test_negative_time_to_expiry_rejected():
    vc = so.VolCorrInputs(0.5, 0.4, 0.2, "test")
    with pytest.raises(ValueError):
        so.price_exchange_option(17.0, 15.0, -0.1, vc)


def test_non_positive_prices_rejected():
    vc = so.VolCorrInputs(0.5, 0.4, 0.2, "test")
    with pytest.raises(ValueError):
        so.price_exchange_option(0.0, 15.0, 1.0, vc)
    with pytest.raises(ValueError):
        so.price_exchange_option(17.0, -1.0, 1.0, vc)


# --- tab_vol_corr(): reading data.load_volatilities()'s table ---


def _make_complete_vol_table():
    idx = pd.Index([0, 1, 2, 5, 44], name="months_forward")
    return pd.DataFrame({
        "vol_TTF": [0.6] * 5,
        "vol_HH": [0.4] * 5,
        "vol_JKM": [0.55] * 5,
        "corr_TTF_HH": [0.5] * 5,
        "corr_TTF_JKM": [0.3] * 5,
    }, index=idx)


def _make_incomplete_vol_table():
    idx = pd.Index([0, 1], name="months_forward")
    return pd.DataFrame({"vol_TTF": [0.6, 0.6], "vol_HH": [0.4, 0.4]}, index=idx)


def test_tab_vol_corr_reads_ttf_jkm_and_their_correlation():
    vc = so.tab_vol_corr(_make_complete_vol_table(), months_forward=1)
    assert vc.vol_jkm == pytest.approx(0.55)
    assert vc.vol_ttf == pytest.approx(0.6)
    assert vc.correlation == pytest.approx(0.3)


def test_tab_vol_corr_clamps_beyond_available_tenor():
    vc = so.tab_vol_corr(_make_complete_vol_table(), months_forward=100)
    assert vc.vol_jkm == pytest.approx(0.55)  # flat-extrapolated from the M+44 row


def test_tab_vol_corr_none_table_returns_all_none():
    vc = so.tab_vol_corr(None, months_forward=1)
    assert vc.vol_jkm is None and vc.vol_ttf is None and vc.correlation is None


def test_tab_vol_corr_missing_columns_returns_none_gracefully():
    """Sheet without Volatility JKM / Correlation TTF-JKM (e.g. an older
    workbook that hasn't been updated yet) must degrade to None, not
    KeyError."""
    vc = so.tab_vol_corr(_make_incomplete_vol_table(), months_forward=1)
    assert vc.vol_jkm is None
    assert vc.correlation is None
    assert "missing" in vc.source_detail.lower()


# --- historical_vol_corr(): realized vol/correlation from price history ---


class _FakeTables:
    def __init__(self, ttf, jkm):
        self.ttf, self.jkm = ttf, jkm


def _synthetic_tables(n_days=70, seed=11):
    """Independent series per column so tenor-alignment tests can tell
    c1 from c2 apart (c2 as a scalar multiple of c1 would have identical
    log returns and defeat the purpose)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-01", periods=n_days)

    def mkdf(base, vol1, vol2):
        c1 = np.cumprod(1 + rng.normal(0, vol1, n_days)) * base
        c2 = np.cumprod(1 + rng.normal(0, vol2, n_days)) * base
        return pd.DataFrame({"date": dates, "c1": c1, "c2": c2})

    return _FakeTables(mkdf(12.0, 0.015, 0.010), mkdf(14.0, 0.030, 0.012)), dates


def test_historical_vol_corr_matches_numpy_reference():
    tables, dates = _synthetic_tables()
    D = dates[-1]
    vc = so.historical_vol_corr(tables, D, ttf_contract=1, jkm_contract=2, window_days=60)

    dates_window = [d for d in dates if D - pd.Timedelta(days=60) <= d <= D]
    ttf_px = tables.ttf.set_index("date").reindex(dates_window)["c1"].to_numpy()
    jkm_px = tables.jkm.set_index("date").reindex(dates_window)["c2"].to_numpy()
    ttf_ret = np.diff(np.log(ttf_px))
    jkm_ret = np.diff(np.log(jkm_px))

    expected_vol_ttf = np.std(ttf_ret, ddof=1) * math.sqrt(so.TRADING_DAYS_PER_YEAR)
    expected_vol_jkm = np.std(jkm_ret, ddof=1) * math.sqrt(so.TRADING_DAYS_PER_YEAR)
    expected_corr = np.corrcoef(jkm_ret, ttf_ret)[0, 1]

    assert vc.vol_ttf == pytest.approx(expected_vol_ttf)
    assert vc.vol_jkm == pytest.approx(expected_vol_jkm)
    assert vc.correlation == pytest.approx(expected_corr)


def test_historical_vol_corr_insufficient_history_returns_none():
    tables, dates = _synthetic_tables(n_days=70)
    D = dates[-1]
    vc = so.historical_vol_corr(tables, D, ttf_contract=1, jkm_contract=2, window_days=1)
    assert vc.vol_jkm is None and vc.correlation is None
    assert "insufficient history" in vc.source_detail


def test_month_spread_option_uses_strips_jkm_contract_not_ttf_tenor():
    """The tenor-alignment regression guard for the review's biggest
    finding: model.strip() prices the front month's JKM leg at contract
    c2 (delivery L+1, roll shift s=0 when day(D) <= 15), so the vol used
    to price the option must come from JKM c2's returns -- an earlier
    version used JKM c1 (the noisy expiring contract) and overstated
    front-month extrinsic ~4x on real data. The synthetic tables give c1
    triple c2's vol, so picking the wrong column fails loudly here."""
    tables, dates = _synthetic_tables()
    D = dates[-1]  # a business day; may fall either side of the 15th
    s = 0 if D.day <= 15 else 1
    load_month = pd.Timestamp(year=D.year, month=D.month, day=1) + pd.DateOffset(months=1)

    row = {"JKM": 14.0, "ttf_usd": 12.0, "load_month": load_month}
    result = so.month_spread_option(row, tables, D, "historical", window_days=60)

    expected = so.historical_vol_corr(tables, D, ttf_contract=1, jkm_contract=2 - s, window_days=60)
    wrong = so.historical_vol_corr(tables, D, ttf_contract=1, jkm_contract=1 + s, window_days=60)
    assert result.vol_jkm == pytest.approx(expected.vol_jkm)
    assert result.vol_ttf == pytest.approx(expected.vol_ttf)
    assert result.correlation == pytest.approx(expected.correlation)
    assert result.vol_jkm != pytest.approx(wrong.vol_jkm)


def test_tab_vol_corr_jkm_read_one_delivery_month_further_out():
    """Tab mode mirrors the same alignment: the JKM leg delivers L+1, so
    its vol is read at tenor months_forward+1 while TTF stays at
    months_forward."""
    idx = pd.Index([0, 1, 2, 3], name="months_forward")
    table = pd.DataFrame({
        "vol_TTF": [0.60, 0.61, 0.62, 0.63],
        "vol_JKM": [0.50, 0.51, 0.52, 0.53],
        "corr_TTF_JKM": [0.30, 0.31, 0.32, 0.33],
    }, index=idx)
    vc = so.tab_vol_corr(table, months_forward=1)
    assert vc.vol_ttf == pytest.approx(0.61)   # tenor 1 (TTF delivers L)
    assert vc.vol_jkm == pytest.approx(0.52)   # tenor 2 (JKM delivers L+1)
    assert vc.correlation == pytest.approx(0.31)  # nearer (TTF) tenor


# --- month_spread_option() / intrinsic_extrinsic_strip(): full wiring ---


def test_month_spread_option_intrinsic_matches_max_jkm_minus_ttf(tables, params):
    df = model.strip("2026-07-08", tables, params, n_months=12)
    for i in (0, 5, 11):
        row = df.iloc[i]
        result = so.month_spread_option(row, tables, "2026-07-08", "tab")
        expected_intrinsic = max(float(row["JKM"]) - float(row["ttf_usd"]), 0.0)
        assert result.jkm_0 == pytest.approx(row["JKM"])
        assert result.ttf_0 == pytest.approx(row["ttf_usd"])
        assert result.intrinsic == pytest.approx(expected_intrinsic)


def test_month_spread_option_rejects_unknown_source(tables, params):
    df = model.strip("2026-07-08", tables, params, n_months=1)
    with pytest.raises(ValueError):
        so.month_spread_option(df.iloc[0], tables, "2026-07-08", "made_up_source")


def test_intrinsic_extrinsic_strip_shape_and_full_coverage(tables, params):
    """Unlike an earlier per-route revenue-vs-cost design, this JKM-vs-TTF
    formula's tab-mode inputs (Volatility JKM/TTF, Correlation TTF/JKM)
    are all present in the real workbook -- both columns should be fully
    populated, in both modes."""
    df = model.strip("2026-07-08", tables, params, n_months=36)
    for source in ("tab", "historical"):
        out = so.intrinsic_extrinsic_strip(df, tables, "2026-07-08", source)
        assert len(out) == 36
        assert list(out.columns) == [
            "load_month", "month_label", "vol_jkm", "vol_ttf", "correlation", "intrinsic", "extrinsic",
        ]
        for col in ("vol_jkm", "vol_ttf", "correlation", "intrinsic", "extrinsic"):
            assert out[col].notna().all(), f"{col} should be fully populated in {source!r} mode"
        assert (out["vol_jkm"] > 0).all()
        assert (out["vol_ttf"] > 0).all()
        assert out["correlation"].between(-1.0, 1.0).all()
        assert (out["intrinsic"] >= 0).all()
        assert (out["extrinsic"] >= 0).all()


def test_intrinsic_extrinsic_strip_vol_corr_columns_match_month_spread_option(tables, params):
    """The strip's vol_jkm/vol_ttf/correlation columns must be exactly the
    inputs month_spread_option() actually priced that month's option
    with, not some independently recomputed or approximated figure."""
    df = model.strip("2026-07-08", tables, params, n_months=12)
    out = so.intrinsic_extrinsic_strip(df, tables, "2026-07-08", "tab")
    for i in range(len(df)):
        single = so.month_spread_option(df.iloc[i], tables, "2026-07-08", "tab")
        assert out.iloc[i]["vol_jkm"] == pytest.approx(single.vol_jkm)
        assert out.iloc[i]["vol_ttf"] == pytest.approx(single.vol_ttf)
        assert out.iloc[i]["correlation"] == pytest.approx(single.correlation)


def test_intrinsic_extrinsic_strip_vol_corr_columns_are_nan_together_with_extrinsic_when_incomplete(tables, params):
    """Section 10-style guard: if a future edit strips a needed tab column
    again, the vol/correlation columns must go NaN in lockstep with
    extrinsic, not show a stale or partial number next to an N/A."""
    df = model.strip("2026-07-08", tables, params, n_months=3)

    class _EmptyVolTables:
        def __init__(self, real):
            self._real = real
            self.vol = None
        def __getattr__(self, name):
            return getattr(self._real, name)

    out = so.intrinsic_extrinsic_strip(df, _EmptyVolTables(tables), "2026-07-08", "tab")
    assert out["vol_jkm"].isna().all()
    assert out["vol_ttf"].isna().all()
    assert out["correlation"].isna().all()
    assert out["extrinsic"].isna().all()
    assert out["intrinsic"].notna().all()  # intrinsic never depends on vol/correlation


def test_intrinsic_extrinsic_strip_historical_caching_matches_per_row_calls(tables, params):
    """The _historical_dates cache threaded through intrinsic_extrinsic_strip
    for performance must not change results relative to calling
    month_spread_option() one row at a time."""
    df = model.strip("2026-07-08", tables, params, n_months=6)
    batched = so.intrinsic_extrinsic_strip(df, tables, "2026-07-08", "historical", window_days=60)
    for i in range(len(df)):
        single = so.month_spread_option(df.iloc[i], tables, "2026-07-08", "historical", 60)
        assert batched.iloc[i]["vol_jkm"] == pytest.approx(single.vol_jkm)
        assert batched.iloc[i]["vol_ttf"] == pytest.approx(single.vol_ttf)
        assert batched.iloc[i]["correlation"] == pytest.approx(single.correlation)
        assert batched.iloc[i]["intrinsic"] == pytest.approx(single.intrinsic)
        assert batched.iloc[i]["extrinsic"] == pytest.approx(single.extrinsic)
