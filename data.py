"""
data.py -- Section 1 loader and validation for LNG history.xlsx.

Reads the HH / TTF / JKM / FX new / charter (/ US netbacks / US transport)
sheets, applies the FX x10 correction, and produces the master curve-date
list used by the date picker.

Only `load_all_cached` touches Streamlit (a thin `st.cache_data` wrapper
around the pure `load_all`), so this module can be imported and exercised
head-less in tests.
"""

from __future__ import annotations

import datetime as dt
import os
import warnings
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Environment / default path configuration (spec Section 1.7)
# ---------------------------------------------------------------------------

ENV_VAR_NAME = "LNG_HISTORY_XLSX"

DEFAULT_CANDIDATE_PATHS = [
    os.environ.get(ENV_VAR_NAME, ""),
    "LNG history.xlsx",
    os.path.join("data", "LNG history.xlsx"),
]


def default_data_path() -> Optional[str]:
    """Best-effort discovery of the workbook without a file_uploader.

    Checks the LNG_HISTORY_XLSX env var first, then a couple of
    conventional relative locations. Returns None if nothing is found,
    in which case the caller (app.py) should fall back to
    st.file_uploader per spec Section 1.7.
    """
    for p in DEFAULT_CANDIDATE_PATHS:
        if p and os.path.isfile(p):
            return p
    return None


# ---------------------------------------------------------------------------
# Date parsing (spec 1.1: datetime, Excel serial, or dd-MMM-yyyy strings)
# ---------------------------------------------------------------------------

_EXCEL_EPOCH = pd.Timestamp("1899-12-30")


def parse_date_cell(x):
    """Coerce one raw cell value into a Timestamp, or NaT if unparsable.

    Handles the three formats named in the spec, plus the one real-world
    wrinkle found in the source file: a lone FX-sheet row whose date cell
    is a bare Excel serial number (int) instead of a formatted date --
    naive pd.to_datetime() on that value silently produces a bogus
    1970-01-01 timestamp (treats the int as nanoseconds), so serial
    numbers are handled explicitly here rather than via pd.to_datetime.
    """
    if isinstance(x, (pd.Timestamp, dt.datetime, dt.date)):
        return pd.Timestamp(x)
    if isinstance(x, (int, float)) and not pd.isna(x):
        return _EXCEL_EPOCH + pd.Timedelta(days=float(x))
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return pd.NaT
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return pd.Timestamp(dt.datetime.strptime(s, fmt))
            except ValueError:
                pass
        return pd.to_datetime(s, errors="coerce")
    return pd.NaT


def _parse_date_column(series: pd.Series) -> pd.Series:
    return series.map(parse_date_cell)


# ---------------------------------------------------------------------------
# Sheet-specific loaders
# ---------------------------------------------------------------------------

_STRIP_SHEETS = {
    # sheet name -> (prefix used for c1..cN column names, number of columns to keep)
    "HH": "c",
    "TTF": "c",
    "JKM": "c",
}


def _load_strip_sheet(path_or_buffer, sheet: str, max_cols: int = 64) -> pd.DataFrame:
    """HH / TTF / JKM layout: row1=index numbers, row2=headers, row3=blank,
    row4+=data. Column 0 is the date; columns 1.. are the c1..cN strip."""
    raw = pd.read_excel(path_or_buffer, sheet_name=sheet, header=None, skiprows=3)
    raw = raw.rename(columns={raw.columns[0]: "date"})
    raw["date"] = _parse_date_column(raw["date"])
    raw = raw.dropna(subset=["date"])

    value_cols = list(raw.columns[1:])
    for c in value_cols:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")

    raw = raw.sort_values("date").drop_duplicates(subset="date", keep="last").reset_index(drop=True)

    keep = value_cols[:max_cols]
    out = raw[["date"] + keep].copy()
    out.columns = ["date"] + [f"c{i}" for i in range(1, len(keep) + 1)]
    return out


def load_hh(path_or_buffer) -> pd.DataFrame:
    return _load_strip_sheet(path_or_buffer, "HH")


def load_ttf(path_or_buffer) -> pd.DataFrame:
    return _load_strip_sheet(path_or_buffer, "TTF")


def load_jkm(path_or_buffer) -> pd.DataFrame:
    return _load_strip_sheet(path_or_buffer, "JKM")


_FX_YEAR_TENOR_COLS = {4: "y2", 5: "y3", 6: "y4", 7: "y5", 8: "y6", 9: "y7", 10: "y8", 11: "y9", 12: "y10"}


