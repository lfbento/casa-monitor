from __future__ import annotations

import asyncio
import time
from collections import deque
from types import SimpleNamespace

import numpy as np
import cv2

import pipeline as pl
from capture import osd
from capture.decoder import Frame
from settings import DetectConfig, PipelineLimits
from zones.roi import Zones


def _frame(seq: int = 1, arrival_mono: float | None = None) -> Frame:
    return Frame(
        image=np.zeros((90, 160, 3), dtype=np.uint8),
        pts=float(seq),
        seq=seq,
        arrival_mono=time.monotonic() if arrival_mono is None else arrival_mono,
        arrival_wall=time.time(),
    )


def _bare_pipeline() -> pl.MonitorPipeline:
    p = pl.MonitorPipeline.__new__(pl.MonitorPipeline)
    p.limits = PipelineLimits()
    p.detect_cfg = DetectConfig()
    p.zones = Zones.load()
    p.engine = SimpleNamespace(zones=p.zones, step=lambda *a, **k: [],
                               book=SimpleNamespace(tracks={}),
                               reset=lambda: setattr(
                                   p.engine, "book", SimpleNamespace(tracks={})))
    p._latest = None
    p._prev_small = None
    p._motion_mask = None
    p._frames_seen = 0
    p._frames_dropped = 0
    p._frames_overwritten = 0
    p._max_frame_age_ms = 0
    p._n_processed = 0
    p._n_events = 0
    p._analysis_dropped = 0
    p._analysis_deduped = 0
    p._last_alert_latency_ms = None
    p.osd = osd.OsdClock(30.0)
    p._last_capture_age_s = None
    p._max_capture_age_s = None
    p._last_captured_at = None
    p._capture_unknown = 0
    p._resync_requested = False
    p._resync_before = None
    p._last_resync_attempt = 0.0
    p._last_container_restart = 0.0
    p._resync_check_deadline = None
    p._resync_min_age = None
    p._cycle_wait_ms = deque(maxlen=10)
    p._cycle_infer_ms = deque(maxlen=10)
    p._cycle_step_ms = deque(maxlen=10)
    p._reader_busy_s = 0.0
    p._reader_frames = 0
    p._cadence_fps = None
    p._cadence_ts = None
    p._cadence_n = 0
    p._last_inference_ts = 0.0
    p._frames_no_motion = 0
    p._sanity_detections = 0
    p._motion_frames = 0
    p._win_motion_n = 0
    p._vlm_queue = asyncio.Queue(maxsize=8)
    p._vlm_prio = asyncio.Queue(maxsize=2)
    p._vlm_task = None
    p._last_detect_ts = 0.0
    p._cur_gray = None
    p._last_analyzed_gray = None
    p.VLM_SCENE_GATE = 0.025
    return p


class _FakeClient:
    def __init__(self, chunks: int) -> None:
        self.chunks = chunks

    async def stream_h264(self, camera_id: str):
        for _ in range(self.chunks):
            yield b"chunk"


class _FakeDecoder:
    def __init__(self) -> None:
        self.seq = 0

    def feed(self, chunk: bytes):
        self.seq += 1
        return [_frame(self.seq)]


class _SlowYolo:
    def track(self, image):
        time.sleep(0.2)
        return []


def test_processor_takes_latest_frame() -> None:
    p = _bare_pipeline()
    first = pl.FrameEnvelope(_frame(1), None, False, 0)
    newer = pl.FrameEnvelope(_frame(2), None, False, 0)
    newest = pl.FrameEnvelope(_frame(3), None, False, 0)

    p._latest = first
    assert p._take_frame(time.monotonic()) is first

    p._latest = newer
    p._latest = newest  # leitor sobrescreve antes do processador olhar
    taken = p._take_frame(time.monotonic())
    assert taken is newest


def test_stale_frame_is_dropped_and_counted() -> None:
    p = _bare_pipeline()
    p.limits = PipelineLimits(max_frame_age_s=2.0)
    p._latest = pl.FrameEnvelope(_frame(1, arrival_mono=time.monotonic() - 5), None, False, 0)

    assert p._take_frame(time.monotonic()) is None
    assert p._frames_dropped == 1
    assert p._max_frame_age_ms >= 5_000


def test_reader_keeps_draining_during_slow_inference() -> None:
    p = _bare_pipeline()
    p.yolo = _SlowYolo()
    p._motion_mask = p._build_motion_mask()
    client = _FakeClient(chunks=10)
    decoder = _FakeDecoder()

    async def scenario() -> None:
        processor = asyncio.create_task(p._process_frames(None, time.monotonic()))
        reader = asyncio.create_task(p._read_frames(client, "cam", decoder))
        # se a leitura esperasse a inferência, 10 chunks levariam ~2 s
        await asyncio.wait_for(reader, timeout=1.0)
        processor.cancel()
        try:
            await processor
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())

    assert p._frames_seen == 10
    assert p._latest is not None
    assert p._frames_overwritten >= 1


