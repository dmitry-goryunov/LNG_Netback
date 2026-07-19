"""Tests for R6 increment F (plan sect 6.F, [R6.8, R6.9]): hedges DERIVED
from exposures -- risk.hedge_legs_from_exposure(),
eu_hedge_pnl_vector_from_exposure()/asia_hedge_pnl_vector_from_exposure(),
and historical_var_physical()'s new "hedged" portfolio (F.4).

Groups:
  (a) Pure unit tests of hedge_legs_from_exposure() on synthetic
      CargoExposure objects -- no workbook dependency (mirrors
      test_cashflows.py's own group (a) pattern).
  (b) Workbook-gated: the VLSFO hedge-tonnage drift-proofing claim (plan
      sect 1) -- legacy-basis tonnage matches the pre-existing Section 7
      pin (tests/test_risk_containment.py::
      test_operating_default_hedge_leg_vlsfo_tonnage_matches_strip_fuel),
      now sourced from hedge_legs_from_exposure() instead of
      europe_hedge_legs()/asia_hedge_legs(); physical-basis tonnage
      matches the REAL voyage ledger (recomputed independently via
      physical.py directly) and DIFFERS from the legacy flat-formula
      estimate -- both halves of the documented base-value gap (plan
      sect 8.5), not just an equality claim.
  (c) Workbook-gated: eu/asia_hedge_pnl_vector_from_exposure() internal
      consistency against the UNCHANGED Section 7 formula functions when
      fed the LEGACY exposure (proves the generalisation didn't silently
      change the legacy hedge's own math, only where the notional comes
      from -- F.3's byte-intact functions are never called by this file
      for anything other than this cross-check and the tonnage pin above).
  (d) Workbook-gated: historical_var_physical()'s new "hedged" portfolio
      (F.4) -- zero-shock, risk reduction under real scenarios, sunk-cost
      narrows the hedge too, and the charter-overlay/hedged regression
      the _charter_quantity_for_portfolio() fix this increment required.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

import cashflows
from cashflows import CargoExposure, CashFlow, RiskFactor
import data
import decision
import model
import physical
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


# ===========================================================================
# (a) Pure unit tests -- synthetic exposures, no workbook
# ===========================================================================


def test_single_factor_hedge_leg_shorts_a_long_exposure():
    """A positive (long) net exposure quantity must hedge to a SHORT
    position of the same magnitude -- 'to hedge a long cargo exposure you
    short the same factor quantities' (plan sect 6.F.1)."""
    exposure = CargoExposure("Europe", 0, cash_flows=[
        CashFlow((RiskFactor.JKM,), 0, 1_000_000.0, "JKM revenue"),
    ], base_prices={RiskFactor.JKM: 12.0})
    legs = risk.hedge_legs_from_exposure(exposure)
    assert len(legs) == 1
    row = legs.iloc[0]
    assert row["factor"] == "JKM"
    assert row["net_exposure_quantity"] == pytest.approx(1_000_000.0)
    assert row["hedge_quantity"] == pytest.approx(-1_000_000.0)
    assert row["direction"] == "Short"


def test_single_factor_hedge_leg_longs_a_short_exposure():
    """A negative (short/cost) net exposure quantity hedges LONG."""
    exposure = CargoExposure("Europe", 0, cash_flows=[
        CashFlow((RiskFactor.HH,), 0, -4_025_000.0, "HH procurement"),
    ], base_prices={RiskFactor.HH: 3.0})
    legs = risk.hedge_legs_from_exposure(exposure)
    row = legs.iloc[0]
    assert row["direction"] == "Long"
    assert row["hedge_quantity"] == pytest.approx(4_025_000.0)


def test_zero_net_exposure_is_flat_not_dropped():
    """A sunk (zeroed) leg still shows up as an explicit 'Flat' row, not
    silently omitted -- disclosure over silence."""
    exposure = CargoExposure("Europe", 0, cash_flows=[
        CashFlow((RiskFactor.HH,), 0, 0.0, "HH procurement -- SUNK, zeroed"),
    ], base_prices={RiskFactor.HH: 3.0})
    legs = risk.hedge_legs_from_exposure(exposure)
    assert len(legs) == 1
    assert legs.iloc[0]["direction"] == "Flat"
    assert legs.iloc[0]["hedge_quantity"] == 0.0


