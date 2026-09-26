"""Tests of the read-only /api endpoints on a synthetic catalog (see conftest)."""

from __future__ import annotations

import base64
import importlib
import json
import sys

import numpy as np
import pytest
from conftest import CYCLE_S, NIGHT, T_A, T_B, T_BAD, T_C

import products_api


def _decode(w):
    a = np.frombuffer(base64.b64decode(w["data"]), dtype="<f4")
    return a.reshape(w["shape"])


def test_insert_gaps():
    t = np.array([0.0, 10, 20, 30, 1000, 1010])
    wf = np.arange(12.0).reshape(6, 2)
    t2, cols = products_api.insert_gaps(t, {"wf": wf, "x": t * 2})
    assert list(t2) == [0, 10, 20, 30, 40, 990, 1000, 1010]
    assert np.isnan(cols["wf"][4:6]).all()
    assert np.isnan(cols["x"][4:6]).all()
    assert cols["wf"].shape == (8, 2)
    # no gaps: unchanged
    t3, _ = products_api.insert_gaps(t[:4], {"x": t[:4]})
    assert len(t3) == 4


def test_nights_latest(client):
    r = client.get("/api/nights/latest")
    assert r.status_code == 200
    d = r.json()
    assert d["date"] == NIGHT
    assert d["timezone"] == "AWST"
    assert d["end_unix"] - d["start_unix"] == 12 * 3600
    assert d["start_unix"] == T_A.replace(hour=10, minute=0).timestamp()  # 18:00 AWST


def test_night_payload(client):
    r = client.get("/api/night")
    assert r.status_code == 200
    d = r.json()
    json.dumps(d, allow_nan=False)  # strict JSON (no NaN literals)
    assert d["night"]["date"] == NIGHT and d["night"]["is_latest"]
    assert d["warnings"] == []

    ql = d["quicklook"]
    assert ql["available"]
    assert ql["n_rows"] == 40 + 40 + 10
    assert len(ql["freq_edges_mhz"]) == len(ql["freq_mhz"]) + 1 == 321
    wf = _decode(ql["waterfall_q"])
    t = np.array(ql["time_unix"], dtype=float)
    assert wf.shape == (len(t), 320)
    # the A->B (~1 h) and B->C (~45 min) gaps each get two NaN rows
    assert len(t) == 90 + 4
    nan_rows = np.isnan(wf).all(axis=1)
    assert nan_rows.sum() == 4
    assert np.all(np.diff(t) > 0)
    gap = np.nonzero(nan_rows)[0][:2]
    assert t[gap[0]] == pytest.approx(T_A.timestamp() + 40 * CYCLE_S, abs=1)
    assert t[gap[1]] == pytest.approx(T_B.timestamp() - CYCLE_S, abs=1)
    assert ql["lst_hour"][gap[0]] is None
    assert np.nanmedian(wf) == pytest.approx(0.5, abs=0.05)
    assert ql["waterfall_p0"] is None  # only on request

    band = d["band"]
    assert len(band["time_unix"]) == 90 + 4
    assert band["q_band_mhz"] == [60.0, 90.0]
    assert sum(v is None for v in band["q_median"]) == 4

    hk = {s["name"]: s for s in d["housekeeping"]["series"]}
    assert set(hk) == set(products_api.HOUSEKEEPING_NAMES)
    hot = hk["hot_load_temperature"]
    assert hot["unit"] == "°C" and hot["code"] == 102
    assert len(hot["t_unix"]) == 26 + 1  # one null at the 1.5 h gap
    assert hot["value"].count(None) == 1
    assert "pr59_current" not in hk

    files = {f["name"]: f for f in d["files"]}
    texts = {n: " ".join(b["text"] for b in f["badges"]) for n, f in files.items()}
    a, c = T_A.strftime("2025_100_%H_%M_%S_ant.acq"), T_C.strftime("2025_100_%H_%M_%S_ant.acq")
    assert files[a]["n_adc_clip_cycles"] == 1 and "ADC full scale" in texts[a]
    assert files[a]["has_l1"] and files[a]["has_ql"]
    assert "short (10 cycles)" in texts[c]
    assert texts[T_B.strftime("2025_100_%H_%M_%S_ant.acq")] == "ok"
    bad = T_BAD.strftime("2025_100_%H_%M_%S_ant.acq")
    # catalogued, but read_acq cannot decode it: shown as missing, not an error
    assert not files[bad]["has_l1"] and not files[bad]["has_ql"]
    assert "unreadable (no products)" in texts[bad]
    assert "ADC full scale: 1 cycle" in texts[a] and "1 cycles" not in texts[a]
    assert d["events"]["adc_clip_unix"] == [T_A.timestamp() + 5 * CYCLE_S]


