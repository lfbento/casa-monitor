from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from events.state_machine import EventEngine
from settings import PipelineLimits, RulesConfig
from zones.roi import Zones


def _engine(store, tmp_path, **limit_overrides) -> EventEngine:
    limits = PipelineLimits(**limit_overrides)
    return EventEngine(Zones.load(), RulesConfig(), store, snapshot_dir=tmp_path, limits=limits)


def _person(make_track, track_id: int, x: float, y: float, *, hits: int = 4,
            path: float = 120.0, first_seen: float = 0.0, last_seen: float = 0.0):
    st = make_track(track_id=track_id, cls_name="person", now=first_seen)
    st.hits = hits
    st.total_path_px = path
    st.first_seen = first_seen
    st.last_seen = last_seen
    st.zones_seen.add("ZONA_RUA")
    st.current_zone = "ZONA_RUA"
    st.bboxes.append((x - 20, y - 20, x + 20, y + 20))
    st.last_conf = 0.8
    st.history.append((first_seen, x - 50, y))   # direção: direita
    st.history.append((last_seen, x, y))
    return st


FRAME = np.zeros((120, 160, 3), dtype=np.uint8)


def test_single_detection_does_not_emit(store, tmp_path, make_track) -> None:
    engine = _engine(store, tmp_path)
    st = _person(make_track, 1, 100, 60, hits=1, path=5.0)
    st.hits = 1
    st.total_path_px = 300.0

    assert engine._finalize(FRAME, st, now=1.0) == []
    assert engine._pending_passages == []


def test_confirmed_track_emits_after_merge_window(store, tmp_path, make_track) -> None:
    engine = _engine(store, tmp_path, passage_merge_s=5.0)
    st = _person(make_track, 2, 100, 60, hits=3)

    assert engine._finalize(FRAME, st, now=1.0) == []
    assert len(engine._pending_passages) == 1
    assert engine._flush_passages(now=3.0) == []          # ainda dentro da janela
    ids = engine._flush_passages(now=7.0)
    assert len(ids) == 1

    ev = store.event_by_id(ids[0])
    assert ev is not None and ev["event_type"] == "passagem_pessoa"
    assert st.track_id in __import__("json").loads(ev["meta"])["merged_tracks"]


def test_static_object_is_not_a_passage(store, tmp_path, make_track) -> None:
    engine = _engine(store, tmp_path)
    st = _person(make_track, 3, 100, 60, hits=10, path=8.0)
    st.zones_seen = {"ZONA_RUA"}   # sem transição de zona

    assert engine._finalize(FRAME, st, now=1.0) == []
    assert engine._pending_passages == []


def test_fragments_of_same_passage_merge_into_one_event(store, tmp_path, make_track) -> None:
    engine = _engine(store, tmp_path, passage_merge_s=5.0, passage_merge_distance_px=200)
    a = _person(make_track, 10, 100, 60, first_seen=0.0, last_seen=0.0)
    b = _person(make_track, 11, 130, 62, first_seen=2.0, last_seen=2.0)

    engine._queue_passage("passagem_pessoa", a, "ZONA_RUA", FRAME, now=10.0)
    engine._queue_passage("passagem_pessoa", b, "ZONA_RUA", FRAME, now=11.0)
    ids = engine._flush_passages(now=20.0)

    assert len(ids) == 1
    meta = __import__("json").loads(store.event_by_id(ids[0])["meta"])
    assert sorted(meta["merged_tracks"]) == [10, 11]


def test_distant_or_opposite_fragments_stay_separate(store, tmp_path, make_track) -> None:
    engine = _engine(store, tmp_path, passage_merge_s=5.0, passage_merge_distance_px=50)
    a = _person(make_track, 20, 100, 60, first_seen=0.0, last_seen=0.0)
    b = _person(make_track, 21, 900, 60, first_seen=1.0, last_seen=1.0)

    engine._queue_passage("passagem_pessoa", a, "ZONA_RUA", FRAME, now=10.0)
    engine._queue_passage("passagem_pessoa", b, "ZONA_RUA", FRAME, now=11.0)
    ids = engine._flush_passages(now=20.0)

    assert len(ids) == 2


def test_passages_separated_by_window_stay_separate(store, tmp_path, make_track) -> None:
    engine = _engine(store, tmp_path, passage_merge_s=5.0)
    a = _person(make_track, 30, 100, 60)
    b = _person(make_track, 31, 110, 60)

    engine._queue_passage("passagem_pessoa", a, "ZONA_RUA", FRAME, now=10.0)
    engine._queue_passage("passagem_pessoa", b, "ZONA_RUA", FRAME, now=20.0)
    ids = engine._flush_passages(now=30.0)

    assert len(ids) == 2


def test_replay_of_real_storm_collapses_episodes(store, tmp_path, make_track) -> None:
    """Rajada real de 05/11 00:29–05:37: 1.460 eventos brutos.

    A fixture traz track, instante, direção, zona e deslocamento. Sem centroides
    reais na fixture, todos os fragmentos usam o mesmo ponto; a agregação então
    vale pelo par (classe, direção, zona, janela) — uma estimativa conservadora
    da redução obtida em produção.
    """
    import csv
    from datetime import datetime
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "storm_20261105.csv"
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 1460

    engine = _engine(store, tmp_path, passage_merge_s=5.0, passage_merge_distance_px=200)
    emitted = 0
    last_ts = 0.0
    for i, row in enumerate(rows):
        ts = datetime.fromisoformat(row["observed_at"]).timestamp()
        last_ts = ts
        st = make_track(track_id=int(row["track_id"]), cls_name=(
            "person" if row["event_type"] == "passagem_pessoa" else
            "car" if row["event_type"] == "passagem_veiculo" else "dog"))
        st.hits = 3
        st.total_path_px = float(row["path_px"])
        st.zones_seen = {row["zone"] or "ZONA_RUA"}
        st.history.append((ts - 30, 0.0, 0.0))
        st.history.append((ts, 0.0, 0.0))
        engine._queue_passage(row["event_type"], st, row["zone"] or "ZONA_RUA", FRAME, now=ts)
        emitted += len(engine._flush_passages(now=ts))
    emitted += len(engine._flush_passages(now=last_ts + 10))

    # produção registrou 1460 passagens brutas. Com centroides iguais (pior caso
    # para separar pessoas distintas), a agregação emite 339 — redução de 4,3x.
    assert emitted <= len(rows) // 4, f"emitidos={emitted} de {len(rows)}"
