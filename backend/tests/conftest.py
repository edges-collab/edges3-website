"""Synthetic catalog + pipeline products for the /api tests.

Builds a tiny field mirror in a temporary directory (never the real data),
ingests it with ``edges-catalog`` and runs the ``edges-pipeline`` QL and L1
stages on it, as ``edges-pipeline/tests/test_l1.py`` does.

The night of 2025-04-10 at the MRO (18:00-06:00 AWST = 10:00-22:00 UTC) has:

- ``A`` 10:30 UTC, 40 cycles, one cycle at ADC full scale;
- ``B`` 12:00 UTC, 40 cycles (a ~1 h gap after ``A``);
- ``C`` 13:00 UTC, 10 cycles (a short file);
- ``bad`` 14:00 UTC, NUL-padded (read_acq cannot decode it: no products);
- ``day`` 2025-04-11 05:00 UTC (13:00 AWST), 5 cycles: daytime data after
  the night, so ``Products.latest_night`` points at the (empty) next night;
- a temperature log every 5 min over 10:00-11:00 and 12:30-13:30 UTC.
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
CYCLE_S = 23


def _age(path: Path, hours: float = 5) -> None:
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def _acq_name(t: datetime) -> str:
    return f"{t.year}_{t.timetuple().tm_yday:03d}_{t:%H_%M_%S}_ant.acq"


def write_acq(root: Path, start: datetime, ncycles: int, seed: int = 0, clip_cycle=None) -> Path:
    from read_acq import encode

    rng = np.random.default_rng(seed)
    f = np.linspace(0, 200, NFREQ, endpoint=False)
    p1 = np.tile((1 + f / 200) * 1e-9, (ncycles, 1))
    p2 = p1 * 2
    p0 = p1 * (1.5 + 0.01 * rng.standard_normal((ncycles, NFREQ)))
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
    path = root / "mro/ant" / str(start.year) / _acq_name(start)
    path.parent.mkdir(parents=True, exist_ok=True)
    encode(path, [p0, p1, p2], meta, anc)
    _age(path)
    return path


def templog_block(t: datetime, hot: float = 111.0) -> str:
    stamp = f"{t.year}_{t.timetuple().tm_yday:03d}_{t.hour:02d}"
    date = t.strftime("%a %b %d %H:%M:%S UTC %Y")
    return (
        f"{stamp}\n{date}\n0 +2.500000e+01\n100 +2.5e+01\n101 +2.4e+01\n"
        f"102 {hot:+.6e}\n103 +2.6e+01\n106 +0.0e+00\n150 +13.78\n152 +0.661\n"
    )


def build_env(tmp: Path):
    from edges_catalog.config import Config
    from edges_catalog.ingest import ingest
    from edges_pipeline.config import Settings
    from edges_pipeline.runner import run_l1, run_ql

    root = tmp / "MRO"
    good = write_acq(root, T_A, 40, clip_cycle=5)
    write_acq(root, T_B, 40, seed=1)
    write_acq(root, T_C, 10, seed=2)
    write_acq(root, T_DAY, 5, seed=3)
    src = good.read_bytes()
    cut = src.index(b"# swpos 0", src.index(b"# swpos 2"))
    bad = root / "mro/ant/2025" / _acq_name(T_BAD)
    bad.write_bytes(src[:cut] + b"\x00" * 3000)
    _age(bad)

    tl = root / "temperature_logger"
    tl.mkdir(parents=True)
    t0 = datetime(2025, 4, 10, 10, 0, 0, tzinfo=UTC)
    blocks = [t0 + timedelta(minutes=5 * i) for i in range(13)]
    blocks += [t0 + timedelta(hours=2.5, minutes=5 * i) for i in range(13)]
    (tl / "temperature.log").write_text("".join(templog_block(t) for t in blocks))
    _age(tl / "temperature.log")

    out = tmp / "out"
    cfg = Config.from_dict({
        "db_path": str(out / "catalog.sqlite"),
        "deployments": [{"name": "edges3-mro", "instrument": "edges3"}],
        "roots": [{"path": str(root), "deployment": "edges3-mro", "layout": "edges3-field-mirror"}],
    })
    ingest(cfg, max_mbps=None)
    settings = Settings(out / "catalog.sqlite", out / "products.sqlite", out / "prod")
    ql_cfg = tmp / "ql_all.toml"  # the default QL config covers only the last 30 days
    ql_cfg.write_text('stage = "ql"\nname = "all"\n[select]\nloads = ["ant"]\n')
    run_ql(settings, ql_cfg, workers=1, max_mbps=None)
    run_l1(settings, workers=1, max_mbps=None)
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
