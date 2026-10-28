"""在席判定器（presence.py）と、推論時に「12フレーム不在で 0」を返すゲートを検証する。"""

from pathlib import Path
import json
import sys

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from presence import PresenceGate, evaluate, fit, load_presence_labels


def separable(n=200, dim=768, seed=0):
    rng = np.random.default_rng(seed)
    targets = (rng.random(n) > 0.3).astype(np.float32)
    features = rng.normal(size=(n, dim)).astype(np.float32)
    features[:, 0] += np.where(targets == 1, 3.0, -3.0)
    return features, targets


def test_fit_learns_a_separable_signal():
    features, targets = separable()

    metrics = evaluate(fit(features, targets), features, targets)

    assert metrics["accuracy"] > 0.95
    assert metrics["absent_recall"] > 0.9


def test_gate_fires_only_after_a_full_window_of_absence(tmp_path):
    features, targets = separable()
    path = tmp_path / "presence.pt"
    torch.save({"model_state_dict": fit(features, targets).state_dict(), "threshold": 0.5}, path)
    gate = PresenceGate(path, window=3, device=torch.device("cpu"))
    absent = features[targets == 0][:3]
    present = features[targets == 1][0]

    assert gate.push(absent[0]) is False  # 窓が埋まっていない
    assert gate.push(absent[1]) is False
    assert gate.push(absent[2]) is True
    assert gate.push(present) is False    # 一度でも写れば解除
    gate.reset()
    assert gate.latest_present is None


def test_only_new_schema_labels_are_used(tmp_path):
    rows = [
        {"frame": 0, "schema_version": 2, "attributes": {"person_visible": False}},
        {"frame": 5, "schema_version": 3, "attributes": {"person_visible": True}},
        {"frame": 10, "schema_version": 3, "attributes": {"person_visible": "not_visible"}},
    ]
    (tmp_path / "attributes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    assert load_presence_labels(tmp_path) == {5: True}
