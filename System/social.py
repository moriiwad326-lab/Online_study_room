"""ユーザー同士のつながり（フォロー・通知・メッセージ）を SQLite で管理する。

- フォローは承認なしで、すぐに成立する（片方向）。フォローされると相手に通知が届く
- メッセージ（1対1）は、相互フォローの相手とだけやりとりできる。相互でなくなったら、
  過去のやりとりは読めるが新しくは送れない
- 通知は「フォローされた」「招待申請が届いた（管理者あて）」「招待申請の結果」の3種類。
  メッセージは通知に入れず、未読数を別に数える（ヘッダーのバッジが別なので）
- 他の人に見せるプロフィールは、ニックネーム・アイコン色・自己紹介・タグ・学年・志望・目標だけ。
  メールアドレス・性別・生まれた年・都道府県は本人以外に出さない

HTTP の扱いは `server.py`、ここは純粋なデータ操作だけを持つ。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auth import AuthError
from .db import connect

MAX_MESSAGE_LEN = 1000
MAX_SEARCH_LEN = 40
SEARCH_LIMIT = 30
NOTIFICATION_LIMIT = 50
MESSAGE_PAGE = 200

# 他の人に見せてよいプロフィールの項目
PUBLIC_PROFILE_KEYS = ("nickname", "avatarColor", "bio", "tags", "grade", "targetSchool", "examName")

NOTIFICATION_TYPES = ("follow", "invite_request", "invite_approved", "invite_rejected")

SCHEMA = """
CREATE TABLE IF NOT EXISTS follows (
    follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    followee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL,
    PRIMARY KEY (follower_id, followee_id),
    CHECK (follower_id <> followee_id)
);
CREATE INDEX IF NOT EXISTS follows_followee ON follows(followee_id);
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type       TEXT    NOT NULL,
    actor_id   INTEGER REFERENCES users(id) ON DELETE CASCADE,
    data       TEXT    NOT NULL DEFAULT '{}',
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS notifications_user ON notifications(user_id, id);
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body         TEXT    NOT NULL,
    is_read      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_pair ON messages(sender_id, recipient_id, id);
CREATE INDEX IF NOT EXISTS messages_unread ON messages(recipient_id, is_read);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_profile(row: sqlite3.Row) -> Dict[str, Any]:
    """users の行（id, profile）から、他の人に見せてよい形を作る。"""
    profile = json.loads(row["profile"])
    user = {"id": row["id"]}
    for key in PUBLIC_PROFILE_KEYS:
        user[key] = profile.get(key, [] if key == "tags" else "")
    return user


