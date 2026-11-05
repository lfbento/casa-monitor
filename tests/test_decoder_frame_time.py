from __future__ import annotations

import time
from fractions import Fraction

import av
import numpy as np

from capture.decoder import H264StreamDecoder


def _encoded_packets(count: int = 5) -> list[bytes]:
    ctx = av.CodecContext.create("libx264", "w")
    ctx.width, ctx.height, ctx.pix_fmt = 64, 64, "yuv420p"
    ctx.time_base = Fraction(1, 25)
    ctx.framerate = 25
    packets: list[bytes] = []
    for i in range(count):
        arr = np.full((64, 64, 3), 128, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(arr, format="bgr24").reformat(format="yuv420p")
        frame.pts = i
        packets.extend(bytes(pkt) for pkt in ctx.encode(frame))
    packets.extend(bytes(pkt) for pkt in ctx.encode(None))
    return packets


def test_decoded_frame_carries_arrival_times() -> None:
    decoder = H264StreamDecoder()
    frames = []
    before = time.time()
    for chunk in _encoded_packets():
        frames.extend(decoder.feed(chunk))
    after = time.time()

    assert frames, "o chunk sintético deveria produzir ao menos um frame"
    for frame in frames:
        assert before - 5 <= frame.arrival_wall <= after + 5
        assert isinstance(frame.arrival_mono, float)
        assert isinstance(frame.arrival_wall, float)

    arrivals = [frame.arrival_mono for frame in frames]
    assert arrivals == sorted(arrivals)
