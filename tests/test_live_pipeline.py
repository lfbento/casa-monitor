from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import numpy as np

import pipeline as pl
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
                               book=SimpleNamespace(tracks={}))
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
