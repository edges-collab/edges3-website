"""
Shared utilities for saving 1D / 2D plot data and writing the manifest.

The convention here is THE single source of truth for the on-disk format and
the manifest schema. Both ``run_single_day.py`` (used by the daemon and by the
HTTP ``/run_pipeline`` endpoint) and ``daemon.py`` import from here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# Manifest schema (must match src/types/manifest.ts on the frontend)
# ---------------------------------------------------------------------------
# Plot types
S11 = "s11"
SINGLE = "single"
MULTI = "multi"
IMAGE = "image"
HEATMAP = "heatmap"

# Page names
PAGE_CALIBRATION = "calibration"
PAGE_CALIBRATED = "calibrated"
PAGE_RAW = "raw"

VALID_PLOT_TYPES = {S11, SINGLE, MULTI, IMAGE, HEATMAP}
VALID_PAGES = {PAGE_CALIBRATION, PAGE_CALIBRATED, PAGE_RAW}


@dataclass
class Plot:
    page: str
    id: str
    type: str
    title: str
    filePath: Optional[str] = None
    filePath1: Optional[str] = None
    filePath2: Optional[str] = None
    name1: Optional[str] = None
    name2: Optional[str] = None
    xKey: Optional[str] = None
    yKey: Optional[str] = None
    # Axis labels with units — surfaced to the frontend so plotters don't
    # have to guess. Defaults are applied by each plotter if not set.
    axisx: Optional[str] = None
    axisy: Optional[str] = None
    dates: Dict[str, str] = field(default_factory=dict)
    source: Optional[str] = None  # "daemon" or "user"
    # Optional metadata (used by actual_temperature plots, etc.)
    time: Optional[str] = None       # ISO-8601 timestamp of the sample
    temperature_k: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "page": self.page,
            "id": self.id,
            "type": self.type,
            "title": self.title,
        }
        if self.filePath is not None:
            d["filePath"] = self.filePath
        if self.filePath1 is not None:
            d["filePath1"] = self.filePath1
        if self.filePath2 is not None:
            d["filePath2"] = self.filePath2
        if self.name1 is not None:
            d["name1"] = self.name1
        if self.name2 is not None:
            d["name2"] = self.name2
        if self.xKey is not None:
            d["xKey"] = self.xKey
        if self.yKey is not None:
            d["yKey"] = self.yKey
        if self.axisx is not None:
            d["axisx"] = self.axisx
        if self.axisy is not None:
            d["axisy"] = self.axisy
        if self.dates:
            d["dates"] = self.dates
        if self.source is not None:
            d["source"] = self.source
        if self.time is not None:
            d["time"] = self.time
        if self.temperature_k is not None:
            d["temperature_k"] = self.temperature_k
        # Only emit non-empty entries
        return {k: v for k, v in d.items() if v not in (None, {}, [])}


# ---------------------------------------------------------------------------
# Low-level save helpers
# ---------------------------------------------------------------------------
def save_1d_npz(
    output_dir: Path,
    filename: str,
    x: np.ndarray,
    y: np.ndarray,
    x_key: str = "x",
    y_key: str = "y",
    metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    """Save a 1D plot as ``{x_key}.npy`` and ``{y_key}.npy`` inside an npz.

    ``metadata`` is an optional dict of additional scalar fields to attach
    (e.g. ``{"time": "2026-09-01T22:24:54", "temperature_k": 298.14}``).
    They are written as single-element string/float arrays so anyone
    reading the npz can see them.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    payload: Dict[str, Any] = {x_key: np.asarray(x), y_key: np.asarray(y)}
    if metadata:
        for k, v in metadata.items():
            if v is None:
                continue
            if isinstance(v, str):
                # np.savez refuses raw strings; encode as an object array.
                payload[k] = np.array([v], dtype=object)
            elif isinstance(v, (int, float, np.floating, np.integer)):
                payload[k] = np.array([float(v)])
            else:
                payload[k] = np.asarray([v])
    np.savez(path, **payload)
    return path


def save_waterfall_jpeg(
    data_2d: np.ndarray,
    freqs: np.ndarray,
    lsts: np.ndarray,
    output_dir: Path,
    filename: str,
    title: str = "",
    xlabel: str = "Frequency [MHz]",
    ylabel: str = "LST [hr]",
    dpi: int = 80,
) -> Path:
    """Save a 2D waterfall as JPEG. ``data_2d`` has shape (n_lsts, n_freqs)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 6))
    extent = [float(freqs[0]), float(freqs[-1]), float(lsts[0]), float(lsts[-1])]
    im = ax.imshow(
        np.asarray(data_2d),
        aspect="auto",
        origin="lower",
        extent=extent,
        cmap="viridis",
    )
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    fig.colorbar(im, ax=ax)
    out = output_dir / filename
    fig.savefig(out, dpi=dpi, bbox_inches="tight", format="jpeg")
    plt.close(fig)
    return out


def save_heatmap_npz(
    output_dir: Path,
    filename: str,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
) -> Path:
    """Save a 2D heatmap dataset as ``x.npy`` / ``y.npy`` / ``z.npy`` in an npz.

    Frontend convention: ``z`` is shaped ``(len(y), len(x))``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    np.savez(path, x=np.asarray(x), y=np.asarray(y), z=np.asarray(z))
    return path


