from __future__ import annotations

from types import SimpleNamespace

import pytest

import pipeline as pl
from events.state_machine import EventImages


def _pipe(store):
    from settings import PipelineLimits

    p = pl.MonitorPipeline.__new__(pl.MonitorPipeline)
    p.store = store
    p.limits = PipelineLimits()
    p._alert_state = {}
    p._alert_last = {}
    p._last_alert_latency_ms = None
    p.ALERT_STATE_MAX = 4096
    p.ALERT_TYPES = {"aproximacao_portao", "alerta_suspeito", "acesso_casa"}
    return p


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_debounce_uses_last_successful_send(store, monkeypatch):
    p = _pipe(store)
    monkeypatch.setattr(pl, "ALERT_COOLDOWN", {"alerta_suspeito": 10})
    sent = []
    monkeypatch.setattr("notifier.send", lambda text: sent.append(text) or True)
    monkeypatch.setattr("notifier.send_photo", lambda path, cap: sent.append(cap) or True)
    clock = {"t": 0.0}
    monkeypatch.setattr(pl.time, "monotonic", lambda: clock["t"])
    imgs = EventImages(None, None)

    clock["t"] = 0.0
    _run(p._send_alert_now(1, "alerta_suspeito", imgs))   # envia
    clock["t"] = 5.0
    _run(p._send_alert_now(2, "alerta_suspeito", imgs))   # suprime
    clock["t"] = 9.0
    _run(p._send_alert_now(3, "alerta_suspeito", imgs))   # suprime
    clock["t"] = 11.0
    _run(p._send_alert_now(4, "alerta_suspeito", imgs))   # envia (cooldown do 1o)

    assert len(sent) == 2
    assert p._alert_last["alerta_suspeito"] == 11.0  # suprimidos não zeram o relógio


def test_alert_types_have_independent_cooldown(store, monkeypatch):
    p = _pipe(store)
    monkeypatch.setattr(pl, "ALERT_COOLDOWN", {"alerta_suspeito": 60, "aproximacao_portao": 60})
    sent = []
    monkeypatch.setattr("notifier.send", lambda text: sent.append(text) or True)
    clock = {"t": 0.0}
    monkeypatch.setattr(pl.time, "monotonic", lambda: clock["t"])
    imgs = EventImages(None, None)

    _run(p._send_alert_now(1, "alerta_suspeito", imgs))
    clock["t"] = 1.0
    _run(p._send_alert_now(2, "aproximacao_portao", imgs))

    assert len(sent) == 2


def test_failure_does_not_advance_cooldown(store, monkeypatch):
    p = _pipe(store)
    monkeypatch.setattr(pl, "ALERT_COOLDOWN", {"alerta_suspeito": 60})
    monkeypatch.setattr("notifier.send", lambda text: False)
    monkeypatch.setattr("notifier.send_photo", lambda path, cap: False)
    monkeypatch.setattr(pl.time, "monotonic", lambda: 0.0)
    imgs = EventImages(None, None)

    _run(p._send_alert_now(1, "alerta_suspeito", imgs))

    assert p._alert_state[1] == "failed"
    assert "alerta_suspeito" not in p._alert_last


def test_text_fallback_without_snapshot(store, monkeypatch):
    p = _pipe(store)
    sent = []
    photo = []
    monkeypatch.setattr("notifier.send", lambda text: sent.append(text) or True)
    monkeypatch.setattr("notifier.send_photo", lambda path, cap: photo.append(cap) or True)
    imgs = EventImages(None, None)

    _run(p._send_alert_now(1, "alerta_suspeito", imgs))

    assert sent and not photo


def test_emit_calls_on_event_even_without_snapshot(store, tmp_path, make_track, monkeypatch):
    from events.state_machine import EventEngine
    from settings import RulesConfig
    from zones.roi import Zones
    import numpy as np

    engine = EventEngine(Zones.load(), RulesConfig(), store, snapshot_dir=tmp_path)
    seen = []
    engine.on_event = lambda eid, etype, imgs: seen.append((etype, imgs))
    monkeypatch.setattr(engine, "_save_snapshot", lambda *a, **k: EventImages(None, None))
    st = make_track()
    st.history.append((0.0, 1.0, 1.0))
    st.bboxes.append((0.0, 0.0, 10.0, 10.0))

    engine._emit("alerta_suspeito", st, "ZONA_PORTAO", np.zeros((10, 10, 3), dtype="uint8"))

    assert seen and seen[0][0] == "alerta_suspeito"


def test_alert_caption_uses_observation_time_and_flags_delay(store, monkeypatch):
    from datetime import datetime, timedelta

    p = _pipe(store)
    observed = (datetime.now().astimezone() - timedelta(seconds=10)).isoformat(timespec="seconds")
    eid = store.add_event("alerta_suspeito", 1, "ZONA_PORTAO", observed_at=observed)
    sent = []
    monkeypatch.setattr("notifier.send", lambda text: sent.append(text) or True)
    monkeypatch.setattr("notifier.send_photo", lambda path, cap: sent.append(cap) or True)
    imgs = EventImages(None, None)

    _run(p._send_alert_now(eid, "alerta_suspeito", imgs))

    caption = sent[0]
    assert f"Observado: {observed}" in caption
    assert "Atraso:" in caption
    assert p._last_alert_latency_ms >= 9_000


def test_alert_is_not_blocked_by_analysis_executor(store, monkeypatch):
    import asyncio
    import threading
    import time as _time
    from concurrent.futures import ThreadPoolExecutor

    p = _pipe(store)
    p._analysis_executor = ThreadPoolExecutor(max_workers=1)
    release = threading.Event()
    p._analysis_executor.submit(release.wait, 5)
    sent = []
    monkeypatch.setattr("notifier.send", lambda text: sent.append(text) or True)
    imgs = EventImages(None, None)
    eid = store.add_event("alerta_suspeito", 1, "ZONA_PORTAO")

    started = _time.monotonic()
    try:
        asyncio.run(asyncio.wait_for(p._send_alert_now(eid, "alerta_suspeito", imgs), timeout=1.0))
        elapsed = _time.monotonic() - started
    finally:
        release.set()
        p._analysis_executor.shutdown(wait=False, cancel_futures=True)

    assert sent
    assert elapsed < 1.0
