# Frame extraction for study videos

This project includes a small script to extract images from study videos at regular second intervals.

Usage:

```bash
python tools/convert_frame_from_video.py <input_path> --output output --rate 1
```

- `<input_path>`: path to a single video file (e.g. `input/input_video.mp4`) or a directory containing videos.
- `--output`: base output directory (default: `output/`). Each video will have a subdirectory named after the video filename (without extension).
- `--rate`: sampling rate in seconds (default: `1`).
- Images are saved as `<second>.jpg` (e.g. `0.jpg`, `1.jpg`, ...).

Install dependencies:

```bash
pip install -r requirements.txt
```

Notes:
- The script uses OpenCV (`cv2`). If you prefer `ffmpeg` you can adapt the script or run ffmpeg commands manually.
