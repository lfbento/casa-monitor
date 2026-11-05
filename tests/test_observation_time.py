from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np

from events.state_machine import EventEngine
from settings import RulesConfig
from zones.roi import Zones


def test_event_uses_observation_time_and_records_latency(store, tmp_path, make_track) -> None:
    engine = EventEngine(Zones.load(), RulesConfig(), store, snapshot_dir=tmp_path)
    st = make_track(track_id=7, cls_name="person")
    st.bboxes.append((0.0, 0.0, 100.0, 100.0))
    st.last_conf = 0.91
    observed = datetime.now().astimezone() - timedelta(seconds=30)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)

    engine.step(frame, [], now=0.0, observed_at=observed)
    event_id = engine._emit("passagem_pessoa", st, "ZONA_RUA", frame,
                            meta={"direcao": "direita"})

    event = store.event_by_id(event_id)
    assert event is not None
    assert event["observed_at"] == observed.isoformat(timespec="seconds")
    assert event["timestamp_start"] == event["observed_at"]
    assert event["detect_latency_ms"] >= 30_000

    meta = json.loads(event["meta"])
    assert meta["direcao"] == "direita"
    assert meta["evidencia"] == {
        "classe": "person",
        "area_px": 10_000,
        "conf": 0.91,
        "track": 7,
    }
