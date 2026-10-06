#!/usr/bin/env python3
"""
EDGES-3 calibration and observation stages
==========================================

Two stages, run by the website's job runner (``runs_api.py``) or by hand:

* ``--stage calibration``: a receiver calibration of one day with
  *non-default* settings, computed by edges-pipeline
  (``edges_pipeline.stages.rcal.calibrate_day``: the stage's own code, inputs
  from the catalog). Writes the solution (``rcal.h5``, in the layout of a stored rcal
  product) and
  ``result.json``, the JSON the Calibrations page plots
  (``calibrations.calibration_json``). The default settings are never
  computed here: the pipeline stores them for every day.
* ``--stage observation``: one night of antenna spectra, calibrated with a
  receiver calibration (stored by the pipeline, or computed by the stage
  above) and an antenna S11 session; writes the night-mean spectra (P_ant,
  P_load, P_LNS, Q, R, uncalibrated and calibrated temperature), the antenna
  S11, the linear coefficients and waterfalls. The night's files are
  processed one at a time, so memory stays bounded.

Observation inputs (exact files) come from the catalog
(``catalog_inputs.resolve_observation``) as an ``inputs.json``; outputs are a
``result.json`` plus ``plots.npz`` (and ``waterfalls.npz``) in the run
directory. Raw data are only read.
"""

from __future__ import annotations

import argparse
import json
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
from edges.cal import ReflectionCoefficient, S11ModelParams, sparams as sp  # noqa: E402
from edges.frequencies import get_mask  # noqa: E402
import edges.io as io  # noqa: E402
import edges.modeling as mdl  # noqa: E402

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
#: Antenna S11 model: fit window and number of terms. Outside ~58-105 MHz the
#: EDGES-3 antenna is poorly matched (|S11| up to ~0.9) and a single model fits
#: ~300x worse, so the calibration (a, b, T_cal) is only produced in this window.
DEFAULT_ANT_S11_FSTART = 58.0
DEFAULT_ANT_S11_FSTOP = 105.0
DEFAULT_ANT_S11_NTERMS = 12

#: Waterfall frequency bins (MHz) and band.
WF_BIN_MHZ = 0.25
WF_BAND_MHZ = (40.0, 200.0)

#: Exit code of the calibration stage when the pipeline cannot calibrate the
#: day (``LookupError``: e.g. no temperature log, mixed S11 grids).
EXIT_CANNOT_CALIBRATE = 2
#: The noise-wave solution a calibration run writes (an rcal product's layout).
SOLUTION_FILE = "rcal.h5"


# ---------------------------------------------------------------------------
# Raw data readers
# ---------------------------------------------------------------------------
def read_antenna_acq(path: str, spec_timestamp: str) -> GSData:
    """Read the antenna spectrum (path from the catalog)."""
    return read_acq_to_gsdata(
        Path(path),
        telescope=KNOWN_TELESCOPES["edges3"],
        name=f"ant_{spec_timestamp}",
        lst_setter=fast_lst_setter,
    )


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
# Stage 1: calibration (non-default settings)
# ---------------------------------------------------------------------------
def run_calibration(
    day: str, params: Dict[str, Any], run_dir: Path, catalog_db: Optional[str] = None
) -> Dict[str, Any]:
    """Compute one day's receiver calibration with edges-pipeline. Writes
    ``rcal.h5`` (:func:`write_solution`) and ``result.json``
    (:func:`calibrations.calibration_json`)."""
    from edges_pipeline.stages import rcal

    import calibrations

    tic = time.time()
    run_dir.mkdir(parents=True, exist_ok=True)
    settings = None
    if catalog_db:  # the server's catalog (the products are not needed)
        from edges_pipeline.config import Settings

        d = Settings.default()
        settings = Settings(catalog_db, d.products_db, d.products_root)
    print(f"[run] calibrating {day} with {json.dumps(params)} (edges-pipeline rcal) ...")
    cal, info = rcal.calibrate_day(day, params, settings=settings)
    write_solution(run_dir / SOLUTION_FILE, cal, float(info["t_load"]), float(info["t_load_ns"]))
    result = calibrations.calibration_json(cal, info, "computed")
    result["params"] = params
    result["seconds"] = round(time.time() - tic, 1)
    for issue in result["issues"]:
        print(f"[run] issue: {issue}")
    _write_json(run_dir / "result.json", result)
    print(f"[run] calibration done in {result['seconds']} s")
    return result


