#!/usr/bin/env python3
"""
EDGES-3 calibration and observation pipeline
============================================

Two stages, run by the website's job runner (``runs_api.py``) or by hand:

* ``--stage calibration``: the receiver calibration of one day (the four
  calibration loads and a full S11 session) with ``alancal_edges3``; writes
  the modelled S11s, the noise-wave parameters, the calibrated load
  temperatures (with the hot-load loss removed from the hot load) and the
  calibration spectra.
* ``--stage observation``: one night of antenna spectra, calibrated with a
  finished calibration and an antenna S11 session; writes the night-mean
  spectra (P_ant, P_load, P_LNS, Q, R, uncalibrated and calibrated
  temperature), the antenna S11, the linear coefficients and waterfalls. The
  night's files are processed one at a time, so memory stays bounded.

Inputs (exact files, temperatures) come from the catalog
(``catalog_inputs.resolve_*``) as an ``inputs.json``; outputs are a
``result.json`` plus ``plots.npz`` (and ``waterfalls.npz``) in the run
directory. Raw data are only read.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from astropy import units as un

# Allow ``python run_single_day.py`` to import the site's modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))

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
from edges.cal import ReflectionCoefficient, S11ModelParams, sparams as sp  # noqa: E402
from edges.frequencies import get_mask  # noqa: E402
import edges.io as io  # noqa: E402
import edges.modeling as mdl  # noqa: E402


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_CTERMS = 6
DEFAULT_WTERMS = 5
DEFAULT_FSTART = 40.0
DEFAULT_FSTOP = 190.0
DEFAULT_WFSTART = 40.0
DEFAULT_WFSTOP = 190.0
#: Antenna S11 model: fit window and number of terms. Outside ~58-105 MHz the
#: EDGES-3 antenna is poorly matched (|S11| up to ~0.9) and a single model fits
#: ~300x worse, so the calibration (a, b, T_cal) is only produced in this window.
DEFAULT_ANT_S11_FSTART = 58.0
DEFAULT_ANT_S11_FSTOP = 105.0
DEFAULT_ANT_S11_NTERMS = 12

CAL_LOADS = ("amb", "hot", "open", "short")
S11_LOADS = ("amb", "hot", "open", "short", "lna")

#: Nominal Dicke-switch temperatures. ``alancal_edges3`` writes ``specal.txt``
#: with ``t_load=T_LOAD, t_load_ns=T_NS`` (``AlanCalOpts.avg.tload/tcal``), and
#: ``read_specal`` rebuilds ``Tsca = t_load_ns*C1`` and ``Toff = t_load - C2``
#: from whatever it is given, so the file must be read, and Q converted to an
#: approximate temperature, with the same values. (They are conventions, not
#: measured temperatures: the probe readings enter as the known load
#: temperatures of the fit.)
T_LOAD = 300.0
T_NS = 1000.0

#: Waterfall frequency bins (MHz) and band.
WF_BIN_MHZ = 0.25
WF_BAND_MHZ = (40.0, 200.0)


def parse_yyyy_ddd(s: str) -> Tuple[int, int]:
    """Parse a ``YYYY_DDD`` date. Raises ValueError on bad input."""
    m = re.fullmatch(r"(\d{4})_(\d{3})", s)
    if not m:
        raise ValueError(f"Not a YYYY_DDD date: {s!r}")
    return int(m.group(1)), int(m.group(2))


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
                    tload=T_LOAD,
                    tcal=T_NS,
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


# ---------------------------------------------------------------------------
# Antenna S11
# ---------------------------------------------------------------------------
def compute_antenna_s11(
    root: Path,
    s11_run: str,
    target_freqs: Any,
    f_low: float = DEFAULT_ANT_S11_FSTART,
    f_high: float = DEFAULT_ANT_S11_FSTOP,
    n_terms: int = DEFAULT_ANT_S11_NTERMS,
) -> Tuple[ReflectionCoefficient, ReflectionCoefficient]:
    """The antenna S11 model at ``target_freqs`` and the calibrated measurement.

    The model is fitted in ``[f_low, f_high]`` MHz and set to NaN outside it:
    extrapolated polynomial values there are meaningless (magnitudes ~1e14),
    and NaN propagates honestly through a, b and T_cal.
    """
    base = root / s11_run
    ck = sp.CalkitReadings.from_filespec(
        io.CalkitFileSpec(open=f"{base}_O.s1p", short=f"{base}_S.s1p", match=f"{base}_L.s1p")
    )
    calkit = sp.get_calkit(
        sp.AGILENT_ALAN,
        resistance_of_match=49.930 * un.ohm,
        short={"offset_delay": 33 * un.ps},
        open={"offset_delay": 33 * un.ps},
        match={"offset_delay": 33 * un.ps},
    )
    ant_s11_raw = sp.ReflectionCoefficient.from_s1p(f"{base}_ant.s1p")
    gamma_ant = sp.calibrate_gamma_src(gamma_src=ant_s11_raw, internal_calkit=calkit, internal_osl=ck)

    mask = get_mask(gamma_ant.freqs, f_low * un.MHz, f_high * un.MHz)
    in_window = ReflectionCoefficient(
        reflection_coefficient=gamma_ant.reflection_coefficient[mask], freqs=gamma_ant.freqs[mask]
    )
    params = S11ModelParams(
        model=mdl.Polynomial(
            n_terms=n_terms, transform=mdl.Log10Transform(scale=(f_low + f_high) / 2)
        ),
        complex_model_type=mdl.ComplexRealImagModel,
        set_transform_range=True,
        fit_method="alan-qrd",
        find_model_delay=True,
    )
    model = in_window.smoothed(freqs=target_freqs, params=params)
    inside = (model.freqs >= f_low * un.MHz) & (model.freqs <= f_high * un.MHz)
    model = ReflectionCoefficient(
        reflection_coefficient=np.where(inside, model.reflection_coefficient, np.nan + 0j),
        freqs=model.freqs,
    )
    return model, gamma_ant


def _on_grid(src: ReflectionCoefficient, to_mhz: np.ndarray) -> np.ndarray:
    """Interpolate a NaN-masked S11 onto ``to_mhz``; NaN where a neighbour is NaN."""
    f = src.freqs.to_value("MHz")
    re, im = np.real(src.reflection_coefficient), np.imag(src.reflection_coefficient)
    bad = np.isnan(re) | np.isnan(im)
    out = np.interp(to_mhz, f, np.where(bad, 0, re)) + 1j * np.interp(to_mhz, f, np.where(bad, 0, im))
    idx = np.searchsorted(f, to_mhz).clip(1, len(f) - 1)
    out[bad[idx - 1] | bad[idx]] = np.nan
    return out


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _f32(x: Any) -> np.ndarray:
    return np.asarray(x, dtype=np.float32)


def _q(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray) -> np.ndarray:
    """The Dicke ratio Q = (P_ant - P_load) / (P_LNS - P_load)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return (p0 - p1) / (p2 - p1)


