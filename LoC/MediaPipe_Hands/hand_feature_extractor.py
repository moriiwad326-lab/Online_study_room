"""MediaPipe Hands から手指の姿勢・位置・運動特徴を抽出する。

`MediaPipe_Hands.sample.py` はWebカメラでの可視化デモのみで特徴ベクトルを
返す関数を持たないため、LoCパイプライン（core.py）から呼び出せる形で
関数/クラス化したもの。

特徴ベクトルのレイアウト（片手81次元 × Left/Right の2ブロック = 162次元）::

    [ 0: 60]  rel        landmark 1..20 の手首基準の相対座標 (x, y, z) × 20点。
                         手のサイズで正規化し、アスペクト比を補正済み。
                         landmark 0 は定義上つねに原点なので含めない。
    [60: 62]  wrist_xy   手首(landmark 0)の絶対位置 (x, y)。正規化画像座標。
    [62: 63]  scale      手のサイズ（手首→中指付け根の距離）。カメラからの距離の代理量。
    [63: 64]  flag       検出フラグ。検出=1.0 / 未検出=0.0。
    [64: 66]  wrist_vel  手首の絶対位置のフレーム間差分 (x, y)。手全体の移動量。
    [66: 81]  tip_vel    指先5点の相対座標のフレーム間差分 (x, y, z) × 5点。指の動き。

前半81次元がLeft、後半81次元がRight。未検出の手のブロックは全て0で、
`flag` が0であることによって「原点付近に手がある」状態と区別できる。

設計意図（なぜ生の座標をそのまま並べないか）は README の
「設計上の意思決定」を参照。
"""
import argparse
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np
import mediapipe as mp

NUM_LANDMARKS = 21
NUM_COORDS = 3   # x, y, z
NUM_HANDS = 2    # Left, Right

WRIST = 0                 # 相対座標の基準点
MIDDLE_FINGER_MCP = 9     # 手のサイズ算出に使う点（手首からの距離が安定している）
FINGERTIPS = (4, 8, 12, 16, 20)  # 親指〜小指の指先

# --- 片手ブロック内のオフセット ---
REL_OFFSET = 0
REL_DIM = (NUM_LANDMARKS - 1) * NUM_COORDS  # 60
WRIST_XY_OFFSET = REL_OFFSET + REL_DIM      # 60
WRIST_XY_DIM = 2
SCALE_OFFSET = WRIST_XY_OFFSET + WRIST_XY_DIM  # 62
FLAG_OFFSET = SCALE_OFFSET + 1                 # 63
STATIC_DIM = FLAG_OFFSET + 1                   # 64

VELOCITY_OFFSET = STATIC_DIM                              # 64
VELOCITY_DIM = WRIST_XY_DIM + len(FINGERTIPS) * NUM_COORDS  # 17
HAND_BLOCK_DIM = STATIC_DIM + VELOCITY_DIM                # 81

FEATURE_DIM = HAND_BLOCK_DIM * NUM_HANDS  # 162

# 検出された手を常に Left→前半ブロック / Right→後半ブロック に割り当てる
_HAND_SLOT = {"Left": 0, "Right": 1}

# 手が潰れて写った場合に相対座標が発散しないための下限
_MIN_SCALE = 1e-3

# フレーム間差分を取る対象（静的ブロック内のインデックス）。
# 手首の絶対位置(x, y) → 手全体の移動、指先の相対座標 → 指の動き。
_VELOCITY_SOURCE = np.array(
    [WRIST_XY_OFFSET, WRIST_XY_OFFSET + 1]
    + [
        REL_OFFSET + (landmark - 1) * NUM_COORDS + axis
        for landmark in FINGERTIPS
        for axis in range(NUM_COORDS)
    ],
    dtype=np.intp,
)


def encode_hand_block(landmarks: Sequence, aspect: float = 1.0) -> np.ndarray:
    """1つの手のランドマーク列から静的ブロック(64次元)を組み立てる。

    Args:
        landmarks: 長さ `NUM_LANDMARKS` の、`.x` / `.y` / `.z` を持つ要素の列
            （MediaPipe の `hand_landmarks.landmark` をそのまま渡せる）。
        aspect: 画像の幅/高さ。MediaPipe の x は幅で、y は高さで正規化されており、
            z は「x とほぼ同じスケール」と定義されている。非正方形の画像では
            そのままだと手の形が歪むため、x と z に aspect を掛けて等方な空間に揃える。

    Returns:
        shape (STATIC_DIM,) / dtype float32 のベクトル。
    """
    points = np.array(
        [(lm.x * aspect, lm.y, lm.z * aspect) for lm in landmarks], dtype=np.float32
    )

    relative = points[1:] - points[WRIST]

    # 手首→中指付け根の距離を手のサイズとみなす（xy平面上で測る）
    scale = float(np.linalg.norm(relative[MIDDLE_FINGER_MCP - 1, :2]))
    normalizer = max(scale, _MIN_SCALE)

    block = np.zeros(STATIC_DIM, dtype=np.float32)
    block[REL_OFFSET : REL_OFFSET + REL_DIM] = (relative / normalizer).reshape(-1)
    # 絶対位置は「画面のどこに手があるか」の情報なので、正規化前の座標を残す
    block[WRIST_XY_OFFSET] = landmarks[WRIST].x
    block[WRIST_XY_OFFSET + 1] = landmarks[WRIST].y
    block[SCALE_OFFSET] = scale
    block[FLAG_OFFSET] = 1.0
    return block


