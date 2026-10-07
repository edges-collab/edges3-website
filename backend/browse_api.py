"""
Browse a receiver's raw data from the catalog
=============================================

The raw-data overview of a deployment (first: EDGES-2 low2), from the EDGES
catalog only: what was recorded when, the per-file and per-cycle quality
numbers that spectrum extraction adds (``v_spectra``, ``Catalog.acq_cycles``),
the raw S11 measurements and the site's weather. Nothing here needs pipeline
products (EDGES-2 has none yet), and nothing reads a spectrum file: the only
raw files read are S11 ``.s1p`` files (~10 kB), one session at a time.

Endpoints (prefix ``/api/browse``)
----------------------------------
GET /deployments                       the receivers this page offers
GET /{deployment}/overview             per UTC day over the whole record: files,
                                       GB, hours and cycles of data, data drops,
                                       S11 sessions (cached)
GET /{deployment}/range?start=&end=    files and S11 sessions in a range (at
                                       most :data:`MAX_RANGE_DAYS` days)
GET /{deployment}/weather?start=&end=  the site's weather log in that range
GET /{deployment}/cycles/{file_id}     one spectrum file's cycles: ADC extremes
                                       and data drops per switch position
GET /{deployment}/s11?stamp=           one S11 session's files, as measured
                                       (raw, uncalibrated)

Times are POSIX seconds (UTC); ``start``/``end`` also take ISO strings.
Only the deployments in :data:`DEPLOYMENTS` can be asked for.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException

import products_api
from products_api import _db_errors, _float_list, _heavy_slot, _parse_time, _scalar

router = APIRouter(prefix="/api/browse", tags=["browse"])

#: The receivers the browse page offers: deployment -> label (more to come).
DEPLOYMENTS: dict[str, str] = {
    "edges2-low2-mro": "EDGES-2 low2",
    # probably the same antenna with a 131,072-channel spectrometer: not yet
    # confirmed (edges-database ISSUES #35), so listed on its own
    "edges2-low2-128k-mro": "EDGES-2 low2 (128k spectrometer, 2024–)",
}
MAX_RANGE_DAYS = 62
#: Weather points sent for a range at most (the log is every 5 min).
MAX_WEATHER_POINTS = 6000
#: The labels of a raw EDGES-2 S11 session (``s11/daily``): the antenna and
#: the VNA's four calibration inputs.
S11_SESSION_LABEL = "antenna_s11"


def _deployment(name: str) -> str:
    if name not in DEPLOYMENTS:
        raise HTTPException(status_code=404, detail=f"unknown deployment {name!r}")
    return name


def _range(start: str | None, end: str | None) -> tuple[float, float]:
    t0, t1 = _parse_time(start, "start"), _parse_time(end, "end")
    if t0 is None or t1 is None:
        raise HTTPException(status_code=400, detail="start and end are required")
    if not 0 < t1 - t0 <= MAX_RANGE_DAYS * 86400:
        raise HTTPException(status_code=400, detail=f"the range must be 0 to {MAX_RANGE_DAYS} days long")
    return t0, t1


def _finite(v: Any) -> bool:
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _day(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d")


def _acq_files(cat: Any, deployment: str, t0: float | None = None, t1: float | None = None):
    """The deployment's spectrum files, with what extraction added (or NaN)."""
    where, params = ["f.deployment = ?", "f.kind = 'acq'", "f.status = 'present'"], [deployment]
    if t0 is not None:
        # a file before extraction has only its name's time stamp
        where.append("coalesce(s.t_end_unix, f.stamp_unix) >= ? AND coalesce(s.t_start_unix, f.stamp_unix) < ?")
        params += [t0, t1]
    return cat.sql(
        "SELECT f.id AS file_id, f.relpath, f.category, f.stamp_unix, f.stamp_precision, f.size,"
        " s.n_cycles, s.t_start_unix, s.t_end_unix, s.duration_hr, s.total_data_drops,"
        " s.adcmax_max, s.adcmin_min"
        " FROM v_file f LEFT JOIN v_spectra s ON s.file_id = f.id"
        f" WHERE {' AND '.join(where)} ORDER BY coalesce(s.t_start_unix, f.stamp_unix)",
        tuple(params),
    )


def _s11_files(cat: Any, deployment: str, t0: float | None = None, t1: float | None = None):
    """The deployment's daily S11 files (sessions share a time stamp; science
    files only, as the catalog's own sessions)."""
    where = ["deployment = ?", "kind = 's1p'", "status = 'present'", "category = 'science'",
             "stamp_precision = 'second'", "label IS NOT NULL"]
    params: list[Any] = [deployment]
    if t0 is not None:
        where.append("stamp_unix >= ? AND stamp_unix < ?")
        params += [t0, t1]
    return cat.sql(
        f"SELECT id AS file_id, relpath, label, stamp_unix FROM v_file WHERE {' AND '.join(where)}"
        " ORDER BY stamp_unix, label",
        tuple(params),
    )


