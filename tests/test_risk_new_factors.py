"""Tests for R6 increment E (docs/R6_RISK_REBUILD_PLAN.md sect 6.E): new
risk factors -- charter stress rows + optional overlay (E.1), data-gated
VLSFO/EUA (E.2), and the factor-coverage disclosure (E.3).

Groups:
  (a) Charter stress rows (E.1(a)) -- workbook-gated: independent
      cross-checks of the three new run_stress_tests() rows (which are
      ALSO pinned bit-exact in test_physical_cashflows.py's stress
      group; this file re-derives them a SECOND, formula-level way).
  (b) Charter overlay (E.1(b)) -- pure unit tests (synthetic series) plus
      workbook-gated wiring/determinism/portfolio-coverage checks.
  (c) Gate-4 fixture freeze (E.1(c)) -- workbook-gated: everything this
      increment adds must leave the frozen legacy VaR fixtures
      (tests/test_model.py's own GATE 4a/4b numbers) unchanged, proven
      independently via pytest rather than trusting the frozen suite
      alone stayed green.
  (d) VLSFO/EUA loaders (E.2, data.py) -- a synthetic in-memory workbook
      (openpyxl, no real sheet exists yet) plus tolerate-absence checks
      against the real workbook.
  (e) VLSFO/EUA scenario wiring (E.2, risk.py ScenarioSet/
      build_scenarios()/repricers) -- synthetic CurveTables
      (dataclasses.replace onto the real fixture), both bases for
      VLSFO, physical-only for EUA.
  (f) EUA re-split (E.2, cashflows.py `eua_live`) -- the folded-vs-live
      cash-flow shape change, both paths tested per the plan's own
      instruction ("test BOTH paths").
  (g) Factor-coverage line (E.3) -- pure string-content tests.
"""
from __future__ import annotations

import dataclasses
import io
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import openpyxl
import pandas as pd
import pytest

import cashflows
import data
import decision
import model
import risk
from cashflows import RiskFactor

D = "2026-07-08"


@pytest.fixture(scope="module")
def workbook_path():
    path = os.environ.get(data.ENV_VAR_NAME)
    if not path:
        local = Path(__file__).resolve().parents[1] / "LNG history.xlsx"
        path = str(local) if local.exists() else data.default_data_path()
    if not path:
        pytest.skip(f"set {data.ENV_VAR_NAME} to run workbook-backed tests")
    return path


@pytest.fixture(scope="module")
def tables(workbook_path):
    return data.load_all(workbook_path)


@pytest.fixture(autouse=True)
def _clean_quantity_caches():
    cashflows.clear_quantity_caches()
    yield
    cashflows.clear_quantity_caches()


def _synthetic_price_series(seed: int, start="2010-01-01", end="2026-12-31", base: float = 500.0) -> pd.DataFrame:
    """A small, reproducible, non-trivial daily price path -- stands in
    for a VLSFO/EUA sheet that doesn't exist yet (plan sect 5).
    Business-day-spaced (pd.bdate_range) so it overlaps the real HS-VaR
    scenario window for any lookback this test file uses; log-return
    path (not iid prices) so consecutive-row returns are non-trivial and
    finite everywhere."""
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    log_ret = rng.normal(0.0, 0.01, size=len(dates) - 1)
    log_path = np.concatenate([[0.0], np.cumsum(log_ret)])
    price = base * np.exp(log_path)
    return pd.DataFrame({"date": dates, "price": price})


def _with_synthetic_vlsfo(tables_obj, seed=1, base=500.0):
    return dataclasses.replace(tables_obj, vlsfo=_synthetic_price_series(seed, base=base))


def _with_synthetic_eua(tables_obj, seed=2, base=75.0):
    return dataclasses.replace(tables_obj, eua=_synthetic_price_series(seed, base=base))


def _with_synthetic_vlsfo_eua(tables_obj, vlsfo_seed=1, eua_seed=2, vlsfo_base=500.0, eua_base=75.0):
    return dataclasses.replace(
        tables_obj,
        vlsfo=_synthetic_price_series(vlsfo_seed, base=vlsfo_base),
        eua=_synthetic_price_series(eua_seed, base=eua_base),
    )


def _zero_scenario(n: int = 1, **overrides) -> risk.ScenarioSet:
    base = dict(
        dates=[pd.Timestamp("2026-07-07")] * n,
        hh_ret=np.zeros((n, 13)), ttf_ret=np.zeros((n, 13)), jkm_ret=np.zeros((n, 13)),
        fx_ret=np.zeros(n), end_date=pd.Timestamp("2026-07-07"), lookback=n, method="naive",
    )
    base.update(overrides)
    return risk.ScenarioSet(**base)


# ===========================================================================
# (a) Charter stress rows (E.1(a)) -- independent cross-check
# ===========================================================================


@pytest.mark.parametrize("params_factory", [model.Params, model.operating_default_params], ids=["legacy", "operating"])
def test_charter_stress_rows_match_independent_quantity_times_shock(tables, params_factory):
    """Charter's cash flow is single-factor (CHARTER-linear, no cross
    term), so a pure price bump's P&L delta must equal EXACTLY
    quantity_on(CHARTER) * shock_size -- recompute that independently
    here for the M1 spread (asia - eu quantities) rather than trusting
    run_stress_tests()'s own arithmetic (bit-exact-pinned separately in
    test_physical_cashflows.py; this is a SECOND, formula-level check)."""
    params = params_factory()
    ch_row = model.snap(tables.charter, D)
    base_charter = params.charter_override if params.charter_override is not None else float(ch_row["rate174"])

    eu, asia = cashflows.legacy_cargo_cashflows(D, tables, params, 0)
    d_charter_eu = eu.quantity_on((RiskFactor.CHARTER,))
    d_charter_asia = asia.quantity_on((RiskFactor.CHARTER,))

    df = risk.run_stress_tests(D, tables, params, basis="legacy")
    for label, shock in (("Charter +$25k/day", 25_000.0), ("Charter -$25k/day", -25_000.0),
                          ("Charter +50%", base_charter * 0.5)):
        row = df[df["scenario"] == label].iloc[0]
        expected_spread_delta = (d_charter_asia - d_charter_eu) * shock
        assert row["pnl_m1_spread"] == pytest.approx(expected_spread_delta, rel=1e-9, abs=1.0)


