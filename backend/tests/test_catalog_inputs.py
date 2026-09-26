"""Tests of the catalog-based Select/Calibration inputs (see conftest)."""

from __future__ import annotations

import importlib
import json
import sys

import pytest
from conftest import CAL_DATE, S11_GOOD, S11_LABELS, S11_OLD, T_A, T_B, T_DAY

import catalog_inputs
import products_api

RAW_A = T_A.strftime("2025_100_%H_%M_%S")
RAW_DAY = T_DAY.strftime("2025_101_%H_%M_%S")


@pytest.fixture
def cat(settings):
    products_api.configure(settings)
    with catalog_inputs.open_catalog() as c:
        yield c
    products_api.configure(None)


@pytest.fixture
def api(settings, tmp_path, monkeypatch):
    """The main app, with outputs in a temporary directory and no pipeline runs."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("EDGES_OUTPUT_ROOT", str(tmp_path / "outputs"))
    monkeypatch.setenv("EDGES_RAW_DATA_ROOT", str(tmp_path / "no-raw-data"))
    for m in ("config", "catalog_inputs", "backend_api"):
        sys.modules.pop(m, None)
    backend_api = importlib.import_module("backend_api")
    products_api.configure(settings)
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        run_dir = cmd[cmd.index("--run_dir") + 1]
        (tmp_path / "outputs" / "manifest.json").write_text(
            json.dumps({"plots": [], "latest_run": run_dir.rsplit("/", 1)[-1]})
        )

    monkeypatch.setattr(backend_api, "_execute_subprocess", fake_run)
    yield TestClient(backend_api.app), backend_api, calls
    products_api.configure(None)
    for m in ("config", "catalog_inputs", "backend_api"):
        sys.modules.pop(m, None)
    importlib.import_module("catalog_inputs")


def test_available_dates(cat):
    d = catalog_inputs.available_dates(cat)
    assert d["calibration"] == [CAL_DATE]  # all four loads start that day
    assert d["s11"] == [S11_OLD, S11_GOOD]
    assert RAW_A in d["raw"] and d["raw"] == sorted(d["raw"])
    assert d["raw"][-1] == RAW_DAY


def test_resolve_dates(cat):
    avail = catalog_inputs.available_dates(cat)
    r = catalog_inputs.resolve_dates(cat, {"cal": "Latest", "s11": "Latest", "raw": "Latest"}, avail)
    # "Latest" S11 = the session the catalog recommends for the calibration
    # day; "Latest" raw skips short files (T_DAY has 5 cycles, all have < 100)
    assert r["cal"] == CAL_DATE and r["s11"] == S11_GOOD
    assert r["raw"] == RAW_DAY  # no file is long enough: fall back to the last
    catalog_inputs.MIN_LATEST_RAW_CYCLES = 30
    try:
        r = catalog_inputs.resolve_dates(cat, {}, avail)
        assert r["raw"] == T_B.strftime("2025_100_%H_%M_%S")  # latest with >= 30 cycles
    finally:
        catalog_inputs.MIN_LATEST_RAW_CYCLES = 100
    with pytest.raises(catalog_inputs.InputError):
        catalog_inputs.resolve_dates(cat, {"cal": "2025_001"}, avail)
    with pytest.raises(catalog_inputs.InputError):
        catalog_inputs.resolve_dates(cat, {"s11": "2020_001_00"}, avail)


def test_resolve_inputs(cat):
    inp = catalog_inputs.resolve_inputs(cat, {"cal": CAL_DATE, "s11": S11_GOOD, "raw": RAW_A})
    json.dumps(inp, allow_nan=False)
    f = inp["files"]
    assert f["amb"].endswith(f"mro/amb/2025/{CAL_DATE}_03_00_00_amb.acq")
    assert f["short"].endswith(f"{CAL_DATE}_06_00_00_short.acq")
    assert f["ant"].endswith(f"{RAW_A}_ant.acq")
    assert set(f["s11"]) == set(S11_LABELS)
    assert f["s11"]["O"].endswith(f"{S11_GOOD}_O.s1p")
    assert inp["recommended_s11"] == S11_GOOD

    t = inp["temperatures"]
    # ambient: the .tmp snapshot (27 C), not the log (which has no reading then)
    assert t["ambient"]["source"] == "snapshot"
    assert t["ambient"]["temperature_k"] == pytest.approx(300.15)
    # hot load: the log reading of probe 102 nearest the hot spectrum
    assert t["hot"]["source"] == "templog" and t["hot"]["probe"] == 102
    assert t["hot"]["temperature_c"] == pytest.approx(111.0)
    # LNA and the "actual" ambient at the observation: probe 100 in the log
    assert t["lna"]["source"] == "templog" and t["lna"]["temperature_c"] == pytest.approx(25.0)
    assert t["obs_ambient"]["temperature_c"] == pytest.approx(25.0)
    assert not any("temperature" in i for i in inp["issues"])


def test_resolve_inputs_issues(cat):
    inp = catalog_inputs.resolve_inputs(cat, {"cal": CAL_DATE, "s11": S11_OLD, "raw": RAW_DAY})
    issues = " | ".join(inp["issues"])
    assert f"S11 session {S11_OLD} chosen; the catalog recommends {S11_GOOD}" in issues
    # no log reading within 15 min of the daytime antenna file: fallback + issue
    assert inp["temperatures"]["lna"]["source"] == "default"
    assert "no lna temperature (probe 100)" in issues
    with pytest.raises(catalog_inputs.InputError):
        catalog_inputs.resolve_inputs(cat, {"cal": CAL_DATE, "s11": S11_GOOD, "raw": "2020_001_00_00_00"})


def test_endpoints(api):
    client, _, _ = api
    d = client.get("/available_dates").json()
    assert d["calibration"] == [CAL_DATE]
    r = client.get("/api/calibration/inputs", params={"raw": RAW_A})
    assert r.status_code == 200
    assert r.json()["dates"] == {"cal": CAL_DATE, "s11": S11_GOOD, "raw": RAW_A}
    assert client.get("/api/calibration/inputs", params={"cal": "1999_001"}).status_code == 400


def test_run_pipeline_passes_catalog_inputs(api):
    client, backend_api, calls = api
    r = client.post("/run_pipeline", json={"dates": {"cal": CAL_DATE, "s11": S11_OLD, "raw": RAW_A}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert any("recommends" in i for i in body["issues"])
    (cmd,) = calls
    assert "--inputs" in cmd
    for gone in ("--rawdata_root", "--temperature_log", "--cal_date"):
        assert gone not in cmd
    inputs = json.loads(open(cmd[cmd.index("--inputs") + 1]).read())
    assert inputs["dates"] == {"cal": CAL_DATE, "s11": S11_OLD, "raw": RAW_A}
    latest = client.get("/latest_run").json()
    assert latest["input_issues"] == inputs["issues"]
    # same dates, parameters and inputs: reused, not recomputed
    client.post("/run_pipeline", json={"dates": {"cal": CAL_DATE, "s11": S11_OLD, "raw": RAW_A}})
    assert len(calls) == 1


def test_align_s11_grids_uses_given_files(cat, tmp_path):
    rsd = pytest.importorskip("run_single_day")
    inp = catalog_inputs.resolve_inputs(cat, {"cal": CAL_DATE, "s11": S11_GOOD, "raw": RAW_A})
    shadow, warn = rsd.align_s11_grids(list(inp["files"]["s11"].values()), S11_GOOD, tmp_path)
    assert warn == []
    assert sorted(p.name for p in shadow.iterdir()) == sorted(
        f"{S11_GOOD}_{label}.s1p" for label in S11_LABELS
    )  # only this session's files; nothing written next to the raw data
