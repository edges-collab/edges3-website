"""
Read-only API over the EDGES catalog and pipeline products
==========================================================

These endpoints never touch the raw data tree: they read the precomputed
quick-look (QL) and L1 QA products and the catalog through the
``edges-pipeline`` and ``edges-catalog`` packages. Both packages are
optional (they are not published yet); without them every endpoint
returns 503 with an install hint, and the rest of the site keeps working.

Endpoints
---------
GET /api/status            Whether the products are available, and their coverage
GET /api/nights/latest     UTC bounds of the night containing the latest QL data
GET /api/night             Everything the "Last night" page needs, in one request
GET /api/quicklook         QL waterfalls for a time range
GET /api/l1                Per-file L1 QA metrics for a time range
GET /api/housekeeping      Temperature-log readings for a time range

Conventions
-----------
Times are POSIX seconds (UTC). ``start``/``end`` query parameters take
POSIX seconds or ISO strings (a naive string is UTC). A night is
18:00-06:00 site time (AWST = UTC+8 at the MRO) and is named by the
local date of its evening. Waterfalls are sent as base64 little-endian
float32 (``{"dtype", "shape", "data"}``) so NaN survives and payloads stay
small. Time gaps longer than a few cycles are made explicit (NaN rows /
null values) so plots show them instead of interpolating across them.

Where the products live is set by ``EDGES_PIPELINE_ROOT`` (see
``edges_pipeline.config.Settings``); the catalog is the one next to the
products (``$EDGES_PIPELINE_ROOT/catalog.sqlite``).
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import sqlite3
import threading
import time
import warnings
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from fastapi import APIRouter, HTTPException, Query

try:
    from edges_catalog import Catalog
    from edges_pipeline.products import SITE_UTC_OFFSET_HOURS, Products

    IMPORT_ERROR: Optional[str] = None
except ImportError as _e:  # the data packages are optional
    Catalog = Products = None  # type: ignore[assignment,misc]
    SITE_UTC_OFFSET_HOURS = {"edges3-mro": 8.0}
    IMPORT_ERROR = str(_e)


log = logging.getLogger("edges.products_api")

router = APIRouter(prefix="/api", tags=["products"])

DEPLOYMENT = "edges3-mro"
SITE_TZ_NAME = "AWST"

#: Housekeeping quantities shown on the night page (catalog names), with labels.
#: Code 152 (``pr59_current``) and 0 (``setpoint``) are deliberately left out.
HOUSEKEEPING_NAMES: Dict[str, str] = {
    "hot_load_temperature": "Hot load",
    "amb_load_temperature": "Ambient load",
    "front_end_temperature": "Front end",
    "inner_box_temperature": "Inner box",
    "battery_voltage": "Battery",
}

#: Per-file QA thresholds for the badges (provisional; tune with the team).
QA_THRESHOLDS: Dict[str, float] = {
    # |ADC max/min| at or above this is a full-scale hit (full scale is 0.5).
    "adc_full_scale": 0.499,
    # A file with fewer cycles than this fraction of the night's longest file.
    "short_fraction": 0.8,
    # Intermittent-RFI flag fraction (L1 ``rfi_occupancy``) above this.
    "rfi_occupancy": 0.01,
    # Cycles whose band-median Q deviates > 5 robust sigma (L1).
    "outlier_cycles": 10,
}

#: A gap between consecutive samples longer than this many median steps is
#: shown as a gap (files are ~1 min apart, cycles ~23 s: not gaps).
GAP_FACTOR = 5.0
#: Housekeeping is logged every ~5 min; a longer silence is a gap.
HOUSEKEEPING_GAP_S = 20 * 60

MAX_SPAN_DAYS = 8
MAX_SPAN_DAYS_P0 = 2  # the p0 waterfall doubles the payload
MAX_ROWS_LIMIT = 5000
DEFAULT_MAX_ROWS = 2000

CACHE_TTL_S = 15 * 60
CACHE_MAX_ENTRIES = 8
HEAVY_WAIT_S = 30


# ---------------------------------------------------------------------------
# Products / catalog access
# ---------------------------------------------------------------------------
_products: Any = None
_products_lock = threading.Lock()
# Bound concurrent heavy requests (each holds a night of arrays in memory).
_heavy = threading.BoundedSemaphore(2)


def configure(settings: Any = None) -> None:
    """(Re)initialise the products reader, e.g. with test settings."""
    global _products
    with _products_lock:
        _products = Products(settings) if Products and settings else None
    _cache.clear()


def get_products() -> Any:
    """Return the shared (thread-safe) ``Products`` reader, or raise 503."""
    global _products
    if IMPORT_ERROR is not None:
        raise HTTPException(
            status_code=503,
            detail=(
                "The edges-pipeline/edges-catalog packages are not installed "
                f"({IMPORT_ERROR}). See README: 'Catalog and pipeline products'."
            ),
        )
    with _products_lock:
        if _products is None:
            _products = Products()
        return _products


@contextlib.contextmanager
def _db_errors():
    """Turn database errors (missing/unreadable SQLite files) into 503s."""
    try:
        yield
    except (sqlite3.Error, OSError) as e:
        log.warning("products/catalog database unavailable: %s", e)
        raise HTTPException(
            status_code=503, detail=f"catalog/products database unavailable: {e}"
        ) from None


@contextlib.contextmanager
def _heavy_slot():
    """Bound concurrent heavy requests; give up (503) rather than queue forever."""
    if not _heavy.acquire(timeout=HEAVY_WAIT_S):
        raise HTTPException(status_code=503, detail="server busy; try again")
    try:
        yield
    finally:
        _heavy.release()


def _open_catalog(prod: Any) -> Any:
    """Open a catalog connection for one request (``Catalog`` is not thread-safe)."""
    return Catalog(prod.settings.catalog_db)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _site_tz() -> timezone:
    return timezone(timedelta(hours=SITE_UTC_OFFSET_HOURS[DEPLOYMENT]))


def _parse_time(value: Optional[str], name: str) -> Optional[float]:
    """POSIX seconds or an ISO string (naive = UTC) -> POSIX seconds."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"{name} must be POSIX seconds or ISO time"
        ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _require_range(start: Optional[str], end: Optional[str]) -> Tuple[float, float]:
    t0, t1 = _parse_time(start, "start"), _parse_time(end, "end")
    if t0 is None or t1 is None:
        raise HTTPException(status_code=400, detail="start and end are required")
    if not t1 > t0:
        raise HTTPException(status_code=400, detail="end must be after start")
    if t1 - t0 > MAX_SPAN_DAYS * 86400:
        raise HTTPException(
            status_code=400, detail=f"range is limited to {MAX_SPAN_DAYS} days"
        )
    return t0, t1


