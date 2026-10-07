"""Synthetic catalog + pipeline products for the /api tests.

Builds a tiny field mirror in a temporary directory (never the real data),
ingests it with ``edges-catalog`` and runs the ``edges-pipeline`` QL and L1
stages on it, as ``edges-pipeline/tests/test_l1.py`` does.

The night of 2025-04-10 at the MRO (18:00-06:00 AWST = 10:00-22:00 UTC) has:

- ``A`` 10:30 UTC, 40 cycles, one cycle at ADC full scale;
- ``B`` 12:00 UTC, 40 cycles (a ~1 h gap after ``A``), cycle 7 an antenna
  dropout (p0 below p1, so Q < 0);
- ``C`` 13:00 UTC, 10 cycles (a short file), with a narrowband RFI line
  (:data:`RFI_CHANNEL`);
- ``bad`` 14:00 UTC, catalogued, then its first entry garbled in place
  (read_acq cannot decode it: no products);
- ``day`` 2025-04-11 05:00 UTC (13:00 AWST), 5 cycles: daytime data after
  the night, so ``Products.latest_night`` points at the (empty) next night;
- a temperature log every 5 min over 10:00-11:00 and 12:30-13:30 UTC.

A calibration day, 2025-04-12 (``2025_102``):

- ``amb``/``hot``/``open``/``short`` spectra starting 03:00/04:00/05:00/06:00 UTC;
- full averaged S11 sessions ``2025_102_02`` (the one to use) and
  ``2025_101_05`` (the day before);
- an ambient-load ``.tmp`` snapshot for 03:00 (probe 100 = 27 C);
- a temperature log every 5 min over 03:30-06:30 UTC (hot load 111 C, probe
  100 = 25 C), so the ambient spectrum has only its snapshot.

Its receiver calibration is faked in the products database (as
``edges-pipeline/tests/test_calibration.py`` does), with a trivial solution
``T = 1000 Q + 300`` (C1 = 1, C2 = 0, no noise waves, a matched receiver):
in the default configuration (rcal version 2), and in another one
(:data:`ALT_PARAMS`, standing in for the old Alan-mode products).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

pytest.importorskip("edges_pipeline")
pytest.importorskip("edges_catalog")

NFREQ = 1024
UTC = timezone.utc
NIGHT = "2025-04-10"
T_A = datetime(2025, 4, 10, 10, 30, 0, tzinfo=UTC)
T_B = datetime(2025, 4, 10, 12, 0, 0, tzinfo=UTC)
T_C = datetime(2025, 4, 10, 13, 0, 0, tzinfo=UTC)
T_BAD = datetime(2025, 4, 10, 14, 0, 0, tzinfo=UTC)
T_DAY = datetime(2025, 4, 11, 5, 0, 0, tzinfo=UTC)
CAL_DAY = datetime(2025, 4, 12, tzinfo=UTC)
CAL_DATE = "2025_102"
S11_GOOD, S11_OLD = "2025_102_02", "2025_101_05"
S11_LABELS = (
    "amb", "hot", "open", "short", "L", "O", "S", "lna", "lna_L", "lna_O", "lna_S", "ant",
)
CYCLE_S = 23
#: Settings of the second stored rcal configuration.
ALT_PARAMS = {"fit": {"cterms": 9}}
#: A narrowband RFI line in file C (119.92 MHz): its QL bin has 3 channels,
#: so the median hides it and the mean and max keep it.
RFI_CHANNEL = 614


def _age(path: Path, hours: float = 5) -> None:
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def _acq_name(t: datetime) -> str:
    return f"{t.year}_{t.timetuple().tm_yday:03d}_{t:%H_%M_%S}_ant.acq"


def write_acq(
    root: Path, start: datetime, ncycles: int, seed: int = 0, clip_cycle=None,
    dropout_cycle=None, load: str = "ant", rfi_channel=None,
) -> Path:
    from read_acq import encode

    rng = np.random.default_rng(seed)
    f = np.linspace(0, 200, NFREQ, endpoint=False)
    p1 = np.tile((1 + f / 200) * 1e-9, (ncycles, 1))
    p2 = p1 * 2
    p0 = p1 * (1.5 + 0.01 * rng.standard_normal((ncycles, NFREQ)))
    if rfi_channel is not None:  # a narrowband line: Q = 19 in one channel
        p0[:, rfi_channel] = p1[:, rfi_channel] * 20
    if dropout_cycle is not None:
        p0[dropout_cycle] = p1[dropout_cycle] * 0.5
    t = [start + timedelta(seconds=CYCLE_S * i) for i in range(ncycles)]
    times = np.array([[f"{x.year}:{x.timetuple().tm_yday:03d}:{x:%H:%M:%S}"] * 3 for x in t])
    adcmax = np.full((ncycles, 3), 0.25)
    if clip_cycle is not None:
        adcmax[clip_cycle, 0] = 0.49994
    meta = {
        "fastspec_version": "v1.1.0", "temperature": 0, "nblk": 1, "nfreq": NFREQ,
        "freq_min": 0.0, "freq_max": 200.0, "freq_res": 200.0 / NFREQ,
    }
    anc = {
        "times": times,
        "adcmax": adcmax,
        "adcmin": np.full((ncycles, 3), -0.25),
        "data_drops": np.zeros((ncycles, 3), dtype=int),
    }
    path = root / f"mro/{load}" / str(start.year) / _acq_name(start).replace("_ant.", f"_{load}.")
    path.parent.mkdir(parents=True, exist_ok=True)
    encode(path, [p0, p1, p2], meta, anc)
    _age(path)
    return path


def garble_first_entry(path: Path) -> None:
    """Garble the time of the first data line, in place, so read_acq cannot read it
    (as edges-pipeline tests/test_l1.py does: read_acq >= 1.3 reads the complete
    cycles of a NUL-padded file). Size, mtime and inode are kept."""
    src = bytearray(path.read_bytes())
    dl = src.index(b"\n", src.index(b"# swpos 0")) + 1  # the first data line
    src[dl : dl + 4] = b"XXXX"
    st = path.stat()
    path.write_bytes(src)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def templog_block(t: datetime, hot: float = 111.0) -> str:
    stamp = f"{t.year}_{t.timetuple().tm_yday:03d}_{t.hour:02d}"
    date = t.strftime("%a %b %d %H:%M:%S UTC %Y")
    return (
        f"{stamp}\n{date}\n0 +2.500000e+01\n100 +2.5e+01\n101 +2.4e+01\n"
        f"102 {hot:+.6e}\n103 +2.6e+01\n106 +0.0e+00\n150 +13.78\n152 +0.661\n"
    )


def rcal_arrays(offset: float = 0.0, alan_mode: bool = False) -> dict:
    """A trivial rcal v2 solution, T = 1000 Q + 300, with all four loads.

    ``alan_mode``: laid out as the stored rcal version 1 products are (no load
    S11s; the hot-load loss as a (frequency, loss) table).
    """
    f = np.arange(40.0, 190.01, 0.5)
    zero = np.zeros_like(f)
    out = {
        "freq_mhz": f,
        "nw_Tsca": np.full_like(f, 1000.0),
        "nw_Toff": np.full_like(f, 300.0),
        "nw_Tunc": zero, "nw_Tcos": zero, "nw_Tsin": zero,
        "receiver_s11_re": zero, "receiver_s11_im": zero,
        "load_freq_mhz": f,
        "hot_load_loss": np.full_like(f, 0.99),
    }
    for i, (name, t) in enumerate((("ambient", 300.0), ("hot_load", 385.0),
                                   ("open", 300.0), ("short", 300.0))):
        out[f"calibrated_{name}"] = np.full_like(f, t + 0.1 * i + offset)
        out[f"known_{name}"] = np.full_like(f, t)
        if not alan_mode:
            out[f"s11_{name}_real"] = np.full_like(f, 0.1 * i)
            out[f"s11_{name}_imag"] = np.full_like(f, -0.05)
    if alan_mode:
        out["hot_load_loss"] = np.stack([f, np.full_like(f, 0.98)], axis=1)
    return out


def fake_rcal(settings, day: str, params=None, arrays=None) -> str:
    """Insert a finished rcal task, product and summary row; return its path.

    The first configuration registered becomes the readers' default (as after
    a complete packaged run).
    """
    from edges_pipeline.config import StageConfig
    from edges_pipeline.db import connect, utcnow
    from edges_pipeline.runner import register_config
    from edges_pipeline.stages import rcal
    from edges_pipeline.stages.common import write_product

    cfg = StageConfig("rcal", "test", params or {}, rcal.STAGE_VERSION)
    con = connect(settings.products_db)
    register_config(con, cfg)
    key = f"rcal:edges3-mro:{day}:{cfg.config_hash[:8]}"
    dest = settings.products_root / "RCAL" / f"{key.replace(':', '_')}.h5"
    metrics = {"method": "edges.cal", "t_load": 300.0, "t_load_ns": 1000.0,
               "issues": ["no full S11 session earlier that day; using 2025-04-11T05:00:00+00:00"],
               "n_readings": {"ambient": 7, "hot": 7},
               "spectra": {"amb": [f"{day}_03_00_00_amb.acq"]}}
    write_product(dest, metrics, arrays or rcal_arrays(),
                  {"t_load": 300.0, "t_load_ns": 1000.0, "config": cfg.algorithm_params})
    t_end = CAL_DAY.timestamp() + 7 * 3600
    with con:
        tid = con.execute(
            "INSERT INTO task (stage, config_hash, input_key, input_path, status,"
            " attempts, updated_utc, unit) VALUES (?,?,?,?,'done',1,?,?)",
            ("rcal", cfg.config_hash, key, str(dest), utcnow(), f"rcal:edges3-mro:{day}"),
        ).lastrowid
        con.execute(
            "INSERT INTO product (task_id, kind, path, size, sha256, created_utc)"
            " VALUES (?,?,?,?,?,?)",
            (tid, "rcal_h5", str(dest), dest.stat().st_size, key, utcnow()),
        )
        con.execute(
            "INSERT OR IGNORE INTO stage_default (stage, config_hash, set_utc) VALUES (?,?,?)",
            ("rcal", cfg.config_hash, utcnow()),
        )
        row = {"task_id": tid, "deployment": "edges3-mro", "cal_day": day,
               "t_start_unix": t_end - 4 * 3600, "t_end_unix": t_end, "s11_session": S11_GOOD,
               "t_ambient_k": 300.15, "t_hot_k": 384.15, "t_load": 300.0, "t_load_ns": 1000.0,
               "rms_ambient_k": 0.0, "rms_hot_k": 0.1, "rms_open_k": 0.2, "rms_short_k": 0.3,
               "f_low_mhz": 40.0, "f_high_mhz": 190.0}
        con.execute(
            f"INSERT INTO rcal_day ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
            tuple(row.values()),
        )
    con.close()
    return str(dest)


def build_env(tmp: Path):
    from edges_catalog.config import Config
    from edges_catalog.ingest import ingest
    from edges_pipeline.config import Settings
    from edges_pipeline.runner import run_l1, run_ql

    root = tmp / "MRO"
    write_acq(root, T_A, 40, clip_cycle=5)
    write_acq(root, T_B, 40, seed=1, dropout_cycle=7)
    write_acq(root, T_C, 10, seed=2, rfi_channel=RFI_CHANNEL)
    write_acq(root, T_DAY, 5, seed=3)
    bad = write_acq(root, T_BAD, 10, seed=4)

    tl = root / "temperature_logger"
    tl.mkdir(parents=True)
    t0 = datetime(2025, 4, 10, 10, 0, 0, tzinfo=UTC)
    blocks = [t0 + timedelta(minutes=5 * i) for i in range(13)]
    blocks += [t0 + timedelta(hours=2.5, minutes=5 * i) for i in range(13)]
    blocks += [CAL_DAY + timedelta(hours=3.5, minutes=5 * i) for i in range(37)]
    (tl / "temperature.log").write_text("".join(templog_block(t) for t in blocks))
    _age(tl / "temperature.log")

    # the calibration day
    for i, load in enumerate(("amb", "hot", "open", "short")):
        write_acq(root, CAL_DAY + timedelta(hours=3 + i), 4, seed=10 + i, load=load)
    freqs = np.linspace(40e6, 200e6, 151)
    s1p = "BEGIN\nDB\n" + "\n".join(f"{f:.6f} -1.0 10.0" for f in freqs) + "\nEND\n"
    for stem in (S11_GOOD, S11_OLD):
        for label in S11_LABELS:
            (root / f"{stem}_{label}.s1p").write_text(s1p)
            _age(root / f"{stem}_{label}.s1p")
    (root / f"{CAL_DATE}_03_amb.tmp").write_text("100 +2.7e+01\n102 +1.11e+02\n")
    _age(root / f"{CAL_DATE}_03_amb.tmp")

    out = tmp / "out"
    cfg = Config.from_dict({
        "db_path": str(out / "catalog.sqlite"),
        "deployments": [{"name": "edges3-mro", "instrument": "edges3"}],
        "roots": [{"path": str(root), "deployment": "edges3-mro", "layout": "edges3-field-mirror"}],
    })
    ingest(cfg, max_mbps=None)
    garble_first_entry(bad)
    settings = Settings(out / "catalog.sqlite", out / "products.sqlite", out / "prod")
    ql_cfg = tmp / "ql_all.toml"  # the default QL config covers only the last 30 days
    ql_cfg.write_text('stage = "ql"\nname = "all"\n[select]\nloads = ["ant"]\n')
    run_ql(settings, ql_cfg, workers=1, max_mbps=None)
    run_l1(settings, workers=1, max_mbps=None)
    fake_rcal(settings, CAL_DATE)  # the default configuration
    fake_rcal(settings, CAL_DATE, ALT_PARAMS, rcal_arrays(offset=0.5, alan_mode=True))
    return settings


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    return build_env(tmp_path_factory.mktemp("edges"))


@pytest.fixture
def client(settings):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import products_api

    products_api.configure(settings)
    app = FastAPI()
    app.include_router(products_api.router)
    yield TestClient(app)
    products_api.configure(None)