def test_charter_stress_rows_are_linear_in_shock_size(tables):
    """+50% must be an EXACT multiple of the +$25k/day row's delta at
    this workbook's base charter rate, and -$25k/day must be the EXACT
    negation of +$25k/day -- the charter cash flow is linear (no
    convexity, single factor, no cross term)."""
    params = model.operating_default_params()
    df = risk.run_stress_tests(D, tables, params, basis="legacy")
    up = df[df["scenario"] == "Charter +$25k/day"].iloc[0]
    down = df[df["scenario"] == "Charter -$25k/day"].iloc[0]
    pct = df[df["scenario"] == "Charter +50%"].iloc[0]

    assert down["pnl_12cargo"] == pytest.approx(-up["pnl_12cargo"], rel=1e-9)
    assert down["pnl_m1_spread"] == pytest.approx(-up["pnl_m1_spread"], rel=1e-9)

    ch_row = model.snap(tables.charter, D)
    base_charter = params.charter_override if params.charter_override is not None else float(ch_row["rate174"])
    ratio = (base_charter * 0.5) / 25_000.0
    assert pct["pnl_12cargo"] == pytest.approx(up["pnl_12cargo"] * ratio, rel=1e-9)
    assert pct["pnl_m1_spread"] == pytest.approx(up["pnl_m1_spread"] * ratio, rel=1e-9)


@pytest.mark.parametrize("params_factory", [model.Params, model.operating_default_params], ids=["legacy", "operating"])
def test_charter_stress_rows_cost_increase_sign_matches_both_bases(tables, params_factory):
    """A charter rate INCREASE must move pnl_m1_spread in the SAME
    direction on both bases (the physical ledger's total_days equals the
    legacy day-count formula by construction -- cashflows.
    physical_cargo_quantities()'s own docstring), and -$25k/day must be
    the exact opposite sign of +$25k/day on both."""
    params = params_factory()
    legacy_df = risk.run_stress_tests(D, tables, params, basis="legacy")
    phys_df = risk.run_stress_tests(D, tables, params, basis="physical",
                                     first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT)
    for df in (legacy_df, phys_df):
        up = df[df["scenario"] == "Charter +$25k/day"].iloc[0]
        down = df[df["scenario"] == "Charter -$25k/day"].iloc[0]
        assert np.sign(up["pnl_m1_spread"]) == -np.sign(down["pnl_m1_spread"])
        assert up["pnl_m1_spread"] != 0.0


# ===========================================================================
# (b) Charter overlay (E.1(b))
# ===========================================================================


def test_charter_weekly_vol_matches_manual_log_return_std():
    dates = pd.bdate_range("2020-01-01", periods=20, freq="7D")
    rng = np.random.default_rng(99)
    prices = 50_000.0 * np.exp(np.cumsum(rng.normal(0, 0.15, size=20)))
    df = pd.DataFrame({"date": dates, "rate174": prices})
    expected = np.std(np.diff(np.log(prices)), ddof=1)
    assert risk.charter_weekly_vol(df) == pytest.approx(expected, rel=1e-12)


def test_charter_daily_vol_scales_by_sqrt5():
    dates = pd.bdate_range("2020-01-01", periods=20, freq="7D")
    rng = np.random.default_rng(99)
    prices = 50_000.0 * np.exp(np.cumsum(rng.normal(0, 0.15, size=20)))
    df = pd.DataFrame({"date": dates, "rate174": prices})
    weekly = risk.charter_weekly_vol(df)
    daily = risk.charter_daily_vol(df)
    assert risk.CHARTER_OVERLAY_BUSINESS_DAYS_PER_WEEK == pytest.approx(5.0)
    assert daily == pytest.approx(weekly / np.sqrt(5.0), rel=1e-12)


def test_charter_weekly_vol_rejects_too_short_series():
    df = pd.DataFrame({"date": pd.bdate_range("2020-01-01", periods=2), "rate174": [50_000.0, 51_000.0]})
    with pytest.raises(ValueError, match="too short"):
        risk.charter_weekly_vol(df)


def test_charter_overlay_calibration_matches_component_functions_and_real_workbook(tables):
    calib = risk.charter_overlay_calibration(tables)
    assert calib["n_obs"] == len(tables.charter) == 459
    assert calib["weekly_vol"] == pytest.approx(risk.charter_weekly_vol(tables.charter))
    assert calib["daily_vol"] == pytest.approx(risk.charter_daily_vol(tables.charter))
    assert calib["business_days_per_week"] == risk.CHARTER_OVERLAY_BUSINESS_DAYS_PER_WEEK
    assert calib["seed"] == risk.CHARTER_OVERLAY_SEED
    # Sanity bounds on the real series (not a tautology -- catches a
    # units/scaling regression, e.g. an accidental extra x100 or /100).
    assert 0.05 < calib["weekly_vol"] < 1.0
    assert 0.02 < calib["daily_vol"] < 0.5


