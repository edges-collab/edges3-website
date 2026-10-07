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
    products_api.configure(settings)
    app = FastAPI()
    app.include_router(browse_api.router)
    yield TestClient(app)
    products_api.configure(None)


def test_deployments(client):
    assert client.get("/api/browse/deployments").json() == [{"name": DEP, "label": "EDGES-3 (test)"}]
    assert client.get("/api/browse/edges2-low2-mro/overview").status_code == 404  # not offered


def test_overview(client):
    d = client.get(f"/api/browse/{DEP}/overview").json()
    s = d["summary"]
    # A, B, C, bad, day and the four calibration loads (bad was extracted
    # before it was garbled)
    assert s["n_files"] == 9 and s["n_extracted"] == 9
    assert s["n_s11_sessions"] == 0  # the synthetic sessions are stamped to the hour, not the second
    i = d["days"].index(NIGHT)
    assert d["files"][i] == 4  # A, B, C and bad start on 2025-04-10 (UTC)
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