def _float_list(x: Any, digits: Optional[int] = None) -> List[Optional[float]]:
    """Array -> JSON-safe list (NaN/inf -> None), optionally rounded."""
    a = np.asarray(x, dtype=float)
    if digits is not None:
        a = np.round(a, digits)
    return [float(v) if np.isfinite(v) else None for v in a.tolist()]


def _scalar(v: Any) -> Any:
    """numpy/pandas scalar -> JSON-safe Python value."""
    if v is None:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return float(v) if np.isfinite(v) else None
    try:
        import pandas as pd

        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _encode_f32(a: np.ndarray) -> Dict[str, Any]:
    a = np.ascontiguousarray(a, dtype="<f4")
    return {
        "dtype": "float32",
        "shape": list(a.shape),
        "data": base64.b64encode(a.tobytes()).decode("ascii"),
    }


def _gap_positions(t: np.ndarray, max_gap: float) -> np.ndarray:
    """Indices ``i`` where ``t[i+1] - t[i] > max_gap``."""
    if len(t) < 2:
        return np.array([], dtype=int)
    return np.nonzero(np.diff(t) > max_gap)[0]


def insert_gaps(
    t: np.ndarray, columns: Dict[str, np.ndarray], factor: float = GAP_FACTOR
) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """Insert NaN rows at gaps longer than ``factor`` median steps.

    Two NaN rows go into each gap, one step after its start and one step
    before its end, so heatmap cells next to a gap keep their normal width
    and line plots break instead of joining across it.
    """
    t = np.asarray(t, dtype=float)
    if len(t) < 3:
        return t, columns
    step = float(np.median(np.diff(t)))
    if not step > 0:
        return t, columns
    gaps = _gap_positions(t, factor * step)
    if len(gaps) == 0:
        return t, columns
    at = np.repeat(gaps + 1, 2)
    t_new = np.insert(t, at, np.ravel(np.column_stack([t[gaps] + step, t[gaps + 1] - step])))
    out = {}
    for k, v in columns.items():
        v = np.asarray(v, dtype=float)
        out[k] = np.insert(v, at, np.nan, axis=0)
    return t_new, out


