"""Identidade de pessoas: histograma de aparência + pareamento com conhecidos.

Assinatura leve (HSV 24H + 16S, igual ao TrackState) — boa o suficiente pra
apontar "parecido com X"; a confirmação final é sempre o usuário. Quando
disponível, o VLM pode ser usado como juiz (futuro).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = float(os.getenv("MATCH_THRESHOLD", "0.95"))  # cosseno do recorte da pessoa
MATCH_MARGIN = float(os.getenv("MATCH_MARGIN", "0.05"))        # separação mínima sobre a 2a melhor


@dataclass
class Match:
    person_id: int
    name: str
    score: float


@dataclass
class MatchResult:
    """Resultado conservador: nome apenas quando há evidência suficiente."""
    status: str                      # 'matched' | 'unknown' | 'uncertain'
    name: str | None = None
    person_id: int | None = None
    score: float = 0.0
    second_score: float = 0.0

    @property
    def matched(self) -> bool:
        return self.status == "matched"


def match(hist: np.ndarray | None, known: list[dict],
          threshold: float = MATCH_THRESHOLD,
          margin: float = MATCH_MARGIN) -> MatchResult:
    """Pareia recorte com pessoas conhecidas, com limiar e margem mínima.

    Só assinaturas provenientes de recorte da pessoa (`person_crop`) entram.
    Retorna 'matched' apenas com melhor candidato acima do limiar e com margem
    suficiente sobre o segundo; 'uncertain' quando os melhores estão próximos;
    'unknown' quando ninguém atinge o limiar.
    """
    if hist is None:
        return MatchResult(status="unknown")
    scored: list[Match] = []
    for p in known:
        if p.get("signature_source", "full_frame") != "person_crop":
            continue
        kh = p.get("hist")
        if not kh:
            continue
        kvec = np.frombuffer(kh, dtype=np.float32)
        if kvec.shape != hist.shape:
            continue
        score = float(np.dot(kvec, hist))
        if score >= threshold:
            scored.append(Match(person_id=p["id"], name=p["name"], score=score))
    if not scored:
        return MatchResult(status="unknown")
    scored.sort(key=lambda m: m.score, reverse=True)
    best = scored[0]
    second = scored[1].score if len(scored) > 1 else 0.0
    if len(scored) > 1 and (best.score - second) < margin:
        return MatchResult(status="uncertain", name=best.name,
                           person_id=best.person_id, score=best.score,
                           second_score=second)
    return MatchResult(status="matched", name=best.name,
                       person_id=best.person_id, score=best.score,
                       second_score=second)


def crop_hist(path: Path | str | None) -> np.ndarray | None:
    """Histograma HSV normalizado do recorte da pessoa (mesma assinatura do TrackState)."""
    if not path:
        return None
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
