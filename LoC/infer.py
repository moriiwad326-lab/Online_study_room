"""学習済み LoCModel で集中度をリアルタイム推定する。

`build_features.py` が学習時に行うのと同じ前処理（ConvNeXt 768次元 + Hands 162次元）を
1フレームずつ行い、直近 seq_len フレームぶんを LSTM に流して LoC を出す。

重要: 手の速度特徴は「直前に処理したフレームとの差分」なので、**学習時のサンプリング
間隔（dataset_creation の --rate）と推論時の間隔を揃える**こと。揃えないと速度成分の
スケールが変わり、そのまま精度に効く。`--interval` で秒間隔を指定する。

使い方:
    python LoC/infer.py --source 0 --checkpoint LoC/checkpoints/loc_model.pt --interval 5
    python LoC/infer.py --source video.mp4 --no-display
"""

from __future__ import annotations

import argparse
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
import torch

from ConvNeXT.image_feature_extractor import ImageFeatureExtractor
from MediaPipe_Hands.hand_feature_extractor import HandFeatureExtractor
from core import build_model
from train import DEFAULT_CHECKPOINT


class LoCPredictor:
    """フレームを1枚ずつ与えると、窓が埋まり次第 LoC（1〜5）を返す推論器。

    `HandFeatureExtractor` と同様にステートフル。別の映像に移るときは `reset()` を呼ぶ。
    """

    def __init__(
        self,
        checkpoint_path: str | Path = DEFAULT_CHECKPOINT,
        device: Optional[str] = None,
    ):
        self.device = torch.device(device) if device else torch.device(
            "cuda" if torch.cuda.is_available() else "cpu"
        )
        checkpoint = torch.load(Path(checkpoint_path), map_location=self.device)
        self.seq_len = int(checkpoint.get("seq_len", 20))
        self.progress = 0

        self.model = build_model(dropout=float(checkpoint.get("dropout", 0.2)))
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.eval().to(self.device)

        self.image_extractor = ImageFeatureExtractor(device=str(self.device))
        self.hand_extractor = HandFeatureExtractor()
        self._image_buffer: deque = deque(maxlen=self.seq_len)
        self._hand_buffer: deque = deque(maxlen=self.seq_len)

    @property
    def warmup_remaining(self) -> int:
        """窓が埋まるまでに、あと何フレーム必要か。"""
        return max(0, self.seq_len - len(self._image_buffer))

    def reset(self):
        """窓と速度計算の状態を捨てる（別の映像・別の被験者に移るとき）。"""
        self._image_buffer.clear()
        self._hand_buffer.clear()
        self.hand_extractor.reset()

    def push(self, frame_bgr: np.ndarray) -> Optional[float]:
        """1フレーム投入する。窓が埋まっていなければ None を返す。"""
        self._image_buffer.append(self.image_extractor.extract(frame_bgr))
        self._hand_buffer.append(self.hand_extractor.extract(frame_bgr))

        if len(self._image_buffer) < self.seq_len:
            return None

        img_feat = torch.from_numpy(np.stack(self._image_buffer)).unsqueeze(0).to(self.device)
        hand_feat = torch.from_numpy(np.stack(self._hand_buffer)).unsqueeze(0).to(self.device)
        with torch.no_grad():
            prediction = self.model(img_feat, hand_feat)
        return float(prediction.item())

    def close(self):
        self.image_extractor.close()
        self.hand_extractor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def open_source(source: str) -> cv2.VideoCapture:
    """数値ならカメラ番号、そうでなければ動画ファイルとして開く。"""
    capture = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not capture.isOpened():
        raise RuntimeError(f"映像ソースを開けませんでした: {source}")
    return capture


def iter_sampled_frames(capture: cv2.VideoCapture, interval: float, is_camera: bool):
    """interval 秒ごとのフレームを1枚ずつ返す。

    カメラは**実時間**、動画ファイルは**動画内時間**で間引く。学習データは
    `convert_frame_from_video.py --rate` によって動画内の秒間隔で抜かれているので、
    ファイル入力を実時間で間引くと、デコードが実時間より速い分だけ間隔が広がり、
    手の速度特徴のスケールが学習時とずれる（長い動画ではウォームアップすら終わらない）。
    """
    if is_camera:
        next_sample_at = 0.0
        while True:
            ok, frame = capture.read()
            if not ok:
                return
            now = time.monotonic()
            if now >= next_sample_at:
                next_sample_at = now + interval
                yield frame
        return

    fps = capture.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:  # コンテナによっては fps を取れないので既定値に落とす
        fps = 30.0

    # 学習データを作る convert_frame_from_video.py は CAP_PROP_POS_MSEC で秒シークして
    # いるので、推論側も再生位置（動画内時間）で合わせる。iPhone の MOV のような可変
    # フレームレートでは fps が平均値でしかなく、フレーム数換算だと間隔がずれる。
    next_sample_at = 0.0
    index = 0
    while True:
        # grab() はデコードしないので、サンプルしないフレームを安く読み飛ばせる
        if not capture.grab():
            return
        index += 1
        position = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if position <= 0.0:  # POS_MSEC を返さないコンテナ向けのフォールバック
            position = index / fps
        if position >= next_sample_at:
            ok, frame = capture.retrieve()
            if not ok:
                return
            next_sample_at = position + interval
            yield frame


def run(
    source: str = "0",
    checkpoint_path: str | Path = DEFAULT_CHECKPOINT,
    interval: float = 5.0,
    display: bool = True,
    device: Optional[str] = None,
) -> None:
    capture = open_source(source)
    is_camera = source.isdigit()
    try:
        with LoCPredictor(checkpoint_path, device=device) as predictor:
            print(f"device={predictor.device}, seq_len={predictor.seq_len}, "
                  f"interval={interval}s（{'実時間' if is_camera else '動画内時間'}）"
                  f" / 'q' or Esc で終了", flush=True)
            latest_loc: Optional[float] = None

            for frame in iter_sampled_frames(capture, interval, is_camera):
                loc = predictor.push(frame)
                if loc is not None:
                    latest_loc = loc
                    predictor.progress += 1
                    print(f"[{predictor.progress * interval + predictor.seq_len}s] LoC = {loc:.2f}", flush=True)
                else:
                    print(f"ウォームアップ中（あと {predictor.warmup_remaining} フレーム）",
                          flush=True)

                if display:
                    text = "LoC: --" if latest_loc is None else f"LoC: {latest_loc:.2f}"
                    cv2.putText(frame, text, (16, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                                (0, 255, 0), 3, cv2.LINE_AA)
                    cv2.imshow("LoC", frame)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                        break

            if predictor.progress == 0:
                print(f"LoC を1度も出力できませんでした: ウォームアップに必要な "
                      f"{predictor.seq_len} サンプル（= {predictor.seq_len * interval:.0f} 秒ぶん）"
                      f"より映像が短いか、--interval が大きすぎます")
    finally:
        capture.release()
        if display:
            cv2.destroyAllWindows()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="学習済み LoCModel で集中度を推定する")
    parser.add_argument("--source", default="0", help="カメラ番号（既定0）または動画ファイル")
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--interval", type=float, default=5.0,
                        help="サンプリング間隔（秒）。学習時の --rate に揃える")
    parser.add_argument("--no-display", action="store_true", help="OpenCVウィンドウを開かない")
    parser.add_argument("--device", default=None, help="cpu / cuda（既定は自動判定）")
    args = parser.parse_args(argv)

    run(
        source=args.source,
        checkpoint_path=args.checkpoint,
        interval=args.interval,
        display=not args.no_display,
        device=args.device,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
