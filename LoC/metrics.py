"""LoC 回帰モデルの評価指標を計算・表示する。

`train.py`（学習後の test 評価）と `evaluate.py`（チェックポイントの test 評価・交差検証）
の両方から使う。train.py ↔ evaluate.py の循環 import を避けるため独立したモジュールにした。

指標:
- 回帰: MAE / RMSE / R² / Pearson 相関
- baseline_MAE: 学習データのラベル平均を常に出力するだけの予測器の MAE。
  MAE がこれを下回っていなければ、モデルは入力を使えていない。
- ACC: LoC を 20 点刻みの5クラス（0–20 / 20–40 / 40–60 / 60–80 / 80–100）に分けたときの
  正解率。ラベリングの段階（10/30/50/70/90, tools/loc_rubric.LEVELS）をそれぞれ中心に
  持つ区間なので、「段階を当てられたか」に相当する。
- ACC±10: |予測 − 正解| ≤ 10 の割合（段階の間隔 20 の半分）。
- macro-F1 と混同行列: クラスの偏り（60–80 が約4割）に引っ張られない見方として併記する。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
import torch
from torch.utils.data import DataLoader

# 5クラスの境界（右端は 100 を含める）。tools/loc_rubric.LEVELS の各段階が区間の中心
CLASS_EDGES = (0.0, 20.0, 40.0, 60.0, 80.0, 100.0)
CLASS_NAMES = ("0-20", "20-40", "40-60", "60-80", "80-100")
TOLERANCE = 10.0


def to_classes(values: np.ndarray) -> np.ndarray:
    """LoC 値（0〜100）を 0..4 のクラス番号に変換する。100 ちょうどは最上位クラス。"""
    inner_edges = np.asarray(CLASS_EDGES[1:-1])
    return np.searchsorted(inner_edges, np.asarray(values, dtype=np.float64), side="right")


@torch.no_grad()
def predict(model: torch.nn.Module, dataset, device: torch.device,
            batch_size: int = 256) -> Dict[str, np.ndarray]:
    """データセット全体を推論し、予測・正解・動画名を返す（並びは dataset.samples 順）。"""
    model.eval()
    predictions: List[np.ndarray] = []
    targets: List[np.ndarray] = []
    for img_feat, hand_feat, target in DataLoader(dataset, batch_size=batch_size):
        output = model(img_feat.to(device), hand_feat.to(device))
        predictions.append(output.squeeze(-1).cpu().numpy())
        targets.append(target.squeeze(-1).numpy())
    videos = np.array([dataset.source_dirs[v].name for v, _, _ in dataset.samples])
    return {
        "pred": np.concatenate(predictions) if predictions else np.zeros(0),
        "true": np.concatenate(targets) if targets else np.zeros(0),
        "video": videos,
    }


def compute_metrics(pred: np.ndarray, true: np.ndarray,
                    train_mean: Optional[float] = None) -> Dict[str, object]:
    """回帰指標と5クラス分類指標をまとめて返す。"""
    pred = np.asarray(pred, dtype=np.float64)
    true = np.asarray(true, dtype=np.float64)
    n = len(true)
    if n == 0:
        raise ValueError("評価サンプルが0件です")

    error = pred - true
    ss_res = float((error ** 2).sum())
    ss_tot = float(((true - true.mean()) ** 2).sum())
    pearson = float(np.corrcoef(pred, true)[0, 1]) if pred.std() > 0 and true.std() > 0 else float("nan")

    pred_cls = to_classes(pred)
    true_cls = to_classes(true)
    k = len(CLASS_NAMES)
    confusion = np.zeros((k, k), dtype=np.int64)
    np.add.at(confusion, (true_cls, pred_cls), 1)

    f1s = []
    for c in range(k):
        tp = confusion[c, c]
        support = confusion[c, :].sum()
        predicted = confusion[:, c].sum()
        if support == 0:
            continue  # 正解に現れないクラスは macro 平均に入れない
        precision = tp / predicted if predicted else 0.0
        recall = tp / support
        f1s.append(0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))

    metrics: Dict[str, object] = {
        "n": n,
        "mae": float(np.abs(error).mean()),
        "rmse": float(np.sqrt((error ** 2).mean())),
        "r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
        "pearson": pearson,
        "acc": float((pred_cls == true_cls).mean()),
        "acc_tol": float((np.abs(error) <= TOLERANCE).mean()),
        "macro_f1": float(np.mean(f1s)) if f1s else float("nan"),
        "confusion": confusion,
        "pred_mean": float(pred.mean()),
        "true_mean": float(true.mean()),
    }
    if train_mean is not None:
        metrics["baseline_mae"] = float(np.abs(true - train_mean).mean())
        metrics["baseline_acc"] = float((to_classes(np.full(n, train_mean)) == true_cls).mean())
    return metrics


def per_video_metrics(result: Dict[str, np.ndarray],
                      train_mean: Optional[float] = None) -> List[Dict[str, object]]:
    """動画ごとの指標（表示順は動画名順）。"""
    rows = []
    for name in sorted(set(result["video"].tolist())):
        mask = result["video"] == name
        row = compute_metrics(result["pred"][mask], result["true"][mask], train_mean)
        row["video"] = name
        rows.append(row)
    return rows


def format_report(metrics: Dict[str, object], title: str = "test",
                  per_video: Optional[Sequence[Dict[str, object]]] = None) -> str:
    """指標を人が読む形の文字列にする。"""
    lines = [f"=== {title}: {metrics['n']} サンプル ==="]
    line = (f"MAE={metrics['mae']:.3f}  RMSE={metrics['rmse']:.3f}  "
            f"R2={metrics['r2']:.3f}  Pearson={metrics['pearson']:.3f}")
    lines.append(line)
    line = (f"ACC(5クラス)={metrics['acc']:.3f}  ACC±{TOLERANCE:g}={metrics['acc_tol']:.3f}  "
            f"macro-F1={metrics['macro_f1']:.3f}")
    lines.append(line)
    if "baseline_mae" in metrics:
        verdict = "○ baseline 超え" if metrics["mae"] < metrics["baseline_mae"] else "× baseline 以下"
        lines.append(f"baseline（学習平均を出すだけ）: MAE={metrics['baseline_mae']:.3f}  "
                     f"ACC={metrics['baseline_acc']:.3f}  → {verdict}")
    lines.append(f"予測平均={metrics['pred_mean']:.2f} / 正解平均={metrics['true_mean']:.2f}")

    confusion = metrics["confusion"]
    lines.append("")
    lines.append("混同行列（行=正解, 列=予測）")
    width = 8
    lines.append(" " * width + "".join(f"{name:>{width}}" for name in CLASS_NAMES) + f"{'recall':>{width}}")
    for i, name in enumerate(CLASS_NAMES):
        support = confusion[i].sum()
        recall = f"{confusion[i, i] / support:.3f}" if support else "-"
        lines.append(f"{name:>{width}}" + "".join(f"{v:>{width}}" for v in confusion[i]) + f"{recall:>{width}}")

    if per_video:
        lines.append("")
        header = f"{'動画':<34} {'n':>6} {'MAE':>7} {'ACC':>6} {'ACC±10':>7} {'baseline':>9}"
        lines.append(header)
        lines.append("-" * len(header))
        for row in per_video:
            baseline = f"{row['baseline_mae']:>9.3f}" if "baseline_mae" in row else f"{'-':>9}"
            lines.append(f"{str(row['video'])[:33]:<34} {row['n']:>6} {row['mae']:>7.3f} "
                         f"{row['acc']:>6.3f} {row['acc_tol']:>7.3f} {baseline}")
    return "\n".join(lines)


def to_jsonable(metrics: Dict[str, object]) -> Dict[str, object]:
    """json.dump できる形にする（混同行列を list に）。"""
    out = {}
    for key, value in metrics.items():
        if isinstance(value, np.ndarray):
            out[key] = value.tolist()
        elif isinstance(value, (np.floating, np.integer)):
            out[key] = value.item()
        else:
            out[key] = value
    return out
