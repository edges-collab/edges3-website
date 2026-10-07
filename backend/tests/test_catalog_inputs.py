"""Tests of the catalog-based observation inputs (see conftest)."""

from __future__ import annotations

import json
import os

import pytest
from conftest import CAL_DAY, S11_GOOD, S11_OLD, T_A, T_B, T_C, UTC

import catalog_inputs
import products_api

NIGHT = "2025-04-10"


@pytest.fixture
def cat(settings):
    products_api.configure(settings)
    with catalog_inputs.open_catalog() as c:
        yield c
    products_api.configure(None)


def test_full_s11_sessions(cat):
    assert catalog_inputs.full_s11_sessions(cat) == [S11_OLD, S11_GOOD]


def test_probe_temperature(cat):
    t_amb = CAL_DAY.timestamp() + 3 * 3600  # the ambient spectrum
    # the .tmp snapshot (27 C), not the log (which has no reading then)
    snap = catalog_inputs.probe_temperature(
        cat, probe=100, context="amb", stamp_unix=t_amb, t_unix=t_amb, default_k=1.0)
    assert snap["source"] == "snapshot" and snap["temperature_k"] == pytest.approx(300.15)
    # the log reading of probe 102 nearest the hot spectrum
    t_hot = t_amb + 3600
    log = catalog_inputs.probe_temperature(
        cat, probe=102, context="hot", stamp_unix=t_hot, t_unix=t_hot, default_k=1.0)
    assert log["source"] == "templog" and log["temperature_c"] == pytest.approx(111.0)
    # nothing within 15 min: the fallback
    none = catalog_inputs.probe_temperature(
        cat, probe=102, context="hot", stamp_unix=t_amb - 86400, t_unix=t_amb - 86400,
        default_k=1.0)
    assert none["source"] == "default" and none["temperature_k"] == 1.0


@pytest.mark.parametrize(
    ("start", "end", "nights"),
    [
        ((2025, 4, 10, 9, 18), (2025, 4, 10, 12, 14), ["2025-04-10"]),  # 17:18-20:14 AWST
        ((2025, 4, 11, 5, 0), (2025, 4, 11, 5, 2), []),  # 13:00 AWST: no night
        ((2025, 4, 10, 21, 0), (2025, 4, 11, 0, 0), ["2025-04-10"]),  # 05:00-08:00 AWST
        ((2025, 4, 10, 9, 0), (2025, 4, 11, 11, 0), ["2025-04-10", "2025-04-11"]),
    ],
)
def test_night_dates_of(start, end, nights):
    from datetime import datetime

    t0 = datetime(*start, tzinfo=UTC).timestamp()
    t1 = datetime(*end, tzinfo=UTC).timestamp()
    assert catalog_inputs._night_dates_of(t0, t1) == nights


def test_observation_options(cat):
    d = catalog_inputs.observation_options(cat)
    assert d["nights"] == [NIGHT]  # the daytime file (T_DAY) is in no night
    assert d["antenna_s11"] == [S11_OLD, S11_GOOD]


def test_resolve_observation(cat):
    inp = catalog_inputs.resolve_observation(cat)  # Latest / Latest
    json.dumps(inp, allow_nan=False)
    assert inp["night"]["date"] == NIGHT
    assert inp["night"]["end_unix"] - inp["night"]["start_unix"] == 12 * 3600
    names = [f["name"] for f in inp["files"]["ant"]]
    for t in (T_A, T_B, T_C):
        assert t.strftime("2025_100_%H_%M_%S_ant.acq") in names
    # no session before the night: the first one after it
    assert inp["dates"]["ant_s11"] == S11_OLD == inp["recommended_ant_s11"]
    assert {"ant", "O", "S", "L"} <= set(inp["files"]["ant_s11"])
    by_name = {f["name"]: f for f in inp["files"]["ant"]}
    a = by_name[T_A.strftime("2025_100_%H_%M_%S_ant.acq")]["temperatures"]
    assert a["obs_ambient"]["source"] == "templog"
    # probe 101, the ambient load (as the pipeline's calibration), not 100
    assert a["obs_ambient"]["probe"] == 101
    assert a["obs_ambient"]["temperature_c"] == pytest.approx(24.0)
    # the 12:00 file has no log reading within 15 min: shown as missing, no issue
    # (the antenna calibration needs no probe temperature)
    b = by_name[T_B.strftime("2025_100_%H_%M_%S_ant.acq")]["temperatures"]
    assert b["obs_ambient"]["source"] == "default"
    assert inp["issues"] == []


def test_resolve_observation_errors(cat):
    with pytest.raises(catalog_inputs.InputError):
        catalog_inputs.resolve_observation(cat, "2025-04-12")
    with pytest.raises(catalog_inputs.InputError):
        catalog_inputs.resolve_observation(cat, NIGHT, "2020_001_00")
    inp = catalog_inputs.resolve_observation(cat, NIGHT, S11_GOOD)
    assert any("chosen; the nearest before the night" in i for i in inp["issues"])


def test_file_being_written_is_left_out(cat):
    import time

    inp = catalog_inputs.resolve_observation(cat)
    path = next(f["path"] for f in inp["files"]["ant"] if f["name"] == T_C.strftime("2025_100_%H_%M_%S_ant.acq"))
    st = os.stat(path)
    os.utime(path, (time.time(), time.time()))  # (synthetic file)
    try:
        again = catalog_inputs.resolve_observation(cat)
        assert path not in [f["path"] for f in again["files"]["ant"]]
        assert any("still being written" in i for i in again["issues"])
    finally:
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_bin_rows_empty_bins():
    import numpy as np

    rsd = pytest.importorskip("run_single_day")
    f = np.array([40.1, 40.2, 41.1, 41.2])  # nothing in 40.25-41.0 or 41.25-200
    sel, idx, centres = rsd._bin_index(f)
    x = np.array([[1.0, 3.0, 5.0, np.nan]])
    out = rsd._bin_rows(x, sel, idx, len(centres))
    assert out[0, 0] == 2.0 and out[0, 4] == 5.0
    assert np.isnan(out[0, 1:4]).all() and np.isnan(out[0, 5:]).all()


def test_file_versions_see_changes(cat):
    inp = catalog_inputs.resolve_observation(cat)
    path = inp["files"]["ant"][0]["path"]
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - 10**9))  # (synthetic file)
    try:
        assert catalog_inputs.resolve_observation(cat)["file_versions"] != inp["file_versions"]
    finally:
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