def _sessions(s11) -> list[dict[str, Any]]:
    """S11 files grouped by time stamp; only groups with an antenna measurement."""
    out = []
    for stamp, g in s11.groupby("stamp_unix", sort=True):
        labels = sorted(str(v) for v in g.label)
        if S11_SESSION_LABEL in labels:
            out.append({"stamp_unix": float(stamp), "labels": labels,
                        "name": g.relpath.iloc[0].rsplit("/", 1)[-1].rsplit("_", 2)[0]})
    return out


# ---------------------------------------------------------------------------
# Overview of the whole record
# ---------------------------------------------------------------------------
def build_overview(cat: Any, deployment: str) -> dict[str, Any]:
    """Per UTC day: spectrum files (by start, or name stamp before extraction),
    their size, the hours and cycles of data (split across days by each file's
    span), data drops, the largest ADC value, and S11 sessions."""
    acq = _acq_files(cat, deployment)
    sci = acq[acq.category == "science"]
    s11 = _sessions(_s11_files(cat, deployment))
    if sci.empty and not s11:
        return {"deployment": deployment, "label": DEPLOYMENTS[deployment], "days": [], "summary": None}
    per = defaultdict(lambda: {"files": 0, "gb": 0.0, "hours": 0.0, "cycles": 0.0, "drops": 0.0,
                               "adcmax": None, "s11": 0})
    extracted = sci.t_start_unix.notna() & sci.t_end_unix.notna()
    for r in sci.itertuples():
        d = per[_day(r.t_start_unix if _finite(r.t_start_unix) else r.stamp_unix)]
        d["files"] += 1
        d["gb"] += float(r.size) / 1e9
        if _finite(r.total_data_drops):
            d["drops"] += float(r.total_data_drops)
        if _finite(r.adcmax_max):
            d["adcmax"] = max(d["adcmax"] or 0.0, float(r.adcmax_max))
    # hours and cycles: each extracted file's span split across the days it covers
    for r in sci[extracted].itertuples():
        a, b = float(r.t_start_unix), float(r.t_end_unix)
        span = max(b - a, 1e-9)
        n = float(r.n_cycles) if _finite(r.n_cycles) else 0.0
        t = a
        while t < b:
            nxt = min(b, (math.floor(t / 86400) + 1) * 86400)
            d = per[_day(t)]
            d["hours"] += (nxt - t) / 3600
            d["cycles"] += n * (nxt - t) / span
            t = nxt
    for s in s11:
        per[_day(s["stamp_unix"])]["s11"] += 1
    first, last = min(per), max(per)
    d0 = date.fromisoformat(first)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range((date.fromisoformat(last) - d0).days + 1)]
    rows = [per.get(d) for d in days]

    def col(k: str, digits: int = 3) -> list[Any]:
        return [None if r is None or r[k] is None else round(r[k], digits) for r in rows]

    t_first = float(np.nanmin(np.where(extracted, sci.t_start_unix, sci.stamp_unix))) if len(sci) else None
    t_last = float(np.nanmax(np.where(extracted, sci.t_end_unix, sci.stamp_unix))) if len(sci) else None
    return {
        "deployment": deployment,
        "label": DEPLOYMENTS[deployment],
        "days": days,
        "files": col("files", 0),
        "gb": col("gb"),
        "hours": col("hours"),
        "cycles": col("cycles", 0),
        "drops": col("drops", 0),
        "adcmax": col("adcmax", 4),
        "s11_sessions": col("s11", 0),
        "summary": {
            "n_files": int(len(sci)),
            "n_extracted": int(extracted.sum()),
            "n_other_files": int(len(acq) - len(sci)),
            "tb": round(float(sci["size"].sum()) / 1e12, 3),
            "hours": round(float(sum(r["hours"] for r in per.values())), 1),
            "n_s11_sessions": len(s11),
            "t_first_unix": t_first,
            "t_last_unix": t_last,
            "days_with_data": sum(1 for r in per.values() if r["files"]),
        },
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/deployments")
def deployments() -> list[dict[str, str]]:
    return [{"name": k, "label": v} for k, v in DEPLOYMENTS.items()]


@router.get("/{deployment}/overview")
def overview(deployment: str) -> dict[str, Any]:
    dep = _deployment(deployment)
    prod = products_api.get_products()
    key = ("browse-overview", dep, products_api._data_version(prod))
    hit = products_api._cache.get(key)
    if hit is not None:
        return hit
    with _heavy_slot(), _db_errors(), products_api._open_catalog(prod) as cat:
        out = build_overview(cat, dep)
    products_api._cache.put(key, out)
    return out


@router.get("/{deployment}/range")
def file_range(deployment: str, start: str, end: str) -> dict[str, Any]:
    """The spectrum files and S11 sessions of a range."""
    dep = _deployment(deployment)
    t0, t1 = _range(start, end)
    prod = products_api.get_products()
    with _db_errors(), products_api._open_catalog(prod) as cat:
        acq = _acq_files(cat, dep, t0, t1)
        s11 = _sessions(_s11_files(cat, dep, t0, t1))
    files = [
        {
            "file_id": int(r.file_id),
            "name": r.relpath.rsplit("/", 1)[-1],
            "category": r.category,
            "stamp_unix": _scalar(r.stamp_unix),
            "size_mb": round(float(r.size) / 1e6, 1),
            "n_cycles": _scalar(r.n_cycles),
            "t_start_unix": _scalar(r.t_start_unix),
            "t_end_unix": _scalar(r.t_end_unix),
            "duration_hr": _scalar(r.duration_hr),
            "data_drops": _scalar(r.total_data_drops),
            "adcmax": _scalar(r.adcmax_max),
            "adcmin": _scalar(r.adcmin_min),
        }
        for r in acq.itertuples()
    ]
    return {"deployment": dep, "start_unix": t0, "end_unix": t1, "files": files, "s11_sessions": s11}


@router.get("/{deployment}/weather")
def weather(deployment: str, start: str, end: str) -> dict[str, Any]:
    """The site's weather log (temperatures in K, humidity in %) in a range.

    Its ``frontend`` column is not attributed to a receiver (whose front end
    it measured is an open question), so the page labels it as such."""
    dep = _deployment(deployment)
    t0, t1 = _range(start, end)
    prod = products_api.get_products()
    with _db_errors(), products_api._open_catalog(prod) as cat:
        site = cat.sql("SELECT site FROM deployment WHERE name = ?", (dep,)).site
        if site.empty or site.iloc[0] is None:
            return {"available": False, "reason": "no site for this deployment"}
        w = cat.weather(start=t0, end=t1, site=str(site.iloc[0]))
    if w.empty:
        return {"available": False, "reason": "no weather log in this range"}
    step = max(1, math.ceil(len(w) / MAX_WEATHER_POINTS))
    w = w.iloc[::step]
    cols = [c for c in w.columns if c not in ("t", "t_unix", "n_files", "n_versions")
            and np.issubdtype(w[c].dtype, np.number) and w[c].notna().any()]
    return {
        "available": True,
        "site": str(site.iloc[0]),
        "time_unix": _float_list(w.t_unix, 0),
        "series": {c: _float_list(w[c], 2) for c in cols},
    }


@router.get("/{deployment}/cycles/{file_id}")
def cycles(deployment: str, file_id: int) -> dict[str, Any]:
    """One spectrum file's cycles: ADC extremes and data drops per switch position
    (0 antenna, 1 ambient load, 2 load + noise source)."""
    dep = _deployment(deployment)
    prod = products_api.get_products()
    with _db_errors(), products_api._open_catalog(prod) as cat:
        own = cat.sql("SELECT relpath FROM v_file WHERE id = ? AND deployment = ? AND kind = 'acq'",
                      (int(file_id), dep))
        if own.empty:
            raise HTTPException(status_code=404, detail="no such spectrum file in this deployment")
        c = cat.acq_cycles([int(file_id)])
    if c.empty:
        return {"available": False, "name": own.relpath.iloc[0].rsplit("/", 1)[-1],
                "reason": "not extracted yet (the catalog has no cycles for this file)"}
    c = c.sort_values("cycle")
    return {
        "available": True,
        "name": own.relpath.iloc[0].rsplit("/", 1)[-1],
        "time_unix": _float_list(c.t_unix, 1),
        **{k: _float_list(c[k], 5) for k in
           ("adcmax0", "adcmax1", "adcmax2", "adcmin0", "adcmin1", "adcmin2")},
        **{k: [int(v) for v in c[k]] for k in ("drops0", "drops1", "drops2")},
    }


@router.get("/{deployment}/s11")
def s11_session(deployment: str, stamp: float) -> dict[str, Any]:
    """One S11 session, as measured: each file's |S11| and phase (uncalibrated)."""
    dep = _deployment(deployment)
    prod = products_api.get_products()
    with _db_errors(), products_api._open_catalog(prod) as cat:
        files = cat.sql(
            "SELECT path, label FROM v_file WHERE deployment = ? AND kind = 's1p'"
            " AND status = 'present' AND stamp_unix = ? AND label IS NOT NULL ORDER BY label",
            (dep, float(stamp)),
        )
    if files.empty:
        raise HTTPException(status_code=404, detail="no S11 session at this time")
    from edges.cal import ReflectionCoefficient

    traces = {}
    with _heavy_slot():
        for r in files.itertuples():
            try:
                rc = ReflectionCoefficient.from_s1p(r.path)
            except Exception as e:  # a malformed file: say so, keep the others
                traces[str(r.label)] = {"error": f"{type(e).__name__}: {e}"}
                continue
            g = np.asarray(rc.reflection_coefficient)
            traces[str(r.label)] = {
                "freq_mhz": _float_list(rc.freqs.to_value("MHz"), 4),
                "re": _float_list(np.real(g), 7),
                "im": _float_list(np.imag(g), 7),
            }
    return {"deployment": dep, "stamp_unix": float(stamp), "traces": traces}
