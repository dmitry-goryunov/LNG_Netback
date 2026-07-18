"""Regenerate ../hardcoded_curve.py from the current LNG history.xlsx.

The built-in curve is a single-day snapshot the app falls back on when no
workbook is present (see hardcoded_curve.py's docstring and
app.get_tables()). Re-run this whenever you want the built-in curve to
track a newer workbook date:

    LNG_HISTORY_XLSX="/path/to/LNG history.xlsx" python scripts/regenerate_hardcoded_curve.py

It captures, for the workbook's latest master date, the row model.snap()
returns from each time-series table (HH/TTF/JKM/FX/charter) plus the whole
volatilities term structure, and rewrites hardcoded_curve.py with that
snapshot embedded as JSON. The multi-year workbook itself stays out of the
repo as before; only this one day's forward curve is embedded.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import data
import model

OUT_PATH = REPO / "hardcoded_curve.py"


def _resolve_workbook() -> str:
    path = os.environ.get(data.ENV_VAR_NAME) or data.default_data_path()
    if not path or not os.path.isfile(path):
        sys.exit(
            f"No workbook found. Set {data.ENV_VAR_NAME} to LNG history.xlsx and re-run.\n"
            f"(checked ${data.ENV_VAR_NAME} and {data.DEFAULT_CANDIDATE_PATHS})"
        )
    return path


def _row_to_record(df: pd.DataFrame, D: pd.Timestamp) -> dict:
    """model.snap(df, D) (last row <= D) as an ordered {col: value} dict,
    date as an ISO string, NaN -> None, everything else float."""
    r = model.snap(df, D)
    rec = {}
    for col, val in r.items():
        if col == "date":
            rec[col] = pd.Timestamp(val).strftime("%Y-%m-%d")
        elif pd.isna(val):
            rec[col] = None
        else:
            rec[col] = float(val)
    return rec


def build_snapshot(tables) -> dict:
    D = pd.Timestamp(tables.master_dates.max())
    snapshot = {
        "curve_date": D.strftime("%Y-%m-%d"),
        "hh": _row_to_record(tables.hh, D),
        "ttf": _row_to_record(tables.ttf, D),
        "jkm": _row_to_record(tables.jkm, D),
        "fx": _row_to_record(tables.fx, D),
        "charter": _row_to_record(tables.charter, D),
    }
    if tables.vol is not None:
        vol = tables.vol.reset_index()
        records = []
        for _, r in vol.iterrows():
            rec = {}
            for col, val in r.items():
                if pd.isna(val):
                    rec[col] = None
                elif col == "months_forward":
                    rec[col] = int(val)
                else:
                    rec[col] = float(val)
            records.append(rec)
        snapshot["vol"] = records
    else:
        snapshot["vol"] = None
    return snapshot


# Filled by sentinel-token replacement (not str.format) so the literal
# f-string braces in the generated module body need no escaping.
_MODULE_TEMPLATE = '''"""
hardcoded_curve.py -- a built-in single-day forward-curve snapshot so the
app can produce numbers for ONE curve date (__CURVE_DATE__) with no
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

from data import CurveTables

CURVE_DATE = "__CURVE_DATE__"

# One curve date's snapshot, JSON so it stays diffable/regenerable. Prices
# are the vendor forward curve at CURVE_DATE; see module docstring.
_SNAPSHOT_JSON = r"""
__EMBEDDED_JSON__
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
    tables = CurveTables(
        hh=hh, ttf=ttf, jkm=jkm, fx=fx, charter=charter,
        us_netbacks=None, us_transport=None, vol=vol,
        vlsfo=None, eua=None,
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
'''


def main() -> None:
    path = _resolve_workbook()
    tables = data.load_all(path)
    snapshot = build_snapshot(tables)
    embedded = json.dumps(snapshot, indent=1)
    module = (_MODULE_TEMPLATE
              .replace("__CURVE_DATE__", snapshot["curve_date"])
              .replace("__EMBEDDED_JSON__", embedded))
    OUT_PATH.write_text(module, encoding="utf-8", newline="\n")
    print(f"wrote {OUT_PATH} for curve date {snapshot['curve_date']} ({len(module)} chars)")


if __name__ == "__main__":
    main()