def compute_velocity(
    static: np.ndarray, previous_static: Optional[np.ndarray]
) -> np.ndarray:
    """静的ブロックの差分から速度ブロックを求める。

    直前フレームが無い場合、および対象の手がどちらかのフレームで未検出の場合は0を返す。
    未検出フレームは0ベクトルなので、そのまま差分を取ると手が現れた瞬間に
    巨大な偽の速度が立ってしまうため、両フレームで検出されている場合のみ差分を取る。

    Args:
        static: shape (NUM_HANDS, STATIC_DIM) の現フレームの静的ブロック。
        previous_static: 直前フレームの同形状の配列。無い場合は None。

    Returns:
        shape (NUM_HANDS, VELOCITY_DIM) / dtype float32 の配列。
    """
    velocity = np.zeros((NUM_HANDS, VELOCITY_DIM), dtype=np.float32)
    if previous_static is None:
        return velocity

    for slot in range(NUM_HANDS):
        if not (static[slot, FLAG_OFFSET] and previous_static[slot, FLAG_OFFSET]):
            continue
        velocity[slot] = (
            static[slot, _VELOCITY_SOURCE] - previous_static[slot, _VELOCITY_SOURCE]
        )
    return velocity


class HandFeatureExtractor:
    """MediaPipe Hands をラップし、画像から162次元の手指特徴ベクトルを返す。

    速度特徴のために直前フレームの状態を保持する**ステートフルなオブジェクト**である。
    別の動画・別の被験者の処理に移る際は `reset()` を呼ぶこと。
    """

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
        self._previous_static: Optional[np.ndarray] = None

    def reset(self):
        """速度計算用の直前フレーム状態を破棄する（連続していない映像に移るとき）。"""
        self._previous_static = None

    def extract(self, image_bgr: np.ndarray) -> np.ndarray:
        """BGR画像から162次元特徴ベクトルを取得する。

        レイアウトはモジュールのdocstringを参照。速度成分は直前に `extract` した
        フレームとの差分なので、最初の1フレームでは0になる。
        """
        height, width = image_bgr.shape[:2]
        aspect = (width / height) if height else 1.0

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        results = self._hands.process(rgb)

        static = np.zeros((NUM_HANDS, STATIC_DIM), dtype=np.float32)
        if results.multi_hand_landmarks and results.multi_handedness:
            for hand_landmarks, handedness in zip(
                results.multi_hand_landmarks, results.multi_handedness
            ):
                slot = _HAND_SLOT.get(handedness.classification[0].label)
                if slot is None:
                    continue
                static[slot] = encode_hand_block(hand_landmarks.landmark, aspect)

        velocity = compute_velocity(static, self._previous_static)
        self._previous_static = static

        features = np.zeros(FEATURE_DIM, dtype=np.float32)
        for slot in range(NUM_HANDS):
            base = slot * HAND_BLOCK_DIM
            features[base : base + STATIC_DIM] = static[slot]
            features[base + VELOCITY_OFFSET : base + HAND_BLOCK_DIM] = velocity[slot]
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
    """単発の画像から162次元特徴ベクトルを取得するヘルパー関数。

    `extractor` を渡さない場合は1枚限りの抽出となり、**速度成分は常に0**になる。
    連続フレームを処理する場合は `HandFeatureExtractor` を1つ生成して
    使い回すこと（効率のためだけでなく、速度特徴を得るために必須）。
    """
    if extractor is not None:
        return extractor.extract(image_bgr)

    with HandFeatureExtractor(static_image_mode=True) as one_shot_extractor:
        return one_shot_extractor.extract(image_bgr)


def main():
    parser = argparse.ArgumentParser(
        description="MediaPipe Hands で画像から162次元の手指特徴ベクトルを取得"
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
    print("(単一画像のため速度成分は0です)")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(output_path, features)
        print(f"saved to {output_path}")


if __name__ == "__main__":
    main()