def write_solution(path: Path, cal: Any, t_load: float, t_load_ns: float) -> None:
    """Save a noise-wave solution as an rcal (version 2) product holds it, so
    ``edges_pipeline.products.rcal_calibrator`` rebuilds it like a stored one.
    (``edges.cal.Calibrator.write``/``from_file`` do not round-trip in edges 8.3.)"""
    from edges_pipeline.stages.common import write_product

    rcv = np.asarray(cal.receiver_s11)
    arrays = {
        "freq_mhz": np.asarray(cal.freqs.to_value("MHz")),
        **{f"nw_{k}": np.asarray(getattr(cal, k), dtype=np.float64)
           for k in ("Tsca", "Toff", "Tunc", "Tcos", "Tsin")},
        "receiver_s11_re": np.real(rcv),
        "receiver_s11_im": np.imag(rcv),
    }
    write_product(path, {}, arrays, {"t_load": t_load, "t_load_ns": t_load_ns})


def load_calibrator(spec: Dict[str, Any]) -> Tuple[Any, float, float]:
    """``(calibrator, t_load, t_load_ns)`` of an observation's calibration.

    ``spec`` (``calibration.json`` of the observation run) names an rcal
    product: a stored one (``source`` "stored") or the solution a calibration
    run wrote (``source`` "computed"); ``path`` is that file either way.
    """
    from edges_pipeline.products import rcal_calibrator

    return rcal_calibrator(spec["path"])


# ---------------------------------------------------------------------------
# Stage 2: observation (one night)
# ---------------------------------------------------------------------------
def run_observation(
    inputs: Dict[str, Any], calibration: Dict[str, Any], run_dir: Path, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Calibrate one night of antenna spectra. Returns (and writes) ``result.json``.

    ``calibration``: which receiver calibration (see :func:`load_calibrator`).
    Q becomes an approximate temperature ``t_load_ns Q + t_load`` with the
    calibration's own Dicke convention (300/1000 K), which the calibrator
    then calibrates.
    """
    tic = time.time()
    run_dir.mkdir(parents=True, exist_ok=True)
    calobs, t_load, t_load_ns = load_calibrator(calibration)
    if calibration["source"] == "computed":  # its S11 session is known once computed
        done = json.loads((Path(calibration["path"]).parent / "result.json").read_text())
        calibration = {**calibration, "s11_session": done.get("s11_session")}
    print(f"[run] receiver calibration {calibration.get('cal_day')} ({calibration['source']},"
          f" config {str(calibration.get('config_hash'))[:12]})")
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
        tuncal = q * t_load_ns + t_load
        tcal = np.asarray(calobs.calibrate_approximate_temperature(
            tuncal, t_load=t_load, t_load_ns=t_load_ns, ant_s11=ant_model.s11, freqs=gs.freqs,
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
        "calibration": {k: calibration.get(k) for k in (
            "source", "cal_day", "s11_session", "config_hash", "id", "params")},
        "params": params,
        "ant_s11_window_mhz": [f_lo, f_hi],
        "calibration_band_mhz": [float(cal_f.min()), float(cal_f.max())],
        "t_load": t_load,
        "t_load_ns": t_load_ns,
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
        description="EDGES-3 calibration (non-default settings) / observation stage."
    )
    p.add_argument("--stage", choices=("calibration", "observation"), required=True)
    p.add_argument("--run_dir", required=True, help="Output directory of this run")
    p.add_argument("--params", default=None, help="JSON file of stage parameters")
    p.add_argument("--day", default=None, help="Calibration: day YYYY_DDD")
    p.add_argument("--catalog_db", default=None,
                   help="Calibration: catalog database (default: the pipeline's)")
    p.add_argument("--inputs", default=None,
                   help="Observation: inputs.json (catalog_inputs.resolve_observation)")
    p.add_argument("--calibration", default=None,
                   help="Observation: JSON file naming the receiver calibration")
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    params = json.loads(Path(args.params).read_text()) if args.params else {}
    if args.stage == "calibration":
        if not args.day:
            p.error("--day is required for the calibration stage")
        try:
            run_calibration(args.day, params, run_dir, args.catalog_db)
        except LookupError as e:  # the pipeline says why it cannot
            print(f"[run] {e}")
            return EXIT_CANNOT_CALIBRATE
        return 0
    if not (args.inputs and args.calibration):
        p.error("--inputs and --calibration are required for the observation stage")
    inputs = json.loads(Path(args.inputs).read_text())
    calibration = json.loads(Path(args.calibration).read_text())
    params = {**DEFAULT_PARAMS, **params}
    run_observation(inputs, calibration, run_dir, params)
    return 0


#: Observation parameters (runs_api.PARAMS checks the same names and ranges).
DEFAULT_PARAMS: Dict[str, Any] = {
    "ant_s11_fstart": DEFAULT_ANT_S11_FSTART,
    "ant_s11_fstop": DEFAULT_ANT_S11_FSTOP,
    "ant_s11_nterms": DEFAULT_ANT_S11_NTERMS,
}


if __name__ == "__main__":
    raise SystemExit(main())