def test_bilinear_ttf_fx_term_splits_into_two_legs():
    """(TTF, FX) decomposes into a native-unit TTF leg (the product's own
    quantity) plus an FX leg notional = quantity x base TTF -- exactly how
    europe_hedge_legs() splits TTF future + EUR forward today."""
    exposure = CargoExposure("Europe", 0, cash_flows=[
        CashFlow((RiskFactor.TTF, RiskFactor.FX), 0, 500_000.0, "TTF revenue x FX"),
    ], base_prices={RiskFactor.TTF: 30.0, RiskFactor.FX: 1.08})
    legs = risk.hedge_legs_from_exposure(exposure).set_index("factor")

    assert legs.loc["TTF", "net_exposure_quantity"] == pytest.approx(500_000.0)
    assert legs.loc["TTF", "hedge_quantity"] == pytest.approx(-500_000.0)
    assert legs.loc["TTF", "direction"] == "Short"

    expected_fx_notional = 500_000.0 * 30.0
    assert legs.loc["FX", "net_exposure_quantity"] == pytest.approx(expected_fx_notional)
    assert legs.loc["FX", "hedge_quantity"] == pytest.approx(-expected_fx_notional)
    assert legs.loc["FX", "direction"] == "Short"


def test_fx_legs_from_bilinear_and_linear_terms_are_netted_into_one_row():
    """Europe's FX exposure comes from TWO sources -- the TTF-revenue
    bilinear co-factor AND the standalone ETS FX-linear term -- and must
    net into ONE combined FX row, matching analytic_deltas()'s own FX
    delta formula (quantity_on((TTF,FX))*base_ttf + quantity_on((FX,)),
    tests/test_cashflows.py::test_deltas_derived_from_quantities_match_
    analytic_deltas pins this same combination)."""
    exposure = CargoExposure("Europe", 0, cash_flows=[
        CashFlow((RiskFactor.TTF, RiskFactor.FX), 0, 500_000.0, "TTF revenue x FX"),
        CashFlow((RiskFactor.FX,), 0, -300_000.0, "ETS (EUA folded)"),
    ], base_prices={RiskFactor.TTF: 30.0, RiskFactor.FX: 1.08})
    legs = risk.hedge_legs_from_exposure(exposure)
    fx_rows = legs[legs["factor"] == "FX"]
    assert len(fx_rows) == 1
    expected_fx_notional = 500_000.0 * 30.0 + (-300_000.0)
    assert fx_rows.iloc[0]["net_exposure_quantity"] == pytest.approx(expected_fx_notional)


def test_asia_exposure_has_no_fx_row():
    """Asia never references FX -- hedge_legs_from_exposure() must not
    invent one."""
    exposure = CargoExposure("Asia", 0, cash_flows=[
        CashFlow((RiskFactor.JKM,), 0, 1_000_000.0, "JKM revenue"),
        CashFlow((RiskFactor.HH,), 0, -300_000.0, "HH procurement"),
    ], base_prices={RiskFactor.JKM: 12.0, RiskFactor.HH: 3.0})
    legs = risk.hedge_legs_from_exposure(exposure)
    assert "FX" not in set(legs["factor"])


def test_constant_cashflow_generates_no_leg():
    exposure = CargoExposure("Europe", 0, cash_flows=[
        CashFlow((), 0, -500_000.0, "fixed fees"),
        CashFlow((RiskFactor.HH,), 0, -100_000.0, "HH"),
    ], base_prices={RiskFactor.HH: 3.0})
    legs = risk.hedge_legs_from_exposure(exposure)
    assert len(legs) == 1
    assert legs.iloc[0]["factor"] == "HH"


