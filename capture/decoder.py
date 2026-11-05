"""Decodificador H.264 (Annex-B) -> frames numpy, via PyAV.

O transcoder do Miloco manda chunks de stream elementar H.264; o Parser do
PyAV cuida de agrupar NALs parciais em pacotes válidos entre chamadas.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional

import av
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    image: np.ndarray      # BGR (formato OpenCV)
    pts: float             # timestamp em segundos (relativo ao stream)
    seq: int
    arrival_mono: float    # monotonic no instante em que o frame ficou disponível
    arrival_wall: float    # time.time() no mesmo instante (horário de observação)
    captured_at: Optional[datetime] = None   # instante gravado no OSD (None = ilegível)
    capture_age_s: Optional[float] = None    # arrival_wall - captured_at


class H264StreamDecoder:
    def __init__(self):
        self._codec = av.CodecContext.create("h264", "r")
        self._seq = 0
        self._size: tuple[int, int] | None = None
        # taxa de reprodução: Δpts / Δwall, reiniciada a cada janela
        self._rate_pts0: float | None = None
        self._rate_wall0: float | None = None
        self.pts_rate: float | None = None
        self.rate_out_of_sync: bool = False

    def _note_pts(self, pts: float, wall: float) -> None:
        """Mede a velocidade de reprodução sem depender do OSD.

        Em 1× o conteúdo avança como o relógio (taxa ≈ 1). Taxa persistentemente
        diferente de 1 indica backlog sendo drenado (ou reprodução acelerada);
        não diz a idade absoluta — isso é papel do OSD.
        """
        if self._rate_pts0 is None or self._rate_wall0 is None:
            self._rate_pts0, self._rate_wall0 = pts, wall
            return
        dw = wall - self._rate_wall0
        if dw <= 0:
            return
        if dw >= 10.0:
            rate = (pts - self._rate_pts0) / dw
            self.pts_rate = rate
            self.rate_out_of_sync = rate < 0.9 or rate > 1.1
            self._rate_pts0, self._rate_wall0 = pts, wall

    def feed(self, data: bytes) -> Iterator[Frame]:
        """Alimenta um chunk do stream e itera os frames completos decodificados.

        Em PyAV 19 o parser embutido (CodecContext.parse) agrupa NALs parciais
        em pacotes válidos entre chamadas.
        """
        for packet in self._codec.parse(data):
            try:
                decoded = self._codec.decode(packet)
            except av.error.InvalidDataError as err:
                logger.debug("chunk inválido descartado: %s", err)
                continue
            for frame in decoded:
                img = frame.to_ndarray(format="bgr24")
                self._size = (frame.width, frame.height)
                t = float(frame.time) if frame.time is not None else 0.0
                self._seq += 1
                wall = time.time()
                self._note_pts(t, wall)
                yield Frame(
                    image=img,
                    pts=t,
                    seq=self._seq,
                    arrival_mono=time.monotonic(),
                    arrival_wall=wall,
                )

    @property
    def size(self) -> tuple[int, int] | None:
        return self._size
