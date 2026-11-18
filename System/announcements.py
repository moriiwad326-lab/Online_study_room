"""Onsta の版（バージョン）と、更新したときの全ユーザーへのお知らせ。

サーバーの起動時（main.py）に `announce_release` を呼ぶ。その版のお知らせをまだ送っていなければ、
その時点で登録済みの全ユーザーに「announcement」の通知を1件ずつ入れる。送った版は announcements に
残すので、再起動しても二重には送らない。

フロント側の版と更新内容は static/js/version.js に持っている。版を上げるときは両方を変える。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .db import connect
from .social import SCHEMA as SOCIAL_SCHEMA, notify

APP_VERSION = "1.3"
RELEASE_TITLE = f"Onsta ver{APP_VERSION} に更新しました"

SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    version    TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
"""


def announce_release(db_path: Path, version: str = APP_VERSION, title: str = RELEASE_TITLE) -> int:
    """version のお知らせを全ユーザーに送る。送った人数を返す（送り済みなら 0）。"""
    with closing(connect(Path(db_path))) as conn, conn:
        conn.executescript(SOCIAL_SCHEMA + SCHEMA)
        # 2つのプロセスが同時に起動しても二重に送らないよう、書き込みロックを取ってから確かめる
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT INTO announcements (version, created_at) VALUES (?, ?)",
                         (version, datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError:
            return 0
        # ログインできるユーザー（Google アカウントと結び付いている人）全員に送る
        user_ids = [row[0] for row in conn.execute(
            "SELECT id FROM users WHERE google_sub IS NOT NULL ORDER BY id").fetchall()]
        for user_id in user_ids:
            notify(conn, user_id, "announcement", None, {"version": version, "title": title})
    return len(user_ids)
