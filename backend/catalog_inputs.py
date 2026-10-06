"""
Calibration and observation inputs from the EDGES catalog
=========================================================

Everything here is a read-only query of the catalog (``edges-catalog``); the
site never scans the raw data tree or merges temperature-log files.

* An **observation** (one night of antenna spectra and an antenna S11
  session; see ``runs_api.py``): :func:`observation_options`,
  :func:`resolve_observation` return a JSON-serialisable ``inputs`` dict with
  the exact files, their versions and ``issues``. ``"Latest"`` is the latest
  night with antenna data, and the antenna S11 session nearest before it.
* **Calibration days** (:func:`calibration_days`), only to say which days
  have no stored receiver calibration. The calibration itself, inputs
  included, is edges-pipeline's (``calibrations.py``).

The ambient-load temperature at each antenna file is shown for information
(the calibration of the antenna needs none): the ``.tmp`` snapshot written at
the hour of the file's time stamp, else the temperature-log reading of the
probe nearest to the file's first cycle, within :data:`TEMPLOG_TOLERANCE_S`.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import config
import products_api

DEPLOYMENT = products_api.DEPLOYMENT
CAL_LOADS = ("amb", "hot", "open", "short")
#: Temperature-log readings are every ~5.2 min; accept the nearest within this.
TEMPLOG_TOLERANCE_S = 15 * 60
LATEST = "Latest"
#: Warn if the antenna S11 session is further than this from the night.
ANT_S11_WARN_DAYS = 7
#: Antenna files modified more recently than this may still be being written
#: (FASTSPEC appends cycles): they are left out of a night, with an issue.
SETTLE_S = 10 * 60


class InputError(ValueError):
    """A requested date/session does not exist in the catalog."""


def open_catalog() -> Any:
    """A read-only catalog connection (the one next to the pipeline products)."""
    return products_api._open_catalog(products_api.get_products())


def _stamp(t_unix: float, fmt: str) -> str:
    return datetime.fromtimestamp(t_unix, timezone.utc).strftime(fmt)


def _iso(t_unix: Optional[float]) -> Optional[str]:
    if t_unix is None:
        return None
    return datetime.fromtimestamp(t_unix, timezone.utc).isoformat()


def _nan_to_none(v: Any) -> Any:
    return None if v is None or v != v else v


# ---------------------------------------------------------------------------
# S11 sessions
# ---------------------------------------------------------------------------
def _s11_sessions(cat: Any, kinds: Tuple[str, ...] = ("full",)):
    """Averaged S11 sessions (``kinds``: ``full`` and/or ``antenna``) at the root
    of the tree, with their file-name stems."""
    df = cat.sql(
        "SELECT s.id, s.kind, s.stamp_unix, vf.relpath FROM s11_session s"
        " JOIN s11_session_file sf ON sf.session_id = s.id"
        " JOIN v_file vf ON vf.id = sf.file_id"
        f" WHERE s.kind IN ({','.join('?' * len(kinds))}) AND s.source = 'averaged'"
        " AND sf.label = 'O' AND vf.deployment = ? AND vf.status = 'present'"
        " AND vf.relpath NOT LIKE '%/%' ORDER BY s.stamp_unix",
        (*kinds, DEPLOYMENT),
    )
    df["stem"] = [p[: -len("_O.s1p")] for p in df.relpath]
    return df


def _session_files(cat: Any, sessions, stem: str, what: str) -> Dict[str, str]:
    match = sessions[sessions.stem == stem]
    if match.empty:
        raise InputError(f"No {what} S11 session {stem!r} in the catalog")
    s11 = cat.s11_files(int(match.id.iloc[-1]))
    return dict(zip(s11.label, s11.path))


# ---------------------------------------------------------------------------
# File versions and temperatures
# ---------------------------------------------------------------------------
def _file_versions(cat: Any, paths: List[str]) -> Dict[str, Any]:
    """Version of every input: catalog sha256 and live size/mtime (dedup key)."""
    paths = [p for p in paths if p]
    if not paths:
        return {}
    df = cat.sql(
        f"SELECT path, sha256 FROM v_file WHERE path IN ({','.join('?' * len(paths))})",
        tuple(paths),
    )
    out: Dict[str, Any] = {}
    for r in df.itertuples():
        try:  # the file now (metadata only), in case it changed since the catalog update
            st = os.stat(r.path)
            live = [st.st_size, st.st_mtime_ns]
        except OSError:
            live = None
        out[r.path] = {"catalog_sha256": r.sha256, "live": live}
    return out


def _file_times(cat: Any, paths: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    """``path -> {stamp_unix, t_start_unix}`` (file-name stamp, first cycle)."""
    paths = [p for p in paths if p]
    if not paths:
        return {}
    df = cat.sql(
        "SELECT f.path, f.stamp_unix, s.t_start_unix FROM v_file f"
        " LEFT JOIN v_spectra s ON s.path = f.path"
        f" WHERE f.path IN ({','.join('?' * len(paths))})",
        tuple(paths),
    )
    return {
        r.path: {"stamp_unix": float(r.stamp_unix), "t_start_unix": _nan_to_none(r.t_start_unix)}
        for r in df.itertuples()
    }


def probe_temperature(
    cat: Any,
    *,
    probe: float,
    context: str,
    stamp_unix: float,
    t_unix: float,
    default_k: float,
) -> Dict[str, Any]:
    """Probe reading for one spectrum: snapshot, else nearest log reading, else default."""
    code = int(probe)
    hour = stamp_unix - stamp_unix % 3600
    entry: Dict[str, Any] = {
        "probe": probe,
        "time": _iso(t_unix),
        "reading_time": None,
        "source": "default",
        "temperature_k": default_k,
        "temperature_c": default_k - 273.15,
    }
    snap = cat.sql(
        "SELECT t_unix, value FROM v_housekeeping WHERE deployment = ?"
        " AND source = 'snapshot' AND context = ? AND code = ? AND t_unix = ?",
        (DEPLOYMENT, context, code, hour),
    )
    if len(snap):
        row, source = snap.iloc[0], "snapshot"
    else:
        near = cat.sql(
            "SELECT t_unix, value FROM v_housekeeping WHERE deployment = ?"
            " AND source = 'templog' AND code = ? AND t_unix BETWEEN ? AND ?"
            " ORDER BY abs(t_unix - ?) LIMIT 1",
            (DEPLOYMENT, code, t_unix - TEMPLOG_TOLERANCE_S,
             t_unix + TEMPLOG_TOLERANCE_S, t_unix),
        )
        if not len(near):
            return entry
        row, source = near.iloc[0], "templog"
    t_c = float(row.value)
    entry.update(
        source=source,
        reading_time=_iso(float(row.t_unix)),
        temperature_c=t_c,
        temperature_k=t_c + 273.15,
    )
    return entry


def _temperatures(
    cat: Any, lookups: List[Tuple[str, Optional[str], str, float, float]], issues: List[str]
) -> Dict[str, Any]:
    """``lookups``: (name, spectrum path, snapshot context, probe, fallback K)."""
    times = _file_times(cat, [p for _, p, *_ in lookups])
    temps: Dict[str, Any] = {}
    for name, path, context, probe, default_k in lookups:
        ft = times.get(path) if path else None
        if ft is None:
            e = {
                "probe": probe, "time": None, "reading_time": None, "source": "default",
                "temperature_k": default_k, "temperature_c": default_k - 273.15,
            }
        else:
            t = ft["t_start_unix"] if ft["t_start_unix"] is not None else ft["stamp_unix"]
            e = probe_temperature(
                cat, probe=probe, context=context, stamp_unix=ft["stamp_unix"],
                t_unix=t, default_k=default_k,
            )
        if e["source"] == "default":
            where = os.path.basename(path) if path else "its spectrum"
            issues.append(
                f"no {name} temperature (probe {int(probe)}) within "
                f"{TEMPLOG_TOLERANCE_S // 60} min of {where}: using the fallback"
                f" {default_k:.2f} K"
            )
        temps[name] = e
    return temps


# ---------------------------------------------------------------------------
# Calibrations
# ---------------------------------------------------------------------------
def calibration_days(cat: Any) -> List[str]:
    """UTC days (``YYYY_DDD``) on which all four calibration loads start."""
    df = cat.sql(
        "SELECT load, stamp_unix FROM v_file WHERE kind = 'acq' AND status = 'present'"
        " AND category = 'science' AND deployment = ?"
        f" AND load IN ({','.join('?' * len(CAL_LOADS))})",
        (DEPLOYMENT, *CAL_LOADS),
    )
    days: Dict[str, set] = {}
    for load, t in zip(df.load, df.stamp_unix):
        days.setdefault(_stamp(t, "%Y_%j"), set()).add(load)
    return sorted(d for d, loads in days.items() if len(loads) == len(CAL_LOADS))


# ---------------------------------------------------------------------------
# Observations (nights)
# ---------------------------------------------------------------------------
def _night_dates_of(t0: float, t1: float) -> List[str]:
    """Nights (local evening dates) that the interval ``[t0, t1]`` overlaps."""
    out = []
    start, _ = products_api.Products.night(float(t0), deployment=DEPLOYMENT)
    if start > t0:  # an afternoon instant: the night that starts this evening
        start -= 86400
    s = start
    while s <= t1:
        if t0 < s + 12 * 3600:
            out.append(products_api._night_date(s))
        s += 86400
    return out


def observation_options(cat: Any) -> Dict[str, List[str]]:
    """Nights with antenna spectra, and antenna S11 sessions (sorted, oldest first)."""
    sp = cat.sql(
        "SELECT t_start_unix, t_end_unix FROM v_spectra WHERE load = 'ant'"
        " AND status = 'present' AND category = 'science' AND deployment = ?"
        " AND t_start_unix IS NOT NULL AND t_end_unix IS NOT NULL",
        (DEPLOYMENT,),
    )
    nights = set()
    for t0, t1 in zip(sp.t_start_unix, sp.t_end_unix):
        nights.update(_night_dates_of(float(t0), float(t1)))
    stems = sorted(set(_s11_sessions(cat, ("full", "antenna")).stem))
    return {"nights": sorted(nights), "antenna_s11": stems}


def recommended_ant_s11(cat: Any, night_start: float) -> Optional[str]:
    """The antenna S11 session nearest before the night's end (else the first after)."""
    sessions = _s11_sessions(cat, ("full", "antenna"))
    if sessions.empty:
        return None
    before = sessions[sessions.stamp_unix <= night_start + 12 * 3600]
    best = before.iloc[-1] if len(before) else sessions.iloc[0]
    return str(best.stem)


