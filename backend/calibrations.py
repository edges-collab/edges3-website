"""
Receiver calibrations from edges-pipeline
=========================================

The site never calibrates the receiver itself. Every EDGES-3 calibration day
has a *stored* calibration in the pipeline's default settings
(``Products.calibrator``, ~0.3 s); other settings are *computed* with the same
code (``edges_pipeline.stages.rcal.calibrate_day``, ~45 s and ~2 GB, in the
background job runner of ``runs_api``, never in a request). The pipeline picks
the inputs from the catalog: the recommended full S11 session of the day and
the probe means during the ambient and hot-load spectra.

This module holds what both paths share: the readers' default configuration
(:func:`default_config`), which settings the page offers (:data:`FIELDS`) and
their validation (:func:`clean_params`), the day list with the reasons for
days without a calibration (:func:`day_list`), and the JSON a page plots
(:func:`calibration_json`), the same for a stored and a computed calibration.

The page's settings are *overrides* of the readers' default configuration
(the promoted one, whose settings the products database records), by
section: ``{"fit": {"cterms": 7}}`` (sections ``spectra``, ``fit``,
``hardware``, ``temperatures``, ``dicke``). No overrides means the stored
calibration. ``fstart``/``fstop`` are always ``wfstart``/``wfstop``
(edges-collab/edges-analysis#305), and the S11 session is the catalog's
recommendation (the stage has no setting for either).
"""

from __future__ import annotations

import copy
import json
import math
import threading
import time
from collections import OrderedDict
from typing import Any

import numpy as np

try:
    from edges_pipeline.products import _cal_day_key
    from edges_pipeline.stages import rcal
except ImportError:  # optional: products_api.get_products() answers 503
    rcal = None  # type: ignore[assignment]

    def _cal_day_key(day: Any) -> str:  # type: ignore[misc]
        raise ValueError("edges-pipeline is not installed")


LATEST = "Latest"
LOADS = ("ambient", "hot_load", "open", "short")

#: What the pipeline's day statuses (``Products.calibration_days``) mean.
STATUS_TEXT = {
    "pending": "not computed yet: the pipeline adds new days within about 12 hours",
    "no_temperature": "too few temperature-log readings during its ambient or hot-load spectrum",
    "s11_grids_differ": "its S11 session mixes VNA frequency grids",
    "no_s11_session": "no full S11 session for it",
    "incomplete": "not all four calibration loads have spectra",
    "missing": "some of its files are missing",
    "unhashed": "some of its files are not checksummed in the catalog yet",
    "unsettled": "some of its files are still being written",
}
NOTE = (
    "Days with all four calibration loads are listed; there were none in 2024–2025. "
    "New days appear within about 12 hours of their data."
)
#: Day statuses are cached this long (computing them reads the catalog, ~3-7 s).
STATUS_TTL_S = 10 * 60

#: The settings the page offers, by section: (key, label, min, max, integer).
#: The others (``fit.Lh`` -1 only, ``fit.delay_sweep_ns``, ``dicke``: a
#: convention the solution does not depend on) keep their defaults.
FIELDS: dict[str, list[tuple[str, str, float, float, bool]]] = {
    "fit": [
        ("cterms", "Scale/offset terms (cterms)", 1, 20, True),
        ("wterms", "Noise-wave terms (wterms)", 1, 20, True),
        ("wfstart", "Fit from [MHz] (wfstart)", 40, 200, False),
        ("wfstop", "Fit to [MHz] (wfstop)", 40, 200, False),
        ("nfit2", "Load S11 terms (nfit2; Fourier above 16)", 3, 60, True),
        ("nfit3", "Receiver S11 terms (nfit3)", 3, 30, True),
        ("ncal_iter", "Iterations (ncal_iter)", 1, 30, True),
        ("poly_spacing", "Scale/offset polynomial spacing", 0.05, 5, False),
    ],
    "spectra": [
        ("smooth", "Smoothing, decimation [channels]", 1, 64, True),
        ("delaystart", "Ignore the first [s] (delaystart)", 0, 7200, True),
    ],
    "temperatures": [
        ("ambient_code", "Ambient-load probe (101 ambient load, 100 front end)", 0, 999, True),
        ("hot_code", "Hot-load probe", 0, 999, True),
        ("min_readings", "Minimum probe readings", 1, 1000, True),
    ],
    "hardware": [
        ("match_resistance", "Match resistance [Ω]", 40, 60, False),
        ("calkit_delays", "Calkit offset delays [ps]", 0, 100, False),
        ("lna_cable_length", "LNA cable length [in]", 0, 50, False),
        ("lna_cable_loss", "LNA cable loss [%]", -500, 500, False),
        ("lna_cable_dielectric", "LNA cable dielectric [%]", -100, 100, False),
    ],
}
#: Shown first; the rest are under "More settings".
MAIN_FIELDS = {("fit", k) for k in ("cterms", "wterms", "wfstart", "wfstop", "nfit2", "nfit3")}
MIN_BAND_MHZ = 10.0


