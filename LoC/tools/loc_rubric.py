"""LoC（集中度）の属性ラベル定義と、qwen の段階判定を属性で減点するルール。

## ラベルの作り方（2段構え）

    5フレーム --qwen--> 段階（0 / 20 / 40 / 70 / 100）  … 画像だけから見た大づかみの状態
    現在フレーム --qwen--> 属性（観測可能な事実）       … 非集中の証拠による減点
                         ↓
         LoC = 段階 − 属性ルーブリックの減点（0〜20、0〜100 でクリップ）

段階は「40（普通）」を軸にした非等間隔のスケール。0 は退出・居眠り、100 はフロー状態、
70 / 20 はその中間で qwen が画像を見て柔軟に選ぶ（`qwen3_5.LEVEL_PROMPT` 参照）。

属性は**減点にだけ**使い、加点はしない。集中しているかどうかは5フレームを見た段階判定に
任せ、属性は1枚の静止画からでも確実に言える非集中の証拠（スマホ操作・突っ伏し等）で
段階を割り引くだけにする。加点を入れると、紙で筆記している人ほど得をして、PC・タブレット
での学習が相対的に低くなっていた（筆記の証拠が取れないため）。

## なぜ属性も聞くのか

旧々方式（1〜5の総合判断だけ）はラベルが3と4に固まり、隣接フレームで45%もラベルが
変わった。総合判断だけでは段階の中の差が付かず、段階の境目でラベルが跳ねる。そこで
観測可能な事実（ペン先が紙に触れているか、スマホを手に持っているか等）で段階の中の
位置を減点で付け、連続値の教師にする。

段階と属性の生データ（attributes.jsonl）を両方保存しておけば、合成式を変えても
**再推論なしで全ラベルを作り直せる**（LoCは派生値にすぎない）。

## qwen の段階判定を優先する

スマホ操作・突っ伏し・離席のような「非学習が確定する」属性があっても、LoC を決め打ち
にはしない。段階の判定は qwen に任せ、属性は強い減点として効かせるだけにする。属性は
1枚の静止画からの判定なので、5フレームを見た段階判定より誤りやすいため。

## 画角と not_visible

学習動画は真上の手元ショットから斜め上で上半身が一部入るものまで様々で、顔・視線は
写ったり写らなかったりする。**写っていないものを判定させるとノイズになる**ので、すべての
属性は "not_visible" を取れる。減点は観測できて当てはまった項目だけで付けるので、写って
いない属性が値を歪めることはない。

## 不在が続いたら 0 に固定する

`person_visible`（手・腕・頭のどれかが写っているか）が直近 `ABSENCE_WINDOW` フレーム
（5秒間隔で60秒＝LSTM の窓と同じ長さ）の評価点ですべて false なら、段階や減点に
かかわらず LoC を 0 にする（`force_absent_to_zero`）。旧方式では無人の机に段階100が
付く事故があった（「手が止まっていても無駄な動きが一切ない」を qwen が無人に当てはめた）。
MediaPipe Hands の検出フラグは使わない。真上からの画角では書いている最中の手でも
約半分を見逃し、勉強中の窓の24%が「12フレーム手が無い」になってしまうため（実測）。
"""

from __future__ import annotations

import bisect
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 属性スキーマのバージョン。attributes.jsonl に記録する。
# 属性を足し引きしたら必ず上げること（古いデータと混ぜるときの判定に使う）。
# 2: qwen の段階判定（level）を追加し、LoC を 0〜100 にした
# 3: person_visible を追加し、不在が続く区間を 0 に固定するようにした
# 4: 段階を 0/20/40/70/100 にし、属性は減点（最大 −20）だけに使うようにした
SCHEMA_VERSION = 4

NOT_VISIBLE = "not_visible"

LOC_MIN = 0.0
LOC_MAX = 100.0

# qwen が画像から選ぶ段階。40（普通）を軸にした非等間隔のスケール
LEVELS = (0, 20, 40, 70, 100)

# 属性ルーブリックで段階から引ける最大の点数
MAX_PENALTY = 20.0

# この長さ（フレーム数）の窓で人が一度も写っていなければ LoC を 0 にする。
# LSTM の窓（seq_len=12）と同じ = 5秒間隔で60秒
ABSENCE_WINDOW = 12

