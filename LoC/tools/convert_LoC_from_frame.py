"""フレーム群を qwen3.5 に通して**属性**を判定し、LoC ラベルを合成する。

出力はディレクトリ内に2つ:

- `attributes.jsonl` … qwen が返した属性の生データ（1行1フレーム）。**これが一次データ**。
- `labels.csv` … `loc_rubric.compose_loc` で合成した LoC（`frame,LoC`）。**派生物**。

合成ルールを変えたくなったら `--recompose` を使う。qwen を呼び直さずに
`attributes.jsonl` から `labels.csv` を作り直せる（これが属性を保存する最大の利点）。

## サンプリング

`--stride` を指定すると N フレームおきに推論し、間は線形補間でラベルを埋める。
集中度は本来なめらかに変化するので、5秒刻みで全フレームを個別に判定するのは冗長。
推論コストが 1/N になり、浮いた分を自己一貫性（`--samples`）や動画の追加に回せる。

先頭の数フレームは文脈窓が埋まらないため、既定では評価せず CSV にも現れない
（`--keep-warmup` で評価させられるが、過去との比較ができないので推奨しない）。

長時間の実行を前提に、`attributes.jsonl` に既にあるフレームは自動でスキップする。
中断しても同じコマンドで続きから再開できる。

使い方:
  python convert_LoC_from_frame.py /path/to/frames_dir --stride 2 --samples 1
  python convert_LoC_from_frame.py /path/to/frames_dir --recompose   # 再推論なしで作り直す
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from loc_rubric import NOT_VISIBLE, SCHEMA_VERSION, compose_loc

IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
ATTRIBUTES_FILENAME = 'attributes.jsonl'
LABELS_FILENAME = 'labels.csv'
INVALID_LOC = -1

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')


def list_image_files(input_dir: str) -> List[str]:
    files = [f for f in os.listdir(input_dir)
             if f.lower().endswith(IMAGE_EXTS) and os.path.isfile(os.path.join(input_dir, f))]
    if not files:
        raise FileNotFoundError(f"No image files found in {input_dir!r} with extensions {IMAGE_EXTS}")

    def sort_key(filename: str):
        match = re.search(r"(\d+)(?!.*\d)", filename)
        return (0, int(match.group(1))) if match else (1, filename)

    files.sort(key=sort_key)
    return files


def extract_frame_number(filename: str) -> Optional[int]:
    match = re.search(r"(\d+)(?!.*\d)", filename)
    try:
        return int(match.group(1)) if match else None
    except Exception:
        return None


def load_attributes(path: str) -> Dict[int, Dict[str, Any]]:
    """attributes.jsonl を {フレーム番号: レコード} として読み込む（無ければ空）。"""
    records: Dict[int, Dict[str, Any]] = {}
    if not os.path.exists(path):
        return records
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                records[int(record['frame'])] = record
            except Exception:
                continue  # 壊れた行は無視して読み進める（中断時の書きかけ対策）
    return records


def append_attributes(path: str, record: Dict[str, Any]) -> None:
    """1件を追記して即 flush する（中断しても途中までが残るように）。"""
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')


def interpolate_labels(points: Sequence[Tuple[int, float]], frames: Sequence[int]) -> List[Tuple[int, float]]:
    """評価済みフレームのLoCから、間のフレームを線形補間で埋める。

    points: (フレーム番号, LoC) の昇順リスト（評価できたものだけ）
    frames: ラベルを出力したいフレーム番号の昇順リスト
    評価点の外側（最初の評価点より前）は埋めない。
    """
    if not points:
        return []
    rows: List[Tuple[int, float]] = []
    index = 0
    for frame in frames:
        if frame < points[0][0]:
            continue  # ウォームアップ区間。ラベルを作らない
        while index + 1 < len(points) and points[index + 1][0] <= frame:
            index += 1
        left_frame, left_loc = points[index]
        if index + 1 >= len(points):
            rows.append((frame, left_loc))  # 最後の評価点より後ろは最後の値を保持
            continue
        right_frame, right_loc = points[index + 1]
        if right_frame == left_frame:
            rows.append((frame, left_loc))
            continue
        ratio = (frame - left_frame) / (right_frame - left_frame)
        rows.append((frame, left_loc + (right_loc - left_loc) * ratio))
    return rows


def write_labels_csv(output_path: str, rows: Sequence[Tuple[int, Optional[float]]]) -> None:
    with open(output_path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['frame', 'LoC'])
        for frame, loc in rows:
            writer.writerow([frame, INVALID_LOC if loc is None else f"{loc:.3f}"])


def compose_labels_from_attributes(input_dir: str, files: Sequence[str]) -> List[Tuple[int, float]]:
    """attributes.jsonl から labels.csv を組み立てて書き出し、評価点を返す。"""
    records = load_attributes(os.path.join(input_dir, ATTRIBUTES_FILENAME))
    points: List[Tuple[int, float]] = []
    for frame in sorted(records):
        loc = compose_loc(records[frame].get('attributes', {}))
        if loc is not None:
            points.append((frame, loc))

    all_frames = sorted(filter(None, (extract_frame_number(f) for f in files)))
    rows = interpolate_labels(points, all_frames)
    write_labels_csv(os.path.join(input_dir, LABELS_FILENAME), rows)
    logging.info(f"{len(points)} 件の評価点から {len(rows)} 行の labels.csv を書き出しました")
    return points


def compute_attributes_for_images(
    input_dir: str,
    files: Sequence[str],
    offsets: Optional[Sequence[int]] = None,
    stride: int = 1,
    samples: int = 1,
    model: Optional[str] = None,
    simulate: bool = False,
    skip_warmup: bool = True,
    resume: bool = True,
) -> int:
    """stride ごとに属性を判定し、attributes.jsonl へ追記する。新たに評価した件数を返す。"""
    attributes_path = os.path.join(input_dir, ATTRIBUTES_FILENAME)
    done = load_attributes(attributes_path) if resume else {}
    if done:
        logging.info(f"既存の属性 {len(done)} 件をスキップします（再開）")

    if simulate:
        qwen = None
        context_offsets = tuple(offsets) if offsets else (-12, -6, -3, -1, 0)
    else:
        import importlib
        qwen = importlib.import_module('qwen3_5')
        context_offsets = tuple(offsets) if offsets else qwen.DEFAULT_CONTEXT_OFFSETS

    # 文脈窓が埋まらない先頭フレームは評価しない。過去との比較ができず、
    # writing_increased / page_turned が意味を持たないため。
    warmup = -min(context_offsets) if skip_warmup else 0
    total = len(files)
    targets = [i for i in range(warmup, total, stride)]
    if targets and targets[-1] != total - 1:
        targets.append(total - 1)  # 末尾は必ず評価して補間の右端にする

    evaluated = 0
    started = time.time()
    for order, index in enumerate(targets, start=1):
        frame_number = extract_frame_number(files[index])
        if frame_number is None:
            frame_number = index
        if frame_number in done:
            continue

        if simulate:
            attributes = {
                'hands_in_frame': 2, 'phone': 'absent', 'pen_held': True,
                'pen_tip_on_paper': True, 'hand_on_material': True,
                'writing_increased': True, 'page_turned': False,
                'material_open': True, 'device_in_use': 'none',
                'head_posture': NOT_VISIBLE, 'note': '', 'samples': 1,
            }
        else:
            context = qwen.build_context_paths(
                [os.path.abspath(os.path.join(input_dir, f)) for f in files],
                index, context_offsets,
            )
            try:
                attributes = qwen.evaluate_attributes(
                    context, model=model or qwen.DEFAULT_MODEL, samples=samples)
            except Exception as exc:
                logging.error(f"frame {frame_number} の判定に失敗: {exc}")
                continue

        note = attributes.pop('note', '')
        sample_count = attributes.pop('samples', 1)
        loc = compose_loc(attributes)
        append_attributes(attributes_path, {
            'frame': frame_number,
            'schema_version': SCHEMA_VERSION,
            'attributes': attributes,
            'note': note,
            'samples': sample_count,
            'loc': loc,
        })
        evaluated += 1

        if evaluated % 20 == 0 or order == len(targets):
            elapsed = time.time() - started
            speed = elapsed / max(1, evaluated)
            remaining = (len(targets) - order) * speed
            logging.info(
                f"{order}/{len(targets)} frame={frame_number} LoC={loc if loc is None else round(loc, 2)} "
                f"({speed:.1f}秒/件, 残り約{remaining / 60:.0f}分)"
            )

    return evaluated


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='フレーム群を qwen3.5 に通して属性を判定し、LoC ラベルを合成する')
    parser.add_argument('input_dir', help='フレーム画像を含むディレクトリ')
    parser.add_argument('--stride', '-s', type=int, default=1,
                        help='何フレームおきに評価するか（既定1。間は線形補間で埋める）')
    parser.add_argument('--samples', '-n', type=int, default=1,
                        help='同じ入力を何回サンプリングして多数決を取るか（既定1）')
    parser.add_argument('--model', '-m', default=None, help='Ollama のモデル名')
    parser.add_argument('--offsets', default=None,
                        help='文脈フレームのオフセットをカンマ区切りで指定（既定 -12,-6,-3,-1,0）')
    parser.add_argument('--simulate', action='store_true', help='qwen を呼ばずにダミー属性を書く')
    parser.add_argument('--recompose', action='store_true',
                        help='再推論せず attributes.jsonl から labels.csv を作り直すだけ')
    parser.add_argument('--no-resume', action='store_true',
                        help='既存の attributes.jsonl を無視して最初から評価する')
    parser.add_argument('--keep-warmup', action='store_true',
                        help='文脈窓が埋まらない先頭フレームも評価する（非推奨）')
    args = parser.parse_args(argv)

    input_dir = args.input_dir
    if not os.path.isdir(input_dir):
        logging.error(f"ディレクトリが見つかりません: {input_dir!r}")
        return 2

    files = list_image_files(input_dir)
    offsets = [int(v) for v in args.offsets.split(',')] if args.offsets else None

    if not args.recompose:
        logging.info(f"{os.path.basename(input_dir)}: {len(files)} フレーム "
                     f"(stride={args.stride}, samples={args.samples})")
        compute_attributes_for_images(
            input_dir, files, offsets=offsets, stride=args.stride, samples=args.samples,
            model=args.model, simulate=args.simulate,
            skip_warmup=not args.keep_warmup, resume=not args.no_resume,
        )

    compose_labels_from_attributes(input_dir, files)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
