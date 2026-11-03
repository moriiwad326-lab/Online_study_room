"""metrics.py の指標計算と、train.py の学習後 test 評価・evaluate.py の test 評価を検証する。"""

from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("torch")

from metrics import compute_metrics, format_report, to_classes  # noqa: E402
from test_loc_dataset import make_dataset_dir  # noqa: E402


def test_to_classes_uses_20_point_bins_and_keeps_100_in_top_class():
    assert to_classes(np.array([0, 19.9, 20, 39.9, 40, 60, 79.9, 80, 100])).tolist() == \
        [0, 0, 1, 1, 2, 3, 3, 4, 4]


def test_perfect_prediction():
    true = np.array([10.0, 30.0, 50.0, 70.0, 90.0])
    metrics = compute_metrics(true, true, train_mean=50.0)

    assert metrics["mae"] == 0.0
    assert metrics["acc"] == 1.0
    assert metrics["acc_tol"] == 1.0
    assert metrics["macro_f1"] == 1.0
    assert metrics["r2"] == pytest.approx(1.0)
    assert np.trace(metrics["confusion"]) == 5
    # 平均50を出すだけなら 40-60 の1件だけ当たる
    assert metrics["baseline_acc"] == pytest.approx(0.2)
    assert metrics["baseline_mae"] == pytest.approx(24.0)


def test_acc_and_tolerance_differ_across_bin_edges():
    # 19 と 21 は誤差2だがクラスは違う / 41 と 59 は誤差18だがクラスは同じ
    metrics = compute_metrics(np.array([21.0, 59.0]), np.array([19.0, 41.0]))

    assert metrics["acc"] == 0.5
    assert metrics["acc_tol"] == 0.5
    assert metrics["confusion"][0, 1] == 1
    assert metrics["confusion"][2, 2] == 1


def test_report_includes_main_metrics():
    true = np.array([10.0, 50.0, 90.0])
    text = format_report(compute_metrics(true + 5, true, train_mean=50.0))

    for key in ("MAE=", "ACC(5クラス)=", "ACC±10=", "macro-F1=", "baseline", "混同行列"):
        assert key in text


def _make_split(tmp_path):
    train_root = tmp_path / "train"
    test_root = tmp_path / "test"
    for i in range(2):
        make_dataset_dir(train_root, f"train_{i}", [float(10 + 20 * (j % 5)) for j in range(20)])
    make_dataset_dir(test_root, "test_0", [float(10 + 20 * (j % 5)) for j in range(20)])
    return train_root, test_root


def test_train_evaluates_best_weights_on_test(tmp_path):
    from train import train

    train_root, test_root = _make_split(tmp_path)
    checkpoint = tmp_path / "model.pt"
    metrics = train(train_root, seq_len=3, epochs=2, batch_size=8, checkpoint_path=checkpoint,
                    device="cpu", test_root=test_root, verbose=False)

    assert metrics["test"]["n"] == 20 - 3 + 1
    assert 0.0 <= metrics["test"]["acc"] <= 1.0
    assert "baseline_mae" in metrics["test"]


def test_evaluate_checkpoint_on_test(tmp_path):
    from evaluate import evaluate_checkpoint
    from train import train

    train_root, test_root = _make_split(tmp_path)
    checkpoint = tmp_path / "model.pt"
    train(train_root, seq_len=3, epochs=1, batch_size=8, checkpoint_path=checkpoint,
          device="cpu", verbose=False)

    result = evaluate_checkpoint(checkpoint, test_root=test_root, train_root=train_root, device="cpu")

    assert result["seq_len"] == 3
    assert result["overall"]["n"] == 18
    assert [row["video"] for row in result["per_video"]] == ["test_0"]
