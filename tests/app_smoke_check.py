"""Headless Streamlit smoke checks.

Run directly, not through pytest, because Streamlit's AppTest runtime can
interact poorly with some pytest event-loop plugins.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LNG_HISTORY_XLSX", str(ROOT / "LNG history.xlsx"))

import dataclasses  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import model  # noqa: E402 (after sys.path insert, matches app.py's own import order)

app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
assert not app.exception, [e.message for e in app.exception]
assert [title.value for title in app.title] == ["LNG cargo and vessel decision"]
metrics = {metric.label: metric.value for metric in app.metric}
# Operating case since 16-Jul-2026: 17 kn / 1.5 d loading / 1.5 d
# unloading (model.operating_default_params), and the default programme
# horizon is DERIVED as two Europe round trips rounded up (54.1 d at
# this geometry; user instruction "make it fit 2x Europe" -- a literal
# 54.0 would exclude the second voyage by ~56 minutes since one RT is
# 27.0196 d, not a clean 27).
assert metrics["Best programme"] == "Europe -> Europe"
assert metrics["Used vessel-days"] == "54.0392"
# The Decision page now strips out 36 months using fx_curve_multi(), which
# interpolates through real 2Y/3Y FX anchors instead of extrapolating past
# 1Y -- so unlike the old 12-month-only strip, nothing in this window should
# be flagged. This is the fix, not a gap: it directly resolves the M12
# extrapolation warning that used to fire here every time.
assert not any("beyond the available" in w.value for w in app.warning), \
    "no month within a 36-month strip should need FX extrapolation (real anchors run to 10Y)"
print("PASS decision page: discrete programme, 36-month strip, no FX extrapolation")

page = next(widget for widget in app.sidebar.radio if widget.label == "Page")
page.set_value("4 VaR & stress")
app.run(timeout=60)
assert not app.exception, [e.message for e in app.exception]
roll = next(box for box in app.checkbox if "roll-aligned" in box.label)
assert roll.value is True
print("PASS risk page: roll-aligned scenarios default on")

# Re-fetch the widget: AppTest element references go stale after app.run()
# rebuilds its internal tree, so reusing the `page` object from above would
# silently no-op (and did, the first time this was written -- caught by the
# subheader assertion below actually failing against the *previous* page's
# content instead of raising).
page = next(widget for widget in app.sidebar.radio if widget.label == "Page")
page.set_value("1 Forward strip")
app.run(timeout=60)
assert not app.exception, [e.message for e in app.exception]
assert any("Intrinsic / extrinsic value" in h.value for h in app.subheader), \
    "Forward-strip page should render the JKM-vs-TTF diversion option section"
print("PASS forward-strip page: loads clean, intrinsic/extrinsic section present")

# The remaining two pages were never smoke-covered at all (review gap):
# an import-time or render-time break there would only surface in
# production. Load-clean assertions are deliberately minimal.
for page_name in ("2 Sensitivities", "3 Hedging"):
    page = next(widget for widget in app.sidebar.radio if widget.label == "Page")
    page.set_value(page_name)
    app.run(timeout=120)
    assert not app.exception, [f"{page_name}: {e.message}" for e in app.exception]
    print(f"PASS {page_name.split(' ', 1)[1].lower()} page: loads clean")

# Regression check for the stale-session-state guard (redacted production
# AttributeError at p.heel_fraction on Streamlit Cloud, 17-Jul-2026): a
# browser session that stayed open across a deploy adding a new Params
# field carries an old-shaped instance forever, since a module reload
# doesn't retroactively add fields to an already-constructed object.
# Pre-seed a fresh AppTest's session_state with exactly that shape. A
# SimpleNamespace, not a Params instance with a deleted attribute: fields
# with plain literal defaults (e.g. heel_fraction: float = 0.0) leave
# that default reachable as a *class* attribute, so hasattr() on a
# same-class instance falls through to it even after `del`, which would
# never trigger the guard and silently defeat this whole check. A real
# stale instance predates the field at the class level too (it's bound
# to the pre-reload Params class), which only an unrelated type reproduces.
stale_app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
fresh_params = model.operating_default_params()
stale_params = SimpleNamespace(**{
    f.name: getattr(fresh_params, f.name) for f in dataclasses.fields(model.Params)
    if f.name != "heel_fraction"
})
stale_app.session_state["params"] = stale_params
stale_app.run()
assert not stale_app.exception, [e.message for e in stale_app.exception]
assert hasattr(stale_app.session_state["params"], "heel_fraction"), \
    "a stale params instance should be reset to current defaults, not crashed on"
assert any("predated" in i.value for i in stale_app.info), \
    "resetting a stale session should tell the user why their inputs changed"
print("PASS stale-session guard: old-shaped params reset cleanly, no crash")

# R6 increment C.5: the VaR page's "Value basis" toggle. Navigate the main
# AppTest instance back to the VaR page, flip the basis radio to Physical
# engine, and assert the physical-basis surface renders: no exception, the
# basis-disclosure caption appears, the portfolio list narrows to
# single/spread only (plan sect 8.3 -- no 12cargo, no hedged), and the
# first-cargo-state selector shows up. (The stress table's replay-row n/a
# marking is pinned by tests/test_physical_cashflows.py's stress group,
# not here.)
page = next(widget for widget in app.sidebar.radio if widget.label == "Page")
page.set_value("4 VaR & stress")
app.run(timeout=120)
assert not app.exception, [e.message for e in app.exception]
basis = next(w for w in app.radio if w.label == "Value basis")
assert basis.value == "Legacy strip (frozen)", "legacy must stay the default basis"
basis.set_value("Physical engine")
app.run(timeout=120)
assert not app.exception, [e.message for e in app.exception]
assert any("physical engine" in c.value for c in app.caption), \
    "physical basis must render its basis-disclosure caption"
portfolio_box = next(s for s in app.selectbox if s.label == "Portfolio")
assert len(portfolio_box.options) == 4, \
    (f"physical basis portfolio list must be committed programme + single Europe/Asia + spread, "
     f"got {portfolio_box.options}")
assert not any("12-cargo" in o for o in portfolio_box.options), \
    "12cargo must not be selectable under the physical basis (plan sect 8.3)"
assert any(w.label == "Current cargo state" for w in app.radio), \
    "physical basis must surface the Decision page's first-cargo-state selector"
print("PASS var page: physical basis toggle renders, portfolio list narrowed, state selector present")

# R6 increment D.2 (plan sect 6.D.2): "Committed programme" replaces the
# infeasible 12-cargo strip as the physical basis's DEFAULT portfolio (the
# legacy basis's own PORTFOLIO_MAP is untouched -- verified separately
# below). Its captions must disclose hold-plan-fixed pricing and which
# programme is being priced.
assert portfolio_box.value == "Committed programme", \
    f"Committed programme must be the default physical-basis portfolio, got {portfolio_box.value!r}"
assert not app.exception, [e.message for e in app.exception]
assert any("hold-plan-fixed" in c.value.lower() for c in app.caption), \
    "committed-programme portfolio must disclose hold-plan-fixed pricing in a caption"
assert any("committed programme" in c.value.lower() for c in app.caption), \
    "committed-programme portfolio must disclose which programme (legs/routes/months) is being priced"
print("PASS var page: committed programme is the default physical-basis portfolio and discloses hold-plan-fixed pricing")

# Legacy basis's own PORTFOLIO_MAP (plan sect 8 decision 3: "12cargo"
# stays legacy-basis-only, fixture-bound, untouched by this increment) must
# be completely unperturbed by reordering PORTFOLIO_MAP_PHYSICAL above --
# same 6 options, same pre-existing default ("Single cargo - Europe" was
# first before this increment and still is; 12cargo itself was never the
# UI's default selection, only the still-selectable legacy comparison
# point the plan says must survive). Re-fetch `basis` first -- AppTest
# element references go stale after app.run() rebuilds the tree (see the
# "Page" radio re-fetch above); reusing the line-115 reference here would
# silently no-op, exactly the gotcha that comment already warns about.
basis = next(w for w in app.radio if w.label == "Value basis")
basis.set_value("Legacy strip (frozen)")
app.run(timeout=120)
assert not app.exception, [e.message for e in app.exception]
legacy_portfolio_box = next(s for s in app.selectbox if s.label == "Portfolio")
assert len(legacy_portfolio_box.options) == 6, \
    f"legacy basis portfolio list must be unchanged (6 options), got {legacy_portfolio_box.options}"
assert "12-cargo strip (verdict-optimal)" in legacy_portfolio_box.options, \
    "12cargo must remain selectable under the legacy basis (plan sect 8 decision 3)"
assert legacy_portfolio_box.value == "Single cargo - Europe", \
    f"legacy basis default portfolio must stay unchanged, got {legacy_portfolio_box.value!r}"
print("PASS var page: legacy basis portfolio list and default unchanged by the physical-basis reordering")

# R6 increment E.3 (plan sect 6.E.3): the factor-coverage disclosure line
# renders on the legacy basis (still current page/basis from the block
# above) with no silent omissions -- names every RiskFactor plus the
# excluded locational basis. EUA must read as unconditionally
# deterministic HERE (legacy is frozen to model.strip(), no live-factor
# path -- risk.factor_coverage_line()'s own basis-conditional branch).
coverage_captions = [c.value for c in app.caption if "Factor coverage this run" in c.value]
assert len(coverage_captions) == 1, f"expected exactly one factor-coverage caption, got {len(coverage_captions)}"
assert "HH / TTF / JKM / FX -- stochastic" in coverage_captions[0]
assert "EXCLUDED" in coverage_captions[0] and "locational basis" in coverage_captions[0], \
    "locational (NWE/JKM) basis must be disclosed as excluded, plan sect 8 decision 4"
assert "frozen to model.strip()" in coverage_captions[0], \
    "legacy basis EUA status must read as unconditionally deterministic (no live-factor path on legacy)"
print("PASS var page: factor-coverage disclosure line renders on the legacy basis, no silent omissions")

# R6 increment E.1(b): the optional independent charter overlay checkbox,
# default OFF, both bases. Toggling it on must not raise and must render
# BOTH required assumptions (independence, calibration basis) plus the
# "model overlay, not historical simulation" label -- plan sect 6.E.1(b)'s
# explicit requirement.
overlay_box = next(b for b in app.checkbox if "charter overlay" in b.label.lower())
assert overlay_box.value is False, "charter overlay must default OFF"
overlay_box.set_value(True)
app.run(timeout=120)
assert not app.exception, [e.message for e in app.exception]
overlay_captions = [c.value for c in app.caption if "Charter overlay ON" in c.value]
assert len(overlay_captions) == 1, f"expected exactly one charter-overlay caption when ON, got {len(overlay_captions)}"
assert "model overlay, not historical simulation" in overlay_captions[0]
assert "Calibration basis" in overlay_captions[0] and "vol of the" in overlay_captions[0], \
    "overlay caption must disclose the calibration basis (plan sect 6.E.1(b))"
assert "Independence assumption" in overlay_captions[0] and "ZERO correlation" in overlay_captions[0], \
    "overlay caption must disclose the independence assumption (plan sect 6.E.1(b))"
coverage_with_overlay = next(c.value for c in app.caption if "Factor coverage this run" in c.value)
assert "optional independent model overlay (ON" in coverage_with_overlay, \
    "factor-coverage line must reflect the overlay toggle state"
print("PASS var page: charter overlay defaults off, discloses both required assumptions when switched on")

# Same overlay checkbox under the PHYSICAL basis (plan sect 6.E.1(b):
# "VaR page checkbox, BOTH bases") -- re-fetch every widget reference
# after app.run() per this file's own established gotcha (see the
# "Page"/"basis" re-fetch comments above). The checkbox is `key`-based,
# so (like the pre-existing "roll-aligned"/"Current cargo state" widgets
# on this same page) its ON state from the legacy-basis block above
# PERSISTS across the basis switch -- Streamlit widget state semantics,
# not a bug -- so this exercises the overlay's PHYSICAL-basis code path
# (a different historical_var_physical()-based portfolio-quantity branch
# inside risk._charter_quantity_for_portfolio()) without re-toggling.
# Also re-checks the factor-coverage line's basis-conditional EUA
# wording: physical basis must NOT claim the legacy-only "frozen to
# model.strip()" text.
basis = next(w for w in app.radio if w.label == "Value basis")
basis.set_value("Physical engine")
app.run(timeout=120)
assert not app.exception, [e.message for e in app.exception]
phys_coverage = next(c.value for c in app.caption if "Factor coverage this run" in c.value)
assert "frozen to model.strip()" not in phys_coverage, \
    "physical basis must not claim the legacy-only EUA wording"
phys_overlay_box = next(b for b in app.checkbox if "charter overlay" in b.label.lower())
assert phys_overlay_box.value is True, \
    "charter overlay's keyed widget state should persist across a basis switch (Streamlit semantics)"
assert any("Charter overlay ON" in c.value for c in app.caption), \
    "charter overlay must render its disclosure caption under the physical basis too"
# Toggle it back off, proving the OFF path also still works cleanly
# under the physical basis (not just the legacy basis exercised above).
phys_overlay_box.set_value(False)
app.run(timeout=120)
assert not app.exception, [e.message for e in app.exception]
assert not any("Charter overlay ON" in c.value for c in app.caption), \
    "charter overlay caption must disappear once switched back off"
print("PASS var page: charter overlay and factor-coverage disclosure both work under the physical basis")

# Hardcoded-curve fallback: the app must produce numbers for ONE curve date
# with NO workbook at all (a mounted/uploaded xls overrides it, exercised by
# every check above, which all ran on the real workbook). Force the
# no-workbook path by clearing both discovery routes get_tables() consults
# -- the live LNG_HISTORY_XLSX env var and data.DEFAULT_CANDIDATE_PATHS (the
# env var is captured into that list at data.py import time, so popping the
# env var alone is not enough; empty the list too). A fresh AppTest then
# falls through to hardcoded_curve.build_hardcoded_tables().
import data  # noqa: E402
import hardcoded_curve  # noqa: E402

os.environ.pop("LNG_HISTORY_XLSX", None)
data.DEFAULT_CANDIDATE_PATHS = []
hc_app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
assert not hc_app.exception, [e.message for e in hc_app.exception]
assert [t.value for t in hc_app.title] == ["LNG cargo and vessel decision"], \
    "Decision page must render on the built-in curve with no workbook"
_disclosure_msgs = ([w.value for w in hc_app.sidebar.warning]
                    + [w.value for w in hc_app.sidebar.info]
                    + [w.value for w in hc_app.info])
assert any("hardcoded forward curve" in m or "built-in" in m.lower() for m in _disclosure_msgs), \
    "hardcoded mode must disclose it is running on the built-in curve"
# Decision-page metrics must actually compute (not just render empty).
hc_metrics = {m.label: m.value for m in hc_app.metric}
assert "Best programme" in hc_metrics and hc_metrics["Best programme"], \
    "the built-in curve must produce a real programme decision, not a blank"
# The history-dependent VaR page must disclose single-day unavailability,
# not crash in build_scenarios().
hc_page = next(w for w in hc_app.sidebar.radio if w.label == "Page")
hc_page.set_value("4 VaR & stress")
hc_app.run(timeout=60)
assert not hc_app.exception, [e.message for e in hc_app.exception]
assert any("single curve date" in i.value for i in hc_app.info), \
    "VaR page must disclose single-day unavailability on the built-in curve, not crash"
print("PASS hardcoded curve: app produces one-day numbers with no workbook; VaR page discloses single-day mode")