def save_heatmap_preview_npz(
    output_dir: Path,
    filename: str,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    max_rows: int = 200,
    max_cols: int = 400,
) -> Path:
    """Save a stride-decimated preview of a 2D heatmap for fast browser load.

    The full-resolution npz from :func:`save_heatmap_npz` is ~120 MB for a
    typical 450×32768 EDGES waterfall. Downloading + parsing + JS downsampling
    in the browser takes >10 s per plot, freezing the main thread. This
    companion ``*_preview.npz`` file keeps ~``max_rows`` × ``max_cols`` cells
    (typically ~1 MB), which Plotly can render immediately. The frontend
    prefers this file when present and falls back to the full-resolution
    npz (with on-the-fly decimation) otherwise.

    Decimation picks every ``N``th row/column rather than block-averaging;
    for visualization that's indistinguishable from averaging and runs in
    microseconds via numpy slicing.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    row_factor = max(1, int(np.ceil(z.shape[0] / max_rows)))
    col_factor = max(1, int(np.ceil(z.shape[1] / max_cols)))
    z_small = z[::row_factor, ::col_factor]
    x_small = np.asarray(x)[::col_factor]
    y_small = np.asarray(y)[::row_factor]
    np.savez(path, x=x_small, y=y_small, z=z_small)
    return path


# ---------------------------------------------------------------------------
# Filename conventions (parse helpers)
# ---------------------------------------------------------------------------
DATE_RE = re.compile(r"(\d{4})_(\d{3})")
TIMESTAMP_RE = re.compile(r"(\d{4})_(\d{3})_(\d{2})_(\d{2})_(\d{2})")


def parse_yyyy_ddd(s: str) -> tuple[int, int]:
    """Parse a ``YYYY_DDD`` string. Raises ValueError on bad input."""
    m = DATE_RE.fullmatch(s)
    if not m:
        raise ValueError(f"Not a YYYY_DDD date: {s!r}")
    return int(m.group(1)), int(m.group(2))


def parse_timestamp(s: str) -> tuple[int, int, int, int, int]:
    m = TIMESTAMP_RE.fullmatch(s)
    if not m:
        raise ValueError(f"Not a YYYY_DDD_HH_MM_SS timestamp: {s!r}")
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def compute_run_hash(dates: Dict[str, str], parameters: Dict[str, Any]) -> str:
    """Deterministic 16-char SHA-256 hash from ``(dates, parameters)``.

    The same ``(cal, s11, raw, params)`` always produces the same hash, which
    is used as the deduplication key. ``save_2d_npz`` is part of the params so
    a request for 2D data never matches a daemon run (which is always 2D-free).
    """
    payload = {
        "dates": {k: str(v) for k, v in sorted((dates or {}).items()) if v},
        "params": {k: v for k, v in sorted((parameters or {}).items())},
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16] 


# ---------------------------------------------------------------------------
# Manifest writer
# ---------------------------------------------------------------------------
# The FastAPI backend serves OUTPUT_ROOT under the URL prefix ``/data``
# (see ``backend_api.py``). Paths stored in the manifest are absolute
# (e.g. ``/data/runs/<id>/calibrated.npz``) so the browser can fetch them
# without needing to know the on-disk layout.
DATA_PREFIX = "/data"


def write_manifest(
    output_root: Path,
    run_dir: Path,
    source: str,
    plots: List[Plot],
    dates: Dict[str, str],
) -> Path:
    """Write ``manifest.json`` and ``latest_run.json`` for one run.

    ``output_root`` is the on-disk root. Paths stored in the manifest
    are absolute URLs (``/data/...``) because FastAPI serves
    ``OUTPUT_ROOT`` under the ``/data`` prefix.
    """
    rel_run = f"runs/{run_dir.name}"

    rendered = []
    for p in plots:
        d = p.to_dict()
        # Rewrite absolute paths (in case the caller pre-built them) so
        # they point at the /data/ static mount.
        for key in ("filePath", "filePath1", "filePath2"):
            v = d.get(key)
            if v and Path(v).is_absolute():
                try:
                    rel = Path(v).relative_to(output_root)
                    d[key] = f"{DATA_PREFIX}/{rel.as_posix()}"
                except ValueError:
                    d[key] = v
        d.setdefault("source", source)
        d.setdefault("dates", dates)
        rendered.append(d)

    manifest = {
        "latest_run": run_dir.name,
        "source": source,
        "dates": dates,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "plots": rendered,
    }
    out_manifest = output_root / "manifest.json"
    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    with open(out_manifest, "w") as f:
        json.dump(manifest, f, indent=2)

    latest = {
        "source": source,
        "run_id": run_dir.name,
        "dates": dates,
        "manifest": f"{DATA_PREFIX}/manifest.json",
        "generated_at": manifest["generated_at"],
    }
    return out_manifest