def test_non_fx_bilinear_pair_raises_not_implemented():
    """A bilinear term that doesn't involve FX has no decomposition rule
    -- must fail loud, not guess."""
    exposure = CargoExposure("Test", 0, cash_flows=[
        CashFlow((RiskFactor.EUA, RiskFactor.CHARTER), 0, 100.0, "made-up product"),
    ], base_prices={RiskFactor.EUA: 70.0, RiskFactor.CHARTER: 60_000.0})
    with pytest.raises(NotImplementedError):
        risk.hedge_legs_from_exposure(exposure)


def test_lot_rounding_worked_example_shows_residual_and_tx_cost():
    """Worked example: a JKM leg (lot size 10,000 MMBtu/lot per
    CONTRACT_SPECS) whose hedge notional is NOT an exact multiple of the
    lot size -- lots must round to the NEAREST integer, and lot_residual
    must be the exact leftover the rounding can't reach (shown, not
    hidden -- plan sect 6.F.2)."""
    net_qty = 1_003_456.0  # deliberately not a multiple of 10,000
    exposure = CargoExposure("Asia", 0, cash_flows=[
        CashFlow((RiskFactor.JKM,), 0, net_qty, "JKM revenue"),
    ], base_prices={RiskFactor.JKM: 12.0})
    legs = risk.hedge_legs_from_exposure(exposure)
    row = legs.iloc[0]

    lot_size = risk.CONTRACT_SPECS["ICE JKM"]["size"]
    hedge_qty = -net_qty
    expected_lots = float(np.round(hedge_qty / lot_size))
    expected_residual = hedge_qty - expected_lots * lot_size

    assert row["lot_size"] == pytest.approx(lot_size)
    assert row["lots"] == pytest.approx(expected_lots)
    assert row["lot_residual"] == pytest.approx(expected_residual)
    assert abs(row["lot_residual"]) < lot_size / 2.0 + 1e-6
    assert row["verified"] == False
    expected_tx_cost = abs(hedge_qty) * 12.0 * risk.HEDGE_TX_COST_BPS_LISTED / 10_000.0
    assert row["tx_cost_usd"] == pytest.approx(expected_tx_cost)


def test_charter_leg_has_no_liquid_contract_full_residual():
    """CHARTER has no CONTRACT_SPECS-listed lot size -- the ENTIRE
    notional reports as residual (no lot concept to round to), the wider
    OTC tx-cost bps applies, verified is False."""
    exposure = CargoExposure("Europe", 0, cash_flows=[
        CashFlow((RiskFactor.CHARTER,), 0, -1_500.0, "charter hire"),
    ], base_prices={RiskFactor.CHARTER: 65_000.0})
    legs = risk.hedge_legs_from_exposure(exposure)
    row = legs.iloc[0]
    assert row["lot_size"] is None
    assert row["lots"] is None
    assert row["lot_residual"] == pytest.approx(1_500.0)
    assert row["tx_cost_bps"] == risk.HEDGE_TX_COST_BPS_OTC
    assert row["verified"] == False


def test_ttf_lot_size_uses_hours_in_month_when_given():
    exposure = CargoExposure("Europe", 0, cash_flows=[
        CashFlow((RiskFactor.TTF, RiskFactor.FX), 0, 730_000.0, "TTF revenue"),
    ], base_prices={RiskFactor.TTF: 30.0, RiskFactor.FX: 1.08})
    load_month = pd.Timestamp("2026-09-01")   # September, 30 days -> 720 hours
    legs = risk.hedge_legs_from_exposure(exposure, load_month=load_month)
    ttf_row = legs[legs["factor"] == "TTF"].iloc[0]
    assert ttf_row["lot_size"] == pytest.approx(risk.hours_in_month(load_month))
    assert ttf_row["lot_size"] == pytest.approx(720.0)


def test_ttf_lot_size_falls_back_when_load_month_omitted():
    exposure = CargoExposure("Europe", 0, cash_flows=[
        CashFlow((RiskFactor.TTF, RiskFactor.FX), 0, 730_000.0, "TTF revenue"),
    ], base_prices={RiskFactor.TTF: 30.0, RiskFactor.FX: 1.08})
    legs = risk.hedge_legs_from_exposure(exposure)
    ttf_row = legs[legs["factor"] == "TTF"].iloc[0]
    assert ttf_row["lot_size"] == pytest.approx(risk.TTF_LOT_HOURS_FALLBACK)


