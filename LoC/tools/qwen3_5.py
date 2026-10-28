"""Ollama 経由で qwen3.5 に学習フレームの**段階**と**属性**を判定させる。

LoC は次の2段で作る（合成は `loc_rubric.compose_loc`）。

1. **段階コール**: 60秒前〜現在の5枚（-60 / -30 / -10 / -5 / 0 秒）を見せ、画像だけから
   0 / 25 / 40 / 75 / 100 の5段階で答えさせる。直前の様子ほど現在の集中を表すので、
   フレームは直前に寄せて選ぶ。40（普通）が軸で、75 / 25 は中間として柔軟に選ばせる。
2. **属性コール**: 観測可能な事実（ペン先が紙に触れているか等）を答えさせ、
   段階の中での細かい位置を ±25 の加減点で決める。

旧々版は「集中度を1〜5で答えて」と総合判断だけをさせていたが、ラベルが3と4に固まり、
「最初のフレームが非学習なら Level 2 で固定」のようなルールが窓の先頭1フレームへの強い
依存を生んでいた。段階の判定は大づかみに留め（段階の定義に具体的な行動を書く）、細かい
差は属性に任せる。

## なぜ属性を2回に分けて聞くのか

属性10個を1回のプロンプトでまとめて聞くと、**`writing_increased` が常に false になる**という
失敗が実測で出た（8フレーム試して8回とも false。目視では明らかに書き込みが増えている）。
一方、2枚の画像だけを見せて「書き込みは増えたか」だけを聞くと正しく true を返し、根拠も
具体的に述べる。つまり能力の問題ではなく、他の9項目に埋もれていた。

そこで質問を性質ごとに2回へ分ける。

1. **進捗コール**: 60秒前と現在の2枚を見せ、時間方向の変化だけを聞く
   （`writing_increased` / `page_turned`）
2. **状態コール**: 現在の1枚だけを見せ、その瞬間の静的な事実を聞く（残り8項目）

段階コールも属性とは別に聞く。点数を付けさせるプロンプトに属性の質問を混ぜると、
属性が点数に引きずられる（「集中度の点数は付けないでください」と書いているのはそのため）。

## 進捗コールは「既定 false」で聞く

分けただけでは不十分だった。素直に「増えているか」と聞くと **96% が true** になり、
人が離席していて2枚が実質同一のフレームでも true を返す（実測）。聞き方を4通り比較した
結果、「**既定は false。はっきり確認できる場合のみ true**」と明示する形が最も正確だった
（正解が分かっている6ペアで 5/6。素直に聞く形は 3/6）。VLM は「違いを探せ」と言われると
違いを作ってしまうので、判断を保留したときに倒れる先を false 側に固定しておく。
"""

from __future__ import annotations

import io
import json
import re
import statistics
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ollama import chat
from PIL import Image

from loc_rubric import ATTRIBUTE_NAMES, NOT_VISIBLE, normalize_attributes, normalize_level

DEFAULT_MODEL = "qwen3.5:9b"

# 送信前に画像の長辺をここまで縮める。qwen3.5 の視覚エンコーダは動的解像度なので、
# プロンプトのトークン数（＝レイテンシ）はおおむね画素数に比例する。元フレームは4Kも
# あるため、縮めないと1回の推論が数万トークンに膨れる。768px あれば、ノートの
# 書き込みが増えたかどうかの判定まで通ることを実測で確認している。
IMAGE_MAX_SIDE = 768
IMAGE_JPEG_QUALITY = 85

# 文脈フレームのオフセット（判定対象=0、負の値が過去）。フレーム間隔が5秒なら
# -60 / -30 / -10 / -5 / 0 秒。60秒ぶん12フレームのうち、直前に寄せて5枚を使う。
# 段階コールは5枚すべて、進捗コールは先頭（60秒前）と末尾（現在）の2枚だけを使う。
DEFAULT_CONTEXT_OFFSETS = (-12, -6, -2, -1, 0)

# フレームの切り出し間隔（秒）。dataset_creation の --rate の既定と同じ。
# 段階プロンプトで「何秒前の画像か」を伝えるのに使う
FRAME_INTERVAL_SECONDS = 5

