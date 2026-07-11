"""Compute LoC (concentration) for frames in a folder using qwen3_5.

This script scans an input directory for image frames, calls
`qwen3_5.evaluate_loc` with up to `--window` frames of context
(default 10) for each frame (the last image in the window is the
target), and writes `labels.csv` into the input directory containing
two columns: `frame` and `LoC` (1-5 integer or -1 when failed).

Usage:
  python convert_LoC_from_frame.py /path/to/frames_dir [--csv labels.csv]

The script dynamically imports the local `qwen3_5` module so importing
this file does not require Ollama unless you actually run the
evaluation. Use `--simulate` to generate placeholder labels for tests.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
from typing import List, Optional, Sequence, Tuple

IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
DEFAULT_WINDOW = 20

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')


def list_image_files(input_dir: str) -> List[str]:
	files = [f for f in os.listdir(input_dir)
			 if f.lower().endswith(IMAGE_EXTS) and os.path.isfile(os.path.join(input_dir, f))]
	if not files:
		raise FileNotFoundError(f"No image files found in {input_dir!r} with extensions {IMAGE_EXTS}")

	def sort_key(fn: str):
		m = re.search(r"(\d+)(?!.*\d)", fn)
		if m:
			return (0, int(m.group(1)))
		return (1, fn)

	files.sort(key=sort_key)
	return files


def extract_frame_number(filename: str) -> Optional[int]:
	m = re.search(r"(\d+)(?!.*\d)", filename)
	if not m:
		return None
	try:
		return int(m.group(1))
	except Exception:
		return None


def compute_loc_for_images(input_dir: str, files: Sequence[str], window: int = DEFAULT_WINDOW,
						   model: Optional[str] = None, simulate: bool = False) -> List[Tuple[int, Optional[int]]]:
	"""Compute LoC for each image.

	Returns a list of tuples (frame_number, level_or_None).
	"""
	results: List[Tuple[int, Optional[int]]] = []
	effective_window = max(1, min(window, DEFAULT_WINDOW))

	qwen = None
	user_prompt = None
	if not simulate:
		# import locally to avoid requiring Ollama at import-time
		script_dir = os.path.dirname(__file__)
		if script_dir not in sys.path:
			sys.path.insert(0, script_dir)
		try:
			import importlib

			qwen = importlib.import_module('qwen3_5')
			user_prompt = getattr(qwen, 'user_prompt', None)
			if user_prompt is None:
				logging.warning('qwen3_5.user_prompt not found; using empty prompt')
				user_prompt = ''
		except Exception as exc:  # pragma: no cover - environment dependent
			raise RuntimeError(f'Failed to import qwen3_5 module: {exc}') from exc

	total = len(files)
	for idx, fname in enumerate(files):
		start = max(0, idx - effective_window + 1)
		window_files = files[start: idx + 1]
		abs_paths = [os.path.abspath(os.path.join(input_dir, f)) for f in window_files]
		frame_num = extract_frame_number(fname) or idx

		if simulate:
			level = 3
			logging.info(f"[simulate] frame {frame_num}: LoC={level}")
		else:
			try:
				if model:
					level = qwen.evaluate_loc(user_prompt, abs_paths, model=model)
				else:
					level = qwen.evaluate_loc(user_prompt, abs_paths)
				logging.info(f"frame {frame_num}: LoC={level}")
			except Exception as exc:  # pragma: no cover - runtime dependent
				logging.error(f"Error evaluating frame {frame_num} (index {idx}): {exc}")
				level = None

		results.append((frame_num, level))

	return results


def write_labels_csv(output_path: str, rows: Sequence[Tuple[int, Optional[int]]]) -> None:
	with open(output_path, 'w', newline='', encoding='utf-8') as fh:
		writer = csv.writer(fh)
		writer.writerow(['frame', 'LoC'])
		for frame_num, level in rows:
			writer.writerow([frame_num, -1 if level is None else level])


def main(argv: Optional[List[str]] = None) -> int:
	parser = argparse.ArgumentParser(description='Compute LoC (concentration) for frames in a folder using qwen3_5.')
	parser.add_argument('input_dir', help='Directory containing frame images')
	parser.add_argument('--csv', '-o', default=None, help='Output CSV filename (defaults to labels.csv in input directory)')
	parser.add_argument('--window', '-w', type=int, default=DEFAULT_WINDOW, help='Number of frames to include as context (default 4 to stay within Ollama context limits)')
	parser.add_argument('--model', '-m', default=None, help='Model name to pass to qwen3_5.evaluate_loc')
	parser.add_argument('--simulate', action='store_true', help='Simulate outputs without calling qwen3_5 (for testing)')
	args = parser.parse_args(argv)

	input_dir = args.input_dir
	if not os.path.isdir(input_dir):
		logging.error(f"Input directory not found: {input_dir!r}")
		return 2

	files = list_image_files(input_dir)
	output_csv = args.csv if args.csv else os.path.join(input_dir, 'labels.csv')

	logging.info(f"Found {len(files)} images in {input_dir!r}. Window={args.window}. Output CSV: {output_csv!r}")

	rows = compute_loc_for_images(input_dir, files, window=args.window, model=args.model, simulate=args.simulate)

	write_labels_csv(output_csv, rows)
	logging.info(f"Wrote {len(rows)} rows to {output_csv!r}")
	return 0


if __name__ == '__main__':
	raise SystemExit(main())