# ===========================================================================
# (b) Workbook-gated: the VLSFO hedge-tonnage drift-proofing claim
# ===========================================================================


def test_legacy_hedge_vlsfo_tonnage_matches_strip_fuel_via_exposure(tables):
    """R6 increment F's headline claim, legacy half: hedge_legs_from_
    exposure(), applied to the LEGACY exposure, reproduces the EXACT
    tonnage the pre-existing Section 7 pin
    (tests/test_risk_containment.py::
    test_operating_default_hedge_leg_vlsfo_tonnage_matches_strip_fuel)
    independently recomputes from Params fields -- proving the NEW
    single-source-of-truth function agrees with the hand-maintained
    formula it is meant to make structurally impossible to drift from
    going forward."""
    params = model.operating_default_params()
    eu, asia = cashflows.legacy_cargo_cashflows(D, tables, params, 0)

    eu_legs = risk.hedge_legs_from_exposure(eu)
    eu_vlsfo = eu_legs[eu_legs["factor"] == "VLSFO"].iloc[0]
    eu_expected = (params.residual_laden_vlsfo * params.europe_laden_days
                   + params.ballast_fuel * params.europe_ballast_days
                   + params.port_fuel_rate * (params.europe_port_days + params.loading_days))
    assert eu_vlsfo["hedge_quantity"] == pytest.approx(eu_expected, rel=1e-12)
    # VLSFO is a COST (negative exposure quantity), so hedge_quantity is
    # POSITIVE -> "Long": you hedge a fuel cost by going long fuel (a long
    # bunker swap gains when VLSFO rises, offsetting a higher bunker bill),
    # exactly as the legacy "Long NG future" hedges the HH cost.
    assert eu_vlsfo["direction"] == "Long"

    asia_legs = risk.hedge_legs_from_exposure(asia)
    asia_vlsfo = asia_legs[asia_legs["factor"] == "VLSFO"].iloc[0]
    asia_ballast = (params.asia_rt_days - params.asia_laden_days
                    - params.asia_port_days - params.loading_days)
    asia_expected = (params.residual_laden_vlsfo * params.asia_laden_days
                     + params.ballast_fuel * asia_ballast
                     + params.port_fuel_rate * (params.asia_port_days + params.loading_days))
    assert asia_vlsfo["hedge_quantity"] == pytest.approx(asia_expected, rel=1e-12)

    # Cross-check against the EXISTING, byte-intact (F.3) Section 7
    # functions too -- both paths must agree, since both ultimately read
    # the same Params-derived legacy quantity.
    legacy_eu_legs = risk.europe_hedge_legs(D, tables, params)
    legacy_swap = float(legacy_eu_legs.loc[legacy_eu_legs["leg"].str.startswith("VLSFO swap"), "volume"].iloc[0])
    assert eu_vlsfo["hedge_quantity"] == pytest.approx(legacy_swap, rel=1e-9)

    legacy_asia_legs = risk.asia_hedge_legs(D, tables, params)
    legacy_asia_swap = float(
        legacy_asia_legs.loc[legacy_asia_legs["leg"].str.startswith("VLSFO swap"), "volume"].iloc[0]
    )
    assert asia_vlsfo["hedge_quantity"] == pytest.approx(legacy_asia_swap, rel=1e-9)


