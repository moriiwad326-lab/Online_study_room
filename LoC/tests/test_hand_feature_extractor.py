from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from MediaPipe_Hands.hand_feature_extractor import (
    FEATURE_DIM,
    FLAG_OFFSET,
    HAND_BLOCK_DIM,
    NUM_COORDS,
    NUM_HANDS,
    NUM_LANDMARKS,
    REL_DIM,
    SCALE_OFFSET,
    STATIC_DIM,
    VELOCITY_DIM,
    WRIST_XY_OFFSET,
    HandFeatureExtractor,
    compute_velocity,
    encode_hand_block,
    extract_hand_features,
)


class _Landmark:
    """MediaPipe の NormalizedLandmark を模した最小のスタブ。"""

    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


def _make_landmarks(wrist=(0.5, 0.5, 0.0), step=0.02):
    """手首から一定方向に広がる合成ランドマーク列を作る。"""
    return [
        _Landmark(
            wrist[0] + i * step,
            wrist[1] + i * step * 0.5,
            wrist[2] + i * step * 0.25,
        )
        for i in range(NUM_LANDMARKS)
    ]


def _scale_about_wrist(landmarks, factor):
    """手首を固定したまま手全体を拡大する（＝カメラに近づいた状態）。"""
    wrist = landmarks[0]
    return [
        _Landmark(
            wrist.x + (lm.x - wrist.x) * factor,
            wrist.y + (lm.y - wrist.y) * factor,
            wrist.z + (lm.z - wrist.z) * factor,
        )
        for lm in landmarks
    ]


def test_feature_layout_is_consistent():
    assert REL_DIM == (NUM_LANDMARKS - 1) * NUM_COORDS == 60
    assert STATIC_DIM == 64
    assert VELOCITY_DIM == 17
    assert HAND_BLOCK_DIM == STATIC_DIM + VELOCITY_DIM == 81
    assert FEATURE_DIM == HAND_BLOCK_DIM * NUM_HANDS == 162


def test_extract_returns_zero_vector_when_no_hand_detected():
    blank_image = np.zeros((480, 640, 3), dtype=np.uint8)

    with HandFeatureExtractor(static_image_mode=True) as extractor:
        features = extractor.extract(blank_image)

    assert features.shape == (FEATURE_DIM,)
    assert features.dtype == np.float32
    assert np.all(features == 0)


def test_detection_flags_are_zero_when_no_hand_detected():
    """0埋めされたブロックが検出フラグによって識別できること。"""
    blank_image = np.zeros((480, 640, 3), dtype=np.uint8)

    with HandFeatureExtractor(static_image_mode=True) as extractor:
        features = extractor.extract(blank_image)

    for slot in range(NUM_HANDS):
        assert features[slot * HAND_BLOCK_DIM + FLAG_OFFSET] == 0.0


def test_extract_hand_features_helper_without_extractor():
    blank_image = np.zeros((480, 640, 3), dtype=np.uint8)

    features = extract_hand_features(blank_image)

    assert features.shape == (FEATURE_DIM,)
    assert np.all(features == 0)


def test_encode_hand_block_marks_detection_and_keeps_wrist_position():
    block = encode_hand_block(_make_landmarks(wrist=(0.3, 0.7, 0.0)))

    assert block.shape == (STATIC_DIM,)
    assert block.dtype == np.float32
    assert block[FLAG_OFFSET] == 1.0
    np.testing.assert_allclose(
        block[WRIST_XY_OFFSET : WRIST_XY_OFFSET + 2], [0.3, 0.7], rtol=1e-5
    )
    assert block[SCALE_OFFSET] > 0.0


def test_encode_hand_block_is_translation_invariant():
    """手が画面内で移動しても、手の形を表す相対座標は変わらないこと。"""
    left = encode_hand_block(_make_landmarks(wrist=(0.2, 0.2, 0.0)))
    right = encode_hand_block(_make_landmarks(wrist=(0.8, 0.6, 0.0)))

    np.testing.assert_allclose(right[:REL_DIM], left[:REL_DIM], rtol=1e-5, atol=1e-6)
    # 絶対位置の方は当然変化する
    assert right[WRIST_XY_OFFSET] != left[WRIST_XY_OFFSET]


def test_encode_hand_block_is_scale_invariant():
    """カメラとの距離が変わっても相対座標は変わらず、scale だけが変化すること。"""
    base_landmarks = _make_landmarks()
    near_landmarks = _scale_about_wrist(base_landmarks, 2.0)

    base = encode_hand_block(base_landmarks)
    near = encode_hand_block(near_landmarks)

    np.testing.assert_allclose(near[:REL_DIM], base[:REL_DIM], rtol=1e-5, atol=1e-6)
    np.testing.assert_allclose(near[SCALE_OFFSET], base[SCALE_OFFSET] * 2.0, rtol=1e-5)


def test_compute_velocity_is_zero_without_previous_frame():
    static = np.zeros((NUM_HANDS, STATIC_DIM), dtype=np.float32)
    static[0] = encode_hand_block(_make_landmarks())

    velocity = compute_velocity(static, None)

    assert velocity.shape == (NUM_HANDS, VELOCITY_DIM)
    assert np.all(velocity == 0)


def test_compute_velocity_separates_hand_movement_from_finger_movement():
    """手を平行移動しただけなら、手首速度のみが立ち指先速度は0になること。"""
    previous = np.zeros((NUM_HANDS, STATIC_DIM), dtype=np.float32)
    current = np.zeros((NUM_HANDS, STATIC_DIM), dtype=np.float32)
    previous[0] = encode_hand_block(_make_landmarks(wrist=(0.4, 0.5, 0.0)))
    current[0] = encode_hand_block(_make_landmarks(wrist=(0.5, 0.5, 0.0)))

    velocity = compute_velocity(current, previous)

    np.testing.assert_allclose(velocity[0, :2], [0.1, 0.0], atol=1e-5)
    np.testing.assert_allclose(velocity[0, 2:], 0.0, atol=1e-6)
    # 未検出の手は速度も0のまま
    assert np.all(velocity[1] == 0)


def test_compute_velocity_is_zero_when_hand_missing_in_either_frame():
    """未検出フレームとの差分で偽の速度が立たないこと。"""
    detected = np.zeros((NUM_HANDS, STATIC_DIM), dtype=np.float32)
    detected[0] = encode_hand_block(_make_landmarks(wrist=(0.9, 0.9, 0.0)))
    missing = np.zeros((NUM_HANDS, STATIC_DIM), dtype=np.float32)

    appearing = compute_velocity(detected, missing)
    disappearing = compute_velocity(missing, detected)

    assert np.all(appearing == 0)
    assert np.all(disappearing == 0)
