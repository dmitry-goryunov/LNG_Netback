"""
hardcoded_curve.py -- a built-in single-day forward-curve snapshot so the
app can produce numbers for ONE curve date (2026-07-08) with no
LNG history.xlsx present. A mounted/uploaded workbook always OVERRIDES
this (see app.get_tables()); this is only the no-workbook fallback.

The snapshot is the vendor forward curve as snapped by model.snap() at the
latest master date in the workbook used to generate it -- one row per
time-series table (HH/TTF/JKM/FX/charter) at that table's latest date on
or before the curve date, plus the whole volatilities term structure.
model.strip()/decision.py/spread_option.py read these exactly as they read
a workbook-loaded CurveTables, so the deterministic pages (Decision,
Forward strip, Sensitivities, Hedging leg sizing) reproduce the workbook's
own numbers for this one date to the last decimal. History-dependent pages
(VaR/stress/backtest, which need a ~500-day lookback) cannot run on a
single day and are disclosed as unavailable in this mode.

Regenerate with scripts/regenerate_hardcoded_curve.py when the desired
snapshot date changes. This embeds one day of vendor market data; the full
multi-year workbook stays out of the repo as before.

GENERATED FILE -- edit scripts/regenerate_hardcoded_curve.py, not this.
"""

from __future__ import annotations

import json

import pandas as pd

import data

CURVE_DATE = "2026-07-08"

# One curve date's snapshot, JSON so it stays diffable/regenerable. Prices
# are the vendor forward curve at CURVE_DATE; see module docstring.
_SNAPSHOT_JSON = r"""
{
 "curve_date": "2026-07-08",
 "hh": {
  "date": "2026-07-07",
  "c1": 3.265,
  "c2": 3.171,
  "c3": 3.164,
  "c4": 3.336,
  "c5": 3.912,
  "c6": 4.316,
  "c7": 3.899,
  "c8": 3.096,
  "c9": 2.904,
  "c10": 2.878,
  "c11": 3.003,
  "c12": 3.206,
  "c13": 3.263,
  "c14": 3.234,
  "c15": 3.309,
  "c16": 3.566,
  "c17": 4.213,
  "c18": 4.634,
  "c19": 4.168,
  "c20": 3.379,
  "c21": 3.118,
  "c22": 3.093,
  "c23": 3.225,
  "c24": 3.434,
  "c25": 3.495,
  "c26": 3.474,
  "c27": 3.538,
  "c28": 3.75,
  "c29": 4.349,
  "c30": 4.75,
  "c31": 4.31,
  "c32": 3.41,
  "c33": 3.094,
  "c34": 3.068,
  "c35": 3.178,
  "c36": 3.393,
  "c37": 3.456,
  "c38": 3.441,
  "c39": 3.516,
  "c40": 3.681,
  "c41": 4.266,
  "c42": 4.701,
  "c43": 4.257,
  "c44": 3.432,
  "c45": 3.032,
  "c46": 2.987,
  "c47": 3.105,
  "c48": 3.339,
  "c49": 3.376,
  "c50": 3.348,
  "c51": 3.429,
  "c52": 3.624,
  "c53": 4.154,
  "c54": 4.541,
  "c55": 4.092,
  "c56": 3.367,
  "c57": 2.966,
  "c58": 2.934,
  "c59": 3.062,
  "c60": 3.263,
  "c61": 3.32,
  "c62": 3.303,
  "c63": 3.381,
  "c64": 3.621
 },
 "ttf": {
  "date": "2026-07-08",
  "c1": 46.576,
  "c2": 46.804,
  "c3": 46.765,
  "c4": 46.165,
  "c5": 46.0,
  "c6": 45.628,
  "c7": 44.827,
  "c8": 42.453,
  "c9": 36.122,
  "c10": 33.684,
  "c11": 33.042,
  "c12": 32.832,
  "c13": 32.882,
  "c14": 32.882,
  "c15": 32.444,
  "c16": 32.489,
  "c17": 32.789,
  "c18": 32.481,
  "c19": 31.956,
  "c20": 30.496,
  "c21": 26.946,
  "c22": 25.791,
  "c23": 25.491,
  "c24": 25.412,
  "c25": 25.282,
  "c26": 25.427,
  "c27": 25.777,
  "c28": 26.237,
  "c29": 26.657,
  "c30": 26.973,
  "c31": 27.018,
  "c32": 26.438,
  "c33": 23.683,
  "c34": 23.163,
  "c35": 22.653,
  "c36": 22.253,
  "c37": 22.278,
  "c38": 22.728,
  "c39": 22.958,
  "c40": 23.353,
  "c41": 23.713,
  "c42": 23.803,
  "c43": 23.803,
  "c44": 23.703,
  "c45": 21.943,
  "c46": 21.553,
  "c47": 21.128,
  "c48": 20.198,
  "c49": 20.558,
  "c50": 21.223,
  "c51": 21.928,
  "c52": 22.708,
  "c53": 23.138,
  "c54": 23.198,
  "c55": 22.998,
  "c56": 35.959,
  "c57": 27.318,
  "c58": 23.918,
  "c59": 22.131,
  "c60": 21.938,
  "c61": 22.589,
  "c62": 23.878,
  "c63": 25.388,
  "c64": 26.599
 },
 "jkm": {
  "date": "2026-07-08",
  "c1": 16.175,
  "c2": 17.1,
  "c3": 16.48,
  "c4": 16.21,
  "c5": 16.235,
  "c6": 16.015,
  "c7": 15.555,
  "c8": 14.32,
  "c9": 12.465,
  "c10": 11.68,
  "c11": 11.65,
  "c12": 11.785,
  "c13": 11.865,
  "c14": 11.78,
  "c15": 11.76,
  "c16": 11.715,
  "c17": 11.98,
  "c18": 11.975,
  "c19": 11.76,
  "c20": 11.0,
  "c21": 9.795,
  "c22": 9.385,
  "c23": 9.34,
  "c24": 9.325,
  "c25": 9.265,
  "c26": 9.3,
  "c27": 9.355,
  "c28": 9.475,
  "c29": 9.73,
  "c30": 10.795,
  "c31": 10.6,
  "c32": 9.915,
  "c33": 8.825,
  "c34": 8.455,
  "c35": 8.42,
  "c36": 8.405,
  "c37": 8.35,
  "c38": 8.38,
  "c39": 8.43,
  "c40": 8.54,
  "c41": 8.77,
  "c42": 10.165,
  "c43": 9.985,
  "c44": 9.34
 },
 "fx": {
  "date": "2026-07-08",
  "spot": 1.1417,
  "m6": 1.22912,
  "y1": 1.31595,
  "y2": 1.4807899999999998,
  "y3": 1.6301999999999999,
  "y4": 1.77819,
  "y5": 1.92389,
  "y6": 2.0735799999999998,
  "y7": 2.1906999999999996,
  "y8": 2.3266999999999998,
  "y9": 2.4607,
  "y10": 2.6326799999999997,
  "o6": 1.150442,
  "o1": 1.159125,
  "o_y2": 1.175609,
  "o_y3": 1.19055,
  "o_y4": 1.205349,
  "o_y5": 1.219919,
  "o_y6": 1.234888,
  "o_y7": 1.2466,
  "o_y8": 1.2602,
  "o_y9": 1.2736,
  "o_y10": 1.290798
 },
 "charter": {
  "date": "2026-07-03",
  "rate174": 87500.0
 },
 "vol": [
  {
   "months_forward": 0,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 1,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 2,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 3,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 4,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 5,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 6,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 7,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 8,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 9,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 10,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 11,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 12,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 13,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 14,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 15,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 16,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 17,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 18,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 19,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 20,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 21,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 22,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 23,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 24,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 25,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 26,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 27,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 28,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 29,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 30,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 31,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 32,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 33,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 34,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 35,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 36,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 37,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 38,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 39,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 40,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 41,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 42,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 43,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  },
  {
   "months_forward": 44,
   "vol_TTF": 0.6,
   "vol_HH": 0.6,
   "vol_JKM": 0.6,
   "corr_TTF_HH": 0.5,
   "corr_TTF_JKM": 0.5
  }
 ]
}
"""


