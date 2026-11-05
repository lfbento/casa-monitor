"""Decodificador H.264 (Annex-B) -> frames numpy, via PyAV.

O transcoder do Miloco manda chunks de stream elementar H.264; o Parser do
PyAV cuida de agrupar NALs parciais em pacotes válidos entre chamadas.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterator

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


class H264StreamDecoder:
    def __init__(self):
        self._codec = av.CodecContext.create("h264", "r")
        self._seq = 0
        self._size: tuple[int, int] | None = None

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
                yield Frame(
                    image=img,
                    pts=t,
                    seq=self._seq,
                    arrival_mono=time.monotonic(),
                    arrival_wall=time.time(),
                )

    @property
    def size(self) -> tuple[int, int] | None:
        return self._size
