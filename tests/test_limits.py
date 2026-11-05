from __future__ import annotations

import pytest

from settings import PipelineLimits

ENV_NAMES = [
    "MAX_FRAME_AGE_SECONDS",
    "ALERT_MAX_LATENCY_SECONDS",
    "ANALYSIS_QUEUE_MAX",
    "ANALYSIS_MAX_AGE_SECONDS",
    "ANALYSIS_DEDUP_SECONDS",
    "ANALYSIS_MIN_AREA_PX",
    "ANALYSIS_MIN_CONF",
    "ANALYSIS_TIMEOUT_SECONDS",
]


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    limits = PipelineLimits()

    assert limits.max_frame_age_s == 2.0
    assert limits.alert_max_latency_s == 5.0
    assert limits.analysis_queue_max == 8
    assert limits.analysis_max_age_s == 180.0
    assert limits.analysis_dedup_s == 60.0
    assert limits.analysis_min_area_px == 2500
    assert limits.analysis_min_conf == 0.5
    assert limits.analysis_timeout_s == 30.0


def test_invalid_or_negative_values_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FRAME_AGE_SECONDS", "abc")
    monkeypatch.setenv("ANALYSIS_QUEUE_MAX", "0")
    monkeypatch.setenv("ANALYSIS_MIN_CONF", "-1")
    monkeypatch.setenv("ANALYSIS_MAX_AGE_SECONDS", "")

    limits = PipelineLimits()

    assert limits.max_frame_age_s == 2.0
    assert limits.analysis_queue_max == 8
    assert limits.analysis_min_conf == 0.5
    assert limits.analysis_max_age_s == 180.0