LEVEL_PROMPT = """学習中の机を同じ角度から撮影した{count}枚の画像です。左から順に{times}の様子で、最後の1枚が現在です。
画像だけを見て、**現在**の学習者の集中の状態を次の5段階から1つ選んでください。
直前（後ろの画像）の様子ほど重視してください。

最初に、直前の画像に**人（手・腕・頭・上半身のどれか）が写っているか**を確認してください。
- 人がまったく写っていない（無人の机、ペンや教材が置かれているだけ）なら、それは離席です。必ず 0 にしてください。
- 「手が止まっている」と「人がいない」は違います。手が止まって見えるのは、人が写っている場合だけです。
- 100 と 75 は、人が写っていて、学習行動（書く・読む・教材に触れる・タイピング）が画像から確認できる場合だけ選べます。

100: 極めて高い集中（フロー状態）
     - ペン先の動きやタイピングなどの学習行動が、一定のペースで途切れなく続いている。
     - 人が写っていて、暗記や黙読などで手が止まっていても、手遊びなどの無駄な動きが一切ない。
75:  高い集中
     - 勉強以外のほかの作業をしていない。
     - 学習行動が続いている。ペンが止まる時間があっても、資料を見ている・考えている様子で、手遊びはないか1回程度
40:  普通
     - ペン回し、髪や顔を触る、机を指で叩くなどの手遊び・無意識の動作が散見される。
     - 作業ペースにムラがあり、ペンが止まっている時間と動いている時間が混在する。
     - 時折、視線が学習対象から外れている（頭部が上を向くなど）。
25:  学習対象から離れている時間が長い、学習に戻れていない。
     - 勉強以外の作業や行動時間のほうが長く、学習行動がほとんど見られない。
     - スマホを片手で触る、教材から顔・体が離れている、教材と関係ない物を触っている時間が、学習行動より長い（離席・居眠りまではいかない）
0:   退出・居眠り
     - 人がまったく写っていない（無人の机）。
     - スマートフォンを両手で操作している（ゲームやSNSなどの明らかな非学習行動）。
     - 机に突っ伏している、頭を机や腕に乗せて伏せている、または腕組みをして目を閉じている（居眠り）。
       真上や斜め上から撮っているので、突っ伏していると**頭頂部や後頭部（髪）が画面に大きく写り、顔もペンを持つ手も見えない**ことが多いです。
       頭が写っているのに学習行動が見えない場合は、突っ伏しを疑ってください。突っ伏しは他にどんな様子があっても 0 です。
     - 両手がフレームアウトしている（離席、または別の作業をしている）。

人が写っていて、どれかに迷ったら 40 を基準にして、はっきりした根拠があるときだけ上下に動かしてください。
75 と 25 は、画像の様子に合わせて柔軟に使ってください。

次のJSONだけを出力してください。

{{
  "level": 0 | 25 | 40 | 75 | 100,
  "reason": "<判断の根拠を40文字以内>"
}}
"""

PROGRESS_PROMPT = """2枚の画像は、同じ机を同じ角度から撮影したものです。1枚目が過去、2枚目が現在です。

**既定は false です。** 変化が「はっきり確認できる場合のみ」 true にしてください。
似ているがよく分からない、という場合は false です。推測で true にしないでください。

{
  "writing_increased": true | false,
      // 紙・ノート・解答用紙に、新しい文字・数式・線が増えたと明確に確認できるか。
  "page_turned": true | false,
      // ページがめくられた、または別の教材に替わったと明確に確認できるか。
  "evidence": "<増えた箇所・変わった箇所を30文字以内。変化が無ければ空文字>"
}

集中度の点数は付けないでください。
"""

