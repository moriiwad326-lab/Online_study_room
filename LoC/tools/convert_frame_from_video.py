"""Extract frames from videos at regular second intervals.

Usage:
  python tools/convert_image_from_video.py <input_path> [--output OUTPUT_DIR] [--rate SECONDS]

If <input_path> is a directory, all video files in it will be processed.
Each video's extracted frames are saved under OUTPUT_DIR/<video_basename>/, with filenames
named by the extracted second (e.g. 0.jpg, 1.jpg, ...).
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Iterable, List

try:
	import cv2
except Exception:
	cv2 = None


VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mpg", ".mpeg"}


def iter_videos(path: Path) -> Iterable[Path]:
	if path.is_file():
		yield path
		return
	for p in sorted(path.iterdir()):
		if p.suffix.lower() in VIDEO_EXTS and p.is_file():
			yield p


def extract_every_n_seconds(video_path: Path, out_base: Path, rate_s: int = 1, image_ext: str = ".jpg") -> List[Path]:
	if cv2 is None:
		raise RuntimeError("OpenCV (cv2) is required. Install with: pip install opencv-python")

	cap = cv2.VideoCapture(str(video_path))
	if not cap.isOpened():
		raise RuntimeError(f"Cannot open video: {video_path}")

	fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
	frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
	duration_s = frame_count / fps if fps > 0 else 0.0
	max_second = int(math.floor(duration_s))

	out_base.mkdir(parents=True, exist_ok=True)
	saved: List[Path] = []
	for sec in range(0, max_second + 1, rate_s):
		# Seek to the millisecond position and read
		cap.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000.0)
		ret, frame = cap.read()
		if not ret:
			# try seeking by frame index as fallback
			if fps > 0:
				frame_idx = int(sec * fps)
				cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
				ret, frame = cap.read()
			if not ret:
				continue
		out_filename = out_base / f"{sec}{image_ext}"
		cv2.imwrite(str(out_filename), frame)
		saved.append(out_filename)
	cap.release()
	return saved


def main(argv: List[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Extract frames from video at N-second intervals.")
	parser.add_argument("input", help="Input video file or directory containing videos")
	parser.add_argument("--output", "-o", default="tools/output", help="Output base directory (default: output/)")
	parser.add_argument("--rate", "-r", type=int, default=1, help="Sampling rate in seconds (default: 1)")
	parser.add_argument("--ext", default=".jpg", help="Image extension to write (default: .jpg)")
	args = parser.parse_args(argv)

	input_path = Path(args.input)
	out_base_dir = Path(args.output)
	if not input_path.exists():
		print(f"Input path does not exist: {input_path}", file=sys.stderr)
		return 2

	videos = list(iter_videos(input_path))
	if not videos:
		print(f"No video files found in {input_path}", file=sys.stderr)
		return 3

	for vid in videos:
		name = vid.stem
		target_dir = out_base_dir / name
		print(f"Processing {vid} -> {target_dir} (every {args.rate}s)")
		try:
			saved = extract_every_n_seconds(vid, target_dir, rate_s=args.rate, image_ext=args.ext)
			print(f"Saved {len(saved)} images to {target_dir}")
		except Exception as e:
			print(f"Failed processing {vid}: {e}", file=sys.stderr)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())