@pytest.mark.parametrize(
    ("route", "segments_fn"),
    [("Europe", physical.europe_route_segments), ("Asia", physical.asia_route_segments)],
)
def test_physical_hedge_vlsfo_tonnage_matches_real_ledger_not_legacy_formula(tables, route, segments_fn):
    """R6 increment F's headline claim, physical half (this is the drift
    this rebuild kills, plan sect 1): on the PHYSICAL basis, the
    exposure-derived VLSFO hedge tonnage equals the physical engine's
    REAL net purchased fuel -- physical.run_voyage()'s own
    ledger.total_liquid_fuel_tonnes, recomputed HERE directly via
    physical.py (READ-ONLY ground truth per this increment's hard
    constraints, not re-derived through cashflows.py or risk.py at all)
    -- and, by design (cashflows.physical_cargo_quantities()'s own
    docstring: "the engine's REAL net purchased fuel..., not the legacy
    flat formula's estimate"), this quantity DIFFERS from model.strip()'s
    flat fuel formula (the SAME quantity the legacy exposure's VLSFO leg
    carries, per the test above). Both halves of the documented
    legacy-vs-physical base-value gap (plan sect 8.5) are asserted here,
    not just the equality half."""
    params = model.operating_default_params()

    vessel = physical.vessel_performance_from_params(params)
    segments = segments_fn(params)
    heel_target = params.heel_fraction * params.cargo_size
    ledger = physical.run_voyage(segments, vessel, loaded_mmbtu=params.cargo_size, heel_target_mmbtu=heel_target)

    exposure = cashflows.physical_cargo_cashflows(D, tables, params, 0, route)
    legs = risk.hedge_legs_from_exposure(exposure)
    vlsfo_row = legs[legs["factor"] == "VLSFO"].iloc[0]

    assert vlsfo_row["hedge_quantity"] == pytest.approx(ledger.total_liquid_fuel_tonnes, rel=1e-9)
    # VLSFO is a cost -> hedge LONG (see the legacy-tonnage test above for
    # the full sign reasoning).
    assert vlsfo_row["direction"] == "Long"

    legacy_eu, legacy_asia = cashflows.legacy_cargo_cashflows(D, tables, params, 0)
    legacy_exposure = legacy_eu if route == "Europe" else legacy_asia
    legacy_fuel_qty = -legacy_exposure.quantity_on((RiskFactor.VLSFO,))  # model.strip()'s fuel definition
    assert vlsfo_row["hedge_quantity"] != pytest.approx(legacy_fuel_qty, rel=1e-6), (
        "the physical-basis fuel tonnage must NOT silently match the legacy flat-formula estimate -- "
        "if it does, the physical engine isn't actually being consulted (plan sect 8.5's documented gap "
        "would have vanished, which would itself be a bug, not an improvement)"
    )
    print(f"[{route}] physical ledger fuel={ledger.total_liquid_fuel_tonnes:,.1f}t vs "
          f"legacy formula fuel={legacy_fuel_qty:,.1f}t")


# ===========================================================================
# (c) Workbook-gated: pnl-vector generalisation matches the byte-intact
# Section 7 formula functions when fed the legacy exposure
# ===========================================================================


def test_eu_hedge_pnl_vector_from_exposure_matches_legacy_formula_on_legacy_exposure(tables):
    params = model.operating_default_params()
    eu, _ = cashflows.legacy_cargo_cashflows(D, tables, params, 0)
    rng = np.random.default_rng(20260718)
    n = 50
    ttf_scen = eu.base_prices[RiskFactor.TTF] * np.exp(rng.normal(0, 0.02, n))
    hh_scen = eu.base_prices[RiskFactor.HH] * np.exp(rng.normal(0, 0.02, n))
    fx_scen = eu.base_prices[RiskFactor.FX] * np.exp(rng.normal(0, 0.01, n))

    from_exposure = risk.eu_hedge_pnl_vector_from_exposure(eu, ttf_scen, hh_scen, fx_scen)
    from_formula = risk.eu_hedge_pnl_vector(
        ttf_scen, hh_scen, fx_scen,
        eu.base_prices[RiskFactor.TTF], eu.base_prices[RiskFactor.HH], eu.base_prices[RiskFactor.FX],
        params.cargo_size, params.hh_grossup, params.boil_off_rate, params.europe_laden_days,
    )
    np.testing.assert_allclose(from_exposure, from_formula, rtol=1e-9, atol=1e-6)


