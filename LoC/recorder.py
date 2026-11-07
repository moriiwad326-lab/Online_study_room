"""LoC 計測中のカメラ映像と推定値をファイルに残す。

`LoC/server.py` が1接続（＝1回の計測）ごとに `SessionRecorder` を作り、フロントから届いた
フレームをすべて動画に、推定した LoC を CSV に書き出す。

保存先:
    <root>/<yyyymmdd>/<HHMMSS>.mp4   … 受け取ったフレーム（640px 前後・既定 5fps）
    <root>/<yyyymmdd>/<HHMMSS>.csv   … timestamp_ms, datetime, video_sec, loc

動画の時間はフロントの timestamp（撮影時刻）に合わせる。フレームの間隔が空いたら直前の
コマを繰り返して埋めるので、動画の t 秒目はおおよそ計測開始の t 秒後になる。ただし一時停止
などで `max_gap` 秒以上途切れたときは埋めずに詰める（数十分の静止画が入らないように）。
CSV の video_sec は、その LoC を出したフレームが動画の何秒目にあるかを表す。
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

DEFAULT_RECORD_ROOT = Path(__file__).resolve().parent / "recordings"

_OPEN_LOCK = threading.Lock()


class SessionRecorder:
    """1回の計測ぶんの動画と LoC の CSV を書く。フレームが1枚も来なければファイルを作らない。"""

    def __init__(self, root: Path, fps: float = 5.0, max_gap: float = 2.0,
                 started_at: Optional[datetime] = None):
        self.root = Path(root)
        self.fps = fps
        self.max_gap = max_gap
        self.started_at = started_at or datetime.now()

        self.video_path: Optional[Path] = None
        self.csv_path: Optional[Path] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self._csv_file = None
        self._csv = None
        self._size: Optional[tuple] = None   # (width, height)
        self._frames_written = 0
        self._last_frame: Optional[np.ndarray] = None
        self._last_ts: Optional[float] = None  # 秒（フロントの timestamp）
        self._offset = 0.0                     # 動画の秒 = timestamp - _offset

    # ---- 公開API ----

    def write_frame(self, frame: np.ndarray, timestamp_ms: int) -> None:
        """フレームを動画に追加する。timestamp_ms はフロントの撮影時刻（ミリ秒）。"""
        if frame is None or frame.size == 0:
            return
        if self._writer is None:
            self._open(frame)

        frame = self._fit(frame)
        ts = timestamp_ms / 1000.0
        if self._last_ts is None:
            self._offset = ts
        elif ts - self._last_ts > self.max_gap:
            # 長く途切れた区間は詰める（次のコマが直前のコマのすぐ後に来るようにずらす）
            self._offset += ts - self._last_ts - 1.0 / self.fps
        elif ts < self._last_ts:
            return  # 順番が前後したフレームは捨てる

        # このフレームが入るべきコマ番号まで、直前のコマで埋める
        target = int(round((ts - self._offset) * self.fps))
        while self._last_frame is not None and self._frames_written < target:
            self._writer.write(self._last_frame)
            self._frames_written += 1
        self._writer.write(frame)
        self._frames_written += 1

        self._last_frame = frame
        self._last_ts = ts

    def write_loc(self, timestamp_ms: int, loc: float) -> None:
        """推定した LoC を CSV に1行追加する。"""
        if self._csv is None:
            return
        ts = timestamp_ms / 1000.0
        video_sec = max(0.0, ts - self._offset)
        self._csv.writerow([
            timestamp_ms,
            datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
            f"{video_sec:.1f}",
            loc,
        ])
        self._csv_file.flush()

    def close(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        if self._csv_file is not None:
            self._csv_file.close()
            self._csv_file = self._csv = None

    def __enter__(self) -> "SessionRecorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 内部 ----

    def _open(self, frame: np.ndarray) -> None:
        day_dir = self.root / self.started_at.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        stem = self.started_at.strftime("%H%M%S")
        height, width = frame.shape[:2]
        self._size = (width, height)

        # 同じ秒に始まった別の計測と同じ名前にならないよう、作成までをまとめて1つずつ行う
        with _OPEN_LOCK:
            base = day_dir / stem
            n = 1
            while base.with_suffix(".mp4").exists() or base.with_suffix(".csv").exists():
                base = day_dir / f"{stem}_{n}"
                n += 1

            self.video_path = base.with_suffix(".mp4")
            self.csv_path = base.with_suffix(".csv")
            self._writer = cv2.VideoWriter(
                str(self.video_path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, self._size)
            if not self._writer.isOpened():
                raise RuntimeError(f"動画ファイルを作成できません: {self.video_path}")

            self._csv_file = open(self.csv_path, "w", newline="", encoding="utf-8")
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow(["timestamp_ms", "datetime", "video_sec", "loc"])
        self._csv_file.flush()

    def _fit(self, frame: np.ndarray) -> np.ndarray:
        """端末の向きが変わるなどで大きさが変わったら、最初のフレームの大きさに合わせる。"""
        height, width = frame.shape[:2]
        if (width, height) != self._size:
            frame = cv2.resize(frame, self._size)
        return frame
