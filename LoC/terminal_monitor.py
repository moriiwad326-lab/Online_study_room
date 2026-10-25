"""Webカメラの LoC をターミナル上でライブ表示する。

`infer.py` の `LoCPredictor`（保存済みチェックポイント）をそのまま使い、OpenCV ウィンドウの
代わりにターミナルへ次を描画し続ける。

- カメラ映像の ASCII プレビュー（画角の確認用。学習データは机の真上〜斜め上からの画角）
- 現在の LoC（0〜100）のゲージと、直近の推移のスパークライン
- ウォームアップの進み具合、次のサンプルまでの秒数、手の検出状況

推論は `--interval` 秒ごと（学習時の `--rate` と揃える。既定 5 秒）で、プレビューだけは
それより速く更新する。最初の LoC が出るまで seq_len × interval 秒（既定 12 × 5 = 60 秒）かかる。

使い方:
    python LoC/terminal_monitor.py
    python LoC/terminal_monitor.py --source 1 --checkpoint LoC/checkpoints/first_train/model.pt
    python LoC/terminal_monitor.py --no-preview      # ゲージと推移だけ

終了は q / Esc / Ctrl+C。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections import deque
from pathlib import Path
from typing import List, Optional

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import cv2
import numpy as np

from MediaPipe_Hands.hand_feature_extractor import FLAG_OFFSET, HAND_BLOCK_DIM
from core import LOC_MAX, LOC_MIN
from infer import LoCPredictor, open_source
from train import DEFAULT_CHECKPOINT

try:  # Windows でのキー入力（q / Esc で終了）
    import msvcrt
except ImportError:  # pragma: no cover - Windows 以外は Ctrl+C のみ
    msvcrt = None

ASCII_RAMP = " .:-=+*#%@"
SPARK_CHARS = "▁▂▃▄▅▆▇█"

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"


def enable_ansi() -> None:
    """Windows の旧コンソールでも ANSI エスケープを解釈させる。"""
    if os.name == "nt":
        os.system("")
    # リダイレクト時に cp932 で █ などを出せず落ちるのを防ぐ
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def loc_color(loc: float) -> str:
    """低い=赤、中間=黄、高い=緑。"""
    if loc < 25:
        return "\x1b[31m"
    if loc < 60:
        return "\x1b[33m"
    return "\x1b[32m"


def ascii_preview(frame_bgr: np.ndarray, width: int, max_height: int) -> List[str]:
    """フレームをグレースケールの ASCII アートにする（文字の縦横比 ≒ 2:1 を補正）。"""
    h, w = frame_bgr.shape[:2]
    height = max(1, min(max_height, int(width * h / w * 0.5)))
    width = max(1, min(width, int(height * w / h * 2)))
    gray = cv2.cvtColor(cv2.resize(frame_bgr, (width, height), interpolation=cv2.INTER_AREA),
                        cv2.COLOR_BGR2GRAY)
    indices = (gray.astype(np.float32) / 256 * len(ASCII_RAMP)).astype(int)
    return ["".join(ASCII_RAMP[i] for i in row) for row in indices]


def gauge(loc: Optional[float], width: int) -> str:
    if loc is None:
        return DIM + "░" * width + RESET + "  --"
    ratio = (loc - LOC_MIN) / (LOC_MAX - LOC_MIN)
    filled = int(round(ratio * width))
    return (loc_color(loc) + "█" * filled + RESET + DIM + "░" * (width - filled) + RESET
            + f"  {BOLD}{loc_color(loc)}{loc:.1f}{RESET} / {LOC_MAX:.0f}")


def sparkline(history: List[float], width: int) -> str:
    if not history:
        return DIM + "（まだ推定値がありません）" + RESET
    out = []
    for loc in history[-width:]:
        ratio = (loc - LOC_MIN) / (LOC_MAX - LOC_MIN)
        out.append(loc_color(loc) + SPARK_CHARS[min(len(SPARK_CHARS) - 1, int(ratio * len(SPARK_CHARS)))])
    return "".join(out) + RESET


def progress_bar(done: int, total: int, width: int) -> str:
    filled = int(width * done / total) if total else width
    return "█" * filled + DIM + "░" * (width - filled) + RESET


def hands_status(hand_feat: Optional[np.ndarray]) -> str:
    if hand_feat is None:
        return "--"
    left = hand_feat[FLAG_OFFSET] > 0.5
    right = hand_feat[HAND_BLOCK_DIM + FLAG_OFFSET] > 0.5
    mark = lambda ok: "\x1b[32m●" + RESET if ok else DIM + "○" + RESET  # noqa: E731
    return f"左 {mark(left)}  右 {mark(right)}"


def key_pressed_quit() -> bool:
    if msvcrt is None:
        return False
    while msvcrt.kbhit():
        if msvcrt.getwch() in ("q", "Q", "\x1b"):
            return True
    return False


def render(
    *,
    preview: Optional[List[str]],
    latest_loc: Optional[float],
    history: List[float],
    predictor: LoCPredictor,
    interval: float,
    next_in: float,
    elapsed: float,
    hand_feat: Optional[np.ndarray],
    checkpoint_name: str,
    cols: int,
) -> str:
    bar_width = max(10, min(50, cols - 20))
    lines = [f"{BOLD}LoC モニター{RESET}  {DIM}{checkpoint_name} / device={predictor.device} / "
             f"interval={interval:g}s / q・Esc・Ctrl+C で終了{RESET}", ""]
    if preview:
        lines += preview
        lines.append("")

    lines.append(f"集中度  {gauge(latest_loc, bar_width)}")
    remaining = predictor.warmup_remaining
    if remaining:
        done = predictor.seq_len - remaining
        lines.append(f"準備中  {progress_bar(done, predictor.seq_len, bar_width)}  "
                     f"{done}/{predictor.seq_len}（最初の推定まで約 {remaining * interval:.0f} 秒）")
    else:
        lines.append(f"推移    {sparkline(history, bar_width)}")
    lines.append(f"手検出  {hands_status(hand_feat)}    次のサンプルまで {max(0.0, next_in):4.1f}s"
                 f"    経過 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}")
    # 各行末を消してから改行し、前回の描画の残りを消す
    return "\x1b[H" + "\x1b[K\n".join(lines) + "\x1b[K\n\x1b[J"


def run(
    source: str = "0",
    checkpoint_path: str | Path = DEFAULT_CHECKPOINT,
    interval: float = 5.0,
    preview: bool = True,
    fps: float = 10.0,
    device: Optional[str] = None,
) -> None:
    enable_ansi()
    print("モデルを読み込み中...", flush=True)
    capture = open_source(source)
    history: deque = deque(maxlen=200)
    latest_loc: Optional[float] = None
    hand_feat: Optional[np.ndarray] = None

    try:
        with LoCPredictor(checkpoint_path, device=device) as predictor:
            sys.stdout.write("\x1b[?25l\x1b[2J")  # カーソルを隠して画面をクリア
            started = time.monotonic()
            next_sample_at = started
            next_draw_at = started

            while True:
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError("カメラからフレームを取得できませんでした"
                                       "（他のアプリが使用中か、プライバシー設定でブロックされている可能性）")
                now = time.monotonic()

                if now >= next_sample_at:
                    next_sample_at = now + interval
                    loc = predictor.push(frame)
                    hand_feat = predictor.latest_hand_features
                    if loc is not None:
                        latest_loc = loc
                        history.append(loc)

                if now >= next_draw_at:
                    next_draw_at = now + 1.0 / fps
                    cols, rows = shutil.get_terminal_size((100, 40))
                    lines_ascii = None
                    if preview:
                        # ステータス表示ぶん（約7行）を残してプレビューに使う
                        lines_ascii = ascii_preview(frame, max(10, cols - 1), max(4, rows - 9))
                    sys.stdout.write(render(
                        preview=lines_ascii,
                        latest_loc=latest_loc,
                        history=list(history),
                        predictor=predictor,
                        interval=interval,
                        next_in=next_sample_at - now,
                        elapsed=now - started,
                        hand_feat=hand_feat,
                        checkpoint_name=Path(checkpoint_path).name,
                        cols=cols,
                    ))
                    sys.stdout.flush()

                if key_pressed_quit():
                    break
    except KeyboardInterrupt:
        pass
    finally:
        capture.release()
        sys.stdout.write("\x1b[?25h" + RESET + "\n")  # カーソルを戻す
        sys.stdout.flush()
        if history:
            values = np.array(history)
            print(f"LoC: {len(values)} 回推定 / 平均 {values.mean():.2f} / "
                  f"最小 {values.min():.2f} / 最大 {values.max():.2f}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Webカメラの LoC をターミナルにライブ表示する")
    parser.add_argument("--source", default="0", help="カメラ番号（既定0）")
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--interval", type=float, default=5.0,
                        help="推論のサンプリング間隔（秒）。学習時の --rate に揃える")
    parser.add_argument("--no-preview", action="store_true", help="ASCII プレビューを出さない")
    parser.add_argument("--fps", type=float, default=10.0, help="画面の更新頻度")
    parser.add_argument("--device", default=None, help="cpu / cuda（既定は自動判定）")
    args = parser.parse_args(argv)

    run(
        source=args.source,
        checkpoint_path=args.checkpoint,
        interval=args.interval,
        preview=not args.no_preview,
        fps=args.fps,
        device=args.device,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
