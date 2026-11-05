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


FRESHNESS_ENV = [
    "CAPTURE_AGE_WARN_SECONDS",
    "CAPTURE_AGE_FAIL_SECONDS",
    "CAPTURE_AGE_OCR_INTERVAL_SECONDS",
    "CAPTURE_AGE_RESYNC_SECONDS",
    "CAPTURE_AGE_RESYNC_WAIT_SECONDS",
    "DETECT_MIN_EFFECTIVE_RATIO",
    "MOTION_SANITY_INTERVAL_SECONDS",
    "TRACK_MIN_HITS",
    "TRACK_MIN_LIFETIME_S",
    "PASSAGE_MERGE_SECONDS",
    "PASSAGE_MERGE_DISTANCE_PX",
    "PASSAGE_MIN_PATH_PX",
]


def test_freshness_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in FRESHNESS_ENV:
        monkeypatch.delenv(name, raising=False)

    limits = PipelineLimits()

    assert limits.capture_age_warn_s == 30.0
    assert limits.capture_age_fail_s == 60.0
    assert limits.capture_age_ocr_interval_s == 30.0
    assert limits.capture_age_resync_s == 300.0
    assert limits.capture_age_resync_wait_s == 10.0
    assert limits.detect_min_effective_ratio == 0.5
    assert limits.motion_sanity_interval_s == 10.0
    assert limits.track_min_hits == 3
    assert limits.track_min_lifetime_s == 1.5
    assert limits.passage_merge_s == 5.0
    assert limits.passage_merge_distance_px == 200
    assert limits.passage_min_path_px == 40


def test_freshness_invalid_values_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPTURE_AGE_WARN_SECONDS", "abc")
    monkeypatch.setenv("CAPTURE_AGE_FAIL_SECONDS", "0")
    monkeypatch.setenv("TRACK_MIN_HITS", "-2")
    monkeypatch.setenv("PASSAGE_MIN_PATH_PX", "")

    limits = PipelineLimits()

    assert limits.capture_age_warn_s == 30.0
    assert limits.capture_age_fail_s == 60.0
    assert limits.track_min_hits == 3
    assert limits.passage_min_path_px == 40
