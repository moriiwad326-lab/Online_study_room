"""LoCModel の学習スクリプト。

前提: `tools/dataset_creation.py` でフレームとラベルを作り、`tools/build_features.py`
で `features.npz` をキャッシュ済みであること。ConvNeXt は freeze して事前キャッシュ
する方針なので、ここで学習するのは射影MLP + LSTM + 回帰ヘッド（約60万パラメータ）のみ。

損失は MSE ではなく Huber（SmoothL1Loss）。ラベルが VLM の自動生成でノイズを含むため、
外れ値に引っ張られにくい方が安全という判断（README「出力レンジと損失関数」）。

検証の分割は既定で**動画単位**（`--split video`）。同一動画が train と val の両方に入ると
ConvNeXt 特徴から人物・部屋を手がかりに当てられてしまい、汎化性能を過大評価する。
旧挙動（動画ごとに末尾を検証に回す）は `--split tail` で、学習が回っているかの確認用。

`--label-smooth 5` でラベル系列に中央値フィルタをかける。qwen のラベルは分散の約6割が
独立ノイズなので、平滑化しないと val の数字がノイズに埋もれて比較できない（dataset.py 参照）。

数字の読み方の基準として、毎エポック「常に学習データの平均を出力するだけの予測器」の
MAE（baseline_MAE）を併記する。val_MAE がこれを下回っていなければ、モデルは何も学習
できていないのと同じ。

使い方:
    python LoC/train.py output --split video --label-smooth 5 --epochs 50
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from core import build_model
from dataset import build_datasets

DEFAULT_CHECKPOINT = PACKAGE_ROOT / "checkpoints" / "loc_model.pt"


def run_epoch(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
    optimizer: Optional[torch.optim.Optimizer] = None,
) -> Tuple[float, float]:
    """1エポック分を回して (平均損失, 平均絶対誤差) を返す。optimizer=None で評価のみ。"""
    is_train = optimizer is not None
    model.train(is_train)

    total_loss = 0.0
    total_abs_error = 0.0
    total_samples = 0

    with torch.set_grad_enabled(is_train):
        for img_feat, hand_feat, target in loader:
            img_feat = img_feat.to(device)
            hand_feat = hand_feat.to(device)
            target = target.to(device)

            prediction = model(img_feat, hand_feat)
            loss = criterion(prediction, target)

            if is_train:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

            batch_size = target.size(0)
            total_loss += loss.item() * batch_size
            total_abs_error += (prediction - target).abs().sum().item()
            total_samples += batch_size

    if total_samples == 0:
        return float("nan"), float("nan")
    return total_loss / total_samples, total_abs_error / total_samples


def train(
    data_root: str | Path,
    seq_len: int = 20,
    epochs: int = 50,
    batch_size: int = 32,
    learning_rate: float = 1e-3,
    val_ratio: float = 0.2,
    dropout: float = 0.2,
    checkpoint_path: Optional[str | Path] = DEFAULT_CHECKPOINT,
    device: Optional[str] = None,
    split: str = "video",
    val_videos: Optional[Sequence[str]] = None,
    label_smooth: int = 1,
    verbose: bool = True,
) -> Dict[str, float]:
    """1回の学習を実行し、指標の辞書を返す。

    checkpoint_path=None でチェックポイントを保存しない（交差検証から呼ぶとき用）。
    """
    torch_device = torch.device(device) if device else torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
    )

    train_set, val_set = build_datasets(
        data_root, seq_len=seq_len, val_ratio=val_ratio,
        split=split, val_videos=val_videos, label_smooth=label_smooth,
    )
    if len(train_set) == 0:
        raise ValueError(
            "学習サンプルが0件です。seq_len がフレーム数に対して長すぎるか、"
            "labels.csv が -1（判定不能）ばかりの可能性があります。"
        )

    # 「常に学習データの平均を出力する」だけの予測器の MAE。val_MAE がこれを下回って
    # いなければ、モデルは入力を一切使えていないのと同じ（ラベルノイズに埋もれている）。
    train_mean = float(train_set.label_values().mean())
    baseline_mae = (
        float(np.abs(val_set.label_values() - train_mean).mean()) if val_set else float("nan")
    )

    if verbose:
        print(f"train: {len(train_set)} サンプル / val: {len(val_set) if val_set else 0} サンプル "
              f"(seq_len={seq_len}, split={split}, label_smooth={label_smooth}, device={torch_device})")
        if split == "video" and val_set is not None:
            print(f"  学習に使う動画: {len(train_set.video_names())}本 / "
                  f"検証に回す動画: {val_set.video_names()}")
        print(f"  学習ラベル平均={train_mean:.3f} / 平均を出すだけの baseline_MAE={baseline_mae:.3f}")

    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_set, batch_size=batch_size) if val_set else None

    model = build_model(dropout=dropout).to(torch_device)
    # Huber損失: 自動ラベルのノイズ（外れ値）に引っ張られにくくする
    criterion = nn.SmoothL1Loss()
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    if checkpoint_path is not None:
        checkpoint_path = Path(checkpoint_path)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    best_score = float("inf")
    best = {"epoch": 0, "train_mae": float("nan"), "val_mae": float("nan")}

    for epoch in range(1, epochs + 1):
        train_loss, train_mae = run_epoch(model, train_loader, criterion, torch_device, optimizer)
        message = f"Epoch [{epoch}/{epochs}] train_loss={train_loss:.4f} train_MAE={train_mae:.3f}"

        score = train_loss
        val_mae = float("nan")
        if val_loader is not None:
            val_loss, val_mae = run_epoch(model, val_loader, criterion, torch_device)
            message += f" val_loss={val_loss:.4f} val_MAE={val_mae:.3f}"
            score = val_loss
        if verbose:
            print(message)

        # 検証があれば検証損失、無ければ学習損失で best を更新する
        if score < best_score:
            best_score = score
            best = {"epoch": epoch, "train_mae": train_mae, "val_mae": val_mae}
            if checkpoint_path is not None:
                torch.save(
                    {
                        "model_state_dict": model.state_dict(),
                        "seq_len": seq_len,
                        "dropout": dropout,
                        "split": split,
                        "label_smooth": label_smooth,
                        "epoch": epoch,
                        "score": score,
                    },
                    checkpoint_path,
                )

    metrics = {
        "best_score": best_score,
        "best_epoch": float(best["epoch"]),
        "train_mae": float(best["train_mae"]),
        "val_mae": float(best["val_mae"]),
        "baseline_mae": baseline_mae,
        "train_samples": float(len(train_set)),
        "val_samples": float(len(val_set) if val_set else 0),
    }
    if verbose:
        print(f"best score={best_score:.4f} (epoch {best['epoch']}) "
              f"train_MAE={metrics['train_mae']:.3f} val_MAE={metrics['val_mae']:.3f} "
              f"baseline_MAE={baseline_mae:.3f}")
        if checkpoint_path is not None:
            print(f"checkpoint: {checkpoint_path}")
    return metrics


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LoCModel（集中度回帰）を学習する")
    parser.add_argument("data", help="features.npz と labels.csv を含むディレクトリ（またはその親）")
    parser.add_argument("--seq-len", type=int, default=20, help="LSTM に入れる時系列長")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--val-ratio", type=float, default=0.2,
                        help="検証に回す割合（0で検証なし）")
    parser.add_argument("--split", choices=("video", "tail"), default="video",
                        help="video: 動画単位で分割（既定・汎化性能を測るならこちら） / "
                             "tail: 動画ごとに末尾を検証に回す（同一動画が両側に入るためリークあり）")
    parser.add_argument("--val-videos", default=None,
                        help="検証に回す動画ディレクトリ名をカンマ区切りで明示（--split video のとき）")
    parser.add_argument("--label-smooth", type=int, default=1,
                        help="ラベル系列にかける中央値フィルタの幅（奇数。1で無効、推奨5）")
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--device", default=None, help="cpu / cuda（既定は自動判定）")
    args = parser.parse_args(argv)

    train(
        data_root=args.data,
        seq_len=args.seq_len,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        val_ratio=args.val_ratio,
        dropout=args.dropout,
        checkpoint_path=args.checkpoint,
        device=args.device,
        split=args.split,
        val_videos=[v for v in args.val_videos.split(",") if v] if args.val_videos else None,
        label_smooth=args.label_smooth,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