STATE_PROMPT = """学習中の机を撮影した画像です。画面から確認できる事実だけを報告してください。

最も重要な原則: **推測しないこと。**
カメラの画角によっては、顔・上半身・机の一部が写っていません。
写っていないものは必ず null と答えてください。「たぶんこうだろう」で埋めてはいけません。

次のJSONだけを出力してください。

{
  "person_visible": true | false,
      // 人の体の一部（手・腕・頭・上半身のどれか）が少しでも写っているか。
      // 画面の端に指先や腕だけ見えている場合も true。無人の机（物が置かれているだけ）なら false。
  "hands_in_frame": 0 | 1 | 2,
      // 机の上（作業領域）に写っている手の数。手が写っていなければ 0。
  "phone": "absent" | "on_desk" | "in_hand",
      // スマートフォン。画面内に無ければ "absent"、机に置いてあるだけなら "on_desk"、
      // 手に持っている・画面を操作していれば "in_hand"。
  "pen_held": true | false,
      // ペン・シャープペンシルなどの筆記具を手で握っているか。
  "pen_tip_on_paper": true | false,
      // 筆記具の先端が紙・ノートに接しているか（＝書いている最中か）。
  "hand_on_material": true | false,
      // 手がノートや教材の上に置かれている・触れているか（押さえる、指でたどる等）。
  "material_open": true | false,
      // ノート・問題集・プリントなどが開いて置かれているか。
  "device_in_use": "none" | "pc" | "tablet",
      // **手がキーボードや画面に触れて操作している場合のみ** "pc" / "tablet"。
      // ノートPCやタブレットが机に置いてあるだけ、閉じている、画面が暗い場合は "none"。
  "head_posture": "upright" | "propped" | "down" | null,
      // 頭・上半身が写っている場合のみ。普通に起きていれば "upright"、
      // 頬杖をついていれば "propped"、机に突っ伏していれば "down"。
      // **頭が画面に写っていなければ必ず null**。
  "note": "<気づいた点を30文字以内。無ければ空文字>"
}

集中度の点数は付けないでください。あなたの仕事は観察の報告だけです。
"""

# 進捗コールが答える属性と、状態コールが答える属性
PROGRESS_ATTRIBUTES = ("writing_increased", "page_turned")
STATE_ATTRIBUTES = tuple(name for name in ATTRIBUTE_NAMES if name not in PROGRESS_ATTRIBUTES)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """応答から最初のJSONオブジェクトを取り出す。失敗したら None。"""
    if not text:
        return None
    try:
        obj = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
        except Exception:
            return None
    if isinstance(obj, list):
        obj = next((item for item in obj if isinstance(item, dict)), None)
    return obj if isinstance(obj, dict) else None


def _resize_image_bytes(image_path: str, max_side: int = IMAGE_MAX_SIDE) -> bytes:
    """長辺が max_side 以下になるよう縮小し、JPEGバイト列として返す。

    縮小版をディスクに書かずに済ませ、4Kの元フレームをそのまま送るのも避ける。
    """
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        width, height = im.size
        scale = max_side / max(width, height)
        if scale < 1:
            im = im.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)
        buffer = io.BytesIO()
        im.save(buffer, format="JPEG", quality=IMAGE_JPEG_QUALITY)
        return buffer.getvalue()


def _ask(prompt: str, image_paths: Sequence[str], model: str, temperature: float) -> Dict[str, Any]:
    response = chat(
        model=model,
        messages=[{
            "role": "user",
            "content": prompt,
            "images": [_resize_image_bytes(path) for path in image_paths],
        }],
        format="json",
        think=False,  # 観察の報告であって推論課題ではない。思考トレースは
                      # レイテンシを約10倍にする割に、ここでは精度に寄与しない。
        options={
            "num_ctx": 8192,
            "temperature": temperature,
            "top_p": 0.9,
            "num_predict": 250,
        },
        keep_alive=True,
    )
    return _extract_json(response.message.content or "") or {}


def _vote(values: Sequence[Any]) -> Any:
    """複数回サンプリングした値の多数決。割れたら not_visible（＝教師にしない）。"""
    if not values:
        return NOT_VISIBLE
    counts = Counter(repr(value) for value in values)
    top, count = counts.most_common(1)[0]
    if count * 2 <= len(values):
        return NOT_VISIBLE  # 過半数が取れない＝モデル自身が判断できていない
    return next(value for value in values if repr(value) == top)


def _describe_times(offsets: Optional[Sequence[int]], count: int, interval: float) -> str:
    """段階プロンプトに入れる「各画像が何秒前か」の説明文。"""
    if offsets is None or len(offsets) != count:
        return "古い順"
    labels = [
        "現在" if offset == 0 else f"{-offset * interval:g}秒前"
        for offset in sorted(offsets)
    ]
    return "・".join(labels)


