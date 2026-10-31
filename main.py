"""Onsta アプリサーバーの起動スクリプト。

フロント（static/）の配信と API（ログイン・教材）を http://localhost:8000/ で立てる。
LoC 推論も使うときは、別のターミナルで `python LoC/server.py --no-static` を起動する。

使い方:
    python main.py
    python main.py --port 8080 --db System/data/onsta.db
    python main.py --google-books-key <APIキー>   # バーコード検索で Google Books も使う
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List, Optional

from System.auth import AuthStore
from System.books import BookLookup
from System.materials import MaterialStore
from System.server import make_server

ROOT = Path(__file__).resolve().parent


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Onsta のアプリサーバー（フロント配信＋API）")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(ROOT / "System" / "data" / "onsta.db"),
                        help="ユーザー情報を保存する SQLite ファイル")
    parser.add_argument("--static-dir", default=str(ROOT / "static"))
    parser.add_argument("--secure-cookie", action="store_true",
                        help="HTTPS で公開するときに Cookie に Secure を付ける")
    parser.add_argument("--google-books-key", default=os.environ.get("GOOGLE_BOOKS_API_KEY"),
                        help="Google Books API のキー（省略時は環境変数 GOOGLE_BOOKS_API_KEY）。"
                             "無くても openBD で検索できる")
    args = parser.parse_args(argv)

    store = AuthStore(Path(args.db))
    materials = MaterialStore(Path(args.db))
    books = BookLookup(google_api_key=args.google_books_key)
    httpd = make_server(args.host, args.port, Path(args.static_dir), store, materials, books,
                        args.secure_cookie)
    print(f"Onsta: http://{args.host}:{args.port}/  ← ブラウザで開く（DB: {args.db}）")
    print("LoC 推論も使うときは別ターミナルで: python LoC/server.py --no-static")
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
