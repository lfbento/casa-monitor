"""Camada 1 — detecção + rastreamento (YOLO + ByteTrack via Ultralytics).

Classes COCO monitoradas: person, bicycle, car, motorcycle, truck, bird, cat, dog.
A inferência roda na GTX 1080 (CUDA) quando disponível; fallback CPU automático.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# ids COCO
WANTED_CLASSES = {
    0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 7: "truck",
    14: "bird", 15: "cat", 16: "dog",
}
VEHICLE_CLASSES = {"car", "motorcycle", "truck", "bicycle"}
ANIMAL_CLASSES = {"dog", "cat", "bird"}


@dataclass
class Detection:
    track_id: int
    cls_name: str
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    def iou(self, other: "Detection") -> float:
        ix1, iy1 = max(self.x1, other.x1), max(self.y1, other.y1)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


class YoloEngine:
    """Wrapper fino do Ultralytics: track() com persistência de IDs (ByteTrack)."""

    def __init__(self, model_name: str = "yolo11n.pt", conf: float = 0.35, device: str | None = None):
        import torch
        from ultralytics import YOLO

        self._conf = conf
        if device is None:
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.device = device
        self._model = YOLO(model_name)
        self._model.to(device)
        logger.info("YOLO %s em %s (cuda=%s)", model_name, device, torch.cuda.is_available())

    def track(self, frame_bgr: np.ndarray) -> list[Detection]:
        """Uma passada de rastreamento (mantém IDs entre chamadas)."""
        results = self._model.track(
            frame_bgr,
            persist=True,
            tracker="bytetrack.yaml",
            conf=self._conf,
            classes=sorted(WANTED_CLASSES),
            verbose=False,
            device=self.device,
        )
        out: list[Detection] = []
        res = results[0]
        if res.boxes is None or res.boxes.id is None:
            return out
        ids = res.boxes.id.int().tolist()
        clss = res.boxes.cls.int().tolist()
        confs = res.boxes.conf.tolist()
        xyxy = res.boxes.xyxy.tolist()
        for tid, cls, cf, (x1, y1, x2, y2) in zip(ids, clss, confs, xyxy):
            name = WANTED_CLASSES.get(cls)
            if name is None:
                continue
            out.append(Detection(track_id=tid, cls_name=name, conf=cf, x1=x1, y1=y1, x2=x2, y2=y2))
        return out
