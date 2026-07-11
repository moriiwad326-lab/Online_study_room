"""Run the full dataset creation pipeline for video input.

This script orchestrates the existing tools:
1. Split input videos into frame images at a fixed interval.
2. Run LoC evaluation over the extracted frames using qwen3_5.
3. Save the resulting labels CSV next to the extracted frames.

Example:
    python tools/dataset_creation.py input_video.mp4 --output output --rate 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from convert_LoC_from_frame import compute_loc_for_images, list_image_files, write_labels_csv
from convert_frame_from_video import extract_every_n_seconds


def build_pipeline_config(
    input_path: str | Path,
    output_root: str | Path,
    rate_seconds: int = 5,
    window: int = 20,
    model: Optional[str] = None,
    simulate: bool = False,
) -> dict[str, object]:
    """Create a configuration dictionary for a single video pipeline."""
    video_path = Path(input_path).expanduser().resolve()
    output_root_path = Path(output_root).expanduser().resolve()
    frame_output_dir = output_root_path / video_path.stem
    return {
        "video_input": str(video_path),
        "frame_output_dir": str(frame_output_dir),
        "labels_csv": str(frame_output_dir / "labels.csv"),
        "rate_seconds": rate_seconds,
        "window": window,
        "model": model,
        "simulate": simulate,
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
    window: int = 20,
    model: Optional[str] = None,
    simulate: bool = False,
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
            window=window,
            model=model,
            simulate=simulate,
        )
        frame_output_dir = Path(config["frame_output_dir"])
        frame_output_dir.mkdir(parents=True, exist_ok=True)

        print(f"Processing {video_path} -> {frame_output_dir} (every {rate_seconds}s)")
        saved_frames = extract_every_n_seconds(video_path, frame_output_dir, rate_s=rate_seconds, image_ext=".jpg")
        if not saved_frames:
            print(f"No frames were extracted from {video_path}")
            continue

        image_files = list_image_files(str(frame_output_dir))
        rows = compute_loc_for_images(str(frame_output_dir), image_files, window=window, model=model, simulate=simulate)
        write_labels_csv(str(Path(config["labels_csv"])), rows)

        print(f"Saved {len(saved_frames)} frames and {len(rows)} LoC labels to {frame_output_dir}")
        results.append(config)

    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create a frame-based LoC dataset from video input")
    parser.add_argument("input", help="Input video file or directory containing videos")
    parser.add_argument("--output", "-o", default="output", help="Output root directory")
    parser.add_argument("--rate", "-r", type=int, default=5, help="Sampling interval in seconds (default: 5)")
    parser.add_argument("--window", "-w", type=int, default=20, help="Context window for LoC evaluation")
    parser.add_argument("--model", "-m", default=None, help="Model name for qwen3_5.evaluate_loc")
    parser.add_argument("--simulate", action="store_true", help="Simulate LoC outputs without calling qwen")
    args = parser.parse_args(argv)

    try:
        run_pipeline(
            input_path=args.input,
            output_root=args.output,
            rate_seconds=args.rate,
            window=args.window,
            model=args.model,
            simulate=args.simulate,
        )
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
