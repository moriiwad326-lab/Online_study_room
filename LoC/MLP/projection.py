"""LoCモデルの枝ごとに置く射影MLP（アーキテクチャ ③④）。

`MLP_classifier.py` は ReLU + 分類出力の単体PoCであり、集中度回帰の枝で必要な
「Linear + LayerNorm + GELU (+ Dropout)」の構成とは異なるため、射影専用のブロックを
ここに分けて定義する。ConvNeXtの768次元と MediaPipe Hands の162次元を、それぞれ
低次元へ圧縮してから concat することで、両モダリティの寄与を対等にするのが目的。
"""

from __future__ import annotations

from typing import Sequence

import torch.nn as nn


class ProjectionMLP(nn.Module):
    """Linear -> LayerNorm -> GELU (-> Dropout) を積み重ねる射影ブロック。

    input_size:   入力次元
    hidden_sizes: 中間層の次元リスト（空なら1ブロックのみ）。int も可
    output_size:  出力次元
    dropout:      各ブロックの後に挟む Dropout の確率（0.0 なら挿入しない）
    """

    def __init__(
        self,
        input_size: int,
        hidden_sizes: Sequence[int] | int | None,
        output_size: int,
        dropout: float = 0.0,
    ):
        super().__init__()

        if hidden_sizes is None:
            hidden_sizes = []
        elif isinstance(hidden_sizes, int):
            hidden_sizes = [hidden_sizes]

        dims = [input_size, *hidden_sizes, output_size]
        layers: list[nn.Module] = []
        for prev_dim, next_dim in zip(dims[:-1], dims[1:]):
            layers.append(nn.Linear(prev_dim, next_dim))
            layers.append(nn.LayerNorm(next_dim))
            layers.append(nn.GELU())
            if dropout > 0.0:
                layers.append(nn.Dropout(dropout))

        self.layers = nn.Sequential(*layers)
        self.input_size = input_size
        self.output_size = output_size

    def forward(self, x):
        # (..., input_size) -> (..., output_size)。時系列次元 T はそのまま通る
        return self.layers(x)
