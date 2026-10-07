"""Tests of the read-only /api endpoints on a synthetic catalog (see conftest)."""

from __future__ import annotations

import base64
import importlib
import json
import sys

import numpy as np
import pytest

import products_api
from conftest import CYCLE_S, NFREQ, NIGHT, RFI_CHANNEL, T_A, T_B, T_BAD, T_C, T_DAY


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


def test_insert_gaps_at_segments():
    t = np.array([0.0, 10, 20, 30, 40, 50])
    seg = np.array([0, 0, 0, 1, 1, 1])  # e.g. Products.quicklook()["segment"]
    t2, cols = products_api.insert_gaps(t, {"x": t}, segment=seg)
    assert len(t2) == 8 and np.isnan(cols["x"][3:5]).all()
    assert t2[2] < t2[3] < t2[4] < t2[5]  # inserted rows stay between neighbours


def test_badges_midnight_split():
    row = {
        "has_l1": True, "has_ql": True, "n_cycles": 310, "t_start_unix": 0.0,
        "t_end_unix": T_A.replace(hour=23, minute=59, second=0).timestamp(),
        "total_data_drops": 0, "n_adc_clip_cycles": 0, "rfi_occupancy": 0.001,
        "n_outlier_cycles": 0, "n_nonfinite": 0,
    }
    texts = [b["text"] for b in products_api._badges(row, 450, False)]
    assert texts == ["ends at the UTC-midnight file split"]
    row["t_end_unix"] -= 3600
    assert [b["text"] for b in products_api._badges(row, 450, False)] == ["short (310 cycles)"]
    row["has_l1"] = False
    assert "no L1 (failed)" in [b["text"] for b in products_api._badges(row, 450, True)]


def test_nights_latest(client):
    r = client.get("/api/nights/latest")
    assert r.status_code == 200
    d = r.json()
    assert d["date"] == NIGHT
    assert d["timezone"] == "AWST"
    assert d["end_unix"] - d["start_unix"] == 12 * 3600
    assert d["start_unix"] == T_A.replace(hour=10, minute=0).timestamp()  # 18:00 AWST
    # the latest data (T_DAY, 13:00 AWST next day) is in no night
    assert T_DAY.timestamp() > d["end_unix"]


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
    bad = T_BAD.strftime("2025_100_%H_%M_%S_ant.acq")
    # catalogued, but read_acq cannot decode it: shown as missing, not an error
    assert not files[bad]["has_l1"] and not files[bad]["has_ql"]
    assert "unreadable (no products)" in texts[bad]
    assert "ADC full scale: 1 cycle" in texts[a] and "1 cycles" not in texts[a]
    assert d["events"]["adc_clip_unix"] == [T_A.timestamp() + 5 * CYCLE_S]

    # the antenna dropout (edges-database ISSUES #24): counted in the night,
    # badged, and on the events strip
    b = T_B.strftime("2025_100_%H_%M_%S_ant.acq")
    assert files[b]["n_dropout_cycles"] == 1
    assert files[b]["badges"][0] == {"level": "critical", "text": "antenna dropouts: 1 cycle"}
    assert d["events"]["dropout_unix"] == [T_B.timestamp() + 7 * CYCLE_S]
    assert d["dropouts"] == {"n_cycles": 1, "n_files": 1}
    # window (in-night) columns from Products.l1(clip=True)
    assert files[a]["n_cycles_window"] == 40


def test_night_p0_and_cache(client):
    d = client.get("/api/night", params={"p0": "true"}).json()
    p0 = _decode(d["quicklook"]["waterfall_p0"])
    assert p0.shape == _decode(d["quicklook"]["waterfall_q"]).shape
    again = client.get("/api/night", params={"p0": "true"}).json()
    assert again["generated_at"] == d["generated_at"]  # served from the cache


def _rfi_bin(ql):
    """Rows of file C and the column of the RFI line's bin."""
    f = np.asarray(ql["freq_edges_mhz"])
    col = int(np.searchsorted(f, RFI_CHANNEL * 200 / NFREQ, side="right") - 1)
    t = np.asarray([np.nan if v is None else v for v in ql["time_unix"]])
    rows = (t >= T_C.timestamp()) & (t < T_C.timestamp() + 10 * CYCLE_S)
    return rows, col


