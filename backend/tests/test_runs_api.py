"""Tests of the calibration endpoints and the job runner.

Stored calibrations are the fake products of ``conftest``. Computations run
the real calibration stage of ``run_single_day.py`` with
``rcal.calibrate_day`` mocked (``fake_pipeline``); nights run a fake stage.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
from conftest import ALT_PARAMS, CAL_DATE, S11_GOOD

import calibrations
import config
import products_api
import runs_api

TESTS = Path(__file__).resolve().parent

FAKE = """
import json, os, pathlib, sys
a = sys.argv
d = pathlib.Path(a[a.index("--run_dir") + 1])
stage = a[a.index("--stage") + 1]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(stage + "\\n")
if os.environ.get("FAKE_FAIL") == stage:
    print("boom"); sys.exit(3)
if stage == "calibration":  # the real stage, with rcal.calibrate_day mocked
    sys.path[:0] = [os.environ["FAKE_TESTS"], os.environ["FAKE_BACKEND"]]
    from edges_pipeline.stages import rcal
    import fake_pipeline
    rcal.calibrate_day = fake_pipeline.calibrate_day
    import run_single_day
    sys.exit(run_single_day.main(a[1:]))
spec = json.loads(pathlib.Path(a[a.index("--calibration") + 1]).read_text())
assert pathlib.Path(spec["path"]).exists(), spec
params = json.loads(pathlib.Path(a[a.index("--params") + 1]).read_text())
(d / "plots.npz").write_bytes(b"x")
(d / "result.json").write_text(json.dumps({"kind": stage, "params": params, "calibration": spec}))
"""

CUSTOM = {"fit": {"cterms": 7}}


@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    script = tmp_path / "fake_stage.py"
    script.write_text(FAKE)
    monkeypatch.setattr(config, "OUTPUT_ROOT", tmp_path / "outputs")
    monkeypatch.setattr(config, "RUN_SCRIPT", script)
    monkeypatch.setattr(config, "PYTHON", sys.executable)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.txt"))
    monkeypatch.setenv("FAKE_TESTS", str(TESTS))
    monkeypatch.setenv("FAKE_BACKEND", str(TESTS.parent))
    monkeypatch.setenv("FAKE_CATALOG_DB", str(settings.catalog_db))
    (tmp_path / "calls.txt").write_text("")
    products_api.configure(settings)
    calibrations.clear_cache()
    app = FastAPI()
    app.include_router(runs_api.router)
    yield TestClient(app), tmp_path / "calls.txt"
    products_api.configure(None)


def _wait(client, kind, rid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/{kind}s/{rid}").json()
        if d["status"]["state"] in ("done", "failed"):
            return d
        time.sleep(0.1)
    raise AssertionError("run did not finish")


# ---------------------------------------------------------------------------
# Stored calibrations
# ---------------------------------------------------------------------------
def test_calibration_list(client):
    from edges_pipeline.stages import rcal

    c, _ = client
    d = c.get("/api/calibrations").json()
    assert [r["cal_day"] for r in d["stored"]] == [CAL_DATE]
    assert d["stored"][0]["s11_session"] == S11_GOOD
    assert d["missing"] == []  # the only catalog calibration day is stored
    assert d["default_hash"] == rcal.config_hash()
    assert [o["config_hash"] for o in d["other_configs"]] == [rcal.config_hash(ALT_PARAMS)]
    assert d["defaults"]["fit"]["cterms"] == 6
    keys = {(f["section"], f["key"]) for f in d["fields"]}
    assert ("fit", "cterms") in keys and ("fit", "fstart") not in keys


def test_stored_calibration(client):
    c, calls = client
    d = c.get(f"/api/calibrations/stored/{CAL_DATE}").json()
    assert d["source"] == "stored" and d["is_default"] and d["cal_day"] == CAL_DATE
    assert d["product"].endswith(".h5") and "/" not in d["product"]
    n = len(d["freq_mhz"])
    assert n == 301 and all(len(d["nw"][k]) == n for k in ("Tsca", "Toff", "Tunc", "Tcos", "Tsin"))
    assert set(d["loads"]) == {"ambient", "hot_load", "open", "short"}
    assert d["loads"]["open"]["calibrated"][0] == pytest.approx(300.2)
    assert d["loads"]["open"]["s11_re"][0] == pytest.approx(0.2)
    assert d["rms_k"]["hot_load"] == pytest.approx(0.1)
    assert d["hot_load_loss"][0] == pytest.approx(0.99)
    assert d["issues"] and d["config"]["fit"]["cterms"] == 6
    assert (d["t_load"], d["t_load_ns"]) == (300.0, 1000.0)
    assert c.get("/api/calibrations/stored/2025:102").json() == d
    assert calls.read_text() == ""  # nothing computed


def test_stored_calibration_of_another_config(client):
    from edges_pipeline.stages import rcal

    c, _ = client
    h = rcal.config_hash(ALT_PARAMS)
    d = c.get(f"/api/calibrations/stored/{CAL_DATE}", params={"config_hash": h}).json()
    assert not d["is_default"] and d["config_hash"] == h
    assert d["loads"]["ambient"]["calibrated"][0] == pytest.approx(300.5)
    # laid out as the Alan-mode (rcal v1) products: no load S11s, the loss as a table
    assert d["loads"]["open"]["s11_re"] is None
    assert len(d["hot_load_loss"]) == len(d["freq_mhz"]) and d["hot_load_loss"][0] == pytest.approx(0.98)


@pytest.mark.parametrize(
    ("day", "code", "text"),
    [
        ("2026_257", 404, "mixes VNA"),
        ("2022_316", 404, "temperature-log"),
        ("2025_050", 404, "2024–2025"),
        ("April", 400, "not a calibration day"),
    ],
)
def test_stored_calibration_missing(client, day, code, text):
    c, _ = client
    r = c.get(f"/api/calibrations/stored/{day}")
    assert r.status_code == code and text in r.json()["detail"]


def test_resolve(client):
    c, _ = client
    r = c.post("/api/calibrations/resolve", json={}).json()
    assert r["day"] == CAL_DATE and r["source"] == "stored" and r["is_default"]
    assert r["stored"]["cal_day"] == CAL_DATE and r["id"] is None
    # equal settings, written differently: still the stored default
    same = c.post("/api/calibrations/resolve", json={"params": {"fit": {"cterms": 6.0}}}).json()
    assert same["source"] == "stored" and same["params"] == {}
    # other settings: a computation, not started
    other = c.post("/api/calibrations/resolve", json={"params": CUSTOM}).json()
    assert other["source"] == "computed" and other["status"] is None
    assert other["config_hash"] != r["config_hash"] and other["params"] == CUSTOM
    # a day without a stored calibration: said, not an error
    gap = c.post("/api/calibrations/resolve", json={"day": "2026_257"}).json()
    assert gap["stored"] is None and "VNA" in gap["unavailable"]


@pytest.mark.parametrize(
    "params",
    [
        {"fit": {"cterm": 6}},
        {"fits": {"cterms": 6}},
        {"fit": {"cterms": 0}},
        {"fit": {"cterms": 6.5}},
        {"fit": {"cterms": "six"}},
        {"fit": {"cterms": True}},
        {"fit": {"wfstart": 150, "wfstop": 100}},
        {"fit": {"Lh": 0}},
        {"dicke": {"t_load": 290}},
    ],
)
def test_bad_settings(client, params):
    c, _ = client
    for path in ("/api/calibrations/resolve", "/api/calibrations"):
        assert c.post(path, json={"params": params}).status_code == 400


# ---------------------------------------------------------------------------
# Computed calibrations
# ---------------------------------------------------------------------------
def test_compute_and_reuse(client):
    import run_single_day

    c, calls = client
    # the default settings are stored: nothing to compute
    assert c.post("/api/calibrations", json={}).status_code == 400
    r = c.post("/api/calibrations/resolve", json={"params": CUSTOM}).json()
    started = c.post("/api/calibrations", json={"params": CUSTOM}).json()
    assert started["id"] == r["id"]
    done = _wait(c, "calibration", r["id"])
    assert done["status"]["state"] == "done", done
    res = done["result"]
    assert res["source"] == "computed" and res["params"] == CUSTOM
    assert res["product"] is None and res["config_hash"] == r["config_hash"]
    assert res["loads"]["ambient"]["calibrated"][0] == pytest.approx(300.07)
    assert res["config"]["fit"]["cterms"] == 7
    # the solution is rebuilt as a stored one is
    path = config.OUTPUT_ROOT / "calibration" / r["id"] / "rcal.h5"
    cal, t_load, t_load_ns = run_single_day.load_calibrator({"source": "computed", "path": str(path)})
    q = np.full(301, 0.5)
    assert np.allclose(np.asarray(cal.calibrate_q(q, np.zeros(301, complex))), 800.0)  # 1000 Q + 300
    assert (t_load, t_load_ns) == (300.0, 1000.0)
    # the same settings again (written differently): found, not recomputed
    again = c.post("/api/calibrations", json={"day": CAL_DATE, "params": {"fit": {"cterms": 7.0}}}).json()
    assert again["id"] == r["id"] and again["status"]["state"] == "done"
    assert calls.read_text().split() == ["calibration"]
    zf = zipfile.ZipFile(io.BytesIO(c.get(f"/api/runs/calibration/{r['id']}/download").content))
    assert {"result.json", "rcal.h5", "params.json"} <= set(zf.namelist())


def test_stored_and_computed_load_alike(client, settings):
    import run_single_day

    c, _ = client
    stored = c.post("/api/calibrations/resolve", json={}).json()["stored"]
    cal, *_ = run_single_day.load_calibrator({"source": "stored", "path": stored["path"]})
    assert np.allclose(np.asarray(cal.calibrate_q(np.full(301, 0.5), np.zeros(301, complex))), 800.0)


def test_cannot_calibrate(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("FAKE_CANNOT", "no_temperature")
    rid = c.post("/api/calibrations", json={"params": {"fit": {"cterms": 8}}}).json()["id"]
    d = _wait(c, "calibration", rid)
    assert d["status"]["state"] == "failed"
    assert d["status"]["error"] == f"cannot calibrate {CAL_DATE}: no_temperature"


# ---------------------------------------------------------------------------
# Nights
# ---------------------------------------------------------------------------
def test_observation_with_the_stored_calibration(client):
    c, calls = client
    r = c.post("/api/observations/resolve", json={"night": "Latest"}).json()
    assert r["calibration"]["source"] == "stored" and r["inputs"]["night"]["date"] == "2025-04-10"
    d = _wait(c, "observation", c.post("/api/observations", json={}).json()["id"])
    assert d["status"]["state"] == "done", d
    spec = d["result"]["calibration"]
    assert spec["source"] == "stored" and spec["cal_day"] == CAL_DATE
    assert spec["path"] == r["calibration"]["stored"]["path"]
    assert calls.read_text().split() == ["observation"]
    assert d["result"]["params"]["ant_s11_fstart"] == 58.0


def test_observation_runs_its_calibration_first(client):
    c, calls = client
    body = {"night": "Latest", "calibration": {"day": CAL_DATE, "params": CUSTOM}}
    r = c.post("/api/observations/resolve", json=body).json()
    assert r["calibration"]["source"] == "computed" and r["calibration"]["status"] is None
    d = _wait(c, "observation", c.post("/api/observations", json=body).json()["id"])
    assert d["status"]["state"] == "done", d
    assert d["request"]["calibration_id"] == r["calibration"]["id"]
    assert d["result"]["calibration"]["path"].endswith(f"{r['calibration']['id']}/rcal.h5")
    assert calls.read_text().split() == ["calibration", "observation"]
    # the stored and the computed calibration make different nights
    assert r["id"] != c.post("/api/observations/resolve", json={}).json()["id"]


def test_observation_without_a_stored_calibration(client):
    c, _ = client
    r = c.post("/api/observations", json={"calibration": {"day": "2026_257"}})
    assert r.status_code == 400 and "VNA" in r.json()["detail"]


def test_failed_run_reports_and_retries(client, monkeypatch):
    c, calls = client
    monkeypatch.setenv("FAKE_FAIL", "calibration")
    params = {"fit": {"cterms": 9, "wterms": 4}}
    rid = c.post("/api/calibrations", json={"params": params}).json()["id"]
    d = _wait(c, "calibration", rid)
    assert d["status"]["state"] == "failed" and "exit 3" in d["status"]["error"]
    assert "boom" in d["log_tail"]
    # an observation on a failed calibration fails too
    obs = c.post("/api/observations", json={"calibration": {"params": params}}).json()
    assert _wait(c, "observation", obs["id"])["status"]["state"] == "failed"
    monkeypatch.delenv("FAKE_FAIL")
    rid2 = c.post("/api/calibrations", json={"params": params}).json()["id"]
    assert rid2 == rid and _wait(c, "calibration", rid)["status"]["state"] == "done"


@pytest.mark.parametrize(
    "body",
    [
        {"night": "1999-01-01"},
        {"params": {"ant_s11_fstart": 120, "ant_s11_fstop": 60}},
        {"calibration": {"params": {"fit": {"nonsense": 1}}}},
    ],
)
def test_bad_observation_requests(client, body):
    c, _ = client
    assert c.post("/api/observations", json=body).status_code == 400


def test_unknown_runs(client):
    c, _ = client
    assert c.get("/api/calibrations/0123456789abcdef").status_code == 404
    assert c.get("/api/calibrations/../../etc").status_code == 404
    assert c.get("/api/runs/nope/0123456789abcdef/download").status_code == 404
    assert c.get("/api/calibrations/stored/2025_102?config_hash=xyz").status_code == 400


def test_eviction(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(runs_api, "MAX_RUNS_PER_KIND", 2)
    monkeypatch.setattr(runs_api, "_NEW_DIR_GRACE_S", 0)
    # a run being created (no status yet) is never evicted
    half = config.OUTPUT_ROOT / "calibration" / "0123456789abcdef"
    half.mkdir(parents=True)
    ids = []
    for n in (11, 12, 13):
        rid = c.post("/api/calibrations", json={"params": {"fit": {"cterms": n}}}).json()["id"]
        _wait(c, "calibration", rid)
        ids.append(rid)
        time.sleep(0.05)
    kept = sorted(p.name for p in (config.OUTPUT_ROOT / "calibration").iterdir())
    assert ids[0] not in kept and set(ids[1:]) <= set(kept)
    assert half.name in kept


def test_interrupted_run_reads_as_failed(client):
    c, _ = client
    r = c.post("/api/calibrations/resolve", json={"params": {"fit": {"cterms": 20}}}).json()
    d = config.OUTPUT_ROOT / "calibration" / r["id"]
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"state": "running"}))
    st = c.get(f"/api/calibrations/{r['id']}").json()["status"]
    assert st["state"] == "failed" and "interrupted" in st["error"]
    # owned by another live server process (here: this test's parent): kept
    (d / "status.json").write_text(json.dumps({"state": "running", "pid": os.getppid()}))
    assert c.get(f"/api/calibrations/{r['id']}").json()["status"]["state"] == "running"