# 属性名 -> 許容値。bool 属性は True/False/not_visible を取る。
ATTRIBUTE_SPEC: Dict[str, Tuple[Any, ...]] = {
    # 人の体の一部（手・腕・頭・上半身のどれか）が写っているか。無人の机なら False
    "person_visible": (True, False, NOT_VISIBLE),
    # 机の上に写っている手の数。0なら離席か手を引っ込めている
    "hands_in_frame": (0, 1, 2, NOT_VISIBLE),
    # スマホ。机上にあるだけでも誘惑源、手に持っていれば非学習
    "phone": ("absent", "on_desk", "in_hand", NOT_VISIBLE),
    # ペン・シャーペンを握っているか
    "pen_held": (True, False, NOT_VISIBLE),
    # ペン先が紙に接しているか（筆記そのもの）
    "pen_tip_on_paper": (True, False, NOT_VISIBLE),
    # 手がノート・教材の上に置かれているか（読書・思考中の関与の代理）
    "hand_on_material": (True, False, NOT_VISIBLE),
    # 最初のフレームと比べて書き込みが増えたか（学習が進んだ唯一の直接証拠）
    "writing_increased": (True, False, NOT_VISIBLE),
    # ページめくり・教材の持ち替えがあったか
    "page_turned": (True, False, NOT_VISIBLE),
    # ノート・教材・問題集が開いて置かれているか
    "material_open": (True, False, NOT_VISIBLE),
    # PC・タブレットでの学習（筆記以外の学習形態を救済する）
    "device_in_use": ("none", "pc", "tablet", NOT_VISIBLE),
    # 頭の位置。上半身が写っているときだけ判定できる
    "head_posture": ("upright", "propped", "down", NOT_VISIBLE),
}

ATTRIBUTE_NAMES = tuple(ATTRIBUTE_SPEC)


def normalize_attributes(raw: Dict[str, Any]) -> Dict[str, Any]:
    """モデルの出力を検証し、欠けている属性を not_visible で埋める。

    文字列の "true" / "false" / "null"、数値の 0/1/2、大文字小文字の揺れを吸収する。
    値がスキーマに無いものは not_visible として扱う（捏造された値を学習に混ぜない）。
    """
    normalized: Dict[str, Any] = {}
    for name, allowed in ATTRIBUTE_SPEC.items():
        value = raw.get(name, NOT_VISIBLE)

        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "yes"):
                value = True
            elif lowered in ("false", "no"):
                value = False
            elif lowered in ("", "null", "none", "unknown", "n/a", NOT_VISIBLE):
                # "none" は device_in_use では有効値なので、その場合だけ残す
                value = "none" if (lowered == "none" and "none" in allowed) else NOT_VISIBLE
            else:
                value = lowered
        elif value is None:
            value = NOT_VISIBLE
        elif isinstance(value, bool):
            pass  # bool は int のサブクラスなので int より先に判定する
        elif isinstance(value, int):
            value = value if value in allowed else NOT_VISIBLE

        normalized[name] = value if value in allowed else NOT_VISIBLE
    return normalized


