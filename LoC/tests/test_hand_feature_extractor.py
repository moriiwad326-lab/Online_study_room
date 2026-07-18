from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from MediaPipe_Hands.hand_feature_extractor import (
    FEATURE_DIM,
    NUM_COORDS,
    NUM_HANDS,
    NUM_LANDMARKS,
    HandFeatureExtractor,
    extract_hand_features,
)


def test_feature_dim_matches_landmark_geometry():
    assert FEATURE_DIM == NUM_LANDMARKS * NUM_COORDS * NUM_HANDS
    assert FEATURE_DIM == 84


def test_extract_returns_zero_vector_when_no_hand_detected():
    blank_image = np.zeros((480, 640, 3), dtype=np.uint8)

    with HandFeatureExtractor(static_image_mode=True) as extractor:
        features = extractor.extract(blank_image)

    assert features.shape == (FEATURE_DIM,)
    assert features.dtype == np.float32
    assert np.all(features == 0)


def test_extract_hand_features_helper_without_extractor():
    blank_image = np.zeros((480, 640, 3), dtype=np.uint8)

    features = extract_hand_features(blank_image)

    assert features.shape == (FEATURE_DIM,)
    assert np.all(features == 0)
