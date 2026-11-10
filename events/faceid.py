"""Identidade por ROsto — embeddings ArcFace locais (InsightFace buffalo_l).

Roda em CPU só no momento do registro/alerta (poucos recortes por minuto).
Substitui o "reconhecimento de roupa" (histograma HSV) por identidade de rosto,
imune a roupa, luz e boné. Fallback do histograma: events.identity.identify().

Tudo local — nenhum rosto sai da máquina.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

FACE_THRESHOLD = float(os.getenv("FACE_THRESHOLD", "0.45"))  # cosseno ArcFace = mesma pessoa
FACE_MARGIN = float(os.getenv("FACE_MARGIN", "0.05"))        # separação mínima p/ decidir

_model = None
_lock = threading.Lock()


def _get_model():
    global _model  # pylint: disable=global-statement
    with _lock:
        if _model is None:
            from insightface.app import FaceAnalysis
            fa = FaceAnalysis(name=os.getenv("FACE_MODEL", "buffalo_l"),
                              providers=["CPUExecutionProvider"])
            fa.prepare(ctx_id=-1, det_size=(640, 640))
            # câmera de segurança: rosto pequeno/ângulo/noturno — detector permissivo
            fa.det_thresh = float(os.getenv("FACE_DET_THRESH", "0.3"))
            _model = fa
            logger.info("InsightFace carregado (buffalo_l, CPU)")
    return _model


def face_embed(crop_path: Path | str | None) -> np.ndarray | None:
    """Embedding 512d normalizado do maior rosto do recorte; None se não houver rosto."""
    if not crop_path:
        return None
    try:
        import cv2
        img = cv2.imread(str(crop_path))
        if img is None:
            return None
        h, w = img.shape[:2]
        if max(h, w) < 640:
            # recortes da câmera são pequenos: amplia p/ o detector achar rosto
            f = 640.0 / max(h, w)
            img = cv2.resize(img, (int(w * f) + 1, int(h * f) + 1),
                             interpolation=cv2.INTER_CUBIC)
        faces = _get_model().get(img)
        if not faces:
            return None
        best = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
        return np.asarray(best.normed_embedding, dtype=np.float32)
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.warning("face_embed falhou (%s)", err)
        return None


def match_face(emb: np.ndarray | None, known: list[dict],
               threshold: float = FACE_THRESHOLD,
               margin: float = FACE_MARGIN) -> dict | None:
    """Compara embedding com face_emb dos conhecidos.

    Retorna dict {person_id, name, score, second, decisive} ou None.
    `decisive` = melhor acima do limiar E com margem sobre o 2º.
    """
    if emb is None:
        return None
    scored: list[tuple[float, int, str]] = []
    for p in known:
        fb = p.get("face_emb")
        if not fb:
            continue
        k = np.frombuffer(fb, dtype=np.float32)
        if k.shape != emb.shape:
            continue
        s = float(np.dot(k, emb))
        if s >= threshold:
            scored.append((s, p["id"], p["name"]))
    if not scored:
        return None
    scored.sort(reverse=True)
    best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    return {"person_id": best[1], "name": best[2], "score": best[0],
            "second": second, "decisive": (best[0] - second) >= margin}
