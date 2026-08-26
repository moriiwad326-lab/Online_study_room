"""`features.npz` + `labels.csv` から LoC 学習用の時系列データセットを組み立てる。

1サンプル = 「直近 seq_len フレームの特徴列」→「最終フレームの LoC ラベル」。
リアルタイム推論では未来フレームを参照できないため、窓は常に**過去側**へ取る。

除外ルール:
- `qwen3_5.py` が判定不能時に返す `LoC = -1` の行は教師にならないので、その行を
  最終フレームとする窓は作らない（README にある「マスク処理」に相当）。窓の途中に
  -1 の行が含まれるのは問題ない（入力特徴は有効なため）。
- 窓は1つの動画ディレクトリ内で閉じる。動画をまたいだ窓は作らない。
- 先頭 seq_len-1 フレームは窓が埋まらないので使わない。

学習/検証の分割は動画ごとに**時系列順**で行う（後ろ val_ratio を検証に回す）。
連続フレームはほぼ同じ内容なので、ランダム分割すると検証データが訓練データと
ほぼ同一になりリークするため。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
from torch.utils.data import Dataset

FEATURES_FILENAME = "features.npz"
LABELS_FILENAME = "labels.csv"
INVALID_LOC = -1


def load_labels(labels_path: str | Path) -> Dict[int, int]:
    """labels.csv を {フレーム番号: LoC} として読み込む。"""
    labels: Dict[int, int] = {}
    with open(labels_path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                labels[int(row["frame"])] = int(row["LoC"])
            except (TypeError, ValueError):
                continue
    return labels


def find_dataset_dirs(roots: Sequence[str | Path] | str | Path) -> List[Path]:
    """features.npz と labels.csv が揃ったディレクトリを列挙する。"""
    if isinstance(roots, (str, Path)):
        roots = [roots]

    found: List[Path] = []
    for root in roots:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            raise FileNotFoundError(f"入力パスが存在しません: {root_path}")

        candidates = [root_path, *sorted(p for p in root_path.iterdir() if p.is_dir())] \
            if root_path.is_dir() else []
        for candidate in candidates:
            if (candidate / FEATURES_FILENAME).exists() and (candidate / LABELS_FILENAME).exists():
                found.append(candidate)
    return found


class LoCSequenceDataset(Dataset):
    """(img_feat (T, 768), hand_feat (T, 162), loc (1,)) を返すデータセット。

    roots:     features.npz / labels.csv を含むディレクトリ、またはその親
    seq_len:   LSTM に入れる時系列長 T
    subset:    "train" / "val" / "all"
    val_ratio: 各動画の末尾から検証に回す割合（subset="all" のときは無視）
    """

    def __init__(
        self,
        roots: Sequence[str | Path] | str | Path,
        seq_len: int = 20,
        subset: str = "all",
        val_ratio: float = 0.2,
    ):
        if subset not in ("train", "val", "all"):
            raise ValueError(f"subset は train/val/all のいずれか: {subset!r}")
        if seq_len < 1:
            raise ValueError(f"seq_len は1以上: {seq_len}")

        self.seq_len = seq_len
        self.subset = subset

        self._image: List[np.ndarray] = []
        self._hand: List[np.ndarray] = []
        # (動画インデックス, 窓の最終フレーム位置, ラベル)
        self.samples: List[Tuple[int, int, float]] = []
        self.source_dirs: List[Path] = []

        dataset_dirs = find_dataset_dirs(roots)
        if not dataset_dirs:
            raise FileNotFoundError(
                f"{FEATURES_FILENAME} と {LABELS_FILENAME} が揃ったディレクトリがありません: {roots}"
            )

        for dataset_dir in dataset_dirs:
            windows = self._load_one(dataset_dir)
            if not windows:
                continue
            # 時系列順（_load_one が昇順で返す）に前後で分割する
            split_at = int(round(len(windows) * (1.0 - val_ratio)))
            if subset == "train":
                windows = windows[:split_at]
            elif subset == "val":
                windows = windows[split_at:]
            self.samples.extend(windows)

    def _load_one(self, dataset_dir: Path) -> List[Tuple[int, int, float]]:
        data = np.load(dataset_dir / FEATURES_FILENAME)
        frames = data["frames"]
        image = data["image"].astype(np.float32)
        hand = data["hand"].astype(np.float32)
        if not (len(frames) == len(image) == len(hand)):
            raise ValueError(f"features.npz の長さが不一致です: {dataset_dir}")

        labels = load_labels(dataset_dir / LABELS_FILENAME)

        video_index = len(self._image)
        self._image.append(image)
        self._hand.append(hand)
        self.source_dirs.append(dataset_dir)

        windows: List[Tuple[int, int, float]] = []
        for position in range(self.seq_len - 1, len(frames)):
            loc = labels.get(int(frames[position]))
            # 未ラベル（ウォームアップ分）と判定不能(-1)は教師にしない
            if loc is None or loc == INVALID_LOC:
                continue
            windows.append((video_index, position, float(loc)))
        return windows

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int):
        video_index, position, loc = self.samples[index]
        start = position - self.seq_len + 1
        img_feat = torch.from_numpy(self._image[video_index][start : position + 1])
        hand_feat = torch.from_numpy(self._hand[video_index][start : position + 1])
        target = torch.tensor([loc], dtype=torch.float32)
        return img_feat, hand_feat, target


def build_datasets(
    roots: Sequence[str | Path] | str | Path,
    seq_len: int = 20,
    val_ratio: float = 0.2,
) -> Tuple[LoCSequenceDataset, Optional[LoCSequenceDataset]]:
    """学習用と検証用のデータセットを作る（検証が空なら None を返す）。"""
    train_set = LoCSequenceDataset(roots, seq_len=seq_len, subset="train", val_ratio=val_ratio)
    val_set = LoCSequenceDataset(roots, seq_len=seq_len, subset="val", val_ratio=val_ratio)
    return train_set, (val_set if len(val_set) > 0 else None)
