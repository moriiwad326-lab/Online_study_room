"""ConvNeXt-Tiny を分類ヘッドなしの特徴抽出器として使う（アーキテクチャ ①）。

`tiny_model.py` は ImageNet 1000クラスのロジットを返すデモだが、LoCモデルが必要と
するのは分類結果ではなく **768次元の意味ベクトル** である。ここでは
`ConvNextModel`（分類ヘッドを持たない本体のみ）を freeze した状態でロードし、
BGR画像から768次元ベクトルを取り出す。

CLI:
    python LoC/ConvNeXT/image_feature_extractor.py <画像パス> --output feature.npy
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

MODEL_NAME = "facebook/convnext-tiny-224"

# ConvNeXt-Tiny の最終ステージのチャンネル数 = プーリング後の特徴次元
IMAGE_FEATURE_DIM = 768


class ImageFeatureExtractor:
    """ConvNeXt-Tiny（freeze）で画像から768次元特徴ベクトルを返す。

    学習初期は ConvNeXt を凍結して特徴を事前キャッシュする方針のため、
    パラメータは `requires_grad=False`、常に `eval()` で保持する。
    """

    def __init__(
        self,
        model_name: str = MODEL_NAME,
        device: Optional[str] = None,
    ):
        # transformers / torch は重いのでモジュール import 時ではなく生成時に読む
        # （`IMAGE_FEATURE_DIM` だけを参照したい側に読み込みコストを負わせない）
        import torch
        from transformers import AutoImageProcessor, ConvNextModel

        self._torch = torch
        # device 未指定なら GPU を優先する。ConvNeXt の forward がこのモジュール最大の
        # 計算コストなので、既定が CPU のままだと特徴抽出が数倍遅くなる。
        self.device = torch.device(device) if device else torch.device(
            "cuda" if torch.cuda.is_available() else "cpu"
        )
        self.processor = AutoImageProcessor.from_pretrained(model_name, use_fast=True)
        self.model = ConvNextModel.from_pretrained(model_name)
        self.model.eval().to(self.device)
        for param in self.model.parameters():
            param.requires_grad_(False)

    def extract(self, image_bgr: np.ndarray) -> np.ndarray:
        """BGR画像1枚から768次元ベクトルを取得する。"""
        return self.extract_batch([image_bgr])[0]

    def extract_batch(self, images_bgr: Sequence[np.ndarray]) -> np.ndarray:
        """BGR画像のリストから (N, 768) の特徴行列を取得する。"""
        if len(images_bgr) == 0:
            return np.zeros((0, IMAGE_FEATURE_DIM), dtype=np.float32)

        # OpenCV は BGR、画像プロセッサは RGB を期待する。
        # `[:, :, ::-1]` はストライドが負のビューになり torch.from_numpy が受け付けない
        # ため、連続メモリにコピーしてから渡す。
        rgb_images = [np.ascontiguousarray(image[:, :, ::-1]) for image in images_bgr]
        inputs = self.processor(images=rgb_images, return_tensors="pt").to(self.device)

        with self._torch.no_grad():
            outputs = self.model(**inputs)

        # pooler_output: 最終特徴マップの GAP + LayerNorm → (N, 768)
        features = outputs.pooler_output.cpu().numpy().astype(np.float32)
        return features

    def close(self):
        # 明示的な後始末は不要だが HandFeatureExtractor と API を揃えておく
        self.model = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def extract_image_features(
    image_bgr: np.ndarray, extractor: Optional[ImageFeatureExtractor] = None
) -> np.ndarray:
    """単発の画像から768次元ベクトルを取得するヘルパー関数。

    `extractor` を渡さない場合は毎回モデルをロードするため、複数枚を処理するときは
    `ImageFeatureExtractor` を1つ生成して使い回すこと。
    """
    if extractor is not None:
        return extractor.extract(image_bgr)

    with ImageFeatureExtractor() as one_shot_extractor:
        return one_shot_extractor.extract(image_bgr)


def main(argv: Optional[List[str]] = None) -> int:
    import cv2

    parser = argparse.ArgumentParser(
        description="ConvNeXt-Tiny（分類ヘッドなし）で画像から768次元特徴を取得"
    )
    parser.add_argument("image", help="入力画像のパス")
    parser.add_argument("--output", help="保存先 npy ファイル（任意）")
    parser.add_argument("--model", default=MODEL_NAME, help="使用するモデル名")
    args = parser.parse_args(argv)

    image_path = Path(args.image)
    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    image_bgr = cv2.imread(str(image_path))
    if image_bgr is None:
        raise ValueError(f"画像を読み込めませんでした: {image_path}")

    with ImageFeatureExtractor(model_name=args.model) as extractor:
        features = extractor.extract(image_bgr)

    print("features shape:", features.shape)
    print("features:", features)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(output_path, features)
        print(f"saved to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