def test_charter_overlay_pnl_matches_manual_rng_draw():
    n, qty, base, vol, seed = 10, -25.0, 90_000.0, 0.05, 12345
    result = risk.charter_overlay_pnl(n, qty, base, vol, seed=seed)
    rng = np.random.default_rng(seed)
    z = rng.normal(0.0, 1.0, size=n)
    expected = qty * (base * vol * z)
    np.testing.assert_allclose(result, expected)


def test_charter_overlay_pnl_is_deterministic_for_same_seed():
    a = risk.charter_overlay_pnl(50, -10.0, 80_000.0, 0.1, seed=7)
    b = risk.charter_overlay_pnl(50, -10.0, 80_000.0, 0.1, seed=7)
    np.testing.assert_array_equal(a, b)


def test_charter_overlay_pnl_differs_for_different_seeds():
    a = risk.charter_overlay_pnl(50, -10.0, 80_000.0, 0.1, seed=7)
    b = risk.charter_overlay_pnl(50, -10.0, 80_000.0, 0.1, seed=8)
    assert not np.array_equal(a, b)


def test_charter_overlay_pnl_zero_quantity_is_zero_pnl():
    result = risk.charter_overlay_pnl(20, 0.0, 90_000.0, 0.1, seed=1)
    np.testing.assert_array_equal(result, np.zeros(20))


@pytest.mark.parametrize("basin", ["Europe", "Asia"])
def test_charter_quantity_for_single_matches_analytic_delta(tables, basin):
    """analytic_deltas()'s own "Charter +$10k/day" row is an independent,
    already-tested source of truth for the single-cargo charter
    quantity (it is literally quantity_on(CHARTER) * 10_000 -- see
    risk.analytic_deltas()'s source)."""
    params = model.operating_default_params()
    qty = risk._charter_quantity_for_portfolio(pd.Timestamp(D), tables, params, "legacy", "single", 0, basin)
    deltas = {dd.name: dd for dd in risk.analytic_deltas(D, tables, params, 0)}
    charter_delta = deltas["Charter +$10k/day"]
    expected = (charter_delta.eu_cargo_delta if basin == "Europe" else charter_delta.asia_cargo_delta) / 10_000.0
    assert qty == pytest.approx(expected, rel=1e-9)


def test_charter_quantity_for_hedged_equals_single(tables):
    """Section 7's mechanical hedge has no charter leg (the 'Freight FFA'
    row is required=False, 'no liquid contract')."""
    params = model.operating_default_params()
    single = risk._charter_quantity_for_portfolio(pd.Timestamp(D), tables, params, "legacy", "single", 0, "Europe")
    hedged = risk._charter_quantity_for_portfolio(pd.Timestamp(D), tables, params, "legacy", "hedged", 0, "Europe")
    assert hedged == pytest.approx(single)


def test_charter_quantity_for_spread_is_asia_minus_europe(tables):
    params = model.operating_default_params()
    eu = risk._charter_quantity_for_portfolio(pd.Timestamp(D), tables, params, "legacy", "single", 0, "Europe")
    asia = risk._charter_quantity_for_portfolio(pd.Timestamp(D), tables, params, "legacy", "single", 0, "Asia")
    spread = risk._charter_quantity_for_portfolio(pd.Timestamp(D), tables, params, "legacy", "spread", 0, "Europe")
    assert spread == pytest.approx(asia - eu)


def test_charter_quantity_for_12cargo_sums_verdict_selected_months(tables):
    params = model.operating_default_params()
    base = model.strip(D, tables, params)
    expected = 0.0
    for i in range(12):
        eu, asia = cashflows.legacy_cargo_cashflows(D, tables, params, i)
        exp = asia if base.iloc[i]["verdict"] == "Asia" else eu
        expected += exp.quantity_on((RiskFactor.CHARTER,))
    qty = risk._charter_quantity_for_portfolio(pd.Timestamp(D), tables, params, "legacy", "12cargo", 0, "Europe")
    assert qty == pytest.approx(expected)


def test_charter_quantity_physical_spread_and_programme(tables):
    params = model.operating_default_params()
    state = decision.FirstCargoState.FULLY_PRE_LIFT
    eu = cashflows.physical_cargo_cashflows(D, tables, params, 0, "Europe", state)
    asia = cashflows.physical_cargo_cashflows(D, tables, params, 0, "Asia", state)
    spread_qty = risk._charter_quantity_for_portfolio(
        pd.Timestamp(D), tables, params, "physical", "spread", 0, "Europe", state)
    assert spread_qty == pytest.approx(asia.quantity_on((RiskFactor.CHARTER,)) - eu.quantity_on((RiskFactor.CHARTER,)))

    plan = risk.build_committed_programme(D, tables, params, 0, state)
    leg_exposures = risk._programme_leg_exposures(D, tables, params, plan, state)
    expected_prog = sum(exp.quantity_on((RiskFactor.CHARTER,)) for _, exp in leg_exposures)
    prog_qty = risk._charter_quantity_for_portfolio(
        pd.Timestamp(D), tables, params, "physical", "programme", 0, "Europe", state)
    assert prog_qty == pytest.approx(expected_prog)


def test_charter_quantity_rejects_unknown_portfolio_or_basis(tables):
    params = model.operating_default_params()
    with pytest.raises(ValueError):
        risk._charter_quantity_for_portfolio(pd.Timestamp(D), tables, params, "legacy", "programme", 0, "Europe")
    with pytest.raises(ValueError):
        risk._charter_quantity_for_portfolio(pd.Timestamp(D), tables, params, "physical", "12cargo", 0, "Europe")
    with pytest.raises(ValueError):
        risk._charter_quantity_for_portfolio(pd.Timestamp(D), tables, params, "not_a_basis", "single", 0, "Europe")


