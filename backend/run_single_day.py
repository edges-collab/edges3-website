#!/usr/bin/env python3
"""
EDGES-3 Single-Day Analysis Pipeline
=====================================

Reads raw calibration and antenna .acq files, runs receiver calibration,
computes the antenna S11, applies Dicke and noise-wave calibration, and writes
all required outputs (.npz for 1D, JPEG for 2D waterfalls, .npz for heatmaps).

Used in two contexts:
  * Daemon — runs once a day with the latest dates, no 2D heatmap data
  * User   — runs from the website when the user changes dates / parameters

All paths come from ``config.py`` so the same script works on macOS and on the
enterprise cluster (via environment variables).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from astropy import units as un
from astropy.time import Time

# Allow ``python run_single_day.py`` to import config.py / io_utils.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
from io_utils import (  # noqa: E402
    PAGE_CALIBRATED,
    PAGE_CALIBRATION,
    PAGE_RAW,
    Plot,
    compute_run_hash,
    parse_timestamp,
    parse_yyyy_ddd,
    save_1d_npz,
    save_heatmap_npz,
    save_heatmap_preview_npz,
    save_waterfall_jpeg,
    write_manifest,
)

from pygsdata import GSData  # noqa: E402
from read_acq.gsdata import fast_lst_setter, read_acq_to_gsdata  # noqa: E402
from edges.const import KNOWN_TELESCOPES  # noqa: E402
from edges.alanmode import (  # noqa: E402
    Edges3CalobsParams,
    EdgesScriptParams,
    ACQPlot7aMoonParams,
    read_specal,
)
from edges.alanmode.cli import AlanCalOpts, alancal_edges3  # noqa: E402
from edges.cal.apply import approximate_temperature  # noqa: E402
from edges.cal.dicke import dicke_calibration  # noqa: E402
from edges.cal import ReflectionCoefficient, S11ModelParams, sparams as sp  # noqa: E402
from edges.frequencies import get_mask  # noqa: E402
import edges.io as io  # noqa: E402
import edges.modeling as mdl  # noqa: E402


# ---------------------------------------------------------------------------
# Defaults — match the originals so behaviour is preserved when no flags set
# ---------------------------------------------------------------------------
DEFAULT_CTERMS = 6
DEFAULT_WTERMS = 5
DEFAULT_FSTART = 40.0
DEFAULT_FSTOP = 190.0
DEFAULT_WFSTART = 40.0
DEFAULT_WFSTOP = 190.0

CAL_LOADS = ("amb", "hot", "open", "short")

# ---------------------------------------------------------------------------
# Calibration temperatures
# ---------------------------------------------------------------------------
# The probe readings at each calibration / observation are resolved from the
# EDGES catalog by ``catalog_inputs.resolve_inputs`` (the ``.tmp`` snapshot at
# the spectrum's hour, else the nearest temperature-log reading of that
# probe within 15 min, else the fallback constant) and arrive in
# ``inputs["temperatures"]``. The catalog's housekeeping is de-duplicated
# and excludes logs of other receivers, so nothing here parses or merges
# temperature-log files.
def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """ISO-8601 string for a naive-UTC ``datetime``.

    ``astropy Time.to_datetime()`` returns naive UTC datetimes; tagging
    them as UTC (``+00:00``) lets the browser's ``new Date(iso)`` parse
    them on their own timezone without shifting the displayed time.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _celsius(entry: Dict[str, Any]) -> Optional[float]:
    """The probe reading in Celsius, or ``None`` if only the fallback exists."""
    if entry.get("source") == "default":
        return None
    return entry.get("temperature_c")


# ---------------------------------------------------------------------------
# Raw data readers
# ---------------------------------------------------------------------------
def read_calibration_acq(files: Dict[str, Optional[str]], cal_date: str) -> Dict[str, GSData]:
    """Read the four calibration spectra (paths from the catalog)."""
    cal_data: Dict[str, GSData] = {}
    for load in CAL_LOADS:
        path = files.get(load)
        if not path:
            print(f"[run] WARN: no {load} calibration file for {cal_date}")
            continue
        cal_data[load] = read_acq_to_gsdata(
            Path(path),
            telescope=KNOWN_TELESCOPES["edges3"],
            name=f"cal_{load}_{cal_date}",
            lst_setter=fast_lst_setter,
        )
    return cal_data


def read_antenna_acq(path: str, spec_timestamp: str) -> GSData:
    """Read the antenna spectrum (path from the catalog)."""
    return read_acq_to_gsdata(
        Path(path),
        telescope=KNOWN_TELESCOPES["edges3"],
        name=f"ant_{spec_timestamp}",
        lst_setter=fast_lst_setter,
    )


# ---------------------------------------------------------------------------
# S11 grid alignment
# ---------------------------------------------------------------------------
# EDGES assumes every ``.s1p`` file in a single ``YYYY_DDD_HH`` set shares a
# frequency grid (it does e.g. ``gamma_in - sparams.s11`` inside
# ``gamma_de_embed`` and broadcasts). When the VNA was reconfigured mid-
# session, files can have different point counts — the most common case on
# the cluster is one coarse ``_amb.s1p`` (49 pts, 40–91 MHz) plus the rest
# at 151 pts (40–200 MHz). Without alignment, EDGES raises
# ``ValueError: operands could not be broadcast together with shapes (49,)
# (151,)`` and the run fails.
#
# Strategy: resample every file down onto the SMALLEST count present so
    # every output value is a real measurement (no extrapolation), at the
    # cost of narrowing the calibration's frequency range.
