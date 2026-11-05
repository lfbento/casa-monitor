from __future__ import annotations

import numpy as np

from events.identity import match


def _vec(x: float, y: float) -> np.ndarray:
    v = np.array([x, y], dtype=np.float32)
    return v / np.linalg.norm(v)


def _person(person_id: int, name: str, vec: np.ndarray, source: str = "person_crop") -> dict:
    return {"id": person_id, "name": name, "hist": vec.tobytes(), "signature_source": source}


def test_valid_unique_match():
    target = _vec(1.0, 0.0)
    known = [_person(1, "Bento", _vec(1.0, 0.0))]
    res = match(target, known, threshold=0.95, margin=0.05)
    assert res.matched and res.name == "Bento"


def test_below_threshold_is_unknown():
    target = _vec(1.0, 0.0)
    known = [_person(1, "Bento", _vec(0.0, 1.0))]
    res = match(target, known, threshold=0.95, margin=0.05)
    assert res.status == "unknown" and res.name is None


def test_two_close_candidates_is_uncertain():
    target = _vec(1.0, 0.0)
    known = [
        _person(1, "Bento", _vec(1.0, 0.01)),
        _person(2, "Ana", _vec(1.0, 0.02)),
    ]
    res = match(target, known, threshold=0.90, margin=0.05)
    assert res.status == "uncertain" and not res.matched


def test_legacy_full_frame_signature_is_ignored():
    target = _vec(1.0, 0.0)
    known = [_person(1, "Bento", _vec(1.0, 0.0), source="full_frame")]
    res = match(target, known, threshold=0.95, margin=0.05)
    assert res.status == "unknown"


def test_missing_hist_returns_unknown():
    res = match(None, [_person(1, "Bento", _vec(1.0, 0.0))])
    assert res.status == "unknown"
