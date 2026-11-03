"""`datasets/train/` と `datasets/test/` の動画ディレクトリを `TEST_VIDEOS` どおりに振り分け直す。

データ作成（`dataset_creation.py` / `convert_frame_from_video.py` / `build_features.py`）は
既定で `datasets/train/<動画名>/` に直接書き出す。test に回す動画は `TEST_VIDEOS` で固定して
おき、このスクリプトで train ↔ test 間を**移動**して揃える（コピーではないので両側に残らない）。
新しい動画を test に回したいときは `TEST_VIDEOS` に名前を足してから実行する。

test は最終評価専用で、学習（検証によるエポック選択を含む）には使わない。

`TEST_VIDEOS` は、LoC の平均・分散・20点刻みの分布が train とほぼ同じになり、行数が
およそ 8:2 になる組み合わせを探索して選んだ（2026-11-03 時点: train 27本・15204行 /
test 8本・3884行、平均 54.6 / 54.8、SD 25.9 / 26.2）。
同じ動画が別名で2つ入っていると train と test にまたがってリークするので、重複は事前に
片方を消しておくこと（1000006459 - H M と 202607120134 が同一だった）。

使い方:
    python LoC/tools/split_dataset.py             # datasets/{train,test} を TEST_VIDEOS どおりに揃える
    python LoC/tools/split_dataset.py --dry-run   # 移動せずに振り分けだけ表示
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

LOC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASETS = LOC_ROOT / "datasets"
SUBSETS = ("train", "test")

# 最終評価専用。学習（検証によるエポック選択を含む）には使わない
TEST_VIDEOS = (
    "1000006458 - H M",
    "202606091834",
    "202606111845",
    "202610171708",
    "IMG_7394",
    "IMG_7636",
    "IMG_7655",
    "IMG_7656",
)

REQUIRED_FILES = ("features.npz", "labels.csv")


def subset_for(video_name: str) -> str:
    return "test" if video_name in TEST_VIDEOS else "train"


def split(datasets_root: Path, dry_run: bool = False) -> int:
    # 動画名 → 現在の置き場所（train/test）
    located: Dict[str, List[str]] = {}
    for subset in SUBSETS:
        root = datasets_root / subset
        if not root.exists():
            continue
        for video in sorted(p for p in root.iterdir() if p.is_dir()):
            located.setdefault(video.name, []).append(subset)

    duplicated = sorted(name for name, subsets in located.items() if len(subsets) > 1)
    if duplicated:
        # 両側にあると内容が同じか分からないので自動では消さない
        print(f"train と test の両方にある動画（リークになるので片方を手で消すこと）: {duplicated}",
              file=sys.stderr)
        return 1

    missing_test = sorted(set(TEST_VIDEOS) - set(located))
    if missing_test:
        print(f"TEST_VIDEOS のうち {datasets_root} に無い動画: {missing_test}", file=sys.stderr)

    counts = {subset: 0 for subset in SUBSETS}
    moved = 0
    incomplete: List[str] = []
    for name in sorted(located):
        current = located[name][0]
        target = subset_for(name)
        counts[target] += 1
        src = datasets_root / current / name
        if not all((src / f).exists() for f in REQUIRED_FILES):
            incomplete.append(f"{target}/{name}")
        if current == target:
            print(f"{target:<5} {name}")
            continue
        print(f"{target:<5} {name}  （{current} から移動）")
        moved += 1
        if not dry_run:
            dest = datasets_root / target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))

    if incomplete:
        print(f"\n{'/'.join(REQUIRED_FILES)} が揃っておらず学習・評価で使われない動画: {incomplete}")
    print(f"\ntrain {counts['train']}本 / test {counts['test']}本（移動 {moved}本）"
          + ("（dry-run: 移動していません）" if dry_run else ""))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="datasets/train・test の動画を TEST_VIDEOS どおりに振り分け直す")
    parser.add_argument("--datasets", default=str(DEFAULT_DATASETS),
                        help=f"train/ と test/ を含むディレクトリ。既定: {DEFAULT_DATASETS}")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return split(Path(args.datasets), args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
