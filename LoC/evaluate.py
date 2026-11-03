"""学習済み LoC モデルを `datasets/test/` で評価する（MAE / ACC など）。

`train.py` が保存したチェックポイントを読み、学習に一切使っていない test/ の動画で
指標を出す。指標の定義は `metrics.py` を参照:

- 回帰: MAE / RMSE / R² / Pearson 相関
- ACC: LoC を 20 点刻みの5クラスに分けたときの正解率（段階 10/30/50/70/90 を当てられたか）
- ACC±10: |予測 − 正解| ≤ 10 の割合
- macro-F1、混同行列、動画ごとの MAE / ACC
- baseline: 学習データのラベル平均を常に出力するだけの予測器。MAE がこれを下回って
  いなければ、モデルは入力を使えていない

`--cv` を付けると、従来の leave-one-video-out 交差検証を **train/ の中だけで** 行う
（ハイパーパラメータ選びに使う。test/ は混ぜない）。各 fold の val でも ACC を出す。

使い方:
    python LoC/evaluate.py                                    # checkpoints/loc_model.pt を test/ で評価
    python LoC/evaluate.py --checkpoint path/to/model.pt --json result.json
    python LoC/evaluate.py --cv --label-smooth 5 --epochs 20  # train/ の中で交差検証
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import numpy as np
import torch

from core import build_model
from dataset import DEFAULT_TEST_DIR, DEFAULT_TRAIN_DIR, LoCSequenceDataset, find_dataset_dirs
from metrics import compute_metrics, format_report, per_video_metrics, predict, to_jsonable
from train import DEFAULT_CHECKPOINT, train


def evaluate_checkpoint(
    checkpoint_path: str | Path = DEFAULT_CHECKPOINT,
    test_root: str | Path = DEFAULT_TEST_DIR,
    train_root: str | Path = DEFAULT_TRAIN_DIR,
    label_smooth: Optional[int] = None,
    device: Optional[str] = None,
) -> dict:
    """チェックポイントを test で評価し、全体・動画ごとの指標を返す。

    seq_len と label_smooth は既定でチェックポイントに保存された学習時の値を使う
    （学習と同じ条件のラベルで比べないと数字が比較できないため）。
    """
    torch_device = torch.device(device) if device else torch.device(
        "cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(Path(checkpoint_path), map_location=torch_device)
    seq_len = int(checkpoint.get("seq_len", 12))
    if label_smooth is None:
        label_smooth = int(checkpoint.get("label_smooth", 1))

    model = build_model(dropout=float(checkpoint.get("dropout", 0.2))).to(torch_device)
    model.load_state_dict(checkpoint["model_state_dict"])

    # baseline 用の学習ラベル平均。古いチェックポイントには入っていないので train/ から計算する
    train_mean = checkpoint.get("train_mean")
    if train_mean is None:
        train_set = LoCSequenceDataset(train_root, seq_len=seq_len, subset="all",
                                       label_smooth=label_smooth)
        train_mean = float(train_set.label_values().mean())

    test_set = LoCSequenceDataset(test_root, seq_len=seq_len, subset="all",
                                  label_smooth=label_smooth)
    result = predict(model, test_set, torch_device)
    overall = compute_metrics(result["pred"], result["true"], train_mean)
    per_video = per_video_metrics(result, train_mean)

    print(f"checkpoint: {checkpoint_path} (epoch {checkpoint.get('epoch', '?')}, "
          f"seq_len={seq_len}, label_smooth={label_smooth})")
    print(f"test: {Path(test_root)} の {len(test_set.source_dirs)}本\n")
    print(format_report(overall, title="test", per_video=per_video))
    return {
        "checkpoint": str(checkpoint_path),
        "test_root": str(test_root),
        "seq_len": seq_len,
        "label_smooth": label_smooth,
        "train_mean": float(train_mean),
        "overall": overall,
        "per_video": per_video,
    }


def cross_validate(
    data_root: str | Path = DEFAULT_TRAIN_DIR,
    seq_len: int = 12,
    epochs: int = 30,
    batch_size: int = 32,
    learning_rate: float = 1e-3,
    dropout: float = 0.2,
    label_smooth: int = 1,
    device: Optional[str] = None,
    seed: int = 0,
) -> dict:
    """train/ の各動画を1本ずつ検証に回して学習し、結果をまとめて返す（test/ は使わない）。"""
    dataset_dirs = find_dataset_dirs(data_root)
    if len(dataset_dirs) < 2:
        raise ValueError(f"交差検証には2本以上の動画が必要です（検出: {len(dataset_dirs)}本）")

    print(f"leave-one-video-out: {Path(data_root)} の {len(dataset_dirs)}本 "
          f"(seq_len={seq_len}, epochs={epochs}, label_smooth={label_smooth})\n")
    header = (f"{'検証に回した動画':<34} {'val数':>6} {'train_MAE':>10} {'val_MAE':>8} "
              f"{'baseline':>9} {'ACC':>6} {'ACC±10':>7} {'判定':>6}")
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
            # 検証に回した1本を「test」として渡し、ベストエポックの重みで ACC などを出す
            test_root=dataset_dir,
            verbose=False,
        )
        metrics["video"] = dataset_dir.name
        folds.append(metrics)
        fold_test = metrics["test"]
        verdict = "○" if metrics["val_mae"] < metrics["baseline_mae"] else "×"
        print(f"{dataset_dir.name[:33]:<34} {int(metrics['val_samples']):>6} "
              f"{metrics['train_mae']:>10.3f} {metrics['val_mae']:>8.3f} "
              f"{metrics['baseline_mae']:>9.3f} {fold_test['acc']:>6.3f} "
              f"{fold_test['acc_tol']:>7.3f} {verdict:>6}")

    weights = np.array([f["val_samples"] for f in folds])
    val_mae = np.array([f["val_mae"] for f in folds])
    base_mae = np.array([f["baseline_mae"] for f in folds])
    train_mae = np.array([f["train_mae"] for f in folds])
    acc = np.array([f["test"]["acc"] for f in folds])
    acc_tol = np.array([f["test"]["acc_tol"] for f in folds])

    summary = {
        "folds": folds,
        # 動画ごとの長さが大きく違うので、サンプル数で重み付けした平均も出す
        "val_mae_weighted": float(np.average(val_mae, weights=weights)),
        "val_mae_mean": float(val_mae.mean()),
        "baseline_mae_weighted": float(np.average(base_mae, weights=weights)),
        "train_mae_mean": float(train_mae.mean()),
        "acc_weighted": float(np.average(acc, weights=weights)),
        "acc_tol_weighted": float(np.average(acc_tol, weights=weights)),
        "wins": int((val_mae < base_mae).sum()),
    }

    print("-" * len(header))
    print(f"{'加重平均（サンプル数重み）':<34} {int(weights.sum()):>6} "
          f"{summary['train_mae_mean']:>10.3f} {summary['val_mae_weighted']:>8.3f} "
          f"{summary['baseline_mae_weighted']:>9.3f} {summary['acc_weighted']:>6.3f} "
          f"{summary['acc_tol_weighted']:>7.3f} "
          f"{summary['wins']}/{len(folds)}本で baseline 超え")
    print(f"\n単純平均の val_MAE={summary['val_mae_mean']:.3f} / "
          f"過学習ギャップ（val-train）={summary['val_mae_weighted'] - summary['train_mae_mean']:+.3f}")
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="学習済みモデルを datasets/test で評価する（--cv で train 内の交差検証）")
    parser.add_argument("--test", default=str(DEFAULT_TEST_DIR),
                        help=f"評価する test データ。既定: {DEFAULT_TEST_DIR}")
    parser.add_argument("--train", default=str(DEFAULT_TRAIN_DIR),
                        help=f"学習データ（--cv の対象と baseline の計算に使う）。既定: {DEFAULT_TRAIN_DIR}")
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--json", default=None, help="指標を JSON で保存するパス（任意）")
    parser.add_argument("--cv", action="store_true",
                        help="test 評価の代わりに train 内で leave-one-video-out 交差検証を行う")
    parser.add_argument("--seq-len", type=int, default=12, help="--cv のときの時系列長")
    parser.add_argument("--epochs", type=int, default=30, help="--cv のときのエポック数")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--label-smooth", type=int, default=None,
                        help="ラベル系列にかける中央値フィルタの幅（奇数。1で無効、推奨5）。"
                             "test 評価では既定でチェックポイントの学習時の値、--cv では既定1")
    parser.add_argument("--device", default=None, help="cpu / cuda（既定は自動判定）")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    if args.cv:
        result = cross_validate(
            data_root=args.train,
            seq_len=args.seq_len,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.lr,
            dropout=args.dropout,
            label_smooth=args.label_smooth if args.label_smooth is not None else 1,
            device=args.device,
            seed=args.seed,
        )
        payload = {k: v for k, v in result.items() if k != "folds"}
        payload["folds"] = [
            {**{k: v for k, v in fold.items() if k != "test"}, "test": to_jsonable(fold["test"])}
            for fold in result["folds"]
        ]
    else:
        result = evaluate_checkpoint(
            checkpoint_path=args.checkpoint,
            test_root=args.test,
            train_root=args.train,
            label_smooth=args.label_smooth,
            device=args.device,
        )
        payload = {**result, "overall": to_jsonable(result["overall"]),
                   "per_video": [to_jsonable(row) for row in result["per_video"]]}

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n指標を保存: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
