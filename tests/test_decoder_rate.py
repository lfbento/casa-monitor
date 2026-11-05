from __future__ import annotations

from capture.decoder import H264StreamDecoder


def test_pts_rate_detects_realtime_playback() -> None:
    dec = H264StreamDecoder()

    for i in range(21):
        dec._note_pts(pts=float(i), wall=1000.0 + i)

    assert dec.pts_rate is not None
    assert abs(dec.pts_rate - 1.0) < 0.1
    assert dec.rate_out_of_sync is False


def test_pts_rate_detects_drain_above_realtime() -> None:
    dec = H264StreamDecoder()

    for i in range(21):
        dec._note_pts(pts=1.2 * i, wall=2000.0 + i)

    assert dec.pts_rate is not None and dec.pts_rate > 1.15
    assert dec.rate_out_of_sync is True


def test_pts_rate_waits_for_window() -> None:
    dec = H264StreamDecoder()

    dec._note_pts(pts=0.0, wall=0.0)
    dec._note_pts(pts=1.0, wall=1.0)

    assert dec.pts_rate is None
    assert dec.rate_out_of_sync is False
