"""LoCSequenceDataset（dataset.py）の窓の切り出しとラベル除外を検証する。"""

import csv
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("torch")

from dataset import LoCSequenceDataset, build_datasets, load_labels

IMAGE_DIM = 768
HAND_DIM = 162


def make_dataset_dir(base: Path, name: str, locs):
    """frames=0..N-1 の features.npz と labels.csv を持つディレクトリを作る。"""
    directory = base / name
    directory.mkdir(parents=True, exist_ok=True)
    count = len(locs)
    frames = np.arange(count, dtype=np.int64)
    # 特徴には後で照合できるようフレーム番号を埋め込んでおく
    image = np.tile(frames.reshape(-1, 1), (1, IMAGE_DIM)).astype(np.float32)
    hand = np.tile(frames.reshape(-1, 1), (1, HAND_DIM)).astype(np.float32)
    np.savez_compressed(directory / "features.npz", frames=frames, image=image, hand=hand)

    with open(directory / "labels.csv", "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["frame", "LoC"])
        for frame, loc in zip(frames, locs):
            if loc is not None:
                writer.writerow([int(frame), loc])
    return directory


def test_load_labels_reads_frame_and_loc(tmp_path):
    directory = make_dataset_dir(tmp_path, "video", [3, 4, -1])

    labels = load_labels(directory / "labels.csv")

    assert labels == {0: 3, 1: 4, 2: -1}


def test_window_count_skips_warmup(tmp_path):
    make_dataset_dir(tmp_path, "video", [3] * 10)

    dataset = LoCSequenceDataset(tmp_path, seq_len=4, subset="all")

    # 先頭 seq_len-1 フレームは窓が埋まらない
    assert len(dataset) == 10 - 4 + 1


def test_invalid_loc_rows_are_excluded(tmp_path):
    # -1（qwen3.5 が判定不能）を最終フレームに持つ窓は作らない
    make_dataset_dir(tmp_path, "video", [3, 3, -1, 3, -1, 3])

    dataset = LoCSequenceDataset(tmp_path, seq_len=2, subset="all")

    targets = [float(dataset[i][2]) for i in range(len(dataset))]
    assert len(dataset) == 3
    assert all(target == 3.0 for target in targets)


def test_unlabeled_frames_are_excluded(tmp_path):
    # labels.csv に行が無いフレーム（ラベリングのウォームアップ分）も教師にしない
    make_dataset_dir(tmp_path, "video", [None, None, 2, 5])

    dataset = LoCSequenceDataset(tmp_path, seq_len=2, subset="all")

    assert sorted(float(dataset[i][2]) for i in range(len(dataset))) == [2.0, 5.0]


def test_sample_shapes_and_window_is_past_side(tmp_path):
    make_dataset_dir(tmp_path, "video", [4] * 6)

    dataset = LoCSequenceDataset(tmp_path, seq_len=3, subset="all")
    img_feat, hand_feat, target = dataset[0]

    assert img_feat.shape == (3, IMAGE_DIM)
    assert hand_feat.shape == (3, HAND_DIM)
    assert target.shape == (1,)
    # 最初の窓はフレーム 0,1,2（過去側に取る）
    assert [float(v) for v in img_feat[:, 0]] == [0.0, 1.0, 2.0]


def test_windows_do_not_span_videos(tmp_path):
    make_dataset_dir(tmp_path, "a", [3] * 5)
    make_dataset_dir(tmp_path, "b", [3] * 5)

    dataset = LoCSequenceDataset(tmp_path, seq_len=3, subset="all")

    assert len(dataset) == 2 * (5 - 3 + 1)
    for index in range(len(dataset)):
        img_feat, _, _ = dataset[index]
        column = [float(v) for v in img_feat[:, 0]]
        # 動画をまたぐとフレーム番号が不連続になるはず
        assert column == list(range(int(column[0]), int(column[0]) + 3))


def test_train_val_split_is_chronological(tmp_path):
    make_dataset_dir(tmp_path, "video", [3] * 12)

    train_set, val_set = build_datasets(tmp_path, seq_len=2, val_ratio=0.25)

    assert len(train_set) + len(val_set) == 12 - 2 + 1
    last_train_frame = float(train_set[len(train_set) - 1][0][-1, 0])
    first_val_frame = float(val_set[0][0][-1, 0])
    # 検証は必ず学習より後ろの時刻（連続フレームのリークを避ける）
    assert first_val_frame > last_train_frame
