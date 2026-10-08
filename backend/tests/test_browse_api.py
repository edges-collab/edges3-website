"""Tests of the raw-data browse endpoints on the synthetic catalog (see conftest).

The synthetic mirror is an EDGES-3 one, so the tests let the page browse
``edges3-mro`` (the site offers EDGES-2 deployments).
"""

from __future__ import annotations

import pytest

import browse_api
import products_api
from conftest import CYCLE_S, NIGHT, T_A, T_B, T_BAD, T_C, T_DAY

DEP = "edges3-mro"


@pytest.fixture
def client(settings, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(browse_api, "DEPLOYMENTS", {DEP: "EDGES-3 (test)"})
    monkeypatch.setattr(browse_api, "HK_SOURCE", {DEP: "templog"})
    products_api.configure(settings)
    app = FastAPI()
    app.include_router(browse_api.router)
    yield TestClient(app)
    products_api.configure(None)


def test_deployments(client):
    assert client.get("/api/browse/deployments").json() == [{
        "name": DEP, "label": "EDGES-3 (test)", "instrument": "EDGES-3", "band_mhz": None,
        "utc_offset_hours": 8.0, "timezone": "AWST", "night_products": True, "calibration": True}]
    # every offered receiver has a site clock in edges-pipeline
    from edges_pipeline.products import SITE_UTC_OFFSET_HOURS

    assert set(browse_api.BANDS_MHZ) <= set(SITE_UTC_OFFSET_HOURS)  # (DEPLOYMENTS is patched here)
    # every offered EDGES-2 antenna has a band
    assert all(browse_api.BANDS_MHZ.get(d) for d in browse_api.DEPLOYMENTS if d.startswith("edges2"))
    assert client.get("/api/browse/edges2-low2-mro/overview").status_code == 404  # not offered


def test_overview(client):
    d = client.get(f"/api/browse/{DEP}/overview").json()
    s = d["summary"]
    # A, B, C, bad, day and the four calibration loads (bad was extracted
    # before it was garbled)
    assert s["n_files"] == 9 and s["n_extracted"] == 9
    assert s["n_calibration_files"] == 4 and s["has_housekeeping"]
    assert s["n_s11_sessions"] == 2  # the catalog's averaged sessions (2025_101_05, 2025_102_02)
    i = d["days"].index(NIGHT)
    assert d["files"][i] == 4  # A, B, C and bad start on 2025-04-10 (UTC)
    j = d["days"].index("2025-04-12")  # the calibration day: four loads, 4 cycles each
    assert d["cal_files"][j] == 4 and d["files"][j] in (0, None) and d["cal_hours"][j] > 0
    assert d["cycles"][i] == pytest.approx(100)  # A 40, B 40, C 10, bad 10
    assert d["hours"][i] == pytest.approx(100 * CYCLE_S / 3600, rel=0.05)
    assert d["days"] == sorted(d["days"]) and len(set(d["days"])) == len(d["days"])
    assert len(d["gb"]) == len(d["days"])


def test_range_and_cycles(client, monkeypatch):
    r = client.get(f"/api/browse/{DEP}/range", params={"start": NIGHT, "end": T_DAY.isoformat()}).json()
    names = [f["name"] for f in r["files"]]
    assert names[:4] == [t.strftime("%Y_%j_%H_%M_%S_ant.acq") for t in (T_A, T_B, T_C, T_BAD)]
    a = r["files"][0]
    assert a["n_cycles"] == 40 and a["t_start_unix"] == pytest.approx(T_A.timestamp())
    c = client.get(f"/api/browse/{DEP}/cycles/{a['file_id']}").json()
    assert c["available"] and len(c["time_unix"]) == 40 and len(c["drops0"]) == 40
    assert max(c["adcmax0"]) == pytest.approx(0.49994, abs=1e-4)  # the ADC full-scale cycle
    # before extraction: no cycles, said so
    import pandas as pd
    from edges_catalog import Catalog

    monkeypatch.setattr(Catalog, "acq_cycles", lambda self, ids: pd.DataFrame())
    nc = client.get(f"/api/browse/{DEP}/cycles/{a['file_id']}").json()
    assert not nc["available"] and "not extracted" in nc["reason"]


@pytest.mark.parametrize(
    ("path", "params", "code"),
    [
        (f"/api/browse/{DEP}/range", {"start": "2025-01-01", "end": "2025-06-01"}, 400),
        (f"/api/browse/{DEP}/range", {"start": "2025-04-10", "end": "2025-04-09"}, 400),
        (f"/api/browse/{DEP}/cycles/999999999", {}, 404),
        (f"/api/browse/{DEP}/s11", {"stamp": 1.0}, 404),
    ],
)
def test_bad_requests(client, path, params, code):
    assert client.get(path, params=params).status_code == code


def test_weather_absent(client):
    w = client.get(f"/api/browse/{DEP}/weather", params={"start": NIGHT, "end": "2025-04-11"}).json()
    assert w["available"] is False and w["reason"]


def test_s11_sessions_and_housekeeping(client):
    r = client.get(f"/api/browse/{DEP}/range", params={"start": "2025-04-11", "end": "2025-04-13"}).json()
    sessions = {x["name"]: x for x in r["s11_sessions"]}
    assert set(sessions) == {"2025_101_05", "2025_102_02"}
    full = sessions["2025_102_02"]
    assert full["session_id"] and full["kind"] == "full" and "ant" in full["labels"]
    s = client.get(f"/api/browse/{DEP}/s11", params={"session_id": full["session_id"]}).json()
    assert set(s["traces"]) == set(full["labels"])  # each file read (or its error said)
    assert client.get(f"/api/browse/{DEP}/s11", params={"session_id": 999999}).status_code == 404
    assert client.get(f"/api/browse/{DEP}/s11").status_code == 400
    loads = {f["load"] for f in r["files"]}
    assert {"amb", "hot", "open", "short"} <= loads
    hk = client.get(f"/api/browse/{DEP}/housekeeping", params={"start": "2025-04-12", "end": "2025-04-13"}).json()
    assert hk["available"] and hk["source"] == "templog"
    assert hk["series"]["hot_load_temperature"]["value"][0] == pytest.approx(111.0)
    assert hk["series"]["hot_load_temperature"]["unit"]


def test_quicklook(client):
    ql = client.get(f"/api/browse/{DEP}/quicklook", params={"start": NIGHT, "end": "2025-04-11"}).json()
    assert ql["available"] and ql["waterfall_stat"] == "median" and ql["rfi_waterfalls"] is True
    mx = client.get(f"/api/browse/{DEP}/quicklook",
                    params={"start": NIGHT, "end": "2025-04-11", "waterfall": "max"}).json()
    assert mx["waterfall_stat"] == "max" and mx["n_rows"] == ql["n_rows"]
    too_long = {"start": "2025-04-01", "end": "2025-04-20"}
    assert client.get(f"/api/browse/{DEP}/quicklook", params=too_long).status_code == 400


def test_night(client):
    from datetime import date

    from edges_pipeline.products import Products

    n = client.get(f"/api/browse/{DEP}/night", params={"date": NIGHT}).json()
    # the same night as the pipeline's (18:00-06:00 AWST)
    assert (n["start_unix"], n["end_unix"]) == Products.night(date.fromisoformat(NIGHT), deployment=DEP)
    assert n["nights_with_data"] == 1 and n["prev_date"] is None and n["timezone"] == "AWST"
    # the daytime file (13:00 AWST) is in no night; the calibration loads are not antenna data
    assert n["first_date"] == NIGHT and n["next_date"] is None
    # latest: the latest night with QL products (Products.latest_night)
    latest = client.get(f"/api/browse/{DEP}/night").json()
    assert latest["date"] == latest["latest_date"] == NIGHT and latest["is_latest"]
    after = client.get(f"/api/browse/{DEP}/night", params={"date": "2025-04-12"}).json()
    assert after["prev_date"] == NIGHT and after["nights_with_data"] == 0 and not after["is_latest"]
    month = client.get(f"/api/browse/{DEP}/night", params={"date": "2025-04-01", "nights": 31}).json()
    assert month["last_date"] == "2025-05-01" and month["nights_with_data"] == 1
    assert month["end_unix"] - month["start_unix"] == pytest.approx(30 * 86400 + 12 * 3600)
    assert client.get(f"/api/browse/{DEP}/night", params={"date": "2025-13-01"}).status_code == 400
    assert client.get(f"/api/browse/{DEP}/night", params={"nights": 40}).status_code == 422
    assert client.get("/api/browse/nowhere/night").status_code == 404
