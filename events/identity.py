"""Identidade de pessoas: histograma de aparência + pareamento com conhecidos.

Assinatura leve (HSV 24H + 16S, igual ao TrackState) — boa o suficiente pra
apontar "parecido com X"; a confirmação final é sempre o usuário. Quando
disponível, o VLM pode ser usado como juiz (futuro).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 0.85  # cosseno do histograma (roupa+aparência)


def crop_hist(path: Path) -> np.ndarray | None:
    """Histograma HSV normalizado do crop (mesma assinatura do TrackState)."""
    try:
        import cv2

        img = cv2.imread(str(path))
        if img is None:
            return None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0], None, [24], [0, 180]).flatten()
        s = cv2.calcHist([hsv], [1], None, [16], [0, 256]).flatten()
        feat = np.concatenate([h, s]).astype(np.float32)
        norm = np.linalg.norm(feat)
        return feat / norm if norm > 0 else feat
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.error("crop_hist falhou: %s", err)
        return None


@dataclass
class Match:
    person_id: int
    name: str
    score: float


def match(hist: np.ndarray | None, known: list[dict],
          threshold: float = MATCH_THRESHOLD) -> Match | None:
    """Melhor pareamento entre o histograma e as pessoas conhecidas."""
    if hist is None:
        return None
    best: Match | None = None
    for p in known:
        kh = p.get("hist")
        if not kh:
            continue
        kvec = np.frombuffer(kh, dtype=np.float32)
        if kvec.shape != hist.shape:
            continue
        score = float(np.dot(kvec, hist))
        if score >= threshold and (best is None or score > best.score):
            best = Match(person_id=p["id"], name=p["name"], score=score)
    return best