def evaluate_level(
    image_paths: Sequence[str],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.1,
    offsets: Optional[Sequence[int]] = None,
    interval: float = FRAME_INTERVAL_SECONDS,
) -> Tuple[Optional[int], str]:
    """5枚（時系列順、末尾が現在）から段階を1回判定する。読めなければ (None, 理由)。"""
    prompt = LEVEL_PROMPT.format(
        count=len(image_paths), times=_describe_times(offsets, len(image_paths), interval))
    raw = _ask(prompt, image_paths, model, temperature)
    reason = raw.get("reason")
    return normalize_level(raw.get("level")), (reason[:60] if isinstance(reason, str) else "")


def evaluate_attributes(
    image_paths: Sequence[str],
    model: str = DEFAULT_MODEL,
    samples: int = 1,
    offsets: Optional[Sequence[int]] = None,
    interval: float = FRAME_INTERVAL_SECONDS,
) -> Dict[str, Any]:
    """最後の画像について段階と属性を判定して返す。

    image_paths は時系列順で、先頭を「過去の基準フレーム」、末尾を「判定対象の現在フレーム」
    として扱う。段階コール（全枚）・進捗コール（先頭と末尾の2枚）・状態コール（末尾1枚）を
    別々に投げ、結果を1つの辞書にまとめる。段階は "level"、その根拠は "level_reason" に入る。

    samples > 1 なら各コールを複数回サンプリングし、属性は**属性ごとに多数決**、段階は
    **中央値**を取る。割れた属性は not_visible になるので、自信の無い判定が教師に混ざらない。
    """
    if not image_paths:
        raise ValueError("image_paths が空です")
    current = image_paths[-1]
    past = image_paths[0]

    levels: List[int] = []
    level_reasons: List[str] = []
    progress_runs: List[Dict[str, Any]] = []
    state_runs: List[Dict[str, Any]] = []
    notes: List[str] = []

    for index in range(max(1, samples)):
        # 1回目は決定的に、2回目以降は散らして多様性を出す（自己一貫性）
        temperature = 0.1 if index == 0 else 0.7

        level, reason = evaluate_level(image_paths, model, temperature, offsets, interval)
        if level is not None:
            levels.append(level)
            if reason:
                level_reasons.append(reason)

        if past != current:
            raw = _ask(PROGRESS_PROMPT, [past, current], model, temperature)
            if raw:
                progress_runs.append(normalize_attributes(raw))
                evidence = raw.get("evidence")
                if isinstance(evidence, str) and evidence:
                    notes.append(evidence[:40])

        raw = _ask(STATE_PROMPT, [current], model, temperature)
        if raw:
            state_runs.append(normalize_attributes(raw))
            note = raw.get("note")
            if isinstance(note, str) and note:
                notes.append(note[:40])

    if not state_runs:
        raise ValueError("qwen の応答から属性JSONを取り出せませんでした")

    result: Dict[str, Any] = {}
    for name in PROGRESS_ATTRIBUTES:
        result[name] = _vote([run[name] for run in progress_runs]) if progress_runs else NOT_VISIBLE
    for name in STATE_ATTRIBUTES:
        result[name] = _vote([run[name] for run in state_runs])

    # 段階は順序のある値なので、多数決ではなく中央値を取る（割れても中間に落ち着く）
    result["level"] = normalize_level(statistics.median(levels)) if levels else None
    result["level_reason"] = level_reasons[0] if level_reasons else ""
    result["note"] = " / ".join(notes[:2])
    result["samples"] = len(state_runs)
    return result


def build_context_paths(
    files: Sequence[str],
    index: int,
    offsets: Sequence[int] = DEFAULT_CONTEXT_OFFSETS,
) -> List[str]:
    """判定対象 index に対する文脈フレームのパスを、時系列順に組み立てる。

    範囲外のオフセットは捨てる（重複も除く）。最後は必ず判定対象のフレーム。
    """
    positions = sorted({index + offset for offset in offsets if 0 <= index + offset <= index})
    if index not in positions:
        positions.append(index)
    return [files[position] for position in positions]


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_CONTEXT_OFFSETS",
    "FRAME_INTERVAL_SECONDS",
    "LEVEL_PROMPT",
    "PROGRESS_PROMPT",
    "STATE_PROMPT",
    "evaluate_level",
    "evaluate_attributes",
    "build_context_paths",
]
