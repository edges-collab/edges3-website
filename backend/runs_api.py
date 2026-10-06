"""
Calibration and observation runs
================================

Receiver calibrations come from edges-pipeline (see ``calibrations.py``): the
stored default of a day is read directly (fast); other settings are computed
by ``run_single_day.py --stage calibration`` (``rcal.calibrate_day``) as a
background job. Nights are background jobs too. A request names a
calibration by its day and settings, ``{"day": "2026_250", "params":
{"fit": {"cterms": 7}}}``; when ``rcal.config_hash(params)`` is the
pipeline's default the stored one is used, otherwise a computed one.

* a **calibration** job (non-default settings) is keyed by its day, the
  configuration hash of its settings and the edges-pipeline version;
* an **observation** (one night) is keyed by the night, the antenna S11
  session, its parameters, the calibration it uses and its input files.

The key (a 16-hex-digit hash) names the run directory,
``$EDGES_OUTPUT_ROOT/<kind>/<id>/``, so an identical request finds the
finished run instead of recomputing it. Each directory holds
``request.json``, ``params.json``, ``status.json``
(``queued``/``running``/``done``/``failed``), ``log.txt`` and, when done,
``result.json``. A calibration also has ``rcal.h5`` (the solution); an
observation ``inputs.json`` (from ``catalog_inputs``), ``calibration.json``
(which solution) and ``plots.npz``/``waterfalls.npz`` (served under
``/data/<kind>/<id>/``).

One worker thread runs the jobs one at a time (a calibration takes ~45 s and
~2 GB, a night ~0.5 min), niced and with few threads, so the shared server is
not overloaded. An observation whose computed calibration is not done yet
queues the calibration first. Only the newest :data:`MAX_RUNS_PER_KIND` runs
of each kind are kept.

Endpoints (prefix ``/api``)
---------------------------
GET  /calibrations                  days (stored, and without one: why), the
                                    settings form, other stored configurations
GET  /calibrations/stored/{day}     a day's stored calibration (JSON, 3072
                                    channels per array); ?config_hash= another
POST /calibrations/resolve          stored or computed, and the job's id/status
POST /calibrations                  start (or find) a computation
GET  /calibrations/{id}             a computation: status, result, log tail
GET  /observations/options          nights and antenna S11 sessions, defaults
POST /observations/resolve          inputs + id (+ status) of an observation
POST /observations                  start (or find) an observation
GET  /observations/{id}             status, inputs, result, log tail
GET  /runs/{kind}/{id}/download     zip of a finished run
GET  /runs/queue                    what the worker is doing
"""

from __future__ import annotations

import collections
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import calibrations
import catalog_inputs
import config
import products_api

log = logging.getLogger("edges.runs")

router = APIRouter(prefix="/api", tags=["runs"])

KINDS = ("calibration", "observation")
MAX_RUNS_PER_KIND = 20
RUN_TIMEOUT_S = 1800
#: run_single_day.EXIT_CANNOT_CALIBRATE: the pipeline cannot calibrate the day.
EXIT_CANNOT_CALIBRATE = 2
_ID = re.compile(r"^[0-9a-f]{16}$")
#: Never reuse a run whose inputs are younger than this (a file still being
#: written makes the key change between requests).
_NEW_DIR_GRACE_S = 120
_start_lock = threading.Lock()  # one start/retry at a time (FastAPI's threadpool)


def _dist_version(name: str) -> str:
    """A package's version, with its git commit when installed from git."""
    try:
        from importlib.metadata import distribution

        dist = distribution(name)
        commit = (json.loads(dist.read_text("direct_url.json") or "{}").get("vcs_info") or {}).get(
            "commit_id", "")
        return f"{dist.version}@{commit[:12]}" if commit else dist.version
    except Exception:
        return "?"


