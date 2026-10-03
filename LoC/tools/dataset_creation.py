"""Run the full dataset creation pipeline for video input.

This script orchestrates the existing tools:
1. Split input videos into frame images at a fixed interval.
2. Run LoC evaluation over the extracted frames using qwen3_5. The leading
   frames without a full context window are skipped (see
   convert_LoC_from_frame); pass --keep-warmup to label them anyway.
3. Save the resulting labels CSV next to the extracted frames.

Example:
    python tools/dataset_creation.py input_video.mp4 --output output --rate 5
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from convert_LoC_from_frame import (
    compose_labels_from_attributes,
    compute_attributes_for_images,
    list_image_files,
)
from convert_frame_from_video import extract_every_n_seconds


def build_pipeline_config(
    input_path: str | Path,
    output_root: str | Path,
    rate_seconds: int = 5,
    stride: int = 1,
    samples: int = 1,
    model: Optional[str] = None,
    simulate: bool = False,
    skip_warmup: bool = True,
) -> dict[str, object]:
    """Create a configuration dictionary for a single video pipeline."""
    video_path = Path(input_path).expanduser().resolve()
    output_root_path = Path(output_root).expanduser().resolve()
    frame_output_dir = output_root_path / video_path.stem
    return {
        "video_input": str(video_path),
        "frame_output_dir": str(frame_output_dir),
        "labels_csv": str(frame_output_dir / "labels.csv"),
        "attributes_jsonl": str(frame_output_dir / "attributes.jsonl"),
        "rate_seconds": rate_seconds,
        "stride": stride,
        "samples": samples,
        "model": model,
        "simulate": simulate,
        "skip_warmup": skip_warmup,
    }


def iter_video_inputs(input_path: Path) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    video_files = [
        p for p in sorted(input_path.iterdir())
        if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mpg", ".mpeg"}
    ]
    return video_files


def run_pipeline(
    input_path: str | Path,
    output_root: str | Path,
    rate_seconds: int = 5,
    stride: int = 1,
    samples: int = 1,
    model: Optional[str] = None,
    simulate: bool = False,
    skip_warmup: bool = True,
    reuse_frames: bool = False,
) -> List[dict[str, object]]:
    """Process one or more videos and create frame images and LoC labels."""
    input_path_obj = Path(input_path).expanduser().resolve()
    output_root_path = Path(output_root).expanduser().resolve()
    videos = iter_video_inputs(input_path_obj)
    if not videos:
        raise FileNotFoundError(f"No supported video files found in: {input_path_obj}")

    results: List[dict[str, object]] = []
    for video_path in videos:
        config = build_pipeline_config(
            input_path=video_path,
            output_root=output_root_path,
            rate_seconds=rate_seconds,
            stride=stride,
            samples=samples,
            model=model,
            simulate=simulate,
            skip_warmup=skip_warmup,
        )
        frame_output_dir = Path(config["frame_output_dir"])
        has_frames = frame_output_dir.exists() and any(frame_output_dir.glob("*.jpg"))
        if has_frames and reuse_frames:
            # 抽出済みのフレームをそのまま使う。--rate を変えていないときに、
            # 数時間かかる再抽出を避けるため（ラベルだけ付け直したい場合）。
            print(f"Reusing extracted frames in {frame_output_dir}")
        else:
            if frame_output_dir.exists():
                # Remove any frames/labels left over from a previous run (e.g. at a
                # different --rate) so stale files don't get mixed in with the new
                # extraction and inflate the LoC pass with duplicate frames.
                shutil.rmtree(frame_output_dir)
            frame_output_dir.mkdir(parents=True, exist_ok=True)
            print(f"Processing {video_path} -> {frame_output_dir} (every {rate_seconds}s)")
            if not extract_every_n_seconds(video_path, frame_output_dir,
                                           rate_s=rate_seconds, image_ext=".jpg"):
                print(f"No frames were extracted from {video_path}")
                continue

        image_files = list_image_files(str(frame_output_dir))
        compute_attributes_for_images(
            str(frame_output_dir), image_files, stride=stride, samples=samples,
            model=model, simulate=simulate, skip_warmup=skip_warmup,
        )
        points = compose_labels_from_attributes(str(frame_output_dir), image_files)
        if not points:
            print(f"No LoC labels produced for {video_path}")
            continue

        print(f"Saved {len(image_files)} frames and {len(points)} evaluations to {frame_output_dir}")
        results.append(config)

    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create a frame-based LoC dataset from video input")
    parser.add_argument("input", help="Input video file or directory containing videos")
    parser.add_argument("--output", "-o", default="output", help="Output root directory")
    parser.add_argument("--rate", "-r", type=int, default=5, help="Sampling interval in seconds (default: 5)")
    parser.add_argument("--stride", "-s", type=int, default=1,
                        help="何フレームおきに属性を判定するか（既定1。間は線形補間）")
    parser.add_argument("--samples", "-n", type=int, default=1,
                        help="同じ入力を何回サンプリングして多数決を取るか（既定1）")
    parser.add_argument("--model", "-m", default=None, help="Ollama のモデル名")
    parser.add_argument("--simulate", action="store_true", help="qwen を呼ばずにダミー属性を書く")
    parser.add_argument("--reuse-frames", action="store_true",
                        help="抽出済みのフレームを再利用する（ラベルだけ付け直したいとき）")
    parser.add_argument("--keep-warmup", action="store_true",
                        help="文脈窓が埋まらない先頭フレームも評価する（非推奨）")
    args = parser.parse_args(argv)

    try:
        run_pipeline(
            input_path=args.input,
            output_root=args.output,
            rate_seconds=args.rate,
            stride=args.stride,
            samples=args.samples,
            model=args.model,
            simulate=args.simulate,
            skip_warmup=not args.keep_warmup,
            reuse_frames=args.reuse_frames,
        )
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
