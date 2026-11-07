"""recorder.py の SessionRecorder（計測中の録画と LoC の CSV 保存）を検証する。"""

import csv
from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from recorder import SessionRecorder  # noqa: E402

STARTED = datetime(2026, 11, 3, 14, 5, 9)


def frame(value=0, size=(64, 48)):
    w, h = size
    return np.full((h, w, 3), value, dtype=np.uint8)


def frame_count(path):
    cap = cv2.VideoCapture(str(path))
    try:
        return int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()


def test_saves_video_and_csv_under_date_folder(tmp_path):
    with SessionRecorder(tmp_path, fps=5, started_at=STARTED) as rec:
        for i in range(5):
            rec.write_frame(frame(i * 40), 1_000_000 + i * 200)
        rec.write_loc(1_000_800, 72.5)

    assert rec.video_path == tmp_path / "20261103" / "140509.mp4"
    assert rec.csv_path == tmp_path / "20261103" / "140509.csv"
    assert frame_count(rec.video_path) == 5

    rows = list(csv.reader(rec.csv_path.open(encoding="utf-8")))
    assert rows[0] == ["timestamp_ms", "datetime", "video_sec", "loc"]
    assert rows[1][0] == "1000800"
    assert rows[1][2] == "0.8"
    assert rows[1][3] == "72.5"


def test_no_files_without_frames(tmp_path):
    with SessionRecorder(tmp_path, started_at=STARTED) as rec:
        rec.write_loc(0, 50.0)
    assert rec.video_path is None
    assert not (tmp_path / "20261103").exists()


def test_does_not_overwrite_existing_recording(tmp_path):
    for _ in range(2):
        with SessionRecorder(tmp_path, started_at=STARTED) as rec:
            rec.write_frame(frame(), 0)
    assert rec.video_path.name == "140509_1.mp4"


def test_fills_short_gaps_and_skips_long_pauses(tmp_path):
    with SessionRecorder(tmp_path, fps=5, max_gap=2.0, started_at=STARTED) as rec:
        rec.write_frame(frame(), 0)
        rec.write_frame(frame(), 1000)    # 1秒空いた → 間の4コマを埋めて計6コマ
        rec.write_frame(frame(), 61_000)  # 60秒の一時停止 → 埋めずに1コマだけ
        rec.write_loc(61_000, 40.0)
    assert frame_count(rec.video_path) == 7
    rows = list(csv.reader(rec.csv_path.open(encoding="utf-8")))
    assert rows[1][2] == "1.2"


def test_resizes_frames_of_different_size(tmp_path):
    with SessionRecorder(tmp_path, started_at=STARTED) as rec:
        rec.write_frame(frame(size=(64, 48)), 0)
        rec.write_frame(frame(size=(48, 64)), 200)
    assert frame_count(rec.video_path) == 2