def resolve_observation(cat: Any, night: str = LATEST, ant_s11: str = LATEST) -> Dict[str, Any]:
    """Inputs of one night's observation (``night``: local evening date YYYY-MM-DD)."""
    opts = observation_options(cat)
    if night in (None, "", LATEST):
        if not opts["nights"]:
            raise InputError("No nights with antenna data")
        night = opts["nights"][-1]
    elif night not in opts["nights"]:
        raise InputError(f"No antenna data for the night of {night!r}")
    start, end = products_api.Products.night(date.fromisoformat(night), deployment=DEPLOYMENT)

    issues: List[str] = []
    sp = cat.sql(
        "SELECT path, t_start_unix, t_end_unix, n_cycles FROM v_spectra"
        " WHERE load = 'ant' AND status = 'present' AND category = 'science'"
        " AND deployment = ? AND t_end_unix >= ? AND t_start_unix < ?"
        " ORDER BY t_start_unix",
        (DEPLOYMENT, start, end),
    )
    if sp.empty:
        raise InputError(f"No antenna spectra in the night of {night}")

    suggested = recommended_ant_s11(cat, start)
    if ant_s11 in (None, "", LATEST):
        if suggested is None:
            raise InputError("No antenna S11 session in the catalog")
        ant_s11 = suggested
    elif ant_s11 not in opts["antenna_s11"]:
        raise InputError(f"Unknown antenna S11 session: {ant_s11!r}")
    sessions = _s11_sessions(cat, ("full", "antenna"))
    s11_files = _session_files(cat, sessions, ant_s11, "antenna")
    missing = [lab for lab in ("ant", "O", "S", "L") if lab not in s11_files]
    if missing:
        raise InputError(f"S11 session {ant_s11} lacks {', '.join(missing)}")
    s11_stamp = float(sessions[sessions.stem == ant_s11].stamp_unix.iloc[-1])
    days = abs(s11_stamp - start) / 86400
    if days > ANT_S11_WARN_DAYS:
        issues.append(f"antenna S11 session {ant_s11} is {days:.0f} days from the night")
    if ant_s11 != suggested:
        issues.append(
            f"antenna S11 session {ant_s11} chosen; the nearest before the night is {suggested}"
        )

    ant_files = []
    now = datetime.now(timezone.utc).timestamp()
    for r in sp.itertuples():
        try:
            fresh = now - os.stat(r.path).st_mtime < SETTLE_S
        except OSError:
            fresh = False
        if fresh:
            issues.append(f"{os.path.basename(r.path)} is still being written: left out")
            continue
        # the ambient probe at each file (for information; the calibration of
        # the antenna needs no probe temperature, see run_single_day.T_LOAD)
        temps = _temperatures(cat, [
            ("obs_ambient", r.path, "ant", config.PROBE_AMBIENT, config.AMBIENT_FALLBACK_K),
        ], [])
        ant_files.append({
            "path": r.path,
            "name": os.path.basename(r.path),
            "t_start_unix": _nan_to_none(r.t_start_unix),
            "t_end_unix": _nan_to_none(r.t_end_unix),
            "n_cycles": _nan_to_none(r.n_cycles),
            "temperatures": temps,
        })
    if not ant_files:
        raise InputError(f"No finished antenna spectra in the night of {night} yet")
    return {
        "kind": "observation",
        "night": {"date": night, "start_unix": start, "end_unix": end},
        "dates": {"night": night, "ant_s11": ant_s11},
        "root": os.path.dirname(s11_files["O"]),
        "files": {"ant": ant_files, "ant_s11": s11_files},
        "file_versions": _file_versions(
            cat, [f["path"] for f in ant_files] + list(s11_files.values())
        ),
        "recommended_ant_s11": suggested,
        "issues": issues,
    }