def test_apply_charter_overlay_preserves_metadata_changes_pnl(tables):
    params = model.operating_default_params()
    scen = risk.build_scenarios(tables, D, lookback=250, method="naive")
    r = risk.historical_var(D, tables, params, portfolio="single", basin="Europe", month_index=0, scen=scen)
    r2 = risk.apply_charter_overlay(r, D, tables, params, portfolio="single", month_index=0, basin="Europe")

    assert r2.n == r.n
    assert r2.portfolio == r.portfolio
    assert r2.basis == r.basis
    assert r2.scen is r.scen
    assert not np.array_equal(r2.pnl, r.pnl), "overlay must actually perturb the P&L vector"
    assert r2.sd > 0


def test_apply_charter_overlay_deterministic_across_calls(tables):
    params = model.operating_default_params()
    scen = risk.build_scenarios(tables, D, lookback=250, method="naive")
    r = risk.historical_var(D, tables, params, portfolio="single", basin="Europe", month_index=0, scen=scen)
    r_a = risk.apply_charter_overlay(r, D, tables, params, portfolio="single", month_index=0, basin="Europe")
    r_b = risk.apply_charter_overlay(r, D, tables, params, portfolio="single", month_index=0, basin="Europe")
    np.testing.assert_array_equal(r_a.pnl, r_b.pnl)
    assert r_a.var95 == r_b.var95


def test_apply_charter_overlay_on_zero_gas_scenario_still_produces_nonzero_pnl(tables):
    """Demonstrates the overlay's own two headline properties concretely:
    ADDITIVE (stacks onto a P&L that is otherwise exactly zero) and
    INDEPENDENT (produces dispersion having nothing to do with the gas
    complex, which is unshocked here)."""
    params = model.operating_default_params()
    n = 200
    zero_scen = _zero_scenario(n)
    r = risk.historical_var(D, tables, params, portfolio="single", basin="Europe", month_index=0, scen=zero_scen)
    assert np.allclose(r.pnl, 0.0, atol=0.01)
    r2 = risk.apply_charter_overlay(r, D, tables, params, portfolio="single", month_index=0, basin="Europe")
    assert not np.allclose(r2.pnl, 0.0, atol=0.01)
    assert r2.sd > 0


def test_apply_charter_overlay_works_for_every_legacy_portfolio(tables):
    params = model.operating_default_params()
    scen = risk.build_scenarios(tables, D, lookback=100, method="naive")
    for portfolio, basin in [("single", "Europe"), ("single", "Asia"), ("hedged", "Europe"),
                              ("12cargo", "Europe"), ("spread", "Europe")]:
        r = risk.historical_var(D, tables, params, portfolio=portfolio, basin=basin, month_index=0, scen=scen)
        r2 = risk.apply_charter_overlay(r, D, tables, params, portfolio=portfolio, month_index=0, basin=basin)
        assert np.isfinite(r2.pnl).all(), portfolio


def test_apply_charter_overlay_works_for_every_physical_portfolio(tables):
    params = model.operating_default_params()
    scen = risk.build_scenarios(tables, D, lookback=100, method="naive")
    state = decision.FirstCargoState.FULLY_PRE_LIFT
    for portfolio in ("single", "spread", "programme"):
        r = risk.historical_var_physical(D, tables, params, portfolio=portfolio, basin="Europe", month_index=0,
                                          scen=scen, first_cargo_state=state)
        r2 = risk.apply_charter_overlay(r, D, tables, params, portfolio=portfolio, month_index=0, basin="Europe",
                                         first_cargo_state=state)
        assert np.isfinite(r2.pnl).all(), portfolio


# ===========================================================================
# (c) Gate-4 fixture freeze (E.1(c))
# ===========================================================================

# Independent re-statement of tests/test_model.py's own frozen GATE
# 4a/4b spec dicts (that file is frozen and must not be edited -- this
# duplication is intentional: an independent pytest proof, not a
# replacement).
_GATE4_SPEC_7 = {
    "unhedged": dict(var95=-3_054_920, var99=-4_951_928, sd=2_098_992),
    "hedged": dict(var95=-12_437, var99=-35_599, sd=13_681),
}
_GATE4_SPEC_8 = {
    "12cargo": dict(var95=-26_093_953, var99=-47_706_721, sd=18_447_468),
    "spread": dict(var95=-1_351_610, var99=-2_256_879, sd=996_909),
}


def _pct_diff(a, b):
    return abs(a - b) / abs(b)


