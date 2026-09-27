"""
Calibration and observation runs
================================

Runs of ``run_single_day.py`` (see its docstring) as background jobs:

* a **calibration** is keyed by its day, S11 session, parameters and the
  versions of its input files;
* an **observation** (one night) is keyed by the night, the antenna S11
  session, its parameters, the calibration it uses and its input files.

The key (a 16-hex-digit hash) names the run directory,
``$EDGES_OUTPUT_ROOT/<kind>/<id>/``, so an identical request finds the
finished run instead of recomputing it. Each directory holds
``request.json``, ``inputs.json`` (from ``catalog_inputs``), ``params.json``,
``status.json`` (``queued``/``running``/``done``/``failed``), ``log.txt`` and,
when done, ``result.json`` with ``plots.npz``/``waterfalls.npz`` (served under
``/data/<kind>/<id>/``).

One worker thread runs the jobs one at a time (a calibration takes ~1 min,
a night ~0.5 min), niced and with few threads, so the shared server is not
overloaded. An observation whose calibration is not done yet queues the
calibration first. Only the newest :data:`MAX_RUNS_PER_KIND` runs of each
kind are kept.

Endpoints (prefix ``/api``)
---------------------------
GET  /calibrations/options          days and S11 sessions, default parameters
POST /calibrations/resolve          inputs + id (+ status) of a calibration
POST /calibrations                  start (or find) a calibration
GET  /calibrations/{id}             status, inputs, result, log tail
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

import catalog_inputs
import config
import products_api

log = logging.getLogger("edges.runs")

router = APIRouter(prefix="/api", tags=["runs"])

KINDS = ("calibration", "observation")
MAX_RUNS_PER_KIND = 20
RUN_TIMEOUT_S = 1800
_ID = re.compile(r"^[0-9a-f]{16}$")
#: The pipeline code is part of every run's key, so a code change never
#: reuses outputs of the old code.
try:
    CODE_VERSION = hashlib.sha256(config.RUN_SCRIPT.read_bytes()).hexdigest()[:12]
except OSError:
    CODE_VERSION = "unknown"

#: Parameters of each stage: default and allowed range (must match
#: run_single_day.DEFAULT_PARAMS, which is not imported to keep this light).
PARAMS: Dict[str, Dict[str, Tuple[float, float, float, bool]]] = {
    #                name: (default, min, max, integer)
    "calibration": {
        "cterms": (6, 1, 30, True),
        "wterms": (5, 1, 30, True),
        "fstart": (40.0, 0.0, 200.0, False),
        "fstop": (190.0, 0.0, 200.0, False),
        "wfstart": (40.0, 0.0, 200.0, False),
        "wfstop": (190.0, 0.0, 200.0, False),
    },
    "observation": {
        "ant_s11_fstart": (58.0, 40.0, 200.0, False),
        "ant_s11_fstop": (105.0, 40.0, 200.0, False),
        "ant_s11_nterms": (12, 3, 30, True),
    },
}
_RANGES = {"calibration": (("fstart", "fstop"), ("wfstart", "wfstop")),
           "observation": (("ant_s11_fstart", "ant_s11_fstop"),)}


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


def run_id(kind: str, inputs: Dict[str, Any], params: Dict[str, Any], calibration_id: Optional[str] = None) -> str:
    """The run's key: what it computes from, including the input files' versions."""
    return _digest({
        "code": CODE_VERSION,
        "kind": kind,
        "dates": inputs["dates"],
        "params": params,
        "calibration": calibration_id,
        "files": inputs["files"],
        "versions": inputs.get("file_versions"),
        "temperatures": inputs.get("temperatures"),
    })


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
                _evict(kind)

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
    status.update(state=state, updated_utc=_now(), **extra)
    status.setdefault(f"{state}_utc", _now())
    _write(path, status)


def status_of(kind: str, rid: str) -> Optional[Dict[str, Any]]:
    """The run's status, or None if there is no such run. A run left queued or
    running by a previous server process reads as ``failed`` (interrupted)."""
    d = _root(kind) / rid
    status = _read(d / "status.json")
    if status is None:
        return None
    if status.get("state") in ("queued", "running") and not queue.is_active(kind, rid):
        status = {**status, "state": "failed", "error": "interrupted (server restarted)"}
    return status


def _execute(kind: str, rid: str) -> None:
    d = _run_dir(kind, rid)
    request = _read(d / "request.json", {})
    cmd = [config.PYTHON, str(config.RUN_SCRIPT), "--stage", kind, "--run_dir", str(d),
           "--inputs", str(d / "inputs.json"), "--params", str(d / "params.json")]
    if kind == "observation":
        cal_id = request["calibration_id"]
        cal_status = status_of("calibration", cal_id) or {}
        if cal_status.get("state") != "done":
            raise RuntimeError(f"calibration {cal_id} is {cal_status.get('state', 'missing')}")
        cmd += ["--calibration_dir", str(_run_dir("calibration", cal_id))]
    _set_status(kind, rid, "running")
    env = {**os.environ, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2",
           "OPENBLAS_NUM_THREADS": "2", "PYTHONUNBUFFERED": "1"}
    with open(d / "log.txt", "w") as logf:
        try:
            proc = subprocess.run(
                cmd, stdout=logf, stderr=subprocess.STDOUT, env=env, timeout=RUN_TIMEOUT_S,
                preexec_fn=lambda: os.nice(10),
            )
        except subprocess.TimeoutExpired:
            _set_status(kind, rid, "failed", error=f"timed out after {RUN_TIMEOUT_S} s")
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
    """Keep the newest MAX_RUNS_PER_KIND runs (never active ones or their calibrations)."""
    root = _root(kind)
    if not root.is_dir():
        return
    keep = set()
    with queue.lock:
        for (k, i) in queue.active:
            if k == kind:
                keep.add(i)
            elif kind == "calibration" and k == "observation":
                cal = (_read(_root(k) / i / "request.json", {}) or {}).get("calibration_id")
                keep.add(cal)
    runs = sorted(
        (p for p in root.iterdir() if p.is_dir() and _ID.match(p.name)),
        key=lambda p: (p / "status.json").stat().st_mtime if (p / "status.json").exists() else 0,
        reverse=True,
    )
    for p in runs[MAX_RUNS_PER_KIND:]:
        if p.name not in keep:
            shutil.rmtree(p, ignore_errors=True)


# ---------------------------------------------------------------------------
# Creating runs
# ---------------------------------------------------------------------------
class CalibrationRequest(BaseModel):
    cal: str = "Latest"
    s11: str = "Latest"
    params: Dict[str, Any] = Field(default_factory=dict)


class ObservationRequest(BaseModel):
    night: str = "Latest"
    ant_s11: str = "Latest"
    params: Dict[str, Any] = Field(default_factory=dict)
    calibration: CalibrationRequest = Field(default_factory=CalibrationRequest)


def _prepare(kind: str, rid: str, inputs: Dict[str, Any], params: Dict[str, Any], request: Dict[str, Any]) -> None:
    d = _run_dir(kind, rid)
    if (d / "status.json").exists():
        return
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "request.json", request)
    _write(d / "inputs.json", inputs)
    _write(d / "params.json", params)


def resolve_calibration(req: CalibrationRequest) -> Dict[str, Any]:
    params = clean_params("calibration", req.params)
    with _catalog() as cat:
        inputs = catalog_inputs.resolve_calibration(cat, req.cal, req.s11)
    rid = run_id("calibration", inputs, params)
    return {"id": rid, "params": params, "inputs": inputs, "status": status_of("calibration", rid)}


def resolve_observation(req: ObservationRequest) -> Dict[str, Any]:
    cal = resolve_calibration(req.calibration)
    params = clean_params("observation", req.params)
    with _catalog() as cat:
        inputs = catalog_inputs.resolve_observation(cat, req.night, req.ant_s11)
    rid = run_id("observation", inputs, params, cal["id"])
    return {"id": rid, "params": params, "inputs": inputs,
            "status": status_of("observation", rid), "calibration": cal}


def _start(kind: str, resolved: Dict[str, Any], request: Dict[str, Any]) -> None:
    rid = resolved["id"]
    st = status_of(kind, rid)
    if st and st["state"] in ("done", "queued", "running"):
        return
    if st:  # failed or interrupted: retry from scratch
        shutil.rmtree(_run_dir(kind, rid), ignore_errors=True)
    _prepare(kind, rid, resolved["inputs"], resolved["params"], request)
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


@router.get("/calibrations/options")
def calibration_options() -> Dict[str, Any]:
    with _catalog() as cat:
        return {**catalog_inputs.calibration_options(cat), "defaults": _defaults("calibration")}


@router.post("/calibrations/resolve")
def calibration_resolve(req: CalibrationRequest) -> Dict[str, Any]:
    return resolve_calibration(req)


@router.post("/calibrations")
def calibration_start(req: CalibrationRequest) -> Dict[str, Any]:
    r = resolve_calibration(req)
    _start("calibration", r, {"cal": req.cal, "s11": req.s11, "params": r["params"]})
    return _describe("calibration", r["id"])


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
    _start("calibration", cal, {"cal": req.calibration.cal, "s11": req.calibration.s11,
                                "params": cal["params"]})
    _start("observation", r, {"night": req.night, "ant_s11": req.ant_s11,
                              "params": r["params"], "calibration_id": cal["id"]})
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
