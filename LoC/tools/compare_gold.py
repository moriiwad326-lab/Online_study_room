"""人手のゴールド集合（gold/gold.csv）と、qwen のラベルの一致度を測る。

ラベリング方式（プロンプト・合成ルール）を変えたら、ここで新旧を並べて比べる。
ゴールドは私（人）が「現在の画像 + 直前4枚」を見て、段階と人が写っているかを付けたもの。
段階は旧スケール（0 / 25 / 40 / 75 / 100）で付けてある。現在の qwen は 0 / 20 / 40 / 70 / 100
で答え、加点も無いので、ゴールド 75 のフレームは最高でも 70 になり MAE に 5 点の下駄が乗る。

    python compare_gold.py                              # datasets/train・test の labels.csv を比べる
    python compare_gold.py --labels labels.csv labels_v2.csv
    python compare_gold.py LoC/datasets/test            # 置き場所を明示する場合（複数可）

ゴールドの各動画は、渡したルート（既定: datasets/train と datasets/test）のうち
その動画ディレクトリがある方から読む。

出力する指標:
- LoC の MAE … ゴールドの段階を正解の LoC とみなした平均絶対誤差
- 不在の0率 … ゴールドで「無人・突っ伏し（段階0）」のフレームで LoC が 10 未満になった割合
- 在席の誤0 … ゴールドで学習中（段階70以上）なのに LoC が 10 未満になった割合
- person_visible の一致率 … attributes.jsonl の qwen 判定（新スキーマのみ）
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GOLD = os.path.join(SCRIPT_DIR, 'gold', 'gold.csv')
DATASETS_ROOT = os.path.join(os.path.dirname(SCRIPT_DIR), 'datasets')
DEFAULT_ROOTS = [os.path.join(DATASETS_ROOT, 'train'), os.path.join(DATASETS_ROOT, 'test')]

# LoC がこれ未満なら「0 扱い」とみなす
ZERO_THRESHOLD = 10.0


def load_gold(path: str) -> List[Dict]:
    with open(path, encoding='utf-8', newline='') as handle:
        return [
            {'video': row['video'], 'frame': int(row['frame']),
             'person_visible': row['person_visible'].strip().lower() == 'true',
             'level': float(row['level'])}
            for row in csv.DictReader(handle)
        ]


def load_labels(path: str) -> Dict[int, float]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8', newline='') as handle:
        return {int(row['frame']): float(row['LoC']) for row in csv.DictReader(handle)
                if float(row['LoC']) >= 0}


def load_presence(path: str) -> Dict[int, bool]:
    presence: Dict[int, bool] = {}
    if not os.path.exists(path):
        return presence
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            visible = record.get('attributes', {}).get('person_visible')
            if record.get('schema_version', 0) >= 3 and isinstance(visible, bool):
                presence[int(record['frame'])] = visible
    return presence


def find_video_dir(roots: List[str], video: str) -> Optional[str]:
    """roots のうち video のディレクトリがある最初のものを返す。"""
    for root in roots:
        path = os.path.join(root, video)
        if os.path.isdir(path):
            return path
    return None


def summarize(pairs: List[Tuple[Dict, float]]) -> Dict[str, Optional[float]]:
    if not pairs:
        return {'n': 0}
    absent = [loc for gold, loc in pairs if gold['level'] == 0]
    studying = [loc for gold, loc in pairs if gold['level'] >= 70]
    return {
        'n': len(pairs),
        'mae': sum(abs(gold['level'] - loc) for gold, loc in pairs) / len(pairs),
        'absent_zero': (sum(loc < ZERO_THRESHOLD for loc in absent) / len(absent)) if absent else None,
        'studying_zero': (sum(loc < ZERO_THRESHOLD for loc in studying) / len(studying)) if studying else None,
    }


def format_rate(value: Optional[float]) -> str:
    return '  -  ' if value is None else f'{value * 100:5.1f}%'


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='ゴールド集合と qwen のラベルの一致度を測る')
    parser.add_argument('roots', nargs='*', default=DEFAULT_ROOTS,
                        help='動画ごとのディレクトリを含む親（複数可）。既定: datasets/train と datasets/test')
    parser.add_argument('--gold', default=DEFAULT_GOLD)
    parser.add_argument('--labels', nargs='+', default=['labels.csv'],
                        help='比べるラベルファイル名（各動画ディレクトリ内）')
    args = parser.parse_args(argv)

    gold = load_gold(args.gold)
    videos = sorted({row['video'] for row in gold})

    video_dirs = {video: find_video_dir(args.roots, video) for video in videos}
    missing = sorted(v for v, d in video_dirs.items() if d is None)
    if missing:
        print(f'ゴールドにあるが {args.roots} に無い動画: {missing}', file=sys.stderr)
    video_dirs = {v: d for v, d in video_dirs.items() if d is not None}

    print(f'ゴールド {len(gold)} フレーム（{len(videos)} 本）')
    print(f'{"ラベル":<16} {"n":>3} {"MAE":>6} {"不在の0率":>9} {"在席の誤0":>9}')
    for name in args.labels:
        pairs = []
        for video, video_dir in video_dirs.items():
            labels = load_labels(os.path.join(video_dir, name))
            pairs += [(row, labels[row['frame']]) for row in gold
                      if row['video'] == video and row['frame'] in labels]
        stats = summarize(pairs)
        if not stats['n']:
            print(f'{name:<16}   0')
            continue
        print(f'{name:<16} {stats["n"]:>3} {stats["mae"]:6.1f} '
              f'{format_rate(stats["absent_zero"]):>9} {format_rate(stats["studying_zero"]):>9}')

    hits = total = 0
    for video, video_dir in video_dirs.items():
        presence = load_presence(os.path.join(video_dir, 'attributes.jsonl'))
        for row in gold:
            if row['video'] == video and row['frame'] in presence:
                total += 1
                hits += presence[row['frame']] == row['person_visible']
    if total:
        print(f'person_visible の一致率: {hits}/{total} = {hits / total * 100:.1f}%')
    return 0


if __name__ == '__main__':
    sys.exit(main())