def test_motion_ignores_tiny_blobs_but_not_objects() -> None:
    p = _bare_pipeline()
    p._motion_mask = None
    base = np.zeros((180, 320), dtype=np.uint8)

    p._prev_small = base.copy()
    wind = base.copy()
    wind[10, 10] = 255  # gota/ruído minúsculo
    motion, changed = p._motion(wind)
    assert (motion, changed) == (False, 0)

    obj = base.copy()
    obj[40:70, 40:70] = 255  # bloco do tamanho de um objeto
    motion, changed = p._motion(obj)
    assert motion is True
    assert changed > 40


def test_watchdog_line_exposes_freshness_and_queues() -> None:
    p = _bare_pipeline()
    p._frames_seen = 120
    p._n_processed = 30
    p._frames_dropped = 4
    p._max_frame_age_ms = 180
    p._last_alert_latency_ms = 2100

    line = p._watchdog_line()

    assert "frames=120" in line
    assert "processados=30" in line
    assert "descartados_idade=4" in line
    assert "idade_max=180ms" in line
    assert "latência_alerta=2100ms" in line
    assert "análise=" in line
    assert "frescor=" in line
    assert "ocr_falhas=" in line
    assert "cadência=" in line and "(cfg " in line
    assert "ciclo ms p50/p95" in line
    assert "leitura=" in line


class _OsdDecoder:
    def __init__(self, image, arrival_wall: float) -> None:
        self.image = image
        self.arrival_wall = arrival_wall
        self.seq = 0

    def feed(self, chunk: bytes):
        self.seq += 1
        return [Frame(image=self.image, pts=0.0, seq=self.seq,
                      arrival_mono=time.monotonic(), arrival_wall=self.arrival_wall)]


def _osd_frame(name: str = "osd_0800.png"):
    from pathlib import Path

    gray = cv2.imread(str(Path(__file__).parent / "fixtures" / "osd" / name),
                      cv2.IMREAD_GRAYSCALE)
    assert gray is not None
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def test_reader_propagates_capture_time_from_osd() -> None:
    p = _bare_pipeline()
    p._motion_mask = None
    captured = pl.datetime(2026, 11, 5, 8, 37, 25).astimezone()
    decoder = _OsdDecoder(_osd_frame(), arrival_wall=captured.timestamp() + 7)

    asyncio.run(p._read_frames(_FakeClient(chunks=1), "cam", decoder))

    assert p._latest is not None
    assert p._latest.captured_at is not None
    assert int(p._latest.capture_age_s or 0) == 7
    assert p._latest.frame.captured_at == p._latest.captured_at


def test_reader_leaves_capture_time_unknown_when_osd_illegible() -> None:
    p = _bare_pipeline()
    p._motion_mask = None
    decoder = _OsdDecoder(_osd_frame("osd_bad.png"), arrival_wall=time.time())

    asyncio.run(p._read_frames(_FakeClient(chunks=1), "cam", decoder))

    assert p._latest is not None
    assert p._latest.captured_at is None
    assert p._latest.capture_age_s is None
    p._take_frame(time.monotonic())
    assert p._capture_unknown == 1


def test_resync_is_requested_above_limit_and_honors_cooldown() -> None:
    p = _bare_pipeline()
    p.limits = PipelineLimits(capture_age_resync_s=300.0)

    assert p._maybe_request_resync(10.0) is False
    assert p._resync_requested is False

    assert p._maybe_request_resync(400.0) is True
    assert p._resync_requested is True
    assert p._resync_before == 400.0

    p._resync_requested = False
    assert p._maybe_request_resync(500.0) is False  # cooldown de 300s


def test_prepare_resync_discards_pending_state() -> None:
    p = _bare_pipeline()
    p._latest = pl.FrameEnvelope(_frame(1), None, False, 0)
    p._last_capture_age_s = 900.0
    p._height = None
    p.engine.book = SimpleNamespace(tracks={11: object()})
    p._resync_before = 900.0

    p._prepare_resync()

    assert p._latest is None
    assert p._last_capture_age_s is None
    assert p._resync_before == 900.0  # preservado para o veredito pós-reconexão
    assert not p.engine.book.tracks