def notify(conn: sqlite3.Connection, user_id: int, type_: str,
           actor_id: Optional[int] = None, data: Optional[Dict[str, Any]] = None) -> None:
    """通知を1件入れる。呼び出し側のトランザクションの中で使う。"""
    assert type_ in NOTIFICATION_TYPES, type_
    conn.execute(
        "INSERT INTO notifications (user_id, type, actor_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, type_, actor_id, json.dumps(data or {}, ensure_ascii=False), _now()),
    )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SocialStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        with closing(connect(self.db_path)) as conn, conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    # ---- ユーザー ----

    @staticmethod
    def _require_visible(conn: sqlite3.Connection, user_id: Any) -> sqlite3.Row:
        """初回設定まで済んでいるユーザーだけを、他の人から見えるものとして扱う。"""
        row = None
        if isinstance(user_id, int) and not isinstance(user_id, bool):
            row = conn.execute(
                "SELECT id, profile FROM users WHERE id = ? AND profile_completed = 1", (user_id,)
            ).fetchone()
        if not row:
            raise AuthError(404, "ユーザーが見つかりません")
        return row

    @staticmethod
    def _relation(conn: sqlite3.Connection, viewer_id: int, other_id: int) -> Dict[str, bool]:
        following = conn.execute(
            "SELECT 1 FROM follows WHERE follower_id = ? AND followee_id = ?", (viewer_id, other_id)
        ).fetchone() is not None
        followed_by = conn.execute(
            "SELECT 1 FROM follows WHERE follower_id = ? AND followee_id = ?", (other_id, viewer_id)
        ).fetchone() is not None
        return {
            "isMe": viewer_id == other_id,
            "isFollowing": following,
            "followsMe": followed_by,
            "canMessage": following and followed_by,
        }

    def get_user(self, viewer_id: int, user_id: int) -> Dict[str, Any]:
        """他の人のプロフィール（公開する項目だけ）と、フォロー数・自分との関係。"""
        with closing(self._connect()) as conn:
            row = self._require_visible(conn, user_id)
            user = public_profile(row)
            user["followerCount"] = conn.execute(
                "SELECT COUNT(*) FROM follows WHERE followee_id = ?", (user_id,)).fetchone()[0]
            user["followingCount"] = conn.execute(
                "SELECT COUNT(*) FROM follows WHERE follower_id = ?", (user_id,)).fetchone()[0]
            user.update(self._relation(conn, viewer_id, user_id))
        return user

    def search_users(self, viewer_id: int, query: Any) -> List[Dict[str, Any]]:
        """ニックネームの部分一致で探す（自分は除く）。"""
        if not isinstance(query, str) or not query.strip():
            return []
        query = query.strip()[:MAX_SEARCH_LEN]
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, profile FROM users"
                " WHERE profile_completed = 1 AND id <> ?"
                "   AND json_extract(profile, '$.nickname') LIKE ? ESCAPE '\\'"
                " ORDER BY id LIMIT ?",
                (viewer_id, f"%{_escape_like(query)}%", SEARCH_LIMIT),
            ).fetchall()
            return [self._with_relation(conn, viewer_id, row) for row in rows]

    def _with_relation(self, conn: sqlite3.Connection, viewer_id: int, row: sqlite3.Row) -> Dict[str, Any]:
        user = public_profile(row)
        user.update(self._relation(conn, viewer_id, row["id"]))
        return user

    # ---- フォロー ----

    def follow(self, follower_id: int, followee_id: int) -> Dict[str, Any]:
        if follower_id == followee_id:
            raise AuthError(400, "自分はフォローできません")
        with closing(self._connect()) as conn, conn:
            self._require_visible(conn, followee_id)
            cur = conn.execute(
                "INSERT OR IGNORE INTO follows (follower_id, followee_id, created_at) VALUES (?, ?, ?)",
                (follower_id, followee_id, _now()),
            )
            # フォロー → 解除 → フォローを繰り返しても、未読の通知は1つにまとめる
            if cur.rowcount and not conn.execute(
                "SELECT 1 FROM notifications WHERE user_id = ? AND type = 'follow' AND actor_id = ?"
                " AND is_read = 0", (followee_id, follower_id)
            ).fetchone():
                notify(conn, followee_id, "follow", follower_id)
        return self.get_user(follower_id, followee_id)

    def unfollow(self, follower_id: int, followee_id: int) -> Dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            self._require_visible(conn, followee_id)
            conn.execute(
                "DELETE FROM follows WHERE follower_id = ? AND followee_id = ?", (follower_id, followee_id))
        return self.get_user(follower_id, followee_id)

    def is_following(self, follower_id: int, followee_id: int) -> bool:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT 1 FROM follows WHERE follower_id = ? AND followee_id = ?", (follower_id, followee_id)
            ).fetchone() is not None

    def list_followers(self, viewer_id: int, user_id: int) -> List[Dict[str, Any]]:
        return self._list_follow(viewer_id, user_id, "follower_id", "followee_id")

    def list_following(self, viewer_id: int, user_id: int) -> List[Dict[str, Any]]:
        return self._list_follow(viewer_id, user_id, "followee_id", "follower_id")

    def _list_follow(self, viewer_id: int, user_id: int, pick: str, match: str) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            self._require_visible(conn, user_id)
            rows = conn.execute(
                f"SELECT u.id, u.profile FROM follows f JOIN users u ON u.id = f.{pick}"
                f" WHERE f.{match} = ? AND u.profile_completed = 1 ORDER BY f.created_at DESC",
                (user_id,),
            ).fetchall()
            return [self._with_relation(conn, viewer_id, row) for row in rows]

    # ---- 通知 ----

    def list_notifications(self, user_id: int) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT n.id, n.type, n.data, n.is_read, n.created_at, u.id AS actor_id, u.profile"
                " FROM notifications n LEFT JOIN users u ON u.id = n.actor_id"
                " WHERE n.user_id = ? ORDER BY n.id DESC LIMIT ?",
                (user_id, NOTIFICATION_LIMIT),
            ).fetchall()
        return [{
            "id": row["id"],
            "type": row["type"],
            "actor": public_profile({"id": row["actor_id"], "profile": row["profile"]})
                     if row["actor_id"] is not None else None,
            "data": json.loads(row["data"]),
            "isRead": bool(row["is_read"]),
            "createdAt": row["created_at"],
        } for row in rows]

    def mark_notifications_read(self, user_id: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0", (user_id,))

    def badges(self, user_id: int) -> Dict[str, int]:
        """ヘッダーに出す未読の数。"""
        with closing(self._connect()) as conn:
            notifications = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0", (user_id,)
            ).fetchone()[0]
            messages = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE recipient_id = ? AND is_read = 0", (user_id,)
            ).fetchone()[0]
        return {"notifications": notifications, "messages": messages}

    # ---- メッセージ ----

    def list_conversations(self, user_id: int) -> List[Dict[str, Any]]:
        """やりとりのある相手ごとに、最後のメッセージと未読数。新しい順。"""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT partner_id, MAX(id) AS last_id,
                       SUM(CASE WHEN recipient_id = :me AND is_read = 0 THEN 1 ELSE 0 END) AS unread
                FROM (
                    SELECT id, recipient_id, is_read,
                           CASE WHEN sender_id = :me THEN recipient_id ELSE sender_id END AS partner_id
                    FROM messages WHERE sender_id = :me OR recipient_id = :me
                )
                GROUP BY partner_id ORDER BY last_id DESC
                """,
                {"me": user_id},
            ).fetchall()
            result = []
            for row in rows:
                partner = conn.execute(
                    "SELECT id, profile FROM users WHERE id = ?", (row["partner_id"],)).fetchone()
                last = conn.execute(
                    "SELECT id, sender_id, body, created_at FROM messages WHERE id = ?", (row["last_id"],)
                ).fetchone()
                result.append({
                    "partner": self._with_relation(conn, user_id, partner),
                    "lastMessage": self._message_json(last),
                    "unread": row["unread"],
                })
        return result

    def get_thread(self, user_id: int, partner_id: int, after_id: int = 0) -> Dict[str, Any]:
        """相手とのメッセージ（after_id より新しいもの）。相手からの未読は既読にする。"""
        with closing(self._connect()) as conn, conn:
            partner_row = self._require_visible(conn, partner_id)
            rows = conn.execute(
                "SELECT id, sender_id, body, created_at FROM ("
                "  SELECT id, sender_id, body, created_at FROM messages"
                "  WHERE ((sender_id = :me AND recipient_id = :p) OR (sender_id = :p AND recipient_id = :me))"
                "    AND id > :after ORDER BY id DESC LIMIT :limit"
                ") ORDER BY id",
                {"me": user_id, "p": partner_id, "after": after_id, "limit": MESSAGE_PAGE},
            ).fetchall()
            conn.execute(
                "UPDATE messages SET is_read = 1 WHERE sender_id = ? AND recipient_id = ? AND is_read = 0",
                (partner_id, user_id),
            )
            partner = self._with_relation(conn, user_id, partner_row)
        return {"partner": partner, "messages": [self._message_json(row) for row in rows]}

    def send_message(self, sender_id: int, recipient_id: int, body: Any) -> Dict[str, Any]:
        if not isinstance(body, str) or not body.strip():
            raise AuthError(400, "メッセージを入力してください", "body")
        body = body.strip()
        if len(body) > MAX_MESSAGE_LEN:
            raise AuthError(400, f"メッセージは{MAX_MESSAGE_LEN}文字以内にしてください", "body")
        with closing(self._connect()) as conn, conn:
            self._require_visible(conn, recipient_id)
            if not self._relation(conn, sender_id, recipient_id)["canMessage"]:
                raise AuthError(403, "メッセージは相互フォローの相手にだけ送れます")
            cur = conn.execute(
                "INSERT INTO messages (sender_id, recipient_id, body, created_at) VALUES (?, ?, ?, ?)",
                (sender_id, recipient_id, body, _now()),
            )
            row = conn.execute(
                "SELECT id, sender_id, body, created_at FROM messages WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return self._message_json(row)

    @staticmethod
    def _message_json(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "senderId": row["sender_id"],
            "body": row["body"],
            "createdAt": row["created_at"],
        }