def _bin_index(freqs_mhz: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(channel selection, bin index per selected channel, bin centres) for waterfalls."""
    lo, hi = WF_BAND_MHZ
    edges = np.arange(lo, hi + 1e-9, WF_BIN_MHZ)
    sel = (freqs_mhz >= edges[0]) & (freqs_mhz < edges[-1])
    idx = np.searchsorted(edges, freqs_mhz[sel], side="right") - 1
    return sel, idx, (edges[:-1] + edges[1:]) / 2


def _bin_rows(x: np.ndarray, sel: np.ndarray, idx: np.ndarray, nbins: int) -> np.ndarray:
    """NaN-aware mean of each row of ``x`` over frequency bins (NaN for empty bins)."""
    v = x[:, sel]
    ok = np.isfinite(v)
    out = np.full((x.shape[0], nbins), np.nan)
    present = np.unique(idx)  # bins with channels; channels are sorted by bin
    if len(present) == 0:
        return out
    starts = np.searchsorted(idx, present)
    total = np.add.reduceat(np.where(ok, v, 0.0), starts, axis=1)
    count = np.add.reduceat(ok, starts, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[:, present] = total / count
    return out


class _Mean:
    """Running NaN-aware mean over time of per-channel spectra."""

    def __init__(self) -> None:
        self.sum: Optional[np.ndarray] = None
        self.n: Optional[np.ndarray] = None

    def add(self, x: np.ndarray) -> None:
        ok = np.isfinite(x)
        s, n = np.where(ok, x, 0.0).sum(axis=0), ok.sum(axis=0)
        self.sum = s if self.sum is None else self.sum + s
        self.n = n if self.n is None else self.n + n

    def value(self) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return self.sum / self.n


def _save(run_dir: Path, name: str, arrays: Dict[str, Any]) -> str:
    np.savez(run_dir / name, **arrays)
    return name


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=float))
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Stage 1: calibration
# ---------------------------------------------------------------------------
def run_calibration(inputs: Dict[str, Any], run_dir: Path, params: Dict[str, Any]) -> Dict[str, Any]:
    """Receiver calibration of one day. Returns (and writes) ``result.json``."""
    tic = time.time()
    run_dir.mkdir(parents=True, exist_ok=True)
    cal_date, s11_run = inputs["dates"]["cal"], inputs["dates"]["s11"]
    root = Path(inputs["root"])
    files, temps = inputs["files"], inputs["temperatures"]
    for issue in inputs.get("issues", []):
        print(f"[run] input issue: {issue}")
    ambient_k = temps["ambient"]["temperature_k"]
    hot_k = temps["hot"]["temperature_k"]
    for name in ("ambient", "hot"):
        e = temps[name]
        print(f"[run] {name} load: {e['temperature_k']:.2f} K (probe {e['probe']:.0f}, {e['source']})")

    print("[run] Receiver calibration ...")
    calib_dir = run_dir / "calibration"
    specal_file, s11_modelled_file = run_receiver_calibration(
        root, cal_date, s11_run, list(files["s11"].values()), calib_dir,
        int(params["cterms"]), int(params["wterms"]),
        ambient_temp_k=ambient_k, hot_temp_k=hot_k, cable_temp_k=ambient_k,
        fstart=float(params["fstart"]), fstop=float(params["fstop"]),
        wfstart=float(params["wfstart"]), wfstop=float(params["wfstop"]),
    )
    calobs = read_specal(specal_file, t_load=T_LOAD, t_load_ns=T_NS)
    arrays: Dict[str, Any] = {}

    # modelled S11s of the loads and the receiver (LNA)
    s11m = np.genfromtxt(s11_modelled_file, comments="#", names=True)
    arrays["s11_freq"] = np.asarray(s11m["freq"])
    for load in S11_LOADS:
        arrays[f"s11_{load}_re"] = _f32(s11m[f"{load}_real"])
        arrays[f"s11_{load}_im"] = _f32(s11m[f"{load}_imag"])

    # noise-wave parameters
    arrays["nw_freq"] = calobs.freqs.to_value("MHz")
    for k in ("Tsca", "Toff", "Tunc", "Tcos", "Tsin"):
        arrays[f"nw_{k}"] = _f32(getattr(calobs, k))

    # calibrated load temperatures vs the known ones (edges-analysis outputs)
    ct = np.genfromtxt(calib_dir / "calibrated_temps.txt", comments="#", names=True)
    kt = np.genfromtxt(calib_dir / "known_load_temps.txt", comments="#", names=True)
    loss = np.loadtxt(calib_dir / "hot_load_loss.txt")
    arrays["lt_freq"] = np.asarray(ct["freq"])
    for name in ("ambient", "hot_load", "open", "short"):
        arrays[f"lt_cal_{name}"] = _f32(ct[name])
        arrays[f"lt_known_{name}"] = _f32(kt[name])
    # The fit sees the hot load through its cable: T_in = G T_hot + (1 - G) T_amb
    # (edges InputSource.temp_ave). Remove the loss to compare with the probe.
    gain = np.interp(ct["freq"], loss[:, 0], loss[:, 1])
    arrays["lt_hot_gain"] = _f32(gain)
    arrays["lt_cal_hot_load_delossed"] = _f32((ct["hot_load"] - (1 - gain) * ambient_k) / gain)
    arrays["lt_probe_hot_load"] = _f32(np.full(ct["freq"].size, hot_k))

    # calibration spectra: mean Q per load, and waterfalls of the change over
    # time as an equivalent temperature, T_NS (Q - median over time) [K]: a
    # small dynamic range that shows drifts and RFI, also where Q ~ 0 (ambient)
    print("[run] Calibration spectra ...")
    wf: Dict[str, Any] = {}
    n_cycles: Dict[str, int] = {}
    cal_data = read_calibration_acq(files, cal_date)
    for load, gs in cal_data.items():
        freqs = gs.freqs.to_value("MHz")
        q = _q(gs.data[0, 0], gs.data[1, 0], gs.data[2, 0])
        n_cycles[load] = int(q.shape[0])
        arrays["spec_freq"] = freqs
        arrays[f"spec_q_{load}"] = _f32(np.nanmean(q, axis=0))
        sel, idx, centres = _bin_index(freqs)
        qb = _bin_rows(q, sel, idx, len(centres))
        with np.errstate(invalid="ignore"):
            wf[f"{load}_dev"] = _f32(T_NS * (qb - np.nanmedian(qb, axis=0)))
        wf["freq"] = centres
        wf[f"{load}_time_unix"] = np.asarray(gs.times[:, 0].unix)
        del gs, q
    del cal_data

    warnings_doc = {}
    warnings_path = run_dir / "s11_grid_warnings.json"
    if warnings_path.exists():
        warnings_doc = json.loads(warnings_path.read_text())

    result = {
        "kind": "calibration",
        "dates": inputs["dates"],
        "params": params,
        "temperatures": {"ambient_k": ambient_k, "hot_k": hot_k},
        "t_load": T_LOAD,
        "t_load_ns": T_NS,
        "hot_load_gain": [float(np.nanmin(gain)), float(np.nanmax(gain))],
        "n_cycles": n_cycles,
        "s11_grid": warnings_doc,
        "issues": inputs.get("issues", []),
        "data": {"plots": _save(run_dir, "plots.npz", arrays),
                 "waterfalls": _save(run_dir, "waterfalls.npz", wf)},
        "seconds": round(time.time() - tic, 1),
    }
    _write_json(run_dir / "result.json", result)
    print(f"[run] calibration done in {result['seconds']} s")
    return result


# ---------------------------------------------------------------------------
# Stage 2: observation (one night)
# ---------------------------------------------------------------------------
def run_observation(
    inputs: Dict[str, Any], calibration_dir: Path, run_dir: Path, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Calibrate one night of antenna spectra. Returns (and writes) ``result.json``."""
    tic = time.time()
    run_dir.mkdir(parents=True, exist_ok=True)
    cal_result = json.loads((calibration_dir / "result.json").read_text())
    calobs = read_specal(calibration_dir / "calibration" / "specal.txt", t_load=T_LOAD, t_load_ns=T_NS)
    night = inputs["night"]
    t0, t1 = float(night["start_unix"]), float(night["end_unix"])
    root = Path(inputs["root"])
    ant_s11 = inputs["dates"]["ant_s11"]
    f_lo = float(params.get("ant_s11_fstart", DEFAULT_ANT_S11_FSTART))
    f_hi = float(params.get("ant_s11_fstop", DEFAULT_ANT_S11_FSTOP))
    n_terms = int(params.get("ant_s11_nterms", DEFAULT_ANT_S11_NTERMS))
    for issue in inputs.get("issues", []):
        print(f"[run] input issue: {issue}")

    means = {k: _Mean() for k in ("pant", "pload", "plns", "q", "r", "tuncal", "tcal")}
    wf_rows: Dict[str, List[np.ndarray]] = {"q": [], "tcal": [], "time_unix": [], "lst": []}
    freqs_mhz = None
    ant_model = gamma_ant = None
    per_file = []
    for f in inputs["files"]["ant"]:
        print(f"[run] {f['name']} ...")
        gs = read_antenna_acq(f["path"], f["name"])
        t_unix = np.asarray(gs.times[:, 0].unix)
        keep = (t_unix >= t0) & (t_unix < t1)
        per_file.append({"name": f["name"], "n_cycles_night": int(keep.sum()),
                         "obs_ambient": f["temperatures"]["obs_ambient"]})
        if not keep.any():
            continue
        if freqs_mhz is None:
            freqs_mhz = gs.freqs.to_value("MHz")
            ant_model, gamma_ant = compute_antenna_s11(
                root, ant_s11, target_freqs=gs.freqs, f_low=f_lo, f_high=f_hi, n_terms=n_terms
            )
            sel, idx, centres = _bin_index(freqs_mhz)
            cal_f = calobs.freqs.to_value("MHz")
            cal_band = (freqs_mhz >= cal_f.min()) & (freqs_mhz <= cal_f.max())
        p0, p1, p2 = (np.asarray(gs.data[k, 0])[keep] for k in range(3))
        q = _q(p0, p1, p2)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = p1 / (p2 - p1)
        tuncal = q * T_NS + T_LOAD
        tcal = np.asarray(calobs.calibrate_approximate_temperature(
            tuncal, t_load=T_LOAD, t_load_ns=T_NS, ant_s11=ant_model.s11, freqs=gs.freqs,
        ))
        # edges interpolates the calibration with extrapolating splines: keep
        # only the calibrated band
        tcal[:, ~cal_band] = np.nan
        for k, v in (("pant", p0), ("pload", p1), ("plns", p2), ("q", q), ("r", r),
                     ("tuncal", tuncal), ("tcal", tcal)):
            means[k].add(v)
        wf_rows["q"].append(_f32(_bin_rows(q, sel, idx, len(centres))))
        wf_rows["tcal"].append(_f32(_bin_rows(tcal, sel, idx, len(centres))))
        wf_rows["time_unix"].append(t_unix[keep])
        wf_rows["lst"].append(np.asarray(gs.lsts[:, 0].to_value("hourangle")).ravel()[keep])
        del gs, p0, p1, p2, q, r, tuncal, tcal
    if freqs_mhz is None:
        raise RuntimeError("no antenna cycles inside the night")

    # linear coefficients T_cal = a Q + b on the calibration grid
    a, b = calobs.get_linear_coefficients(ant_s11=_on_grid(ant_model, cal_f), freqs=calobs.freqs)

    arrays = {
        "freq": freqs_mhz,
        **{f"mean_{k}": _f32(m.value()) for k, m in means.items()},
        "ab_freq": cal_f, "a": _f32(a.value), "b": _f32(b.value),
        "ant_s11_freq": freqs_mhz,
        "ant_s11_re": _f32(np.real(ant_model.reflection_coefficient)),
        "ant_s11_im": _f32(np.imag(ant_model.reflection_coefficient)),
        "ant_s11_meas_freq": gamma_ant.freqs.to_value("MHz"),
        "ant_s11_meas_re": _f32(np.real(gamma_ant.reflection_coefficient)),
        "ant_s11_meas_im": _f32(np.imag(gamma_ant.reflection_coefficient)),
    }
    wf = {
        "freq": centres,
        "time_unix": np.concatenate(wf_rows["time_unix"]),
        "lst": np.concatenate(wf_rows["lst"]),
        "q": np.concatenate(wf_rows["q"]),
        "tcal": np.concatenate(wf_rows["tcal"]),
    }
    result = {
        "kind": "observation",
        "night": night,
        "dates": inputs["dates"],
        "calibration": {"id": calibration_dir.name, "dates": cal_result["dates"],
                        "params": cal_result["params"]},
        "params": params,
        "ant_s11_window_mhz": [f_lo, f_hi],
        "calibration_band_mhz": [float(cal_f.min()), float(cal_f.max())],
        "t_load": T_LOAD,
        "t_load_ns": T_NS,
        "files": per_file,
        "n_cycles": int(len(wf["time_unix"])),
        "issues": inputs.get("issues", []),
        "data": {"plots": _save(run_dir, "plots.npz", arrays),
                 "waterfalls": _save(run_dir, "waterfalls.npz", wf)},
        "seconds": round(time.time() - tic, 1),
    }
    _write_json(run_dir / "result.json", result)
    print(f"[run] observation done in {result['seconds']} s")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="EDGES-3 calibration / observation stage. Inputs come from the "
        "EDGES catalog: --inputs (written by the website) or dates resolved here."
    )
    p.add_argument("--stage", choices=("calibration", "observation"), required=True)
    p.add_argument("--run_dir", required=True, help="Output directory of this run")
    p.add_argument("--inputs", default=None, help="inputs.json (catalog_inputs.resolve_*)")
    p.add_argument("--params", default=None, help="JSON file of stage parameters")
    p.add_argument("--calibration_dir", default=None,
                   help="Observation: directory of a finished calibration run")
    p.add_argument("--cal_date", default=None, help="Calibration: YYYY_DDD (or Latest)")
    p.add_argument("--s11_date", default=None, help="Calibration: S11 session stem (or Latest)")
    p.add_argument("--night", default=None, help="Observation: night YYYY-MM-DD (or Latest)")
    p.add_argument("--ant_s11", default=None, help="Observation: antenna S11 stem (or Latest)")
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    params = json.loads(Path(args.params).read_text()) if args.params else {}
    params = {**DEFAULT_PARAMS[args.stage], **params}
    if args.inputs:
        inputs = json.loads(Path(args.inputs).read_text())
    else:
        try:
            import catalog_inputs  # noqa: E402  (needs edges-catalog/edges-pipeline)

            with catalog_inputs.open_catalog() as cat:
                if args.stage == "calibration":
                    inputs = catalog_inputs.resolve_calibration(
                        cat, args.cal_date or "Latest", args.s11_date or "Latest")
                else:
                    inputs = catalog_inputs.resolve_observation(
                        cat, args.night or "Latest", args.ant_s11 or "Latest")
        except ValueError as e:  # catalog_inputs.InputError
            p.error(str(e))
        except Exception as e:  # e.g. packages or catalog missing (HTTPException 503)
            p.error(f"cannot resolve the inputs in the catalog: {getattr(e, 'detail', e)}")
        _write_json(run_dir / "inputs.json", inputs)
    if args.stage == "calibration":
        run_calibration(inputs, run_dir, params)
    else:
        if not args.calibration_dir:
            p.error("--calibration_dir is required for the observation stage")
        run_observation(inputs, Path(args.calibration_dir), run_dir, params)
    return 0


DEFAULT_PARAMS: Dict[str, Dict[str, Any]] = {
    "calibration": {
        "cterms": DEFAULT_CTERMS, "wterms": DEFAULT_WTERMS,
        "fstart": DEFAULT_FSTART, "fstop": DEFAULT_FSTOP,
        "wfstart": DEFAULT_WFSTART, "wfstop": DEFAULT_WFSTOP,
    },
    "observation": {
        "ant_s11_fstart": DEFAULT_ANT_S11_FSTART,
        "ant_s11_fstop": DEFAULT_ANT_S11_FSTOP,
        "ant_s11_nterms": DEFAULT_ANT_S11_NTERMS,
    },
}


if __name__ == "__main__":
    raise SystemExit(main())
