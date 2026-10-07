"""A stand-in for ``edges_pipeline.stages.rcal.calibrate_day``, which needs real data.

It returns the trivial solution of the stored fake products (``conftest``),
with the calibrated loads offset by ``cterms / 100`` K so settings show, in
exactly the ``(calibrator, info)`` shape of the real one. The environment
variable ``FAKE_CANNOT`` makes it raise ``LookupError`` with that reason, as
the real one does for a day it cannot calibrate.
"""

from __future__ import annotations

import os

from conftest import S11_GOOD, rcal_arrays


def calibrate_day(day, params=None, deployment="edges3-mro", settings=None, s11_session=None):
    from astropy import units as un
    from edges.cal import Calibrator
    from edges_pipeline.products import _cal_day_key, rcal_info
    from edges_pipeline.stages import rcal

    key = _cal_day_key(day)
    if os.environ.get("FAKE_KEYERROR"):
        raise KeyError("t_load")  # a bug, not a reason
    if os.environ.get("FAKE_CANNOT"):
        raise LookupError(f"cannot calibrate {key}: {os.environ['FAKE_CANNOT']}")
    expected = os.environ.get("FAKE_CATALOG_DB")
    if expected and str(getattr(settings, "catalog_db", "")) != expected:
        raise AssertionError(f"not the server's catalog: {settings}")
    cfg = rcal.effective_config(params)
    arrays = rcal_arrays(offset=cfg["fit"]["cterms"] / 100)
    cal = Calibrator(
        freqs=arrays["freq_mhz"] * un.MHz,
        **{k: arrays[f"nw_{k}"] for k in ("Tsca", "Toff", "Tunc", "Tcos", "Tsin")},
        receiver_s11=arrays["receiver_s11_re"] + 1j * arrays["receiver_s11_im"],
    )
    metrics = {"method": "edges.cal", "t_load": 300.0, "t_load_ns": 1000.0,
               "issues": [], "n_readings": {"ambient": 7, "hot": 7}, "spectra": {}}
    row = {"deployment": deployment, "cal_day": key, "s11_session": s11_session or S11_GOOD,
           "t_ambient_k": 300.15, "t_hot_k": 384.15, "rms_ambient_k": 0.0, "rms_hot_k": 0.1,
           "rms_open_k": 0.2, "rms_short_k": 0.3, "path": None,
           "config_hash": rcal.config_hash(params)}
    return cal, rcal_info(row, cal, arrays, metrics, cfg)
