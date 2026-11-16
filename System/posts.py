"""投稿（学習記録）へのいいね・返信を SQLite で管理する。

- 学習記録はログインしている誰からでも見えるものとして扱っている（records.py）ので、
  いいね・返信も、初回設定を済ませた誰でもできる
- いいねされると投稿者に通知が届く。いいね → 取り消し → いいねを繰り返しても、未読の通知は1つにまとめる
- 返信は返信にも付けられる（スレッド）。投稿者と、返信先の返信を書いた人に通知が届く（自分には送らない）
- 返信は書いた本人と投稿者が消せる。下に返信が付いていることがあるので、行は残して本文だけ消す
  （一覧では「削除された返信」と出し、下に何も残っていなければ一覧から外す）
- 投稿を消すと、いいね・返信も一緒に消える（外部キーの ON DELETE CASCADE）

HTTP の扱いは `server.py`、ここは純粋なデータ操作だけを持つ。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .auth import AuthError
from .db import connect
from .social import notify, public_profile

MAX_REPLY_LEN = 500
# 通知に載せる返信の冒頭
REPLY_SNIPPET_LEN = 40

SCHEMA = """
CREATE TABLE IF NOT EXISTS post_likes (
    record_id  INTEGER NOT NULL REFERENCES study_records(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (record_id, user_id)
);
CREATE INDEX IF NOT EXISTS post_likes_user ON post_likes(user_id);
CREATE TABLE IF NOT EXISTS post_replies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id  INTEGER NOT NULL REFERENCES study_records(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id  INTEGER REFERENCES post_replies(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS post_replies_record ON post_replies(record_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def attach_reactions(conn: sqlite3.Connection, viewer_id: int, records: List[Dict[str, Any]]) -> None:
    """記録の JSON に likeCount / liked / replyCount を付ける（まとめて数える）。"""
    if not records:
        return
    ids = [r["id"] for r in records]
    marks = ",".join("?" * len(ids))
    likes = dict(conn.execute(
        f"SELECT record_id, COUNT(*) FROM post_likes WHERE record_id IN ({marks}) GROUP BY record_id", ids
    ).fetchall())
    liked = {row[0] for row in conn.execute(
        f"SELECT record_id FROM post_likes WHERE user_id = ? AND record_id IN ({marks})", [viewer_id, *ids]
    ).fetchall()}
    replies = dict(conn.execute(
        f"SELECT record_id, COUNT(*) FROM post_replies WHERE is_deleted = 0 AND record_id IN ({marks})"
        " GROUP BY record_id", ids
    ).fetchall())
    for record in records:
        record["likeCount"] = likes.get(record["id"], 0)
        record["liked"] = record["id"] in liked
        record["replyCount"] = replies.get(record["id"], 0)


def delete_record_notifications(conn: sqlite3.Connection, record_ids: Iterable[int]) -> None:
    """消した投稿へのいいね・返信の通知を消す（開いても投稿が無いので）。"""
    ids = list(record_ids)
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM notifications WHERE type IN ('like', 'reply')"
        f" AND json_extract(data, '$.recordId') IN ({marks})", ids)


class PostStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        # study_records は records.py が作る。ここでは参照する表だけを作っておく
        from .records import RecordStore
        RecordStore(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    @staticmethod
    def _record_owner(conn: sqlite3.Connection, record_id: int) -> int:
        row = conn.execute("SELECT user_id FROM study_records WHERE id = ?", (record_id,)).fetchone()
        if not row:
            raise AuthError(404, "投稿が見つかりません")
        return row["user_id"]

    # ---- いいね ----

    def like(self, user_id: int, record_id: int) -> Dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            owner = self._record_owner(conn, record_id)
            cur = conn.execute(
                "INSERT OR IGNORE INTO post_likes (record_id, user_id, created_at) VALUES (?, ?, ?)",
                (record_id, user_id, _now()),
            )
            if cur.rowcount and owner != user_id and not conn.execute(
                "SELECT 1 FROM notifications WHERE user_id = ? AND type = 'like' AND actor_id = ?"
                " AND is_read = 0 AND json_extract(data, '$.recordId') = ?", (owner, user_id, record_id)
            ).fetchone():
                notify(conn, owner, "like", user_id, {"recordId": record_id})
            return self._like_state(conn, user_id, record_id)

    def unlike(self, user_id: int, record_id: int) -> Dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            self._record_owner(conn, record_id)
            conn.execute("DELETE FROM post_likes WHERE record_id = ? AND user_id = ?", (record_id, user_id))
            return self._like_state(conn, user_id, record_id)

    @staticmethod
    def _like_state(conn: sqlite3.Connection, user_id: int, record_id: int) -> Dict[str, Any]:
        count = conn.execute("SELECT COUNT(*) FROM post_likes WHERE record_id = ?", (record_id,)).fetchone()[0]
        liked = conn.execute(
            "SELECT 1 FROM post_likes WHERE record_id = ? AND user_id = ?", (record_id, user_id)
        ).fetchone() is not None
        return {"likeCount": count, "liked": liked}

    # ---- 返信 ----

    def list_replies(self, viewer_id: int, record_id: int) -> List[Dict[str, Any]]:
        """投稿への返信（古い順）。parentId で木にする。下に何も残っていない削除済みの返信は外す。"""
        with closing(self._connect()) as conn:
            owner = self._record_owner(conn, record_id)
            rows = conn.execute(
                "SELECT p.id, p.parent_id, p.user_id, p.body, p.is_deleted, p.created_at, u.profile"
                " FROM post_replies p JOIN users u ON u.id = p.user_id"
                " WHERE p.record_id = ? ORDER BY p.id",
                (record_id,),
            ).fetchall()

        # 子が残っているかを葉の側から決める（id は親より子が大きいので、逆順に見ればよい）
        has_child = set()
        keep = []
        for row in reversed(rows):
            if not row["is_deleted"] or row["id"] in has_child:
                keep.append(row)
                if row["parent_id"] is not None:
                    has_child.add(row["parent_id"])
        keep.reverse()
        return [self._reply_json(row, viewer_id, owner) for row in keep]

    def add_reply(self, user_id: int, record_id: int, body: Any, parent_id: Any = None) -> Dict[str, Any]:
        if not isinstance(body, str) or not body.strip():
            raise AuthError(400, "返信を入力してください", "body")
        body = body.strip()
        if len(body) > MAX_REPLY_LEN:
            raise AuthError(400, f"返信は{MAX_REPLY_LEN}文字以内にしてください", "body")
        if parent_id is not None and (isinstance(parent_id, bool) or not isinstance(parent_id, int)):
            raise AuthError(400, "返信先が正しくありません", "parentId")

        with closing(self._connect()) as conn, conn:
            owner = self._record_owner(conn, record_id)
            parent_author: Optional[int] = None
            if parent_id is not None:
                parent = conn.execute(
                    "SELECT user_id, is_deleted FROM post_replies WHERE id = ? AND record_id = ?",
                    (parent_id, record_id),
                ).fetchone()
                if not parent or parent["is_deleted"]:
                    raise AuthError(404, "返信先が見つかりません", "parentId")
                parent_author = parent["user_id"]

            reply_id = conn.execute(
                "INSERT INTO post_replies (record_id, user_id, parent_id, body, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (record_id, user_id, parent_id, body, _now()),
            ).lastrowid

            snippet = body[:REPLY_SNIPPET_LEN] + ("…" if len(body) > REPLY_SNIPPET_LEN else "")
            if owner != user_id:
                notify(conn, owner, "reply", user_id,
                       {"recordId": record_id, "replyId": reply_id, "toReply": False, "snippet": snippet})
            if parent_author is not None and parent_author not in (user_id, owner):
                notify(conn, parent_author, "reply", user_id,
                       {"recordId": record_id, "replyId": reply_id, "toReply": True, "snippet": snippet})

            row = conn.execute(
                "SELECT p.id, p.parent_id, p.user_id, p.body, p.is_deleted, p.created_at, u.profile"
                " FROM post_replies p JOIN users u ON u.id = p.user_id WHERE p.id = ?", (reply_id,)
            ).fetchone()
        return self._reply_json(row, user_id, owner)

    def delete_reply(self, user_id: int, reply_id: int) -> None:
        """書いた本人か、投稿者だけが消せる。"""
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT p.user_id, r.user_id AS owner_id FROM post_replies p"
                " JOIN study_records r ON r.id = p.record_id WHERE p.id = ? AND p.is_deleted = 0",
                (reply_id,),
            ).fetchone()
            if not row or user_id not in (row["user_id"], row["owner_id"]):
                raise AuthError(404, "返信が見つかりません")
            conn.execute("UPDATE post_replies SET body = '', is_deleted = 1 WHERE id = ?", (reply_id,))
            conn.execute(
                "DELETE FROM notifications WHERE type = 'reply' AND json_extract(data, '$.replyId') = ?",
                (reply_id,))

    @staticmethod
    def _reply_json(row: sqlite3.Row, viewer_id: int, owner_id: int) -> Dict[str, Any]:
        deleted = bool(row["is_deleted"])
        return {
            "id": row["id"],
            "parentId": row["parent_id"],
            "user": None if deleted else public_profile({"id": row["user_id"], "profile": row["profile"]}),
            "body": "" if deleted else row["body"],
            "isDeleted": deleted,
            "isMine": row["user_id"] == viewer_id,
            # 消せるのは書いた本人と投稿者
            "canDelete": not deleted and viewer_id in (row["user_id"], owner_id),
            "createdAt": row["created_at"],
        }
