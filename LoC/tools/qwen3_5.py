from typing import List, Optional
import re
import json
from ollama import chat

system_prompt = """あなたは勉強中の手元動画から集中度を判定する専門AIです。
出力は必ずJSON形式のみで行ってください。マークダウンの装飾(```jsonなど)も不要です。

出力するJSONは以下の形式に厳密に従ってください:
{"reason": "<判定の理由(50文字以内)>", "level": <整数: 1~5>}
"""

user_prompt = """入力された複数の連続画像(最後が判定対象の現在時刻)から、現在の集中度(1~5)を判定してください。
複雑な判断を避けるため、必ず以下の[判定ステップ]の順番に従って評価してください。

【過去のフレームと最後のフレームの評価ルール】
入力された複数枚の画像は「直近の文脈」を把握するためのものです。
最初のフレームで「スマホ操作」などの明らかな非学習行動（Level 2以下）があった場合、最後のフレームで一時的にページを押さえていたとしても、集中度は回復していないとみなし、全体の評価として「Level 2」を適用してください。

これで「前はスマホを触っていたけど、最後は触っていないから…」という堂々巡りの思考を強制終了させられます。

[判定ステップ]
ステップ1: 完全な集中切れ(Level 1)か?
・フレームアウトしている、机に突っ伏している(居眠り)、スマホを両手で操作している等の非学習行動があれば"Level 1"。そうでなければステップ2へ。

ステップ2: 学習以外の動作・手遊び(Level 2)か?
・スマホを裏返すなど学習と無関係なものに触れている、ペン回しや髪を触るなどの手遊びがある、頬杖など姿勢が崩壊している場合は"Level 2"。そうでなければステップ3へ。

ステップ3: 学習中の中でのレベル分け(Level 3~5)
ここからは"学習行動をしている"前提での分類です。
・Level 3 (普通): 手が止まる時間がある、頻繁に姿勢を変える、時折視線が対象から外れる。
・Level 4 (高い集中): 概ね安定。ノートの角度変更や肩の力を抜くなど、学習を補助する小さな姿勢変更のみ。
・Level 5 (極めて高い集中): ペン先が一定ペースで動き続けている、または無駄な動きが一切なく姿勢が完全に固定されている。

最後の画像の集中度について、上記のステップに従って判定理由を"reason"に短く記述し、最終的な"level"を出力してください。
"""


def _parse_level_from_json(text: str) -> Optional[int]:
    """Parse a JSON object from text and extract the integer `level` (1-5).
    Returns None if parsing fails or value is out of range.
    """
    if not text:
        return None
    # Try parsing the whole text as JSON first
    try:
        obj = json.loads(text)
    except Exception:
        # Fallback: extract first JSON object substring
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None

    def _extract_from_obj(o):
        if isinstance(o, dict):
            for key in ('level', 'Level', '集中度', '集中度レベル'):
                if key in o:
                    try:
                        val = int(o[key])
                        if 1 <= val <= 5:
                            return val
                    except Exception:
                        return None
        return None

    # If top-level is dict
    val = _extract_from_obj(obj)
    if val is not None:
        return val

    # If it's a list, try objects inside
    if isinstance(obj, list):
        for item in obj:
            val = _extract_from_obj(item)
            if val is not None:
                return val

    return None


def evaluate_loc(user_prompt_text: str, image_paths: List[str], model: str = 'qwen3.5:9b') -> int:
    """
    Call Ollama chat model with a fixed system prompt and a user prompt.
    - `user_prompt_text`: the user instruction text to send to the model.
    - `image_paths`: list of image file paths to attach to the user message.
    Returns the concentration level as an integer in 1..5.
    """
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt_text, 'images': image_paths},
    ]

    response = chat(
        model=model,
        messages=messages,
        format="json",
        options={
            "num_ctx": 2**16,
            "temperature": 0.1,
            "top_p": 0.9,
            "num_predict": -1,
        },keep_alive=True
    )

    # coerce response to text for parsing
    text = ''
    try:
        if isinstance(response, str):
            text = response
        elif isinstance(response, dict):
            for k in ('content', 'text', 'response', 'result'):
                if k in response and isinstance(response[k], str):
                    text = response[k]
                    break
            if not text and 'choices' in response and isinstance(response['choices'], list):
                first = response['choices'][0]
                if isinstance(first, dict):
                    for k in ('message', 'text', 'content'):
                        if k in first and isinstance(first[k], str):
                            text = first[k]
                            break
            if not text:
                text = str(response)
        else:
            if hasattr(response, 'content'):
                text = response.content
            elif hasattr(response, 'text'):
                text = response.text
            else:
                text = str(response)
    except Exception:
        text = str(response)

    level = _parse_level_from_json(text)
    if level is None:
        raise ValueError(f"Could not parse concentration level from JSON response: {text!r}")
    return level


if __name__ == '__main__':
    response = None
    try:
        level = evaluate_loc(user_prompt, [str(f"output/202606172311/{i}.jpg") for i in range(40)])
        print(level)
    except Exception as e:
        print("Error:", e)
        raise