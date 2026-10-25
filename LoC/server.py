"""Onsta フロントエンド（../static）と学習済み LoCModel をつなぐローカルサーバー。

1プロセスで次の2つを立てる。

- HTTP  http://localhost:8000/      … ../static をそのまま配信（カメラは localhost なら使える）
- WS    ws://localhost:8765/loc     … フロントの `js/locClient.js` が送るカメラフレームを受け取り、
                                      `LoCPredictor` で推定した LoC を返す

プロトコル（`static/README.md` の「LoC API の仕様」と同じ）:
    受信  { "type": "frame",  "timestamp": <ms>, "image": "<JPEGのBase64>" }
    送信  { "type": "loc",    "timestamp": <ms>, "loc": 0〜100 }
          { "type": "warmup", "timestamp": <ms>, "remaining": <残りサンプル数>,
            "total": <seq_len>, "seconds": <最初の推定までのおおよその秒数> }

フロントは約 5fps でフレームを送ってくるが、推論に使うのは `--interval` 秒（学習時の
`--rate` と同じ。既定 5 秒）ごとの1枚だけ。間引きはフロントの timestamp（撮影時刻）で行う。
モデルはフロントと同じ 0〜100 を出すので、小数第1位に丸めてそのまま返す。

`LoCPredictor` はステートフル（直近 seq_len 枚の窓と手の速度計算）なので、同時に計測できる
のは1接続だけ。新しい接続が来たら窓を捨ててそちらに切り替える。

使い方:
    python LoC/server.py                      # → http://localhost:8000/ を開く
    python LoC/server.py --checkpoint LoC/checkpoints/first_train/model.pt
    python LoC/server.py --no-static          # 推論サーバーだけ（フロントは別で配信）
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import functools
import http.server
import json
import sys
import threading
from pathlib import Path
from typing import List, Optional

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import cv2
import numpy as np
from websockets.asyncio.server import ServerConnection, serve

from core import LOC_MAX, LOC_MIN
from infer import LoCPredictor
from train import DEFAULT_CHECKPOINT

DEFAULT_STATIC_DIR = PACKAGE_ROOT.parent / "static"

# 一時停止などでフレームが途切れたとき、この倍数以上 interval が空いたら手の速度を
# 計算し直す（離れたフレーム同士の差分を「速度」として使わない）
GAP_RESET_FACTOR = 3.0


def to_percent(loc: float) -> float:
    """モデル出力（LOC_MIN〜LOC_MAX）をフロントの 0〜100 に変換する（現状は同じ値域）。"""
    return round((loc - LOC_MIN) / (LOC_MAX - LOC_MIN) * 100, 1)


def decode_jpeg(image_b64: str) -> Optional[np.ndarray]:
    try:
        data = np.frombuffer(base64.b64decode(image_b64), dtype=np.uint8)
    except (ValueError, TypeError):
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


class LoCServer:
    def __init__(self, predictor: LoCPredictor, interval: float):
        self.predictor = predictor
        self.interval = interval
        self._active: Optional[ServerConnection] = None
        self._busy = False

    async def handle(self, connection: ServerConnection) -> None:
        if connection.request.path != "/loc":
            await connection.close(code=1008, reason="path must be /loc")
            return

        # 新しい接続に切り替える（窓・速度の状態を捨てる）
        self._active = connection
        self.predictor.reset()
        next_sample_at: Optional[float] = None  # 秒（フロントの timestamp 基準）
        last_sample_at: Optional[float] = None
        print(f"接続: {connection.remote_address}", flush=True)
        await self._send_warmup(connection, timestamp=0)

        try:
            async for raw in connection:
                if connection is not self._active:
                    break  # 後から来た接続に計測を譲った
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if message.get("type") != "frame" or "image" not in message:
                    continue

                timestamp = int(message.get("timestamp") or 0)
                now = timestamp / 1000.0
                if next_sample_at is not None and now < next_sample_at:
                    continue
                if self._busy:
                    continue  # 前のフレームを処理中なら、次に来るフレームで拾う

                frame = decode_jpeg(message["image"])
                if frame is None:
                    continue

                if last_sample_at is not None and now - last_sample_at > self.interval * GAP_RESET_FACTOR:
                    self.predictor.hand_extractor.reset()
                next_sample_at = now + self.interval
                last_sample_at = now

                # ConvNeXt + MediaPipe はブロッキングなのでスレッドに逃がす
                self._busy = True
                try:
                    loc = await asyncio.to_thread(self.predictor.push, frame)
                finally:
                    self._busy = False

                if loc is None:
                    await self._send_warmup(connection, timestamp)
                else:
                    percent = to_percent(loc)
                    print(f"LoC = {loc:.1f}", flush=True)
                    await connection.send(json.dumps(
                        {"type": "loc", "timestamp": timestamp, "loc": percent}))
        finally:
            if connection is self._active:
                self._active = None
            print("切断", flush=True)

    async def _send_warmup(self, connection: ServerConnection, timestamp: int) -> None:
        remaining = self.predictor.warmup_remaining
        await connection.send(json.dumps({
            "type": "warmup",
            "timestamp": timestamp,
            "remaining": remaining,
            "total": self.predictor.seq_len,
            "seconds": round(remaining * self.interval),
        }))


def start_static_server(static_dir: Path, port: int) -> http.server.ThreadingHTTPServer:
    handler = functools.partial(_QuietHandler, directory=str(static_dir))
    httpd = http.server.ThreadingHTTPServer(("localhost", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - 親クラスのシグネチャ
        pass  # 静的ファイルのアクセスログで推論ログが埋もれないようにする

    def end_headers(self):
        # 開発中に JS を書き換えたとき古いものが使われないように
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


async def serve_forever(server: LoCServer, host: str, port: int) -> None:
    async with serve(server.handle, host, port, max_size=8 * 1024 * 1024):
        await asyncio.Future()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Onsta フロントと LoC 推論をつなぐローカルサーバー")
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--interval", type=float, default=5.0,
                        help="推論のサンプリング間隔（秒）。学習時の --rate に揃える")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--ws-port", type=int, default=8765)
    parser.add_argument("--http-port", type=int, default=8000)
    parser.add_argument("--static-dir", default=str(DEFAULT_STATIC_DIR))
    parser.add_argument("--no-static", action="store_true", help="フロントを配信しない")
    parser.add_argument("--device", default=None, help="cpu / cuda（既定は自動判定）")
    args = parser.parse_args(argv)

    print("モデルを読み込み中...", flush=True)
    with LoCPredictor(args.checkpoint, device=args.device) as predictor:
        server = LoCServer(predictor, interval=args.interval)
        httpd = None
        if not args.no_static:
            httpd = start_static_server(Path(args.static_dir), args.http_port)
        print(f"\n推論サーバー: ws://{args.host}:{args.ws_port}/loc"
              f"（{Path(args.checkpoint).name}, device={predictor.device}, "
              f"{args.interval:g}秒ごと, 最初の推定まで約{predictor.seq_len * args.interval:.0f}秒）")
        if httpd:
            print(f"フロント:     http://localhost:{args.http_port}/  ← ブラウザで開く")
        print("終了は Ctrl+C\n", flush=True)
        try:
            asyncio.run(serve_forever(server, args.host, args.ws_port))
        except KeyboardInterrupt:
            pass
        finally:
            if httpd:
                httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
