"""Tests for R6 increment G (docs/R6_RISK_REBUILD_PLAN.md sect 6.G,
[R6.10, R6.11]): the contract-ID same-cargo backtest (G.2) and the
VaR/ES bootstrap uncertainty bands (G.3). G.1 (contract-ID scenario
returns) is tested alongside the existing roll_aligned tests in
tests/test_risk_containment.py, since that is where the pre-existing
build_scenarios()-method tests already live.

Groups:
  (a) same_cargo_backtest() (G.2) -- workbook-gated: shape/month-index
      countdown, cached-coefficient proof (cache-hit introspection, both
      bases), a value-equality cross-check against an independent direct
      computation, determinism, the out-of-window disclosure, and a
      timing smoke test.
  (b) bootstrap_var_es()/bootstrap_var_result() (G.3): seeded
      reproducibility (including a byte-level manual-RNG cross-check),
      band-brackets-point-estimate, band-widens-for-deeper-tail,
      n_tail disclosure, and the n<2 guard. Mostly pure (no workbook
      needed beyond one real VaR result for the realism-anchored group).
"""
from __future__ import annotations

import os
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

import cashflows
import data
import decision
import model
import risk

D = "2026-07-07"


@pytest.fixture(scope="module")
def tables():
    path = os.environ.get(data.ENV_VAR_NAME)
    if not path:
        local = Path(__file__).resolve().parents[1] / "LNG history.xlsx"
        path = str(local) if local.exists() else data.default_data_path()
    if not path:
        pytest.skip(f"set {data.ENV_VAR_NAME} to run workbook-backed tests")
    return data.load_all(str(path))


@pytest.fixture(autouse=True)
def _clean_quantity_caches():
    """Same convention as tests/test_physical_cashflows.py: cache state
    must never leak between tests."""
    cashflows.clear_quantity_caches()
    yield
    cashflows.clear_quantity_caches()


# A load month that sits inside the 12-month pricing window for most of a
# ~250-trading-day tail window ending at the workbook's latest date
# (verified: 177/250 dates valid, the rest correctly excluded and counted
# in skipped_out_of_window -- see group (a) below).
LOAD_MONTH = pd.Timestamp("2026-11-01")


# ===========================================================================
# (a) same_cargo_backtest() -- G.2
# ===========================================================================


def test_same_cargo_backtest_shape_and_month_index_countdown(tables):
    bt = risk.same_cargo_backtest(tables, model.operating_default_params(), LOAD_MONTH,
                                   basis="legacy", basin="Europe", lookback=260, window_days=30)
    assert not bt.empty
    assert list(bt.columns) == ["date", "next_date", "month_index", "var", "realised_pnl", "exception", "method"]
    assert (bt["method"] == "contract_id").all()
    # month_index only ever moves DOWN (or stays flat within a calendar
    # month) as D advances towards LOAD_MONTH -- never up, and never
    # jumps (no roll to jump across, unlike backtest_var()'s near-month
    # convention).
    diffs = np.diff(bt["month_index"].to_numpy())
    assert (diffs <= 0).all()
    assert (bt["month_index"] >= 0).all() and (bt["month_index"] < 12).all()
    assert np.isfinite(bt["var"]).all()
    assert np.isfinite(bt["realised_pnl"]).all()


def test_same_cargo_backtest_rejects_unknown_basis(tables):
    with pytest.raises(ValueError, match="legacy.*physical"):
        risk.same_cargo_backtest(tables, model.operating_default_params(), LOAD_MONTH, basis="nope")


