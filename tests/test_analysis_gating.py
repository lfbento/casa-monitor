from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import pipeline as pl
from events.identity import match as real_match
from events.state_machine import EventImages
from settings import PipelineLimits


def _pipe(store) -> pl.MonitorPipeline:
    p = pl.MonitorPipeline.__new__(pl.MonitorPipeline)
    p.store = store
    p.limits = PipelineLimits()
    p.ALERT_TYPES = {"aproximacao_portao", "alerta_suspeito", "acesso_casa"}
    p._vlm_queue = asyncio.Queue(maxsize=p.limits.analysis_queue_max)
    p._vlm_prio = asyncio.Queue(maxsize=2)
    p._analysis_seen = {}
    p._analysis_dropped = 0
    p._analysis_deduped = 0
    p._vlm_window = deque()
    p._alert_state = {}
    p._analysis_executor = ThreadPoolExecutor(max_workers=1)
    p._cur_gray = None
    p._last_analyzed_gray = None
    p.VLM_SCENE_GATE = 0.025
    return p


def _event(store, event_type="passagem_pessoa", classe="person",
           area=10000, conf=0.9, track=1) -> int:
    meta = json.dumps({"evidencia": {"classe": classe, "area_px": area,
                                     "conf": conf, "track": track}})
    return store.add_event(event_type, track, "ZONA_RUA", meta=meta,
                           snapshot_path="/tmp/foto.jpg")


def test_wind_without_object_evidence_is_not_queued(store) -> None:
    p = _pipe(store)
    event_id = store.add_event("passagem_pessoa", 1, "ZONA_RUA",
                               meta=json.dumps({"direcao": "indireita"}),
                               snapshot_path="/tmp/foto.jpg")

    p._on_event(event_id, "passagem_pessoa", EventImages("/tmp/foto.jpg", None))

    assert p._vlm_queue.qsize() == 0


def test_person_with_evidence_is_queued(store) -> None:
    p = _pipe(store)
    event_id = _event(store)

    p._on_event(event_id, "passagem_pessoa", EventImages("/tmp/foto.jpg", None))

    assert p._vlm_queue.qsize() == 1
    assert p._vlm_queue.get_nowait()[0] == event_id


def test_detection_below_minimum_is_not_queued(store) -> None:
    p = _pipe(store)
    small = _event(store, area=100, track=2)
    weak = _event(store, conf=0.1, track=3)

    p._on_event(small, "passagem_pessoa", EventImages("/tmp/foto.jpg", None))
    p._on_event(weak, "passagem_pessoa", EventImages("/tmp/foto.jpg", None))

    assert p._vlm_queue.qsize() == 0


def test_same_track_is_deduplicated(store) -> None:
    p = _pipe(store)
    first = _event(store, track=9)
    second = _event(store, track=9)

    p._on_event(first, "passagem_pessoa", EventImages("/tmp/foto.jpg", None))
    p._on_event(second, "passagem_pessoa", EventImages("/tmp/foto.jpg", None))

    assert p._vlm_queue.qsize() == 1
    assert p._analysis_deduped == 1


def test_full_queue_drops_oldest(store) -> None:
    p = _pipe(store)
    p._vlm_queue = asyncio.Queue(maxsize=1)

    p._enqueue_analysis(p._vlm_queue, ("a",))
    p._enqueue_analysis(p._vlm_queue, ("b",))

    assert p._vlm_queue.qsize() == 1
    assert p._vlm_queue.get_nowait() == ("b",)
    assert p._analysis_dropped == 1


def test_stale_queue_item_skips_provider(store) -> None:
    p = _pipe(store)
    event_id = _event(store)
    enqueued = time.monotonic() - p.limits.analysis_max_age_s - 5
    p._vlm_queue.put_nowait((event_id, "passagem_pessoa", EventImages(None, None), enqueued))
    called = []

    p_describe = lambda *a, **k: called.append(1)
    try:
        asyncio.run(p._vlm_worker_once(lambda path: None, real_match, lambda *a: True, p_describe))
    finally:
        p._analysis_executor.shutdown(wait=False, cancel_futures=True)

    assert called == []
    assert p._analysis_dropped == 1


def test_analysis_failure_keeps_worker_and_sends_fallback_alert(store, monkeypatch) -> None:
    p = _pipe(store)
    event_id = _event(store, event_type="aproximacao_portao", track=5)
    p._vlm_queue.put_nowait(
        (event_id, "aproximacao_portao", EventImages(None, None), time.monotonic()))
    sent = []
    monkeypatch.setattr("notifier.send", lambda text: sent.append(text) or True)

    def boom(*args, **kwargs):
        raise RuntimeError("provedor caiu")

    try:
        asyncio.run(p._vlm_worker_once(lambda path: None, real_match, lambda *a: True, boom))
    finally:
        p._analysis_executor.shutdown(wait=False, cancel_futures=True)

    assert sent, "o alerta de retaguarda deveria sair mesmo com a análise falhando"
