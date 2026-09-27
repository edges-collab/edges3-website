"""Tests of the catalog-based calibration/observation inputs (see conftest)."""

from __future__ import annotations

import json
import os

import pytest
from conftest import CAL_DATE, S11_GOOD, S11_LABELS, S11_OLD, T_A, T_B, T_C, UTC

import catalog_inputs
import products_api

NIGHT = "2025-04-10"


@pytest.fixture
def cat(settings):
    products_api.configure(settings)
    with catalog_inputs.open_catalog() as c:
        yield c
    products_api.configure(None)


def test_calibration_options(cat):
    d = catalog_inputs.calibration_options(cat)
    assert d["calibration"] == [CAL_DATE]  # all four loads start that day
    assert d["s11"] == [S11_OLD, S11_GOOD]


def test_resolve_calibration(cat):
    inp = catalog_inputs.resolve_calibration(cat)  # Latest / Latest
    json.dumps(inp, allow_nan=False)
    assert inp["dates"] == {"cal": CAL_DATE, "s11": S11_GOOD}  # the catalog's pick
    f = inp["files"]
    assert f["amb"].endswith(f"mro/amb/2025/{CAL_DATE}_03_00_00_amb.acq")
    assert f["short"].endswith(f"{CAL_DATE}_06_00_00_short.acq")
    assert set(f["s11"]) == set(S11_LABELS)
    t = inp["temperatures"]
    # ambient: the .tmp snapshot (27 C), not the log (which has no reading then)
    assert t["ambient"]["source"] == "snapshot"
    assert t["ambient"]["temperature_k"] == pytest.approx(300.15)
    # hot load: the log reading of probe 102 nearest the hot spectrum
    assert t["hot"]["source"] == "templog" and t["hot"]["probe"] == 102
    assert t["hot"]["temperature_c"] == pytest.approx(111.0)
    assert inp["issues"] == []
    assert all(v["live"] and v["catalog_sha256"] for v in inp["file_versions"].values())


def test_resolve_calibration_issues(cat, monkeypatch):
    inp = catalog_inputs.resolve_calibration(cat, CAL_DATE, S11_OLD)
    assert any(f"S11 session {S11_OLD} chosen; the catalog recommends {S11_GOOD}" in i
               for i in inp["issues"])
    with pytest.raises(catalog_inputs.InputError):
        catalog_inputs.resolve_calibration(cat, "2025_001")
    with pytest.raises(catalog_inputs.InputError):
        catalog_inputs.resolve_calibration(cat, CAL_DATE, "2020_001_00")
    # "Auto" S11 needs a usable recommendation
    monkeypatch.setattr(catalog_inputs, "recommended_s11", lambda cat, day: None)
    with pytest.raises(catalog_inputs.InputError, match="choose one explicitly"):
        catalog_inputs.resolve_calibration(cat)


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
    assert a["obs_ambient"]["temperature_c"] == pytest.approx(25.0)
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


def test_file_versions_see_changes(cat):
    inp = catalog_inputs.resolve_calibration(cat)
    path = inp["files"]["amb"]
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # (synthetic file)
    try:
        assert catalog_inputs.resolve_calibration(cat)["file_versions"] != inp["file_versions"]
    finally:
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_align_s11_grids_uses_given_files(cat, tmp_path):
    rsd = pytest.importorskip("run_single_day")
    inp = catalog_inputs.resolve_calibration(cat)
    shadow, warn = rsd.align_s11_grids(list(inp["files"]["s11"].values()), S11_GOOD, tmp_path)
    assert warn == []
    assert sorted(p.name for p in shadow.iterdir()) == sorted(
        f"{S11_GOOD}_{label}.s1p" for label in S11_LABELS
    )  # only this session's files; nothing written next to the raw data
