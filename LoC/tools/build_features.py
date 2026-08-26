"""フレーム画像群から学習用の特徴量（画像768次元 + 手162次元）を事前キャッシュする。

`dataset_creation.py` が作った `output/<動画名>/`（フレーム画像 + labels.csv）を入力に、
同じディレクトリへ `features.npz` を書き出す。ConvNeXt は freeze して使う前提なので、
毎エポック画像を読み直す必要はなく、ここで1度だけ通しておくことで学習が大幅に速くなる。

features.npz の中身:
    frames : (N,)      フレーム番号（= 抽出時の秒数）。昇順
    image  : (N, 768)  ConvNeXt-Tiny の特徴（float32）
    hand   : (N, 162)  MediaPipe Hands の特徴（float32）

手の速度特徴は「直前に処理したフレームとの差分」なので、**フレーム番号の昇順に、
1動画ぶんを連続して**処理し、動画が変わるところで `HandFeatureExtractor.reset()` を呼ぶ。

使い方:
    # 1動画ぶん（labels.csv のあるディレクトリ）
    python LoC/tools/build_features.py output/<動画名>
    # 出力ルートを渡すと配下の動画ディレクトリをまとめて処理する
    python LoC/tools/build_features.py output --batch-size 16
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = SCRIPT_DIR.parent
for path in (str(SCRIPT_DIR), str(PACKAGE_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from convert_LoC_from_frame import extract_frame_number, list_image_files

from ConvNeXT.image_feature_extractor import IMAGE_FEATURE_DIM, ImageFeatureExtractor
from MediaPipe_Hands.hand_feature_extractor import FEATURE_DIM as HAND_FEATURE_DIM
from MediaPipe_Hands.hand_feature_extractor import HandFeatureExtractor

FEATURES_FILENAME = "features.npz"


def _list_images_or_empty(directory: Path) -> List[str]:
    """画像が無い場合に例外ではなく空リストを返す `list_image_files` のラッパー。"""
    try:
        return list_image_files(str(directory))
    except FileNotFoundError:
        return []


def iter_frame_dirs(input_path: Path) -> List[Path]:
    """入力がフレームディレクトリ本体か、その親（出力ルート）かを判別して列挙する。"""
    if not input_path.exists():
        raise FileNotFoundError(f"入力パスが存在しません: {input_path}")
    if not input_path.is_dir():
        raise NotADirectoryError(f"ディレクトリを指定してください: {input_path}")

    if _list_images_or_empty(input_path):
        return [input_path]

    return [child for child in sorted(input_path.iterdir())
            if child.is_dir() and _list_images_or_empty(child)]


def build_features_for_dir(
    frame_dir: Path,
    image_extractor: ImageFeatureExtractor,
    hand_extractor: HandFeatureExtractor,
    batch_size: int = 16,
) -> dict[str, np.ndarray]:
    """1動画ぶんのフレームから特徴行列を作る（保存はしない）。"""
    files = _list_images_or_empty(frame_dir)
    if not files:
        raise FileNotFoundError(f"フレーム画像が見つかりません: {frame_dir}")

    # 速度特徴が動画の境界をまたがないよう、動画ごとに直前フレームの状態を捨てる
    hand_extractor.reset()

    frames: List[int] = []
    hand_features: List[np.ndarray] = []
    image_features: List[np.ndarray] = []
    batch: List[np.ndarray] = []

    for index, filename in enumerate(files):
        image_path = frame_dir / filename
        image_bgr = cv2.imread(str(image_path))
        if image_bgr is None:
            print(f"  スキップ（読み込み失敗）: {image_path}")
            continue

        frame_number = extract_frame_number(filename)
        frames.append(index if frame_number is None else frame_number)
        # 手の特徴はフレーム順に1枚ずつ（ステートフルなのでバッチ化できない）
        hand_features.append(hand_extractor.extract(image_bgr))

        batch.append(image_bgr)
        if len(batch) >= batch_size:
            image_features.append(image_extractor.extract_batch(batch))
            batch = []

    if batch:
        image_features.append(image_extractor.extract_batch(batch))

    if not frames:
        raise ValueError(f"有効なフレームがありませんでした: {frame_dir}")

    return {
        "frames": np.asarray(frames, dtype=np.int64),
        "image": np.concatenate(image_features, axis=0).astype(np.float32),
        "hand": np.stack(hand_features).astype(np.float32),
    }


def run(input_path: str | Path, batch_size: int = 16, overwrite: bool = True) -> List[Path]:
    """入力配下の各動画ディレクトリに features.npz を書き出す。"""
    input_path_obj = Path(input_path).expanduser().resolve()
    frame_dirs = iter_frame_dirs(input_path_obj)
    if not frame_dirs:
        raise FileNotFoundError(f"フレーム画像を含むディレクトリが見つかりません: {input_path_obj}")

    written: List[Path] = []
    with ImageFeatureExtractor() as image_extractor, HandFeatureExtractor() as hand_extractor:
        for frame_dir in frame_dirs:
            output_path = frame_dir / FEATURES_FILENAME
            if output_path.exists() and not overwrite:
                print(f"既存のためスキップ: {output_path}")
                continue

            print(f"特徴抽出: {frame_dir}")
            features = build_features_for_dir(
                frame_dir, image_extractor, hand_extractor, batch_size=batch_size
            )
            np.savez_compressed(output_path, **features)
            print(
                f"  保存: {output_path} "
                f"(frames={len(features['frames'])}, image={features['image'].shape}, "
                f"hand={features['hand'].shape})"
            )
            written.append(output_path)

    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="フレーム画像から画像768次元 + 手162次元の特徴をキャッシュする"
    )
    parser.add_argument("input", help="フレームディレクトリ、またはそれらを含む出力ルート")
    parser.add_argument("--batch-size", "-b", type=int, default=16, help="ConvNeXt のバッチサイズ")
    parser.add_argument("--skip-existing", action="store_true",
                        help="features.npz が既にあるディレクトリを飛ばす")
    args = parser.parse_args(argv)

    written = run(args.input, batch_size=args.batch_size, overwrite=not args.skip_existing)
    print(f"{len(written)} 件の features.npz を書き出しました "
          f"(image_dim={IMAGE_FEATURE_DIM}, hand_dim={HAND_FEATURE_DIM})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