def test_asia_hedge_pnl_vector_from_exposure_matches_legacy_formula_on_legacy_exposure(tables):
    params = model.operating_default_params()
    _, asia = cashflows.legacy_cargo_cashflows(D, tables, params, 0)
    rng = np.random.default_rng(20260719)
    n = 50
    jkm_scen = asia.base_prices[RiskFactor.JKM] * np.exp(rng.normal(0, 0.02, n))
    hh_scen = asia.base_prices[RiskFactor.HH] * np.exp(rng.normal(0, 0.02, n))

    from_exposure = risk.asia_hedge_pnl_vector_from_exposure(asia, jkm_scen, hh_scen)
    from_formula = risk.asia_hedge_pnl_vector(
        jkm_scen, hh_scen, asia.base_prices[RiskFactor.JKM], asia.base_prices[RiskFactor.HH],
        params.cargo_size, params.hh_grossup, params.boil_off_rate, params.asia_laden_days,
    )
    np.testing.assert_allclose(from_exposure, from_formula, rtol=1e-9, atol=1e-6)


# ===========================================================================
# (d) Workbook-gated: historical_var_physical()'s new "hedged" portfolio (F.4)
# ===========================================================================


def _zero_scenario() -> risk.ScenarioSet:
    return risk.ScenarioSet(
        dates=[pd.Timestamp("2026-07-07")],
        hh_ret=np.zeros((1, 13)), ttf_ret=np.zeros((1, 13)), jkm_ret=np.zeros((1, 13)),
        fx_ret=np.zeros(1), end_date=pd.Timestamp("2026-07-07"), lookback=1, method="naive",
    )


@pytest.mark.parametrize("basin", ["Europe", "Asia"])
def test_physical_hedged_zero_shock_pnl_is_zero(tables, basin):
    """Same R1-style construction as every other zero-shock test in this
    codebase: a zero-return ScenarioSet must reprice the hedged residual
    to ~$0 P&L (cargo P&L and hedge P&L both individually zero at zero
    shock, so their sum is too)."""
    result = risk.historical_var_physical(
        D, tables, model.operating_default_params(), portfolio="hedged", basin=basin,
        month_index=0, scen=_zero_scenario(),
    )
    assert abs(float(result.pnl[0])) <= 0.01


@pytest.mark.parametrize("basin", ["Europe", "Asia"])
def test_physical_hedged_residual_var_is_materially_smaller_than_unhedged(tables, basin):
    """F.4's acceptance criterion: the hedge must actually DO something
    under REAL (non-zero) scenarios, on the SAME portfolio/scenarios."""
    params = model.operating_default_params()
    scen = risk.build_scenarios(tables, D, lookback=250, method="naive")

    unhedged = risk.historical_var_physical(
        D, tables, params, portfolio="single", basin=basin, month_index=0, scen=scen,
    )
    hedged = risk.historical_var_physical(
        D, tables, params, portfolio="hedged", basin=basin, month_index=0, scen=scen,
    )
    assert abs(hedged.var95) < abs(unhedged.var95)
    assert abs(hedged.sd) < abs(unhedged.sd)
    ratio = abs(hedged.var95) / max(abs(unhedged.var95), 1.0)
    assert ratio < 0.5, f"hedge should materially reduce risk, got ratio={ratio:.2%}"


