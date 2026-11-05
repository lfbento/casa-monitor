"""Pipeline de monitoramento 24/7: captura → YOLO+ByteTrack → eventos → VLM.

Amostragem: DETECT_FPS do stream de 25 FPS (camada 1); a camada 2 processa
só geometria. VLM dispara por evento relevante, com limite de chamadas/hora.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import NamedTuple, Optional

from capture.decoder import Frame, H264StreamDecoder
from capture.ws_client import MilocoClient
from db.store import Store
from detect.yolo_engine import ANIMAL_CLASSES, VEHICLE_CLASSES, YoloEngine
from events.state_machine import EventEngine
from settings import (DATA_DIR, DetectConfig, PipelineLimits, RulesConfig,
                      ensure_dirs)
from zones.roi import Zones

logger = logging.getLogger(__name__)

VLM_MAX_PER_HOUR = 40
ALERT_COOLDOWN = {"acesso_casa": 30, "aproximacao_portao": 60, "alerta_suspeito": 120}
# classes que justificam análise semântica (vento/vegetação não têm classe)
ANALYSIS_CLASSES = {"person"} | VEHICLE_CLASSES | ANIMAL_CLASSES


class FrameEnvelope(NamedTuple):
    """Frame mais recente + o que o leitor já calculou sobre ele."""
    frame: Frame
    gray: object
    motion: bool
    changed: int


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
        self.limits = PipelineLimits()
        self._vlm_queue: asyncio.Queue = asyncio.Queue(maxsize=self.limits.analysis_queue_max)
        self._vlm_prio: asyncio.Queue = asyncio.Queue(
            maxsize=max(2, min(4, self.limits.analysis_queue_max)))  # alertas na frente
        self._vlm_window: deque = deque()
        self._analysis_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vlm")
        self._analysis_seen: dict[tuple, float] = {}
        self._last_detect_ts = 0.0
        # eventos que viram alerta IMEDIATO (foto + quem) — nunca esperam a
        # fila de VLM (alerta de segurança atrasado é alerta inútil)
        self.ALERT_TYPES = {"aproximacao_portao", "alerta_suspeito", "acesso_casa"}
        # estado de entrega por evento: pending | sent | suppressed | failed
        self._alert_state: dict[int, str] = {}
        self.ALERT_STATE_MAX = 4096
        # gate de variação de CENA (frame completo) para a fila de análise:
        # pula eventos cujo cenário não mudou >2.5% desde a última análise
        # (medido: ruído de fundo ~1.5%; reduz a fila em ~50-57%)
        self._cur_gray = None
        self._last_analyzed_gray = None
        self.VLM_SCENE_GATE = 0.025
        # debounce de alerta: rajadas do mesmo tipo (rastro piscando na linha)
        # viram UM alerta — spam satura o Telegram e atrasa a entrega de todos
        self._alert_last: dict = {}
        self._vlm_task = None
        # ---- frescor e métricas do pipeline ----
        self._latest: Optional[FrameEnvelope] = None
        self._prev_small = None
        self._motion_mask = None
        self._frames_seen = 0
        self._frames_dropped = 0       # descartados por idade
        self._frames_overwritten = 0   # substituídos no slot antes de processar
        self._max_frame_age_ms = 0
        self._n_processed = 0
        self._n_events = 0
        self._analysis_dropped = 0
        self._analysis_deduped = 0
        self._last_alert_latency_ms: Optional[int] = None

    # ------------------------------------------------------------- VLM worker
    def _on_event(self, event_id: int, event_type: str, imgs) -> None:
        # ALERTA INSTANTÂNEO primeiro: nunca depende de fila, análise ou cena.
        if event_type in self.ALERT_TYPES:
            try:
                asyncio.get_running_loop().create_task(
                    self._send_alert_now(event_id, event_type, imgs))
            except RuntimeError:
                pass

        # A fila de análise NÃO recebe passagens rotineiras de veículo (400+/dia
        # entupiam a fila e consumiam o orçamento de VLM).
        if event_type == "passagem_veiculo":
            return
        if not (imgs.context or imgs.person_crop):
            return
        if not self._has_object_evidence(event_id):
            return

        ev = self.store.event_by_id(event_id) or {}
        key = (ev.get("track_id"), event_type)
        now = time.monotonic()
        self._prune_analysis_seen(now)
        previous = self._analysis_seen.get(key)
        if previous is not None and now - previous < self.limits.analysis_dedup_s:
            self._analysis_deduped += 1
            logger.debug("evento #%d pulado (mesmo track/episódio)", event_id)
            return
        self._analysis_seen[key] = now

        # gate de cena: filtro ADICIONAL de redundância; nunca o único critério
        if event_type not in self.ALERT_TYPES and self._last_analyzed_gray is not None:
            import cv2
            import numpy as np
            try:
                frac = float(np.mean(cv2.absdiff(
                    self._cur_gray, self._last_analyzed_gray) > 20)) \
                    if self._cur_gray is not None else 1.0
                if frac < self.VLM_SCENE_GATE:
                    logger.debug("evento #%d pulado (cena %.1f%% mudou)", event_id, frac * 100)
                    return
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        if self._cur_gray is not None:
            self._last_analyzed_gray = self._cur_gray.copy()

        q = self._vlm_prio if event_type in self.ALERT_TYPES else self._vlm_queue
        self._enqueue_analysis(q, (event_id, event_type, imgs, now))

    def _has_object_evidence(self, event_id: int) -> bool:
        """Só analisa eventos com pessoa/animal/veículo real acima dos mínimos."""
        ev = self.store.event_by_id(event_id) or {}
        try:
            meta = json.loads(ev.get("meta") or "{}")
        except (TypeError, ValueError):
            return False
        evidence = meta.get("evidencia") or {}
        if evidence.get("classe") not in ANALYSIS_CLASSES:
            return False
        if int(evidence.get("area_px") or 0) < self.limits.analysis_min_area_px:
            return False
        if float(evidence.get("conf") or 0.0) < self.limits.analysis_min_conf:
            return False
        return True

    def _prune_analysis_seen(self, now: float) -> None:
        if len(self._analysis_seen) < 1024:
            return
        cutoff = now - self.limits.analysis_dedup_s
        for key, seen in list(self._analysis_seen.items()):
            if seen < cutoff:
                del self._analysis_seen[key]

    def _enqueue_analysis(self, queue: asyncio.Queue, item: tuple) -> None:
        """Fila limitada: ao encher, descarta o item mais antigo (nunca o alerta)."""
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            pass
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        self._analysis_dropped += 1
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass

    def _set_alert_state(self, event_id: int, state: str) -> None:
        self._alert_state[event_id] = state
        while len(self._alert_state) > self.ALERT_STATE_MAX:
            self._alert_state.pop(next(iter(self._alert_state)))

    @staticmethod
    def _parse_observed(value) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    def _alert_latency_ms(self, observed) -> Optional[int]:
        obs = self._parse_observed(observed)
        if obs is None:
            return None
        now = datetime.now(obs.tzinfo) if obs.tzinfo else datetime.now()
        return max(0, int((now - obs).total_seconds() * 1000))

    def _alert_caption(self, event_id: int, event_type: str, ev: dict,
                       quem: str, analise: Optional[str] = None) -> str:
        if event_type == "aproximacao_portao":
            titulo = "🔔 Pessoa com interesse no portão"
        elif event_type == "acesso_casa":
            meta = ev.get("meta") or "{}"
            titulo = "🚨 ALGUÉM ENTROU NA CASA" if "ENTRY" in meta else "🚪 Saída da casa"
        else:
            titulo = "⚠️ Comportamento atípico"
        observed = ev.get("observed_at") or ev.get("timestamp_start") or "?"
        latency_ms = self._alert_latency_ms(observed)
        atraso = ""
        if latency_ms is not None and latency_ms > self.limits.alert_max_latency_s * 1000:
            atraso = f"⚠️ Atraso: {latency_ms / 1000:.1f}s (acima do limite)\n"
        caption = (
            f"{titulo} — evento #{event_id}\n"
            f"Observado: {observed} | Zona: {ev.get('zone', '?')}\n"
            f"{atraso}"
            f"Quem: {quem}\n"
        )
        if analise is not None:
            caption += f"Análise: {analise or '(sem VLM)'}\n"
        caption += f"\nSe você conhece, responda: é #{event_id} <nome> — para registrar."
        return caption

    async def _send_alert_now(self, event_id: int, event_type: str, imgs) -> None:
        """Alerta imediato: foto contextual + quem (identidade pelo recorte)."""
        from events.identity import crop_hist, match
        from notifier import send_photo

        cd = ALERT_COOLDOWN
        now = time.monotonic()
        last = self._alert_last.get(event_type)
        if last is not None and now - last < cd.get(event_type, 30):
            self._set_alert_state(event_id, "suppressed")
            logger.info("alerta #%d suprimido (debounce de %ss para %s)",
                        event_id, cd.get(event_type, 30), event_type)
            return
        self._set_alert_state(event_id, "pending")
        ev = self.store.event_by_id(event_id) or {}
        try:
            hist = await asyncio.to_thread(crop_hist, imgs.person_crop)
            m = match(hist, self.store.known_people())
            if m.matched:
                quem = f"{m.name} (conhecido, {m.score:.0%})"
                self.store.set_event_person_label(event_id, m.name)
            elif m.status == "uncertain":
                quem = "pessoa incerta (correspondência ambígua)"
            else:
                quem = "pessoa desconhecida"
        except Exception:  # pylint: disable=broad-exception-caught
            quem = "pessoa desconhecida"
        caption = self._alert_caption(event_id, event_type, ev, quem)
        if imgs.context:
            ok = await asyncio.to_thread(send_photo, str(imgs.context), caption)
        else:
            from notifier import send
            ok = await asyncio.to_thread(send, caption)
        if ok:
            self._set_alert_state(event_id, "sent")
            self._alert_last[event_type] = now
            latency = self._alert_latency_ms(ev.get("observed_at") or ev.get("timestamp_start"))
            self._last_alert_latency_ms = latency
            if latency is not None and latency > self.limits.alert_max_latency_s * 1000:
                logger.warning("alerta #%d entregue %.1fs após a observação (limite %.1fs)",
                               event_id, latency / 1000, self.limits.alert_max_latency_s)
        else:
            self._set_alert_state(event_id, "failed")
        logger.info("alerta INSTANTÂNEO #%d (%s) ok=%s", event_id, quem, ok)

    async def _vlm_worker(self) -> None:
        from events.identity import crop_hist, match
        from notifier import send_photo
        from vlm.describe import describe_event

        while True:
            try:
                await self._vlm_worker_once(crop_hist, match, send_photo, describe_event)
            except Exception as err:  # pylint: disable=broad-exception-caught
                # o worker NUNCA pode morrer em silêncio: loga e segue
                logger.exception("vlm_worker travou (%s) — reiniciando laço", err)
                await asyncio.sleep(1)

    def _watchdog_line(self) -> str:
        worker_alive = self._vlm_task is not None and not self._vlm_task.done()
        latency = ("—" if self._last_alert_latency_ms is None
                   else f"{self._last_alert_latency_ms}ms")
        return (
            f"FILA: análise={self._vlm_queue.qsize()} | prioritária={self._vlm_prio.qsize()}"
            f" | worker vivo={worker_alive}"
            f" | frames={self._frames_seen} processados={self._n_processed}"
            f" descartados_idade={self._frames_dropped}"
            f" substituídos={self._frames_overwritten}"
            f" idade_max={self._max_frame_age_ms}ms"
            f" | análise descartada={self._analysis_dropped}"
            f" deduplicada={self._analysis_deduped}"
            f" | latência_alerta={latency}"
        )

    async def _queue_watchdog(self) -> None:
        """Loga filas, frescor e latência a cada 60s + vigia se o worker está vivo."""
        while True:
            await asyncio.sleep(60)
            logger.info(self._watchdog_line())

    async def _describe_event(self, event_id: int, event_type: str,
                              context_path, crop_path, describe_event) -> Optional[str]:
        """Uma chamada de análise isolada: falha vira None, nunca derruba o worker."""
        vlm_input = context_path or crop_path
        if not vlm_input:
            return None
        now = time.monotonic()
        self._vlm_window.append(now)
        while self._vlm_window and now - self._vlm_window[0] > 3600:
            self._vlm_window.popleft()
        if len(self._vlm_window) > VLM_MAX_PER_HOUR:
            logger.warning("limite de VLM/hora atingido — evento %d sem descrição", event_id)
            return None
        call = functools.partial(describe_event, event_type, [vlm_input],
                                 timeout=self.limits.analysis_timeout_s)
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                self._analysis_executor, call)
        except Exception as err:  # pylint: disable=broad-exception-caught
            logger.warning("análise #%d falhou (%s) — seguindo sem descrição", event_id, err)
            return None
        if not result:
            return None
        self.store.set_vlm_description(event_id, result.descricao, result.risk_level)
        return result.descricao

    async def _vlm_worker_once(self, crop_hist, match, send_photo, describe_event) -> None:
        if self._vlm_prio.qsize():
            event_id, event_type, imgs, enqueued = await self._vlm_prio.get()
        else:
            event_id, event_type, imgs, enqueued = await self._vlm_queue.get()
        context_path = imgs.context
        crop_path = imgs.person_crop
        ev = self.store.event_by_id(event_id) or {}

        queue_age = time.monotonic() - enqueued
        descricao = None
        if queue_age > self.limits.analysis_max_age_s:
            self._analysis_dropped += 1
            logger.info("análise #%d descartada (%.0fs na fila) — alerta segue",
                        event_id, queue_age)
        else:
            descricao = await self._describe_event(
                event_id, event_type, context_path, crop_path, describe_event)

        # identidade: parecia com alguém conhecido? (recorte da pessoa)
        try:
            hist = await asyncio.to_thread(crop_hist, crop_path)
            m = match(hist, self.store.known_people())
            if m.matched:
                quem = f"{m.name} (conhecido, {m.score:.0%})"
                self.store.set_event_person_label(event_id, m.name)
            elif m.status == "uncertain":
                quem = "pessoa incerta (correspondência ambígua)"
            else:
                quem = "pessoa desconhecida"
        except Exception:  # pylint: disable=broad-exception-caught
            quem = "pessoa desconhecida"

        state = self._alert_state.get(event_id)
        if event_type in self.ALERT_TYPES and state in (None, "failed"):
            # retaguarda: o alerta instantâneo não chegou a disparar
            caption = self._alert_caption(event_id, event_type, ev, quem, descricao)
            if context_path:
                await asyncio.to_thread(send_photo, str(context_path), caption)
            else:
                from notifier import send
                await asyncio.to_thread(send, caption)
            logger.info("alerta enviado: evento #%d (%s)", event_id, quem)
        elif event_type in self.ALERT_TYPES and descricao:
            # reforço: a análise do VLM chegou depois do alerta instantâneo
            from notifier import send_telegram
            await asyncio.to_thread(
                send_telegram, f"🧠 Análise do evento #{event_id}: {descricao}")

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
    def _build_motion_mask(self):
        """Máscara 320x180 da ROI de entrada (portão + área interna)."""
        import cv2
        import numpy as np

        zones = self.engine.zones
        sw, sh = 320, 180
        sx, sy = sw / zones.image_size[0], sh / zones.image_size[1]
        mask = np.zeros((sh, sw), np.uint8)
        for name in ("ZONA_PORTAO", "ZONA_INTERNA"):
            poly = zones.polygons.get(name) or []
            if poly:
                cv2.fillPoly(mask, [np.array([[x * sx, y * sy] for x, y in poly], np.int32)], 255)
        return mask

    def _motion(self, gray) -> tuple[bool, int]:
        """Gatilho por mudança de pixel com filtro de BLOBO dentro da ROI."""
        import cv2
        import numpy as np

        if self._prev_small is None:
            self._prev_small = gray
            return False, 0
        diff_mask = (cv2.absdiff(gray, self._prev_small) > 18).astype(np.uint8)
        if self._motion_mask is not None:
            diff_mask = cv2.bitwise_and(diff_mask, self._motion_mask)
        changed = int(diff_mask.sum())
        motion = False
        if changed > 40:
            # só variação do tamanho de um objeto/pessoa/animal dispara;
            # borboleta, gota de chuva e vento geram blobs minúsculos
            nlab, _, stats, _ = cv2.connectedComponentsWithStats(diff_mask, connectivity=8)
            biggest = int(stats[1:, cv2.CC_STAT_AREA].max()) if nlab > 1 else 0
            motion = biggest >= 150
        if not motion:
            changed = 0  # ruído — não força detecção
        self._prev_small = gray
        return motion, changed

    def _take_frame(self, now: float) -> Optional[FrameEnvelope]:
        """Pega o frame mais recente; descarta e conta se estiver velho demais."""
        env = self._latest
        if env is None:
            return None
        age = now - env.frame.arrival_mono
        self._max_frame_age_ms = max(self._max_frame_age_ms, int(age * 1000))
        self._latest = None
        if age > self.limits.max_frame_age_s:
            self._frames_dropped += 1
            return None
        return env

    async def _read_frames(self, client: MilocoClient, camera_id: str,
                           decoder: H264StreamDecoder) -> None:
        """Drena o WS continuamente; o processador só olha o frame mais recente."""
        import cv2

        sw, sh = 320, 180
        self._motion_mask = self._build_motion_mask()
        async for chunk in client.stream_h264(camera_id):
            for frame in decoder.feed(chunk):
                self._frames_seen += 1
                gray = cv2.cvtColor(cv2.resize(frame.image, (sw, sh)), cv2.COLOR_BGR2GRAY)
                self._cur_gray = gray
                motion, changed = self._motion(gray)
                if self._latest is not None:
                    self._frames_overwritten += 1  # substituído antes de ser processado
                self._latest = FrameEnvelope(frame, gray, motion, changed)

    async def _process_frames(self, duration_seconds: Optional[float], t0: float) -> None:
        """Amostra a DETECT_FPS, sempre do frame mais recente, sem travar a leitura."""
        interval = 1.0 / max(1, self.detect_cfg.fps)
        while True:
            await asyncio.sleep(0.005)
            now = time.monotonic()
            if duration_seconds and now - t0 >= duration_seconds:
                return
            env = self._take_frame(now)
            if env is None:
                continue
            if not env.motion:
                wait = interval - (now - self._last_detect_ts)
                if wait > 0:
                    await asyncio.sleep(wait)
                    continue
            self._last_detect_ts = time.monotonic()
            if float(env.frame.image.std()) < 8.0:
                continue  # frame cinza (warm-up) não entra no detector
            if env.motion:
                logger.info("debug-motion: %d px alterados (movimento real)", env.changed)
            dets = await asyncio.to_thread(self.yolo.track, env.frame.image)
            for d in dets:
                if d.cls_name == "person":
                    logger.info("debug-person: track=%s center=(%.0f,%.0f) zone=%s",
                                d.track_id, d.center[0], d.center[1],
                                self.engine.zones.zone_at(*d.center))
            observed_at = datetime.fromtimestamp(env.frame.arrival_wall).astimezone()
            ids = self.engine.step(env.frame.image, dets, time.monotonic(),
                                   observed_at=observed_at)
            self._n_processed += 1
            self._n_events += len(ids)
            if self._n_processed % 300 == 0:
                logger.info("stats: frames=%d processados=%d eventos=%d tracks=%d idade_max=%dms",
                            self._frames_seen, self._n_processed, self._n_events,
                            len(self.engine.book.tracks), self._max_frame_age_ms)

    async def run(self, duration_seconds: float | None = None) -> None:
        if self.yolo is None:
            self.yolo = YoloEngine(self.detect_cfg.model, conf=self.detect_cfg.conf)
        client = MilocoClient()
        await client.login()
        camera_id = await client.resolve_camera_id()
        decoder = H264StreamDecoder()
        vlm_task = asyncio.create_task(self._vlm_worker())
        self._vlm_task = vlm_task
        watchdog = asyncio.create_task(self._queue_watchdog())
        logger.info(
            "pipeline rodando (detecção a %d FPS) | frame_age=%.1fs | fila_análise=%d",
            self.detect_cfg.fps, self.limits.max_frame_age_s, self.limits.analysis_queue_max)
        t0 = time.monotonic()
        reader = asyncio.create_task(self._read_frames(client, camera_id, decoder))
        processor = asyncio.create_task(self._process_frames(duration_seconds, t0))
        try:
            done, _pending = await asyncio.wait(
                {reader, processor}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # propaga erro de leitura/processamento
            if duration_seconds and processor in done:
                logger.info("teste encerrado: frames=%d processados=%d eventos=%d",
                            self._frames_seen, self._n_processed, self._n_events)
        except asyncio.CancelledError:
            if duration_seconds:
                logger.info("teste encerrado: frames=%d processados=%d eventos=%d",
                            self._frames_seen, self._n_processed, self._n_events)
        finally:
            for task in (reader, processor):
                task.cancel()
            for task in (reader, processor):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            vlm_task.cancel()
            watchdog.cancel()
            self._analysis_executor.shutdown(wait=False, cancel_futures=True)
            self.maintenance()