def code_version() -> str:
    """The stage script, edges-analysis and edges-pipeline versions: part of
    every run's key, so a code change never reuses outputs of the old code."""
    try:
        script = hashlib.sha256(Path(config.RUN_SCRIPT).read_bytes()).hexdigest()[:12]
    except OSError:
        script = "unknown"
    return f"{script}+edges-{_dist_version('edges-analysis')}+pipeline-{_dist_version('edges-pipeline')}"

#: Parameters of a night: default and allowed range (must match
#: run_single_day.DEFAULT_PARAMS, which is not imported to keep this light).
#: A calibration's settings are the pipeline's (``calibrations.clean_params``).
PARAMS: Dict[str, Dict[str, Tuple[float, float, float, bool]]] = {
    #                name: (default, min, max, integer)
    "observation": {
        "ant_s11_fstart": (58.0, 40.0, 200.0, False),
        "ant_s11_fstop": (105.0, 40.0, 200.0, False),
        "ant_s11_nterms": (12, 3, 30, True),
    },
}
_RANGES = {"observation": (("ant_s11_fstart", "ant_s11_fstop"),)}
#: Address-space limit of a calibration job (the pipeline's own, for the fit).
CALIBRATION_MEM_LIMIT_GB = 6.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _root(kind: str) -> Path:
    return config.OUTPUT_ROOT / kind


def _run_dir(kind: str, run_id: str) -> Path:
    if kind not in KINDS or not _ID.match(run_id):
        raise HTTPException(status_code=404, detail="no such run")
    return _root(kind) / run_id


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(path)


