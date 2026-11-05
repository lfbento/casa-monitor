from __future__ import annotations

import numpy as np

from detect.tracker import TrackBook, TrackState
from detect.yolo_engine import Detection


def _det(track_id: int = 1, cx: float = 0.0, cy: float = 0.0) -> Detection:
    return Detection(track_id=track_id, cls_name="person", conf=0.9,
                     x1=cx - 5, y1=cy - 5, x2=cx + 5, y2=cy + 5)


def test_speed_ignores_long_processing_gaps() -> None:
    st = TrackState(track_id=1, cls_name="person", first_seen=0.0, last_seen=0.0)
    st.update(_det(cx=0), 0.0)
    st.update(_det(cx=100), 20.0)

    assert st.speed_px_s == 0.0
    assert st.total_path_px == 100.0


def test_track_state_is_discarded_after_long_gap() -> None:
    book = TrackBook(gap_reset_seconds=10.0)
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    book.update([_det()], frame, 0.0)
    old = book.tracks[1]
    old.zones_seen.add("ZONA_RUA")

    book.update([_det(cx=50)], frame, 30.0)
    fresh = book.tracks[1]

    assert fresh is not old
    assert len(fresh.history) == 1
    assert fresh.speed_px_s == 0.0
    assert not fresh.zones_seen