def test_same_cargo_backtest_discloses_out_of_window_dates_and_is_fast(tables):
    """A window long enough to run past LOAD_MONTH's 12-month horizon
    must skip (and disclose, not silently drop) the out-of-range dates --
    mirrors backtest_var()'s skipped_roll_pairs convention. Also the
    validation-battery timing demonstration (plan sect 6.G.2: "a modest
    window, e.g. 250 dates, is fast (seconds)") -- see the implementation
    report for the actual measured figure; generous threshold here to
    avoid flaking on a slow CI runner."""
    t0 = time.perf_counter()
    bt = risk.same_cargo_backtest(tables, model.operating_default_params(), LOAD_MONTH,
                                   basis="legacy", basin="Europe", lookback=260, window_days=250)
    elapsed = time.perf_counter() - t0
    assert bt.attrs["skipped_out_of_window"] > 0
    assert bt.attrs["load_month"] == LOAD_MONTH
    assert bt.attrs["basis"] == "legacy"
    assert len(bt) > 0
    assert elapsed < 60.0, f"same-cargo backtest (250-day window) took {elapsed:.1f}s -- expected seconds"


@pytest.mark.parametrize("basis", ["legacy", "physical"])
def test_same_cargo_backtest_hits_the_quantity_cache_every_day_but_the_first(tables, basis):
    """G.2's headline claim: the SAME fixed delivery month means the
    quantity producer's cache key (Params field values, ..., LOAD_MONTH.
    year[, route, state, eua_live]) never changes across the whole
    window, so every assembly call after the very first must be a cache
    HIT -- proof this does NOT re-run the physical engine (or even
    re-derive the legacy formula) once per date."""
    params = model.operating_default_params()
    kwargs = dict(basis=basis, basin="Europe", lookback=260, window_days=30)
    if basis == "physical":
        kwargs["first_cargo_state"] = decision.FirstCargoState.FULLY_PRE_LIFT
    bt = risk.same_cargo_backtest(tables, params, LOAD_MONTH, **kwargs)
    assert not bt.empty

    info = cashflows.quantity_cache_info()[basis]
    assert info["misses"] == 1, f"expected exactly one distinct (params, year) key, got {info}"
    # One exposure is assembled per valid date; the backtest itself
    # reuses each date's exposure for both its own "realised" leg and
    # (as `prev_exposure`) the next row's VaR reprice, so the number of
    # assembly calls equals len(bt) + 1 (dates in the run) -- every call
    # but the first-ever one must hit.
    assert info["hits"] == len(bt)


