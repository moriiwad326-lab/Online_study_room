"""LoC（集中度）の属性ラベル定義と、qwen の段階判定を属性で加減点するルール。

## ラベルの作り方（2段構え）

    5フレーム --qwen--> 段階（0 / 25 / 40 / 75 / 100）  … 画像だけから見た大づかみの状態
    現在フレーム --qwen--> 属性（観測可能な事実）       … 段階の中での細かい位置
                         ↓
         LoC = 段階 + 属性ルーブリックの加減点（±25、0〜100 でクリップ）

段階は「40（普通）」を軸にした非等間隔のスケール。0 は退出・居眠り、100 はフロー状態、
75 / 25 はその中間で qwen が画像を見て柔軟に選ぶ（`qwen3_5.LEVEL_PROMPT` 参照）。

## なぜ属性も聞くのか

旧々方式（1〜5の総合判断だけ）はラベルが3と4に固まり、隣接フレームで45%もラベルが
変わった。総合判断だけでは段階の中の差が付かず、段階の境目でラベルが跳ねる。そこで
観測可能な事実（ペン先が紙に触れているか、スマホを手に持っているか等）で段階の中の
位置を加減点し、連続値の教師にする。

段階と属性の生データ（attributes.jsonl）を両方保存しておけば、合成式を変えても
**再推論なしで全ラベルを作り直せる**（LoCは派生値にすぎない）。

## qwen の段階判定を優先する

スマホ操作・突っ伏し・離席のような「非学習が確定する」属性があっても、LoC を決め打ち
にはしない。段階の判定は qwen に任せ、属性は強い減点として効かせるだけにする。属性は
1枚の静止画からの判定なので、5フレームを見た段階判定より誤りやすいため。

## 画角と not_visible

学習動画は真上の手元ショットから斜め上で上半身が一部入るものまで様々で、顔・視線は
写ったり写らなかったりする。**写っていないものを判定させるとノイズになる**ので、すべての
属性は "not_visible" を取れる。加減点は観測できた証拠だけで正規化するため、画角が違っても
加減点のスケールが揃う。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# 属性スキーマのバージョン。attributes.jsonl に記録する。
# 属性を足し引きしたら必ず上げること（古いデータと混ぜるときの判定に使う）。
# 2: qwen の段階判定（level）を追加し、LoC を 0〜100 にした
SCHEMA_VERSION = 2

NOT_VISIBLE = "not_visible"

LOC_MIN = 0.0
LOC_MAX = 100.0

# qwen が画像から選ぶ段階。40（普通）を軸にした非等間隔のスケール
LEVELS = (0, 25, 40, 75, 100)

# 属性ルーブリックで段階から動かせる幅（±）
ADJUST_RANGE = 25.0

# 属性名 -> 許容値。bool 属性は True/False/not_visible を取る。
ATTRIBUTE_SPEC: Dict[str, Tuple[Any, ...]] = {
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

    "75" のような文字列や 70 のような半端な値も、最も近い段階に寄せる。
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
# 素朴に「加点項目を並べて合計を最大値で割る」とすると2つ壊れる。
#
# 1. 紙で勉強している人は `device_in_use` の加点を永久に取れないので満点に届かない。
#    学習の形態（紙 / PC）が違うだけで不利になるのはおかしい。
# 2. 「ページをめくっていない」は非集中の証拠ではないのに、分母に入ると減点として働く。
#
# そこで項目を3種類に分ける。
#
# - CORE    … 不在が意味を持つ中核の証拠。正規化の分母に入る。学習形態で切り替える
# - BONUS   … 在れば加点、無くても減点にならない項目。分母に入らない
# - NEGATIVE… 非集中の証拠。当てはまった重みを足し上げる（上限1）
#
#     加減点 = ADJUST_RANGE × clamp(中核の充足率 + ボーナス − 減点の合計, −1, 1)
#
# この分け方のおかげで、顔が写る動画と写らない動画、紙とPCで、加減点のスケールが揃う。
# 減点だけは充足率で正規化しない。正規化すると減点項目が多いほど1つあたりが薄まり、
# スマホ操作が単独で −5点程度にしかならない（実測で −11.8 / 離席 −10.3）。非集中の証拠は
# 1つあれば十分なので、重み 1.0 の項目は単独で −25点まで効かせる。
# ボーナス・減点の重みは ±1 を満幅とする単位（0.2 なら 25 × 0.2 = 5点）。

# 紙での学習における中核の証拠
PAPER_CORE = (
    # 書き込みが増えた = 実際に学習が進んだ、最も強い証拠
    ("writing_increased", 0.8, lambda a: a["writing_increased"] is True),
    # ペン先が紙に触れている = 筆記中
    ("pen_tip_on_paper", 0.5, lambda a: a["pen_tip_on_paper"] is True),
    # ペンを握っている（筆記していなくても関与の証拠）
    ("pen_held", 0.2, lambda a: a["pen_held"] is True),
    # 手が教材の上にある = 読んでいる・考えている
    ("hand_on_material", 0.2, lambda a: a["hand_on_material"] is True),
)

# 学習形態によらず効く中核の証拠（写っているときだけ）
POSTURE_CORE = (
    ("head_posture", 0.3, lambda a: a["head_posture"] == "upright"),
)

# 在れば加点、無くても減点にならない項目
BONUS_RUBRIC = (
    # PC・タブレットでの学習。真上からの画角では画面の中身まで見えないので、
    # 「学習している」と断定はできない。加点はするが中核の証拠には使わない
    ("device_in_use", 0.2, lambda a: a["device_in_use"] in ("pc", "tablet")),
    # ページをめくった = 学習の進行
    ("page_turned", 0.1, lambda a: a["page_turned"] is True),
    # 両手が机にある（片手だけより作業密度が高い傾向）
    ("hands_in_frame", 0.05, lambda a: a["hands_in_frame"] == 2),
)

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
    """手が机に無く、ペンも教材への接触も無い（離席・手を引っ込めている）。

    判定には**現在時刻の事実だけ**を使う。writing_increased は60秒の窓全体の性質なので、
    「書いてから席を立った」場合に離席を打ち消してしまう。加えてこの属性は誤って true に
    なることがあり、条件に混ぜると離席フレームが高いLoCになる事故が起きる（実測）。
    """
    return (
        attributes["hands_in_frame"] == 0
        and attributes["pen_held"] is not True
        and attributes["hand_on_material"] is not True
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


def _core_rubric(attributes: Dict[str, Any]):
    """この学習形態で中核となる証拠を返す。

    PC・タブレットを使っているときは紙の証拠（筆記・ペン）を分母から外す。
    外さないと「PCで学習中なのでペンを持っていない」が減点として働いてしまう。
    """
    if attributes["device_in_use"] in ("pc", "tablet"):
        return POSTURE_CORE
    return PAPER_CORE + POSTURE_CORE


def compose_adjustment(attributes: Dict[str, Any]) -> Optional[float]:
    """属性から加減点（−ADJUST_RANGE〜+ADJUST_RANGE）を計算する。何も観測できなければ None。

    中核の加点は**観測できた証拠の合計で正規化**する。写らない属性があっても値域が揃うので、
    画角の違う動画どうしで加減点が比較できる。減点は観測できて当てはまったものの合計。
    """
    a = normalize_attributes(attributes)

    # ── 中核の証拠（観測できたものだけで正規化する）──
    positive = possible_positive = 0.0
    for name, weight, condition in _core_rubric(a):
        if not _visible(a, name):
            continue
        possible_positive += weight
        if condition(a):
            positive += weight

    # ── ボーナス（在れば加点、無くても減点にならない）──
    bonus = sum(
        weight for name, weight, condition in BONUS_RUBRIC
        if _visible(a, name) and condition(a)
    )

    # ── 非集中の証拠（当てはまった重みの合計、上限1）──
    any_negative_observable = False
    negative = 0.0
    for name, weight, condition in NEGATIVE_RUBRIC:
        if not _negative_is_observable(a, name):
            continue
        any_negative_observable = True
        if condition(a):
            negative += weight

    if possible_positive == 0.0 and not any_negative_observable and bonus == 0.0:
        return None

    score = bonus - min(1.0, negative)
    if possible_positive > 0:
        score += positive / possible_positive
    return ADJUST_RANGE * max(-1.0, min(1.0, score))


def compose_loc(attributes: Dict[str, Any], level: Any) -> Optional[float]:
    """qwen の段階に属性の加減点を足して LoC（0.0〜100.0の実数）を合成する。

    段階が読めなければ None（ラベルを付けない）。属性が何も観測できなければ
    段階をそのまま使う。
    """
    base = normalize_level(level)
    if base is None:
        return None
    adjustment = compose_adjustment(attributes) or 0.0
    return max(LOC_MIN, min(LOC_MAX, base + adjustment))


def describe_rubric() -> str:
    """ルーブリックを人が読める形で返す（デバッグ・ドキュメント用）。"""
    lines = [f"段階: {' / '.join(str(level) for level in LEVELS)}"]
    lines += ["中核（紙）:"]
    lines += [f"  +{w:.2f}  {n}" for n, w, _ in PAPER_CORE]
    lines += ["中核（姿勢）:"]
    lines += [f"  +{w:.2f}  {n}" for n, w, _ in POSTURE_CORE]
    lines += ["ボーナス:"]
    lines += [f"  +{w:.2f}  {n}" for n, w, _ in BONUS_RUBRIC]
    lines += ["減点:"]
    lines += [f"  -{w:.2f}  {n}" for n, w, _ in NEGATIVE_RUBRIC]
    lines += [f"加減点 = ±{ADJUST_RANGE:.0f} × clamp(中核の充足率 + ボーナス − 減点の合計(≦1), −1, 1)"]
    return "\n".join(lines)


__all__ = [
    "SCHEMA_VERSION",
    "NOT_VISIBLE",
    "LOC_MIN",
    "LOC_MAX",
    "LEVELS",
    "ADJUST_RANGE",
    "ATTRIBUTE_SPEC",
    "ATTRIBUTE_NAMES",
    "normalize_attributes",
    "normalize_level",
    "compose_adjustment",
    "compose_loc",
    "describe_rubric",
]