def test_night_p0_and_cache(client):
    d = client.get("/api/night", params={"p0": "true"}).json()
    p0 = _decode(d["quicklook"]["waterfall_p0"])
    assert p0.shape == _decode(d["quicklook"]["waterfall_q"]).shape
    again = client.get("/api/night", params={"p0": "true"}).json()
    assert again["generated_at"] == d["generated_at"]  # served from the cache


def test_night_by_date_outside_coverage(client):
    d = client.get("/api/night", params={"date": "2025-03-01"}).json()
    assert d["night"]["date"] == "2025-03-01" and not d["night"]["is_latest"]
    assert not d["quicklook"]["available"]
    assert "outside QL coverage" in d["quicklook"]["reason"]
    assert d["files"] == []
    assert d["band"]["time_unix"] == []


def test_night_decimation(client):
    d = client.get("/api/night", params={"date": NIGHT, "max_rows": 30}).json()
    assert d["quicklook"]["decimation"] == 3
    assert d["quicklook"]["n_rows"] == 30


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/night", {"date": "2025-13-01"}),
        ("/api/night", {"date": "yesterday"}),
        ("/api/quicklook", {"start": "2025-04-10", "end": "2025-04-01"}),
        ("/api/quicklook", {"start": "2025-04-01", "end": "2025-04-20"}),
        ("/api/quicklook", {"start": "soon", "end": "2025-04-20"}),
        ("/api/l1", {"start": "2025-04-10", "end": "2025-04-10"}),
    ],
)
def test_bad_parameters(client, path, params):
    assert client.get(path, params=params).status_code == 400


def test_range_endpoints(client):
    rng = {"start": "2025-04-10T10:00", "end": str(T_C.timestamp() + 3600)}
    ql = client.get("/api/quicklook", params=rng).json()
    assert ql["n_rows"] == 90
    rows = client.get("/api/l1", params=rng).json()["rows"]
    assert len(rows) == 3
    assert {r["name"] for r in rows} >= {T_A.strftime("2025_100_%H_%M_%S_ant.acq")}
    assert all("path" not in r for r in rows)
    hk = client.get("/api/housekeeping", params={**rng, "names": "battery_voltage"}).json()
    assert [s["name"] for s in hk["series"]] == ["battery_voltage"]
    assert hk["series"][0]["value"][0] == pytest.approx(13.78)


def test_status(client):
    d = client.get("/api/status").json()
    assert d["available"]
    assert d["ql"]["n_files"] == 3
    assert d["l1"]["n_files"] == 3


def test_packages_missing(client, monkeypatch):
    monkeypatch.setattr(products_api, "IMPORT_ERROR", "No module named 'edges_pipeline'")
    r = client.get("/api/night")
    assert r.status_code == 503
    assert "not installed" in r.json()["detail"]
    assert client.get("/api/status").json() == {
        "available": False, "error": "No module named 'edges_pipeline'"
    }


def test_backend_app_routes(settings, tmp_path, monkeypatch):
    """The main app serves /api/* itself (not via the SPA fallback)."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("EDGES_OUTPUT_ROOT", str(tmp_path / "outputs"))
    monkeypatch.setenv("EDGES_RAW_DATA_ROOT", str(tmp_path / "no-raw-data"))
    for m in ("config", "scan_dates", "backend_api"):
        sys.modules.pop(m, None)
    try:
        backend_api = importlib.import_module("backend_api")
        products_api.configure(settings)
        client = TestClient(backend_api.app)  # no startup event: no date scan
        r = client.get("/api/nights/latest")
        assert r.status_code == 200 and r.json()["date"] == NIGHT
        assert client.get("/api/nope").status_code == 404
    finally:
        products_api.configure(None)
        for m in ("config", "scan_dates", "backend_api"):
            sys.modules.pop(m, None)
