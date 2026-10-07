"""Unit tests of the calibration settings and gap reasons (no server)."""

from __future__ import annotations

import time

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


def test_gap_reasons():
    assert "VNA" in calibrations.gap_reason("2026_257")
    assert "2024–2025" in calibrations.gap_reason("2024_200")
    today = time.strftime("%Y_%j", time.gmtime())
    assert "not processed yet" in calibrations.gap_reason(today)
    assert "no calibration for this day" in calibrations.gap_reason("2023_001")
    assert calibrations.hopeless("2023_001") is None and calibrations.hopeless("2022_316")
