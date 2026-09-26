"""
EDGES-3 Web API
===============

Endpoints
---------
GET  /available_dates        List of dates per category (cached on disk)
GET  /manifest.json          The manifest for the currently displayed run
GET  /latest_run             JSON pointer to the currently displayed run
POST /run_pipeline           Trigger a user-run with custom dates / parameters
POST /save_outputs           Bundle the current outputs into a downloadable zip
GET  /download/<name>        Download a previously saved zip
GET  /health                 Liveness probe
GET  /api/...                Read-only catalog/products endpoints (see products_api.py)

Static files
------------
``OUTPUT_ROOT`` is mounted under ``/data`` (and legacy paths
``/runs``, ``/saved`` for backward compatibility with older manifests)
so that ``/data/manifest.json``, ``/data/runs/<id>/...``,
``/data/saved/<name>.zip``, etc. are directly fetchable by the browser.
The built SPA is served from ``frontend/dist/`` at ``/`` with a
catch-all fallback that returns ``index.html`` for React Router paths
like ``/Select``.

Concurrency
-----------
A single ``RunLock`` serialises pipeline runs so that two simultaneous
clicks cannot clobber each other.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import products_api  # noqa: E402
import scan_dates  # noqa: E402
from io_utils import compute_run_hash  # noqa: E402


log = logging.getLogger("edges.api")

# Ensure output directories exist before StaticFiles mounts at import time.
config.ensure_dirs()


# ---------------------------------------------------------------------------
# Pipeline parameter schema (mirrors Select.tsx)
# ---------------------------------------------------------------------------
# S11 plots and calibrated spectra use 40–190 MHz so the EDGES science
# band (50–190 MHz) is visible with band-edge context on either side.
PIPELINE_DEFAULTS: Dict[str, Any] = {
    "cterms": 6,
    "wterms": 5,
    "fstart": 40.0,
    "fstop": 190.0,
    "wfstart": 40.0,
    "wfstop": 190.0,
    "save_2d_npz": False,
}

NUMERIC_PIPELINE_KEYS = (
    "cterms", "wterms", "fstart", "fstop", "wfstart", "wfstop",
)


class RunRequest(BaseModel):
    dates: Dict[str, str] = Field(
        default_factory=lambda: {"cal": "Latest", "s11": "Latest", "raw": "Latest"}
    )
    parameters: Dict[str, Any] = Field(default_factory=dict)


class SaveRequest(BaseModel):
    include_2d: bool = False
    label: Optional[str] = None


# ---------------------------------------------------------------------------
# Single-process run lock
# ---------------------------------------------------------------------------
class RunLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: Optional[str] = None

    def acquire(self, holder: str, timeout: float = 0.0) -> bool:
        if timeout <= 0:
            got = self._lock.acquire(blocking=False)
        else:
            got = self._lock.acquire(timeout=timeout)
        if got:
            self._holder = holder
        return got

    def release(self, holder: str) -> None:
        if self._holder == holder:
            self._holder = None
            self._lock.release()

    def status(self) -> Dict[str, Any]:
        return {
            "busy": self._holder is not None,
            "holder": self._holder,
        }


run_lock = RunLock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def _resolve_dates(dates_in: Dict[str, str], available: Dict[str, List[str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, list_key in (("cal", "calibration"), ("s11", "s11"), ("raw", "raw")):
        v = dates_in.get(key, "Latest") or "Latest"
        if v == "Latest":
            choices = available.get(list_key, [])
            if not choices:
                raise HTTPException(status_code=400, detail=f"No {list_key} dates available")
            out[key] = choices[-1]
        else:
            if v not in available.get(list_key, []):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown {list_key} date: {v!r}",
                )
            out[key] = v
    return out


def _ensure_dates_scanned(force: bool = False) -> Dict[str, List[str]]:
    """Refresh the on-disk dates cache if it's missing or older than 1 hour."""
    cache = config.AVAILABLE_DATES_FILE
    stale = True
    if cache.exists() and not force:
        age = time.time() - cache.stat().st_mtime
        stale = age > 3600  # 1 hour
    if stale:
        dates = scan_dates.scan_all(config.RAW_DATA_ROOT)
        scan_dates.write_results(dates, cache)
    payload = _read_json(cache, {})
    return {
        "calibration": payload.get("calibration", []),
        "s11": payload.get("s11", []),
        "raw": payload.get("raw", []),
    }


