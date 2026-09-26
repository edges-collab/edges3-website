"""Tests of the calibration/observation job runner, with a fake stage script."""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile

import pytest
from conftest import CAL_DATE, S11_GOOD, S11_OLD

import config
import products_api
import runs_api

FAKE = """
import json, os, pathlib, sys
a = sys.argv
d = pathlib.Path(a[a.index("--run_dir") + 1])
stage = a[a.index("--stage") + 1]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(stage + "\\n")
if os.environ.get("FAKE_FAIL") == stage:
    print("boom"); sys.exit(3)
if stage == "observation":
    assert (pathlib.Path(a[a.index("--calibration_dir") + 1]) / "result.json").exists()
params = json.loads(pathlib.Path(a[a.index("--params") + 1]).read_text())
(d / "plots.npz").write_bytes(b"x")
(d / "result.json").write_text(json.dumps({"kind": stage, "params": params}))
"""


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
    (tmp_path / "calls.txt").write_text("")
    products_api.configure(settings)
    app = FastAPI()
    app.include_router(runs_api.router)
    yield TestClient(app), tmp_path / "calls.txt"
    products_api.configure(None)


def _wait(client, kind, rid, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/{kind}s/{rid}").json()
        if d["status"]["state"] in ("done", "failed"):
            return d
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_calibration_run_and_reuse(client):
    c, calls = client
    opts = c.get("/api/calibrations/options").json()
    assert opts["calibration"] == [CAL_DATE] and opts["defaults"]["cterms"] == 6
    r = c.post("/api/calibrations/resolve", json={}).json()
    assert r["status"] is None and r["inputs"]["dates"]["s11"] == S11_GOOD
    started = c.post("/api/calibrations", json={}).json()
    assert started["id"] == r["id"]
    done = _wait(c, "calibration", r["id"])
    assert done["status"]["state"] == "done"
    assert done["result"]["params"]["cterms"] == 6
    assert done["base_url"] == f"/data/calibration/{r['id']}/"
    # the same request again: found, not recomputed
    again = c.post("/api/calibrations", json={"cal": CAL_DATE, "s11": S11_GOOD}).json()
    assert again["id"] == r["id"] and again["status"]["state"] == "done"
    assert calls.read_text().split() == ["calibration"]
    # other parameters: another run
    other = c.post("/api/calibrations", json={"params": {"cterms": 7}}).json()
    assert other["id"] != r["id"]
    _wait(c, "calibration", other["id"])
    zf = zipfile.ZipFile(io.BytesIO(c.get(f"/api/runs/calibration/{r['id']}/download").content))
    assert {"result.json", "inputs.json", "params.json"} <= set(zf.namelist())


def test_observation_runs_its_calibration_first(client):
    c, calls = client
    body = {"night": "Latest", "calibration": {"cal": CAL_DATE, "s11": S11_OLD}}
    r = c.post("/api/observations/resolve", json=body).json()
    assert r["calibration"]["status"] is None and r["inputs"]["night"]["date"] == "2025-04-10"
    started = c.post("/api/observations", json=body).json()
    d = _wait(c, "observation", started["id"])
    assert d["status"]["state"] == "done"
    assert d["request"]["calibration_id"] == r["calibration"]["id"]
    assert calls.read_text().split() == ["calibration", "observation"]
    assert d["result"]["params"]["ant_s11_fstart"] == 58.0


def test_failed_run_reports_and_retries(client, monkeypatch):
    c, calls = client
    monkeypatch.setenv("FAKE_FAIL", "calibration")
    rid = c.post("/api/calibrations", json={"params": {"cterms": 9}}).json()["id"]
    d = _wait(c, "calibration", rid)
    assert d["status"]["state"] == "failed" and "exit 3" in d["status"]["error"]
    assert "boom" in d["log_tail"]
    # an observation on a failed calibration fails too
    obs = c.post("/api/observations", json={"calibration": {"params": {"cterms": 9}}}).json()
    assert _wait(c, "observation", obs["id"])["status"]["state"] == "failed"
    monkeypatch.delenv("FAKE_FAIL")
    rid2 = c.post("/api/calibrations", json={"params": {"cterms": 9}}).json()["id"]
    assert rid2 == rid and _wait(c, "calibration", rid)["status"]["state"] == "done"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/calibrations", {"params": {"cterms": 0}}),
        ("/api/calibrations", {"params": {"cterms": 6.5}}),
        ("/api/calibrations", {"params": {"fstart": 150, "fstop": 100}}),
        ("/api/calibrations", {"params": {"nonsense": 1}}),
        ("/api/calibrations", {"cal": "1999_001"}),
        ("/api/observations", {"night": "1999-01-01"}),
        ("/api/observations", {"params": {"ant_s11_fstart": 120, "ant_s11_fstop": 60}}),
    ],
)
def test_bad_requests(client, path, body):
    c, _ = client
    assert c.post(path, json=body).status_code == 400


def test_unknown_runs(client):
    c, _ = client
    assert c.get("/api/calibrations/0123456789abcdef").status_code == 404
    assert c.get("/api/calibrations/../../etc").status_code == 404
    assert c.get("/api/runs/nope/0123456789abcdef/download").status_code == 404


def test_eviction(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(runs_api, "MAX_RUNS_PER_KIND", 2)
    ids = []
    for n in (11, 12, 13):
        rid = c.post("/api/calibrations", json={"params": {"cterms": n}}).json()["id"]
        _wait(c, "calibration", rid)
        ids.append(rid)
        time.sleep(0.05)
    kept = sorted(p.name for p in (config.OUTPUT_ROOT / "calibration").iterdir())
    assert ids[0] not in kept and set(ids[1:]) <= set(kept)


def test_interrupted_run_reads_as_failed(client):
    c, _ = client
    r = c.post("/api/calibrations/resolve", json={"params": {"cterms": 20}}).json()
    d = config.OUTPUT_ROOT / "calibration" / r["id"]
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"state": "running"}))
    st = c.get(f"/api/calibrations/{r['id']}").json()["status"]
    assert st["state"] == "failed" and "interrupted" in st["error"]
