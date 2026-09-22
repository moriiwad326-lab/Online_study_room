"""LoCModel の forward を「直近 N フレーム」で試す動作確認スクリプト。

学習の前に、①〜⑧の結線が意図どおり動くか（入力の形・出力の形・値域）を
手早く確かめるためのもの。3つのモードがある。

    # 1. ランダム特徴で forward の配線だけ確認する（依存が torch だけで済む）
    python LoC/tools/try_forward.py

    # 2. 実フレーム画像から特徴を作って推論する（ConvNeXt + MediaPipe が必要）
    python LoC/tools/try_forward.py --frames "LoC/tools/output/202606091834"

    # 3. 学習済みチェックポイントを読み込んで推論する
    python LoC/tools/try_forward.py --frames <dir> --checkpoint LoC/checkpoints/loc_model.pt

注意: 手の特徴は「直前フレームとの差分」を含むステートフルな抽出なので、
フレームは必ず時系列順に1枚ずつ通すこと（このスクリプトは連番を数値ソートする）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Tuple

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import numpy as np
import torch

from core import HAND_FEATURE_DIM, IMAGE_FEATURE_DIM, LOC_MAX, LOC_MIN, build_model

DEFAULT_SEQ_LEN = 20
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


def random_features(seq_len: int) -> Tuple[torch.Tensor, torch.Tensor]:
    """特徴抽出器を動かさずに、ランダム値で forward の配線だけ確認する。"""
    img = torch.randn(1, seq_len, IMAGE_FEATURE_DIM)
    hand = torch.randn(1, seq_len, HAND_FEATURE_DIM)
    return img, hand


def sorted_frame_paths(frame_dir: Path, seq_len: int, tail: bool) -> list[Path]:
    """フレーム画像を時系列順に seq_len 枚選ぶ。

    `convert_frame_from_video.py` の出力はファイル名が秒数の連番（0.jpg, 5.jpg, ...）
    なので、文字列ではなく**数値として**ソートしないと 10 < 100 < 5 の順になる。
    """
    paths = [p for p in frame_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES]
    try:
        paths.sort(key=lambda p: int(p.stem))
    except ValueError:  # 連番でない命名なら名前順にフォールバック
        paths.sort()

    if len(paths) < seq_len:
        raise SystemExit(
            f"フレームが足りません: {frame_dir} に {len(paths)} 枚 < seq_len={seq_len}"
        )
    # 末尾を使うと「映像の最後の20フレームから現在のLoCを出す」本番と同じ形になる
    return paths[-seq_len:] if tail else paths[:seq_len]


def features_from_frames(
    frame_dir: Path, seq_len: int, tail: bool, device: str
) -> Tuple[torch.Tensor, torch.Tensor]:
    """フレーム画像から、学習時（build_features.py）と同じ前処理で特徴列を作る。"""
    import cv2

    from ConvNeXT.image_feature_extractor import ImageFeatureExtractor
    from MediaPipe_Hands.hand_feature_extractor import HandFeatureExtractor

    paths = sorted_frame_paths(frame_dir, seq_len, tail)
    print(f"フレーム: {paths[0].name} ... {paths[-1].name}（{len(paths)}枚）")

    image_extractor = ImageFeatureExtractor(device=device)
    hand_extractor = HandFeatureExtractor()
    try:
        img_feats, hand_feats = [], []
        for path in paths:
            frame = cv2.imread(str(path))
            if frame is None:
                raise SystemExit(f"画像を読み込めません: {path}")
            img_feats.append(image_extractor.extract(frame))
            hand_feats.append(hand_extractor.extract(frame))  # ← 時系列順に通すこと
    finally:
        image_extractor.close()
        hand_extractor.close()

    detected = sum(1 for f in hand_feats if np.any(f))
    print(f"手を検出できたフレーム: {detected}/{len(hand_feats)}")

    img = torch.from_numpy(np.stack(img_feats)).unsqueeze(0)  # (1, T, 768)
    hand = torch.from_numpy(np.stack(hand_feats)).unsqueeze(0)  # (1, T, 162)
    return img, hand


def load_model(checkpoint: Path | None, device: torch.device, seq_len: int):
    """チェックポイントがあれば読み込み、無ければ未学習の初期モデルを返す。"""
    if checkpoint is None:
        print("モデル: 未学習（ランダム初期値） ※出力値そのものに意味はない")
        return build_model().eval().to(device), seq_len

    state = torch.load(checkpoint, map_location=device)
    trained_seq_len = int(state.get("seq_len", seq_len))
    if trained_seq_len != seq_len:
        print(f"注意: 学習時の seq_len={trained_seq_len} に合わせます（指定は {seq_len}）")
    model = build_model(dropout=float(state.get("dropout", 0.2)))
    model.load_state_dict(state["model_state_dict"])
    print(f"モデル: {checkpoint}")
    return model.eval().to(device), trained_seq_len


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frames", type=Path, default=None,
                        help="フレーム画像のディレクトリ。省略時はランダム特徴で配線だけ確認")
    parser.add_argument("--seq-len", type=int, default=DEFAULT_SEQ_LEN,
                        help=f"入力するフレーム数（既定 {DEFAULT_SEQ_LEN}）")
    parser.add_argument("--checkpoint", type=Path, default=None,
                        help="学習済みチェックポイント。省略時は未学習の初期モデル")
    parser.add_argument("--head", action="store_true",
                        help="末尾ではなく先頭の seq_len 枚を使う")
    parser.add_argument("--device", default=None, help="cpu / cuda（既定は自動判定）")
    args = parser.parse_args()

    device = torch.device(args.device) if args.device else torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
    )
    model, seq_len = load_model(args.checkpoint, device, args.seq_len)

    if args.frames is None:
        img, hand = random_features(seq_len)
        print("入力: ランダム特徴")
    else:
        img, hand = features_from_frames(args.frames, seq_len, tail=not args.head,
                                         device=str(device))
        print(f"入力: {args.frames}")

    img, hand = img.to(device), hand.to(device)
    with torch.no_grad():  # eval() 済みなので Dropout は無効
        loc = model(img, hand)

    print(f"  img_feat  : {tuple(img.shape)}  (B, T, {IMAGE_FEATURE_DIM})")
    print(f"  hand_feat : {tuple(hand.shape)}  (B, T, {HAND_FEATURE_DIM})")
    print(f"出力 shape  : {tuple(loc.shape)}")
    value = float(loc.item())
    print(f"LoC         : {value:.3f}  ({LOC_MIN:.0f}〜{LOC_MAX:.0f})")
    print(f"0〜100換算  : {(value - LOC_MIN) / (LOC_MAX - LOC_MIN) * 100:.1f}")

    assert loc.shape == (1, 1), "出力は (B, 1) のはず"
    assert LOC_MIN <= value <= LOC_MAX, "⑧の出力レンジ制約が効いていない"
    print("OK: 出力の形と値域は設計どおり")


if __name__ == "__main__":
    main()
