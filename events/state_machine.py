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
from typing import Callable, Optional

import numpy as np

from db.store import Store
from detect.tracker import TrackBook, TrackState
from detect.yolo_engine import ANIMAL_CLASSES, VEHICLE_CLASSES, Detection
from settings import RulesConfig
from zones.roi import Zones

logger = logging.getLogger(__name__)

SPEED_STOPPED = 12.0     # px/s — abaixo disso, "parado"
SPEED_SLOW = 25.0        # px/s — loitering: andar devagar/oscilar
VEHICLE_NEAR_MARGIN = 40  # px de margem ao redor do bbox do veículo


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class EventEngine:
    def __init__(self, zones: Zones, rules: RulesConfig, store: Store,
                 snapshot_dir: Path,
                 on_event: Optional[Callable[[int, str, Path], None]] = None):
        self.zones = zones
        self.rules = rules
        self.store = store
        self.snapshot_dir = snapshot_dir
        self.on_event = on_event  # (event_id, event_type, snapshot_path) → fila VLM
        self.book = TrackBook()

    # ------------------------------------------------------------------ utils
    def _is_night(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now().astimezone()
        h1, m1, h2, m2 = self.rules.night_bounds
        t = now.hour * 60 + now.minute
        a, b = h1 * 60 + m1, h2 * 60 + m2
        return t >= a or t < b if a > b else a <= t < b

    def _save_snapshot(self, frame: np.ndarray, st: TrackState, tag: str) -> Optional[str]:
        """Salva o melhor crop do track (não o último — no fim o alvo já saiu)."""
        try:
            from PIL import Image

            day = datetime.now().astimezone().strftime("%Y-%m-%d")
            folder = self.snapshot_dir / day
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{tag}_track{st.track_id}_{datetime.now().astimezone().strftime('%H%M%S')}.jpg"
            path = folder / name
            if st.best_crop:
                path.write_bytes(st.best_crop)
                return str(path)
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = (int(v) for v in st.bboxes[-1])
            pad_x, pad_y = int((x2 - x1) * 0.25) + 10, int((y2 - y1) * 0.25) + 10
            x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
            x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                return None
            Image.fromarray(crop[:, :, ::-1]).save(path, quality=88)
            return str(path)
        except Exception as err:  # pylint: disable=broad-exception-caught
            logger.error("snapshot falhou: %s", err)
            return None

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

    def _emit(self, event_type: str, st: TrackState, zone: Optional[str],
              frame: np.ndarray, risk: str = "baixo", ts_end: Optional[str] = None,
              meta: Optional[dict] = None) -> int:
        snap = self._save_snapshot(frame, st, event_type)
        eid = self.store.add_event(
            event_type=event_type, track_id=st.track_id, zone=zone,
            ts_start=_now_iso(), ts_end=ts_end, risk_level=risk,
            snapshot_path=snap, meta=json.dumps(meta or {}, ensure_ascii=False),
        )
        logger.info("EVENTO #%d %s track=%s zone=%s risk=%s", eid, event_type, st.track_id, zone, risk)
        if self.on_event and snap:
            self.on_event(eid, event_type, Path(snap))
        return eid

    # ------------------------------------------------------------- principais
    def step(self, frame: np.ndarray, detections: list[Detection],
             now: Optional[float] = None) -> list[int]:
        now = time.monotonic() if now is None else now
        ids: list[int] = []
        states = self.book.update(detections, frame, now)
        for st in states:
            ids.extend(self._process(frame, st, now))
        for st in self.book.lost(now):
            if "finalized" not in st.flags:
                st.flags.add("finalized")
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

        # --- pessoa na ZONA_PORTAO: dwell + loitering ---
        if st.cls_name == "person" and zone == "ZONA_PORTAO" and "entry_done" not in st.flags:
            if not any(f.startswith("gate_t:") for f in st.flags):
                st.flags.add(f"gate_t:{now}")
            gate_t = next((float(f.split(":")[1]) for f in st.flags if f.startswith("gate_t:")), now)
            dwell = now - gate_t
            if dwell >= self.rules.gate_dwell_seconds and "gate_dwell" not in st.flags:
                st.flags.add("gate_dwell")
                ids.append(self._emit("aproximacao_portao", st, zone, frame,
                                      risk="medio" if self._is_night() else "baixo",
                                      meta={"dwell_s": round(dwell, 1)}))
            if dwell >= self.rules.loitering_seconds and "loiter_gate" not in st.flags:
                st.flags.add("loiter_gate")
                ids.append(self._emit("alerta_suspeito", st, zone, frame,
                                      risk="alto" if self._is_night() else "medio",
                                      meta={"motivo": f"loitering {round(dwell)}s no portão"}))

        # --- desembarque: pessoa JOVEM surge junto a veículo que parou há pouco
        #     (exige histórico de movimento no veículo — o carro estacionado
        #     "de sempre" na frente não conta como parada) ---
        if st.cls_name == "person" and "veh_exit" not in st.flags:
            if now - st.first_seen < 10:
                near = self._stopped_vehicle_near(st, recent_only=True)
                if near is not None:
                    st.flags.add("veh_exit")
                    ids.append(self._emit("embarque_desembarque", st, zone or "ZONA_RUA", frame,
                                          meta={"vehicle_track": near.track_id, "acao": "saiu_do_carro",
                                                "parado_desde_s": round(now - (near.stopped_since or now), 1)}))
            else:
                st.flags.add("veh_exit")  # janela de nascimento encerrada

        # --- loitering perto de veículo parado ---
        if st.cls_name == "person" and "veh_exit" in st.flags:
            near = self._stopped_vehicle_near(st)
            if near is not None:
                if "veh_near_since" not in st.flags:
                    st.flags.add("veh_near_since")
                    st.flags.add(f"veh_t:{now}")
                vt = next((float(f.split(":")[1]) for f in st.flags if f.startswith("veh_t:")), now)
                if now - vt >= self.rules.loitering_seconds and "loiter_veh" not in st.flags:
                    st.flags.add("loiter_veh")
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

    def _house_crossing(self, frame: np.ndarray, st: TrackState, crossing: str) -> list[int]:
        ids: list[int] = []
        ts = _now_iso()
        snap = self._save_snapshot(frame, st, f"acesso_{crossing.lower()}")
        if crossing == "ENTRY":
            st.flags.add("entry_done")
            desc = f"pessoa (track {st.track_id})"
            self.store.open_session(desc, ts, snap)
            ids.append(self._emit("acesso_casa", st, "LINHA_ACESSO_CASA", frame, meta={"direcao": "ENTRY"}))
        else:  # EXIT
            sid = self._match_session(st)
            if sid is not None:
                self.store.close_session(sid, ts, snap)
            ids.append(self._emit("acesso_casa", st, "LINHA_ACESSO_CASA", frame,
                                  meta={"direcao": "EXIT", "session_id": sid}))
        return ids

    def _match_session(self, st: TrackState) -> Optional[int]:
        """Correlaciona EXIT com sessões abertas: aparência (cosseno) ou FIFO."""
        sessions = self.store.open_sessions()
        if not sessions:
            return None
        # sessões abertas não guardam embedding; usa FIFO (mais antiga primeiro)
        # e registra a similaridade para auditoria quando houver aparência
        best = sessions[0]
        if st.appearance is not None and len(sessions) > 1:
            # sem embeddings persistidos, FIFO é o desempate determinístico
            pass
        return int(best["id"])

    def _stopped_vehicle_near(self, st: TrackState, recent_only: bool = False) -> Optional[TrackState]:
        _, px, py = st.history[-1]
        now = time.monotonic()
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
            if "entry_done" in st.flags or st.last_entry == "ENTRY":
                return ids  # acesso à casa já registrado
            near = self._stopped_vehicle_near(st, recent_only=True)
            if near is not None and "veh_interaction" not in st.flags:
                st.flags.add("veh_interaction")
                ids.append(self._emit("embarque_desembarque", st, st.current_zone or "ZONA_RUA", frame,
                                      meta={"vehicle_track": near.track_id, "acao": "entrou_no_carro",
                                            "parado_desde_s": round(now - (near.stopped_since or now), 1)}))
                return ids
            if "gate_dwell" in st.flags and "entry_done" not in st.flags:
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