def test_physical_hedged_sunk_procurement_propagates_to_hedge_legs(tables):
    """C.2's sunk-cost zeroing propagates through the DERIVED HEDGE: with
    procurement sunk (ALREADY_LOADED) the HH cargo cash flow is zeroed, so
    the exposure-derived HH hedge leg goes to zero -- nothing left to
    hedge. But HH is fully hedged in BOTH states, so the hedged RESIDUAL
    VaR is economically UNCHANGED between states: the sunk leg is exactly
    the leg the hedge covers, so zeroing it removes an offsetting
    cargo+hedge pair, not net risk.

    A strict VaR narrowing (as an earlier draft asserted) would be WRONG
    here -- the residual difference between states is float-rounding noise
    on an economically-identical number (~1e-9 on ~$12k), not a material
    narrowing, and would flip sign under a different BLAS build. Contrast
    the UNHEDGED single-cargo version
    (test_physical_cashflows.py::test_physical_basis_sunk_procurement_
    narrows_var), which DOES narrow, because there the HH cargo risk is
    unhedged. This test instead pins the two properties that are actually
    true: (a) the HH hedge leg vanishes when sunk, (b) the hedged residual
    is unchanged."""
    params = model.operating_default_params()

    # (a) the HH hedge leg is a real, material Long when fully pre-lift and
    #     exactly zero once procurement is sunk (pinned to first principles:
    #     the HH hedge quantity IS -quantity_on((HH,)) on the exposure).
    exp_exposed = cashflows.physical_cargo_cashflows(
        D, tables, params, 0, "Europe",
        first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT)
    exp_sunk = cashflows.physical_cargo_cashflows(
        D, tables, params, 0, "Europe",
        first_cargo_state=decision.FirstCargoState.ALREADY_LOADED)

    legs_exposed = risk.hedge_legs_from_exposure(exp_exposed)
    legs_sunk = risk.hedge_legs_from_exposure(exp_sunk)
    hh_hedge_exposed = float(legs_exposed.loc[legs_exposed["factor"] == "HH", "hedge_quantity"].iloc[0])
    hh_hedge_sunk = float(legs_sunk.loc[legs_sunk["factor"] == "HH", "hedge_quantity"].iloc[0])

    expected_hh_hedge = -exp_exposed.quantity_on((cashflows.RiskFactor.HH,))
    assert hh_hedge_exposed == pytest.approx(expected_hh_hedge)
    assert expected_hh_hedge > 1_000_000.0          # a real, material HH hedge when exposed
    assert hh_hedge_sunk == pytest.approx(0.0)       # vanishes once procurement is sunk

    # (b) the hedged residual is economically UNCHANGED between states (HH
    #     hedged either way) -- approximate equality, not a strict
    #     inequality on float noise.
    scen = risk.build_scenarios(tables, D, lookback=250, method="naive")
    hedged_exposed = risk.historical_var_physical(
        D, tables, params, portfolio="hedged", basin="Europe", month_index=0, scen=scen,
        first_cargo_state=decision.FirstCargoState.FULLY_PRE_LIFT)
    hedged_sunk = risk.historical_var_physical(
        D, tables, params, portfolio="hedged", basin="Europe", month_index=0, scen=scen,
        first_cargo_state=decision.FirstCargoState.ALREADY_LOADED)
    np.testing.assert_allclose(hedged_sunk.pnl, hedged_exposed.pnl, atol=1e-4)


def test_charter_overlay_with_physical_hedged_portfolio_does_not_crash(tables):
    """Regression test for the _charter_quantity_for_portfolio() fix this
    increment required: before it, the physical branch only recognised
    single/spread/programme, so selecting 'Hedged residual' on the VaR
    page and switching on the charter overlay would have raised
    ValueError. Must now succeed cleanly on both basins."""
    params = model.operating_default_params()
    scen = risk.build_scenarios(tables, D, lookback=250, method="naive")
    for basin in ("Europe", "Asia"):
        r = risk.historical_var_physical(
            D, tables, params, portfolio="hedged", basin=basin, month_index=0, scen=scen,
        )
        r2 = risk.apply_charter_overlay(r, D, tables, params, portfolio="hedged", month_index=0, basin=basin)
        assert np.isfinite(r2.var95)
        assert len(r2.pnl) == len(r.pnl)


def test_physical_basis_now_accepts_hedged_portfolio(tables):
    """Direct acceptance test: 'hedged' must no longer raise on the
    physical basis (superseding the old rejection -- see
    tests/test_physical_cashflows.py::
    test_physical_basis_rejects_unsupported_portfolios, updated by this
    same increment to drop 'hedged' from its parametrisation)."""
    result = risk.historical_var_physical(
        D, tables, model.operating_default_params(), portfolio="hedged", basin="Europe",
        month_index=0, scen=_zero_scenario(),
    )
    assert result.portfolio == "hedged"
    assert result.basis == "physical"