def test_same_cargo_backtest_realised_pnl_matches_direct_computation(tables):
    """Value-equality cross-check (task brief's alternative to pure
    cache-hit introspection): independently recompute one row's realised
    P&L via legacy_cargo_cashflows() called directly (bypassing
    same_cargo_backtest() entirely) and compare."""
    params = model.operating_default_params()
    bt = risk.same_cargo_backtest(tables, params, LOAD_MONTH, basis="legacy", basin="Europe",
                                   lookback=260, window_days=30)
    assert not bt.empty
    row = bt.iloc[len(bt) // 2]

    eu_d, _ = cashflows.legacy_cargo_cashflows(row["date"], tables, params, int(row["month_index"]))
    val_d = eu_d.value(eu_d.base_prices)

    F_next, _ = model.contract_calendar(row["next_date"])
    mi_next = (LOAD_MONTH.year - F_next.year) * 12 + (LOAD_MONTH.month - F_next.month)
    eu_dn, _ = cashflows.legacy_cargo_cashflows(row["next_date"], tables, params, mi_next)
    val_dn = eu_dn.value(eu_dn.base_prices)

    assert (val_dn - val_d) == pytest.approx(row["realised_pnl"], rel=1e-9, abs=1e-3)


@pytest.mark.parametrize(
    ("basis", "alpha", "which"),
    [("legacy", 0.05, "var95"), ("legacy", 0.01, "var99"),
     ("physical", 0.05, "var95"), ("physical", 0.01, "var99")],
)
def test_same_cargo_backtest_var_leg_matches_historical_var(tables, basis, alpha, which):
    """Value-equality cross-check for the MODELLED (var) leg -- the novel
    G.2 output that feeds kupiec_test via exception = realised < var. The
    var is assembled INLINE in same_cargo_backtest (a bespoke per-basin
    prices dict, the [:, month_index] single-month slice, and the
    alpha -> var95/var99 selector), code not exercised by any other test;
    the realised leg is cross-checked above but the var leg was only ever
    asserted finite. Pin it against historical_var()/historical_var_physical()'s
    independently-maintained single-cargo VaR on the SAME date, month and
    scenario: same quantities + same scenario => equal to base-value
    reassociation noise (<$1 on a ~$3M VaR), while a finite-but-wrong
    regression (mapping HH to the TTF price array, an inverted alpha
    selector, an off-by-one month slice) would diverge by millions and
    fail here -- exactly the class every isfinite-only assertion misses."""
    params = model.operating_default_params()
    bt = risk.same_cargo_backtest(tables, params, LOAD_MONTH, basis=basis, basin="Europe",
                                   lookback=260, window_days=30, alpha=alpha, method="contract_id")
    assert not bt.empty
    row = bt.iloc[len(bt) // 2]
    d, mi = row["date"], int(row["month_index"])

    scen = risk.build_scenarios(tables, d, lookback=260, method="contract_id")
    if basis == "legacy":
        r = risk.historical_var(d, tables, params, portfolio="single", basin="Europe",
                                 month_index=mi, scen=scen)
    else:
        r = risk.historical_var_physical(d, tables, params, portfolio="single", basin="Europe",
                                          month_index=mi, scen=scen)
    assert row["var"] == pytest.approx(getattr(r, which), rel=1e-6, abs=1.0), \
        f"{basis} {which}: same_cargo var {row['var']} != independent historical_var {getattr(r, which)}"


def test_same_cargo_backtest_is_deterministic(tables):
    params = model.operating_default_params()
    bt1 = risk.same_cargo_backtest(tables, params, LOAD_MONTH, basis="legacy", basin="Europe",
                                    lookback=260, window_days=30)
    bt2 = risk.same_cargo_backtest(tables, params, LOAD_MONTH, basis="legacy", basin="Europe",
                                    lookback=260, window_days=30)
    pd.testing.assert_frame_equal(bt1, bt2)


def test_same_cargo_backtest_feeds_kupiec_test(tables):
    """Shape compatibility with the existing Kupiec-style exceedance
    check (plan sect 6.G.2's explicit requirement) -- no new statistic
    needed, the SAME kupiec_test() backtest_var() already uses."""
    bt = risk.same_cargo_backtest(tables, model.operating_default_params(), LOAD_MONTH,
                                   basis="legacy", basin="Europe", lookback=260, window_days=30)
    kt = risk.kupiec_test(int(bt["exception"].sum()), len(bt))
    assert kt["n"] == len(bt)
    assert kt["light"] in ("green", "yellow", "red")


def test_same_cargo_backtest_asia_basin_and_alpha99(tables):
    bt = risk.same_cargo_backtest(tables, model.operating_default_params(), LOAD_MONTH,
                                   basis="legacy", basin="Asia", lookback=260, window_days=30, alpha=0.01)
    assert not bt.empty
    assert np.isfinite(bt["var"]).all()


# ===========================================================================
# (b) bootstrap_var_es() / bootstrap_var_result() -- G.3
# ===========================================================================


@pytest.fixture(scope="module")
def real_pnl(tables):
    """One real 500-scenario P&L vector, reused by the realism-anchored
    tests below (bracket / widen / disclosure)."""
    params = model.operating_default_params()
    scen = risk.build_scenarios(tables, D, lookback=500, method="roll_aligned")
    r = risk.historical_var(D, tables, params, portfolio="single", basin="Europe", month_index=0, scen=scen)
    return r.pnl


def test_bootstrap_seeded_reproducible():
    rng = np.random.default_rng(7)
    pnl = rng.normal(-100_000, 500_000, size=500)
    b1 = risk.bootstrap_var_es(pnl, seed=123)
    b2 = risk.bootstrap_var_es(pnl, seed=123)
    assert b1 == b2


def test_bootstrap_different_seed_gives_different_bands():
    rng = np.random.default_rng(7)
    pnl = rng.normal(-100_000, 500_000, size=500)
    b1 = risk.bootstrap_var_es(pnl, seed=1)
    b2 = risk.bootstrap_var_es(pnl, seed=2)
    assert (b1.var95_lo, b1.var95_hi) != (b2.var95_lo, b2.var95_hi)


def test_bootstrap_matches_manual_rng_replication():
    """Byte-level proof that the documented seeding contract
    (np.random.default_rng(seed), integers(0, n, size=(B, n)), sort,
    slice) is EXACTLY what bootstrap_var_es() does -- not merely
    reproducible against itself, but against an independent
    re-implementation of the documented recipe."""
    pnl = np.array([1.0, -2.0, 3.5, -7.0, 0.25, -1.25, 4.0, -3.0, 2.2, -5.5])
    n, seed, B = len(pnl), 999, 50
    out = risk.bootstrap_var_es(pnl, n_resamples=B, seed=seed)

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(B, n))
    resampled = np.sort(pnl[idx], axis=1)
    i95, i99 = int(n * 0.05), int(n * 0.01)
    expected_var95 = float(np.percentile(resampled[:, i95], 5.0))
    expected_var95_hi = float(np.percentile(resampled[:, i95], 95.0))

    assert out.var95_lo == pytest.approx(expected_var95)
    assert out.var95_hi == pytest.approx(expected_var95_hi)


def test_bootstrap_requires_at_least_two_scenarios():
    with pytest.raises(ValueError, match="at least 2"):
        risk.bootstrap_var_es(np.array([1.0]))


def test_bootstrap_n_tail_disclosure_correct():
    pnl = np.zeros(500)
    out = risk.bootstrap_var_es(pnl, n_resamples=10)
    assert out.n == 500
    assert out.n_tail95 == max(int(500 * 0.05), 1) == 25
    assert out.n_tail99 == max(int(500 * 0.01), 1) == 5

    small = np.zeros(50)
    out_small = risk.bootstrap_var_es(small, n_resamples=10)
    assert out_small.n_tail99 == max(int(50 * 0.01), 1) == 1   # floor kicks in


def test_bootstrap_bands_bracket_the_point_estimate(real_pnl):
    var95, var99, es95, es99, sd = risk._var_es(real_pnl)
    bands = risk.bootstrap_var_es(real_pnl)
    assert bands.var95_lo <= var95 <= bands.var95_hi
    assert bands.var99_lo <= var99 <= bands.var99_hi
    assert bands.es95_lo <= es95 <= bands.es95_hi
    assert bands.es99_lo <= es99 <= bands.es99_hi


def test_bootstrap_band_widens_for_deeper_tail(real_pnl):
    """Deeper-tail metrics resample from fewer effective points (ES99:
    ~5 out of 500; VaR95: an order statistic near rank 25) and are
    therefore noisier -- the bootstrap band must widen from VaR95 to
    ES99 (plan sect 6.G.3's explicit acceptance criterion)."""
    bands = risk.bootstrap_var_es(real_pnl)
    width_var95 = bands.var95_hi - bands.var95_lo
    width_var99 = bands.var99_hi - bands.var99_lo
    width_es95 = bands.es95_hi - bands.es95_lo
    width_es99 = bands.es99_hi - bands.es99_lo
    assert width_var95 < width_es99
    assert width_var95 <= width_var99
    assert width_es95 <= width_es99


def test_bootstrap_var_result_wraps_pnl(real_pnl):
    r = risk.VarResult(pnl=real_pnl, var95=0, var99=0, es95=0, es99=0, sd=0, n=len(real_pnl),
                        portfolio="single", scen=None, basis="legacy")
    direct = risk.bootstrap_var_es(real_pnl)
    wrapped = risk.bootstrap_var_result(r)
    assert wrapped == direct


def test_bootstrap_default_constants_match_plan():
    """Documents the plan's own suggested figures (sect 6.G.3): 1,000
    resamples, a 90% band ([5th, 95th] percentile)."""
    assert risk.BOOTSTRAP_N_RESAMPLES == 1000
    assert (risk.BOOTSTRAP_LOWER_PCT, risk.BOOTSTRAP_UPPER_PCT) == (5.0, 95.0)