# ---------------------------------------------------------------------------
# User-cache dedup
# ---------------------------------------------------------------------------
# A user-triggered run with the same (dates, parameters) hash as a
# previous run can be reused — we just copy the prior outputs into a new
# timestamped directory rather than recomputing. Only the immediately
# previous run is preserved (single-entry cache), so the user never has
# to think about stale data.
USER_CACHE_DIR: Path = config.OUTPUT_ROOT / "user_cache"


def _prune_old_runs(keep: Optional[str] = None) -> int:
    """Delete every ``runs/<id>`` directory except ``keep``.

    Called *after* a successful run (never before it), so a pipeline
    failure leaves the previously displayed outputs, manifest and
    ``latest_run.json`` intact instead of destroying them first.
    ``saved/``, ``user_cache/`` and ``available_dates.json`` are never
    touched.
    """
    removed = 0
    runs_root = config.RUNS_DIR
    if not runs_root.exists():
        return 0
    for entry in runs_root.iterdir():
        if entry.name == keep:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        removed += 1
    return removed


def _stash_previous_user_run() -> Optional[str]:
    """Copy the most recent ``runs/<run_id>/`` directory into
    ``user_cache/<previous_hash>/`` (single entry, evicts any older
    entry) so a later identical click can dedup against it.

    The hash is the last ``_``-separated component of the run_id
    (run_id format: ``user_YYYYMMDD_HHMMSS_<hash16>``).
    """
    runs_root = config.RUNS_DIR
    if not runs_root.exists():
        return None
    candidates = sorted(
        (p for p in runs_root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    previous = candidates[0]
    parts = previous.name.split("_")
    if len(parts) < 4 or len(parts[-1]) != 16:
        return None
    prev_hash = parts[-1]

    USER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Single-entry cache: evict any prior entry before stashing the new one.
    for stale in USER_CACHE_DIR.iterdir():
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
    target_dir = USER_CACHE_DIR / prev_hash
    shutil.copytree(previous, target_dir)
    src_manifest = config.OUTPUT_ROOT / "manifest.json"
    if src_manifest.exists():
        shutil.copy(src_manifest, target_dir / "manifest.json")
    log.info("Stashed previous user run %s -> %s", previous.name, target_dir)
    return prev_hash


def _find_existing_run(run_hash: str) -> Optional[Path]:
    """Look for a previous run with matching hash that can be reused.

    The hash already encodes ``save_2d_npz`` (see ``compute_run_hash``)
    so two requests with different 2D-ness never match.
    """
    cached = USER_CACHE_DIR / run_hash
    if cached.exists() and (cached / "manifest.json").exists():
        return cached
    return None


def _write_latest(run_id: str, dates: Dict[str, str]) -> None:
    """Refresh the ``latest_run.json`` pointer."""
    actual_temps: Dict[str, Dict[str, Any]] = {}
    src_manifest = config.OUTPUT_ROOT / "manifest.json"
    if src_manifest.exists():
        try:
            with open(src_manifest, "r") as f:
                m = json.load(f)
            for plot in m.get("plots", []):
                if plot.get("type") == "multi" and plot.get("id", "").endswith("_vs_actual"):
                    load = plot["id"].removesuffix("_vs_actual")
                    actual_temps[load] = {
                        "time": plot.get("time"),
                        "temperature_k": plot.get("temperature_k"),
                    }
        except Exception:
            pass
    run_dir = config.RUNS_DIR / run_id
    payload: Dict[str, Any] = {
        "source": "user",
        "run_id": run_id,
        "dates": dates,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if actual_temps:
        payload["actual_temperatures"] = actual_temps
    # Only the current run counts — rglob-ing OUTPUT_ROOT would also match
    # the stashed previous run in user_cache/ and report stale 2D data.
    payload["has_2d"] = run_dir.exists() and any(run_dir.rglob("*_2d.npz"))

    # Surface any pipeline warnings (e.g. S11 grid resampling) so the
    # frontend banner can show them. The warnings file is written by
    # ``run_single_day.align_s11_grids`` inside the run directory.
    warnings_file = run_dir / "s11_grid_warnings.json"
    if warnings_file.exists():
        try:
            with open(warnings_file, "r") as wf:
                doc = json.load(wf)
            # ``doc`` has ``{"reference": {...}, "warnings": [...]}``.
            # Frontend expects a flat array of warning envelopes, so
            # we return both fields as separate entries with the same
            # ``type`` discriminator.
            envelope = []
            ref = doc.get("reference")
            if ref:
                envelope.append(ref)
            envelope.extend(doc.get("warnings", []))
            payload["warnings"] = envelope
        except Exception:
            pass

    _write_json(config.LATEST_RUN_FILE, payload)


def _read_actual_temperatures() -> Dict[str, Dict[str, Any]]:
    """Read the per-load actual temperatures from the latest_run file."""
    data = _read_json(config.LATEST_RUN_FILE, {})
    temps = data.get("actual_temperatures", {})
    return temps if isinstance(temps, dict) else {}


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------
# Guard against a hung EDGES run pinning the run lock (and a threadpool
# thread) forever.
PIPELINE_TIMEOUT_S = 1800


def _execute_subprocess(cmd: List[str]) -> None:
    log.info("Running pipeline: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PIPELINE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as exc:
        log.error("Pipeline timed out after %ss", PIPELINE_TIMEOUT_S)
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline timed out after {PIPELINE_TIMEOUT_S}s",
        ) from exc
    if result.returncode != 0:
        log.error("Pipeline failed: %s", result.stderr[-4000:])
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline failed (exit {result.returncode}): {result.stderr[-2000:]}",
        )


def _build_pipeline_cmd(
    resolved: Dict[str, str],
    merged: Dict[str, Any],
    output_root: Path,
    run_dir: Path,
    run_hash: str,
) -> List[str]:
    cmd = [
        config.PYTHON, str(config.RUN_SCRIPT),
        "--cal_date", resolved["cal"],
        "--s11_date", resolved["s11"],
        "--spec_date", resolved["raw"],
        "--rawdata_root", str(config.RAW_DATA_ROOT),
        "--output_root", str(output_root),
        "--run_dir", str(run_dir),
        "--temperature_log", str(config.TEMPERATURE_LOG_FILE),
        "--source", "user",
        "--run_hash", run_hash,
    ]
    for k in NUMERIC_PIPELINE_KEYS:
        if k in merged:
            cmd.extend([f"--{k}", str(merged[k])])
    if merged.get("save_2d_npz"):
        cmd.append("--save_2d_npz")
    return cmd


def _copy_run_to(src: Path, dst: Path) -> None:
    """Copy a previously-produced run directory to a new location."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _build_run_id(run_hash: str) -> str:
    return f"user_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{run_hash}"


def run_pipeline(
    dates: Dict[str, str],
    parameters: Dict[str, Any],
    wipe_target_first: bool = True,
) -> Dict[str, Any]:
    """Run the pipeline (single user-triggered tree) with dedup.

    Dedup rules:
      * Hash from ``(dates, parameters)`` is the dedup key.
      * A previous user run is preserved in ``OUTPUT_ROOT/user_cache/`` so
        it can be reused by a later identical click.
      * Reusing copies the previous outputs into ``OUTPUT_ROOT/runs/<id>/``
        under a new timestamped run id, then rewrites the manifest pointing
        at that run.
    """
    available = _ensure_dates_scanned()
    resolved = _resolve_dates(dates, available)
    merged = {**PIPELINE_DEFAULTS, **parameters}
    # A cleared/empty input field arrives as ``null`` (JSON has no NaN);
    # falling back to the defaults keeps a stray ``None`` from being
    # stringified into the CLI ("--fstart None" -> argparse SystemExit).
    for k in NUMERIC_PIPELINE_KEYS:
        v = merged.get(k)
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            merged[k] = PIPELINE_DEFAULTS[k]

    run_hash = compute_run_hash(resolved, merged)

    config.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    # ---- Dedup ----------------------------------------------------------
    if wipe_target_first:
        _stash_previous_user_run()

    existing = _find_existing_run(run_hash)

    run_id = _build_run_id(run_hash)
    run_dir = config.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    reused_from: Optional[str] = None
    if existing is not None:
        existing_path = existing
        log.info(
            "Reusing cached user run for hash=%s from %s",
            run_hash, existing_path,
        )
        _copy_run_to(existing_path, run_dir)
        old_manifest: Dict[str, Any] = {}
        for candidate in (
            existing_path / "manifest.json",
            existing_path.parent / "manifest.json",
            existing_path.parent.parent / "manifest.json",
        ):
            try:
                with open(candidate, "r") as f:
                    old_manifest = json.load(f)
                if old_manifest.get("plots"):
                    break
            except Exception:
                continue
        new_manifest = dict(old_manifest)
        new_manifest["latest_run"] = run_dir.name
        new_manifest["source"] = "user"
        new_manifest["dates"] = old_manifest.get("dates", resolved)
        new_manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
        new_manifest["reused_from"] = {
            "source": "user",
            "path": str(existing_path),
        }
        # The stashed manifest's filePaths reference the run id it was
        # captured from (e.g. ``runs/user_20260916_160612_7aede…/…``) — that
        # directory was pruned, so rewrite the run-id prefix to the new one.
        # ``existing_path.name`` is only the hash, NOT the run id, so it
        # must come from the manifest itself.
        old_run_id = old_manifest.get("latest_run") or existing_path.name
        old_prefixes = (
            f"runs/{old_run_id}",
            f"{DATA_PREFIX}/runs/{old_run_id}",
        )
        new_prefix = f"runs/{run_dir.name}"
        for plot in new_manifest.get("plots", []):
            for key in ("filePath", "filePath1", "filePath2"):
                v = plot.get(key)
                if not isinstance(v, str):
                    continue
                for old_prefix in old_prefixes:
                    if v.startswith(old_prefix):
                        plot[key] = new_prefix + v[len(old_prefix):]
                        break
        out_manifest = config.OUTPUT_ROOT / "manifest.json"
        with open(out_manifest, "w") as f:
            json.dump(new_manifest, f, indent=2)
        log.info("Reused manifest written to %s", out_manifest)
        reused_from = "user"
    else:
        # ---- Fresh pipeline run -----------------------------------------
        cmd = _build_pipeline_cmd(resolved, merged, config.OUTPUT_ROOT, run_dir, run_hash)
        _execute_subprocess(cmd)

    _write_latest(run_id, resolved)

    # Prune old runs only now — a failed pipeline above would have raised
    # and left the previous outputs untouched.
    if wipe_target_first:
        _prune_old_runs(keep=run_id)

    return {
        "success": True,
        "source": "user",
        "run_id": run_id,
        "dates": resolved,
        "parameters": merged,
        "manifest": f"{DATA_PREFIX}/manifest.json",
        "run_hash": run_hash,
        "reused_from": reused_from,
    }


# ---------------------------------------------------------------------------
# Saving outputs as a downloadable zip
# ---------------------------------------------------------------------------
def _zip_run_outputs(run_dir: Path, manifest_path: Path, include_2d: bool) -> io.BytesIO:
    """Create an in-memory zip of one run directory plus the manifest.

    Only the currently displayed run is bundled — ``user_cache`` (the
    stashed previous run), ``saved/`` and other runs are excluded, and
    previously saved zips are never nested inside new ones. If
    ``include_2d`` is False, files ending in ``_2d.npz`` are skipped.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in run_dir.rglob("*"):
            if not path.is_file():
                continue
            # A run dir may contain a stale manifest.json copy (from a
            # dedup stash); the fresh one is added separately below.
            if path.name == "manifest.json":
                continue
            if not include_2d and path.name.endswith("_2d.npz"):
                continue
            arcname = Path("runs") / run_dir.name / path.relative_to(run_dir)
            zf.write(path, arcname.as_posix())
        if manifest_path.exists():
            zf.write(manifest_path, "manifest.json")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="EDGES-3 Web API")
app.add_middleware(
    CORSMiddleware,
    # Never "*" — an open CORS policy lets any website trigger the
    # unauthenticated, state-changing endpoints below. Loopback origins
    # (Vite dev server / uvicorn / SSH tunnel) are always allowed; add
    # real frontend origins via EDGES_ALLOWED_ORIGINS.
    allow_origins=config.ALLOWED_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Read-only /api/* endpoints over the catalog and pipeline products
# (the "Last night" page). They work only when edges-catalog and
# edges-pipeline are installed; otherwise they return 503.
app.include_router(products_api.router)


@app.on_event("startup")
def _startup() -> None:
    config.ensure_dirs()
    _ensure_dates_scanned()


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "time": datetime.now().isoformat()}


@app.get("/available_dates")
def available_dates(force: bool = False) -> Dict[str, List[str]]:
    return _ensure_dates_scanned(force=force)


@app.get("/latest_run")
def latest_run() -> Dict[str, Any]:
    return _read_json(config.LATEST_RUN_FILE, {
        "source": None, "run_id": None, "dates": {}, "generated_at": None,
    })


@app.get("/pipeline/status")
def pipeline_status() -> Dict[str, Any]:
    return {
        "raw_data_root_exists": config.RAW_DATA_ROOT.exists(),
        "lock": run_lock.status(),
    }


@app.post("/run_pipeline")
def run_pipeline_endpoint(req: RunRequest) -> Dict[str, Any]:
    """Trigger a pipeline run with the supplied dates and parameters.

    Dates default to ``"Latest"`` (the most recent available date for
    each category). Parameters override the defaults of
    ``cterms``, ``wterms``, ``fstart``, ``fstop``, ``wfstart``,
    ``wfstop``, and ``save_2d_npz``.

    A request with the same (dates, parameters) hash as a previous run
    is deduped: the prior outputs are copied into a fresh timestamped
    run directory instead of being recomputed.
    """
    if not run_lock.acquire(holder="user"):
        raise HTTPException(
            status_code=409, detail="Pipeline already running; try again later"
        )
    try:
        return run_pipeline(
            dates=req.dates,
            parameters=req.parameters,
            wipe_target_first=True,
        )
    finally:
        run_lock.release(holder="user")


@app.post("/save_outputs")
def save_outputs(req: SaveRequest) -> Dict[str, Any]:
    """Zip the current run's outputs and write to ``OUTPUT_ROOT/saved/<label>.zip``."""
    label = (req.label or datetime.now().strftime("run_%Y%m%d_%H%M%S")).strip()
    safe_label = re.sub(r"[^A-Za-z0-9._-]", "_", label)[:64]
    out_path = config.SAVED_DIR / f"{safe_label}.zip"
    config.SAVED_DIR.mkdir(parents=True, exist_ok=True)

    latest = _read_json(config.LATEST_RUN_FILE, {})
    run_id = latest.get("run_id") if isinstance(latest, dict) else None
    if not run_id:
        raise HTTPException(status_code=400, detail="No run to save; run the pipeline first")
    run_dir = config.RUNS_DIR / str(run_id)
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Run directory not found: {run_id}")

    buf = _zip_run_outputs(
        run_dir, config.OUTPUT_ROOT / "manifest.json", include_2d=req.include_2d
    )
    with open(out_path, "wb") as f:
        f.write(buf.getvalue())

    latest = _read_json(config.LATEST_RUN_FILE, {})
    return {
        "success": True,
        "label": safe_label,
        "include_2d": req.include_2d,
        "size_bytes": out_path.stat().st_size,
        "download_url": f"/saved/{safe_label}.zip",
        "latest_run": latest,
    }


@app.get("/download/{name}")
def download(name: str) -> FileResponse:
    safe = (config.SAVED_DIR / name).resolve()
    if config.SAVED_DIR.resolve() not in safe.parents and safe != config.SAVED_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid path")
    if not safe.exists() or not safe.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(safe, filename=name)


# ---------------------------------------------------------------------------
# Static file serving — order matters: API routes are matched first, then
# the /data mount for OUTPUT_ROOT (manifest, runs/, saved/), and finally
# a catch-all SPA fallback that serves index.html so React Router can
# handle /Select etc.
# ---------------------------------------------------------------------------
FRONTEND_DIST: Path = config.REPO_ROOT / "frontend" / "dist"
DATA_PREFIX = "/data"

# /data/* → OUTPUT_ROOT (manifest.json, runs/<id>/..., saved/<name>.zip, …)
# This is the canonical mount used by manifests written since the
# /data/ prefix was introduced.
app.mount(
    DATA_PREFIX,
    StaticFiles(directory=str(config.OUTPUT_ROOT), html=False),
    name="outputs",
)

# Backward-compat mounts for manifests written before the /data/ prefix
# existed — they referenced ``/runs/<id>/foo.npz`` and ``/saved/foo.zip``
# directly. Keeping these mounted means stale manifests from previous
# runs continue to load their .npz files instead of falling through to
# the SPA fallback (which would return HTML and break JSZip downstream).
if (config.OUTPUT_ROOT / "runs").is_dir():
    app.mount(
        "/runs",
        StaticFiles(directory=str(config.OUTPUT_ROOT / "runs"), html=False),
        name="runs_legacy",
    )
if (config.OUTPUT_ROOT / "saved").is_dir():
    app.mount(
        "/saved",
        StaticFiles(directory=str(config.OUTPUT_ROOT / "saved"), html=False),
        name="saved_legacy",
    )


# Heuristic: a path whose last segment contains a dot is treated as a
# file request, not an SPA route. Without this, a 404 on /runs/<id>/foo.npz
# would return index.html (because the catch-all matches), which then
# gets fed to JSZip and produces "Can't find end of central directory".
_LOOKS_LIKE_FILE = re.compile(r"^[^/]*\.[^/]+$")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    """Serve the built SPA.

    Real file (``/assets/index-…js``, ``/favicon.svg``, etc.) → that file.
    Anything else (``/Select``, ``/CalibrationData``, …) → ``index.html``
    so React Router can take over.

    Paths whose last segment looks like a file (e.g. ``/foo/bar.npz``)
    return a clean 404 — falling back to ``index.html`` here would
    corrupt downstream loaders that expect binary bytes.
    """
    if FRONTEND_DIST.exists():
        candidate = (FRONTEND_DIST / full_path).resolve()
        # Guard against path-traversal: candidate must stay under FRONTEND_DIST.
        if FRONTEND_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
    last_segment = full_path.rsplit("/", 1)[-1]
    # Unknown API paths get a JSON 404, never the SPA's index.html.
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    if _LOOKS_LIKE_FILE.match(last_segment):
        raise HTTPException(status_code=404, detail="Not found")
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend not built. Run `npm run build` in frontend/.")
