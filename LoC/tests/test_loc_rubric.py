"""属性スキーマと LoC 合成ルール（tools/loc_rubric.py）を検証する。

ラベルの質がモデルの精度を直接決めるので、合成ルールの性質はテストで固定しておく:
- LoC = qwen の段階 + 属性の加減点（±25）で、0〜100 に収まること
- 写っていない属性（not_visible）が値を歪めないこと
- 非学習の属性は強い減点になるが、qwen の段階判定を上書きしないこと
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from loc_rubric import (
    ADJUST_RANGE,
    ATTRIBUTE_NAMES,
    LEVELS,
    NOT_VISIBLE,
    compose_adjustment,
    compose_loc,
    normalize_attributes,
    normalize_level,
)


def attrs(**overrides):
    """既定で「ごく普通に筆記している」属性を作り、必要な分だけ上書きする。"""
    base = {
        'hands_in_frame': 1,
        'phone': 'absent',
        'pen_held': True,
        'pen_tip_on_paper': True,
        'hand_on_material': True,
        'writing_increased': True,
        'page_turned': False,
        'material_open': True,
        'device_in_use': 'none',
        'head_posture': NOT_VISIBLE,
    }
    base.update(overrides)
    return base


ALL_INVISIBLE = {name: NOT_VISIBLE for name in ATTRIBUTE_NAMES}


# --- 正規化 ---


def test_missing_attributes_become_not_visible():
    normalized = normalize_attributes({'phone': 'absent'})

    assert set(normalized) == set(ATTRIBUTE_NAMES)
    assert normalized['pen_held'] == NOT_VISIBLE


def test_string_booleans_and_nulls_are_absorbed():
    normalized = normalize_attributes({'pen_held': 'true', 'pen_tip_on_paper': 'False',
                                       'material_open': None, 'hands_in_frame': 2})

    assert normalized['pen_held'] is True
    assert normalized['pen_tip_on_paper'] is False
    assert normalized['material_open'] == NOT_VISIBLE
    assert normalized['hands_in_frame'] == 2


def test_invalid_values_are_not_invented():
    # スキーマに無い値を勝手に有効扱いしない（捏造を学習に混ぜない）
    normalized = normalize_attributes({'phone': 'maybe', 'hands_in_frame': 7})

    assert normalized['phone'] == NOT_VISIBLE
    assert normalized['hands_in_frame'] == NOT_VISIBLE


def test_none_is_a_valid_value_only_for_device():
    normalized = normalize_attributes({'device_in_use': 'none', 'phone': 'none'})

    assert normalized['device_in_use'] == 'none'
    assert normalized['phone'] == NOT_VISIBLE


# --- 段階の正規化 ---


def test_levels_are_anchored_on_forty():
    assert LEVELS == (0, 25, 40, 75, 100)


@pytest.mark.parametrize('raw, expected', [
    (75, 75), ('40', 40), (' 100 ', 100), (0, 0),
    (50, 40),   # 半端な値は最も近い段階へ寄せる（50 は 40 と 75 のうち 40 に近い）
    (60, 75),
    (30, 25),
])
def test_level_is_snapped_to_the_nearest_step(raw, expected):
    assert normalize_level(raw) == expected


@pytest.mark.parametrize('raw', [None, True, 'high', -5, 120])
def test_unreadable_level_is_none(raw):
    assert normalize_level(raw) is None


# --- 合成 ---


def test_unreadable_level_gives_no_label():
    # 段階が読めなければラベルを付けない（属性だけで当てずっぽうの値を作らない）
    assert compose_loc(attrs(), None) is None


def test_nothing_observable_keeps_the_level():
    assert compose_adjustment(ALL_INVISIBLE) is None
    assert compose_loc(ALL_INVISIBLE, 40) == 40


@pytest.mark.parametrize('level', LEVELS)
def test_adjustment_is_within_25_points_of_the_level(level):
    for case in (attrs(), attrs(phone='in_hand', hands_in_frame=0, pen_held=False,
                                hand_on_material=False, material_open=False)):
        loc = compose_loc(case, level)
        assert abs(loc - level) <= ADJUST_RANGE
        assert 0.0 <= loc <= 100.0


def test_never_exceeds_100_or_goes_below_0():
    best = attrs(hands_in_frame=2, page_turned=True, head_posture='upright')
    worst = attrs(phone='in_hand', head_posture='down', hands_in_frame=0, pen_held=False,
                  hand_on_material=False, pen_tip_on_paper=False, writing_increased=False)

    assert compose_loc(best, 100) == 100.0
    assert compose_loc(worst, 0) == 0.0


def test_full_evidence_adds_the_full_range():
    best = attrs(hands_in_frame=2, page_turned=True, head_posture='upright')

    assert compose_loc(best, 40) == 40 + ADJUST_RANGE


# --- 非学習の属性は減点するが、段階判定を優先する ---


def test_phone_in_hand_lowers_but_does_not_override_the_level():
    with_phone = compose_loc(attrs(phone='in_hand'), 75)

    assert with_phone < compose_loc(attrs(), 75)
    assert with_phone >= 75 - ADJUST_RANGE  # 0 に決め打ちしない


def test_a_single_strong_negative_uses_the_full_range():
    # 減点項目が多くても薄まらない。スマホ操作だけで満幅の減点になる
    phone = attrs(phone='in_hand', pen_held=False, pen_tip_on_paper=False,
                  writing_increased=False, hand_on_material=False)

    assert compose_adjustment(phone) == -ADJUST_RANGE


def test_head_down_lowers_the_score():
    assert compose_loc(attrs(head_posture='down'), 40) < compose_loc(attrs(head_posture='upright'), 40)


def test_away_lowers_the_score():
    away = attrs(hands_in_frame=0, pen_held=False, hand_on_material=False,
                 pen_tip_on_paper=False, writing_increased=False)

    assert compose_adjustment(away) < 0


def test_away_is_not_rescued_by_claimed_progress():
    # writing_increased は60秒窓の性質で誤検出もある。離席の減点を打ち消してはいけない
    away = attrs(hands_in_frame=0, pen_held=False, hand_on_material=False,
                 pen_tip_on_paper=False, writing_increased=True)
    present = attrs(writing_increased=True)

    assert compose_adjustment(away) < compose_adjustment(present)


def test_hands_out_of_frame_while_holding_a_pen_is_not_treated_as_away():
    # 画角の端で手が切れていてもペンを握っていれば離席ではない
    assert compose_adjustment(attrs(hands_in_frame=0, pen_held=True)) > 0


def test_no_material_and_no_device_lowers_the_score():
    assert compose_adjustment(attrs(material_open=False)) < compose_adjustment(attrs())


def test_no_material_but_pc_in_use_is_not_penalised():
    # PC学習者が「教材が無い」で減点されないこと
    pc = attrs(material_open=False, device_in_use='pc', pen_held=False,
               pen_tip_on_paper=False, writing_increased=False, hand_on_material=False)

    assert compose_adjustment(pc) >= 0


# --- 証拠による加減点 ---


def test_stalled_hands_are_penalised():
    # 手はあるが筆記もデバイスも教材への関与も無い
    stalled = attrs(pen_tip_on_paper=False, writing_increased=False,
                    hand_on_material=False, pen_held=False)

    assert compose_adjustment(stalled) < 0


def test_phone_on_desk_lowers_the_score():
    assert compose_loc(attrs(phone='on_desk'), 40) < compose_loc(attrs(phone='absent'), 40)


def test_thinking_with_hand_on_material_is_a_small_adjustment():
    # 書いてはいないが教材に手を置いて読んでいる＝段階からあまり動かさない
    thinking = attrs(pen_tip_on_paper=False, writing_increased=False)

    assert -5 <= compose_adjustment(thinking) <= 10


# --- not_visible の扱い（画角の違いへの頑健性） ---


def test_invisible_attributes_do_not_drag_the_score():
    # 顔が写らない動画と写る動画で、同じ学習状態なら同じ評価になるべき
    overhead = attrs(head_posture=NOT_VISIBLE, hands_in_frame=2, page_turned=True)
    frontal = attrs(head_posture='upright', hands_in_frame=2, page_turned=True)

    assert compose_loc(overhead, 75) == compose_loc(frontal, 75)


def test_score_stays_in_range_with_mostly_invisible_attributes():
    sparse = dict(ALL_INVISIBLE, pen_tip_on_paper=True)

    loc = compose_loc(sparse, 40)

    assert loc is not None and 40 <= loc <= 40 + ADJUST_RANGE
