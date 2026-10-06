"""Unit tests of the calibration settings and gap reasons (no server)."""

from __future__ import annotations

import time

import pytest

import calibrations


def test_clean_params_keeps_only_changes():
    from edges_pipeline.stages import rcal

    p = calibrations.clean_params({"fit": {"cterms": 6.0, "wterms": 7, "wfstart": 50},
                                   "spectra": {"smooth": 8}})
    assert p == {"fit": {"wterms": 7, "wfstart": 50.0}}
    assert isinstance(p["fit"]["wterms"], int) and isinstance(p["fit"]["wfstart"], float)
    assert calibrations.clean_params({}) == {}
    assert calibrations.config_hash({}) == rcal.config_hash()
    # every offered setting is one the stage knows, with an in-range default
    for f in calibrations.form()["fields"]:
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
    with pytest.raises(ValueError, match=match):
        calibrations.clean_params(params)


def test_gap_reasons():
    assert "VNA" in calibrations.gap_reason("2026_257")
    assert "2024–2025" in calibrations.gap_reason("2024_200")
    today = time.strftime("%Y_%j", time.gmtime())
    assert "not processed yet" in calibrations.gap_reason(today)
    assert "no calibration for this day" in calibrations.gap_reason("2023_001")
