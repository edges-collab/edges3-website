"""
EDGES-3 Web API
===============

The FastAPI app that serves the site (bind it to localhost only).

Routers
-------
``products_api``   GET /api/night, /api/nights/latest, ... — the Nightly
                   Overview, from precomputed QL/L1 products (read only)
``runs_api``       /api/calibrations/..., /api/observations/..., /api/runs/...
                   — receiver calibrations from edges-pipeline (stored, or
                   computed with other settings as a background job) and
                   nights calibrated with them (background jobs)

Other endpoints
---------------
GET  /health                 Liveness probe
GET  /pipeline/status        Whether the data packages work, and the job queue

Static files
------------
``OUTPUT_ROOT`` is mounted under ``/data`` (``/data/<kind>/<id>/plots.npz``
etc.). The built SPA is served from ``frontend/dist/`` at ``/`` with a
catch-all fallback that returns ``index.html`` for React Router paths.

Nothing here scans or writes the raw data tree: inputs come from the EDGES
catalog (``catalog_inputs.py``) and edges-pipeline (``calibrations.py``), and
outputs go to ``OUTPUT_ROOT``.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import products_api  # noqa: E402
import runs_api  # noqa: E402


log = logging.getLogger("edges.api")

# Ensure output directories exist before StaticFiles mounts at import time.
config.ensure_dirs()

app = FastAPI(title="EDGES-3 Web API")
# A calibration is ~0.9 MB of JSON (~0.2 MB compressed).
app.add_middleware(GZipMiddleware, minimum_size=4096)
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

# Both routers need edges-catalog and edges-pipeline; without them they
# return 503 with an install hint.
app.include_router(products_api.router)
app.include_router(runs_api.router)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "time": datetime.now().isoformat()}


@app.get("/pipeline/status")
def pipeline_status() -> Dict[str, Any]:
    return {
        "data_packages": products_api.IMPORT_ERROR is None,
        "data_packages_error": products_api.IMPORT_ERROR,
        "queue": runs_api.queue.snapshot(),
        "runs": runs_api.summary(),
    }


# ---------------------------------------------------------------------------
# Static file serving — order matters: API routes are matched first, then
# the /data mount for OUTPUT_ROOT, and finally a catch-all SPA fallback that
# serves index.html so React Router can handle its routes.
# ---------------------------------------------------------------------------
FRONTEND_DIST: Path = config.REPO_ROOT / "frontend" / "dist"
DATA_PREFIX = "/data"

app.mount(
    DATA_PREFIX,
    StaticFiles(directory=str(config.OUTPUT_ROOT), html=False),
    name="outputs",
)


# Heuristic: a path whose last segment contains a dot is treated as a
# file request, not an SPA route. Without this, a 404 on /data/.../foo.npz
# would return index.html (because the catch-all matches), which then
# gets fed to JSZip and produces "Can't find end of central directory".
_LOOKS_LIKE_FILE = re.compile(r"^[^/]*\.[^/]+$")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    """Serve the built SPA.

    Real file (``/assets/index-…js``, ``/favicon.svg``, etc.) → that file.
    Anything else (``/calibrations``, ``/data-view``, …) → ``index.html``
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
