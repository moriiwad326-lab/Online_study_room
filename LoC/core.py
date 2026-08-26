"""LoC（集中度）推定モデルの統合エントリポイント。

各モジュール（ConvNeXt / MediaPipe Hands / 射影MLP / LSTM / 回帰ヘッド）を1本の
モデルとして結線する。README「LoC集中度推定モデル：アーキテクチャ」の ①〜⑧ に対応。

    画像 --① ConvNeXt-Tiny(freeze)--> 768 --③ 射影MLP--> 128 --+
                                                               |--⑤ concat 192
    画像 --② MediaPipe Hands--------> 162 --④ 射影MLP--> 64  --+
                                                               |
                    --⑥ LSTM(192->256, 単方向, 最終ステップ)-->
                    --⑦ MLPヘッド(256->64->1)--> ⑧ sigmoid*4+1 --> LoC [1,5]

①②（特徴抽出）は学習前に `tools/build_features.py` で `.npy` へキャッシュする前提
なので、`LoCModel` が受け取るのは画像/手の**特徴ベクトル列**であって生画像ではない。
リアルタイム推論で生フレームから直接推定する場合は `infer.py` を参照。
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import torch
import torch.nn as nn

from ConvNeXT.image_feature_extractor import IMAGE_FEATURE_DIM
from LSTM.LSTM import SimpleLSTM
from MLP.projection import ProjectionMLP

try:
    from MediaPipe_Hands.hand_feature_extractor import FEATURE_DIM as HAND_FEATURE_DIM
except ImportError:  # mediapipe 未インストールの学習環境でもモデル定義は使えるようにする
    HAND_FEATURE_DIM = 162  # MediaPipe_Hands.hand_feature_extractor.FEATURE_DIM と一致させること

# 集中度の物理的な値域（⑧ の出力レンジ制約）
LOC_MIN = 1.0
LOC_MAX = 5.0


class LoCModel(nn.Module):
    """画像特徴列と手指特徴列から集中度（1〜5）を回帰するモデル。

    img_dim / hand_dim: 入力特徴の次元（既定は ConvNeXt 768 / Hands 162）
    d_img / d_hand:     射影後の次元（③④）。128 : 64 とし、素の 768 : 162 ≒ 5 : 1 という
                        次元不均衡を 2 : 1 まで是正して両モダリティを対等に扱う
    hidden:             LSTM の隠れ次元（⑥）
    head_hidden:        回帰ヘッドの中間次元（⑦）
    """

    def __init__(
        self,
        img_dim: int = IMAGE_FEATURE_DIM,
        hand_dim: int = HAND_FEATURE_DIM,
        d_img: int = 128,
        d_hand: int = 64,
        hidden: int = 256,
        head_hidden: int = 64,
        num_layers: int = 1,
        dropout: float = 0.2,
    ):
        super().__init__()

        # ③ 画像側 射影MLP: 768 -> 128（ImageNet向け特徴空間をLoC向けへ適応させる）
        self.img_proj = ProjectionMLP(img_dim, [], d_img, dropout=dropout)
        # ④ 手側 射影MLP: 162 -> 128 -> 64（指の開き具合など非線形な関係を抽象化）
        self.hand_proj = ProjectionMLP(hand_dim, [128], d_hand, dropout=0.0)

        # ⑥ LSTM（単方向・最終タイムステップのみ使用）と ⑦ ヘッド前半 (hidden -> head_hidden)。
        # SimpleLSTM は `out[:, -1, :]` を全結合に通す実装なので、そのまま ⑥+Linear として使える。
        # リアルタイム推論では未来フレームを参照できないため bidirectional にはしない。
        self.lstm = SimpleLSTM(
            input_size=d_img + d_hand,  # ⑤ concat: 128 + 64 = 192
            hidden_size=hidden,
            num_layers=num_layers,
            output_size=head_hidden,
        )
        # ⑦ 回帰ヘッド後半: 64 -> 1
        self.head = nn.Sequential(nn.GELU(), nn.Linear(head_hidden, 1))

    def forward(self, img_feat: torch.Tensor, hand_feat: torch.Tensor) -> torch.Tensor:
        """img_feat: (B, T, img_dim), hand_feat: (B, T, hand_dim) -> (B, 1) の LoC 値。"""
        z = torch.cat([self.img_proj(img_feat), self.hand_proj(hand_feat)], dim=-1)
        last = self.lstm(z)  # (B, head_hidden) 最終タイムステップのみ
        raw = self.head(last)  # (B, 1)
        # ⑧ sigmoid で [0,1] に潰してから [1,5] へ写す
        return torch.sigmoid(raw) * (LOC_MAX - LOC_MIN) + LOC_MIN


def build_model(**kwargs) -> LoCModel:
    """既定構成の LoCModel を生成する（train.py / infer.py から共通で使う）。"""
    return LoCModel(**kwargs)


__all__ = [
    "LoCModel",
    "build_model",
    "IMAGE_FEATURE_DIM",
    "HAND_FEATURE_DIM",
    "LOC_MIN",
    "LOC_MAX",
]
