from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from events.state_machine import EventEngine
from settings import RulesConfig
from zones.roi import Zones


def _engine(store, tmp_path):
    return EventEngine(Zones.load(), RulesConfig(), store, snapshot_dir=tmp_path / "snapshots")


def _person(make_track, track_id, vec, now=0.0):
    st = make_track(track_id=track_id, cls_name="person", now=now)
    st.history.append((now, 100.0, 100.0))
    st.bboxes.append((80.0, 80.0, 120.0, 200.0))
    v = np.asarray(vec, dtype=np.float32)
    st.appearance = v / np.linalg.norm(v)
    return st


def _iso(hours_ago: float = 0.0) -> str:
    return (datetime.now().astimezone() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def test_track_switch_dedupes_session(store, tmp_path, make_track):
    engine = _engine(store, tmp_path)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    same = [1.0, 0.0]

    engine._house_crossing(frame, _person(make_track, 1, same), "ENTRY")
    engine._house_crossing(frame, _person(make_track, 2, same), "ENTRY")  # troca de track

    assert len(store.open_sessions()) == 1


def test_exit_closes_compatible_session_not_oldest(store, tmp_path, make_track):
    engine = _engine(store, tmp_path)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    a = _person(make_track, 1, [1.0, 0.0])
    b = _person(make_track, 2, [0.0, 1.0])
    engine._house_crossing(frame, a, "ENTRY")
    engine._house_crossing(frame, b, "ENTRY")

    # B sai primeiro
    engine._house_crossing(frame, _person(make_track, 2, [0.0, 1.0]), "EXIT")

    open_after = store.open_sessions()
    assert len(open_after) == 1
    assert open_after[0]["entry_track_id"] == 1  # A permanece, não é FIFO


def test_exit_without_match_preserves_sessions(store, tmp_path, make_track):
    engine = _engine(store, tmp_path)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    engine._house_crossing(frame, _person(make_track, 1, [1.0, 0.0]), "ENTRY")
    ids = engine._house_crossing(frame, _person(make_track, 2, [0.0, 1.0]), "EXIT")

    assert len(store.open_sessions()) == 1  # ninguém foi fechado
    import json
    ev = store.event_by_id(ids[-1])
    assert json.loads(ev["meta"])["session_id"] is None


def test_old_open_session_becomes_stale(store):
    store.open_session("pessoa", _iso(25), None, entry_track_id=1,
                       last_seen_time=_iso(25), status="INSIDE")
    sessions = store.open_sessions()
    assert sessions[0]["status"] == "STALE"


def test_session_overlaps_report_window(store):
    entry = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(timespec="seconds")
    exit_ = (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds")
    sid = store.open_session("pessoa", entry, None)
    store.close_session(sid, exit_, None)
    window_from = (datetime.now().astimezone() - timedelta(hours=3)).isoformat(timespec="seconds")
    window_to = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = store.sessions_between(window_from, window_to)
    assert any(r["id"] == sid for r in rows)


def test_historical_open_sessions_uses_window_end(store):
    entry = (datetime.now().astimezone() - timedelta(hours=5)).isoformat(timespec="seconds")
    exit_ = (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds")
    sid = store.open_session("pessoa", entry, None)
    store.close_session(sid, exit_, None)

    mid = (datetime.now().astimezone() - timedelta(hours=3)).isoformat(timespec="seconds")
    past_ids = [s["id"] for s in store.open_sessions(ts_to=mid)]
    assert sid in past_ids  # ainda estava dentro no meio da janela

    now_ids = [s["id"] for s in store.open_sessions()]
    assert sid not in now_ids  # já saiu
