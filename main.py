"""Onsta アプリサーバーの起動スクリプト。

フロント（static/）の配信と API（ログイン・教材）を http://localhost:8000/ で立てる。
LoC 推論も使うときは、別のターミナルで `python LoC/server.py --no-static` を起動する。

ログインは Google アカウントのみ。先に Google Cloud で OAuth クライアントを作り、
環境変数 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET を設定しておく（手順は static/README.md）。
新規登録は招待制で、`python -m System.admin invite <メールアドレス>` で招待する。

使い方:
    python main.py
    python main.py --port 8080 --db System/data/onsta.db
    python main.py --google-books-key <APIキー>   # バーコード検索で Google Books も使う

スマホ・タブレットから使う（ngrok で HTTPS 公開。カメラは HTTPS でないと使えない）:
    python main.py --public-url https://xxxx.ngrok-free.dev   # Cookie に Secure も付く
    ngrok http 8000 --url xxxx.ngrok-free.dev
公開 URL のリダイレクト URI は Google Cloud にも登録しておく。
LoC 推論は /loc（WebSocket）経由で推論サーバー（--loc-upstream）へ中継されるので、
公開するのは 8000 番だけでよい。推論サーバーは同時に1台ぶんしか計測できない。
"""

from __future__ import annotations

import argparse
import os
import urllib.parse
from pathlib import Path
from typing import List, Optional

from System.auth import AuthStore
from System.books import BookLookup
from System.db import DEFAULT_DB_PATH
from System.google_oauth import GoogleOAuth
from System.materials import MaterialStore
from System.server import make_server

ROOT = Path(__file__).resolve().parent


def check_public_url(url: str) -> Optional[str]:
    """公開 URL として使えないなら理由を返す。"""
    try:
        parts = urllib.parse.urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return "URL の形式が正しくありません"
    if parts.scheme not in ("http", "https") or not hostname or parts.path.strip("/") or parts.query:
        return "https://example.com の形（パスなし）で指定してください"
    if parts.scheme == "http" and hostname not in ("localhost", "127.0.0.1"):
        return "localhost 以外は https にしてください"
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Onsta のアプリサーバー（フロント配信＋API）")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH),
                        help="ユーザー情報を保存する SQLite ファイル")
    parser.add_argument("--static-dir", default=str(ROOT / "static"))
    parser.add_argument("--public-url", default=os.environ.get("ONSTA_PUBLIC_URL"),
                        help="ブラウザから開く URL（省略時は環境変数 ONSTA_PUBLIC_URL、なければ "
                             "http://localhost:<port>）。https なら Cookie に Secure を付ける")
    parser.add_argument("--google-books-key", default=os.environ.get("GOOGLE_BOOKS_API_KEY"),
                        help="Google Books API のキー（省略時は環境変数 GOOGLE_BOOKS_API_KEY）。"
                             "無くても openBD で検索できる")
    parser.add_argument("--loc-upstream", default="localhost:8765",
                        help="/loc の WebSocket を中継する先の LoC 推論サーバー（host:port）。"
                             "none で中継しない")
    args = parser.parse_args(argv)

    loc_upstream = None
    if args.loc_upstream.lower() != "none":
        loc_host, _, loc_port = args.loc_upstream.rpartition(":")
        if not loc_host or not loc_port.isdigit():
            parser.error("--loc-upstream は host:port の形で指定してください")
        loc_upstream = (loc_host, int(loc_port))

    # シークレットはコマンドライン引数で受け取らない（履歴やプロセス一覧に残るため）
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        parser.error("環境変数 GOOGLE_CLIENT_ID と GOOGLE_CLIENT_SECRET を設定してください"
                     "（手順は static/README.md）")

    public_url = (args.public_url or f"http://localhost:{args.port}").rstrip("/")
    problem = check_public_url(public_url)
    if problem:
        parser.error(f"--public-url: {problem}")
    google = GoogleOAuth(client_id, client_secret, public_url)

    store = AuthStore(Path(args.db))
    materials = MaterialStore(Path(args.db))
    books = BookLookup(google_api_key=args.google_books_key)
    httpd = make_server(args.host, args.port, Path(args.static_dir), store, materials, books, google,
                        secure_cookie=public_url.startswith("https://"), loc_upstream=loc_upstream)
    print(f"Onsta: {public_url}/  ← ブラウザで開く（DB: {args.db}）")
    print(f"Google Cloud に登録するリダイレクト URI: {google.redirect_uri}")
    print("新規登録は招待制: python -m System.admin invite <メールアドレス>")
    print("LoC 推論も使うときは別ターミナルで: python LoC/server.py --no-static")
    print("スマホ等から使うときは --public-url https://<ngrok のドメイン> を付けて起動し、別ターミナルで: "
          f"ngrok http {args.port} --url <ngrok のドメイン>")
    print("終了は Ctrl+C", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