def test_gate4_legacy_var_fixtures_unchanged_by_increment_e(tables):
    """Independent pytest re-derivation of tests/test_model.py's own
    GATE 4a/4b assertions: historical_var() on the UNMODIFIED real
    workbook, default Params(), 500 naive scenarios, must still produce
    the exact same numbers after everything E.1-E.3 added -- the new
    charter stress rows (a different function), apply_charter_overlay()
    (never called unless a caller opts in), the VLSFO/EUA ScenarioSet
    fields (None on the real workbook, so _scenario_factor_array() takes
    its exact pre-increment-E branch), and cashflows.py's `eua_live`
    (default False everywhere in this call graph, and not even a
    parameter legacy_cargo_quantities() has). 1% tolerance matches
    test_model.py's own pct_diff() convention exactly."""
    params_default = model.Params()
    scen = risk.build_scenarios(tables, D, lookback=500, method="naive")

    r_un = risk.historical_var(D, tables, params_default, portfolio="single", month_index=0, basin="Europe", scen=scen)
    r_hd = risk.historical_var(D, tables, params_default, portfolio="hedged", month_index=0, basin="Europe", scen=scen)
    for label, r in (("unhedged", r_un), ("hedged", r_hd)):
        spec = _GATE4_SPEC_7[label]
        assert _pct_diff(r.var95, spec["var95"]) <= 0.01, label
        assert _pct_diff(r.var99, spec["var99"]) <= 0.01, label
        assert _pct_diff(r.sd, spec["sd"]) <= 0.01, label

    r_12 = risk.historical_var(D, tables, params_default, portfolio="12cargo", scen=scen)
    r_spread = risk.historical_var(D, tables, params_default, portfolio="spread", month_index=0, scen=scen)
    for label, r in (("12cargo", r_12), ("spread", r_spread)):
        spec = _GATE4_SPEC_8[label]
        assert _pct_diff(r.var95, spec["var95"]) <= 0.01, label
        assert _pct_diff(r.var99, spec["var99"]) <= 0.01, label
        assert _pct_diff(r.sd, spec["sd"]) <= 0.01, label


def test_scenario_set_has_no_charter_return_fields(tables):
    """Plan sect 6.E.1's explicit revised-decision constraint:
    'ScenarioSet must NOT grow charter return columns'. VLSFO/EUA (E.2)
    legitimately add fields; charter must not, on either basis, ever."""
    scen = risk.build_scenarios(tables, D, lookback=250, method="naive")
    field_names = {f.name for f in dataclasses.fields(scen)}
    assert not any("charter" in name.lower() for name in field_names), field_names
    assert {"vlsfo_ret", "eua_ret"}.issubset(field_names)


def test_zero_shock_still_holds_with_synthetic_vlsfo_eua_attached(tables):
    """Extra fixture-freeze belt-and-braces: a synthetic VLSFO/EUA table
    attached to `tables` must not perturb a zero-return LEGACY VaR call
    one bit -- the zero scenario here carries no vlsfo_ret/eua_ret, so
    _scenario_factor_array() takes its None branch regardless of what
    `tables` itself carries."""
    params_default = model.Params()
    synthetic_tables = _with_synthetic_vlsfo_eua(tables)
    r = risk.historical_var(D, synthetic_tables, params_default, portfolio="12cargo", scen=_zero_scenario())
    assert abs(r.pnl[0]) < 1.0


# ===========================================================================
# (d) VLSFO/EUA loaders (E.2, data.py)
# ===========================================================================