def _night_bounds(day: date) -> Tuple[float, float]:
    return Products.night(day, deployment=DEPLOYMENT)


def _night_date(start_unix: float) -> str:
    return datetime.fromtimestamp(start_unix, _site_tz()).date().isoformat()


def _night_info(start: float, end: float, latest: Optional[Tuple[float, float]]) -> Dict[str, Any]:
    return {
        "date": _night_date(start),
        "start_unix": start,
        "end_unix": end,
        "deployment": DEPLOYMENT,
        "utc_offset_hours": SITE_UTC_OFFSET_HOURS[DEPLOYMENT],
        "timezone": SITE_TZ_NAME,
        "latest_date": _night_date(latest[0]) if latest else None,
        "is_latest": bool(latest and latest[0] == start),
    }


def _latest_night(prod: Any) -> Optional[Tuple[float, float]]:
    """The latest night that has QL data.

    ``Products.latest_night`` returns the night *containing* the latest data,
    and puts any instant after local noon into the coming evening's night, so
    daytime data (the instrument records all day) would select tonight's
    still-empty night. Step back a night in that case.
    """
    try:
        start, end = prod.latest_night(deployment=DEPLOYMENT)
    except LookupError:
        return None
    if prod.ql_files(start, end, load="ant", deployment=DEPLOYMENT).empty:
        start, end = start - 86400, end - 86400
    return start, end


