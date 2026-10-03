"""`features.npz` + `labels.csv` から LoC 学習用の時系列データセットを組み立てる。

1サンプル = 「直近 seq_len フレームの特徴列」→「最終フレームの LoC ラベル」。
リアルタイム推論では未来フレームを参照できないため、窓は常に**過去側**へ取る。

除外ルール:
- `qwen3_5.py` が判定不能時に返す `LoC = -1` の行は教師にならないので、その行を
  最終フレームとする窓は作らない（README にある「マスク処理」に相当）。窓の途中に
  -1 の行が含まれるのは問題ない（入力特徴は有効なため）。
- 窓は1つの動画ディレクトリ内で閉じる。動画をまたいだ窓は作らない。
- 先頭 seq_len-1 フレームは窓が埋まらないので使わない。

学習/検証の分割は2種類を選べる（`split`）。

- `"video"`（既定・推奨）: **動画単位**で train / val に振り分ける。同一動画は片側にしか
  入らないので、人物・部屋・服装・カメラ位置が train と val で共有されない。ConvNeXt の
  768次元は ImageNet 特徴なので背景や人物を強くエンコードしており、同一動画が両側に
  入ると「誰のどの部屋か」を手がかりに当てられてしまう。汎化性能を測るならこちら。
- `"tail"`（旧挙動）: 動画ごとに時系列順で後ろ val_ratio を検証に回す。同一動画が両側に
  入るためリークがあり、数字は楽観的に出る。学習が回っているかの確認用。

どちらもランダム分割はしない。連続フレームはほぼ同じ内容なので、シャッフルして分割すると
検証データが訓練データとほぼ同一になり、リークが最大化されるため。

`label_smooth` でラベル系列に中央値フィルタをかけられる。qwen のラベルは窓を1フレームずつ
ずらして独立に生成されるため、隣接フレームが入力の95%を共有しているにもかかわらず 45% の
フレームでラベルが変わり、lag-1 自己相関は 0.37 しかない（分散の約6割がノイズ）。集中度は
本来なめらかに変化する量なので、中央値フィルタは信号を保ったままノイズだけを削れる。
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


def load_labels(labels_path: str | Path) -> Dict[int, float]:
    """labels.csv を {フレーム番号: LoC} として読み込む。

    属性ベースのラベリング（tools/loc_rubric.py）では LoC が実数になるため float で読む。
    判定不能の行は `INVALID_LOC`（-1）のまま返し、呼び出し側で除外する。
    """
    labels: Dict[int, float] = {}
    with open(labels_path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                labels[int(row["frame"])] = float(row["LoC"])
            except (TypeError, ValueError):
                continue
    return labels


def median_filter(values: Sequence[float], window: int) -> np.ndarray:
    """1次元系列に中央値フィルタをかける（端は端点の値で埋める）。

    `scipy.signal.medfilt` は端をゼロ埋めするため、系列の先頭・末尾のラベルが 0 側へ
    引っ張られて壊れる。ここでは端点を複製して埋める（edge padding）。

    window は奇数。1以下なら何もせずそのまま返す。
    """
    series = np.asarray(values, dtype=np.float64)
    if window <= 1 or series.size == 0:
        return series.copy()
    if window % 2 == 0:
        raise ValueError(f"window は奇数にしてください: {window}")
    if series.size < window:
        # 系列がフィルタ幅より短い場合は全体の中央値で埋めるのが素直
        return np.full_like(series, float(np.median(series)))

    half = window // 2
    padded = np.pad(series, half, mode="edge")
    # (N, window) のスライディング窓を作って行ごとの中央値を取る
    windows = np.lib.stride_tricks.sliding_window_view(padded, window)
    return np.median(windows, axis=1)


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

    roots:        features.npz / labels.csv を含むディレクトリ、またはその親
    seq_len:      LSTM に入れる時系列長 T
    subset:       "train" / "val" / "all"
    split:        "video"（動画単位で分割）/ "tail"（動画ごとに末尾を検証に回す）
    val_ratio:    検証に回す割合。split="video" では「窓の数の割合」の目標値として使い、
                  実際には動画単位で切るため厳密には一致しない。subset="all" では無視
    val_videos:   検証に回す動画ディレクトリ名を明示する（split="video" のときのみ有効）。
                  指定すると val_ratio は無視される。leave-one-video-out に使う
    label_smooth: ラベル系列にかける中央値フィルタの幅（奇数）。1 で無効
    """

    def __init__(
        self,
        roots: Sequence[str | Path] | str | Path,
        seq_len: int = 20,
        subset: str = "all",
        val_ratio: float = 0.2,
        split: str = "video",
        val_videos: Optional[Sequence[str]] = None,
        label_smooth: int = 1,
    ):
        if subset not in ("train", "val", "all"):
            raise ValueError(f"subset は train/val/all のいずれか: {subset!r}")
        if split not in ("video", "tail"):
            raise ValueError(f"split は video/tail のいずれか: {split!r}")
        if seq_len < 1:
            raise ValueError(f"seq_len は1以上: {seq_len}")

        self.seq_len = seq_len
        self.subset = subset
        self.split = split
        self.label_smooth = label_smooth

        self._image: List[np.ndarray] = []
        self._hand: List[np.ndarray] = []
        # (動画インデックス, 窓の最終フレーム位置, ラベル)
        self.samples: List[Tuple[int, int, float]] = []
        self.source_dirs: List[Path] = []
        self.val_video_names: List[str] = []

        dataset_dirs = find_dataset_dirs(roots)
        if not dataset_dirs:
            raise FileNotFoundError(
                f"{FEATURES_FILENAME} と {LABELS_FILENAME} が揃ったディレクトリがありません: {roots}"
            )

        # 先に全動画を読み、動画ごとの窓リストを作る（分割の判断に窓の数が必要）
        windows_per_video: List[List[Tuple[int, int, float]]] = [
            self._load_one(dataset_dir) for dataset_dir in dataset_dirs
        ]

        if subset == "all":
            for windows in windows_per_video:
                self.samples.extend(windows)
            return

        if split == "tail":
            # 動画ごとに時系列順（_load_one が昇順で返す）で前後に分割する。
            # 同一動画が train と val の両方に入るためリークがあることに注意。
            for windows in windows_per_video:
                if not windows:
                    continue
                split_at = int(round(len(windows) * (1.0 - val_ratio)))
                self.samples.extend(windows[:split_at] if subset == "train" else windows[split_at:])
            return

        # split == "video": 動画をまるごと train / val に振り分ける
        val_indices = self._choose_val_videos(dataset_dirs, windows_per_video, val_ratio, val_videos)
        self.val_video_names = [dataset_dirs[i].name for i in sorted(val_indices)]
        for index, windows in enumerate(windows_per_video):
            is_val = index in val_indices
            if (subset == "val") == is_val:
                self.samples.extend(windows)

    @staticmethod
    def _choose_val_videos(
        dataset_dirs: Sequence[Path],
        windows_per_video: Sequence[Sequence[Tuple[int, int, float]]],
        val_ratio: float,
        val_videos: Optional[Sequence[str]],
    ) -> set:
        """検証に回す動画のインデックス集合を決める。"""
        if val_videos is not None:
            wanted = set(val_videos)
            chosen = {i for i, d in enumerate(dataset_dirs) if d.name in wanted}
            missing = wanted - {dataset_dirs[i].name for i in chosen}
            if missing:
                raise ValueError(f"val_videos に指定された動画が見つかりません: {sorted(missing)}")
            return chosen

        total = sum(len(w) for w in windows_per_video)
        if total == 0 or val_ratio <= 0:
            return set()

        # 末尾の動画から順に、窓の数が val_ratio に達するまで検証へ回す（決定論的）。
        # 動画単位なので val_ratio は目標値で、厳密には一致しない。
        chosen: set = set()
        accumulated = 0
        non_empty = [i for i, w in enumerate(windows_per_video) if w]
        for index in reversed(non_empty):
            if accumulated >= val_ratio * total:
                break
            chosen.add(index)
            accumulated += len(windows_per_video[index])
        # 全動画が検証に行くと学習データが無くなるので、最低1本は学習に残す
        if len(chosen) == len(non_empty):
            chosen.discard(max(chosen))
        return chosen

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

        # 教師にできる窓だけを時系列順に集める。
        # 未ラベル（ラベリングのウォームアップ分）と判定不能(-1)は教師にしない。
        positions: List[int] = []
        values: List[float] = []
        for position in range(self.seq_len - 1, len(frames)):
            loc = labels.get(int(frames[position]))
            if loc is None or loc <= INVALID_LOC:
                continue
            positions.append(position)
            values.append(float(loc))

        # ラベルの時間方向の平滑化。qwen は窓をずらしながら独立に判定するためラベルに
        # 独立ノイズが乗るが、集中度自体はなめらかに変化する。中央値フィルタはステップ
        # （実際に集中が切れた瞬間）を保ったままスパイクだけを削る。
        # 平滑化後は整数でなくなるが、このタスクは回帰なのでそのまま教師に使える。
        # 有効ラベルのみを詰めた系列に対してかけるので、-1 や欠損が混ざることはない。
        smoothed = median_filter(values, self.label_smooth)

        return [
            (video_index, position, float(value))
            for position, value in zip(positions, smoothed)
        ]

    def label_values(self) -> np.ndarray:
        """このデータセットに含まれる教師ラベルの配列（ベースライン計算用）。"""
        return np.array([loc for _, _, loc in self.samples], dtype=np.float64)

    def video_names(self) -> List[str]:
        """サンプルが属する動画名の一覧（重複なし・出現順）。"""
        seen: List[str] = []
        for video_index, _, _ in self.samples:
            name = self.source_dirs[video_index].name
            if name not in seen:
                seen.append(name)
        return seen

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
    split: str = "video",
    val_videos: Optional[Sequence[str]] = None,
    label_smooth: int = 1,
) -> Tuple[LoCSequenceDataset, Optional[LoCSequenceDataset]]:
    """学習用と検証用のデータセットを作る（検証が空なら None を返す）。"""
    common = dict(seq_len=seq_len, val_ratio=val_ratio, split=split,
                  val_videos=val_videos, label_smooth=label_smooth)
    train_set = LoCSequenceDataset(roots, subset="train", **common)
    val_set = LoCSequenceDataset(roots, subset="val", **common)
    return train_set, (val_set if len(val_set) > 0 else None)
