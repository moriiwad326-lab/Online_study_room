"""属性スキーマと LoC 合成ルール（tools/loc_rubric.py）を検証する。

ラベルの質がモデルの精度を直接決めるので、合成ルールの性質はテストで固定しておく:
- LoC = qwen の段階 + 加点（最大 +10） − 減点（最大 25）で、0〜100 に収まること
- 加点と減点は打ち消し合って切り詰められないこと（筆記中でもスマホ操作は減点される）
- 写っていない属性（not_visible）が値を歪めないこと
- 非学習の属性は強い減点になるが、qwen の段階判定を上書きしないこと
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import loc_rubric
from loc_rubric import (
    ATTRIBUTE_NAMES,
    LEVELS,
    MAX_BONUS,
    MAX_PENALTY,
    NOT_VISIBLE,
    compose_adjustment,
    compose_loc,
    force_absent_to_zero,
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

# 学習の証拠が無く、スマホを操作している（減点が満幅になる）
PHONE_ONLY = attrs(phone='in_hand', pen_held=False, pen_tip_on_paper=False,
                   writing_increased=False, hand_on_material=False)


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


def test_levels_are_evenly_spaced_around_fifty():
    assert LEVELS == (10, 30, 50, 70, 90)


@pytest.mark.parametrize('raw, expected', [
    (70, 70), ('50', 50), (' 90 ', 90), (10, 10),
    (55, 50),   # 半端な値は最も近い段階へ寄せる
    (65, 70),
    (0, 10),    # 旧スケールの端の値も端の段階へ寄せる
    (100, 90),
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
    assert compose_loc(ALL_INVISIBLE, 50) == 50


@pytest.mark.parametrize('level', LEVELS)
def test_adjustment_is_between_minus_25_and_plus_10(level):
    for case in (attrs(), PHONE_ONLY, attrs(phone='in_hand', hands_in_frame=0, pen_held=False,
                                            hand_on_material=False, material_open=False)):
        loc = compose_loc(case, level)
        assert level - MAX_PENALTY <= loc <= level + MAX_BONUS
        assert 0.0 <= loc <= 100.0


def test_never_exceeds_100_or_goes_below_0():
    best = attrs(hands_in_frame=2, page_turned=True, head_posture='upright')
    worst = attrs(phone='in_hand', head_posture='down', hands_in_frame=0, pen_held=False,
                  hand_on_material=False, pen_tip_on_paper=False, writing_increased=False)

    assert compose_loc(best, 90) == 100.0
    assert compose_loc(worst, 10) == 0.0


def test_full_evidence_adds_at_most_10():
    best = attrs(hands_in_frame=2, page_turned=True, head_posture='upright')

    assert compose_adjustment(best) == MAX_BONUS
    assert compose_loc(best, 50) == 60


def test_bonus_never_passes_the_next_level():
    # 加点の最大は段階の間隔の半分。加点で隣の段階を追い越さない
    gaps = [b - a for a, b in zip(LEVELS, LEVELS[1:])]

    assert all(MAX_BONUS * 2 <= gap for gap in gaps)


def test_a_strong_negative_drops_below_the_next_level():
    # 減点の最大は段階の間隔より大きい。スマホ操作なら1つ下の段階より低くなる
    assert compose_loc(PHONE_ONLY, 70) < 50


def test_writing_does_not_cancel_the_phone_penalty():
    # 加点と減点は別々に上限を掛けて足す。筆記の証拠でスマホ操作の減点が消えない
    writing_with_phone = attrs(phone='in_hand')

    assert compose_adjustment(writing_with_phone) == MAX_BONUS - MAX_PENALTY


def test_zero_bonus_gives_penalty_only(monkeypatch):
    # MAX_BONUS = 0 にすれば減点だけの方式になる
    monkeypatch.setattr(loc_rubric, 'MAX_BONUS', 0.0)
    best = attrs(hands_in_frame=2, page_turned=True, head_posture='upright')

    assert compose_adjustment(best) == 0
    assert compose_adjustment(PHONE_ONLY) == -MAX_PENALTY


# --- 非学習の属性は減点するが、段階判定を優先する ---


def test_phone_in_hand_lowers_but_does_not_override_the_level():
    with_phone = compose_loc(attrs(phone='in_hand'), 70)

    assert with_phone < compose_loc(attrs(), 70)
    assert with_phone >= 70 - MAX_PENALTY  # 0 に決め打ちしない


def test_a_single_strong_negative_uses_the_full_range():
    # 減点項目が多くても薄まらない。スマホ操作だけで満幅の減点になる
    assert compose_adjustment(PHONE_ONLY) == -MAX_PENALTY


def test_head_down_lowers_the_score():
    assert compose_loc(attrs(head_posture='down'), 50) < compose_loc(attrs(head_posture='upright'), 50)


def test_away_lowers_the_score():
    away = attrs(hands_in_frame=0, pen_held=False, hand_on_material=False,
                 pen_tip_on_paper=False, writing_increased=False)

    assert compose_adjustment(away) < 0


def test_away_is_not_rescued_by_claimed_progress():
    # writing_increased は60秒窓の性質で誤検出もある。離席の減点を打ち消してはいけない
    away = attrs(hands_in_frame=0, pen_held=False, hand_on_material=False,
                 pen_tip_on_paper=False, writing_increased=True)
    present = attrs(writing_increased=True)

    assert compose_adjustment(away) < 0 < compose_adjustment(present)


def test_hands_out_of_frame_while_holding_a_pen_is_not_treated_as_away():
    # 画角の端で手が切れていてもペンを握っていれば離席ではない
    assert compose_adjustment(attrs(hands_in_frame=0, pen_held=True)) > 0


def test_no_material_and_no_device_lowers_the_score():
    assert compose_adjustment(attrs(material_open=False)) < compose_adjustment(attrs())


def test_no_material_but_pc_in_use_is_not_penalised():
    # PC学習者が「教材が無い」で減点されないこと
    pc = attrs(material_open=False, device_in_use='pc', pen_held=False,
               pen_tip_on_paper=False, writing_increased=False, hand_on_material=False)

    assert compose_adjustment(pc) > 0


def test_watching_a_device_with_hands_off_is_not_penalised():
    # 講義動画を見ていて手が画面外でも、端末学習なら離席・停滞の減点を受けない
    watching = attrs(hands_in_frame=0, material_open=False, device_in_use='tablet', pen_held=False,
                     pen_tip_on_paper=False, writing_increased=False, hand_on_material=False)

    assert compose_adjustment(watching) > 0
    assert compose_loc(watching, 70) > 70


# --- 証拠による加減点 ---


def test_stalled_hands_are_penalised():
    # 手はあるが筆記もデバイスも教材への関与も無い
    stalled = attrs(pen_tip_on_paper=False, writing_increased=False,
                    hand_on_material=False, pen_held=False)

    assert compose_adjustment(stalled) < 0


def test_phone_on_desk_lowers_the_score():
    assert compose_loc(attrs(phone='on_desk'), 50) < compose_loc(attrs(phone='absent'), 50)


def test_thinking_with_hand_on_material_is_a_small_bonus():
    # 書いてはいないが教材に手を置いて読んでいる＝減点せず、筆記より小さく加点する
    thinking = attrs(pen_tip_on_paper=False, writing_increased=False)

    assert 0 < compose_adjustment(thinking) < compose_adjustment(attrs())


# --- not_visible の扱い（画角の違いへの頑健性） ---


def test_invisible_attributes_do_not_drag_the_score():
    # 顔が写らない動画と写る動画で、同じ学習状態なら同じ評価になるべき
    overhead = attrs(head_posture=NOT_VISIBLE, hands_in_frame=2, page_turned=True)
    frontal = attrs(head_posture='upright', hands_in_frame=2, page_turned=True)

    assert compose_loc(overhead, 70) == compose_loc(frontal, 70)


def test_score_stays_in_range_with_mostly_invisible_attributes():
    sparse = dict(ALL_INVISIBLE, pen_tip_on_paper=True)

    loc = compose_loc(sparse, 50)

    assert loc is not None and 50 <= loc <= 50 + MAX_BONUS


# --- 不在が続いたら 0 に固定する ---


def test_person_visible_is_part_of_the_schema():
    normalized = normalize_attributes({'person_visible': 'false'})

    assert normalized['person_visible'] is False


def test_absent_for_a_full_window_is_forced_to_zero():
    rows = [(position, 80.0) for position in range(20)]
    presence = {position: False for position in range(0, 20, 2)}  # stride 2 の評価点

    forced = dict(force_absent_to_zero(rows, presence, window=12))

    assert forced[11] == 0.0
    assert forced[19] == 0.0


def test_window_that_is_not_full_yet_keeps_the_label():
    rows = [(position, 80.0) for position in range(12)]
    presence = {position: False for position in range(12)}

    forced = dict(force_absent_to_zero(rows, presence, window=12))

    assert forced[10] == 80.0  # 先頭は窓が埋まらないので判定しない
    assert forced[11] == 0.0


def test_one_sighting_in_the_window_keeps_the_label():
    rows = [(position, 60.0) for position in range(30)]
    presence = {position: False for position in range(0, 30, 2)}
    presence[14] = True  # 窓の途中で一度でも写っていれば離席とはみなさない

    forced = dict(force_absent_to_zero(rows, presence, window=12))

    assert forced[25] == 60.0  # 14 が窓 [14, 25] に入る
    assert forced[26] == 0.0   # 窓 [15, 26] には不在しかない


def test_unknown_presence_never_forces_zero():
    rows = [(position, 50.0) for position in range(20)]

    assert force_absent_to_zero(rows, {}, window=12) == rows