def test_resync_verdict_reports_recovered_blocked_and_unknown() -> None:
    p = _bare_pipeline()
    assert p._resync_verdict(3600.0, 3.0) == "recuperado"
    assert "bloqueado" in p._resync_verdict(3600.0, 3500.0)
    assert "bloqueado" in p._resync_verdict(3600.0, None)


def test_reader_requests_resync_and_emits_nothing_from_stale_interval() -> None:
    p = _bare_pipeline()
    p._motion_mask = None
    p.limits = PipelineLimits(capture_age_resync_s=60.0)
    captured = pl.datetime(2026, 11, 5, 8, 37, 25).astimezone()
    decoder = _OsdDecoder(_osd_frame(), arrival_wall=captured.timestamp() + 3600)

    asyncio.run(p._read_frames(_FakeClient(chunks=1), "cam", decoder))

    assert p._resync_requested is True
    assert p._resync_before and p._resync_before > 3000
    assert p._latest is None


def test_motion_gate_skips_stills_and_keeps_sanity_check() -> None:
    p = _bare_pipeline()
    p.limits = PipelineLimits(motion_sanity_interval_s=10.0)
    still = pl.FrameEnvelope(_frame(1), None, False, 0)
    moving = pl.FrameEnvelope(_frame(2), None, True, 500)

    p._last_inference_ts = 0.0
    assert p._detect_decision(still, now=1.0) == (False, False)
    assert p._frames_no_motion == 1

    # passou o intervalo de sanidade: uma detecção para provar que o gate vive
    assert p._detect_decision(still, now=11.0) == (True, True)
    assert p._sanity_detections == 1

    assert p._detect_decision(moving, now=12.0) == (True, False)
    assert p._motion_frames == 1


def test_auto_restart_disabled_and_cooldown() -> None:
    p = _bare_pipeline()
    p.limits = PipelineLimits(miloco_auto_restart=False)

    assert p._should_restart_container(now=10_000.0) is False

    p.limits = PipelineLimits(miloco_auto_restart=True, miloco_restart_cooldown_s=1800.0)
    p._last_container_restart = 0.0
    p._resync_check_deadline = None
    p._resync_min_age = None
    assert p._should_restart_container(now=1800.0) is True
    p._last_container_restart = 1000.0
    assert p._should_restart_container(now=2000.0) is False


def test_restart_command_uses_configured_container() -> None:
    p = _bare_pipeline()
    p.limits = PipelineLimits(miloco_container="miloco-test")

    cmd = p._restart_command()
    assert "miloco-test" in cmd[-1]
    assert cmd[:3] == ["sg", "docker", "-c"]


def test_attempt_container_restart_runs_once_and_clears_verdict(monkeypatch) -> None:
    p = _bare_pipeline()
    p.limits = PipelineLimits(miloco_auto_restart=True, miloco_restart_cooldown_s=1800.0)
    p._resync_before = 500.0
    calls = []

    class _Proc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Proc()

    monkeypatch.setattr(pl.subprocess, "run", fake_run)

    assert asyncio.run(p._attempt_container_restart()) is True
    assert calls and "docker restart miloco" in calls[0][-1]
    assert p._resync_before is None
    assert asyncio.run(p._attempt_container_restart()) is False
    assert len(calls) == 1


def test_attempt_container_restart_reports_failure(monkeypatch) -> None:
    p = _bare_pipeline()

    class _Proc:
        returncode = 1
        stderr = "permission denied"

    monkeypatch.setattr(pl.subprocess, "run", lambda cmd, **kw: _Proc())

    assert asyncio.run(p._attempt_container_restart()) is False


def test_resync_settle_window_waits_and_judges_minimum() -> None:
    p = _bare_pipeline()
    p.limits = PipelineLimits(capture_age_resync_settle_s=60.0, capture_age_warn_s=30.0)
    p._resync_before = 480.0

    assert p._note_resync_age(480.0, now=0.0) is None       # abre a janela
    assert p._note_resync_age(400.0, now=30.0) is None      # ainda dentro da janela
    result = p._note_resync_age(2.0, now=61.0)               # estabilizou
    assert result is not None
    verdict, before, best = result
    assert verdict == "recuperado" and before == 480.0 and best == 2.0
    assert p._resync_before is None


def test_resync_settle_window_judges_blocked_when_never_fresh() -> None:
    p = _bare_pipeline()
    p.limits = PipelineLimits(capture_age_resync_settle_s=60.0, capture_age_warn_s=30.0)
    p._resync_before = 480.0

    p._note_resync_age(480.0, now=0.0)
    result = p._note_resync_age(470.0, now=61.0)
    assert result is not None
    assert result[0] != "recuperado"
