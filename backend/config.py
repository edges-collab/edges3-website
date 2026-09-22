"""
Central configuration for the EDGES-3 web interface.

All paths are environment-driven so the same code runs:
  * On the SSH cluster (default paths under /data5/... and the repo)
  * Anywhere else (env-var overrides)

Environment variables (all optional):

  EDGES_RAW_DATA_ROOT       Raw MRO data root. Default: /data5/edges/data/EDGES3_data/MRO
  EDGES_OUTPUT_ROOT         Where outputs and the manifest live.
                             Default: <repo>/outputs
  EDGES_TEMP_LOG_FILE       Path to a specific temperature.log (legacy)
  EDGES_TEMP_LOG_DIR        Directory containing one or more temperature log
                             files; every ``*.log`` (plus ``*.backup`` and
                             ``*.txt``) in this directory is read and merged
                             into one timeline.
  EDGES_BEAM_FACTOR_FILE    Path to the EDGES-3 antenna beam factor file
                             (``e3_beam_factor.hickle``). Required for the
                             absolute temperature calibration.
  EDGES_PROBE_AMBIENT       Temperature-log probe for ambient cal (default 100)
  EDGES_PROBE_HOT           Temperature-log probe for hot cal     (default 102)
  EDGES_PROBE_LNA           Temperature-log probe for LNA / cable (default 100)
  EDGES_PROBE_COLD_LOAD     Temperature-log probe for cold load  (default 152)

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

TEMPERATURE_LOG_FILE: Path = Path(
    os.environ.get(
        "EDGES_TEMP_LOG_FILE",
        str(RAW_DATA_ROOT / "temperature_logger" / "temperature.log"),
    )
).expanduser().resolve()

# Directory containing one or more temperature logs. Every ``*.log`` file
# in this directory is read, so multiple log files (one per session, day,
# or sensor) all contribute to the lookup.
TEMPERATURE_LOG_DIR: Path = Path(
    os.environ.get(
        "EDGES_TEMP_LOG_DIR",
        str(TEMPERATURE_LOG_FILE.parent),
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
      2. The great-grandparent of the live ``temperature.log``
         (e.g. ``data5/edges/e3_beam_factor.hickle``).
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
    # 2. great-grandparent of temperature_logger/ (the edges/ dir)
    sibling = TEMPERATURE_LOG_FILE.parent.parent.parent.parent / "e3_beam_factor.hickle"
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

# Subdirectories within OUTPUT_ROOT. Every run lives under ``runs/``;
# ``user_cache/`` holds the previous run for dedup; ``saved/`` holds
# user-saved zips.
RUNS_DIR: Path = OUTPUT_ROOT / "runs"
SAVED_DIR: Path = OUTPUT_ROOT / "saved"

MANIFEST_FILE: Path = OUTPUT_ROOT / "manifest.json"
LATEST_RUN_FILE: Path = OUTPUT_ROOT / "latest_run.json"
AVAILABLE_DATES_FILE: Path = OUTPUT_ROOT / "available_dates.json"


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------
SCRIPTS_DIR: Path = Path(__file__).resolve().parent

RUN_SCRIPT: Path = SCRIPTS_DIR / "run_single_day.py"
SCAN_SCRIPT: Path = SCRIPTS_DIR / "scan_dates.py"


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
# Calibration temperature probes
# ---------------------------------------------------------------------------
# The pipeline auto-derives every per-load calibration temperature from
# the temperature log. There are no user-tunable setpoints anymore —
# whatever the probe says is what the EDGES receiver calibration sees,
# which in turn is what ``calibrated_temps.txt`` reports back.
#
# Probe numbers are the ``offset_s`` values found in the on-site
# temperature log file (see ``temperature_logger/*.log``). Override with
# EDGES_PROBE_* if your hardware uses a different sensor layout.
PROBE_AMBIENT: float = float(os.environ.get("EDGES_PROBE_AMBIENT", "100"))
PROBE_HOT: float = float(os.environ.get("EDGES_PROBE_HOT", "102"))
PROBE_LNA: float = float(os.environ.get("EDGES_PROBE_LNA", "100"))
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

# Fallback values used when no probe reading is found at the calibration
# time. These are NOT exposed as env vars — they're internal constants.
TCOLD_FALLBACK_K = 306.5
THOT_FALLBACK_K = 393.22
TCAB_FALLBACK_K = 306.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ensure_dirs() -> None:
    """Make sure every output subdirectory exists."""
    for d in (OUTPUT_ROOT, RUNS_DIR, SAVED_DIR):
        d.mkdir(parents=True, exist_ok=True)


def describe() -> str:
    return (
        f"REPO_ROOT          = {REPO_ROOT}\n"
        f"RAW_DATA_ROOT      = {RAW_DATA_ROOT}\n"
        f"TEMPERATURE_LOG_DIR= {TEMPERATURE_LOG_DIR}\n"
        f"TEMPERATURE_LOG    = {TEMPERATURE_LOG_FILE}\n"
        f"BEAM_FACTOR_FILE   = {BEAM_FACTOR_FILE}\n"
        f"OUTPUT_ROOT        = {OUTPUT_ROOT}\n"
        f"RUNS_DIR           = {RUNS_DIR}\n"
        f"SAVED_DIR          = {SAVED_DIR}\n"
        f"PYTHON             = {PYTHON}\n"
        f"TCOLD_FALLBACK_K   = {TCOLD_FALLBACK_K} K\n"
        f"THOT_FALLBACK_K    = {THOT_FALLBACK_K} K\n"
        f"TCAB_FALLBACK_K    = {TCAB_FALLBACK_K} K\n"
        f"PROBE_AMBIENT      = {PROBE_AMBIENT}\n"
        f"PROBE_HOT          = {PROBE_HOT}\n"
        f"PROBE_LNA          = {PROBE_LNA}\n"
        f"PROBE_COLD_LOAD    = {PROBE_COLD_LOAD}\n"
    )


if __name__ == "__main__":
    ensure_dirs()
    print(describe())
