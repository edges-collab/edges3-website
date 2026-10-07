"""Unit tests of the calibration settings and gap reasons (no server)."""

from __future__ import annotations

import pytest

import calibrations


def test_clean_params_keeps_only_changes():
    from edges_pipeline.stages import rcal

    D = rcal.DEFAULT_CONFIG
    p = calibrations.clean_params({"fit": {"cterms": 6.0, "wterms": 7, "wfstart": 50},
                                   "spectra": {"smooth": 8}}, D)
    assert p == {"fit": {"wterms": 7, "wfstart": 50.0}}
    assert isinstance(p["fit"]["wterms"], int) and isinstance(p["fit"]["wfstart"], float)
    assert calibrations.clean_params({}, D) == {}
    assert calibrations.config_hash(calibrations.effective(D, {})) == rcal.config_hash()
    # every offered setting is one the stage knows, with an in-range default
    for f in calibrations.form(D)["fields"]:
        d = rcal.DEFAULT_CONFIG[f["section"]][f["key"]]
        assert f["min"] <= d <= f["max"], f


@pytest.mark.parametrize(
    ("params", "match"),
    [
        ({"fit": {"wfstart": 100, "wfstop": 105}}, "at least"),
        ({"fit": {"nfit2": 2}}, r"in \[3, 60\]"),
        ({"fit": {"cterms": float("nan")}}, "in"),
        ({"spectra": []}, "unknown settings section"),
    ],
)
def test_clean_params_rejects(params, match):
    from edges_pipeline.stages import rcal

    with pytest.raises(ValueError, match=match):
        calibrations.clean_params(params, rcal.DEFAULT_CONFIG)


class _Prod:
    """The two queries default_config makes, of a products database."""

    def __init__(self, h, config):
        self.h, self.config = h, config

    def config_hash(self, stage, config_hash=None):
        if self.h is None:
            raise LookupError("no finished rcal products yet")
        return self.h

    def sql(self, query, params=()):
        import json

        import pandas as pd

        return pd.DataFrame({"config_text": [json.dumps(self.config)]})


def test_default_config_is_the_promoted_one():
    from edges_pipeline.stages import rcal

    promoted = calibrations.effective(rcal.DEFAULT_CONFIG, {"fit": {"cterms": 8}})
    d = calibrations.default_config(_Prod(rcal.config_hash(promoted), promoted))
    assert d["config"]["fit"]["cterms"] == 8 and not d["skew"]
    # the settings are overrides of it: 8 is the default, 6 a change
    assert calibrations.clean_params({"fit": {"cterms": 8}}, d["config"]) == {}
    assert calibrations.clean_params({"fit": {"cterms": 6}}, d["config"]) == {"fit": {"cterms": 6}}
    # products of another stage version: the installed pipeline cannot reproduce them
    assert calibrations.default_config(_Prod("0" * 64, promoted))["skew"]
    # no products at all
    none = calibrations.default_config(_Prod(None, None))
    assert none["hash"] is None and none["config"] == rcal.DEFAULT_CONFIG


def test_status_texts():
    statuses = {"2026_257": {"status": "s11_grids_differ",
                             "reason": calibrations.status_text("s11_grids_differ")}}
    assert "VNA" in calibrations.unavailable_reason(statuses, "2026_257")
    assert "no calibration spectra" in calibrations.unavailable_reason(statuses, "2024_200")
    assert "boom" in calibrations.status_text("failed", "boom")
    assert calibrations.status_text("something_new") == "something_new"


def test_day_statuses_are_served_while_refreshing(monkeypatch):
    """Stale statuses are returned at once and refreshed in the background."""
    import threading
    import time

    import pandas as pd

    release = threading.Event()
    calls = []

    class Prod:
        def config_hash(self, stage, config_hash=None):
            return "h"

        def calibration_days(self, deployment):
            calls.append(deployment)
            if len(calls) > 1:
                release.wait(5)
            status = "done" if len(calls) == 1 else "no_temperature"
            return pd.DataFrame([{"cal_day": "2026_250", "status": status, "s11_session": None,
                                  "issues": [], "error": None}])

    calibrations.clear_cache()
    prod = Prod()
    assert calibrations.day_statuses(prod)["2026_250"]["status"] == "done"  # computed, waited
    assert calls == ["edges3-mro"]
    monkeypatch.setattr(calibrations, "STATUS_TTL_S", 0)
    # stale: the old answer at once, one refresh started (and still running)
    assert calibrations.day_statuses(prod)["2026_250"]["status"] == "done"
    assert calibrations.day_statuses(prod)["2026_250"]["status"] == "done"
    release.set()
    for _ in range(100):
        if calibrations._status_cache["rows"]["2026_250"]["status"] == "no_temperature":
            break
        time.sleep(0.02)
    assert calibrations._status_cache["rows"]["2026_250"]["reason"].startswith("too few")
    assert len(calls) == 2 and not calibrations._status_cache["refreshing"]
    calibrations.clear_cache()
