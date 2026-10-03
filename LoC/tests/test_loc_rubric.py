"""属性スキーマと LoC 合成ルール（tools/loc_rubric.py）を検証する。

ラベルの質がモデルの精度を直接決めるので、合成ルールの性質はテストで固定しておく:
- 写っていない属性（not_visible）が値を歪めないこと
- 画角が違っても値域が [1, 5] に収まること
- 非学習が確定する条件が、加点で打ち消されないこと
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from loc_rubric import (
    ATTRIBUTE_NAMES,
    NOT_VISIBLE,
    compose_loc,
    normalize_attributes,
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


# --- ゲート（非学習が確定する条件） ---


def test_phone_in_hand_is_always_lowest():
    # 筆記の証拠がすべて揃っていてもスマホ操作が優先される
    assert compose_loc(attrs(phone='in_hand')) == 1.0


def test_head_down_is_lowest():
    assert compose_loc(attrs(head_posture='down')) == 1.0


def test_no_hands_and_no_contact_is_low():
    # 離席。現在時刻の事実（手・ペン・教材への接触）だけで判定する
    assert compose_loc(attrs(hands_in_frame=0, pen_held=False, hand_on_material=False)) == 1.5


def test_absent_hands_are_not_rescued_by_claimed_progress():
    # writing_increased は60秒窓の性質で誤検出もある。離席の検出を打ち消してはいけない
    away = attrs(hands_in_frame=0, pen_held=False, hand_on_material=False,
                 writing_increased=True)

    assert compose_loc(away) == 1.5


def test_hands_out_of_frame_while_holding_a_pen_is_not_treated_as_away():
    # 画角の端で手が切れていてもペンを握っていれば離席ではない
    assert compose_loc(attrs(hands_in_frame=0, pen_held=True)) > 2.0


def test_no_material_and_no_device_is_low():
    assert compose_loc(attrs(material_open=False)) == 2.0


def test_no_material_but_pc_in_use_is_not_gated():
    # PC学習者がゲートに引っかからないこと
    assert compose_loc(attrs(material_open=False, device_in_use='pc')) > 2.0


# --- 証拠による加減点 ---


def test_full_evidence_reaches_the_top():
    best = attrs(hands_in_frame=2, page_turned=True, head_posture='upright')

    assert compose_loc(best) == 5.0


def test_stalled_hands_score_below_average():
    # 手はあるが筆記もデバイスも教材への関与も無い
    stalled = attrs(pen_tip_on_paper=False, writing_increased=False,
                    hand_on_material=False, pen_held=False)

    assert compose_loc(stalled) < 3.0


def test_phone_on_desk_lowers_the_score():
    with_phone = compose_loc(attrs(phone='on_desk'))
    without_phone = compose_loc(attrs(phone='absent'))

    assert with_phone < without_phone


def test_thinking_with_hand_on_material_stays_around_average():
    # 書いてはいないが教材に手を置いて読んでいる＝「普通」程度に収まる
    thinking = attrs(pen_tip_on_paper=False, writing_increased=False)

    assert 2.8 <= compose_loc(thinking) <= 4.0


# --- not_visible の扱い（画角の違いへの頑健性） ---


def test_invisible_attributes_do_not_drag_the_score():
    # 顔が写らない動画と写る動画で、同じ学習状態なら同じ評価になるべき
    overhead = attrs(head_posture=NOT_VISIBLE, hands_in_frame=2, page_turned=True)
    frontal = attrs(head_posture='upright', hands_in_frame=2, page_turned=True)

    assert compose_loc(overhead) == compose_loc(frontal) == 5.0


def test_score_stays_in_range_with_mostly_invisible_attributes():
    sparse = {name: NOT_VISIBLE for name in ATTRIBUTE_NAMES}
    sparse['pen_tip_on_paper'] = True

    loc = compose_loc(sparse)

    assert loc is not None and 1.0 <= loc <= 5.0


def test_nothing_observable_returns_none():
    # 何も観測できなければラベルを付けない（当てずっぽうの3を作らない）
    assert compose_loc({name: NOT_VISIBLE for name in ATTRIBUTE_NAMES}) is None
