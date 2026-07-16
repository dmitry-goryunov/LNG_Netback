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

app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
assert not app.exception, [e.message for e in app.exception]
assert [title.value for title in app.title] == ["LNG cargo and vessel decision"]
metrics = {metric.label: metric.value for metric in app.metric}
# Operating case since 16-Jul-2026: 17 kn / 1.5 d loading / 1.5 d
# unloading (model.operating_default_params). At that geometry a single
# 50.6-day Asia round trip beats one 27.0-day Europe run inside the
# 52-day default horizon (two Europe runs, 54.0 d, no longer fit) --
# previously 'Europe -> Europe' / 51.8803 used days at the 19.5-kn
# legacy spec defaults.
assert metrics["Best programme"] == "Asia"
assert metrics["Used vessel-days"] == "50.5882"
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
