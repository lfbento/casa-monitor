from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from capture.osd import OsdClock, read_osd

FIXTURES = Path(__file__).parent / "fixtures" / "osd"


def _load(name: str) -> np.ndarray:
    image = cv2.imread(str(FIXTURES / name), cv2.IMREAD_GRAYSCALE)
    assert image is not None and image.shape in ((1440, 2560), (1080, 1920))
    return image


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("osd_0800.png", "2026-11-05 08:37:25"),
        ("osd_0230.png", "2026-11-05 02:34:35"),
        ("osd_0837.png", "2026-11-05 08:38:39"),
        ("osd_1920.png", "2026-11-05 08:50:28"),
    ],
)
def test_reads_known_osd(name: str, expected: str) -> None:
    captured, error = read_osd(_load(name))

    assert error is None
    assert captured is not None
    assert captured.strftime("%Y-%m-%d %H:%M:%S") == expected


def test_rejects_illegible_osd() -> None:
    captured, error = read_osd(_load("osd_bad.png"))

    assert captured is None
    assert error


def test_rejects_unexpected_resolution() -> None:
    captured, error = read_osd(np.zeros((960, 1280), np.uint8))

    assert captured is None
    assert "resolucao" in (error or "")


def test_clock_age_follows_arrival_and_caches_reading() -> None:
    frame = _load("osd_0800.png")
    clock = OsdClock(interval_s=30.0)
    observed = datetime(2026, 11, 5, 8, 37, 25).astimezone().timestamp()

    captured, age = clock.observe(frame, arrival_wall=observed + 5, monotonic=0.0)
    assert captured is not None and int(age or 0) == 5

    # dentro do intervalo não relê: a idade medida na leitura continua valendo
    captured, age = clock.observe(frame, arrival_wall=observed + 9, monotonic=1.0)
    assert captured is not None and int(age or 0) == 5

    # leitura ilegível depois do intervalo zera a referência, não inventa idade
    clock2 = OsdClock(interval_s=0.0001)
    clock2.observe(_load("osd_bad.png"), arrival_wall=observed, monotonic=0.0)
    captured, age = clock2.observe(_load("osd_bad.png"), arrival_wall=observed, monotonic=1.0)
    assert captured is None and age is None
    assert clock2.failures >= 1


def test_clock_retries_quickly_after_failure() -> None:
    frame = _load("osd_0800.png")
    clock = OsdClock(interval_s=30.0)
    observed = datetime(2026, 11, 5, 8, 37, 25).astimezone().timestamp()

    clock.observe(_load("osd_bad.png"), arrival_wall=observed, monotonic=0.0)
    assert clock.failures == 1
    # 6 s depois (bem antes dos 30 s) já tenta de novo e recupera
    captured, _ = clock.observe(frame, arrival_wall=observed, monotonic=6.0)
    assert captured is not None


def test_clock_reads_again_after_interval() -> None:
    frame = _load("osd_0800.png")
    clock = OsdClock(interval_s=1.0)
    observed = datetime(2026, 11, 5, 8, 37, 25).astimezone().timestamp()

    clock.observe(frame, arrival_wall=observed, monotonic=0.0)
    assert clock.failures == 0
    clock.observe(_load("osd_bad.png"), arrival_wall=observed, monotonic=2.0)
    assert clock.failures == 1
    captured, _ = clock.observe(frame, arrival_wall=observed, monotonic=4.0)
    assert captured is not None