# ---------------------------------------------------------------------------
# Response cache (the products change at most twice a day)
# ---------------------------------------------------------------------------
class _Cache:
    def __init__(self) -> None:
        self._d: Dict[Any, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            hit = self._d.get(key)
            if hit is None or hit[0] < time.monotonic():
                self._d.pop(key, None)
                return None
            return hit[1]

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            if len(self._d) >= CACHE_MAX_ENTRIES:
                self._d.pop(min(self._d, key=lambda k: self._d[k][0]))
            self._d[key] = (time.monotonic() + CACHE_TTL_S, value)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


_cache = _Cache()


def _data_version(prod: Any) -> Tuple:
    """Cheap fingerprint of the databases (changes whenever they are written)."""
    out = []
    for db in (prod.settings.products_db, prod.settings.catalog_db):
        for suffix in ("", "-wal"):
            try:
                st = os.stat(f"{db}{suffix}")
                out.append((st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
    return tuple(out)


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------
def _nanmean_groups(x: np.ndarray, n: int) -> np.ndarray:
    """Mean over consecutive groups of ``n`` rows (NaN-aware; last group partial)."""
    m = -(-len(x) // n)
    pad = m * n - len(x)
    if pad:
        x = np.concatenate([x, np.full((pad, *x.shape[1:]), np.nan, x.dtype)])
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(x.reshape(m, n, *x.shape[1:]), axis=1)


def decimate_segments(
    t: np.ndarray, columns: Dict[str, np.ndarray], n: int, factor: float = GAP_FACTOR
) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """Average groups of ``n`` rows *within* gap-free segments.

    Groups never straddle a gap (which would put averaged rows inside it).
    ``lst_hour`` is averaged on the circle, so the 24 -> 0 wrap is handled.
    """
    t = np.asarray(t, dtype=float)
    if n <= 1 or len(t) == 0:
        return t, columns
    step = float(np.median(np.diff(t))) if len(t) > 1 else 0.0
    cuts = _gap_positions(t, factor * step) + 1 if step > 0 else np.array([], int)
    bounds = [0, *cuts.tolist(), len(t)]
    t_out, out = [], {k: [] for k in columns}
    for a, b in zip(bounds[:-1], bounds[1:]):
        t_out.append(_nanmean_groups(t[a:b], n))
        for k, v in columns.items():
            v = np.asarray(v, dtype=float)[a:b]
            if k == "lst_hour":
                ang = v * (2 * np.pi / 24)
                c, s = _nanmean_groups(np.cos(ang), n), _nanmean_groups(np.sin(ang), n)
                out[k].append((np.arctan2(s, c) * 24 / (2 * np.pi)) % 24)
            else:
                out[k].append(_nanmean_groups(v, n))
    return np.concatenate(t_out), {k: np.concatenate(v) for k, v in out.items()}


def _quicklook(prod: Any, t0: float, t1: float, load: str, p0: bool, max_rows: int) -> Dict[str, Any]:
    quantities = ("waterfall_q", "waterfall_p0") if p0 else ("waterfall_q",)
    try:
        # Decimate here, per gap-free segment, not in Products.quicklook().
        ql = prod.quicklook(
            t0, t1, load=load, quantities=quantities, deployment=DEPLOYMENT
        )
    except LookupError as e:
        return {"available": False, "reason": str(e), "n_rows": 0, "n_cycles": 0}
    cov = ql["coverage"]
    n = len(ql["time_unix"])
    reason = None
    if n == 0:
        processed = (
            cov["t_first_unix"] is not None
            and cov["t_first_unix"] <= t1 and cov["t_last_unix"] >= t0
        )
        reason = "no data in this range" if processed else "not processed (outside QL coverage)"
    dec = -(-n // max_rows) if n > max_rows else 1
    t, cols = decimate_segments(
        ql["time_unix"], {"lst_hour": ql["lst_hour"], **{q: ql[q] for q in quantities}}, dec
    )
    n_rows = len(t)
    t, cols = insert_gaps(t, cols)
    return {
        "available": n > 0,
        "reason": reason,
        "n_cycles": n,
        "n_rows": n_rows,
        "time_unix": _float_list(t, 1),
        "lst_hour": _float_list(cols["lst_hour"], 5),
        "freq_mhz": _float_list(ql["freq_mhz"], 4),
        "freq_edges_mhz": _float_list(ql["freq_edges_mhz"], 4),
        "waterfall_q": _encode_f32(cols["waterfall_q"]) if n else None,
        "waterfall_p0": _encode_f32(cols["waterfall_p0"]) if n and p0 else None,
        "decimation": dec,
        "files": [os.path.basename(p) for p in ql["files"]],
        "missing_files": [os.path.basename(p) for p in ql["missing_files"]],
        "coverage": {k: _scalar(v) for k, v in cov.items()},
    }


def _cycle_unix(cycle_time: np.ndarray) -> np.ndarray:
    """L1 ``cycle_time`` strings (``YYYY:DDD:HH:MM:SS``, UTC) -> POSIX seconds."""
    return np.array(
        [
            datetime.strptime(s, "%Y:%j:%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            for s in cycle_time
        ],
        dtype=float,
    )


def _band_series(l1: Any, t0: float, t1: float) -> Tuple[Dict[str, Any], List[str]]:
    """Per-cycle band power and band-median Q from the night's L1 products."""
    parts, missing, bands = [], [], None
    for path in l1.path:
        try:
            arrays, attrs = Products.load(
                path, ("cycle_time", "band_power", "band_median_q")
            )
        except OSError as e:
            log.warning("cannot read L1 product %s: %s", path, e)
            missing.append(os.path.basename(path))
            continue
        if bands is None:
            bands = (attrs.get("config") or {}).get("bands")
        t = _cycle_unix(arrays["cycle_time"])
        keep = (t >= t0) & (t < t1)
        parts.append((t[keep], arrays["band_power"][keep], arrays["band_median_q"][keep]))
    if parts:
        t = np.concatenate([p[0] for p in parts])
        power = np.concatenate([p[1] for p in parts])
        q = np.concatenate([p[2] for p in parts])
        order = np.argsort(t, kind="stable")
        t, power, q = t[order], power[order], q[order]
    else:
        t, power, q = np.array([]), np.empty((0, 3)), np.array([])
    t, cols = insert_gaps(t, {"q": q, "p0": power[:, 0], "p1": power[:, 1], "p2": power[:, 2]})
    bands = bands or {}
    return {
        "time_unix": _float_list(t, 1),
        "q_median": _float_list(cols["q"], 5),
        "p0": [_scalar(v) for v in cols["p0"]],
        "p1": [_scalar(v) for v in cols["p1"]],
        "p2": [_scalar(v) for v in cols["p2"]],
        "q_band_mhz": bands.get("q", [60.0, 90.0]),
        "power_band_mhz": bands.get("power", [50.0, 100.0]),
    }, missing


def _housekeeping(cat: Any, t0: float, t1: float, names: List[str]) -> Dict[str, Any]:
    hk = cat.housekeeping(start=t0, end=t1, names=names, deployment=DEPLOYMENT)
    series = []
    for name in names:
        rows = hk[hk.name == name]
        t = rows.t_unix.to_numpy(dtype=float)
        v = rows.value.to_numpy(dtype=float)
        gaps = _gap_positions(t, HOUSEKEEPING_GAP_S)
        if len(gaps):
            t = np.insert(t, gaps + 1, (t[gaps] + t[gaps + 1]) / 2)
            v = np.insert(v, gaps + 1, np.nan)
        unit = rows.unit.iloc[0] if len(rows) else None
        series.append({
            "name": name,
            "label": HOUSEKEEPING_NAMES.get(name, name),
            "code": _scalar(rows.code.iloc[0]) if len(rows) else None,
            "unit": "°C" if unit == "degC" else _scalar(unit),
            "t_unix": _float_list(t, 0),
            "value": _float_list(v, 3),
        })
    return {"series": series, "n_readings": int(len(hk)), "gap_s": HOUSEKEEPING_GAP_S}


def _files_qa(prod: Any, cat: Any, l1: Any, t0: float, t1: float) -> Tuple[List[Dict[str, Any]], Dict[str, List[float]]]:
    """Per-file QA rows (catalog spectra + L1 metrics) and ADC/drop event times."""
    spectra = cat.spectra(load="ant", start=t0, end=t1, deployment=DEPLOYMENT)
    ids = [int(i) for i in spectra.file_id]
    cycles = cat.acq_cycles(ids) if ids else None
    failed = set()
    if ids:
        marks = ",".join("?" * len(ids))
        failed = set(
            prod.sql(
                "SELECT DISTINCT catalog_file_id FROM task WHERE status = 'failed'"
                f" AND catalog_file_id IN ({marks})",
                tuple(ids),
            ).catalog_file_id.astype(int)
        )
    l1_by_id = {int(r.catalog_file_id): r for r in l1.itertuples()} if len(l1) else {}
    try:
        ql_ids = set(prod.ql_files(t0, t1, load="ant", deployment=DEPLOYMENT).catalog_file_id)
    except LookupError:
        ql_ids = set()
    fs = QA_THRESHOLDS["adc_full_scale"]
    events: Dict[str, List[float]] = {"adc_clip_unix": [], "data_drop_unix": []}
    clip_counts: Dict[int, int] = {}
    if cycles is not None and len(cycles):
        adcmax = cycles[["adcmax0", "adcmax1", "adcmax2"]].to_numpy(dtype=float)
        adcmin = cycles[["adcmin0", "adcmin1", "adcmin2"]].to_numpy(dtype=float)
        drops = cycles[["drops0", "drops1", "drops2"]].to_numpy(dtype=float)
        clip = (np.nanmax(np.abs(np.stack([adcmax, adcmin])), axis=(0, 2)) >= fs)
        dropped = np.nansum(drops, axis=1) > 0
        tc = cycles.t_unix.to_numpy(dtype=float)
        inside = (tc >= t0) & (tc < t1)
        events["adc_clip_unix"] = _float_list(tc[clip & inside], 0)
        events["data_drop_unix"] = _float_list(tc[dropped & inside], 0)
        clip_counts = cycles[clip & inside].groupby("file_id").size().to_dict()

    longest = max([int(n) for n in spectra.n_cycles.fillna(0)] or [0])
    files = []
    for r in spectra.itertuples():
        fid = int(r.file_id)
        name = os.path.basename(r.path)
        q = l1_by_id.get(fid)
        n_cycles = _scalar(r.n_cycles)
        row = {
            "file_id": fid,
            "name": name,
            "t_start_unix": _scalar(r.t_start_unix),
            "t_end_unix": _scalar(r.t_end_unix),
            "n_cycles": n_cycles,
            "has_l1": q is not None,
            "has_ql": fid in ql_ids,
            "total_data_drops": _scalar(r.total_data_drops),
            "n_adc_clip_cycles": int(clip_counts.get(fid, 0)),
            "rfi_occupancy": _scalar(q.rfi_occupancy) if q is not None else None,
            "n_outlier_cycles": _scalar(q.n_outlier_cycles) if q is not None else None,
            "n_persistent_lines": _scalar(q.n_persistent_lines) if q is not None else None,
            "n_nonfinite": _scalar(q.n_nonfinite) if q is not None else None,
            "q_median": _scalar(q.q_median_60_90) if q is not None else None,
        }
        row["badges"] = _badges(row, longest, fid in failed)
        files.append(row)
    return files, events


def _cycles(n: int) -> str:
    return f"{n} cycle{'' if n == 1 else 's'}"


def _badges(row: Dict[str, Any], longest: int, failed: bool) -> List[Dict[str, str]]:
    """QA badges for one file: level is ``warn``, ``info`` or ``ok``."""
    b: List[Dict[str, str]] = []
    if not row["has_l1"] and not row["has_ql"]:
        b.append({
            "level": "info",
            "text": "unreadable (no products)" if failed else "not processed yet",
        })
    elif not row["has_l1"]:
        b.append({"level": "info", "text": "no L1 (failed)" if failed else "no L1 yet"})
    n = row["n_cycles"] or 0
    end = row["t_end_unix"]
    # Files are split at UTC midnight (08:00 AWST): the night's last file is
    # usually cut short there, by design.
    cut_at_midnight = end is not None and end % 86400 > 86400 - 120
    if cut_at_midnight:
        b.append({"level": "info", "text": "ends at the UTC-midnight file split"})
    elif longest and n < QA_THRESHOLDS["short_fraction"] * longest:
        b.append({"level": "info", "text": f"short ({_cycles(n)})"})
    if (row["total_data_drops"] or 0) > 0:
        b.append({"level": "warn", "text": f"data drops: {row['total_data_drops']}"})
    if row["n_adc_clip_cycles"] > 0:
        b.append({"level": "warn", "text": f"ADC full scale: {_cycles(row['n_adc_clip_cycles'])}"})
    rfi = row["rfi_occupancy"]
    if rfi is not None and rfi > QA_THRESHOLDS["rfi_occupancy"]:
        b.append({"level": "warn", "text": f"RFI occupancy {100 * rfi:.2f}%"})
    if (row["n_outlier_cycles"] or 0) > QA_THRESHOLDS["outlier_cycles"]:
        b.append({"level": "warn", "text": f"outliers: {_cycles(row['n_outlier_cycles'])}"})
    if (row["n_nonfinite"] or 0) > 0:
        b.append({"level": "info", "text": f"{row['n_nonfinite']} non-finite Q samples"})
    if not b:
        b.append({"level": "ok", "text": "ok"})
    return b


def build_night(prod: Any, start: float, end: float, p0: bool = False, max_rows: int = DEFAULT_MAX_ROWS) -> Dict[str, Any]:
    """Everything the night page shows, from precomputed products only."""
    tic = time.perf_counter()
    notes: List[str] = []
    out: Dict[str, Any] = {"night": _night_info(start, end, _latest_night(prod))}
    out["quicklook"] = _quicklook(prod, start, end, "ant", p0, max_rows)
    try:
        l1 = prod.l1(start=start, end=end, load="ant")
    except LookupError:
        l1 = None
        notes.append("no L1 products yet")
    if l1 is not None:
        out["band"], missing = _band_series(l1, start, end)
        if missing:
            notes.append(f"unreadable L1 products: {', '.join(missing)}")
    else:
        out["band"] = None
    try:
        with _open_catalog(prod) as cat:
            out["housekeeping"] = _housekeeping(cat, start, end, list(HOUSEKEEPING_NAMES))
            out["files"], out["events"] = _files_qa(
                prod, cat, l1 if l1 is not None else _empty_l1(), start, end
            )
    except Exception as e:  # the catalog is optional for the waterfall
        log.exception("catalog query failed")
        notes.append(f"catalog unavailable: {e}")
        out["degraded"] = True
        out.setdefault("housekeeping", None)
        out.setdefault("files", [])
        out.setdefault("events", {"adc_clip_unix": [], "data_drop_unix": []})
    out["thresholds"] = QA_THRESHOLDS
    out["warnings"] = notes
    out["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["elapsed_s"] = round(time.perf_counter() - tic, 3)
    return out


def _empty_l1() -> Any:
    import pandas as pd

    return pd.DataFrame(columns=["catalog_file_id", "path"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/status")
def status() -> Dict[str, Any]:
    if IMPORT_ERROR is not None:
        return {"available": False, "error": IMPORT_ERROR}
    prod = get_products()
    out: Dict[str, Any] = {"available": True, "error": None,
                           "products_db": str(prod.settings.products_db)}
    for stage in ("ql", "l1"):
        try:
            out[stage] = {k: _scalar(v) for k, v in prod.coverage(stage).items()}
        except LookupError:
            out[stage] = None
        except (sqlite3.Error, OSError) as e:
            out["available"], out["error"] = False, str(e)
    return out


@router.get("/nights/latest")
def nights_latest() -> Dict[str, Any]:
    prod = get_products()
    with _db_errors():
        latest = _latest_night(prod)
    if latest is None:
        raise HTTPException(status_code=404, detail="no quick-look products yet")
    return _night_info(*latest, latest)


@router.get("/night")
def night(
    date: Optional[str] = Query(None, description="Local date of the night's evening (YYYY-MM-DD); default: latest"),
    p0: bool = Query(False, description="Also return the p0 waterfall"),
    max_rows: int = Query(DEFAULT_MAX_ROWS, ge=10, le=MAX_ROWS_LIMIT),
) -> Dict[str, Any]:
    prod = get_products()
    if date:
        try:
            day = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from None
        if not 2000 <= day.year <= 2100:
            raise HTTPException(status_code=400, detail="date out of range")
        start, end = _night_bounds(day)
    else:
        with _db_errors():
            latest = _latest_night(prod)
        if latest is None:
            raise HTTPException(status_code=404, detail="no quick-look products yet")
        start, end = latest
    key = ("night", start, p0, max_rows, _data_version(prod))
    hit = _cache.get(key)
    if hit is not None:
        return hit
    with _heavy_slot(), _db_errors():
        payload = build_night(prod, start, end, p0=p0, max_rows=max_rows)
    if not payload.get("degraded"):  # don't keep a transient failure for 15 min
        _cache.put(key, payload)
    return payload


@router.get("/quicklook")
def quicklook(
    start: str,
    end: str,
    load: str = "ant",
    p0: bool = False,
    max_rows: int = Query(DEFAULT_MAX_ROWS, ge=10, le=MAX_ROWS_LIMIT),
) -> Dict[str, Any]:
    prod = get_products()
    t0, t1 = _require_range(start, end)
    if p0 and t1 - t0 > MAX_SPAN_DAYS_P0 * 86400:
        raise HTTPException(
            status_code=400, detail=f"p0 ranges are limited to {MAX_SPAN_DAYS_P0} days"
        )
    with _heavy_slot(), _db_errors():
        return _quicklook(prod, t0, t1, load, p0, max_rows)


@router.get("/l1")
def l1(start: str, end: str, load: Optional[str] = "ant") -> Dict[str, Any]:
    prod = get_products()
    t0, t1 = _require_range(start, end)
    try:
        with _db_errors():
            df = prod.l1(start=t0, end=t1, load=load or None)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    drop = [c for c in ("path", "input_path", "t_start", "t_end") if c in df]
    rows = df.drop(columns=drop).assign(
        name=[os.path.basename(p) for p in df.input_path]
    )
    return {
        "rows": [{k: _scalar(v) for k, v in r.items()} for r in rows.to_dict("records")],
    }


@router.get("/housekeeping")
def housekeeping(start: str, end: str, names: Optional[str] = None) -> Dict[str, Any]:
    prod = get_products()
    t0, t1 = _require_range(start, end)
    wanted = [n for n in (names or "").split(",") if n] or list(HOUSEKEEPING_NAMES)
    with _db_errors(), _open_catalog(prod) as cat:
        return _housekeeping(cat, t0, t1, wanted)
