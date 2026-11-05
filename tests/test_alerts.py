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
    assert f"Recebido: {observed}" in caption
    assert "Processamento:" in caption and "acima do limite" in caption
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


def test_caption_reports_capture_age_and_stale_marker(store):
    from datetime import datetime, timedelta

    p = _pipe(store)
    now = datetime.now().astimezone()
    eid = store.add_event(
        event_type="acesso_casa", track_id=1, zone="LINHA_ACESSO_CASA",
        observed_at=now.isoformat(timespec="seconds"),
        captured_at=(now - timedelta(hours=5)).isoformat(timespec="seconds"),
        capture_age_ms=5 * 3600 * 1000,
        meta='{"direcao": "ENTRY"}',
    )
    ev = store.event_by_id(eid)

    caption = p._alert_caption(eid, "acesso_casa", ev, "pessoa desconhecida")

    assert "⚠️ Conteúdo atrasado" in caption
    assert "Capturado: " in caption and "(5h00m atrás)" in caption
    assert "Recebido: " in caption
    assert "Processamento: " in caption


def test_caption_without_capture_age_says_unknown(store):
    from datetime import datetime

    p = _pipe(store)
    now = datetime.now().astimezone()
    eid = store.add_event(
        event_type="alerta_suspeito", track_id=2, zone="ZONA_PORTAO",
        observed_at=now.isoformat(timespec="seconds"),
    )
    ev = store.event_by_id(eid)

    caption = p._alert_caption(eid, "alerta_suspeito", ev, "pessoa desconhecida")

    assert "Capturado: desconhecido" in caption
    assert "⚠️ Conteúdo atrasado" not in caption


def test_caption_within_limit_has_no_stale_marker(store):
    from datetime import datetime, timedelta

    p = _pipe(store)
    now = datetime.now().astimezone()
    eid = store.add_event(
        event_type="aproximacao_portao", track_id=3, zone="ZONA_PORTAO",
        observed_at=now.isoformat(timespec="seconds"),
        captured_at=(now - timedelta(seconds=10)).isoformat(timespec="seconds"),
        capture_age_ms=10_000,
    )
    ev = store.event_by_id(eid)

    caption = p._alert_caption(eid, "aproximacao_portao", ev, "pessoa desconhecida")

    assert "⚠️ Conteúdo atrasado" not in caption
    assert "(10s atrás)" in caption