def load_fx(path_or_buffer) -> pd.DataFrame:
    """FX new layout: col A=date, B=spot (EUR=), C=6M, D=1Y, E..M=2Y..10Y.
    Data DESCENDING; re-sorted ascending here. Applies the x10 correction
    to every tenor, not just 6M/1Y: true outright = spot + (stored -
    spot) / 10. Verified across the full 2002-2026 history before adding
    the 2Y-10Y columns here -- the raw (uncorrected) 10Y column ranges
    0.78x-2.73x spot, which is not a plausible EUR/USD forward under any
    realistic scenario; corrected, every sampled date produces a smooth,
    monotonic curve within a few percent of spot, consistent with a real
    term structure. o6/o1 are unchanged from before (same columns, same
    formula) so every existing caller sees byte-identical values; o_y2..
    o_y10 are new."""
    raw = pd.read_excel(path_or_buffer, sheet_name="FX new", header=None, skiprows=3)
    raw = raw.rename(columns={0: "date", 1: "spot", 2: "m6", 3: "y1", **_FX_YEAR_TENOR_COLS})
    raw["date"] = _parse_date_column(raw["date"])
    raw = raw.dropna(subset=["date"])
    value_cols = ["spot", "m6", "y1"] + list(_FX_YEAR_TENOR_COLS.values())
    for c in value_cols:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw = raw[["date"] + value_cols]
    raw = raw.sort_values("date").drop_duplicates(subset="date", keep="last").reset_index(drop=True)

    raw["o6"] = raw["spot"] + (raw["m6"] - raw["spot"]) / 10.0
    raw["o1"] = raw["spot"] + (raw["y1"] - raw["spot"]) / 10.0
    for c in _FX_YEAR_TENOR_COLS.values():
        raw[f"o_{c}"] = raw["spot"] + (raw[c] - raw["spot"]) / 10.0
    return raw


def load_charter(path_or_buffer) -> pd.DataFrame:
    """charter layout: cols A/B = 160k TDFE atlantic (date, rate); cols
    D/E = 2-stroke 174k atlantic (date, rate) -- this is the series the
    spec wants ("col D/E = 174k m3 2-stroke"). Weekly, descending."""
    raw = pd.read_excel(path_or_buffer, sheet_name="charter", header=None, skiprows=3)
    out = raw.rename(columns={3: "date", 4: "rate174"})[["date", "rate174"]].copy()
    out["date"] = _parse_date_column(out["date"])
    out = out.dropna(subset=["date"])
    out["rate174"] = pd.to_numeric(out["rate174"], errors="coerce")
    out = out.sort_values("date").drop_duplicates(subset="date", keep="last").reset_index(drop=True)
    out = out.dropna(subset=["rate174"])
    return out


def load_us_netbacks(path_or_buffer) -> pd.DataFrame:
    """Sanity-reference sheet only; not used by model.py. Header row1,
    data row2+."""
    raw = pd.read_excel(path_or_buffer, sheet_name="US netbacks", header=0)
    raw = raw.rename(columns={raw.columns[0]: "date"})
    raw["date"] = _parse_date_column(raw["date"])
    raw = raw.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return raw


def load_us_transport(path_or_buffer) -> pd.DataFrame:
    """Sanity-reference sheet only; not used by model.py. Title row1,
    header row2, data row3+, footer 'Last updated:' row dropped."""
    raw = pd.read_excel(path_or_buffer, sheet_name="US transport", header=1)
    raw = raw.rename(columns={raw.columns[0]: "date"})
    raw["date"] = _parse_date_column(raw["date"])
    raw = raw.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return raw


# ---------------------------------------------------------------------------
# Master date list (spec 1.5)
# ---------------------------------------------------------------------------

def _first_complete_date(df: pd.DataFrame, cols: list[str]) -> pd.Timestamp:
    mask = df[cols].notna().all(axis=1)
    if not mask.any():
        raise ValueError("no complete rows found")
    return df.loc[mask, "date"].min()


def compute_master_dates(hh: pd.DataFrame, ttf: pd.DataFrame, jkm: pd.DataFrame,
                          fx: pd.DataFrame, charter: pd.DataFrame) -> pd.DatetimeIndex:
    """TTF dates >= the first complete date of every other table (HH/TTF/JKM
    need c1..c13 numeric; FX needs spot/o6/o1; charter needs rate174)."""
    hh_cols = [c for c in hh.columns if c.startswith("c")][:13]
    ttf_cols = [c for c in ttf.columns if c.startswith("c")][:13]
    jkm_cols = [c for c in jkm.columns if c.startswith("c")][:13]

    lower_bound = max(
        _first_complete_date(hh, hh_cols),
        _first_complete_date(ttf, ttf_cols),
        _first_complete_date(jkm, jkm_cols),
        _first_complete_date(fx, ["spot", "o6", "o1"]),
        _first_complete_date(charter, ["rate174"]),
    )

    ttf_complete = ttf[ttf[ttf_cols].notna().all(axis=1)]
    master = ttf_complete.loc[ttf_complete["date"] >= lower_bound, "date"]
    return pd.DatetimeIndex(sorted(master))


# ---------------------------------------------------------------------------
# Validation (spec 1.6)
# ---------------------------------------------------------------------------

def _assert_monotone(df: pd.DataFrame, name: str) -> None:
    dates = df["date"].to_numpy()
    assert np.all(dates[1:] >= dates[:-1]), f"{name}: dates are not monotone ascending"


