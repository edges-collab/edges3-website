"""
Calibration inputs from the EDGES catalog
=========================================

Replaces the old raw-directory scanning (``scan_dates.py``) and the
merging of every file in the temperature-log directory. Everything here
is a read-only query of the catalog (``edges-catalog``):

* :func:`available_dates` lists what the Select page offers: calibration
  days (all four loads start that UTC day), full averaged S11 sessions
  (``YYYY_DDD_HH`` stems) and antenna spectra (``YYYY_DDD_HH_MM_SS``).
* :func:`resolve_dates` turns the Select page's choices (``"Latest"`` or a
  value) into concrete ones; ``"Latest"`` for S11 means the session the
  catalog recommends for the calibration day.
* :func:`resolve_inputs` gives the exact files of one run and the probe
  temperatures at the time of each calibration spectrum, plus ``issues``:
  everything that would stop or weaken the calibration
  (``Catalog.calibration_inputs`` and the lookups below).

Temperatures come from ``Catalog.housekeeping`` data, which is
de-duplicated and excludes logs of other receivers. For each spectrum, in
order: the ``.tmp`` snapshot of that load written at the hour of the
file's time stamp; else the temperature-log reading of *that probe*
nearest to the spectrum's first cycle, within :data:`TEMPLOG_TOLERANCE_S`;
else the fallback constant, with an issue. There is no substitution of
other probes.

The resulting ``inputs`` dict is JSON-serialisable; the backend writes it
to ``<run_dir>/inputs.json`` and ``run_single_day.py --inputs`` reads it.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config
import products_api

DEPLOYMENT = products_api.DEPLOYMENT
CAL_LOADS = ("amb", "hot", "open", "short")
#: Temperature-log readings are every ~5.2 min; accept the nearest within this.
TEMPLOG_TOLERANCE_S = 15 * 60
LATEST = "Latest"
#: "Latest" antenna spectrum: skip files shorter than this (e.g. one still
#: being written, or a short continuation after the UTC-midnight split).
MIN_LATEST_RAW_CYCLES = 100


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


# ---------------------------------------------------------------------------
# Available dates
# ---------------------------------------------------------------------------
def available_dates(cat: Any) -> Dict[str, List[str]]:
    """Calibration days, S11 session stems and antenna spectrum stamps (sorted)."""
    acq = cat.sql(
        "SELECT load, stamp_unix, relpath FROM v_file WHERE kind = 'acq'"
        " AND status = 'present' AND category = 'science' AND deployment = ?"
        f" AND load IN ({','.join('?' * (len(CAL_LOADS) + 1))})",
        (DEPLOYMENT, *CAL_LOADS, "ant"),
    )
    cal = acq[acq.load.isin(CAL_LOADS)]
    days: Dict[str, set] = {}
    for load, t in zip(cal.load, cal.stamp_unix):
        days.setdefault(_stamp(t, "%Y_%j"), set()).add(load)
    calibration = sorted(d for d, loads in days.items() if len(loads) == len(CAL_LOADS))

    ant = acq[acq.load == "ant"]
    raw = sorted(
        os.path.basename(p)[: -len("_ant.acq")]
        for p in ant.relpath
        if p.endswith("_ant.acq")
    )
    return {"calibration": calibration, "s11": _s11_stems(cat), "raw": raw}


def _s11_sessions(cat: Any):
    """Full averaged S11 sessions at the root of the tree, with their stems."""
    df = cat.sql(
        "SELECT s.id, s.stamp_unix, vf.relpath FROM s11_session s"
        " JOIN s11_session_file sf ON sf.session_id = s.id"
        " JOIN v_file vf ON vf.id = sf.file_id"
        " WHERE s.kind = 'full' AND s.source = 'averaged' AND sf.label = 'O'"
        " AND vf.deployment = ? AND vf.status = 'present'"
        " AND vf.relpath NOT LIKE '%/%' ORDER BY s.stamp_unix",
        (DEPLOYMENT,),
    )
    df["stem"] = [p[: -len("_O.s1p")] for p in df.relpath]
    return df


def _s11_stems(cat: Any) -> List[str]:
    return sorted(set(_s11_sessions(cat).stem))


# ---------------------------------------------------------------------------
# Date resolution
# ---------------------------------------------------------------------------
def resolve_dates(
    cat: Any, dates: Dict[str, str], available: Dict[str, List[str]]
) -> Dict[str, str]:
    """Resolve ``{"cal", "s11", "raw"}`` choices (``"Latest"`` or a value)."""
    out: Dict[str, str] = {}
    for key, list_key in (("cal", "calibration"), ("raw", "raw")):
        v = dates.get(key) or LATEST
        choices = available.get(list_key, [])
        if v == LATEST:
            if not choices:
                raise InputError(f"No {list_key} dates available")
            v = (_latest_complete_raw(cat) if key == "raw" else None) or choices[-1]
        elif v not in choices:
            raise InputError(f"Unknown {list_key} date: {v!r}")
        out[key] = v
    v = dates.get("s11") or LATEST
    if v == LATEST:
        v = recommended_s11(cat, out["cal"])
        if v is None or v not in available.get("s11", []):
            raise InputError(
                f"No usable full S11 session near calibration day {out['cal']}:"
                " choose one explicitly"
            )
    elif v not in available.get("s11", []):
        raise InputError(f"Unknown s11 date: {v!r}")
    out["s11"] = v
    return out


def _latest_complete_raw(cat: Any) -> Optional[str]:
    """The latest *finished* antenna spectrum with at least ``MIN_LATEST_RAW_CYCLES``.

    Finished means a later antenna file exists (FASTSPEC had moved on when the
    catalog was updated), so the file was not still being written.
    """
    df = cat.sql(
        "SELECT relpath FROM v_spectra WHERE load = 'ant' AND status = 'present'"
        " AND category = 'science' AND deployment = ? AND n_cycles >= ?"
        " AND t_start_unix < (SELECT max(t_start_unix) FROM v_spectra WHERE"
        " load = 'ant' AND status = 'present' AND deployment = ?)"
        " ORDER BY t_start_unix DESC LIMIT 1",
        (DEPLOYMENT, MIN_LATEST_RAW_CYCLES, DEPLOYMENT),
    )
    if df.empty:
        return None
    return os.path.basename(df.relpath.iloc[0])[: -len("_ant.acq")]


def recommended_s11(cat: Any, cal_date: str) -> Optional[str]:
    """Stem of the S11 session ``Catalog.calibration_inputs`` picks for a day."""
    ci = cat.calibration_inputs(cal_date, deployment=DEPLOYMENT)
    files = (ci.get("s11") or {}).get("files") or {}
    o = files.get("O")
    return os.path.basename(o)[: -len("_O.s1p")] if o else None


# ---------------------------------------------------------------------------
# Inputs of one run
# ---------------------------------------------------------------------------
def _file_times(cat: Any, paths: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    """``path -> {stamp_unix, t_start_unix}`` (file-name stamp, first cycle)."""
    if not paths:
        return {}
    marks = ",".join("?" * len(paths))
    df = cat.sql(
        "SELECT f.path, f.stamp_unix, s.t_start_unix FROM v_file f"
        " LEFT JOIN v_spectra s ON s.path = f.path"
        f" WHERE f.path IN ({marks})",
        tuple(paths),
    )
    return {
        r.path: {
            "stamp_unix": float(r.stamp_unix),
            "t_start_unix": None if r.t_start_unix != r.t_start_unix or r.t_start_unix is None
            else float(r.t_start_unix),
        }
        for r in df.itertuples()
    }


def _file_versions(cat: Any, files: Dict[str, Any]) -> Dict[str, Any]:
    """Version of every input: catalog sha256 and live size/mtime (dedup key)."""
    paths = [p for k, p in files.items() if k != "s11" and p] + list(files["s11"].values())
    if not paths:
        return {}
    df = cat.sql(
        f"SELECT path, size, sha256 FROM v_file WHERE path IN ({','.join('?' * len(paths))})",
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


def probe_temperature(
    cat: Any,
    *,
    probe: float,
    context: str,
    stamp_unix: float,
    t_unix: float,
    label: str,
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


def resolve_inputs(cat: Any, resolved: Dict[str, str]) -> Dict[str, Any]:
    """Files, temperatures and issues for one run (``resolved`` from :func:`resolve_dates`)."""
    cal_date, s11_stem, raw = resolved["cal"], resolved["s11"], resolved["raw"]
    ci = cat.calibration_inputs(cal_date, deployment=DEPLOYMENT)
    issues: List[str] = list(ci["issues"])
    files: Dict[str, Any] = {}
    for load in CAL_LOADS:
        paths = ci["spectra"].get(load) or []
        files[load] = paths[0] if paths else None

    name = f"/{raw}_ant.acq"
    ant = cat.sql(
        "SELECT path FROM v_file WHERE kind = 'acq' AND load = 'ant'"
        " AND status = 'present' AND category = 'science' AND deployment = ?"
        " AND substr(relpath, -length(?)) = ? ORDER BY relpath",
        (DEPLOYMENT, name, name),
    )
    if ant.empty:
        raise InputError(f"No antenna spectrum {raw!r} in the catalog")
    files["ant"] = str(ant.path.iloc[0])

    sessions = _s11_sessions(cat)
    match = sessions[sessions.stem == s11_stem]
    if match.empty:
        raise InputError(f"No full S11 session {s11_stem!r} in the catalog")
    s11 = cat.s11_files(int(match.id.iloc[-1]))
    files["s11"] = dict(zip(s11.label, s11.path))
    suggested = recommended_s11(cat, cal_date)
    if suggested != s11_stem:
        # the catalog's S11 remarks are about its own pick, not this one
        issues = [i for i in issues if not i.startswith("no full S11 session")]
        issues.append(
            f"S11 session {s11_stem} chosen; the catalog recommends"
            f" {suggested or 'none'} for calibration day {cal_date}"
        )
    root = ci.get("root") or str(config.RAW_DATA_ROOT)
    off_root = [p for p in files["s11"].values() if os.path.dirname(p) != root.rstrip("/")]
    if off_root:
        # alancal_edges3 and the antenna S11 read <root>/<stem>_*.s1p
        issues.append(
            f"{len(off_root)} S11 files are not in the spectra root {root};"
            " the calibration reads the copies there"
        )

    times = _file_times(cat, [p for p in (files.get("amb"), files.get("hot"), files["ant"]) if p])
    temps: Dict[str, Any] = {}
    # (display, spectrum, snapshot context, probe, default)
    lookups = [
        ("ambient", files.get("amb"), "amb", config.PROBE_AMBIENT, config.TCOLD_FALLBACK_K),
        ("hot", files.get("hot"), "hot", config.PROBE_HOT, config.THOT_FALLBACK_K),
        ("lna", files["ant"], "ant", config.PROBE_LNA, config.TCAB_FALLBACK_K),
        # the ambient probe at the observation time (the "actual" temperature)
        ("obs_ambient", files["ant"], "ant", config.PROBE_AMBIENT, config.TCOLD_FALLBACK_K),
    ]
    for display, path, context, probe, default_k in lookups:
        ft = times.get(path) if path else None
        if ft is None:
            temps[display] = {
                "probe": probe, "time": None, "reading_time": None, "source": "default",
                "temperature_k": default_k, "temperature_c": default_k - 273.15,
            }
        else:
            t = ft["t_start_unix"] if ft["t_start_unix"] is not None else ft["stamp_unix"]
            temps[display] = probe_temperature(
                cat, probe=probe, context=context, stamp_unix=ft["stamp_unix"],
                t_unix=t, label=display, default_k=default_k,
            )
        e = temps[display]
        if e["source"] == "default":
            issues.append(
                f"no {display} temperature (probe {int(probe)}) within "
                f"{TEMPLOG_TOLERANCE_S // 60} min of {e['time'] or 'its spectrum'}:"
                f" using the fallback {default_k:.2f} K"
            )

    return {
        "dates": dict(resolved),
        "root": root,
        "files": files,
        "file_versions": _file_versions(cat, files),
        "temperatures": temps,
        "hk_coverage": ci.get("hk_coverage"),
        "recommended_s11": suggested,
        "issues": issues,
    }