def available() -> bool:
    return rcal is not None


def default_config(prod: Any) -> dict[str, Any]:
    """The readers' default rcal configuration.

    ``hash``: the promoted configuration (else the newest with products; None
    if there are none); ``config``: its settings (else ``rcal.DEFAULT_CONFIG``);
    ``skew``: the installed edges-pipeline computes another hash for those
    settings (a different stage version), so a computation never reproduces
    the stored calibrations exactly.
    """
    try:
        h = prod.config_hash("rcal")
    except LookupError:
        h = None
    config = copy.deepcopy(rcal.DEFAULT_CONFIG)
    if h:
        df = prod.sql("SELECT config_text FROM stage_config WHERE config_hash = ?", (h,))
        if len(df):
            config = json.loads(df.config_text.iloc[0])
    return {"hash": h, "config": config, "skew": bool(h) and rcal.config_hash(config) != h}


def effective(defaults: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """The full settings: ``params`` (overrides) applied to ``defaults``."""
    out = copy.deepcopy(defaults)
    for section, values in params.items():
        out.setdefault(section, {}).update(values)
    return out


def clean_params(params: dict[str, Any] | None, defaults: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Check the page's settings; return only those that differ from ``defaults``.

    Raises ``ValueError`` (a 400) for unknown keys, non-numbers, values out of
    range or a fit band narrower than :data:`MIN_BAND_MHZ`.
    """
    out: dict[str, dict[str, Any]] = {}
    known = {s: {f[0]: f for f in fs} for s, fs in FIELDS.items()}
    for section, values in (params or {}).items():
        if section not in known or not isinstance(values, dict):
            raise ValueError(f"unknown settings section: {section!r}")
        for key, v in values.items():
            if key not in known[section]:
                raise ValueError(f"unknown setting: {section}.{key}")
            _, _, lo, hi, integer = known[section][key]
            if isinstance(v, bool) or v is None or v == "":
                raise ValueError(f"{section}.{key} must be a number")
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{section}.{key} must be a number") from None
            if not (math.isfinite(x) and lo <= x <= hi):
                raise ValueError(f"{section}.{key} must be in [{lo:g}, {hi:g}]")
            if integer and not x.is_integer():
                raise ValueError(f"{section}.{key} must be a whole number")
            x = int(x) if integer else x
            if x != defaults.get(section, {}).get(key):
                out.setdefault(section, {})[key] = x
    fit = effective(defaults, out)["fit"]
    if fit["wfstop"] - fit["wfstart"] < MIN_BAND_MHZ:
        raise ValueError(f"the fit band must be at least {MIN_BAND_MHZ:g} MHz wide")
    rcal.effective_config(effective(defaults, out))  # the stage's own check
    return out


def config_hash(settings: dict[str, Any]) -> str:
    """The configuration hash of full settings (:func:`effective`)."""
    return rcal.config_hash(settings)


def form(defaults: dict[str, Any]) -> dict[str, Any]:
    """The settings form: fields by section, with the default settings."""
    return {
        "defaults": defaults,
        "fields": [
            {"section": s, "key": k, "label": label, "min": lo, "max": hi,
             "integer": integer, "main": (s, k) in MAIN_FIELDS}
            for s, fs in FIELDS.items() for k, label, lo, hi, integer in fs
        ],
    }


def day_key(day: str) -> str:
    """``YYYY_DDD`` of a day (also ``YYYY:DDD``); ``ValueError`` otherwise."""
    return _cal_day_key(day)


def status_text(status: str, error: str | None = None) -> str:
    """A day status (``Products.calibration_days``) in words."""
    if status == "failed":
        return f"the pipeline failed on it: {error or 'no error recorded'}"
    return STATUS_TEXT.get(status, status)


_status_cache: dict[str, Any] = {"key": None, "t": 0.0, "rows": None}
_status_lock = threading.Lock()


def day_statuses(prod: Any) -> dict[str, dict[str, Any]]:
    """Every calibration day (all four loads) and what the pipeline made of it
    in the default configuration: ``status``, ``reason`` (None when done),
    ``s11_session``, ``issues``. Cached for :data:`STATUS_TTL_S` (per default
    configuration); one request computes it at a time."""
    try:
        h = prod.config_hash("rcal")
    except LookupError:
        h = None
    with _status_lock:
        c = _status_cache
        if c["rows"] is not None and c["key"] == h and time.time() - c["t"] < STATUS_TTL_S:
            return c["rows"]
        if h is None:
            rows: dict[str, dict[str, Any]] = {}
        else:
            df = prod.calibration_days()
            rows = {}
            for r in df.to_dict("records"):
                st = str(r["status"])
                rows[str(r["cal_day"])] = {
                    "status": st,
                    "reason": None if st == "done" else status_text(st, r.get("error")),
                    "s11_session": r["s11_session"] if isinstance(r["s11_session"], str) else None,
                    "issues": [str(i) for i in (r.get("issues") or [])],
                }
        _status_cache.update(key=h, t=time.time(), rows=rows)
        return rows


def unavailable_reason(statuses: dict[str, dict[str, Any]], key: str) -> str:
    """Why a day has no stored calibration."""
    st = statuses.get(key)
    if st is None:
        return "no calibration spectra (all four loads) on this day"
    return st["reason"] or "not stored yet"


def stored_rows(prod: Any) -> list[dict[str, Any]]:
    """The stored default calibrations, oldest first (summary columns only)."""
    try:
        df = prod.calibrations()
    except LookupError:  # no rcal products at all yet
        return []
    cols = ["cal_day", "s11_session", "t_ambient_k", "t_hot_k", "rms_ambient_k",
            "rms_hot_k", "rms_open_k", "rms_short_k", "t_start_unix", "t_end_unix",
            "t_load", "t_load_ns", "config_hash"]
    df = df[[c for c in cols if c in df.columns]].sort_values("cal_day")
    return [{k: _scalar(v) for k, v in r.items()} for r in df.to_dict("records")]


def day_list(prod: Any) -> dict[str, Any]:
    """Stored days, plus the calibration days without one (and why)."""
    rows = stored_rows(prod)
    have = {r["cal_day"] for r in rows}
    statuses = day_statuses(prod)
    missing = [{"cal_day": d, "status": st["status"], "reason": unavailable_reason(statuses, d)}
               for d, st in sorted(statuses.items()) if d not in have]
    return {"stored": rows, "missing": missing, "note": NOTE}


def latest_day(prod: Any) -> str:
    rows = stored_rows(prod)
    if not rows:
        raise LookupError("no stored receiver calibrations yet")
    return rows[-1]["cal_day"]


def stored_row(prod: Any, key: str) -> dict[str, Any] | None:
    return next((r for r in stored_rows(prod) if r["cal_day"] == key), None)


def product(prod: Any, key: str, config_hash: str,
            deployment: str = "edges3-mro") -> dict[str, Any] | None:
    """The stored product of a day: ``path``, ``sha256`` (of the file) and
    ``input_key`` (of its inputs: it changes when the pipeline reprocesses
    the day because an input changed). None if there is none."""
    df = prod.sql(
        "SELECT p.path, p.sha256, t.input_key FROM rcal_day s JOIN task t ON t.id = s.task_id"
        " JOIN product p ON p.task_id = t.id WHERE t.status = 'done' AND t.config_hash = ?"
        " AND s.deployment = ? AND s.cal_day = ? ORDER BY t.id DESC LIMIT 1",
        (config_hash, deployment, key),
    )
    return None if df.empty else {k: str(v) for k, v in df.iloc[0].items()}


def other_configs(prod: Any, default: str | None) -> list[dict[str, Any]]:
    """Stored rcal configurations other than the default (e.g. Alan mode, v1)."""
    df = prod.configs("rcal")
    return [{k: _scalar(v) for k, v in r.items()} for r in df.to_dict("records")
            if r["config_hash"] != default]


# ---------------------------------------------------------------------------
# JSON of one calibration
# ---------------------------------------------------------------------------
def _scalar(v: Any) -> Any:
    if isinstance(v, (np.floating, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def _arr(x: Any, decimals: int) -> list[float | None] | None:
    """A float array as a JSON list (NaN as null), rounded to keep it small."""
    if x is None:
        return None
    a = np.round(np.asarray(x, dtype=np.float64), decimals)
    return [v if math.isfinite(v) else None for v in a.tolist()]


def _plain(x: Any) -> Any:
    """Metrics/config as JSON (numpy scalars and arrays included)."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.ndarray):
        return _plain(x.tolist())
    return _scalar(x)


def _sig(x: Any, digits: int = 7) -> list[float | None] | None:
    """A float array as a JSON list to ``digits`` significant digits (for
    values far from 1, such as Q of the ambient load, ~5e-4)."""
    if x is None:
        return None
    return [float(f"{v:.{digits}g}") if math.isfinite(v) else None
            for v in np.asarray(x, dtype=np.float64).ravel().tolist()]


def calibration_json(cal: Any, info: dict[str, Any], source: str) -> dict[str, Any]:
    """Everything the page plots, at the product's resolution (3072 channels).

    Temperatures are rounded to 0.1 mK, S11s and the loss to 1e-7, Q to 7
    significant digits. Per load: the calibrated and known temperatures, the
    modelled S11, and (rcal version 3 and later; else None) the mean Q
    spectrum used in the fit, its per-cycle variance and the receiver
    (front-end) temperature during the spectrum (``frontend_c``: mean, min,
    max in degC); and (rcal v4 and later) the mean R = PL / (PLNS − PL) of the
    three-position switch, which depends on the receiver, not the load. ``source`` is ``"stored"`` or ``"computed"``.
    """
    t_load, t_load_ns = float(info["t_load"]), float(info["t_load_ns"])
    metrics = info.get("metrics") or {}

    def load(name: str) -> dict[str, Any]:
        d = (info.get("loads") or {}).get(name) or {}
        s11 = d.get("s11")
        return {
            "calibrated": _arr(d.get("calibrated"), 4),
            "known": _arr(d.get("known"), 4),
            "s11_re": _arr(None if s11 is None else np.real(s11), 7),
            "s11_im": _arr(None if s11 is None else np.imag(s11), 7),
            "q": _sig(d.get("q")),
            "r": _sig(d.get("r")),
            "q_variance": _sig(d.get("q_variance"), 4),
            "frontend_c": _plain(d.get("frontend_c")),
        }

    rcv = np.asarray(cal.receiver_s11)
    path = info.get("path")
    return {
        "source": source,
        "cal_day": info.get("cal_day"),
        "s11_session": info.get("s11_session"),
        "config_hash": info.get("config_hash"),
        "product": path.rsplit("/", 1)[-1] if isinstance(path, str) else None,
        "t_start_unix": _scalar(info.get("t_start_unix")),
        "t_end_unix": _scalar(info.get("t_end_unix")),
        "t_ambient_k": _scalar(info.get("t_ambient_k")),
        "t_hot_k": _scalar(info.get("t_hot_k")),
        "rms_k": {n: _scalar(info.get(f"rms_{'hot' if n == 'hot_load' else n}_k")) for n in LOADS},
        "t_load": t_load,
        "t_load_ns": t_load_ns,
        "method": metrics.get("method"),
        "n_readings": _plain(metrics.get("n_readings")),
        "spectra": _plain(metrics.get("spectra")),
        "issues": [str(i) for i in metrics.get("issues") or []],
        "config": _plain(info.get("config") or {}),
        "freq_mhz": _arr(info.get("freq_mhz"), 6),
        "loads": {n: load(n) for n in LOADS},
        "hot_load_loss": _arr(info.get("hot_load_loss"), 7),
        "nw": {
            "freq_mhz": _arr(cal.freqs.to_value("MHz"), 6),
            **{k: _arr(getattr(cal, k), 4) for k in ("Tsca", "Toff", "Tunc", "Tcos", "Tsin")},
        },
        "receiver_s11": {"re": _arr(np.real(rcv), 7), "im": _arr(np.imag(rcv), 7)},
    }


# ---------------------------------------------------------------------------
# Stored calibrations, cached
# ---------------------------------------------------------------------------
_CACHE_MAX = 16
_cache: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
_cache_lock = threading.Lock()


def stored_json(
    prod: Any, key: str, config_hash: str | None = None, deployment: str = "edges3-mro"
) -> dict[str, Any]:
    """The stored calibration of a day as JSON; ``LookupError`` if there is none.

    Cached by (day, config hash, product file and its checksum): a
    reprocessing makes a new product (or a new default hash), so a cached
    entry is never stale.
    """
    h = prod.config_hash("rcal", config_hash)
    p = product(prod, key, h, deployment)
    if p is None:
        raise LookupError(f"no receiver calibration for {key} ({h[:12]})")
    ck = (key, h, p["path"], p["sha256"])
    with _cache_lock:
        out = _cache.get(ck)
        if out is not None:
            _cache.move_to_end(ck)
    if out is None:
        cal, info = prod.calibrator(key, deployment=deployment, config_hash=h)
        out = calibration_json(cal, info, "stored")
        with _cache_lock:
            _cache[ck] = out
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
    return {**out, "is_default": h == default_config(prod)["hash"]}


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
    with _status_lock:
        _status_cache.update(key=None, t=0.0, rows=None)


def load_cycles(prod: Any, t0: float, t1: float) -> dict[str, Any]:
    """Per cycle of each calibration load in ``[t0, t1]`` (L1): the time, the
    band-median Q (60-90 MHz) and the dropout/outlier flags."""
    out: dict[str, Any] = {}
    for load in ("amb", "hot", "open", "short"):
        try:
            df = prod.l1_cycles(t0, t1, load=load)
        except LookupError:
            df = None
        if df is None or df.empty:
            out[load] = None
            continue
        df = df.sort_values("time_unix")
        out[load] = {
            "time_unix": _arr(df.time_unix, 1),
            "band_median_q": _sig(df.band_median_q),
            "flagged": [bool(a or b) for a, b in zip(df.dropout, df.outlier, strict=True)],
        }
    return out
