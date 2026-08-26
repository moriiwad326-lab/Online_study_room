"""LoCModel の学習スクリプト。

前提: `tools/dataset_creation.py` でフレームとラベルを作り、`tools/build_features.py`
で `features.npz` をキャッシュ済みであること。ConvNeXt は freeze して事前キャッシュ
する方針なので、ここで学習するのは射影MLP + LSTM + 回帰ヘッド（約60万パラメータ）のみ。

損失は MSE ではなく Huber（SmoothL1Loss）。ラベルが VLM の自動生成でノイズを含むため、
外れ値に引っ張られにくい方が安全という判断（README「出力レンジと損失関数」）。

使い方:
    python LoC/train.py output --seq-len 20 --epochs 50
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

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
    checkpoint_path: str | Path = DEFAULT_CHECKPOINT,
    device: Optional[str] = None,
) -> Path:
    torch_device = torch.device(device) if device else torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
    )

    train_set, val_set = build_datasets(data_root, seq_len=seq_len, val_ratio=val_ratio)
    if len(train_set) == 0:
        raise ValueError(
            "学習サンプルが0件です。seq_len がフレーム数に対して長すぎるか、"
            "labels.csv が -1（判定不能）ばかりの可能性があります。"
        )
    print(f"train: {len(train_set)} サンプル / val: {len(val_set) if val_set else 0} サンプル "
          f"(seq_len={seq_len}, device={torch_device})")

    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_set, batch_size=batch_size) if val_set else None

    model = build_model(dropout=dropout).to(torch_device)
    # Huber損失: 自動ラベルのノイズ（外れ値）に引っ張られにくくする
    criterion = nn.SmoothL1Loss()
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    checkpoint_path = Path(checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    best_score = float("inf")

    for epoch in range(1, epochs + 1):
        train_loss, train_mae = run_epoch(model, train_loader, criterion, torch_device, optimizer)
        message = f"Epoch [{epoch}/{epochs}] train_loss={train_loss:.4f} train_MAE={train_mae:.3f}"

        score = train_loss
        if val_loader is not None:
            val_loss, val_mae = run_epoch(model, val_loader, criterion, torch_device)
            message += f" val_loss={val_loss:.4f} val_MAE={val_mae:.3f}"
            score = val_loss
        print(message)

        # 検証があれば検証損失、無ければ学習損失で best を更新する
        if score < best_score:
            best_score = score
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "seq_len": seq_len,
                    "dropout": dropout,
                    "epoch": epoch,
                    "score": score,
                },
                checkpoint_path,
            )

    print(f"best score={best_score:.4f} / checkpoint: {checkpoint_path}")
    return checkpoint_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LoCModel（集中度回帰）を学習する")
    parser.add_argument("data", help="features.npz と labels.csv を含むディレクトリ（またはその親）")
    parser.add_argument("--seq-len", type=int, default=20, help="LSTM に入れる時系列長")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--val-ratio", type=float, default=0.2,
                        help="各動画の末尾から検証に回す割合（0で検証なし）")
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
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
