"""ConvNeXt の768次元特徴から「人が写っているか」を判定する在席判定器。

ラベル側（`tools/loc_rubric.force_absent_to_zero`）は、qwen の `person_visible` が直近
12フレームすべて false なら LoC を 0 に固定する。推論時に同じルールを効かせるには、
qwen を呼ばずに在席を判定する手段が要る。そこで qwen の `person_visible` を教師にして、
推論で既に計算している ConvNeXt 特徴の上にロジスティック回帰を1枚載せる。

MediaPipe Hands の検出フラグを使わないのは、真上からの画角では書いている最中の手でも
約半分を見逃すため（勉強中の窓の24%が「12フレーム手が無い」になる。実測）。

学習:
    python LoC/presence.py LoC/tools/output
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import numpy as np
import torch
import torch.nn as nn

from ConvNeXT.image_feature_extractor import IMAGE_FEATURE_DIM

DEFAULT_PRESENCE_CHECKPOINT = PACKAGE_ROOT / "checkpoints" / "presence.pt"

# tools/loc_rubric.SCHEMA_VERSION のうち person_visible を持つ最初の版
PRESENCE_SCHEMA_VERSION = 3

FEATURES_FILENAME = "features.npz"
ATTRIBUTES_FILENAME = "attributes.jsonl"


class PresenceClassifier(nn.Module):
    """768次元特徴 -> 人が写っている確率。標準化の統計量もモデルに持たせる。"""

    def __init__(self, dim: int = IMAGE_FEATURE_DIM):
        super().__init__()
        self.register_buffer("mean", torch.zeros(dim))
        self.register_buffer("std", torch.ones(dim))
        self.linear = nn.Linear(dim, 1)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        """(..., dim) -> (...,) のロジット。"""
        return self.linear((features - self.mean) / self.std).squeeze(-1)


class PresenceGate:
    """フレームごとの在席判定を窓で溜め、窓すべてが不在かどうかを返す。"""

    def __init__(self, checkpoint_path: str | Path, window: int, device: torch.device):
        checkpoint = torch.load(Path(checkpoint_path), map_location=device)
        self.threshold = float(checkpoint.get("threshold", 0.5))
        self.model = PresenceClassifier()
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.eval().to(device)
        self.device = device
        self._history: deque = deque(maxlen=window)

    @property
    def latest_present(self) -> Optional[bool]:
        return self._history[-1] if self._history else None

    def reset(self):
        self._history.clear()

    def push(self, image_feature: np.ndarray) -> bool:
        """1フレームぶんの特徴を入れ、窓が埋まっていて全フレーム不在なら True を返す。"""
        with torch.no_grad():
            logit = self.model(torch.from_numpy(image_feature).to(self.device))
        self._history.append(bool(torch.sigmoid(logit).item() >= self.threshold))
        return len(self._history) == self._history.maxlen and not any(self._history)


def load_presence_labels(dataset_dir: Path) -> Dict[int, bool]:
    """attributes.jsonl から {フレーム番号: 写っているか} を読む（判定できたものだけ）。"""
    labels: Dict[int, bool] = {}
    path = dataset_dir / ATTRIBUTES_FILENAME
    if not path.exists():
        return labels
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("schema_version", 0) < PRESENCE_SCHEMA_VERSION:
                continue
            visible = record.get("attributes", {}).get("person_visible")
            if isinstance(visible, bool):
                labels[int(record["frame"])] = visible  # 同じフレームは後ろの行を優先
    return labels


def load_samples(roots: Sequence[str | Path]) -> List[Tuple[str, np.ndarray, np.ndarray]]:
    """動画ごとに (動画名, 特徴 (N, 768), 在席ラベル (N,)) を返す。ラベルの無い動画は除く。"""
    videos = []
    for root in roots:
        root = Path(root).expanduser().resolve()
        candidates = [root, *sorted(p for p in root.iterdir() if p.is_dir())]
        for candidate in candidates:
            if not (candidate / FEATURES_FILENAME).exists():
                continue
            labels = load_presence_labels(candidate)
            if not labels:
                continue
            data = np.load(candidate / FEATURES_FILENAME)
            rows = [i for i, frame in enumerate(data["frames"]) if int(frame) in labels]
            if not rows:
                continue
            features = data["image"][rows].astype(np.float32)
            targets = np.array([labels[int(data["frames"][i])] for i in rows], dtype=np.float32)
            videos.append((candidate.name, features, targets))
    return videos


def fit(features: np.ndarray, targets: np.ndarray, epochs: int = 300,
        weight_decay: float = 1e-3) -> PresenceClassifier:
    """全サンプルでロジスティック回帰を学習する（不在が少数でも効くよう重み付けする）。"""
    model = PresenceClassifier(features.shape[1])
    x = torch.from_numpy(features)
    y = torch.from_numpy(targets)
    model.mean.copy_(x.mean(0))
    model.std.copy_(x.std(0).clamp_min(1e-6))

    positives = float(y.sum())
    negatives = float(len(y) - positives)
    pos_weight = torch.tensor(negatives / max(positives, 1.0))
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.LBFGS(model.parameters(), max_iter=epochs, line_search_fn="strong_wolfe")

    def closure():
        optimizer.zero_grad()
        loss = criterion(model(x), y) + weight_decay * model.linear.weight.pow(2).sum()
        loss.backward()
        return loss

    optimizer.step(closure)
    return model.eval()


def evaluate(model: PresenceClassifier, features: np.ndarray, targets: np.ndarray,
             threshold: float = 0.5) -> Dict[str, float]:
    with torch.no_grad():
        predicted = (torch.sigmoid(model(torch.from_numpy(features))) >= threshold).numpy()
    actual = targets.astype(bool)
    absent = ~actual
    return {
        "accuracy": float((predicted == actual).mean()),
        # 不在を正しく不在と言えた割合（0 に固定すべき区間を拾えるか）
        "absent_recall": float((~predicted & absent).sum() / max(absent.sum(), 1)),
        # 在席を誤って不在と言った割合（勉強中を 0 にしてしまう誤り）
        "present_as_absent": float((~predicted & actual).sum() / max(actual.sum(), 1)),
        "n": int(len(actual)),
        "n_absent": int(absent.sum()),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="qwen の person_visible から在席判定器を学習する")
    parser.add_argument("roots", nargs="+", help="features.npz と attributes.jsonl を含むディレクトリ（またはその親）")
    parser.add_argument("--output", default=str(DEFAULT_PRESENCE_CHECKPOINT))
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args(argv)

    videos = load_samples(args.roots)
    if not videos:
        print("person_visible を持つ attributes.jsonl が見つかりません（schema_version 3 で付け直してください）")
        return 1

    # leave-one-video-out で汎化を測る（同じ部屋・人物を見て当てるリークを避ける）
    if len(videos) >= 2:
        totals = {"correct": 0.0, "absent_hit": 0.0, "absent": 0, "present_miss": 0.0, "present": 0}
        for held_out, (name, features, targets) in enumerate(videos):
            train_x = np.concatenate([v[1] for i, v in enumerate(videos) if i != held_out])
            train_y = np.concatenate([v[2] for i, v in enumerate(videos) if i != held_out])
            metrics = evaluate(fit(train_x, train_y), features, targets, args.threshold)
            n_present = metrics["n"] - metrics["n_absent"]
            print(f"  {name}: acc={metrics['accuracy']:.3f} 不在の検出率={metrics['absent_recall']:.3f} "
                  f"在席→不在の誤り={metrics['present_as_absent']:.3f} "
                  f"(n={metrics['n']}, 不在={metrics['n_absent']})")
            totals["correct"] += metrics["accuracy"] * metrics["n"]
            totals["absent_hit"] += metrics["absent_recall"] * metrics["n_absent"]
            totals["absent"] += metrics["n_absent"]
            totals["present_miss"] += metrics["present_as_absent"] * n_present
            totals["present"] += n_present
        total = totals["absent"] + totals["present"]
        print(f"leave-one-video-out: acc={totals['correct'] / total:.3f} "
              f"不在の検出率={totals['absent_hit'] / max(totals['absent'], 1):.3f} "
              f"在席→不在の誤り={totals['present_miss'] / max(totals['present'], 1):.3f}")

    features = np.concatenate([v[1] for v in videos])
    targets = np.concatenate([v[2] for v in videos])
    model = fit(features, targets)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"model_state_dict": model.state_dict(), "threshold": args.threshold,
                "videos": [v[0] for v in videos]}, output)
    print(f"{len(videos)} 本・{len(targets)} フレーム（不在 {int((targets == 0).sum())}）で学習 → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
