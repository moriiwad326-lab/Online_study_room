"""MediaPipe Hands から21関節点×(x,y)×両手=84次元の手指特徴ベクトルを抽出する。

`MediaPipe_Hands.sample.py` はWebカメラでの可視化デモのみで特徴ベクトルを
返す関数を持たないため、LoCパイプライン（core.py）から呼び出せる形で
関数/クラス化したもの。
"""
import argparse
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import mediapipe as mp

NUM_LANDMARKS = 21
NUM_COORDS = 2   # x, y
NUM_HANDS = 2    # Left, Right
FEATURE_DIM = NUM_LANDMARKS * NUM_COORDS * NUM_HANDS  # 84

# 検出された手を常に Left→前半42次元 / Right→後半42次元 に割り当てる
_HAND_SLOT = {"Left": 0, "Right": 1}


class HandFeatureExtractor:
    """MediaPipe Hands をラップし、画像から84次元の手指特徴ベクトルを返す。"""

    def __init__(
        self,
        max_num_hands: int = NUM_HANDS,
        min_detection_confidence: float = 0.7,
        min_tracking_confidence: float = 0.7,
        static_image_mode: bool = False,
    ):
        self._hands = mp.solutions.hands.Hands(
            static_image_mode=static_image_mode,
            max_num_hands=max_num_hands,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )

    def extract(self, image_bgr: np.ndarray) -> np.ndarray:
        """BGR画像から84次元特徴ベクトルを取得する。

        レイアウト: [Left(21点×x,y)=42次元, Right(21点×x,y)=42次元]。
        該当する手が検出されなかったスロットは0埋めする。
        """
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        results = self._hands.process(rgb)

        features = np.zeros(FEATURE_DIM, dtype=np.float32)
        if not results.multi_hand_landmarks:
            return features

        for hand_landmarks, handedness in zip(
            results.multi_hand_landmarks, results.multi_handedness
        ):
            label = handedness.classification[0].label
            slot = _HAND_SLOT.get(label)
            if slot is None:
                continue
            offset = slot * NUM_LANDMARKS * NUM_COORDS
            for i, lm in enumerate(hand_landmarks.landmark):
                features[offset + i * NUM_COORDS] = lm.x
                features[offset + i * NUM_COORDS + 1] = lm.y

        return features

    def close(self):
        self._hands.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def extract_hand_features(
    image_bgr: np.ndarray, extractor: Optional[HandFeatureExtractor] = None
) -> np.ndarray:
    """単発の画像から84次元特徴ベクトルを取得するヘルパー関数。

    連続フレームを処理する場合は `HandFeatureExtractor` を1つ生成して
    使い回す方が効率的（extractor引数に渡す）。
    """
    if extractor is not None:
        return extractor.extract(image_bgr)

    with HandFeatureExtractor(static_image_mode=True) as one_shot_extractor:
        return one_shot_extractor.extract(image_bgr)


def main():
    parser = argparse.ArgumentParser(
        description="MediaPipe Hands で画像から84次元の手指特徴ベクトルを取得"
    )
    parser.add_argument("image", help="入力画像のパス")
    parser.add_argument("--output", help="保存先 npy ファイル（任意）")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    image_bgr = cv2.imread(str(image_path))
    if image_bgr is None:
        raise ValueError(f"画像を読み込めませんでした: {image_path}")

    features = extract_hand_features(image_bgr)
    print("features shape:", features.shape)
    print("features:", features)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(output_path, features)
        print(f"saved to {output_path}")


if __name__ == "__main__":
    main()
