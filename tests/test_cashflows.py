"""Tests for cashflows.py (R6.1, increment A of
docs/R6_RISK_REBUILD_PLAN.md sect 6.A).

Three groups:
  (a) Pure unit tests of CashFlow/CargoExposure evaluation on synthetic
      quantities -- no workbook dependency, run in CI (cashflows.py only
      imports model.py, not data.py, so nothing here needs
      LNG_HISTORY_XLSX).
  (b) Workbook-gated pinning tests: legacy_cargo_cashflows()'s output,
      evaluated at its own base_prices, must equal model.strip()'s
      eu_cargo/asia_cargo for every one of the 12 load months, for both
      legacy (Params()) and operating-default parameter sets. This is
      the core proof of the increment: the decomposition reproduces
      strip()'s Step 6 arithmetic exactly.
  (c) Delta-derivation parity: the six analytic sensitivities, re-derived
      purely from CargoExposure.quantity_on(...), must match
      risk.analytic_deltas() -- proving the cash-flow layer's quantities
      are the same numbers risk.py's independently-written closed-form
      deltas encode.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pytest

from cashflows import CargoExposure, CashFlow, RiskFactor, legacy_cargo_cashflows
import data
import model
import risk

D = "2026-07-08"


@pytest.fixture(scope="module")
def tables():
    path = os.environ.get(data.ENV_VAR_NAME)
    if not path:
        local = Path(__file__).resolve().parents[1] / "LNG history.xlsx"
        path = str(local) if local.exists() else data.default_data_path()
    if not path:
        pytest.skip(f"set {data.ENV_VAR_NAME} to run workbook-backed tests")
    return data.load_all(str(path))


PARAMS_FACTORIES = [model.Params, model.operating_default_params]
PARAMS_IDS = ["legacy", "operating"]


# ===========================================================================
# (a) Pure unit tests -- synthetic quantities, no workbook
# ===========================================================================


def test_constant_only_cashflow_is_price_independent():
    """R6.1: a CashFlow with an empty factors tuple is a price-independent
    constant -- value() returns the quantity unchanged no matter what (or
    how little) the price map contains."""
    cf = CashFlow((), 0, -1234.5, "constant fee")
    exposure = CargoExposure("Test", 0, cash_flows=[cf])
    assert exposure.value({}) == pytest.approx(-1234.5)
    assert exposure.value({RiskFactor.TTF: 999.0}) == pytest.approx(-1234.5)


def test_single_factor_cashflow_is_linear():
    """R6.1: a single-factor CashFlow is linear: value = quantity * price[f]."""
    cf = CashFlow((RiskFactor.HH,), 0, -100.0, "HH term")
    exposure = CargoExposure("Test", 0, cash_flows=[cf])
    assert exposure.value({RiskFactor.HH: 3.5}) == pytest.approx(-350.0)


def test_bilinear_product_cashflow_multiplies_both_factors():
    """R6.1: a two-factor CashFlow is the bilinear product
    quantity * price[f1] * price[f2] -- the shape Europe's TTF x FX
    revenue term needs (plan sect 2)."""
    cf = CashFlow((RiskFactor.TTF, RiskFactor.FX), 0, 2.0, "TTF x FX")
    exposure = CargoExposure("Test", 0, cash_flows=[cf])
    assert exposure.value({RiskFactor.TTF: 10.0, RiskFactor.FX: 1.08}) == pytest.approx(21.6)


def test_value_sums_across_mixed_arity_cashflows():
    """R6.1: value() sums contributions across cash flows of different
    arity (constant + linear + bilinear) in one exposure."""
    exposure = CargoExposure("Test", 0, cash_flows=[
        CashFlow((), 0, 100.0, "constant"),
        CashFlow((RiskFactor.HH,), 0, -2.0, "HH"),
        CashFlow((RiskFactor.TTF, RiskFactor.FX), 0, 3.0, "TTF x FX"),
    ])
    prices = {RiskFactor.HH: 4.0, RiskFactor.TTF: 5.0, RiskFactor.FX: 2.0}
    expected = 100.0 + (-2.0 * 4.0) + (3.0 * 5.0 * 2.0)
    assert exposure.value(prices) == pytest.approx(expected)


def test_value_matrix_agrees_with_python_loop_reference():
    """R6.1: value_matrix's vectorised evaluation must agree with a plain
    Python loop calling value() once per scenario, on random quantities
    and price arrays (seed fixed for reproducibility)."""
    rng = np.random.default_rng(20260717)
    n = 200
    factor_sets = [
        (),
        (RiskFactor.HH,),
        (RiskFactor.TTF, RiskFactor.FX),
        (RiskFactor.CHARTER,),
        (RiskFactor.VLSFO,),
    ]
    cash_flows = [
        CashFlow(f, 0, float(rng.normal(0, 1000)), f"term{i}")
        for i, f in enumerate(factor_sets)
    ]
    exposure = CargoExposure("Test", 0, cash_flows=cash_flows)
    price_arrays = {
        RiskFactor.HH: rng.uniform(2, 5, n),
        RiskFactor.TTF: rng.uniform(20, 40, n),
        RiskFactor.FX: rng.uniform(0.9, 1.2, n),
        RiskFactor.CHARTER: rng.uniform(50_000, 150_000, n),
        RiskFactor.VLSFO: rng.uniform(400, 700, n),
    }

    matrix_result = exposure.value_matrix(price_arrays)
    assert matrix_result.shape == (n,)

    loop_result = np.array([
        exposure.value({f: price_arrays[f][i] for f in price_arrays})
        for i in range(n)
    ])
    np.testing.assert_allclose(matrix_result, loop_result, rtol=1e-12, atol=1e-9)


def test_value_raises_keyerror_on_missing_factor():
    """R6.1: value() fails loud on a factor missing from the price map --
    no silent default to base price or zero."""
    cf = CashFlow((RiskFactor.JKM,), 0, 10.0, "JKM term")
    exposure = CargoExposure("Test", 0, cash_flows=[cf])
    with pytest.raises(KeyError):
        exposure.value({})


def test_value_matrix_raises_keyerror_on_missing_factor():
    """R6.1: same fail-loud contract for the vectorised path."""
    cf = CashFlow((RiskFactor.JKM,), 0, 10.0, "JKM term")
    exposure = CargoExposure("Test", 0, cash_flows=[cf])
    with pytest.raises(KeyError):
        exposure.value_matrix({RiskFactor.HH: np.array([1.0, 2.0])})


def test_quantity_on_is_order_insensitive():
    """R6.1: quantity_on((TTF, FX)) and quantity_on((FX, TTF)) must
    address the same cash flow -- delta derivation depends on this."""
    cf = CashFlow((RiskFactor.TTF, RiskFactor.FX), 0, 7.5, "TTF x FX")
    exposure = CargoExposure("Test", 0, cash_flows=[cf])
    assert exposure.quantity_on((RiskFactor.TTF, RiskFactor.FX)) == pytest.approx(7.5)
    assert exposure.quantity_on((RiskFactor.FX, RiskFactor.TTF)) == pytest.approx(7.5)


def test_quantity_on_sums_all_matching_cashflows():
    """R6.1: quantity_on sums across every cash flow with exactly that
    factor tuple, not just the first match."""
    exposure = CargoExposure("Test", 0, cash_flows=[
        CashFlow((RiskFactor.HH,), 0, -100.0, "HH a"),
        CashFlow((RiskFactor.HH,), 0, -50.0, "HH b"),
        CashFlow((RiskFactor.JKM,), 0, 10.0, "JKM"),
    ])
    assert exposure.quantity_on((RiskFactor.HH,)) == pytest.approx(-150.0)
    assert exposure.quantity_on((RiskFactor.JKM,)) == pytest.approx(10.0)


def test_quantity_on_no_match_returns_zero():
    """R6.1: a factor tuple with no matching cash flow returns 0.0, not
    an error -- quantity_on is a query, not an assertion of presence."""
    exposure = CargoExposure("Test", 0, cash_flows=[CashFlow((RiskFactor.HH,), 0, -100.0, "HH")])
    assert exposure.quantity_on((RiskFactor.EUA,)) == 0.0


# ===========================================================================
# (b) Workbook-gated parity: the pinning tests (core of the increment)
# ===========================================================================


@pytest.mark.parametrize("params_factory", PARAMS_FACTORIES, ids=PARAMS_IDS)
def test_cargo_exposure_value_matches_strip_all_months(tables, params_factory):
    """R6.1: CargoExposure.value(base_prices) must equal model.strip()'s
    eu_cargo/asia_cargo for every one of the 12 load months, to
    abs <= 0.01, for both legacy and operating-default Params. This is
    the core pinning proof: legacy_cargo_cashflows() decomposes strip()'s
    Step 6 arithmetic exactly rather than approximately (plan targets
    ~1e-9; worst observed error is printed for the record)."""
    params = params_factory()
    strip_df = model.strip(D, tables, params)

    worst_eu = 0.0
    worst_asia = 0.0
    for month_index in range(12):
        eu, asia = legacy_cargo_cashflows(D, tables, params, month_index)
        assert eu.route == "Europe" and eu.month_index == month_index
        assert asia.route == "Asia" and asia.month_index == month_index

        row = strip_df.iloc[month_index]
        eu_value = eu.value(eu.base_prices)
        asia_value = asia.value(asia.base_prices)

        worst_eu = max(worst_eu, abs(eu_value - row["eu_cargo"]))
        worst_asia = max(worst_asia, abs(asia_value - row["asia_cargo"]))

        assert eu_value == pytest.approx(row["eu_cargo"], abs=0.01)
        assert asia_value == pytest.approx(row["asia_cargo"], abs=0.01)

    print(f"[{params_factory.__name__}] worst abs error: eu={worst_eu:.3e} asia={worst_asia:.3e}")


@pytest.mark.parametrize("params_factory", PARAMS_FACTORIES, ids=PARAMS_IDS)
def test_base_prices_cover_every_factor_the_cashflows_reference(tables, params_factory):
    """R6.1: base_prices must supply a price for every RiskFactor that
    exposure's own cash flows reference (else value(base_prices) itself
    would KeyError, which the parity test above implicitly already
    proves -- this test just names the expectation directly). EUA is
    intentionally absent from both: the legacy ETS term folds the EUA
    price into its FX-linear quantity today (see legacy_cargo_cashflows
    docstring), so no cash flow references RiskFactor.EUA yet."""
    params = params_factory()
    eu, asia = legacy_cargo_cashflows(D, tables, params, month_index=0)

    for exposure in (eu, asia):
        referenced = {f for cf in exposure.cash_flows for f in cf.factors}
        assert referenced.issubset(exposure.base_prices.keys())

    assert RiskFactor.EUA not in eu.base_prices
    assert RiskFactor.EUA not in asia.base_prices
    assert RiskFactor.FX not in asia.base_prices
    assert RiskFactor.JKM not in eu.base_prices


# ===========================================================================
# (c) Delta-derivation parity
# ===========================================================================


@pytest.mark.parametrize("params_factory", PARAMS_FACTORIES, ids=PARAMS_IDS)
def test_deltas_derived_from_quantities_match_analytic_deltas(tables, params_factory):
    """R6.1: all six analytic sensitivities (TTF, JKM, HH, FX, CHARTER,
    VLSFO), re-derived purely from CargoExposure.quantity_on(...), must
    match risk.analytic_deltas(): single-factor terms use the summed
    quantity directly; product terms (TTF x FX) multiply by the
    co-factor's base price; the FX delta additionally picks up the
    FX-linear ETS leg. This derivation is the single source of truth
    increment B switches risk.py's analytic_deltas() to read from (plan
    sect 6.A)."""
    params = params_factory()
    month_index = 0
    eu, asia = legacy_cargo_cashflows(D, tables, params, month_index)
    analytic = {d.name: d for d in risk.analytic_deltas(D, tables, params, month_index)}

    base_fx = eu.base_prices[RiskFactor.FX]
    base_ttf = eu.base_prices[RiskFactor.TTF]

    def assert_matches(actual_eu, actual_asia, name):
        expected = analytic[name]
        assert actual_eu == pytest.approx(expected.eu_cargo_delta, rel=1e-9, abs=0.01)
        assert actual_asia == pytest.approx(expected.asia_cargo_delta, rel=1e-9, abs=0.01)

    # TTF +1 EUR/MWh: product term quantity x base FX x shock; Europe only.
    d_ttf_eu = eu.quantity_on((RiskFactor.TTF, RiskFactor.FX)) * base_fx * 1.0
    assert_matches(d_ttf_eu, 0.0, "TTF +1 EUR/MWh")

    # JKM +0.10 $/MMBtu: single-factor; Asia only.
    d_jkm_asia = asia.quantity_on((RiskFactor.JKM,)) * 0.10
    assert_matches(0.0, d_jkm_asia, "JKM +0.10 $/MMBtu")

    # HH +0.10 $/MMBtu: single-factor; both routes.
    d_hh_eu = eu.quantity_on((RiskFactor.HH,)) * 0.10
    d_hh_asia = asia.quantity_on((RiskFactor.HH,)) * 0.10
    assert_matches(d_hh_eu, d_hh_asia, "HH +0.10 $/MMBtu")

    # EURUSD +0.01 parallel: product co-factor TTF, plus the FX-linear
    # ETS leg (EUA price folded into that quantity today); Europe only.
    d_fx_eu = (eu.quantity_on((RiskFactor.TTF, RiskFactor.FX)) * base_ttf
               + eu.quantity_on((RiskFactor.FX,))) * 0.01
    assert_matches(d_fx_eu, 0.0, "EURUSD +0.01 (parallel)")

    # Charter +$10k/day: single-factor; both routes.
    d_charter_eu = eu.quantity_on((RiskFactor.CHARTER,)) * 10_000
    d_charter_asia = asia.quantity_on((RiskFactor.CHARTER,)) * 10_000
    assert_matches(d_charter_eu, d_charter_asia, "Charter +$10k/day")

    # VLSFO +$50/t: single-factor; both routes.
    d_vlsfo_eu = eu.quantity_on((RiskFactor.VLSFO,)) * 50
    d_vlsfo_asia = asia.quantity_on((RiskFactor.VLSFO,)) * 50
    assert_matches(d_vlsfo_eu, d_vlsfo_asia, "VLSFO +$50/t")