def _write_synthetic_date_price_workbook(sheet_name: str, rows: list) -> io.BytesIO:
    """A minimal in-memory .xlsx matching load_vlsfo()/load_eua()'s
    assumed layout (row1=index numbers, row2=headers, row3=blank,
    row4+=date,price) -- no real VLSFO/EUA sheet exists to test against
    (plan sect 5), so this stands in for one, built fresh in memory per
    the task's own instruction to test the live path with a synthetic
    in-memory table."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append([1, 2])
    ws.append(["Date", "Price"])
    ws.append([])
    for dt, price in rows:
        ws.append([dt, price])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_load_vlsfo_eua_absent_sheet_raises_and_load_all_tolerates(tables, workbook_path):
    """The bare loaders raise naturally (no try/except inside them, per
    the load_volatilities() pattern -- tolerance of absence lives at
    load_all()'s call site); confirmed against the REAL workbook, which
    has no VLSFO/EUA sheet (plan sect 5)."""
    with pytest.raises(ValueError):
        data.load_vlsfo(workbook_path)
    with pytest.raises(ValueError):
        data.load_eua(workbook_path)
    assert tables.vlsfo is None
    assert tables.eua is None


def test_load_vlsfo_parses_a_synthetic_in_memory_workbook():
    rows = [(pd.Timestamp("2024-01-01") + pd.Timedelta(days=7 * i), 500.0 + i) for i in range(10)]
    buf = _write_synthetic_date_price_workbook("VLSFO", rows)
    out = data.load_vlsfo(buf)
    assert list(out.columns) == ["date", "price"]
    assert len(out) == 10
    assert out["date"].is_monotonic_increasing
    assert out["price"].iloc[0] == pytest.approx(500.0)
    assert out["price"].iloc[-1] == pytest.approx(509.0)


def test_load_eua_parses_a_synthetic_in_memory_workbook():
    rows = [(pd.Timestamp("2024-01-01") + pd.Timedelta(days=7 * i), 70.0 + 0.1 * i) for i in range(10)]
    buf = _write_synthetic_date_price_workbook("EUA", rows)
    out = data.load_eua(buf)
    assert len(out) == 10
    assert out["price"].iloc[0] == pytest.approx(70.0)
    assert out["price"].iloc[-1] == pytest.approx(70.9)


def test_load_vlsfo_drops_duplicate_dates_keeping_last():
    rows = [(pd.Timestamp("2024-01-01"), 400.0), (pd.Timestamp("2024-01-01"), 555.0),
            (pd.Timestamp("2024-01-08"), 410.0)]
    buf = _write_synthetic_date_price_workbook("VLSFO", rows)
    out = data.load_vlsfo(buf)
    assert len(out) == 2
    assert out.loc[out["date"] == pd.Timestamp("2024-01-01"), "price"].iloc[0] == pytest.approx(555.0)


def test_curve_tables_vlsfo_eua_default_none_and_constructor_accepts_them():
    """'CurveTables gains optional fields (default None -- verify no
    constructor call breaks)' -- both halves of that instruction,
    directly: the bare (no vlsfo/eua kwargs) construction still works,
    AND supplying them explicitly still works."""
    ct = data.CurveTables(hh=pd.DataFrame(), ttf=pd.DataFrame(), jkm=pd.DataFrame(),
                           fx=pd.DataFrame(), charter=pd.DataFrame())
    assert ct.vlsfo is None
    assert ct.eua is None

    synthetic = _synthetic_price_series(1, start="2020-01-01", end="2020-02-01")
    ct2 = data.CurveTables(hh=pd.DataFrame(), ttf=pd.DataFrame(), jkm=pd.DataFrame(),
                            fx=pd.DataFrame(), charter=pd.DataFrame(), vlsfo=synthetic, eua=synthetic)
    assert ct2.vlsfo is synthetic
    assert ct2.eua is synthetic


# ===========================================================================
# (e) VLSFO/EUA scenario wiring (E.2, risk.py)
# ===========================================================================


def test_scenario_factor_array_none_returns_flat_base_price():
    arr = risk._scenario_factor_array(5, None, 530.0)
    np.testing.assert_array_equal(arr, np.full(5, 530.0))


def test_scenario_factor_array_applies_log_return_exponential():
    ret = np.array([0.0, 0.1, -0.1])
    arr = risk._scenario_factor_array(3, ret, 100.0)
    expected = 100.0 * np.exp(ret)
    np.testing.assert_allclose(arr, expected)


def test_snap_series_onto_dates_none_table_returns_none():
    assert risk._snap_series_onto_dates(None, "price", [pd.Timestamp("2020-01-01")]) is None


def test_snap_series_onto_dates_returns_none_when_series_starts_after_window():
    table = pd.DataFrame({"date": pd.bdate_range("2025-01-01", periods=5), "price": [1.0] * 5})
    target_dates = list(pd.bdate_range("2020-01-01", periods=5))
    assert risk._snap_series_onto_dates(table, "price", target_dates) is None


def test_snap_series_onto_dates_forward_fills_last_known_value():
    table = pd.DataFrame({"date": [pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-10")],
                           "price": [100.0, 200.0]})
    target_dates = [pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-05"), pd.Timestamp("2020-01-10"),
                     pd.Timestamp("2020-01-15")]
    out = risk._snap_series_onto_dates(table, "price", target_dates)
    np.testing.assert_array_equal(out, [100.0, 100.0, 200.0, 200.0])


def test_build_scenarios_vlsfo_eua_ret_none_on_real_workbook(tables):
    scen = risk.build_scenarios(tables, D, lookback=250, method="naive")
    assert scen.vlsfo_ret is None
    assert scen.eua_ret is None


def test_build_scenarios_populates_vlsfo_ret_when_present_independent_of_eua(tables):
    synthetic_tables = _with_synthetic_vlsfo(tables)
    scen = risk.build_scenarios(synthetic_tables, D, lookback=250, method="naive")
    assert scen.vlsfo_ret is not None
    assert scen.vlsfo_ret.shape == (250,)
    assert np.isfinite(scen.vlsfo_ret).all()
    assert scen.eua_ret is None


def test_build_scenarios_vlsfo_ret_matches_manual_snap_and_log_return(tables):
    synthetic_tables = _with_synthetic_vlsfo(tables, seed=3, base=480.0)
    lookback = 50
    scen = risk.build_scenarios(synthetic_tables, D, lookback=lookback, method="naive")

    inter = risk._intersection_dates(synthetic_tables)
    end_idx = max(i for i, dt in enumerate(inter) if dt <= pd.Timestamp(D))
    scen_dates = inter[end_idx - lookback:end_idx + 1]
    assert scen_dates[1:] == scen.dates, "reconstructed window must match what build_scenarios() actually used"

    manual_px = risk._snap_series_onto_dates(synthetic_tables.vlsfo, "price", scen_dates)
    manual_ret = np.diff(np.log(manual_px))
    np.testing.assert_allclose(scen.vlsfo_ret, manual_ret)


def test_build_scenarios_vlsfo_presence_does_not_alter_gas_complex_returns(tables):
    """'every existing path is unchanged' (plan sect 6.E.2), proven
    directly: attaching a synthetic VLSFO table must not shrink or shift
    the shared HH/TTF/JKM/FX scenario window one bit."""
    plain_scen = risk.build_scenarios(tables, D, lookback=250, method="naive")
    synthetic_tables = _with_synthetic_vlsfo(tables)
    synth_scen = risk.build_scenarios(synthetic_tables, D, lookback=250, method="naive")
    assert synth_scen.dates == plain_scen.dates
    np.testing.assert_array_equal(synth_scen.hh_ret, plain_scen.hh_ret)
    np.testing.assert_array_equal(synth_scen.ttf_ret, plain_scen.ttf_ret)
    np.testing.assert_array_equal(synth_scen.jkm_ret, plain_scen.jkm_ret)
    np.testing.assert_array_equal(synth_scen.fx_ret, plain_scen.fx_ret)


def test_vlsfo_live_legacy_zero_shock_still_holds(tables):
    """Legacy zero-shock identity vs model.strip() must survive VLSFO
    going live -- safe by construction (VLSFO's cash flow was never
    folded, unlike EUA's), proven with a real non-trivial synthetic
    VLSFO series attached and a zero-return vlsfo_ret."""
    params_default = model.Params()
    synthetic_tables = _with_synthetic_vlsfo(tables)
    zero_scen = _zero_scenario(vlsfo_ret=np.zeros(1))
    r = risk.historical_var(D, synthetic_tables, params_default, portfolio="12cargo", scen=zero_scen)
    assert abs(r.pnl[0]) < 1.0


def test_vlsfo_live_legacy_nonzero_shock_matches_quantity_times_price_shock(tables):
    params = model.operating_default_params()
    synthetic_tables = _with_synthetic_vlsfo(tables)
    shock = 0.05
    scen = _zero_scenario(vlsfo_ret=np.array([shock]))
    r = risk.historical_var(D, synthetic_tables, params, portfolio="single", basin="Europe", month_index=0, scen=scen)

    eu, _ = cashflows.legacy_cargo_cashflows(D, synthetic_tables, params, 0)
    base_vlsfo = params.vlsfo_price
    price_shock = base_vlsfo * np.exp(shock) - base_vlsfo
    expected_pnl = eu.quantity_on((RiskFactor.VLSFO,)) * price_shock
    assert r.pnl[0] == pytest.approx(expected_pnl, rel=1e-9, abs=1e-3)


def test_vlsfo_live_physical_zero_shock_still_holds_every_portfolio(tables):
    params = model.operating_default_params()
    synthetic_tables = _with_synthetic_vlsfo(tables)
    zero_scen = _zero_scenario(vlsfo_ret=np.zeros(1))
    for portfolio in ("single", "spread", "programme"):
        r = risk.historical_var_physical(D, synthetic_tables, params, portfolio=portfolio, month_index=0,
                                          basin="Europe", scen=zero_scen,
                                          first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT)
        assert abs(r.pnl[0]) <= 0.01, portfolio


# ===========================================================================
# (f) EUA re-split (E.2, cashflows.py `eua_live`)
# ===========================================================================


def test_legacy_cargo_quantities_has_no_eua_live_parameter():
    """Hard architectural constraint (not an oversight): the legacy basis
    has no eua_live path and never will -- model.strip() is frozen/
    read-only and can never learn about a live EUA table (see
    cashflows.physical_cargo_quantities()'s 'EUA factor' docstring
    paragraph). A stray eua_live kwarg here must fail loud (TypeError),
    not be silently swallowed or ignored."""
    with pytest.raises(TypeError):
        cashflows.legacy_cargo_quantities(model.Params(), 2026, eua_live=True)


def test_eua_folded_form_is_default_and_unaffected_by_table_presence(tables):
    """eua_live defaults False everywhere -- physical_cargo_cashflows()'s
    output must be numerically UNCHANGED whether or not `tables` happens
    to carry an eua table, as long as the caller never passes
    eua_live=True."""
    params = model.operating_default_params()
    synthetic_tables = _with_synthetic_eua(tables)
    plain = cashflows.physical_cargo_cashflows(D, tables, params, 0, "Europe")
    present_not_live = cashflows.physical_cargo_cashflows(D, synthetic_tables, params, 0, "Europe")
    assert plain.value(plain.base_prices) == pytest.approx(
        present_not_live.value(present_not_live.base_prices), rel=1e-12)
    assert RiskFactor.EUA not in present_not_live.base_prices


def test_eua_live_requires_eua_table(tables):
    """Fail loud rather than silently stay on the folded form when a
    caller explicitly asks for the live path but no data backs it."""
    params = model.operating_default_params()
    with pytest.raises(ValueError, match="eua_live"):
        cashflows.physical_cargo_cashflows(D, tables, params, 0, "Europe", eua_live=True)


def test_eua_live_resplits_ets_into_bilinear_eua_fx(tables):
    params = model.operating_default_params()
    synthetic_tables = _with_synthetic_eua(tables)
    folded = cashflows.physical_cargo_cashflows(D, synthetic_tables, params, 0, "Europe")
    live = cashflows.physical_cargo_cashflows(D, synthetic_tables, params, 0, "Europe", eua_live=True)

    ets_qty_folded = folded.quantity_on((RiskFactor.FX,))
    assert ets_qty_folded != 0.0
    assert live.quantity_on((RiskFactor.FX,)) == 0.0, "the folded FX-linear ETS leg must be gone once live"
    assert RiskFactor.EUA in live.base_prices

    ets_qty_live = live.quantity_on((RiskFactor.EUA, RiskFactor.FX))
    assert ets_qty_live == pytest.approx(ets_qty_folded / params.eua_price, rel=1e-9), \
        "re-split quantity must be the folded quantity with eua_price divided back out"


def test_physical_quantity_cache_distinguishes_eua_live(tables):
    """Regression guard for the cache-key fix: physical_cargo_quantities()
    must NOT serve a folded-mode cached result to a live-mode call (or
    vice versa) for the same (params, route, year, first_cargo_state)."""
    params = model.operating_default_params()
    folded = cashflows.CargoExposure(
        route="Europe", month_index=0,
        cash_flows=cashflows.physical_cargo_quantities(params, "Europe", 2026, eua_live=False))
    live = cashflows.CargoExposure(
        route="Europe", month_index=0,
        cash_flows=cashflows.physical_cargo_quantities(params, "Europe", 2026, eua_live=True))
    assert folded.quantity_on((RiskFactor.FX,)) != 0.0
    assert live.quantity_on((RiskFactor.FX,)) == 0.0
    assert live.quantity_on((RiskFactor.EUA, RiskFactor.FX)) != 0.0
    assert folded.quantity_on((RiskFactor.EUA, RiskFactor.FX)) == 0.0


def test_eua_live_zero_shock_self_consistent_on_physical_basis_every_portfolio(tables):
    """THE critical self-consistency test: historical_var_physical()
    derives eua_live ONCE and applies it identically to the base value
    AND the scenario repricer, so a zero-return EUA scenario must still
    reprice to ~$0 against this function's OWN (live-mode) base -- even
    though that base no longer equals the folded-mode base (a different,
    but self-consistent, number -- see cashflows.
    physical_cargo_quantities()'s 'EUA factor' docstring paragraph)."""
    params = model.operating_default_params()
    synthetic_tables = _with_synthetic_eua(tables)
    zero_scen = _zero_scenario(eua_ret=np.zeros(1))
    for portfolio in ("single", "spread", "programme"):
        r = risk.historical_var_physical(D, synthetic_tables, params, portfolio=portfolio, month_index=0,
                                          basin="Europe", scen=zero_scen,
                                          first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT)
        assert abs(r.pnl[0]) <= 0.01, portfolio


def test_eua_live_nonzero_shock_matches_quantity_times_price_shock(tables):
    params = model.operating_default_params()
    synthetic_tables = _with_synthetic_eua(tables)
    shock = 0.2
    scen = _zero_scenario(eua_ret=np.array([shock]))
    state = decision.FirstCargoState.FULLY_PRE_LIFT
    r = risk.historical_var_physical(D, synthetic_tables, params, portfolio="single", basin="Europe",
                                      month_index=0, scen=scen, first_cargo_state=state)

    base_eua = float(model.snap(synthetic_tables.eua, D)["price"])
    live_eu = cashflows.physical_cargo_cashflows(D, synthetic_tables, params, 0, "Europe", state, eua_live=True)
    eua_fx_qty = live_eu.quantity_on((RiskFactor.EUA, RiskFactor.FX))
    base_fx = live_eu.base_prices[RiskFactor.FX]  # FX is unshocked in this scenario (fx_ret=0)
    shocked_eua = base_eua * np.exp(shock)
    expected_delta = eua_fx_qty * base_fx * (shocked_eua - base_eua)
    assert r.pnl[0] == pytest.approx(expected_delta, rel=1e-9, abs=1e-3)


def test_eua_live_never_affects_legacy_basis(tables):
    """Legacy basis must be COMPLETELY unaffected by tables.eua's mere
    presence -- historical_var() has no eua_live concept anywhere in its
    call graph. Proven by running the IDENTICAL legacy call against
    `tables` and a synthetic-eua variant and requiring byte-identical
    output, even though the synthetic variant's ScenarioSet DOES carry a
    populated eua_ret (build_scenarios() itself is basis-agnostic; it is
    the LEGACY REPRICER that never reads eua_ret at all)."""
    params_default = model.Params()
    synthetic_tables = _with_synthetic_eua(tables)
    scen_a = risk.build_scenarios(tables, D, lookback=100, method="naive")
    scen_b = risk.build_scenarios(synthetic_tables, D, lookback=100, method="naive")
    assert scen_b.eua_ret is not None
    r_a = risk.historical_var(D, tables, params_default, portfolio="12cargo", scen=scen_a)
    r_b = risk.historical_var(D, synthetic_tables, params_default, portfolio="12cargo", scen=scen_b)
    np.testing.assert_array_equal(r_a.pnl, r_b.pnl)


# ===========================================================================
# (g) Factor-coverage line (E.3)
# ===========================================================================


def test_factor_coverage_line_rejects_unknown_basis(tables):
    with pytest.raises(ValueError, match="basis"):
        risk.factor_coverage_line(tables, basis="not_a_basis")


@pytest.mark.parametrize("basis", ["legacy", "physical"])
def test_factor_coverage_line_names_all_four_stochastic_factors(tables, basis):
    line = risk.factor_coverage_line(tables, basis=basis)
    assert all(name in line for name in ("HH", "TTF", "JKM", "FX"))
    assert "stochastic" in line


def test_factor_coverage_line_charter_reflects_overlay_toggle(tables):
    off = risk.factor_coverage_line(tables, basis="legacy", charter_overlay_on=False)
    on = risk.factor_coverage_line(tables, basis="legacy", charter_overlay_on=True)
    assert "overlay OFF" in off
    assert "model overlay (ON" in on
    assert off != on


def test_factor_coverage_line_vlsfo_reflects_workbook_presence(tables):
    absent = risk.factor_coverage_line(tables, basis="physical")
    assert "VLSFO -- deterministic (no history" in absent
    present = risk.factor_coverage_line(_with_synthetic_vlsfo(tables), basis="physical")
    assert "VLSFO -- stochastic (history in workbook)" in present


def test_factor_coverage_line_eua_is_basis_conditional(tables):
    """The one nuance this function exists to get right: EUA reads
    stochastic on the physical basis once a sheet exists, but stays
    unconditionally deterministic on the legacy basis regardless."""
    synthetic_tables = _with_synthetic_eua(tables)
    physical_line = risk.factor_coverage_line(synthetic_tables, basis="physical")
    legacy_line = risk.factor_coverage_line(synthetic_tables, basis="legacy")
    assert "EUA -- stochastic (history in workbook)" in physical_line
    assert "EUA -- deterministic" in legacy_line
    assert "frozen to model.strip()" in legacy_line
    assert "frozen to model.strip()" not in physical_line


def test_factor_coverage_line_excludes_locational_basis_always(tables):
    for basis in ("legacy", "physical"):
        line = risk.factor_coverage_line(tables, basis=basis)
        assert "locational basis -- EXCLUDED" in line
        assert "no proxy" in line
        assert "FuelEU" in line