def _business_days_ago(from_date: dt.date, n: int) -> dt.date:
    d = from_date
    remaining = n
    while remaining > 0:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            remaining -= 1
    return d


def validate(tables: "CurveTables", today: Optional[dt.date] = None) -> list[str]:
    """Hard invariants via assert; soft issues collected as warning strings."""
    _assert_monotone(tables.hh, "HH")
    _assert_monotone(tables.ttf, "TTF")
    _assert_monotone(tables.jkm, "JKM")
    _assert_monotone(tables.fx, "FX new")
    _assert_monotone(tables.charter, "charter")

    fx = tables.fx
    complete = fx.dropna(subset=["spot", "o6", "o1"])
    dev6 = (complete["o6"] - complete["spot"]) / complete["spot"]
    dev1 = (complete["o1"] - complete["spot"]) / complete["spot"]
    assert (dev6.abs() <= 0.05).all(), "FX o6 deviates from spot by more than 5% after x10 correction"
    assert (dev1.abs() <= 0.05).all(), "FX o1 deviates from spot by more than 5% after x10 correction"

    # 2Y-10Y tenors: bound grows with tenor (0.05 + 0.03/year), based on the
    # observed 2002-2026 range (max ~7.2% at 2Y, ~28.5% at 10Y) with headroom.
    # A hit here means either the workbook's encoding convention changed or a
    # genuinely bad row -- fail loudly rather than feed an implausible
    # forward point into a 13-36 month cargo price.
    for years, col in ((2, "o_y2"), (3, "o_y3"), (4, "o_y4"), (5, "o_y5"),
                       (6, "o_y6"), (7, "o_y7"), (8, "o_y8"), (9, "o_y9"), (10, "o_y10")):
        if col not in fx.columns:
            continue
        comp = fx.dropna(subset=["spot", col])
        dev = (comp[col] - comp["spot"]) / comp["spot"]
        tol = 0.05 + 0.03 * years
        assert (dev.abs() <= tol).all(), f"FX {col} deviates from spot by more than {tol:.0%} after x10 correction"

    warnings_out: list[str] = []
    today = today or dt.date.today()
    latest = tables.master_dates.max().date()
    stale_cutoff = _business_days_ago(today, 5)
    if latest < stale_cutoff:
        warnings_out.append(
            f"Workbook latest master date {latest} is older than today-5busdays ({stale_cutoff}); "
            "re-save/refresh LNG history.xlsx."
        )
    return warnings_out


# ---------------------------------------------------------------------------
# Bundle + top-level loader
# ---------------------------------------------------------------------------

@dataclass
class CurveTables:
    hh: pd.DataFrame
    ttf: pd.DataFrame
    jkm: pd.DataFrame
    fx: pd.DataFrame
    charter: pd.DataFrame
    us_netbacks: Optional[pd.DataFrame] = None
    us_transport: Optional[pd.DataFrame] = None
    master_dates: pd.DatetimeIndex = field(default_factory=lambda: pd.DatetimeIndex([]))
    warnings: list[str] = field(default_factory=list)
    source: str = ""


def load_all(path_or_buffer, source_label: str = "") -> CurveTables:
    """Pure loader: parse every sheet, correct FX, build the master date
    list, validate. No Streamlit dependency -- safe to call from tests."""
    hh = load_hh(path_or_buffer)
    ttf = load_ttf(path_or_buffer)
    jkm = load_jkm(path_or_buffer)
    fx = load_fx(path_or_buffer)
    charter = load_charter(path_or_buffer)

    try:
        us_netbacks = load_us_netbacks(path_or_buffer)
    except Exception:
        us_netbacks = None
    try:
        us_transport = load_us_transport(path_or_buffer)
    except Exception:
        us_transport = None

    master_dates = compute_master_dates(hh, ttf, jkm, fx, charter)

    tables = CurveTables(
        hh=hh, ttf=ttf, jkm=jkm, fx=fx, charter=charter,
        us_netbacks=us_netbacks, us_transport=us_transport,
        master_dates=master_dates,
        source=str(source_label),
    )
    tables.warnings = validate(tables)
    return tables


def load_all_cached(path: str):
    """Streamlit-cached wrapper, keyed on path + mtime per spec 1.7.
    Import of streamlit is local so plain `import data` in tests never
    requires a running Streamlit context."""
    import streamlit as st

    @st.cache_data(show_spinner="Loading LNG history.xlsx ...")
    def _cached(path: str, mtime: float) -> CurveTables:
        return load_all(path, source_label=path)

    mtime = os.path.getmtime(path)
    return _cached(path, mtime)


def load_all_from_upload(uploaded_file):
    """For the st.file_uploader fallback path (spec 1.7); not mtime-cached
    since Streamlit's UploadedFile is already in-memory per session."""
    return load_all(uploaded_file, source_label=getattr(uploaded_file, "name", "uploaded file"))