def clean_params(kind: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Defaults merged in, types and ranges checked (400 on bad input)."""
    out: Dict[str, Any] = {}
    params = params or {}
    unknown = set(params) - set(PARAMS[kind])
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown parameters: {sorted(unknown)}")
    for name, (default, lo, hi, integer) in PARAMS[kind].items():
        v = params.get(name)
        if v is None or v == "":
            v = default
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{name} must be a number") from None
        if not lo <= v <= hi or v != v:
            raise HTTPException(status_code=400, detail=f"{name} must be in [{lo}, {hi}]")
        if integer:
            if v != int(v):
                raise HTTPException(status_code=400, detail=f"{name} must be an integer")
            v = int(v)
        out[name] = v
    for a, b in _RANGES[kind]:
        if not out[a] < out[b]:
            raise HTTPException(status_code=400, detail=f"{a} must be below {b}")
    return out


def _digest(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def run_id(inputs: Dict[str, Any], params: Dict[str, Any], calibration: Dict[str, Any]) -> str:
    """A night's key: what it computes from, including the input files' versions
    and the calibration (stored: its product; computed: its job)."""
    return _digest({
        "code": code_version(),
        "kind": "observation",
        "dates": inputs["dates"],
        "params": params,
        "calibration": calibration,
        "files": inputs["files"],
        "versions": inputs.get("file_versions"),
    })


def calibration_id(day: str, config_hash: str) -> str:
    """A computed calibration's key. The pipeline takes the inputs from the
    catalog itself, so the day, the settings (their configuration hash) and
    the code identify it."""
    return _digest({"code": code_version(), "kind": "calibration", "day": day,
                    "config_hash": config_hash})


@contextmanager
def _catalog():
    """A per-request catalog connection; missing packages/DB -> 503, bad input -> 400."""
    try:
        with products_api._db_errors(), catalog_inputs.open_catalog() as cat:
            yield cat
    except catalog_inputs.InputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


# ---------------------------------------------------------------------------
# Status and the worker
# ---------------------------------------------------------------------------
class _Queue:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.pending: Deque[Tuple[str, str]] = collections.deque()
        self.active: Dict[Tuple[str, str], str] = {}  # (kind, id) -> queued|running
        self.worker: Optional[threading.Thread] = None

    def submit(self, kind: str, rid: str) -> None:
        with self.cond:
            if (kind, rid) in self.active:
                return
            self.active[(kind, rid)] = "queued"
            self.pending.append((kind, rid))
            _set_status(kind, rid, "queued")
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._loop, name="edges-runs", daemon=True)
                self.worker.start()
            self.cond.notify()

    def _loop(self) -> None:
        while True:
            with self.cond:
                while not self.pending:
                    self.cond.wait()
                kind, rid = self.pending.popleft()
                self.active[(kind, rid)] = "running"
            try:
                _execute(kind, rid)
            except Exception as e:  # never kill the worker
                log.exception("run %s/%s failed", kind, rid)
                _set_status(kind, rid, "failed", error=str(e))
            finally:
                with self.cond:
                    self.active.pop((kind, rid), None)
                try:
                    _evict(kind)
                except Exception:  # never kill the worker
                    log.exception("eviction failed")

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "running": [f"{k}/{i}" for (k, i), s in self.active.items() if s == "running"],
                "queued": [f"{k}/{i}" for k, i in self.pending],
            }

    def is_active(self, kind: str, rid: str) -> bool:
        with self.lock:
            return (kind, rid) in self.active


queue = _Queue()


def _set_status(kind: str, rid: str, state: str, **extra: Any) -> None:
    path = _run_dir(kind, rid) / "status.json"
    status = _read(path, {}) or {}
    status.update(state=state, updated_utc=_now(), pid=os.getpid(), **extra)
    status.setdefault(f"{state}_utc", _now())
    _write(path, status)


def _alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _orphaned(status: Dict[str, Any], kind: str, rid: str) -> bool:
    """A queued/running run that no live server process owns."""
    if status.get("state") not in ("queued", "running"):
        return False
    if queue.is_active(kind, rid):
        return False
    pid = status.get("pid")
    return pid == os.getpid() or not _alive(pid)


def status_of(kind: str, rid: str) -> Optional[Dict[str, Any]]:
    """The run's status, or None if there is no such run. A run left queued or
    running by a server process that has gone reads as ``failed`` (interrupted);
    one owned by another live server process keeps its state."""
    d = _root(kind) / rid
    status = _read(d / "status.json")
    if status is None:
        return None
    if _orphaned(status, kind, rid):
        status = {**status, "state": "failed", "error": "interrupted (server restarted)"}
    return status


def _limit_memory(gb: float) -> List[str]:
    """A command prefix capping the child's address space (``ulimit -v``)."""
    sh = shutil.which("sh")
    if not sh:
        return []
    return [sh, "-c", 'ulimit -v "$0" && exec "$@"', str(int(gb * 2**20))]


def _execute(kind: str, rid: str) -> None:
    d = _run_dir(kind, rid)
    request = _read(d / "request.json", {})
    cmd = [config.PYTHON, str(config.RUN_SCRIPT), "--stage", kind, "--run_dir", str(d),
           "--params", str(d / "params.json")]
    limit: List[str] = []
    if kind == "calibration":
        cmd += ["--day", request["day"]]
        if request.get("catalog_db"):
            cmd += ["--catalog_db", request["catalog_db"]]
        limit = _limit_memory(CALIBRATION_MEM_LIMIT_GB)
    else:
        cal_id = request.get("calibration_id")
        if cal_id:  # a computed calibration: it must have finished
            cal_status = status_of("calibration", cal_id) or {}
            if cal_status.get("state") != "done":
                raise RuntimeError(f"calibration {cal_id} is {cal_status.get('state', 'missing')}")
        cmd += ["--inputs", str(d / "inputs.json"), "--calibration", str(d / "calibration.json")]
    _set_status(kind, rid, "running")
    env = {**os.environ, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2",
           "OPENBLAS_NUM_THREADS": "2", "PYTHONUNBUFFERED": "1"}
    nice = [shutil.which("nice") or "nice", "-n", "10"] if shutil.which("nice") else []
    with open(d / "log.txt", "w") as logf:
        try:
            proc = subprocess.run(
                nice + limit + cmd, stdout=logf, stderr=subprocess.STDOUT, env=env,
                timeout=RUN_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            _set_status(kind, rid, "failed", error=f"timed out after {RUN_TIMEOUT_S} s")
            return
    if proc.returncode == EXIT_CANNOT_CALIBRATE and kind == "calibration":
        _set_status(kind, rid, "failed", error=_tail(d, 1).removeprefix("[run] "))
        return
    if proc.returncode != 0 or not (d / "result.json").exists():
        _set_status(kind, rid, "failed", error=f"exit {proc.returncode}: {_tail(d, 12)}")
        return
    _set_status(kind, rid, "done")


def _tail(d: Path, n: int = 40) -> str:
    try:
        lines = (d / "log.txt").read_text(errors="replace").splitlines()
    except OSError:
        return ""
    # progress bars of read_acq are noise
    lines = [ln.rsplit("\r", 1)[-1] for ln in lines if "lines/s]" not in ln]
    return "\n".join(lines[-n:])


def _evict(kind: str) -> None:
    """Keep the newest MAX_RUNS_PER_KIND finished runs of ``kind``.

    Never removes a run that is queued or running (in any live process), one
    being created (no status yet, or very new), or a calibration that such an
    observation depends on.
    """
    root = _root(kind)
    if not root.is_dir():
        return
    now = datetime.now().timestamp()
    keep, finished = set(), []
    for p in root.iterdir():
        if not (p.is_dir() and _ID.match(p.name)):
            continue
        st_path = p / "status.json"
        try:
            mtime = st_path.stat().st_mtime
        except OSError:
            keep.add(p.name)  # being created
            continue
        status = _read(st_path, {}) or {}
        if now - mtime < _NEW_DIR_GRACE_S or (
            status.get("state") in ("queued", "running") and not _orphaned(status, kind, p.name)
        ):
            keep.add(p.name)
        else:
            finished.append((mtime, p))
    if kind == "calibration":
        obs_root = _root("observation")
        for p in (obs_root.iterdir() if obs_root.is_dir() else []):
            status = _read(p / "status.json", {}) or {}
            if status.get("state") in ("queued", "running"):
                keep.add((_read(p / "request.json", {}) or {}).get("calibration_id"))
    finished.sort(reverse=True)
    for _, p in finished[MAX_RUNS_PER_KIND:]:
        if p.name not in keep:
            shutil.rmtree(p, ignore_errors=True)


# ---------------------------------------------------------------------------
# Creating runs
# ---------------------------------------------------------------------------
class CalibrationRequest(BaseModel):
    day: str = "Latest"
    #: the pipeline's settings, by section: {"fit": {"cterms": 7}}
    params: Dict[str, Any] = Field(default_factory=dict)


class ObservationRequest(BaseModel):
    night: str = "Latest"
    ant_s11: str = "Latest"
    params: Dict[str, Any] = Field(default_factory=dict)
    calibration: CalibrationRequest = Field(default_factory=CalibrationRequest)


def _prepare(kind: str, rid: str, files: Dict[str, Any]) -> None:
    """Create the run directory with its JSON files (``name -> content``)."""
    d = _run_dir(kind, rid)
    if (d / "status.json").exists():
        return
    d.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        _write(d / name, payload)


@contextmanager
def _pipeline():
    """The products reader; missing packages/DB -> 503, bad settings -> 400."""
    try:
        with products_api._db_errors():
            yield products_api.get_products()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


def resolve_calibration(req: CalibrationRequest) -> Dict[str, Any]:
    """Which calibration a request means: the stored default of the day, or a
    computation with these settings (with its job id and status).

    ``stored`` is the stored day's summary row, or None with ``unavailable``
    saying why (a stored calibration that does not exist is not an error
    here: the page says so).
    """
    with _pipeline() as prod:
        params = calibrations.clean_params(req.params)
        h = calibrations.config_hash(params)
        default = prod.default_config("rcal")
        if req.day in (None, "", calibrations.LATEST):
            try:
                day = calibrations.latest_day(prod)
            except LookupError as e:
                raise HTTPException(status_code=404, detail=str(e)) from None
        else:
            day = calibrations.day_key(req.day)
        out: Dict[str, Any] = {
            "day": day, "params": params, "config_hash": h, "default_hash": default,
            "is_default": h == default, "pipeline": _dist_version("edges-pipeline"),
        }
        if h == default:
            row = calibrations.stored_row(prod, day)
            return {**out, "source": "stored", "id": None, "status": None, "stored": row,
                    "unavailable": None if row else calibrations.gap_reason(day)}
        out["catalog_db"] = str(prod.settings.catalog_db)
    rid = calibration_id(day, h)
    return {**out, "source": "computed", "id": rid, "status": status_of("calibration", rid),
            "stored": None, "unavailable": None}


def _start_calibration(cal: Dict[str, Any]) -> None:
    _start("calibration", cal["id"], {
        "request.json": {"day": cal["day"], "config_hash": cal["config_hash"],
                         "catalog_db": cal.get("catalog_db")},
        "params.json": cal["params"],
    })


def _calibration_spec(cal: Dict[str, Any]) -> Dict[str, Any]:
    """What an observation needs to load its calibration (``calibration.json``)."""
    spec = {k: cal[k] for k in ("day", "config_hash", "source", "id", "params")}
    spec["cal_day"] = spec.pop("day")
    if cal["source"] == "stored":
        if not cal["stored"]:
            raise HTTPException(status_code=400, detail=(
                f"no stored calibration for {cal['day']}: {cal['unavailable']}"))
        spec.update(path=cal["stored"]["path"], s11_session=cal["stored"]["s11_session"])
    else:
        spec.update(path=str(_run_dir("calibration", cal["id"]) / "rcal.h5"), s11_session=None)
    return spec


def resolve_observation(req: ObservationRequest) -> Dict[str, Any]:
    cal = resolve_calibration(req.calibration)
    spec = _calibration_spec(cal)
    params = clean_params("observation", req.params)
    with _catalog() as cat:
        inputs = catalog_inputs.resolve_observation(cat, req.night, req.ant_s11)
    key = {k: spec[k] for k in ("cal_day", "config_hash", "source", "id")}
    key["product"] = os.path.basename(spec["path"]) if cal["source"] == "stored" else None
    rid = run_id(inputs, params, key)
    return {"id": rid, "params": params, "inputs": inputs,
            "status": status_of("observation", rid), "calibration": cal,
            "calibration_spec": spec}


def _start(kind: str, rid: str, files: Dict[str, Any]) -> None:
    with _start_lock:  # check, clean, prepare and submit as one step
        st = status_of(kind, rid)
        if st and st["state"] in ("done", "queued", "running"):
            return
        if st:  # failed or interrupted: retry from scratch
            shutil.rmtree(_run_dir(kind, rid), ignore_errors=True)
        _prepare(kind, rid, files)
        queue.submit(kind, rid)


def _describe(kind: str, rid: str) -> Dict[str, Any]:
    d = _run_dir(kind, rid)
    status = status_of(kind, rid)
    if status is None:
        raise HTTPException(status_code=404, detail="no such run")
    out = {
        "id": rid, "kind": kind, "status": status,
        "request": _read(d / "request.json"), "inputs": _read(d / "inputs.json"),
        "params": _read(d / "params.json"),
        "result": _read(d / "result.json") if status["state"] == "done" else None,
        "log_tail": _tail(d) if status["state"] != "done" else None,
        "base_url": f"/data/{kind}/{rid}/",
    }
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def _defaults(kind: str) -> Dict[str, Any]:
    return {k: v[0] for k, v in PARAMS[kind].items()}


@router.get("/calibrations")
def calibration_list() -> Dict[str, Any]:
    """Days with a stored calibration, catalog days without one (and why), the
    settings form, and the other stored configurations (for comparison)."""
    with _pipeline() as prod, _catalog() as cat:
        default = prod.default_config("rcal")
        return {
            **calibrations.day_list(prod, catalog_inputs.calibration_days(cat)),
            **calibrations.form(),
            "default_hash": default,
            "other_configs": calibrations.other_configs(prod, default),
            "pipeline": _dist_version("edges-pipeline"),
        }


@router.get("/calibrations/stored/{day}")
def calibration_stored(day: str, config_hash: Optional[str] = None) -> Dict[str, Any]:
    """A day's stored calibration (default settings unless ``config_hash``)."""
    if config_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", config_hash):
        raise HTTPException(status_code=400, detail="config_hash must be 64 hex digits")
    with _pipeline() as prod, products_api._heavy_slot():
        key = calibrations.day_key(day)
        try:
            return calibrations.stored_json(prod, key, config_hash)
        except LookupError as e:
            reason = calibrations.gap_reason(key) if config_hash is None else str(e)
            raise HTTPException(status_code=404, detail=f"no stored calibration for {key}: {reason}") from None


@router.post("/calibrations/resolve")
def calibration_resolve(req: CalibrationRequest) -> Dict[str, Any]:
    return resolve_calibration(req)


@router.post("/calibrations")
def calibration_start(req: CalibrationRequest) -> Dict[str, Any]:
    """Compute a calibration with non-default settings (the default is stored)."""
    cal = resolve_calibration(req)
    if cal["source"] == "stored":
        raise HTTPException(status_code=400, detail=(
            "these are the default settings: the stored calibration is used, nothing to compute"))
    _start_calibration(cal)
    return _describe("calibration", cal["id"])


@router.get("/calibrations/{rid}")
def calibration_get(rid: str) -> Dict[str, Any]:
    return _describe("calibration", rid)


@router.get("/observations/options")
def observation_options() -> Dict[str, Any]:
    with _catalog() as cat:
        return {**catalog_inputs.observation_options(cat), "defaults": _defaults("observation")}


@router.post("/observations/resolve")
def observation_resolve(req: ObservationRequest) -> Dict[str, Any]:
    return resolve_observation(req)


@router.post("/observations")
def observation_start(req: ObservationRequest) -> Dict[str, Any]:
    r = resolve_observation(req)
    cal = r["calibration"]
    done = (r["status"] or {}).get("state") == "done"
    if cal["source"] == "computed" and not done:  # a finished night needs nothing
        _start_calibration(cal)
    _start("observation", r["id"], {
        "request.json": {"night": req.night, "ant_s11": req.ant_s11, "params": r["params"],
                         "calibration_id": cal["id"]},
        "inputs.json": r["inputs"],
        "params.json": r["params"],
        "calibration.json": r["calibration_spec"],
    })
    return {**_describe("observation", r["id"]), "calibration_id": cal["id"]}


@router.get("/observations/{rid}")
def observation_get(rid: str) -> Dict[str, Any]:
    return _describe("observation", rid)


@router.get("/runs/queue")
def run_queue() -> Dict[str, Any]:
    return queue.snapshot()


@router.get("/runs/{kind}/{rid}/download")
def run_download(kind: str, rid: str) -> StreamingResponse:
    d = _run_dir(kind, rid)
    if (status_of(kind, rid) or {}).get("state") != "done":
        raise HTTPException(status_code=404, detail="run not finished")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.suffix != ".tmp" and "s11_cache" not in p.parts:
                zf.write(p, p.relative_to(d).as_posix())
    buf.seek(0)
    name = f"edges_{kind}_{rid}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


def summary() -> List[str]:
    """For the status page."""
    return [f"{k}: {len(list(_root(k).glob('*/status.json')))} runs" for k in KINDS]