def align_s11_grids(
    s11_files: List[str],
    s11date: str,
    cache_dir: Path,
) -> Tuple[Path, List[Dict[str, Any]]]:
    """Build a shadow directory with resampled ``{s11date}_*.s1p`` files.

    The raw data files are read-only on the cluster,
    so we cannot modify the originals in place. Instead, this helper
    creates ``cache_dir/{s11date}/`` containing the resampled files
    alongside copies of the unresampled ones. The caller then
    monkey-patches ``edges.io.vna.read_s1p`` (see
    :func:`run_receiver_calibration`) so EDGES reads the shadow copies
    instead of the originals.

    When the VNA was reconfigured mid-session, files in a single
    ``YYYY_DDD_HH`` set can have different point counts — the most
    common case on the cluster is one coarse ``_amb.s1p`` (49 pts,
    40–91 MHz) plus the rest at 151 pts (40–200 MHz). Without
    alignment EDGES raises
    ``ValueError: operands could not be broadcast together with shapes
    (49,) (151,)``. We fix this by resampling every file onto the
    SMALLEST count present — every output value is a real measurement
    (no extrapolation), at the cost of narrowing the calibration's
    frequency range.

    Returns ``(shadow_dir, warnings)``:
      * ``shadow_dir`` — path to a directory containing the
        ready-to-read .s1p files for ``s11date``. Empty if no resampling
        was needed.
      * ``warnings`` — empty list if all files already share a grid,
        otherwise a single envelope ``[{"reference": {...},
        "warnings": [...]}]``.

    The shadow is cached in ``cache_dir`` so subsequent runs with the
    same ``s11date`` reuse it without re-reading or re-resampling.
    """
    shadow = cache_dir / s11date
    # The session's files, from the catalog (``inputs["files"]["s11"]``).
    files = sorted(Path(p) for p in s11_files)
    if len(files) < 2:
        return shadow, []

    # Lazy import — only needed when there's actual work to do.
    from edges.io.vna import read_s1p  # noqa: E402

    grids: Dict[Path, Tuple[np.ndarray, np.ndarray]] = {}
    for f in files:
        try:
            sparams = read_s1p(f)
            freqs = sparams["frequency"].to_value("Hz").astype(float)
            s11 = np.asarray(sparams["s11"], dtype=complex)
        except Exception:
            continue
        if freqs.size == 0 or s11.size == 0:
            continue
        grids[f] = (freqs, s11)

    if len(grids) < 2:
        return shadow, []

    counts = {f: len(g[0]) for f, g in grids.items()}
    distinct = set(counts.values())
    if len(distinct) <= 1:
        # All aligned. Build a shadow with copies of the originals so
        # ``read_s1p`` can resolve any file in the date uniformly.
        shadow.mkdir(parents=True, exist_ok=True)
        for f in grids:
            target = shadow / f.name
            if not target.exists():
                shutil.copy(f, target)
        return shadow, []

    # Mismatch detected: resample DOWN onto the smallest grid.
    shadow.mkdir(parents=True, exist_ok=True)
    target_count = min(distinct)
    target_files = [f for f, n in counts.items() if n == target_count]
    target_file = target_files[0]
    target_freqs = grids[target_file][0]
    target_fmin_mhz = float(target_freqs[0]) / 1e6
    target_fmax_mhz = float(target_freqs[-1]) / 1e6

    warnings: List[Dict[str, Any]] = []
    reference = {
        "type": "s11_grid_reference",
        "file": target_file.name,
        "count": int(target_count),
        "range_mhz": [round(target_fmin_mhz, 4),
                      round(target_fmax_mhz, 4)],
    }

    for f, (freqs, s11) in grids.items():
        target_path = shadow / f.name
        if len(freqs) == target_count and np.allclose(freqs, target_freqs):
            # Reference file — copy the original into the shadow so
            # the patched reader always finds a file there.
            if not target_path.exists():
                shutil.copy(f, target_path)
            continue

        new_real = np.interp(target_freqs, freqs, s11.real)
        new_imag = np.interp(target_freqs, freqs, s11.imag)

        # Touchstone v1 BEGIN/RI/END — frequency in Hz, S11 as
        # real/imag. EDGES's reader always interprets ``d[:,0]`` as
        # Hz regardless of the settings-line unit, so BEGIN/RI/END
        # with no unit prefix is the safe choice.
        with open(target_path, "w") as fh:
            fh.write("BEGIN\nRI\n")
            for fr_hz, rr, ii in zip(target_freqs, new_real, new_imag):
                fh.write(f"{fr_hz:.6f} {rr:.8e} {ii:.8e}\n")
            fh.write("END\n")
        warnings.append({
            "type": "s11_grid_mismatch",
            "file": f.name,
            "from_count": int(len(freqs)),
            "to_count": int(target_count),
            "from_range_mhz": [round(float(freqs[0]) / 1e6, 4),
                               round(float(freqs[-1]) / 1e6, 4)],
            "to_range_mhz": reference["range_mhz"],
        })

    warnings.sort(key=lambda w: w["file"])
    return shadow, [{"reference": reference, "warnings": warnings}]


