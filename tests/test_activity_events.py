from __future__ import annotations

import numpy as np

from db.store import Store
from events.state_machine import EventEngine
from settings import RulesConfig
from zones.roi import Zones


def _engine(store, tmp_path):
    return EventEngine(Zones.load(), RulesConfig(), store, snapshot_dir=tmp_path / "snapshots")


def _person_track(make_track, now=0.0):
    st = make_track(track_id=1, cls_name="person", now=now)
    st.history.append((now, 100.0, 100.0))
    st.bboxes.append((80.0, 80.0, 120.0, 200.0))
    return st


def _count(store: Store, event_type: str) -> int:
    return store._conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type=?", (event_type,)
    ).fetchone()[0]


def test_gate_dwell_is_contiguous_and_resets_after_leaving(store, tmp_path, make_track):
    engine = _engine(store, tmp_path)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    zone = {"value": "ZONA_PORTAO"}
    engine.zones.zone_at = lambda cx, cy: zone["value"]
    st = _person_track(make_track)

    engine._process(frame, st, 0.0)   # inicia episódio
    engine._process(frame, st, 3.0)   # dwell 3s < 5s
    assert _count(store, "aproximacao_portao") == 0

    engine._process(frame, st, 6.0)   # dwell 6s >= 5s
    assert _count(store, "aproximacao_portao") == 1
    engine._process(frame, st, 7.0)   # não duplica no mesmo episódio
    assert _count(store, "aproximacao_portao") == 1

    zone["value"] = "ZONA_RUA"
    engine._process(frame, st, 8.0)   # sai do portão → reinicia
    zone["value"] = "ZONA_PORTAO"
    engine._process(frame, st, 20.0)  # retorna: NÃO soma os períodos
    assert _count(store, "aproximacao_portao") == 1
    engine._process(frame, st, 21.0)  # dwell novo 1s < 5s
    assert _count(store, "aproximacao_portao") == 1
    engine._process(frame, st, 26.0)  # dwell novo 6s → novo evento
    assert _count(store, "aproximacao_portao") == 2


def test_habitual_parked_vehicle_does_not_trigger_loitering(store, tmp_path, make_track):
    engine = _engine(store, tmp_path)
    engine.zones.zone_at = lambda cx, cy: "ZONA_RUA"
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    vehicle = make_track(track_id=99, cls_name="car", now=0.0)
    vehicle.history.append((0.0, 110.0, 120.0))
    vehicle.bboxes.append((80.0, 80.0, 140.0, 160.0))
    vehicle.stopped_since = 0.0          # parado desde o início
    vehicle.total_path_px = 0.0          # sem movimento prévio
    engine.book.tracks[99] = vehicle

    person = _person_track(make_track)
    for t in (0.0, 10.0, 20.0, 31.0, 40.0):
        engine._process(frame, person, t)

    assert _count(store, "alerta_suspeito") == 0


def test_recently_stopped_vehicle_triggers_loitering(store, tmp_path, make_track):
    engine = _engine(store, tmp_path)
    engine.zones.zone_at = lambda cx, cy: "ZONA_RUA"
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    vehicle = make_track(track_id=99, cls_name="car", now=0.0)
    vehicle.history.append((0.0, 110.0, 120.0))
    vehicle.bboxes.append((80.0, 80.0, 140.0, 160.0))
    vehicle.stopped_since = 1.0          # parou há pouco
    vehicle.total_path_px = 100.0        # teve movimento prévio
    engine.book.tracks[99] = vehicle

    person = _person_track(make_track)
    for t in (2.0, 12.0, 22.0, 33.0):
        engine._process(frame, person, t)

    assert _count(store, "alerta_suspeito") == 1
