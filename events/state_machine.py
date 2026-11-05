"""Camada 2 — máquina de estados de eventos (geometria, não pixels).

Regras implementadas (spec do Bento):
- passagem_pessoa / passagem_veiculo / animal: só transitaram pela ZONA_RUA
- embarque_desembarque: pessoa surge/merge junto de veículo parado > X s
- aproximacao_portao: pessoa na ZONA_PORTAO > Y s sem entrar
- acesso_casa: cruzamento da LINHA_ACESSO_CASA (ENTRY/EXIT) + house_sessions
  com correlação por aparência (histograma) e duração exata
- alerta_suspeito (loitering): pessoa parada/oscilando na ZONA_PORTAO ou junto
  a veículo por > N s; risco ALTO na janela noturna (00:00–05:30)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, NamedTuple, Optional

import numpy as np

from db.store import Store
from detect.tracker import TrackBook, TrackState
from detect.yolo_engine import ANIMAL_CLASSES, VEHICLE_CLASSES, Detection
from settings import RulesConfig
from events.identity import MATCH_MARGIN, MATCH_THRESHOLD
from zones.roi import Zones

logger = logging.getLogger(__name__)

SPEED_STOPPED = 12.0     # px/s — abaixo disso, "parado"
SPEED_SLOW = 25.0        # px/s — loitering: andar devagar/oscilar
VEHICLE_NEAR_MARGIN = 40  # px de margem ao redor do bbox do veículo

ALERT_TAGS = {"acesso_casa", "aproximacao_portao", "alerta_suspeito"}


class EventImages(NamedTuple):
    """Foto contextual para análise/humano e recorte da pessoa para identidade."""
    context: Optional[str]
    person_crop: Optional[str]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class EventEngine:
    def __init__(self, zones: Zones, rules: RulesConfig, store: Store,
                 snapshot_dir: Path,
                 on_event: Optional[Callable[[int, str, EventImages], None]] = None):
        self.zones = zones
        self.rules = rules
        self.store = store
        self.snapshot_dir = snapshot_dir
        self.on_event = on_event  # (event_id, event_type, EventImages) → fila VLM/alerta
        self.book = TrackBook()
        # instante em que o frame do step atual foi observado (horário de parede)
        self._observed_at: Optional[datetime] = None

    # ------------------------------------------------------------------ utils
    def _is_night(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now().astimezone()
        h1, m1, h2, m2 = self.rules.night_bounds
        t = now.hour * 60 + now.minute
        a, b = h1 * 60 + m1, h2 * 60 + m2
        return t >= a or t < b if a > b else a <= t < b

    def _person_crop_bytes(self, frame: np.ndarray, st: TrackState) -> Optional[bytes]:
        """Melhor recorte da pessoa; prioriza o crop guardado ao longo do track."""
        if st.best_crop:
            return st.best_crop
        if not st.bboxes:
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (int(v) for v in st.bboxes[-1])
        pad_x, pad_y = int((x2 - x1) * 0.25) + 10, int((y2 - y1) * 0.25) + 10
        x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        from io import BytesIO

        from PIL import Image

        buf = BytesIO()
        Image.fromarray(crop[:, :, ::-1]).save(buf, format="JPEG", quality=88)
        return buf.getvalue()

    def _save_snapshot(self, frame: np.ndarray, st: TrackState, tag: str) -> EventImages:
        """Gera foto contextual (alertas) e recorte da pessoa para identidade."""
        try:
            day = datetime.now().astimezone().strftime("%Y-%m-%d")
            folder = self.snapshot_dir / day
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().astimezone().strftime('%H%M%S')
            base = f"{tag}_track{st.track_id}_{stamp}"

            crop_bytes = self._person_crop_bytes(frame, st)
            crop_path: Optional[Path] = None
            if crop_bytes:
                crop_path = folder / f"{base}_person.jpg"
                crop_path.write_bytes(crop_bytes)

            if tag in ALERT_TAGS:
                import cv2

                annotated = frame.copy()
                if st.bboxes:
                    x1, y1, x2, y2 = (int(v) for v in st.bboxes[-1])
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 6)
                h, w = annotated.shape[:2]
                if w > 1600:
                    annotated = cv2.resize(annotated, (1600, int(h * 1600 / w)))
                ctx_path = folder / f"{base}.jpg"
                cv2.imwrite(str(ctx_path), annotated, [cv2.IMWRITE_JPEG_QUALITY, 82])
                return EventImages(str(ctx_path), str(crop_path) if crop_path else None)

            # não-alerta: o recorte da pessoa serve de contexto e de identidade
            ctx = str(crop_path) if crop_path else None
            return EventImages(ctx, ctx)
        except Exception as err:  # pylint: disable=broad-exception-caught
            logger.error("snapshot falhou: %s", err)
            return EventImages(None, None)

    def _maybe_keep_best_crop(self, frame: np.ndarray, st: TrackState) -> None:
        """Guarda o recorte do momento em que o alvo está MAIS perto/central."""
        if not st.bboxes:
            return
        x1, y1, x2, y2 = st.bboxes[-1]
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area <= st.best_area or area < 60 * 60:  # nada minúsculo
            return
        h, w = frame.shape[:2]
        pad_x, pad_y = int((x2 - x1) * 0.2) + 10, int((y2 - y1) * 0.2) + 10
        ix1, iy1 = max(0, int(x1) - pad_x), max(0, int(y1) - pad_y)
        ix2, iy2 = min(w, int(x2) + pad_x), min(h, int(y2) + pad_y)
        crop = frame[iy1:iy2, ix1:ix2]
        if crop.size == 0:
            return
        try:
            from io import BytesIO

            from PIL import Image

            buf = BytesIO()
            Image.fromarray(crop[:, :, ::-1]).save(buf, format="JPEG", quality=88)
            st.best_crop = buf.getvalue()
            st.best_area = area
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def _obs_iso(self) -> str:
        observed = self._observed_at or datetime.now().astimezone()
        return observed.isoformat(timespec="seconds")

    def _emit(self, event_type: str, st: TrackState, zone: Optional[str],
              frame: np.ndarray, risk: str = "baixo", ts_end: Optional[str] = None,
              meta: Optional[dict] = None) -> int:
        observed = self._observed_at or datetime.now().astimezone()
        latency_ms = max(0, int((datetime.now().astimezone() - observed).total_seconds() * 1000))
        area = 0.0
        if st.bboxes:
            x1, y1, x2, y2 = st.bboxes[-1]
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        payload = dict(meta or {})
        payload.setdefault("evidencia", {
            "classe": st.cls_name,
            "area_px": int(round(area)),
            "conf": round(float(st.last_conf), 3),
            "track": st.track_id,
        })
        ts_start = self._obs_iso()
        imgs = self._save_snapshot(frame, st, event_type)
        eid = self.store.add_event(
            event_type=event_type, track_id=st.track_id, zone=zone,
            ts_start=ts_start, ts_end=ts_end, risk_level=risk,
            snapshot_path=imgs.context, person_crop_path=imgs.person_crop,
            meta=json.dumps(payload, ensure_ascii=False),
            observed_at=ts_start, detect_latency_ms=latency_ms,
        )
        logger.info("EVENTO #%d %s track=%s zone=%s risk=%s", eid, event_type, st.track_id, zone, risk)
        if self.on_event and (imgs.context or imgs.person_crop or event_type in ALERT_TAGS):
            self.on_event(eid, event_type, imgs)
        return eid

    # ------------------------------------------------------------- principais
    def step(self, frame: np.ndarray, detections: list[Detection],
             now: Optional[float] = None,
             observed_at: Optional[datetime] = None) -> list[int]:
        now = time.monotonic() if now is None else now
        self._observed_at = observed_at or datetime.now().astimezone()
        ids: list[int] = []
        states = self.book.update(detections, frame, now)
        for st in states:
            ids.extend(self._process(frame, st, now))
        for st in self.book.lost(now):
            if not st.finalized:
                st.finalized = True
                ids.extend(self._finalize(frame, st, now))
        self.book.prune(now)
        return ids

    def _process(self, frame: np.ndarray, st: TrackState, now: float) -> list[int]:
        ids: list[int] = []
        if not st.history:
            return ids
        _, cx, cy = st.history[-1]
        self._maybe_keep_best_crop(frame, st)
        zone = self.zones.zone_at(cx, cy)
        st.current_zone = zone
        if zone:
            st.zones_seen.add(zone)

        # --- cruzamento da linha de acesso (ENTRY/EXIT) ---
        if len(st.history) >= 2 and st.cls_name == "person":
            _, px, py = st.history[-2]
            crossing = self.zones.crossing_entry((px, py), (cx, cy))
            if crossing and crossing != st.last_entry:
                st.last_entry = crossing
                ids.extend(self._house_crossing(frame, st, crossing))

        # --- fallback de entrada: pessoa na ZONA_INTERNA sem ter cruzado a linha
        #     (tolera rastro quebrado / linha desalinhada) ---
        if (st.cls_name == "person" and zone == "ZONA_INTERNA"
                and not st.entry_done and st.last_entry != "ENTRY"):
            st.last_entry = "ENTRY"
            ids.extend(self._house_crossing(frame, st, "ENTRY", confirmed=False))

        # --- pessoa na ZONA_PORTAO: dwell + loitering (permanência CONTÍGUA) ---
        if st.cls_name == "person" and zone == "ZONA_PORTAO" and not st.entry_done:
            if st.gate_since is None:
                st.gate_since = now
            dwell = now - st.gate_since
            if dwell >= self.rules.gate_dwell_seconds and not st.gate_dwell_emitted:
                st.gate_dwell_emitted = True
                ids.append(self._emit("aproximacao_portao", st, zone, frame,
                                      risk="medio" if self._is_night() else "baixo",
                                      meta={"dwell_s": round(dwell, 1)}))
            if dwell >= self.rules.loitering_seconds and not st.gate_loiter_emitted:
                st.gate_loiter_emitted = True
                ids.append(self._emit("alerta_suspeito", st, zone, frame,
                                      risk="alto" if self._is_night() else "medio",
                                      meta={"motivo": f"loitering {round(dwell)}s no portão"}))
        else:
            # saiu da zona / não é mais elegível: reinicia o episódio de portão
            st.gate_since = None
            st.gate_dwell_emitted = False
            st.gate_loiter_emitted = False

        # --- desembarque: pessoa JOVEM surge junto a veículo que parou há pouco
        #     (exige histórico de movimento no veículo — o carro estacionado
        #     "de sempre" na frente não conta como parada) ---
        if st.cls_name == "person" and not st.veh_exit_emitted:
            if now - st.first_seen < 10:
                near = self._stopped_vehicle_near(st, recent_only=True, now=now)
                if near is not None:
                    st.veh_exit_emitted = True
                    ids.append(self._emit("embarque_desembarque", st, zone or "ZONA_RUA", frame,
                                          meta={"vehicle_track": near.track_id, "acao": "saiu_do_carro",
                                                "parado_desde_s": round(now - (near.stopped_since or now), 1)}))
            else:
                st.veh_exit_emitted = True  # janela de nascimento encerrada

        # --- loitering perto de veículo parado (proximidade CONTÍGUA) ---
        if st.cls_name == "person" and st.veh_exit_emitted:
            near = self._stopped_vehicle_near(st, recent_only=True, now=now)
            if near is None or (st.vehicle_near_track is not None
                                and near.track_id != st.vehicle_near_track):
                # perdeu a proximidade ou trocou de veículo: reinicia episódio
                st.vehicle_near_since = None
                st.vehicle_near_track = None
                st.loiter_veh_emitted = False
            else:
                if st.vehicle_near_since is None:
                    st.vehicle_near_since = now
                    st.vehicle_near_track = near.track_id
                vt = st.vehicle_near_since
                if now - vt >= self.rules.loitering_seconds and not st.loiter_veh_emitted:
                    st.loiter_veh_emitted = True
                    ids.append(self._emit("alerta_suspeito", st, zone or "ZONA_RUA", frame,
                                          risk="alto" if self._is_night() else "medio",
                                          meta={"motivo": f"loitering {round(now - vt)}s junto a veículo parado",
                                                "vehicle_track": near.track_id}))

        # --- velocidade / parada (veículos) ---
        if st.cls_name in VEHICLE_CLASSES:
            if st.speed_px_s < SPEED_STOPPED:
                if st.stopped_since is None:
                    st.stopped_since = now
            else:
                st.stopped_since = None

        return ids

    def _house_crossing(self, frame: np.ndarray, st: TrackState, crossing: str,
                        confirmed: bool = True) -> list[int]:
        ids: list[int] = []
        ts = self._obs_iso()
        imgs = self._save_snapshot(frame, st, f"acesso_{crossing.lower()}")
        if crossing == "ENTRY":
            status, existing = self._session_match(st, self.store.open_sessions())
            if status == "matched" and existing is not None:
                # troca de track da MESMA presença: deduplica, não abre outra sessão
                self.store.touch_session(existing, ts)
                return ids
            st.entry_done = True
            desc = f"pessoa (track {st.track_id})"
            hist = st.appearance.tobytes() if st.appearance is not None else None
            session_status = "INSIDE" if confirmed else "UNCERTAIN"
            self.store.open_session(
                desc, ts, imgs.context, entry_track_id=st.track_id,
                appearance_hist=hist, status=session_status, match_status=status,
            )
            ids.append(self._emit("acesso_casa", st, "LINHA_ACESSO_CASA", frame, meta={"direcao": "ENTRY"}))
        else:  # EXIT
            status, sid = self._session_match(st, self.store.open_sessions())
            if status == "matched" and sid is not None:
                self.store.close_session(sid, ts, imgs.context, match_status="matched")
            else:
                sid = None
            ids.append(self._emit("acesso_casa", st, "LINHA_ACESSO_CASA", frame,
                                  meta={"direcao": "EXIT", "session_id": sid,
                                        "match_status": status}))
        return ids

    def _session_match(self, st: TrackState, sessions: list[dict]) -> tuple[str, Optional[int]]:
        """Correlaciona a aparência do track com sessões abertas.

        Retorna (status, session_id): 'matched' com correspondência única e
        suficiente; 'uncertain' quando os melhores candidatos estão próximos;
        'unknown' quando não há evidência compatível. Nunca escolhe por FIFO.
        """
        if st.appearance is None:
            return "unknown", None
        scored: list[tuple[float, int]] = []
        for s in sessions:
            hist = s.get("appearance_hist")
            if not hist:
                continue
            vec = np.frombuffer(hist, dtype=np.float32)
            if vec.shape != st.appearance.shape:
                continue
            score = float(np.dot(vec, st.appearance))
            if score >= MATCH_THRESHOLD:
                scored.append((score, s["id"]))
        if not scored:
            return "unknown", None
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_id = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        if len(scored) > 1 and (best_score - second) < MATCH_MARGIN:
            return "uncertain", best_id
        return "matched", best_id

    def _match_session(self, st: TrackState) -> Optional[int]:
        """Sessão compatível com a pessoa que saiu; None se não houver."""
        status, sid = self._session_match(st, self.store.open_sessions())
        return sid if status == "matched" else None

    def _stopped_vehicle_near(self, st: TrackState, recent_only: bool = False,
                              now: Optional[float] = None) -> Optional[TrackState]:
        _, px, py = st.history[-1]
        now = time.monotonic() if now is None else now
        for v in self.book.active_vehicles():
            if v.stopped_since is None or not v.bboxes:
                continue
            if recent_only:
                # exige: parada recente (< 120 s) E histórico de movimento
                # (o carro estacionado "de sempre" não gera interação)
                if now - v.stopped_since > 120 or v.total_path_px < 40:
                    continue
            x1, y1, x2, y2 = v.bboxes[-1]
            if (x1 - VEHICLE_NEAR_MARGIN <= px <= x2 + VEHICLE_NEAR_MARGIN and
                    y1 - VEHICLE_NEAR_MARGIN <= py <= y2 + VEHICLE_NEAR_MARGIN):
                return v
        return None

    def _finalize(self, frame: np.ndarray, st: TrackState, now: float) -> list[int]:
        """Track sumiu: emite o evento de passagem/semente e encerra pendências."""
        ids: list[int] = []
        if st.cls_name == "person":
            if st.entry_done or st.last_entry == "ENTRY":
                return ids  # acesso à casa já registrado
            near = self._stopped_vehicle_near(st, recent_only=True, now=now)
            if near is not None and not st.veh_interaction_emitted:
                st.veh_interaction_emitted = True
                ids.append(self._emit("embarque_desembarque", st, st.current_zone or "ZONA_RUA", frame,
                                      meta={"vehicle_track": near.track_id, "acao": "entrou_no_carro",
                                            "parado_desde_s": round(now - (near.stopped_since or now), 1)}))
                return ids
            if st.gate_dwell_emitted and not st.entry_done:
                return ids  # aproximação do portão já registrada
            if st.zones_seen & {"ZONA_RUA", "ZONA_PORTAO"}:
                ids.append(self._emit("passagem_pessoa", st, st.current_zone or "ZONA_RUA", frame,
                                      meta=self._direction_meta(st)))
        elif st.cls_name in VEHICLE_CLASSES:
            if st.stopped_since is not None and (now - st.stopped_since) >= self.rules.vehicle_stop_seconds:
                # veículo que ficou parado e foi embora: se houve interação, já foi emitida
                return ids
            if "ZONA_RUA" in st.zones_seen:
                ids.append(self._emit("passagem_veiculo", st, "ZONA_RUA", frame,
                                      meta={"classe": st.cls_name, **self._direction_meta(st)}))
        elif st.cls_name in ANIMAL_CLASSES:
            ids.append(self._emit("animal", st, st.current_zone or "ZONA_RUA", frame,
                                  meta={"classe": st.cls_name, **self._direction_meta(st)}))
        return ids

    def _direction_meta(self, st: TrackState) -> dict:
        if len(st.history) < 2:
            return {"direcao": "indeterminada"}
        _, x0, y0 = st.history[0]
        _, x1, y1 = st.history[-1]
        if abs(x1 - x0) >= abs(y1 - y0):
            direcao = "direita" if x1 > x0 else "esquerda"
        else:
            direcao = "frente" if y1 > y0 else "fundo"
        return {"direcao": direcao, "path_px": round(st.total_path_px, 1)}
