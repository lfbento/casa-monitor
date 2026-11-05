from __future__ import annotations

import numpy as np

from events.state_machine import ALERT_TAGS, EventEngine, EventImages
from settings import RulesConfig
from zones.roi import Zones


def _engine(store, tmp_path):
    return EventEngine(
        Zones.load(), RulesConfig(), store,
        snapshot_dir=tmp_path / "snapshots",
    )


def _track_with_crop(make_track):
    st = make_track(track_id=7)
    st.bboxes.append((100.0, 100.0, 300.0, 400.0))
    st.best_crop = b"\xff\xd8\xff\xe0fakejpeg"
    return st


def test_alert_snapshot_separates_context_and_person_crop(store, tmp_path, make_track):
    engine = _engine(store, tmp_path)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[100:400, 100:300] = 200  # pessoa
    st = _track_with_crop(make_track)

    imgs = engine._save_snapshot(frame, st, next(iter(ALERT_TAGS)))

    assert isinstance(imgs, EventImages)
    assert imgs.context and imgs.person_crop
    assert imgs.context != imgs.person_crop
    from pathlib import Path
    assert Path(imgs.context).exists()
    assert Path(imgs.person_crop).exists()
    assert imgs.person_crop.endswith("_person.jpg")


def test_event_persists_person_crop_path(store):
    eid = store.add_event(
        event_type="acesso_casa", track_id=7, zone="LINHA_ACESSO_CASA",
        snapshot_path="ctx.jpg", person_crop_path="person.jpg",
    )
    ev = store.event_by_id(eid)
    assert ev["snapshot_path"] == "ctx.jpg"
    assert ev["person_crop_path"] == "person.jpg"


def test_on_event_receives_event_images(store, tmp_path, make_track):
    engine = _engine(store, tmp_path)
    seen = []
    engine.on_event = lambda eid, etype, imgs: seen.append(imgs)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[100:400, 100:300] = 200
    st = _track_with_crop(make_track)

    engine._emit("alerta_suspeito", st, "ZONA_PORTAO", frame)

    assert len(seen) == 1
    assert isinstance(seen[0], EventImages)
    assert seen[0].person_crop and seen[0].person_crop.endswith("_person.jpg")
