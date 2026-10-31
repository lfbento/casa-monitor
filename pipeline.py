"""Pipeline de monitoramento 24/7: captura → YOLO+ByteTrack → eventos → VLM.

Amostragem: DETECT_FPS do stream de 25 FPS (camada 1); a camada 2 processa
só geometria. VLM dispara por evento relevante, com limite de chamadas/hora.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from capture.decoder import Frame, H264StreamDecoder
from capture.ws_client import MilocoClient
from db.store import Store
from detect.yolo_engine import YoloEngine
from events.state_machine import EventEngine
from settings import DATA_DIR, DetectConfig, RulesConfig, ensure_dirs
from zones.roi import Zones

logger = logging.getLogger(__name__)

VLM_MAX_PER_HOUR = 40


class MonitorPipeline:
    def __init__(self, detect: DetectConfig | None = None, rules: RulesConfig | None = None):
        ensure_dirs()
        self.detect_cfg = detect or DetectConfig()
        self.rules = rules or RulesConfig()
        self.zones = Zones.load()
        self.store = Store()
        self.engine = EventEngine(self.zones, self.rules, self.store,
                                  snapshot_dir=DATA_DIR / "snapshots",
                                  on_event=self._on_event)
        self.yolo: YoloEngine | None = None
        self._vlm_queue: asyncio.Queue = asyncio.Queue()
        self._vlm_window: deque = deque()
        self._last_detect_ts = 0.0

    # ------------------------------------------------------------- VLM worker
    def _on_event(self, event_id: int, event_type: str, snapshot: Path) -> None:
        try:
            self._vlm_queue.put_nowait((event_id, event_type, snapshot))
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    async def _vlm_worker(self) -> None:
        from events.identity import crop_hist, match
        from notifier import send_photo
        from vlm.describe import describe_event

        # eventos que viram alerta imediato no Telegram (foto + análise)
        ALERT_TYPES = {"aproximacao_portao", "alerta_suspeito"}

        while True:
            event_id, event_type, snapshot = await self._vlm_queue.get()
            now = time.monotonic()
            self._vlm_window.append(now)
            while self._vlm_window and now - self._vlm_window[0] > 3600:
                self._vlm_window.popleft()
            if len(self._vlm_window) > VLM_MAX_PER_HOUR:
                logger.warning("limite de VLM/hora atingido — evento %d sem descrição", event_id)
                continue
            result = await asyncio.to_thread(describe_event, event_type, [snapshot])
            descricao = None
            if result:
                descricao = result.descricao
                self.store.set_vlm_description(event_id, result.descricao, result.risk_level)

            # identidade: parecia com alguém conhecido?
            hist = await asyncio.to_thread(crop_hist, snapshot)
            m = match(hist, self.store.known_people())
            quem = f"{m.name} (conhecido, {m.score:.0%})" if m else "pessoa desconhecida"
            if m:
                self.store.set_event_person_label(event_id, m.name)

            if event_type in ALERT_TYPES:
                ev = self.store.event_by_id(event_id) or {}
                titulo = ("🔔 Pessoa com interesse no portão" if event_type == "aproximacao_portao"
                          else "⚠️ Comportamento atípico")
                caption = (
                    f"{titulo} — evento #{event_id}\n"
                    f"Horário: {ev.get('timestamp_start', '?')} | Zona: {ev.get('zone', '?')}\n"
                    f"Quem: {quem}\n"
                    f"Análise: {descricao or '(sem VLM)'}\n\n"
                    f"Se você conhece, responda: é #{event_id} <nome> — para registrar."
                )
                await asyncio.to_thread(send_photo, str(snapshot), caption)
                logger.info("alerta enviado: evento #%d (%s)", event_id, quem)

    # ------------------------------------------------------------- manutenção
    def maintenance(self) -> None:
        """Rotação: snapshots > N dias e eventos > N dias."""
        import shutil

        days = self.rules.snapshot_retention_days
        snap_root = DATA_DIR / "snapshots"
        cutoff = time.time() - days * 86400
        if snap_root.exists():
            for day_dir in snap_root.iterdir():
                if day_dir.is_dir() and day_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(day_dir, ignore_errors=True)
        n = self.store.purge_older_than(days)
        logger.info("manutenção: eventos removidos=%d, snapshots > %ddias limpos", n, days)

    # ------------------------------------------------------------- laço
    async def run(self, duration_seconds: float | None = None) -> None:
        if self.yolo is None:
            self.yolo = YoloEngine(self.detect_cfg.model, conf=self.detect_cfg.conf)
        client = MilocoClient()
        await client.login()
        camera_id = await client.resolve_camera_id()
        decoder = H264StreamDecoder()
        vlm_task = asyncio.create_task(self._vlm_worker())
        interval = 1.0 / max(1, self.detect_cfg.fps)
        n_frames = n_processed = n_events = 0
        t0 = time.monotonic()
        logger.info("pipeline rodando (detecção a %d FPS) — Ctrl+C para parar", self.detect_cfg.fps)
        try:
            async for chunk in client.stream_h264(camera_id):
                for frame in decoder.feed(chunk):
                    n_frames += 1
                    now = time.monotonic()
                    if now - self._last_detect_ts < interval:
                        continue
                    self._last_detect_ts = now
                    if float(frame.image.std()) < 8.0:
                        continue  # frame cinza (warm-up) não entra no detector
                    dets = self.yolo.track(frame.image)
                    ids = self.engine.step(frame.image, dets, now)
                    n_processed += 1
                    n_events += len(ids)
                    if n_processed % 300 == 0:
                        logger.info("stats: frames=%d processados=%d eventos=%d tracks=%d",
                                    n_frames, n_processed, n_events, len(self.engine.book.tracks))
                    if duration_seconds and now - t0 >= duration_seconds:
                        raise asyncio.CancelledError("duração do teste atingida")
        except asyncio.CancelledError:
            if duration_seconds:
                logger.info("teste encerrado: frames=%d processados=%d eventos=%d",
                            n_frames, n_processed, n_events)
        finally:
            vlm_task.cancel()
            self.maintenance()
