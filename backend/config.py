"""
Central configuration for the EDGES-3 web interface.

All paths are environment-driven so the same code runs:
  * On the SSH cluster (default paths under /data5/... and the repo)
  * Anywhere else (env-var overrides)

Environment variables (all optional):

  EDGES_RAW_DATA_ROOT       Raw MRO data root (read only; the catalog normally
                             supplies it). Default: /data5/edges/data/EDGES3_data/MRO
  EDGES_PIPELINE_ROOT       Catalog + pipeline products (read only), used by
                             edges-catalog / edges-pipeline. Default: /data6/edges/edges-db
  EDGES_OUTPUT_ROOT         Where calibration and observation runs are written.
                             Default: <repo>/outputs
  EDGES_BEAM_FACTOR_FILE    Path to the EDGES-3 antenna beam factor file
                             (``e3_beam_factor.hickle``). Required for the
                             absolute temperature calibration.
  EDGES_PROBE_AMBIENT       Temperature-log code of the ambient load, shown per
                             antenna file for information (default 101)
  EDGES_PROBE_COLD_LOAD     Code 152 = pr59_current, NOT a temperature
                             (informational only; default 152)

Receiver calibrations, their inputs and load temperatures included, are
edges-pipeline's (``calibrations.py``); the observation inputs come from the
catalog (``catalog_inputs.py``). Nothing scans the raw data tree or merges
temperature-log files.

The pipeline is user-triggered only — there is no daemon, no scheduler,
no systemd unit. The backend runs under ``uvicorn`` and the frontend is
served by Vite (dev) or the built ``dist/`` (prod).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List


# ---------------------------------------------------------------------------
# Repo root: <repo>/  (this file is backend/config.py)
# ---------------------------------------------------------------------------
REPO_ROOT: Path = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Raw data
# ---------------------------------------------------------------------------
RAW_DATA_ROOT: Path = Path(
    os.environ.get(
        "EDGES_RAW_DATA_ROOT",
        "/data5/edges/data/EDGES3_data/MRO",
    )
).expanduser().resolve()

# ---------------------------------------------------------------------------
# Beam factor file
# ---------------------------------------------------------------------------
def _default_beam_factor_file() -> Path:
    """Pick a sensible default for whichever machine we are on.

    Tries, in order:

      1. The canonical edges-3-data-analysis package location
         (``/data4/vydula/edges/packages/edges3-data-analysis/data/e3_beam_factor.hickle``).
      2. Two levels above the raw data root
         (e.g. ``/data5/edges/e3_beam_factor.hickle``).
      3. Linux dev mounts (``/mnt/...``, ``/scratch/...``).
      4. ``$HOME/edges/...``.
    """
    # 1. canonical edges-3-data-analysis package location (on the SSH
    #    cluster the file ships with the edges-3-data-analysis repo)
    canonical = Path(
        "/data4/vydula/edges/packages/edges3-data-analysis/data/e3_beam_factor.hickle"
    )
    if canonical.exists():
        return canonical
    # 2. two levels above the raw data root (the edges/ dir)
    sibling = RAW_DATA_ROOT.parent.parent / "e3_beam_factor.hickle"
    if sibling.exists():
        return sibling
    # 3. linux dev mounts
    for guess in (
        Path("/mnt/data5/edges/e3_beam_factor.hickle"),
        Path("/scratch/edges/e3_beam_factor.hickle"),
        Path.home() / "edges" / "e3_beam_factor.hickle",
    ):
        if guess.exists():
            return guess
    return canonical  # fall through to canonical even if it doesn't exist


BEAM_FACTOR_FILE: Path = Path(
    os.environ.get("EDGES_BEAM_FACTOR_FILE", str(_default_beam_factor_file()))
).expanduser().resolve()


# ---------------------------------------------------------------------------
# Outputs (default: <repo>/outputs)
# ---------------------------------------------------------------------------
OUTPUT_ROOT: Path = Path(
    os.environ.get(
        "EDGES_OUTPUT_ROOT",
        str(REPO_ROOT / "outputs"),
    )
).expanduser().resolve()

# Runs live in ``OUTPUT_ROOT/calibration/<id>/`` and
# ``OUTPUT_ROOT/observation/<id>/`` (see runs_api.py).


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------
SCRIPTS_DIR: Path = Path(__file__).resolve().parent

RUN_SCRIPT: Path = SCRIPTS_DIR / "run_single_day.py"


# ---------------------------------------------------------------------------
# Python interpreter
# ---------------------------------------------------------------------------
def _detect_python() -> str:
    """Prefer ``EDGES_PYTHON`` if set, then the current interpreter
    (``sys.executable``), then any ``python`` on PATH. The current
    interpreter wins over PATH lookups so an active uv venv is
    preserved across subprocess invocations.
    """
    candidates = [
        os.environ.get("EDGES_PYTHON"),
        sys.executable,
        shutil.which("python"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return sys.executable


PYTHON: str = _detect_python()


# ---------------------------------------------------------------------------
# Temperature probes
# ---------------------------------------------------------------------------
# Probe numbers are the codes of the on-site temperature log (catalog
# ``hk_code``): 100 front_end_temperature, 101 amb_load_temperature,
# 102 hot_load_temperature, 103 inner_box_temperature (degC); 106
# thermal_control; 150 battery_voltage (V); 152 pr59_current; 0 the
# thermal setpoint.
#
# The receiver calibration's probes are edges-pipeline settings
# (``[temperatures] ambient_code`` 101, ``hot_code`` 102; the Calibrations
# page can change them). The site only shows the ambient-load reading at
# each antenna file, with the same probe as the pipeline by default.
PROBE_AMBIENT: float = float(os.environ.get("EDGES_PROBE_AMBIENT", "101"))
# Code 152 is pr59_current (edges-analysis), NOT a temperature. The site
# does not use it for any calibration; it is kept only for information.
PROBE_COLD_LOAD: float = float(os.environ.get("EDGES_PROBE_COLD_LOAD", "152"))


# ---------------------------------------------------------------------------
# CORS origins
# ---------------------------------------------------------------------------
# The API is unauthenticated and its POST endpoints are state-changing
# (they delete and regenerate outputs), so the allowed origins are a
# strict allowlist, never "*". Loopback origins (Vite dev server, uvicorn,
# SSH tunnel) are always permitted via the middleware's origin regex; add
# any real frontend host with EDGES_ALLOWED_ORIGINS (comma-separated),
# e.g. ``EDGES_ALLOWED_ORIGINS=https://edges.example.com``.
ALLOWED_ORIGINS: List[str] = [
    o.strip()
    for o in os.environ.get(
        "EDGES_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8003,http://127.0.0.1:8003",
    ).split(",")
    if o.strip()
]

# Shown (as "–") when no ambient-load reading is found near an antenna file.
AMBIENT_FALLBACK_K = 306.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ensure_dirs() -> None:
    """Make sure every output subdirectory exists."""
    for d in (OUTPUT_ROOT, OUTPUT_ROOT / "calibration", OUTPUT_ROOT / "observation"):
        d.mkdir(parents=True, exist_ok=True)


def describe() -> str:
    return (
        f"REPO_ROOT          = {REPO_ROOT}\n"
        f"RAW_DATA_ROOT      = {RAW_DATA_ROOT}\n"
        f"PIPELINE_ROOT      = {os.environ.get('EDGES_PIPELINE_ROOT', '/data6/edges/edges-db')}\n"
        f"BEAM_FACTOR_FILE   = {BEAM_FACTOR_FILE}\n"
        f"OUTPUT_ROOT        = {OUTPUT_ROOT}\n"
        f"PYTHON             = {PYTHON}\n"
        f"PROBE_AMBIENT      = {PROBE_AMBIENT} (information only)\n"
        f"PROBE_COLD_LOAD    = {PROBE_COLD_LOAD} (pr59_current, not a temperature)\n"
    )


if __name__ == "__main__":
    ensure_dirs()
    print(describe())
