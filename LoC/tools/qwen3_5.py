"""Ollama 経由で qwen3.5 に学習フレームの**属性**を判定させる。

旧版はこのモジュールで「集中度を1〜5で答えて」と総合判断をさせていたが、

- 判定基準の多く（突っ伏し・頬杖・視線）が画角に写っておらず、幻覚を生んでいた
- 5段階という抽象スケールへの総合判断はVLMの苦手分野で、ラベル分散の約6割がノイズだった
- 「最初のフレームが非学習なら Level 2 で固定」というルールが、窓の先頭1フレームへの
  強い依存を生み、窓をずらすたびにラベルが階段状に跳んでいた

ので、**観測可能な属性だけを答えさせ、LoC は `loc_rubric.compose_loc` で決定論的に合成する**
方式へ変更した。このモジュールが返すのは属性の辞書であって LoC ではない。

## なぜ2回に分けて聞くのか

属性10個を1回のプロンプトでまとめて聞くと、**`writing_increased` が常に false になる**という
失敗が実測で出た（8フレーム試して8回とも false。目視では明らかに書き込みが増えている）。
一方、2枚の画像だけを見せて「書き込みは増えたか」だけを聞くと正しく true を返し、根拠も
具体的に述べる。つまり能力の問題ではなく、他の9項目に埋もれていた。

そこで質問を性質ごとに2回へ分ける。

1. **進捗コール**: 60秒前と現在の2枚を見せ、時間方向の変化だけを聞く
   （`writing_increased` / `page_turned`）
2. **状態コール**: 現在の1枚だけを見せ、その瞬間の静的な事実を聞く（残り8項目）

画像は合計3枚で済むので、10項目を5枚で聞いていた頃より速く、かつ正確になる。

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
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from ollama import chat
from PIL import Image

from loc_rubric import ATTRIBUTE_NAMES, NOT_VISIBLE, normalize_attributes

DEFAULT_MODEL = "qwen3.5:9b"

# 送信前に画像の長辺をここまで縮める。qwen3.5 の視覚エンコーダは動的解像度なので、
# プロンプトのトークン数（＝レイテンシ）はおおむね画素数に比例する。元フレームは4Kも
# あるため、縮めないと1回の推論が数万トークンに膨れる。768px あれば、ノートの
# 書き込みが増えたかどうかの判定まで通ることを実測で確認している。
IMAGE_MAX_SIDE = 768
IMAGE_JPEG_QUALITY = 85

# 文脈フレームのオフセット（判定対象=0、負の値が過去）。フレーム間隔が5秒なら
# -12 は 60秒前。進捗コールはこの2枚だけを使う。
DEFAULT_CONTEXT_OFFSETS = (-12, 0)

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


def evaluate_attributes(
    image_paths: Sequence[str],
    model: str = DEFAULT_MODEL,
    samples: int = 1,
) -> Dict[str, Any]:
    """最後の画像について属性を判定して返す。

    image_paths の先頭を「過去の基準フレーム」、末尾を「判定対象の現在フレーム」として扱う。
    進捗コール（2枚）と状態コール（1枚）を別々に投げ、結果を1つの辞書にまとめる。

    samples > 1 なら各コールを複数回サンプリングし、**属性ごとに多数決**を取る。
    割れた属性は not_visible になるので、自信の無い判定が教師に混ざらない。
    """
    if not image_paths:
        raise ValueError("image_paths が空です")
    current = image_paths[-1]
    past = image_paths[0]

    progress_runs: List[Dict[str, Any]] = []
    state_runs: List[Dict[str, Any]] = []
    notes: List[str] = []

    for index in range(max(1, samples)):
        # 1回目は決定的に、2回目以降は散らして多様性を出す（自己一貫性）
        temperature = 0.1 if index == 0 else 0.7

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
    "PROGRESS_PROMPT",
    "STATE_PROMPT",
    "evaluate_attributes",
    "build_context_paths",
]