def test_night_waterfalls_that_keep_rfi(client):
    out = {}
    for stat in ("median", "mean", "max"):
        ql = client.get("/api/night", params={"waterfall": stat}).json()["quicklook"]
        assert ql["waterfall_stat"] == stat and ql["rfi_waterfalls"] is True
        assert ql["waterfall_note"] is None
        rows, col = _rfi_bin(ql)
        out[stat] = np.nanmedian(_decode(ql["waterfall_q"])[rows, col])
    # Q = 19 in one of the bin's 3 channels, ~0.5 in the others
    assert out["median"] < 1 and 5 < out["mean"] < 8 and out["max"] > 15


def test_waterfalls_before_ql_version_3(client, monkeypatch):
    """Older products have no mean/max: the median, said so."""
    prod = products_api.get_products()
    real = prod.quicklook

    def old(*a, quantities=("waterfall_q",), **k):
        if any(q in ("waterfall_q_mean", "waterfall_q_max") for q in quantities):
            raise KeyError("waterfall_q_max")
        return real(*a, quantities=quantities, **k)

    monkeypatch.setattr(prod, "quicklook", old)
    products_api._cache.clear()
    ql = client.get("/api/night", params={"waterfall": "max"}).json()["quicklook"]
    assert ql["available"] and ql["waterfall_stat"] == "median"
    assert ql["rfi_waterfalls"] is False and "QL version 3" in ql["waterfall_note"]
    ql = client.get("/api/night").json()["quicklook"]
    assert ql["rfi_waterfalls"] is False and ql["waterfall_note"] is None
    products_api._cache.clear()


def test_unknown_waterfall_statistic(client):
    assert client.get("/api/night", params={"waterfall": "mode"}).status_code == 422


def test_night_by_date_outside_coverage(client):
    d = client.get("/api/night", params={"date": "2025-03-01"}).json()
    assert d["night"]["date"] == "2025-03-01" and not d["night"]["is_latest"]
    assert not d["quicklook"]["available"]
    assert "outside QL coverage" in d["quicklook"]["reason"]
    assert d["files"] == []
    assert d["band"]["time_unix"] == []


def test_night_decimation(client):
    d = client.get("/api/night", params={"date": NIGHT, "max_rows": 30}).json()
    ql = d["quicklook"]
    # groups never straddle the gaps: 40 + 40 + 10 cycles in 3 segments
    assert ql["n_segments"] == 3
    assert ql["n_rows"] <= 30 and ql["decimation"] > 1
    wf = _decode(ql["waterfall_q"])
    t = np.array(ql["time_unix"], dtype=float)
    data_t = t[~np.isnan(wf).all(axis=1)]
    spans = [(T_A, 40), (T_B, 40), (T_C, 10)]
    assert all(
        any(s.timestamp() <= x <= s.timestamp() + n * CYCLE_S for s, n in spans) for x in data_t
    )  # no averaged rows inside gaps


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/night", {"date": "2025-13-01"}),
        ("/api/night", {"date": "yesterday"}),
        ("/api/night", {"date": "9999-12-31"}),
        ("/api/quicklook", {"start": "2025-04-01", "end": "2025-04-05", "p0": "true"}),
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
    assert d["ql"]["n_files"] == 4  # antenna files only
    assert d["l1"]["n_files"] == 8  # plus the four calibration loads


def test_database_missing(tmp_path):
    from edges_pipeline.config import Settings
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    products_api.configure(Settings(tmp_path / "c.sqlite", tmp_path / "p.sqlite", tmp_path))
    try:
        app = FastAPI()
        app.include_router(products_api.router)
        client = TestClient(app)
        for path in ("/api/night", "/api/nights/latest", "/api/night?date=2025-04-10"):
            r = client.get(path)
            assert r.status_code == 503, path
            assert "unavailable" in r.json()["detail"]
        assert client.get("/api/status").json()["available"] is False
    finally:
        products_api.configure(None)


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
    for m in ("config", "backend_api"):
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
        for m in ("config", "backend_api"):
            sys.modules.pop(m, None)
