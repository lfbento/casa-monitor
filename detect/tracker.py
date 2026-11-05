"""Estado por track (Camada 1.5): histórico, velocidade, zonas, aparência.

Alimenta a máquina de estados da Camada 2 sem custo de pixel — só geometria.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from detect.yolo_engine import Detection


@dataclass
class TrackState:
    track_id: int
    cls_name: str
    first_seen: float
    last_seen: float
    history: deque = field(default_factory=lambda: deque(maxlen=64))  # (t, cx, cy)
    bboxes: deque = field(default_factory=lambda: deque(maxlen=8))    # (x1,y1,x2,y2)
    speed_px_s: float = 0.0
    total_path_px: float = 0.0
    last_conf: float = 0.0             # confiança da última detecção do track
    hits: int = 0                      # quantas detecções este track já recebeu
    zones_seen: set = field(default_factory=set)
    current_zone: str | None = None
    last_entry: str | None = None       # 'ENTRY' | 'EXIT' pendente de emitir
    stopped_since: float | None = None  # parado (velocidade baixa) desde
    appearance: np.ndarray | None = None  # histograma de aparência (crop)
    best_crop: bytes | None = None    # melhor recorte visto (JPEG, bbox maior)
    best_area: float = 0.0
    entry_done: bool = False          # já registrou acesso à casa
    gate_since: float | None = None   # início da permanência contígua no portão
    gate_dwell_emitted: bool = False
    gate_loiter_emitted: bool = False
    vehicle_near_since: float | None = None   # início da proximidade contígua c/ veículo
    vehicle_near_track: int | None = None
    veh_exit_emitted: bool = False    # janela de nascimento do desembarque encerrada
    loiter_veh_emitted: bool = False
    veh_interaction_emitted: bool = False
    finalized: bool = False
    gone_since: float | None = None

    MAX_SPEED_DT = 2.0  # s — gaps maiores não entram na velocidade

    def update(self, det: Detection, now: float) -> None:
        cx, cy = det.center
        if self.history:
            t0, x0, y0 = self.history[-1]
            dt = now - t0
            dist = ((cx - x0) ** 2 + (cy - y0) ** 2) ** 0.5
            if dt <= self.MAX_SPEED_DT:
                self.speed_px_s = 0.7 * self.speed_px_s + 0.3 * (dist / max(dt, 1e-3))
            else:
                # gap de processamento: não inventa pico de velocidade
                self.speed_px_s = 0.0
            self.total_path_px += dist
        self.history.append((now, cx, cy))
        self.bboxes.append((det.x1, det.y1, det.x2, det.y2))
        self.last_conf = det.conf
        self.hits += 1
        self.last_seen = now

    def set_appearance(self, frame_bgr: np.ndarray) -> None:
        """Histograma HSV normalizado do crop — assinatura leve de aparência."""
        x1, y1, x2, y2 = (int(v) for v in self.bboxes[-1])
        h, w = frame_bgr.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        crop = frame_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            return
        import cv2

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist_h = cv2.calcHist([hsv], [0], None, [24], [0, 180]).flatten()
        hist_s = cv2.calcHist([hsv], [1], None, [16], [0, 256]).flatten()
        feat = np.concatenate([hist_h, hist_s]).astype(np.float32)
        norm = np.linalg.norm(feat)
        self.appearance = feat / norm if norm > 0 else feat

    @staticmethod
    def appearance_similarity(a: np.ndarray | None, b: np.ndarray | None) -> float:
        if a is None or b is None:
            return 0.0
        return float(np.dot(a, b))  # cosseno (vetores já normalizados)


class TrackBook:
    """Registro vivo de todos os tracks."""

    def __init__(self, stale_seconds: float = 8.0, gap_reset_seconds: float = 10.0):
        self.tracks: dict[int, TrackState] = {}
        self.stale_seconds = stale_seconds
        self.gap_reset_seconds = gap_reset_seconds

    def update(self, detections: list[Detection], frame_bgr: np.ndarray, now: float | None = None) -> list[TrackState]:
        now = time.monotonic() if now is None else now
        seen: list[TrackState] = []
        for det in detections:
            st = self.tracks.get(det.track_id)
            if st is not None and now - st.last_seen > self.gap_reset_seconds:
                # gap longo: o estado antigo não representa mais o objeto
                st = None
            if st is None:
                st = TrackState(track_id=det.track_id, cls_name=det.cls_name,
                                first_seen=now, last_seen=now)
                self.tracks[det.track_id] = st
            st.update(det, now)
            if st.appearance is None and st.cls_name == "person":
                st.set_appearance(frame_bgr)
            seen.append(st)
        # marca os ausentes
        for tid, st in self.tracks.items():
            if tid not in {d.track_id for d in detections}:
                if st.gone_since is None:
                    st.gone_since = now
            else:
                st.gone_since = None
        return seen

    def lost(self, now: float | None = None) -> list[TrackState]:
        """Tracks sem update há mais que stale_seconds (podem ser encerrados)."""
        now = time.monotonic() if now is None else now
        return [st for st in self.tracks.values()
                if st.gone_since is not None and now - st.gone_since >= self.stale_seconds]

    def prune(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for tid in [t for t, st in self.tracks.items()
                    if st.gone_since is not None and now - st.gone_since > 60]:
            del self.tracks[tid]

    def active_vehicles(self) -> list[TrackState]:
        from detect.yolo_engine import VEHICLE_CLASSES
        return [st for st in self.tracks.values() if st.cls_name in VEHICLE_CLASSES]