def _flatten_align_result(
    result: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Unwrap the envelope returned by :func:`align_s11_grids`.

    The function returns either ``[]`` (no resampling) or a single
    envelope ``[{"reference": ..., "warnings": ...}]``. This helper
    splits that into the (reference, warnings) pair that
    ``run_receiver_calibration`` and the API actually consume, keeping
    the no-op case as ``({}, [])``.
    """
    if not result:
        return {}, []
    env = result[0]
    return env.get("reference", {}), env.get("warnings", [])


def _install_s11_shadow_reader(
    root: Path,
    s11date: str,
    shadow_dir: Path,
):
    """Monkey-patch EDGES's ``ReflectionCoefficient.from_s1p`` so reads
    of ``{s11date}_*.s1p`` under ``root`` resolve to ``shadow_dir``.

    Returns a callable that restores the original classmethod. The
    caller MUST invoke it (typically in a ``finally`` block) so other
    code that reads .s1p files (e.g. the antenna S11 calibration later
    in the pipeline) is unaffected.

    Note: patching ``edges.io.vna.read_s1p`` alone is NOT enough —
    EDGES imports it under three different names
    (``edges.io.vna.read_s1p``, ``edges.io.read_s1p``, and
    ``edges.cal.sparams.core.datatypes.read_s1p``). Patching
    ``ReflectionCoefficient.from_s1p`` instead sidesteps all three
    import bindings in one go.
    """
    import edges.cal.sparams.core.datatypes as dt  # noqa: E402

    original = dt.ReflectionCoefficient.from_s1p
    root_resolved = root.resolve()

    def patched(cls, path):
        p = Path(path)
        try:
            if p.parent.resolve() == root_resolved and p.name.startswith(f"{s11date}_"):
                shadow_path = shadow_dir / p.name
                if shadow_path.exists():
                    # Call the *unbound* underlying function — the
                    # original classmethod is already bound to ``cls``,
                    # so we go through ``__func__``.
                    return original.__func__(cls, shadow_path)
        except OSError:
            # ``Path.resolve()`` can fail on broken symlinks; fall
            # through to the original reader in that case.
            pass
        return original.__func__(cls, path)

    dt.ReflectionCoefficient.from_s1p = classmethod(patched)

    def restore():
        dt.ReflectionCoefficient.from_s1p = original

    return restore


# ---------------------------------------------------------------------------
# Receiver calibration
# ---------------------------------------------------------------------------
def run_receiver_calibration(
    root: Path,
    cal_date: str,
    s11_run: str,
    s11_files: List[str],
    outdir: Path,
    cterms: int,
    wterms: int,
    ambient_temp_k: float,
    hot_temp_k: float,
    cable_temp_k: float,
    fstart: float,
    fstop: float,
    wfstart: float,
    wfstop: float,
) -> Tuple[Path, Path]:
    """Run the EDGES receiver calibration.

    ``ambient_temp_k`` / ``hot_temp_k`` / ``cable_temp_k`` are the
    PROBE-MEASURED temperatures of the ambient load, hot load, and LNA
    cable at the time of each calibration. EDGES uses these as the
    *known* reference temperatures in the noise-wave fit; whatever we
    pass becomes the value reported in ``calibrated_temps.txt``. So we
    must pass the actual probe readings (not hardcoded setpoints) to
    keep the noise-wave model honest.

    Before calling EDGES, the helper ``align_s11_grids`` builds a
    shadow copy of the ``{s11_run}_*.s1p`` files under ``cache_dir``
    (resampled onto a common grid when the VNA was reconfigured
    mid-session). EDGES's reader is then monkey-patched so reads of
    these files under ``root`` resolve to the shadow. The raw data
    files in ``root`` are NEVER modified — the cluster filesystem is
    typically read-only. Warnings about resampled files are written to
    ``<outdir>/../s11_grid_warnings.json`` so the API can surface them.
    """
    year, day = parse_yyyy_ddd(cal_date)
    outdir.mkdir(parents=True, exist_ok=True)

    s11_cache = outdir.parent / "s11_cache"
    shadow_dir, align_envelope = align_s11_grids(s11_files, s11_run, s11_cache)
    s11_reference, s11_warnings = _flatten_align_result(align_envelope)

    if s11_warnings:
        warnings_path = outdir.parent / "s11_grid_warnings.json"
        warnings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(warnings_path, "w") as wf:
            json.dump(
                {"reference": s11_reference, "warnings": s11_warnings},
                wf, indent=2,
            )
        ref_name = s11_reference.get("file", "?")
        ref_range = s11_reference.get("range_mhz", [0, 0])
        print(
            f"[run] WARNING: S11 grid mismatch — VNA was reconfigured "
            f"mid-session. Reference file is {ref_name} "
            f"({s11_reference.get('count', '?')} pts, "
            f"{ref_range[0]}–{ref_range[1]} MHz). Resampled "
            f"{len(s11_warnings)} files down to match; calibration is "
            f"restricted to {ref_range[0]}–{ref_range[1]} MHz. "
            f"Shadow directory: {shadow_dir}"
        )

    restore_reader = _install_s11_shadow_reader(root, s11_run, shadow_dir)
    try:
        alancal_edges3(
            data=Edges3CalobsParams(
                specyear=year,
                specday=day,
                s11date=s11_run,
                datadir=root,
                match_resistance=49.8,
                calkit_delays=33,
                lna_cable_length=4.26,
                lna_cable_loss=-91.5,
                lna_cable_dielectric=-1.24,
            ),
            opts=AlanCalOpts(
                avg=ACQPlot7aMoonParams(
                    fstart=fstart,
                    fstop=fstop,
                    delaystart=0,
                    smooth=8,
                    tload=300,
                    tcal=1000,
                ),
                cal=EdgesScriptParams(
                    wfstart=wfstart,
                    wfstop=wfstop,
                    Lh=-1,
                    thot=hot_temp_k,
                    tcold=ambient_temp_k,
                    tcab=cable_temp_k,
                    cfit=cterms,
                    wfit=wterms,
                    nfit2=27,
                    nfit3=10,
                ),
                plot=False,
                out=outdir,
                redo_spectra=False,
                redo_cal=True,
            ),
        )
    finally:
        restore_reader()

    specal = outdir / "specal.txt"
    s11_modelled = outdir / "s11_modelled.txt"
    if not specal.exists():
        raise FileNotFoundError("specal.txt was not created by alancal")
    if not s11_modelled.exists():
        raise FileNotFoundError("s11_modelled.txt was not created by alancal")
    return specal, s11_modelled


# ---------------------------------------------------------------------------
# Antenna S11
# ---------------------------------------------------------------------------
def compute_antenna_s11(
    root: Path,
    s11_run: str,
    target_freqs: Optional[Any] = None,
) -> Tuple[ReflectionCoefficient, np.ndarray, np.ndarray]:
    """Return ``(ant_s11_model, freqs_MHz, real, imag)`` evaluated at target freqs."""
    base = root / s11_run

    ck = sp.CalkitReadings.from_filespec(
        io.CalkitFileSpec(
            open=f"{base}_O.s1p",
            short=f"{base}_S.s1p",
            match=f"{base}_L.s1p",
        )
    )
    calkit = sp.get_calkit(
        sp.AGILENT_ALAN,
        resistance_of_match=49.930 * un.ohm,
        short={"offset_delay": 33 * un.ps},
        open={"offset_delay": 33 * un.ps},
        match={"offset_delay": 33 * un.ps},
    )

    ant_s11_raw = sp.ReflectionCoefficient.from_s1p(f"{base}_ant.s1p")
    gamma_ant = sp.calibrate_gamma_src(
        gamma_src=ant_s11_raw,
        internal_calkit=calkit,
        internal_osl=ck,
    )

    f_low, f_high = 58.0, 105.0
    mask = get_mask(gamma_ant.freqs, f_low * un.MHz, f_high * un.MHz)
    ants11_raw = ReflectionCoefficient(
        reflection_coefficient=gamma_ant.reflection_coefficient[mask],
        freqs=gamma_ant.freqs[mask],
    )

    common_kwargs = dict(
        params=S11ModelParams(
            model=mdl.Polynomial(
                n_terms=12,
                transform=mdl.Log10Transform(scale=(f_low + f_high) / 2),
            ),
            complex_model_type=mdl.ComplexRealImagModel,
            set_transform_range=True,
            fit_method="alan-qrd",
            find_model_delay=True,
        )
    )
    if target_freqs is not None:
        ants11_model = ants11_raw.smoothed(freqs=target_freqs, **common_kwargs)
    else:
        ants11_model = ants11_raw.smoothed(**common_kwargs)

    freqs_mhz = ants11_model.freqs.to_value("MHz")
    real = ants11_model.reflection_coefficient.real
    imag = ants11_model.reflection_coefficient.imag
    return ants11_model, freqs_mhz, real, imag


# ---------------------------------------------------------------------------
# 1D saving helpers (wrap io_utils with domain-specific defaults)
# ---------------------------------------------------------------------------
def _save_1d(out_dir: Path, fname: str, x: np.ndarray, y: np.ndarray) -> Path:
    return save_1d_npz(out_dir, fname, x=x, y=y)


def _save_freq_y(
    out_dir: Path,
    fname: str,
    freqs: np.ndarray,
    y: np.ndarray,
    metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    return save_1d_npz(out_dir, fname, x=freqs, y=y, metadata=metadata)


def _save_freq_realimag(
    out_dir: Path, fname: str, freqs: np.ndarray, real: np.ndarray, imag: np.ndarray
) -> Path:
    """S11 .npz with keys frequency, real, imag (matches s11Loader.ts)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / fname
    np.savez(path, frequency=np.asarray(freqs), real=np.asarray(real), imag=np.asarray(imag))
    return path


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def process_single_day(
    inputs: Dict[str, Any],
    output_root: Path,
    run_dir: Path,
    *,
    cterms: int = DEFAULT_CTERMS,
    wterms: int = DEFAULT_WTERMS,
    fstart: float = DEFAULT_FSTART,
    fstop: float = DEFAULT_FSTOP,
    wfstart: float = DEFAULT_WFSTART,
    wfstop: float = DEFAULT_WFSTOP,
    save_2d_npz: bool = False,
    source: str = "user",
    run_hash: Optional[str] = None,
) -> Path:
    """Run the full pipeline for a single day. Returns the manifest path.

    ``inputs`` comes from ``catalog_inputs.resolve_inputs`` (see
    ``<run_dir>/inputs.json``): the dates, the raw data root, the exact
    input files and the probe temperatures.

    If ``run_hash`` is supplied the caller is responsible for dedup; this
    function just uses the value for the provenance marker.
    """
    cal_date = inputs["dates"]["cal"]
    s11_date = inputs["dates"]["s11"]
    spec_date = inputs["dates"]["raw"]
    raw_root = Path(inputs["root"])
    files = inputs["files"]
    temps = inputs["temperatures"]
    for issue in inputs.get("issues", []):
        print(f"[run] input issue: {issue}")
    output_root = Path(output_root)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    dates = {"cal": cal_date, "s11": s11_date, "raw": spec_date}

    # ---- 0. Calibration temperatures ----------------------------------------
    # Probe readings at each calibration / observation time, resolved from
    # the catalog. They are passed straight to the EDGES receiver
    # calibration, so ``calibrated_temps.txt`` reflects the actual physical
    # temperatures rather than hardcoded setpoints.
    cal_data = read_calibration_acq(files, cal_date)
    for display in ("ambient", "hot", "lna"):
        e = temps[display]
        print(f"[run] cal temp {display}: {e['temperature_k']:.2f} K "
              f"(probe {e['probe']:.0f}, {e['source']}"
              f"{', reading at ' + e['reading_time'] if e.get('reading_time') else ''})")
    ambient_k = temps["ambient"]["temperature_k"]
    hot_k = temps["hot"]["temperature_k"]
    lna_k = temps["lna"]["temperature_k"]

    # ---- 1. Receiver calibration --------------------------------------------
    # The EDGES noise-wave fit needs the *known* load temperatures.
    # Whatever we pass here becomes the value reported back in
    # ``calibrated_temps.txt``. We pass the probe readings so the fit
    # converges on the actual physical temperatures rather than echoing
    # back hardcoded setpoints.
    print("[run] Receiver calibration ...")
    calib_dir = run_dir / "calibration"
    specal_file, s11_modelled_file = run_receiver_calibration(
        raw_root, cal_date, s11_date, list(files["s11"].values()), calib_dir,
        cterms, wterms,
        ambient_temp_k=ambient_k,
        hot_temp_k=hot_k,
        cable_temp_k=ambient_k,   # cable sits in ambient
        fstart=fstart, fstop=fstop, wfstart=wfstart, wfstop=wfstop,
    )
    # ``tload`` (hot-load reference for Dicke switching) and ``tns``
    # (cable / noise source temperature) are derived from the same
    # probe readings.
    tload = hot_k
    tns = lna_k
    calobs = read_specal(specal_file, t_load=tload, t_load_ns=tns)

    # ---- 2. Antenna data ----------------------------------------------------
    print(f"[run] Reading antenna .acq for {spec_date}")
    raw_data = read_antenna_acq(files["ant"], spec_date)
    data_freqs_mhz = raw_data.freqs.to_value("MHz")
    target_freqs = un.Quantity(data_freqs_mhz, "MHz")

    # ---- 3. Antenna S11 at the data frequencies -----------------------------
    print("[run] Computing antenna S11 ...")
    ant_s11_model, ant_freqs_mhz, ant_real, ant_imag = compute_antenna_s11(
        raw_root, s11_date, target_freqs=target_freqs,
    )

    # Mask the antenna S11 to NaN outside the polynomial's fit window
    # (58–105 MHz). Outside that window the polynomial extrapolation with
    # set_transform_range=True produces absurd values — e.g. real/imag
    # magnitudes of order 1e14 — which make the K-matrix denominator
    # K1 → 0 (causing a, b → NaN) or produce wildly wrong calibration
    # (causing Tcal to go negative at the band edges). NaN at the S11
    # level propagates as NaN through every downstream product: a, b,
    # Tcal, and the S11 plot itself. This is the honest answer: the
    # calibration is only valid where the S11 fit is valid.
    s11_mask = (
        (ant_s11_model.freqs >= 58.0 * un.MHz)
        & (ant_s11_model.freqs <= 105.0 * un.MHz)
    )
    ant_s11_model = ReflectionCoefficient(
        reflection_coefficient=np.where(
            s11_mask, ant_s11_model.reflection_coefficient, np.nan + 0j
        ),
        freqs=ant_s11_model.freqs,
    )
    ant_real = np.where(s11_mask, ant_real, np.nan)
    ant_imag = np.where(s11_mask, ant_imag, np.nan)

    # ---- 4. P0/P1/P2/Q and raw spectra --------------------------------------
    p0 = raw_data.data[0, 0]
    p1 = raw_data.data[1, 0]
    p2 = raw_data.data[2, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        q = (p0 - p1) / (p2 - p1)
    lsts = raw_data.lsts[:, 0].to_value("hourangle").flatten()

    for name, arr in (("P0", p0), ("P1", p1), ("P2", p2), ("Q", q)):
        avg = np.nanmean(arr, axis=0)
        _save_freq_y(run_dir / "raw_spectra", f"{spec_date}_{name}.npz", data_freqs_mhz, avg)
        save_waterfall_jpeg(
            arr, data_freqs_mhz, lsts, run_dir / "raw_waterfalls",
            f"{spec_date}_{name}.jpg", title=f"{name} {spec_date}",
        )
        if save_2d_npz:
            save_heatmap_npz(
                run_dir / "raw_waterfalls", f"{spec_date}_{name}_2d.npz",
                x=data_freqs_mhz, y=lsts, z=arr,
            )
            save_heatmap_preview_npz(
                run_dir / "raw_waterfalls", f"{spec_date}_{name}_preview.npz",
                x=data_freqs_mhz, y=lsts, z=arr,
            )

    # ---- 5. Calibration S11s ------------------------------------------------
    print("[run] Saving calibration S11s ...")
    s11m = np.genfromtxt(s11_modelled_file, comments="#", names=True)
    for load in CAL_LOADS + ("lna",):
        _save_freq_realimag(
            run_dir / "calibration_s11", f"{s11_date}_{load}_S11.npz",
            s11m["freq"], s11m[f"{load}_real"], s11m[f"{load}_imag"],
        )

    # ---- 6. Calibration spectra (images + 1D averages) -----------------------
    print("[run] Saving calibration spectra ...")
    cal_spec_lsts: Dict[str, np.ndarray] = {}
    for load, gs in cal_data.items():
        spec = gs.data[0, 0]
        freqs_cal = gs.freqs.to_value("MHz")
        lsts_cal = gs.lsts[:, 0].to_value("hourangle").flatten()
        cal_spec_lsts[load] = lsts_cal
        save_waterfall_jpeg(
            spec, freqs_cal, lsts_cal,
            run_dir / "calibration_spectra", f"{cal_date}_{load}.jpg",
            title=f"{load} calibration spectrum {cal_date}",
        )
        spec_avg = np.nanmean(spec, axis=0)
        _save_freq_y(
            run_dir / "calibration_spectra", f"{cal_date}_{load}_avg.npz",
            freqs_cal, spec_avg,
        )
        if save_2d_npz:
            save_heatmap_npz(
                run_dir / "calibration_spectra", f"{cal_date}_{load}_2d.npz",
                x=freqs_cal, y=lsts_cal, z=spec,
            )
            save_heatmap_preview_npz(
                run_dir / "calibration_spectra", f"{cal_date}_{load}_preview.npz",
                x=freqs_cal, y=lsts_cal, z=spec,
            )

    # ---- 7. TNW coefficients -----------------------------------------------
    print("[run] Saving TNW coefficients ...")
    coeff_dir = run_dir / "calibration_coefficients"
    coeff_dir.mkdir(parents=True, exist_ok=True)
    cal_freqs_mhz = calobs.freqs.to_value("MHz")
    _save_freq_y(coeff_dir, f"{cal_date}_scale.npz", cal_freqs_mhz, np.asarray(calobs.Tsca))
    _save_freq_y(coeff_dir, f"{cal_date}_offset.npz", cal_freqs_mhz, np.asarray(calobs.Toff))
    _save_freq_y(coeff_dir, f"{cal_date}_unc.npz", cal_freqs_mhz, np.asarray(calobs.Tunc))
    _save_freq_y(coeff_dir, f"{cal_date}_cos.npz", cal_freqs_mhz, np.asarray(calobs.Tcos))
    _save_freq_y(coeff_dir, f"{cal_date}_sin.npz", cal_freqs_mhz, np.asarray(calobs.Tsin))
    # Actual linear coefficients used by ``calibrate_q``: ``Tcal = q*a + b``.
    # ``a = Tsca / K1`` and ``b = (Toff - (Tunc*K2 + Tcos*K3 + Tsin*K4)) / K1``,
    # where ``K = (K1, K2, K3, K4) = get_K(gamma_rec, gamma_ant)`` depends on
    # BOTH the receiver S11 and the antenna S11.
    #
    # EDGES's ``Calibrator.get_linear_coefficients`` (calibrator.py:119-122)
    # checks whether ``ant_s11.freqs`` matches ``calobs.freqs``; if not, it
    # re-runs the polynomial smoothing on the antenna S11. Our
    # ``ant_s11_model`` lives on the 32768-point antenna .acq grid, while
    # ``calobs.freqs`` is the 3072-point specal.txt grid, so EDGES would
    # re-smooth — and because the raw antenna data has many NaN points, the
    # re-fit yields NaN everywhere. We avoid that by interpolating the
    # already-NaN-masked antenna S11 onto the calibration grid ourselves and
    # passing it as a plain complex numpy array (EDGES's array branch checks
    # only the length match and uses the values directly).
    _ant_from_f = ant_s11_model.freqs.to_value("MHz")
    _ant_to_f = cal_freqs_mhz
    _src_re = np.real(ant_s11_model.reflection_coefficient)
    _src_im = np.imag(ant_s11_model.reflection_coefficient)
    _src_nan = np.isnan(_src_re) | np.isnan(_src_im)
    _src_re_safe = np.where(_src_nan, 0.0, _src_re)
    _src_im_safe = np.where(_src_nan, 0.0, _src_im)
    _out_re = np.interp(_ant_to_f, _ant_from_f, _src_re_safe)
    _out_im = np.interp(_ant_to_f, _ant_from_f, _src_im_safe)
    _idx = np.searchsorted(_ant_from_f, _ant_to_f).clip(1, len(_ant_from_f) - 1)
    _nn_nan = _src_nan[_idx - 1] | _src_nan[_idx]
    _out_re[_nn_nan] = np.nan
    _out_im[_nn_nan] = np.nan
    ant_s11_on_cal = _out_re + 1j * _out_im
    a_q, b_q = calobs.get_linear_coefficients(
        ant_s11=ant_s11_on_cal, freqs=calobs.freqs
    )
    _save_freq_y(coeff_dir, f"{cal_date}_a.npz", cal_freqs_mhz, np.asarray(a_q.value))
    _save_freq_y(coeff_dir, f"{cal_date}_b.npz", cal_freqs_mhz, np.asarray(b_q.value))

    # ---- 8. Calibration temperatures derived from the analysis ------------
    # The receiver calibration writes two relevant files:
    #   * ``calibrated_temps.txt`` — per-frequency load temperatures
    #     (ambient / hot_load / open / short) solved for by the noise-wave
    #     model fit.
    #   * ``specal.txt`` — the full per-frequency noise-wave model
    #     parameters, including ``Tunc`` = the LNA's noise temperature.
    # These are the *actual* values the analysis determined, NOT the
    # setpoints. The comparison plot (Calibration vs Actual Temp) plots
    # these against the probe-measured actual temperature.
    print("[run] Saving calibration temperatures (from analysis) ...")
    cal_temp_dir = run_dir / "calibration_temperatures"
    cal_temp_dir.mkdir(parents=True, exist_ok=True)

    cal_fit: Dict[str, np.ndarray] = {}
    cal_fit_freqs: Optional[np.ndarray] = None
    cal_fit_file = calib_dir / "calibrated_temps.txt"
    if cal_fit_file.exists():
        try:
            arr = np.genfromtxt(cal_fit_file, comments="#", names=True)
            cal_fit_freqs = np.asarray(arr["freq"])
            cal_fit = {
                "ambient": np.asarray(arr["ambient"], dtype=float),
                "hot":     np.asarray(arr["hot_load"], dtype=float),
                "open":    np.asarray(arr["open"], dtype=float),
                "short":   np.asarray(arr["short"], dtype=float),
            }
            print(f"[run] loaded {cal_fit_file.name} ({len(cal_fit_freqs)} freq bins)")
        except Exception as exc:
            print(f"[run] WARN: failed to parse {cal_fit_file}: {exc}")
    else:
        print(f"[run] WARN: {cal_fit_file} not found")

    def _save_cal_temp(
        npz_key: str,
        analysis_key: Optional[str],
        fallback_k: float,
    ) -> None:
        if (
            analysis_key is not None
            and analysis_key in cal_fit
            and cal_fit_freqs is not None
        ):
            _save_freq_y(
                cal_temp_dir, f"{cal_date}_{npz_key}.npz",
                cal_fit_freqs, cal_fit[analysis_key],
            )
            return
        # Fall back: write a horizontal line at the configured default
        # (setpoint). Only happens if the receiver cal didn't run.
        _save_freq_y(
            cal_temp_dir, f"{cal_date}_{npz_key}.npz",
            data_freqs_mhz, np.full_like(data_freqs_mhz, fallback_k),
        )

    _save_cal_temp("ambient", "ambient", ambient_k)
    _save_cal_temp("hot",     "hot",     hot_k)
    _save_cal_temp("open",    "open",    np.nan)
    _save_cal_temp("short",   "short",   np.nan)

    # ---- 9. Dicke + linear frontend calibration ---------------------------
    # The science output is the calibrated sky temperature. The pipeline
    # is intentionally simple: Dicke switching gives an uncalibrated
    # temperature ``Tuncal`` (the per-frequency, per-time antenna power
    # ratio in K), and the EDGES receiver calibration maps ``Tuncal`` to
    # the absolute sky temperature ``Tcal`` via the linear formula
    # ``Tcal = T0 * Tuncal + T1`` whose coefficients (``T0``, ``T1``)
    # are derived internally by EDGES from ``specal.txt`` and the
    # antenna S11 (the noise-wave correction is applied there).
    #
    # Concretely ``calobs.calibrate_approximate_temperature`` does:
    #     q = (Tuncal - t_load) / t_load_ns
    #     Tcal = a * q + b
    # where ``a`` and ``b`` come from ``specal.txt`` and the antenna
    # S11 (the noise-wave correction lives in ``b``).
    print("[run] Dicke calibration ...")
    dicke_data = dicke_calibration(raw_data)
    approx_temp = approximate_temperature(dicke_data, tload=tload, tns=tns)

    print("[run] Frontend calibration (EDGES linear) ...")
    tuncal_2d = approx_temp.data[0, 0]                          # (n_time, n_freq)
    # Apply the EDGES linear calibration. This handles the freq-grid
    # interpolation between ``specal.txt`` (40-190 MHz, 3072 bins) and
    # the observation data (0-200 MHz, 32768 bins) internally.
    cal_obj = calobs.calibrate_approximate_temperature(
        np.asarray(approx_temp.data[0, 0]),                     # (n_time, n_freq) ndarray
        t_load=tload,
        t_load_ns=tns,
        ant_s11=ant_s11_model.s11,
        freqs=approx_temp.freqs,
    )
    cal_2d = np.asarray(cal_obj.value)                          # (n_time, n_freq)

    avg_temp_data = np.nanmean(tuncal_2d, axis=0)             # time-averaged Tuncal
    cal_temp_data = np.nanmean(cal_2d, axis=0)                # time-averaged Tcal
    _save_freq_y(
        run_dir / "average_temperature", f"{spec_date}_avg_temp.npz",
        data_freqs_mhz, avg_temp_data,
    )
    _save_freq_y(
        run_dir / "calibrated_temperature", f"{spec_date}_cal_temp.npz",
        data_freqs_mhz, cal_temp_data,
    )

    calibrated_lsts = approx_temp.lsts.to_value("hourangle").flatten()
    save_waterfall_jpeg(
        cal_2d, data_freqs_mhz, calibrated_lsts,
        run_dir / "calibrated_waterfalls", f"{spec_date}_calibrated.jpg",
        title=f"Calibrated temperature {spec_date}",
    )
    if save_2d_npz:
        save_heatmap_npz(
            run_dir / "calibrated_waterfalls", f"{spec_date}_calibrated_2d.npz",
            x=data_freqs_mhz, y=calibrated_lsts, z=cal_2d,
        )
        save_heatmap_preview_npz(
            run_dir / "calibrated_waterfalls", f"{spec_date}_calibrated_preview.npz",
            x=data_freqs_mhz, y=calibrated_lsts, z=cal_2d,
        )

    # ---- 10. Antenna S11 in its own folder --------------------------------
    _save_freq_realimag(
        run_dir / "antenna_s11", f"{s11_date}_antenna_S11.npz",
        ant_freqs_mhz, ant_real, ant_imag,
    )

    # ---- 11. Actual temperature -------------------------------------------
    # Step 11 is the "noise-wave fit vs actual probe" comparison. The
    # actual probe temperature must come from the same source as the
    # calibration temperatures in step 0 so the two sides of the plot
    # agree: first try the ``.tmp`` snapshot taken at the cal/obs
    # moment, then fall back to the nearest-in-time log block. Probes
    # come from ``config.PROBE_*`` (so ``EDGES_PROBE_LNA=150`` etc. are
    # honoured here too).
    actual_temp_dir = run_dir / "actual_temperature"
    actual_temp_dir.mkdir(parents=True, exist_ok=True)
    # The ambient probe at the observation time (catalog; see step 0).
    obs = temps["obs_ambient"]
    actual_temp_c = _celsius(obs)
    actual_temp_k = actual_temp_c + 273.15 if actual_temp_c is not None else np.nan
    _save_freq_y(
        actual_temp_dir, f"{spec_date}_actual_temp.npz",
        data_freqs_mhz, np.full_like(data_freqs_mhz, actual_temp_k),
        metadata={
            "time": obs.get("time"),
            "temperature_k": float(actual_temp_k) if np.isfinite(actual_temp_k) else None,
            "temperature_c": actual_temp_c,
            "source": obs.get("source"),
        },
    )

    # ---- 11b. Per-load actual temperature (matched by timestamp) ----------
    # For each calibration load, the probe reading at the moment THAT
    # calibration spectrum was taken, so the "ambient vs actual" and "hot vs
    # actual" plots line up in time. These are the same readings the
    # calibration used (step 0).
    per_load_meta: Dict[str, Dict[str, Any]] = {}
    for display_name in ("ambient", "hot"):
        e = temps[display_name]
        load_actual_c = _celsius(e)
        load_actual_k = load_actual_c + 273.15 if load_actual_c is not None else np.nan
        per_load_meta[display_name] = {
            "time": e.get("time"),
            "temperature_k": float(load_actual_k) if np.isfinite(load_actual_k) else None,
            "probe": e["probe"],
            "source": e["source"],
        }
        _save_freq_y(
            actual_temp_dir, f"{spec_date}_{display_name}_actual_temp.npz",
            data_freqs_mhz, np.full_like(data_freqs_mhz, load_actual_k),
            metadata={
                "time": e.get("time"),
                "temperature_k": float(load_actual_k) if np.isfinite(load_actual_k) else None,
                "temperature_c": load_actual_c,
                "probe": e["probe"],
                "source": e["source"],
            },
        )

    # ---- 12. Build manifest -------------------------------------------------
    plots: List[Plot] = []
    rel = lambda *parts: f"runs/{run_dir.name}/" + "/".join(parts)

    # Calibration page
    for load in CAL_LOADS + ("lna",):
        plots.append(Plot(
            page=PAGE_CALIBRATION, id=f"{load}_s11", type="s11",
            title=f"{load.upper()} S11",
            filePath=rel("calibration_s11", f"{s11_date}_{load}_S11.npz"),
        ))
    for load in CAL_LOADS:
        plots.append(Plot(
            page=PAGE_CALIBRATION, id=f"{load}_cal_spectrum", type="image",
            title=f"{load.capitalize()} Calibration Spectrum  (Frequency [MHz] vs LST [hr])",
            filePath=rel("calibration_spectra", f"{cal_date}_{load}.jpg"),
        ))
        if save_2d_npz:
            plots.append(Plot(
                page=PAGE_CALIBRATION, id=f"{load}_waterfall", type="heatmap",
                title=f"{load.capitalize()} Calibration Waterfall  (Frequency [MHz] vs LST [hr])",
                filePath=rel("calibration_spectra", f"{cal_date}_{load}_preview.npz"),
            ))
    for coeff in ("scale", "offset", "unc", "cos", "sin", "scale_temperature", "offset_temperature"):
        title_map = {
            "scale": "Scale TNW  [K]",
            "offset": "Offset TNW  [K]",
            "unc": "Unc TNW  [K]",
            "cos": "Cos TNW  [K]",
            "sin": "Sin TNW  [K]",
            "scale_temperature": "Scale temperature  [K]",
            "offset_temperature": "Offset temperature  [K]",
        }
        # ``scale_temperature`` and ``offset_temperature`` are the actual
        # linear coefficients ``a`` and ``b`` used by ``Tcal = q*a + b``
        # (not the raw ``Tsca``/``Toff`` inputs). They're saved with the
        # ``_a``/``_b`` filename suffixes so the npz files match their
        # physical meaning; the plot id and title stay the same.
        file_suffix = "a" if coeff == "scale_temperature" else (
            "b" if coeff == "offset_temperature" else coeff
        )
        plots.append(Plot(
            page=PAGE_CALIBRATION, id=coeff, type="single",
            title=title_map[coeff],
            filePath=rel("calibration_coefficients", f"{cal_date}_{file_suffix}.npz"),
            xKey="x", yKey="y",
            axisx="Frequency [MHz]",
            axisy="Temperature [K]",
        ))
    # Calibration temperature comparison plots: only ambient and hot (not
    # the LNA noise temperature — that visualization was retired). These
    # compare the noise-wave fit (i.e. the per-frequency temperature the
    # receiver sees against the known load) to the on-site temperature-
    # log probe reading at the same moment.
    for load in ("ambient", "hot"):
        meta = per_load_meta.get(load, {})
        meta_time = meta.get("time")
        meta_temp = meta.get("temperature_k")
        if load == "ambient":
            title = "Ambient Calibration: Noise-wave Fit vs Ambient Probe  [K]"
            label1, label2 = "Noise-wave fit (load temp) [K]", f"Ambient probe (probe {config.PROBE_AMBIENT:.0f}) [K]"
        else:  # hot
            title = "Hot Calibration: Noise-wave Fit Without Loss Model vs Hot-Load Probe  [K]"
            label1, label2 = "Noise-wave fit (load temp) [K]", f"Hot-load probe (probe {config.PROBE_HOT:.0f}) [K]"
        plots.append(Plot(
            page=PAGE_CALIBRATION, id=f"{load}_vs_actual", type="multi",
            title=title,
            name1=label1, name2=label2,
            filePath1=rel("calibration_temperatures", f"{cal_date}_{load}.npz"),
            filePath2=rel("actual_temperature", f"{spec_date}_{load}_actual_temp.npz"),
            axisx="Frequency [MHz]",
            axisy="Temperature [K]",
            time=meta_time,
            temperature_k=meta_temp,
        ))

    # Calibrated page
    plots.append(Plot(
        page=PAGE_CALIBRATED, id="antenna_s11", type="s11",
        title="Antenna S11",
        filePath=rel("antenna_s11", f"{s11_date}_antenna_S11.npz"),
    ))
    plots.append(Plot(
        page=PAGE_CALIBRATED, id="calibrated_temperature", type="single",
        title="Calibrated Temperature  [K]",
        filePath=rel("calibrated_temperature", f"{spec_date}_cal_temp.npz"),
        xKey="x", yKey="y",
        axisx="Frequency [MHz]",
        axisy="Temperature [K]",
    ))
    if save_2d_npz:
        plots.append(Plot(
            page=PAGE_CALIBRATED, id="calibrated_waterfall", type="heatmap",
            title="Calibrated Temperature Waterfall  (Frequency [MHz] vs LST [hr])",
            filePath=rel("calibrated_waterfalls", f"{spec_date}_calibrated_preview.npz"),
        ))

    # Raw page
    for name, ylabel, axisy in (
        ("P0", "Antenna P0  [arb. units]", "Power [arb. units]"),
        ("P1", "Antenna P1  [arb. units]", "Power [arb. units]"),
        ("P2", "Antenna P2  [arb. units]", "Power [arb. units]"),
        ("Q",  "Antenna Q  (unitless)",   "Q (unitless)"),
    ):
        plots.append(Plot(
            page=PAGE_RAW, id=name, type="single",
            title=ylabel,
            filePath=rel("raw_spectra", f"{spec_date}_{name}.npz"),
            xKey="x", yKey="y",
            axisx="Frequency [MHz]",
            axisy=axisy,
        ))
        if save_2d_npz:
            plots.append(Plot(
                page=PAGE_RAW, id=f"{name}_waterfall", type="heatmap",
                title=f"{name} Waterfall  (Frequency [MHz] vs LST [hr])",
                filePath=rel("raw_waterfalls", f"{spec_date}_{name}_preview.npz"),
            ))
    plots.append(Plot(
        page=PAGE_RAW, id="avg_temp", type="single",
        title="Average uncalibrated temperature  [K]",
        filePath=rel("average_temperature", f"{spec_date}_avg_temp.npz"),
        xKey="x", yKey="y",
        axisx="Frequency [MHz]",
        axisy="Temperature [K]",
    ))

    manifest_path = write_manifest(
        output_root=output_root,
        run_dir=run_dir,
        source=source,
        plots=plots,
        dates=dates,
    )
    print(f"[run] Manifest written: {manifest_path}")

    # The backend API stashes this run into user_cache/ on the next call
    # (the run_id encodes the parameter hash so dedup is just a directory
    # lookup). No provenance marker is written here.
    return manifest_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="EDGES-3 single-day analysis. Inputs come from the EDGES "
        "catalog: either --inputs (written by the backend) or the three dates."
    )
    p.add_argument("--inputs", default=None,
                   help="inputs.json from catalog_inputs.resolve_inputs (backend runs)")
    p.add_argument("--cal_date", default=None, help="YYYY_DDD calibration date (or 'Latest')")
    p.add_argument("--s11_date", default=None,
                   help="YYYY_DDD_HH S11 session stem (or 'Latest': the catalog's pick)")
    p.add_argument("--spec_date", default=None, help="YYYY_DDD_HH_MM_SS antenna timestamp")
    p.add_argument("--output_root", required=True,
                   help="Display root (e.g. OUTPUT_ROOT/daemon or OUTPUT_ROOT/user)")
    p.add_argument("--run_dir", required=True,
                   help="Full path of the run directory (e.g. OUTPUT_ROOT/daemon/runs/<id>)")
    p.add_argument("--cterms", type=int, default=DEFAULT_CTERMS)
    p.add_argument("--wterms", type=int, default=DEFAULT_WTERMS)
    p.add_argument("--fstart", type=float, default=DEFAULT_FSTART)
    p.add_argument("--fstop", type=float, default=DEFAULT_FSTOP)
    p.add_argument("--wfstart", type=float, default=DEFAULT_WFSTART)
    p.add_argument("--wfstop", type=float, default=DEFAULT_WFSTOP)
    p.add_argument("--save_2d_npz", action="store_true",
                   help="Also save heatmap .npz data (off by default)")
    p.add_argument("--source", default="user",
                   help="Provenance label stored in the manifest (daemon|user)")
    p.add_argument("--run_hash", default=None,
                   help="Optional pre-computed parameter hash (for dedup).")
    args = p.parse_args(argv)

    config.ensure_dirs()
    if args.inputs:
        if args.cal_date or args.s11_date or args.spec_date:
            p.error("give either --inputs or the dates, not both")
        with open(args.inputs) as f:
            inputs = json.load(f)
    else:
        if not (args.cal_date and args.spec_date):
            p.error("give --inputs, or --cal_date and --spec_date (and --s11_date)")
        try:
            import catalog_inputs  # noqa: E402  (needs edges-catalog/edges-pipeline)

            with catalog_inputs.open_catalog() as cat:
                resolved = catalog_inputs.resolve_dates(
                    cat,
                    {"cal": args.cal_date, "s11": args.s11_date or "Latest", "raw": args.spec_date},
                    catalog_inputs.available_dates(cat),
                )
                inputs = catalog_inputs.resolve_inputs(cat, resolved)
        except ValueError as e:  # catalog_inputs.InputError: unknown date/session
            p.error(str(e))
        except Exception as e:  # e.g. packages or catalog missing (HTTPException 503)
            p.error(f"cannot resolve the inputs in the catalog: {getattr(e, 'detail', e)}")
        run_dir = Path(args.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        with open(run_dir / "inputs.json", "w") as f:
            json.dump(inputs, f, indent=2)
    process_single_day(
        inputs,
        output_root=Path(args.output_root),
        run_dir=Path(args.run_dir),
        cterms=args.cterms, wterms=args.wterms,
        fstart=args.fstart, fstop=args.fstop,
        wfstart=args.wfstart, wfstop=args.wfstop,
        save_2d_npz=args.save_2d_npz,
        source=args.source,
        run_hash=args.run_hash,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
