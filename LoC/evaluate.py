"""動画単位の leave-one-video-out 交差検証で LoC モデルの汎化性能を測る。

`train.py --split video` は検証に回す動画を1組だけ固定するので、どの動画が検証に
当たったかで数字が大きく動く（現状14本しかなく、1本あたりのフレーム数も 58〜807 と
ばらついている）。全動画を順番に検証側へ回して平均を取れば、その当たり外れを均せる。

同一動画が学習と検証の両方に入らないため、ここで出る MAE が「初めて見る人物・部屋で
どれだけ当たるか」に相当する。読み方の基準は2つ:

- `baseline_MAE`: 学習データのラベル平均を常に出力するだけの予測器の MAE。
  `val_MAE` がこれを下回っていなければ、モデルは入力を使えていない。
- `train_MAE` との差: 開きが大きいほど過学習（または動画間のドメイン差が大きい）。

使い方:
    python LoC/evaluate.py output --label-smooth 5 --epochs 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import numpy as np
import torch

from dataset import find_dataset_dirs
from train import train


def cross_validate(
    data_root: str | Path,
    seq_len: int = 20,
    epochs: int = 30,
    batch_size: int = 32,
    learning_rate: float = 1e-3,
    dropout: float = 0.2,
    label_smooth: int = 1,
    device: Optional[str] = None,
    seed: int = 0,
) -> dict:
    """各動画を1本ずつ検証に回して学習し、結果をまとめて返す。"""
    dataset_dirs = find_dataset_dirs(data_root)
    if len(dataset_dirs) < 2:
        raise ValueError(f"交差検証には2本以上の動画が必要です（検出: {len(dataset_dirs)}本）")

    print(f"leave-one-video-out: {len(dataset_dirs)}本 "
          f"(seq_len={seq_len}, epochs={epochs}, label_smooth={label_smooth})\n")
    header = f"{'検証に回した動画':<34} {'val数':>6} {'train_MAE':>10} {'val_MAE':>8} {'baseline':>9} {'判定':>6}"
    print(header)
    print("-" * len(header))

    folds: List[dict] = []
    for dataset_dir in dataset_dirs:
        # fold 間で初期値が変わると比較にならないので、毎回同じシードから始める
        torch.manual_seed(seed)
        metrics = train(
            data_root=data_root,
            seq_len=seq_len,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            dropout=dropout,
            checkpoint_path=None,  # 交差検証では保存しない
            device=device,
            split="video",
            val_videos=[dataset_dir.name],
            label_smooth=label_smooth,
            verbose=False,
        )
        metrics["video"] = dataset_dir.name
        folds.append(metrics)
        verdict = "○" if metrics["val_mae"] < metrics["baseline_mae"] else "×"
        print(f"{dataset_dir.name[:33]:<34} {int(metrics['val_samples']):>6} "
              f"{metrics['train_mae']:>10.3f} {metrics['val_mae']:>8.3f} "
              f"{metrics['baseline_mae']:>9.3f} {verdict:>6}")

    weights = np.array([f["val_samples"] for f in folds])
    val_mae = np.array([f["val_mae"] for f in folds])
    base_mae = np.array([f["baseline_mae"] for f in folds])
    train_mae = np.array([f["train_mae"] for f in folds])

    summary = {
        "folds": folds,
        # 動画ごとの長さが大きく違うので、サンプル数で重み付けした平均も出す
        "val_mae_weighted": float(np.average(val_mae, weights=weights)),
        "val_mae_mean": float(val_mae.mean()),
        "baseline_mae_weighted": float(np.average(base_mae, weights=weights)),
        "train_mae_mean": float(train_mae.mean()),
        "wins": int((val_mae < base_mae).sum()),
    }

    print("-" * len(header))
    print(f"{'加重平均（サンプル数重み）':<34} {int(weights.sum()):>6} "
          f"{summary['train_mae_mean']:>10.3f} {summary['val_mae_weighted']:>8.3f} "
          f"{summary['baseline_mae_weighted']:>9.3f} "
          f"{summary['wins']}/{len(folds)}本で baseline 超え")
    print(f"\n単純平均の val_MAE={summary['val_mae_mean']:.3f} / "
          f"過学習ギャップ（val-train）={summary['val_mae_weighted'] - summary['train_mae_mean']:+.3f}")
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="動画単位の leave-one-video-out 交差検証で汎化性能を測る")
    parser.add_argument("data", help="features.npz と labels.csv を含むディレクトリ（またはその親）")
    parser.add_argument("--seq-len", type=int, default=20)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--label-smooth", type=int, default=1,
                        help="ラベル系列にかける中央値フィルタの幅（奇数。1で無効、推奨5）")
    parser.add_argument("--device", default=None, help="cpu / cuda（既定は自動判定）")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    cross_validate(
        data_root=args.data,
        seq_len=args.seq_len,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        dropout=args.dropout,
        label_smooth=args.label_smooth,
        device=args.device,
        seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
