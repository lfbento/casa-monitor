from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from db.store import Store
from detect.tracker import TrackState


@pytest.fixture
def store(tmp_path: Path):
    instance = Store(tmp_path / "monitoring.db")
    yield instance
    instance.close()


@pytest.fixture
def make_track() -> Callable[..., TrackState]:
    def factory(track_id: int = 1, cls_name: str = "person", now: float = 0.0) -> TrackState:
        return TrackState(
            track_id=track_id,
            cls_name=cls_name,
            first_seen=now,
            last_seen=now,
        )

    return factory


@pytest.fixture
def notifier_spy():
    calls = []

    def send(text: str) -> bool:
        calls.append(("text", text))
        return True

    def send_photo(path: str, caption: str) -> bool:
        calls.append(("photo", path, caption))
        return True

    return SimpleNamespace(calls=calls, send=send, send_photo=send_photo)


@pytest.fixture
def llm_stub():
    return SimpleNamespace(content=None, finish_reason=None, calls=[])
