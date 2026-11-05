"""Leitura do instante de captura gravado no OSD do frame (watermark).

O pipeline só conhece o instante de *chegada* (`arrival_wall`). O único
instante de captura em banda é o texto que a câmera desenha no topo-esquerdo
do frame: ``YYYY/MM/DD HH:MM:SS``. Este módulo lê esse texto sem dependência
de OCR: a fonte é fixa e monoespaçada, então as células têm posição fixa e
cada glifo é classificado por correlação com templates extraídos da própria
fonte (`osd_templates.png`).

Limitações deliberadas:
- resolução fixa 2560×1440 (a câmera atual); outra resolução falha alto em vez
  de inventar um horário;
- glifo ambíguo ou texto fora do formato → ``None`` + motivo, nunca um valor
  aproximado (usar ``arrival_wall`` como se fosse captura é o bug que este
  módulo existe para corrigir).
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ordem dos glifos na imagem de templates
TEMPLATE_ORDER = "0123456789/:"
TEMPLATES_PATH = Path(__file__).with_name("osd_templates.png")

# geometria do OSD em 2560×1440: início de cada célula, largura e faixa vertical
OSD_STARTS = (126, 165, 206, 246, 291, 330, 370, 411,
              445, 485, 565, 607, 656, 685, 724, 776, 806, 845)
CELL_W = 34
OSD_Y0, OSD_Y1 = 48, 100
BASE_W, BASE_H = 2560, 1440     # geometria de referência dos templates
CELL_H = OSD_Y1 - OSD_Y0
MIN_SCORE = 0.22   # pior correlação observada em frames reais: 0.34


def _prep(cell: np.ndarray) -> np.ndarray:
    """Remove o fundo local e normaliza — o glifo é o que sobra em alto relevo."""
    c = cell.astype(np.float32)
    f = c - cv2.GaussianBlur(c, (0, 0), 11)
    return (f - f.mean()) / (f.std() + 1e-6)


def _load_templates(path: Path = TEMPLATES_PATH) -> dict[str, np.ndarray]:
    sheet = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if sheet is None:
        raise RuntimeError(f"templates do OSD não encontrados: {path}")
    need = CELL_W * len(TEMPLATE_ORDER)
    if sheet.shape[0] < OSD_Y1 - OSD_Y0 or sheet.shape[1] < need:
        raise RuntimeError(f"templates do OSD inválidos: {sheet.shape} (esperado >= x{need})")
    cells = sheet[0:OSD_Y1 - OSD_Y0, :need]
    return {
        ch: _prep(cells[:, i * CELL_W:(i + 1) * CELL_W])
        for i, ch in enumerate(TEMPLATE_ORDER)
    }


_TEMPLATES = _load_templates()


def _match_cell(image: np.ndarray, y0: int, y1: int, x0: int,
                cell_w: int, _offset: int) -> tuple[str, float]:
    if x0 < 0 or x0 + cell_w > image.shape[1]:
        return "?", -2.0
    cell = image[y0:y1, x0:x0 + cell_w]
    if cell.size == 0:
        return "?", -2.0
    if cell.shape != (CELL_H, CELL_W):
        cell = cv2.resize(cell, (CELL_W, CELL_H), interpolation=cv2.INTER_AREA)
    feat = _prep(cell)
    best, best_score = "?", -2.0
    for ch, templ in _TEMPLATES.items():
        score = float(np.mean(feat * templ))
        if score > best_score:
            best, best_score = ch, score
    return best, best_score


def read_osd(image: np.ndarray) -> tuple[Optional[datetime], Optional[str]]:
    """Lê ``YYYY/MM/DD HH:MM:SS`` do OSD.

    Retorna ``(datetime local, None)`` ou ``(None, motivo)``. O motivo é para
    diagnóstico (contador de falhas no watchdog); nunca vira um horário.
    """
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = image.shape[:2]
    if width < 960 or height < 540 or abs(width / height - 16 / 9) > 0.1:
        return None, f"resolucao {image.shape[:2]}"

    # o OSD é desenhado antes do transcode: escala junto com o frame
    sx, sy = width / BASE_W, height / BASE_H
    y0, y1 = round(OSD_Y0 * sy), round(OSD_Y1 * sy)
    cell_w = max(8, round(CELL_W * sx))

    chars: list[str] = []
    pad = max(4, round(12 * sx))  # a fonte é proporcional: "1" desloca a célula
    for x in OSD_STARTS:
        x0 = round(x * sx)
        best, best_score = _match_cell(image, y0, y1, x0, cell_w, 0)
        if best_score < 0.55:  # célula deslocada (glifo estreito antes): procura perto
            for offset in range(-pad, pad + 1):
                cand, score = _match_cell(image, y0, y1, x0 + offset, cell_w, offset)
                if score > best_score:
                    best, best_score = cand, score
        if best_score < MIN_SCORE:
            return None, f"glifo ilegivel {x} ({best_score:.2f})"
        chars.append(best)

    text = "".join(chars)
    if text[4] != "/" or text[7] != "/" or text[12] != ":" or text[15] != ":":
        return None, f"estrutura invalida {text}"
    try:
        parsed = datetime.strptime(
            f"{text[0:4]}-{text[5:7]}-{text[8:10]} "
            f"{text[10:12]}:{text[13:15]}:{text[16:18]}",
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return None, f"data invalida {text}"
    if not 2020 <= parsed.year <= 2100:
        return None, f"ano fora da faixa {text}"
    return parsed.astimezone(), None


class OsdClock:
    """Lê o OSD no máximo a cada ``interval_s`` e mantém o último instante.

    A idade é sempre recalculada contra o instante de chegada do frame atual,
    então continua avançando entre leituras — o que se cacheia é o horário
    absoluto gravado, não a idade.
    """

    def __init__(self, interval_s: float = 30.0):
        self.interval_s = max(1.0, float(interval_s))
        self._last_read_mono = -1e9
        self._captured: Optional[datetime] = None
        self._age_at_read: Optional[float] = None
        self.failures = 0
        self.last_error: Optional[str] = None

    def reset(self) -> None:
        """Esquece a última leitura (usado após reconectar o stream)."""
        self._last_read_mono = -1e9
        self._captured = None
        self._age_at_read = None

    def observe(self, frame, arrival_wall: float,
                monotonic: Optional[float] = None) -> tuple[Optional[datetime], Optional[float]]:
        import time

        mono = time.monotonic() if monotonic is None else monotonic
        if mono - self._last_read_mono >= self.interval_s:
            self._last_read_mono = mono
            captured, error = read_osd(frame)
            if captured is None:
                self.failures += 1
                self.last_error = error
                if self.failures == 1 or self.failures % 10 == 0:
                    logger.warning("OSD ilegível (%s) — idade de captura desconhecida", error)
                self._captured = None
                self._age_at_read = None
                # frame inicial/artefato de warm-up: tenta de novo em poucos segundos
                self._last_read_mono = mono - self.interval_s + min(5.0, self.interval_s)
            else:
                self._captured = captured
                self._age_at_read = arrival_wall - captured.timestamp()
                self.last_error = None
        if self._captured is None:
            return None, None
        # O conteúdo avança junto com a chegada: a idade medida no instante da
        # leitura continua valendo entre leituras (não cresce até o próximo OSD).
        return self._captured, self._age_at_read
