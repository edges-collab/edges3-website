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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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

#: Days the pipeline cannot calibrate whatever the settings, and why
#: (edges-database ISSUES).
KNOWN_GAPS = {
    "2022_316": "no temperature-log coverage of its calibration spectra",
    "2026_257": "its S11 session mixes VNA frequency grids (DATA_ISSUES #29)",
}
GAPS_NOTE = (
    "No calibrations were taken in 2024–2025. 2022_316 has no temperature log and "
    "2026_257's S11 session mixes VNA grids, so neither has a calibration. "
    "New days appear within about 12 hours of their data."
)
#: A day newer than this without a stored calibration is "not processed yet".
NEW_DAY_S = 2 * 86400

#: The settings the page offers, by section: (key, label, min, max, integer).
#: The others (``fit.Lh`` -1 only, ``fit.delay_sweep_ns``, ``dicke``: a
#: convention the solution does not depend on) keep their defaults.
FIELDS: Dict[str, List[Tuple[str, str, float, float, bool]]] = {
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


def default_config(prod: Any) -> Dict[str, Any]:
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


def effective(defaults: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """The full settings: ``params`` (overrides) applied to ``defaults``."""
    out = copy.deepcopy(defaults)
    for section, values in params.items():
        out.setdefault(section, {}).update(values)
    return out


def clean_params(params: Optional[Dict[str, Any]], defaults: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Check the page's settings; return only those that differ from ``defaults``.

    Raises ``ValueError`` (a 400) for unknown keys, non-numbers, values out of
    range or a fit band narrower than :data:`MIN_BAND_MHZ`.
    """
    out: Dict[str, Dict[str, Any]] = {}
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


def config_hash(settings: Dict[str, Any]) -> str:
    """The configuration hash of full settings (:func:`effective`)."""
    return rcal.config_hash(settings)


def form(defaults: Dict[str, Any]) -> Dict[str, Any]:
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


def _day_of(key: str) -> Optional[float]:
    try:
        return datetime.strptime(key, "%Y_%j").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def hopeless(key: str) -> Optional[str]:
    """Why no settings can calibrate this day, if that is known."""
    if key in KNOWN_GAPS:
        return KNOWN_GAPS[key]
    if key[:4] in ("2024", "2025"):
        return "no calibrations were taken in 2024–2025"
    return None


def gap_reason(key: str, now: Optional[float] = None) -> str:
    """Why a day has no stored calibration (as far as the site knows)."""
    why = hopeless(key)
    if why:
        return why
    t = _day_of(key)
    if t is not None and (now or time.time()) - t < NEW_DAY_S:
        return "not processed yet: new days appear within about 12 hours"
    return "the pipeline has no calibration for this day (its inputs are incomplete or unusable)"


def stored_rows(prod: Any) -> List[Dict[str, Any]]:
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


def day_list(prod: Any, catalog_days: List[str]) -> Dict[str, Any]:
    """Stored days, plus catalog calibration days without one (and why)."""
    rows = stored_rows(prod)
    have = {r["cal_day"] for r in rows}
    now = time.time()
    missing = [{"cal_day": d, "reason": gap_reason(d, now)}
               for d in sorted(set(catalog_days) - have)]
    return {"stored": rows, "missing": missing, "note": GAPS_NOTE}


def latest_day(prod: Any) -> str:
    rows = stored_rows(prod)
    if not rows:
        raise LookupError("no stored receiver calibrations yet")
    return rows[-1]["cal_day"]


def stored_row(prod: Any, key: str) -> Optional[Dict[str, Any]]:
    return next((r for r in stored_rows(prod) if r["cal_day"] == key), None)


def product(prod: Any, key: str, config_hash: str,
            deployment: str = "edges3-mro") -> Optional[Dict[str, Any]]:
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


def other_configs(prod: Any, default: Optional[str]) -> List[Dict[str, Any]]:
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


def _arr(x: Any, decimals: int) -> Optional[List[Optional[float]]]:
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


def _loss(info: Dict[str, Any]) -> Optional[np.ndarray]:
    """The hot-load loss on ``freq_mhz``. rcal version 1 (Alan mode) products
    hold it as a ``(n, 2)`` table of (frequency, loss)."""
    loss = info.get("hot_load_loss")
    if loss is None:
        return None
    loss = np.asarray(loss, dtype=np.float64)
    if loss.ndim == 2 and loss.shape[1] == 2:
        return np.interp(np.asarray(info["freq_mhz"], dtype=np.float64), loss[:, 0], loss[:, 1])
    return loss if loss.ndim == 1 else None


def calibration_json(cal: Any, info: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Everything the page plots, at the product's resolution (3072 channels).

    Temperatures are rounded to 0.1 mK, S11s and the loss to 1e-7.
    ``source`` is ``"stored"`` or ``"computed"``.
    """
    t_load, t_load_ns = float(info["t_load"]), float(info["t_load_ns"])
    metrics = info.get("metrics") or {}

    def load(name: str) -> Dict[str, Any]:
        d = (info.get("loads") or {}).get(name) or {}
        s11 = d.get("s11")
        return {
            "calibrated": _arr(d.get("calibrated"), 4),
            "known": _arr(d.get("known"), 4),
            "s11_re": _arr(None if s11 is None else np.real(s11), 7),
            "s11_im": _arr(None if s11 is None else np.imag(s11), 7),
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
        "hot_load_loss": _arr(_loss(info), 7),
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
_cache: "OrderedDict[Tuple[str, str, str], Dict[str, Any]]" = OrderedDict()
_cache_lock = threading.Lock()


def stored_json(
    prod: Any, key: str, config_hash: Optional[str] = None, deployment: str = "edges3-mro"
) -> Dict[str, Any]:
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