def _record_to_frame(rec: dict) -> pd.DataFrame:
    """One snapped row-dict (date ISO string + numeric columns) back into a
    single-row DataFrame with a real Timestamp date, matching the schema
    data.py's loaders produce (column labels are what model.snap() reads)."""
    row = dict(rec)
    row["date"] = pd.Timestamp(row["date"])
    return pd.DataFrame([row])


def build_hardcoded_tables() -> CurveTables:
    """Reconstruct a CurveTables from the embedded snapshot. master_dates is
    exactly [CURVE_DATE]: the date picker then offers only this one day, and
    model.snap(table, CURVE_DATE) returns each single-row table's row (its
    date is <= CURVE_DATE by construction -- e.g. charter is weekly, HH may
    lag a day). us_netbacks/us_transport/vlsfo/eua are None (unused by the
    deterministic pages / absent from the workbook)."""
    snapshot = json.loads(_SNAPSHOT_JSON)

    hh = _record_to_frame(snapshot["hh"])
    ttf = _record_to_frame(snapshot["ttf"])
    jkm = _record_to_frame(snapshot["jkm"])
    fx = _record_to_frame(snapshot["fx"])
    charter = _record_to_frame(snapshot["charter"])

    vol = None
    if snapshot.get("vol") is not None:
        vol = pd.DataFrame(snapshot["vol"]).set_index("months_forward").sort_index()

    curve_date = pd.Timestamp(snapshot["curve_date"])
    # Look up data.CurveTables at CALL time (not a module-level
    # `from data import CurveTables`) and pass ONLY the required + non-default
    # fields. On Streamlit Cloud a stale cached `data` module can lag a
    # deploy; app.py's module-freshness guard reloads `data` in place, and a
    # call-time lookup then picks up the reloaded class. Leaving
    # us_netbacks/us_transport/vlsfo/eua at their None defaults means this
    # never passes a keyword a slightly-older CurveTables lacks (the crash
    # that motivated this: a pre-vlsfo/eua CurveTables rejecting vlsfo=None).
    tables = data.CurveTables(
        hh=hh, ttf=ttf, jkm=jkm, fx=fx, charter=charter,
        vol=vol,
        master_dates=pd.DatetimeIndex([curve_date]),
        source=f"built-in hardcoded curve ({snapshot['curve_date']})",
    )
    tables.warnings = [
        f"Running on the built-in hardcoded forward curve ({snapshot['curve_date']}), "
        "no LNG history.xlsx loaded. Deterministic pages (Decision, Forward strip, "
        "Sensitivities, Hedging) are exact for this one date; VaR/stress/backtest need "
        "the full history and are unavailable. Set LNG_HISTORY_XLSX or upload the "
        "workbook in the sidebar to override."
    ]
    return tables