def normalize_level(value: Any) -> Optional[int]:
    """qwen が返した段階を LEVELS のいずれかに寄せる。読めなければ None。

    "70" のような文字列や 65 のような半端な値も、最も近い段階に寄せる。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    if not LOC_MIN <= number <= LOC_MAX:
        return None
    return min(LEVELS, key=lambda level: abs(level - number))


def _visible(attributes: Dict[str, Any], name: str) -> bool:
    return attributes.get(name, NOT_VISIBLE) != NOT_VISIBLE


# ## ルーブリックの構造
#
# 非集中の証拠だけを並べ、当てはまった重みを足し上げる（上限1）。
#
#     減点 = MAX_PENALTY × min(当てはまった重みの合計, 1)
#
# 加点項目は持たない。以前は「筆記の証拠」を中核に加点していたが、PC・タブレットで学習
# している人は筆記の証拠を取れず、紙で学習している人より一律に低くなっていた。集中の高さは
# 段階判定に任せ、属性は段階を割り引く方向にだけ効かせる。
# 減点を項目数で正規化しないのは、正規化すると項目が多いほど1つあたりが薄まり、スマホ操作が
# 単独で −5点程度にしかならないため（実測で −11.8 / 離席 −10.3）。非集中の証拠は1つあれば
# 十分なので、重み 1.0 の項目は単独で満幅（−20点）まで効かせる。
# 重みは 1 を満幅とする単位（0.5 なら 20 × 0.5 = 10点）。
#
# PC・タブレットでの学習は減点しない。「筆記していない」「紙の教材が無い」は端末で学習して
# いれば当然なので、止まっている・教材が無いの2項目は端末を使っていれば当てはまらない。

NEGATIVE_RUBRIC = (
    # スマホを手に持って操作している（明らかな非学習行動）
    ("phone", 1.0, lambda a: a["phone"] == "in_hand"),
    # 机に突っ伏している（上半身が写るときだけ）
    ("head_posture", 1.0, lambda a: a["head_posture"] == "down"),
    # 離席・手を引っ込めている
    ("_away", 0.8, lambda a: _is_away(a)),
    # 手はあるが筆記もデバイス操作も教材への関与もない = 止まっている
    ("_stalled", 0.6, lambda a: _is_stalled(a)),
    # 教材もデバイスも無い = 学習セッションが成立していない
    ("material_open", 0.5,
     lambda a: a["material_open"] is False and a["device_in_use"] in ("none", NOT_VISIBLE)),
    # 誘惑源が視界にある
    ("phone", 0.5, lambda a: a["phone"] == "on_desk"),
    # 頬杖（上半身が写るときだけ）
    ("head_posture", 0.4, lambda a: a["head_posture"] == "propped"),
)


def _is_away(attributes: Dict[str, Any]) -> bool:
    """手が机に無く、ペンも教材への接触も端末の操作も無い（離席・手を引っ込めている）。

    判定には**現在時刻の事実だけ**を使う。writing_increased は60秒の窓全体の性質なので、
    「書いてから席を立った」場合に離席を打ち消してしまう。加えてこの属性は誤って true に
    なることがあり、条件に混ぜると離席フレームが高いLoCになる事故が起きる（実測）。
    """
    return (
        attributes["hands_in_frame"] == 0
        and attributes["pen_held"] is not True
        and attributes["hand_on_material"] is not True
        and attributes["device_in_use"] not in ("pc", "tablet")
    )


def _is_stalled(attributes: Dict[str, Any]) -> bool:
    """手は写っているのに、学習行動の証拠がひとつも無い状態。"""
    return (
        attributes["writing_increased"] is not True
        and attributes["pen_tip_on_paper"] is not True
        and attributes["hand_on_material"] is not True
        and attributes["device_in_use"] in ("none", NOT_VISIBLE)
    )


def _negative_is_observable(attributes: Dict[str, Any], name: str) -> bool:
    """減点項目を判定できるか。合成条件は関連属性が1つでも見えていれば判定できる。"""
    if name == "_away":
        return _visible(attributes, "hands_in_frame")
    if name == "_stalled":
        return any(
            _visible(attributes, related)
            for related in ("writing_increased", "pen_tip_on_paper", "hand_on_material", "device_in_use")
        )
    return _visible(attributes, name)


def compose_adjustment(attributes: Dict[str, Any]) -> Optional[float]:
    """属性から減点（−MAX_PENALTY〜0）を計算する。減点項目が何も判定できなければ None。

    当てはまった減点項目の重みを足し上げる（上限1）。加点はしない。
    """
    a = normalize_attributes(attributes)

    any_negative_observable = False
    negative = 0.0
    for name, weight, condition in NEGATIVE_RUBRIC:
        if not _negative_is_observable(a, name):
            continue
        any_negative_observable = True
        if condition(a):
            negative += weight

    if not any_negative_observable:
        return None
    return -MAX_PENALTY * min(1.0, negative)


def compose_loc(attributes: Dict[str, Any], level: Any) -> Optional[float]:
    """qwen の段階から属性の減点を引いて LoC（0.0〜100.0の実数）を合成する。

    段階が読めなければ None（ラベルを付けない）。属性が何も観測できなければ
    段階をそのまま使う。
    """
    base = normalize_level(level)
    if base is None:
        return None
    adjustment = compose_adjustment(attributes) or 0.0
    return max(LOC_MIN, min(LOC_MAX, base + adjustment))


def force_absent_to_zero(
    rows: Sequence[Tuple[int, float]],
    presence: Dict[int, bool],
    window: int = ABSENCE_WINDOW,
) -> List[Tuple[int, float]]:
    """直近 window フレームの評価点で人が一度も写っていなければ LoC を 0 にする。

    rows:     (フレームの位置, LoC) のリスト。位置は動画内のフレームの通し番号（0始まり）
    presence: {フレームの位置: 写っているか}。在席を判定できた評価点だけを入れる

    窓が埋まらない先頭（位置 < window-1）と、窓の中に判定できた評価点が1つも無い
    ところは元の値を残す。stride で間引いた評価でも、窓の中の評価点だけで判断する。
    """
    observed = sorted(presence)
    forced: List[Tuple[int, float]] = []
    for position, loc in rows:
        if position >= window - 1:
            start = bisect.bisect_left(observed, position - window + 1)
            end = bisect.bisect_right(observed, position)
            in_window = [presence[p] for p in observed[start:end]]
            if in_window and not any(in_window):
                loc = LOC_MIN
        forced.append((position, loc))
    return forced


def describe_rubric() -> str:
    """ルーブリックを人が読める形で返す（デバッグ・ドキュメント用）。"""
    lines = [f"段階: {' / '.join(str(level) for level in LEVELS)}"]
    lines += ["減点:"]
    lines += [f"  -{w * MAX_PENALTY:.0f}点  {n}" for n, w, _ in NEGATIVE_RUBRIC]
    lines += [f"LoC = 段階 − {MAX_PENALTY:.0f} × min(減点の合計, 1)"]
    lines += [f"不在: 直近{ABSENCE_WINDOW}フレームで person_visible がすべて false なら 0"]
    return "\n".join(lines)


__all__ = [
    "SCHEMA_VERSION",
    "NOT_VISIBLE",
    "LOC_MIN",
    "LOC_MAX",
    "LEVELS",
    "MAX_PENALTY",
    "ABSENCE_WINDOW",
    "ATTRIBUTE_SPEC",
    "ATTRIBUTE_NAMES",
    "normalize_attributes",
    "normalize_level",
    "compose_adjustment",
    "compose_loc",
    "force_absent_to_zero",
    "describe_rubric",
]
