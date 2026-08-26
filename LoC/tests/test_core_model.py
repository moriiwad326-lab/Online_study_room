"""LoCModel（core.py）の結線を検証する。"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

torch = pytest.importorskip("torch")

from core import HAND_FEATURE_DIM, IMAGE_FEATURE_DIM, LOC_MAX, LOC_MIN, LoCModel


@pytest.fixture()
def model():
    return LoCModel()


def test_feature_dims_match_extractors():
    # 射影MLPの入力次元が、各特徴抽出器の出力次元と一致していること
    assert IMAGE_FEATURE_DIM == 768
    assert HAND_FEATURE_DIM == 162


def test_forward_shape(model):
    batch, seq_len = 3, 7
    img = torch.randn(batch, seq_len, IMAGE_FEATURE_DIM)
    hand = torch.randn(batch, seq_len, HAND_FEATURE_DIM)

    out = model(img, hand)

    assert out.shape == (batch, 1)


def test_output_is_bounded_to_loc_range(model):
    # ⑧ sigmoid*4+1 により、どんな入力でも物理的に 1〜5 に収まる
    img = torch.randn(8, 5, IMAGE_FEATURE_DIM) * 1e3
    hand = torch.randn(8, 5, HAND_FEATURE_DIM) * 1e3

    out = model(img, hand)

    assert torch.all(out >= LOC_MIN)
    assert torch.all(out <= LOC_MAX)


def test_projection_dims_follow_architecture(model):
    # ③768->128 / ④162->128->64 / ⑤concat=192 / ⑥hidden=256 / ⑦256->64->1
    assert model.img_proj.layers[0].in_features == IMAGE_FEATURE_DIM
    assert model.img_proj.output_size == 128
    assert model.hand_proj.layers[0].in_features == HAND_FEATURE_DIM
    assert model.hand_proj.output_size == 64
    assert model.lstm.lstm.input_size == 128 + 64
    assert model.lstm.lstm.hidden_size == 256
    assert model.lstm.lstm.bidirectional is False  # リアルタイム推論では未来を見られない
    assert model.head[-1].out_features == 1


def test_uses_only_last_timestep(model):
    # 最終ステップのみ使う設計なので、過去側を変えずに末尾を変えると出力が変わる
    model.eval()
    img = torch.randn(1, 6, IMAGE_FEATURE_DIM)
    hand = torch.randn(1, 6, HAND_FEATURE_DIM)
    with torch.no_grad():
        base = model(img, hand)
        changed_hand = hand.clone()
        changed_hand[:, -1, :] += 5.0
        assert not torch.allclose(base, model(img, changed_hand))


def test_gradients_reach_both_branches(model):
    img = torch.randn(2, 4, IMAGE_FEATURE_DIM)
    hand = torch.randn(2, 4, HAND_FEATURE_DIM)

    model(img, hand).sum().backward()

    assert model.img_proj.layers[0].weight.grad is not None
    assert model.hand_proj.layers[0].weight.grad is not None
    assert torch.any(model.img_proj.layers[0].weight.grad != 0)
    assert torch.any(model.hand_proj.layers[0].weight.grad != 0)
