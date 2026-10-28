"""人手のゴールド集合（gold/gold.csv）と、qwen のラベルの一致度を測る。

ラベリング方式（プロンプト・合成ルール）を変えたら、ここで新旧を並べて比べる。
ゴールドは私（人）が「現在の画像 + 直前4枚」を見て、段階（0 / 25 / 40 / 75 / 100）と
人が写っているかを付けたもの。

    python compare_gold.py output                       # labels.csv を比べる
    python compare_gold.py output --labels labels.csv labels_v2.csv

出力する指標:
- LoC の MAE … ゴールドの段階を正解の LoC とみなした平均絶対誤差
- 不在の0率 … ゴールドで「無人・突っ伏し（段階0）」のフレームで LoC が 10 未満になった割合
- 在席の誤0 … ゴールドで学習中（段階75以上）なのに LoC が 10 未満になった割合
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


def summarize(pairs: List[Tuple[Dict, float]]) -> Dict[str, Optional[float]]:
    if not pairs:
        return {'n': 0}
    absent = [loc for gold, loc in pairs if gold['level'] == 0]
    studying = [loc for gold, loc in pairs if gold['level'] >= 75]
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
    parser.add_argument('output_dir', help='動画ごとのディレクトリを含む親（tools/output）')
    parser.add_argument('--gold', default=DEFAULT_GOLD)
    parser.add_argument('--labels', nargs='+', default=['labels.csv'],
                        help='比べるラベルファイル名（各動画ディレクトリ内）')
    args = parser.parse_args(argv)

    gold = load_gold(args.gold)
    videos = sorted({row['video'] for row in gold})

    print(f'ゴールド {len(gold)} フレーム（{len(videos)} 本）')
    print(f'{"ラベル":<16} {"n":>3} {"MAE":>6} {"不在の0率":>9} {"在席の誤0":>9}')
    for name in args.labels:
        pairs = []
        for video in videos:
            labels = load_labels(os.path.join(args.output_dir, video, name))
            pairs += [(row, labels[row['frame']]) for row in gold
                      if row['video'] == video and row['frame'] in labels]
        stats = summarize(pairs)
        if not stats['n']:
            print(f'{name:<16}   0')
            continue
        print(f'{name:<16} {stats["n"]:>3} {stats["mae"]:6.1f} '
              f'{format_rate(stats["absent_zero"]):>9} {format_rate(stats["studying_zero"]):>9}')

    hits = total = 0
    for video in videos:
        presence = load_presence(os.path.join(args.output_dir, video, 'attributes.jsonl'))
        for row in gold:
            if row['video'] == video and row['frame'] in presence:
                total += 1
                hits += presence[row['frame']] == row['person_visible']
    if total:
        print(f'person_visible の一致率: {hits}/{total} = {hits / total * 100:.1f}%')
    return 0


if __name__ == '__main__':
    sys.exit(main())
