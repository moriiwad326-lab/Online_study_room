"""LoC（集中度）の属性ラベル定義と、属性からLoCを合成するルール。

## なぜ属性を経由するのか

旧方式は「集中度を1〜5で答えて」とVLMに総合判断を丸投げしていた。その結果、

- ラベルの76%が3と4に集中し、3/4/5の区別が機能していなかった
- 隣接フレーム（入力の95%が共通）で45%もラベルが変わり、分散の約6割が独立ノイズだった
- 判定基準の多く（突っ伏し・頬杖・視線）が**そもそも画角に写っていない**ものだった

VLMが得意なのは「スマホが写っているか」「ペン先が紙に触れているか」のような**観測可能な事実**で、
苦手なのは5段階という抽象スケールへの総合判断。そこで、

    フレーム --qwen--> 属性（観測可能な事実）--決定論的な式--> LoC（1〜5）

に分解する。属性の生データ（attributes.jsonl）を保存しておけば、

- 合成式を変えても**再推論なしで全ラベルを作り直せる**（LoCは派生値にすぎない）
- 属性ごとに人手のゴールド集合と突き合わせられるので、どの判定が壊れているか特定できる
- 属性をモデルの補助タスクにできる（少データでの正則化として効く）

## 画角と not_visible

学習動画は真上の手元ショットから斜め上で上半身が一部入るものまで様々で、顔・視線は
写ったり写らなかったりする。**写っていないものを判定させるとノイズになる**ので、すべての
属性は "not_visible" を取れる。合成式は観測できた証拠だけで正規化するため、画角が違っても
LoCのスケールが揃う。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# 属性スキーマのバージョン。attributes.jsonl に記録する。
# 属性を足し引きしたら必ず上げること（古いデータと混ぜるときの判定に使う）。
SCHEMA_VERSION = 1

NOT_VISIBLE = "not_visible"

LOC_MIN = 1.0
LOC_MAX = 5.0

# 属性名 -> 許容値。bool 属性は True/False/not_visible を取る。
ATTRIBUTE_SPEC: Dict[str, Tuple[Any, ...]] = {
    # 机の上に写っている手の数。0なら離席か手を引っ込めている
    "hands_in_frame": (0, 1, 2, NOT_VISIBLE),
    # スマホ。机上にあるだけでも誘惑源、手に持っていれば非学習が確定
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
# - NEGATIVE… 非集中の証拠。こちらも観測できたものだけで正規化する
#
# この分け方のおかげで、顔が写る動画と写らない動画、紙とPCで、LoCのスケールが揃う。

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
    ("device_in_use", 0.4, lambda a: a["device_in_use"] in ("pc", "tablet")),
    # ページをめくった = 学習の進行
    ("page_turned", 0.2, lambda a: a["page_turned"] is True),
    # 両手が机にある（片手だけより作業密度が高い傾向）
    ("hands_in_frame", 0.1, lambda a: a["hands_in_frame"] == 2),
)

NEGATIVE_RUBRIC = (
    # 手はあるが筆記もデバイス操作も教材への関与もない = 止まっている
    ("_stalled", 0.6, lambda a: _is_stalled(a)),
    # 誘惑源が視界にある
    ("phone", 0.5, lambda a: a["phone"] == "on_desk"),
    # 頬杖（上半身が写るときだけ）
    ("head_posture", 0.4, lambda a: a["head_posture"] == "propped"),
)


def _is_stalled(attributes: Dict[str, Any]) -> bool:
    """手は写っているのに、学習行動の証拠がひとつも無い状態。"""
    return (
        attributes["writing_increased"] is not True
        and attributes["pen_tip_on_paper"] is not True
        and attributes["hand_on_material"] is not True
        and attributes["device_in_use"] in ("none", NOT_VISIBLE)
    )


def _stalled_is_observable(attributes: Dict[str, Any]) -> bool:
    """「止まっている」を判定できるのは、関連属性が1つでも見えているとき。"""
    return any(
        _visible(attributes, name)
        for name in ("writing_increased", "pen_tip_on_paper", "hand_on_material", "device_in_use")
    )


def _core_rubric(attributes: Dict[str, Any]):
    """この学習形態で中核となる証拠を返す。

    PC・タブレットを使っているときは紙の証拠（筆記・ペン）を分母から外す。
    外さないと「PCで学習中なのでペンを持っていない」が減点として働いてしまう。
    """
    if attributes["device_in_use"] in ("pc", "tablet"):
        return POSTURE_CORE
    return PAPER_CORE + POSTURE_CORE


def compose_loc(attributes: Dict[str, Any]) -> Optional[float]:
    """属性からLoC（1.0〜5.0の実数）を合成する。判定不能なら None。

    2段構え:
      1. 非学習が確定する条件（ゲート）に当たれば、そこで値を決め打つ。
      2. そうでなければ 3.0 を基準に、観測できた証拠だけで加減点する。

    加点と減点はそれぞれ**観測できた証拠の合計で正規化**する。写らない属性があっても
    値域が [1, 5] に収まり、画角の違う動画どうしでラベルが比較できる。
    """
    a = normalize_attributes(attributes)

    # ── ゲート: 非学習が確定する条件 ──
    if a["phone"] == "in_hand":
        return 1.0  # スマホ操作は無条件で最低
    if a["head_posture"] == "down":
        return 1.0  # 突っ伏し（写っているときだけ判定できる）
    if (
        a["hands_in_frame"] == 0
        and a["pen_held"] is not True
        and a["hand_on_material"] is not True
    ):
        # 離席・手を引っ込めている。判定には**現在時刻の事実だけ**を使う。
        # writing_increased は60秒の窓全体の性質なので、「書いてから席を立った」場合に
        # 離席の検出を打ち消してしまう。加えてこの属性は誤って true になることがあり、
        # ゲートの条件に混ぜると離席フレームが高いLoCになる事故が起きる（実測）。
        return 1.5
    if a["material_open"] is False and a["device_in_use"] in ("none", NOT_VISIBLE):
        return 2.0  # 学習セッションが成立していない

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

    # ── 非集中の証拠 ──
    negative = possible_negative = 0.0
    for name, weight, condition in NEGATIVE_RUBRIC:
        observable = _stalled_is_observable(a) if name == "_stalled" else _visible(a, name)
        if not observable:
            continue
        possible_negative += weight
        if condition(a):
            negative += weight

    if possible_positive == 0.0 and possible_negative == 0.0 and bonus == 0.0:
        return None  # 何ひとつ観測できていない。ラベルを付けない

    loc = 3.0 + bonus
    if possible_positive > 0:
        loc += 2.0 * (positive / possible_positive)
    if possible_negative > 0:
        loc -= 2.0 * (negative / possible_negative)
    return max(LOC_MIN, min(LOC_MAX, loc))


def describe_rubric() -> str:
    """ルーブリックを人が読める形で返す（デバッグ・ドキュメント用）。"""
    lines = ["中核（紙）:"]
    lines += [f"  +{w:.1f}  {n}" for n, w, _ in PAPER_CORE]
    lines += ["中核（姿勢）:"]
    lines += [f"  +{w:.1f}  {n}" for n, w, _ in POSTURE_CORE]
    lines += ["ボーナス:"]
    lines += [f"  +{w:.1f}  {n}" for n, w, _ in BONUS_RUBRIC]
    lines += ["減点:"]
    lines += [f"  -{w:.1f}  {n}" for n, w, _ in NEGATIVE_RUBRIC]
    return "\n".join(lines)


__all__ = [
    "SCHEMA_VERSION",
    "NOT_VISIBLE",
    "ATTRIBUTE_SPEC",
    "ATTRIBUTE_NAMES",
    "normalize_attributes",
    "compose_loc",
    "describe_rubric",
]
