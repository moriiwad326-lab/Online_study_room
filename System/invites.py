"""招待申請（新規登録を許してほしいという申請）を SQLite で管理する。

申請は2通り:
- 本人の申請：招待されていない Google アカウントでログインしようとした人が、ログイン画面から申請する。
  申請に使うメールアドレスは Google が確認したものだけ（入力させると他人のアドレスで申請できてしまう）。
  ログインの戻りで「確認済みのメールアドレスの引換券」（`create_ticket`）を Cookie で渡し、申請時に照合する
- 紹介：登録済みのユーザーが、友だちのメールアドレスを申請する

管理者（users.is_admin）が Web で承認すると、そのメールアドレスの招待（invitations）が作られる。
あとは今までの招待と同じく、その Google アカウントの初回ログインで登録される。
申請が届くと管理者に、結果が出ると紹介した人に通知する（本人の申請はアカウントが無いので通知しない）。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auth import AuthError, normalize_email
from .db import connect
from .social import SCHEMA as SOCIAL_SCHEMA, notify

TICKET_TTL_SEC = 30 * 60
MAX_MESSAGE_LEN = 200
MAX_PENDING_PER_USER = 5
STATUSES = ("pending", "approved", "rejected")

SCHEMA = """
CREATE TABLE IF NOT EXISTS invite_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    message      TEXT    NOT NULL DEFAULT '',
    requester_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    created_at   TEXT    NOT NULL,
    decided_at   TEXT,
    decided_by   INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS invite_requests_pending
    ON invite_requests(email) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS applicant_tickets (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_message(message: Any) -> str:
    message = message or ""
    if not isinstance(message, str):
        raise AuthError(400, "メッセージの形式が正しくありません", "message")
    message = message.strip()
    if len(message) > MAX_MESSAGE_LEN:
        raise AuthError(400, f"メッセージは{MAX_MESSAGE_LEN}文字以内にしてください", "message")
    return message


class InviteRequestStore:
    def __init__(self, db_path: Path, clock=time.time):
        self.db_path = Path(db_path)
        self.clock = clock
        with closing(connect(self.db_path)) as conn, conn:
            conn.executescript(SOCIAL_SCHEMA + SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    # ---- 本人の申請（ログイン画面から） ----

    def create_ticket(self, email: str) -> str:
        """Google で確認できたメールアドレスの引換券を作る。Cookie で渡すトークンを返す。"""
        email = normalize_email(email)
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM applicant_tickets WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO applicant_tickets (token_hash, email, expires_at) VALUES (?, ?, ?)",
                (_hash_token(token), email, now + TICKET_TTL_SEC),
            )
        return token

    def ticket_status(self, token: Optional[str]) -> Dict[str, Any]:
        """引換券のメールアドレスと、そのアドレスの申請の状況。引換券が無効なら 404。"""
        with closing(self._connect()) as conn:
            email = self._ticket_email(conn, token)
            pending = conn.execute(
                "SELECT 1 FROM invite_requests WHERE email = ? AND status = 'pending'", (email,)
            ).fetchone() is not None
        return {"email": email, "pending": pending}

    def apply_with_ticket(self, token: Optional[str], message: Any) -> Dict[str, Any]:
        message = _normalize_message(message)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            email = self._ticket_email(conn, token)
            request = self._create(conn, email, message, None)
            conn.execute("DELETE FROM applicant_tickets WHERE token_hash = ?", (_hash_token(token),))
        return request

    def _ticket_email(self, conn: sqlite3.Connection, token: Optional[str]) -> str:
        row = token and conn.execute(
            "SELECT email FROM applicant_tickets WHERE token_hash = ? AND expires_at >= ?",
            (_hash_token(token), self.clock()),
        ).fetchone()
        if not row:
            raise AuthError(404, "申請の有効期限が切れました。もう一度 Google でログインしてから申請してください")
        return row["email"]

    # ---- 紹介（登録済みのユーザーから） ----

    def refer(self, requester_id: int, email: Any, message: Any) -> Dict[str, Any]:
        email = normalize_email(email)
        message = _normalize_message(message)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                "SELECT COUNT(*) FROM invite_requests WHERE requester_id = ? AND status = 'pending'",
                (requester_id,),
            ).fetchone()[0]
            if pending >= MAX_PENDING_PER_USER:
                raise AuthError(429, f"承認を待っている申請が{MAX_PENDING_PER_USER}件あります。"
                                     "結果が出てから申請してください")
            return self._create(conn, email, message, requester_id)

    def list_mine(self, requester_id: int) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM invite_requests WHERE requester_id = ? ORDER BY id DESC", (requester_id,)
            ).fetchall()
        return [self._json(row, with_requester=False) for row in rows]

    def _create(self, conn: sqlite3.Connection, email: str, message: str,
                requester_id: Optional[int]) -> Dict[str, Any]:
        """申請を1件作り、管理者に通知する。呼び出し側で BEGIN IMMEDIATE しておく。"""
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AuthError(409, "このメールアドレスはすでに登録されています", "email")
        if conn.execute("SELECT 1 FROM invitations WHERE email = ?", (email,)).fetchone():
            raise AuthError(409, "このメールアドレスはすでに招待されています。Google でログインしてください",
                            "email")
        if conn.execute(
            "SELECT 1 FROM invite_requests WHERE email = ? AND status = 'pending'", (email,)
        ).fetchone():
            raise AuthError(409, "このメールアドレスはすでに申請されています。承認をお待ちください", "email")

        request_id = conn.execute(
            "INSERT INTO invite_requests (email, kind, message, requester_id, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (email, "self" if requester_id is None else "referral", message, requester_id, _now()),
        ).lastrowid
        for admin in conn.execute("SELECT id FROM users WHERE is_admin = 1").fetchall():
            notify(conn, admin["id"], "invite_request", requester_id, {"requestId": request_id})
        row = conn.execute("SELECT * FROM invite_requests WHERE id = ?", (request_id,)).fetchone()
        return self._json(row, with_requester=False)

    # ---- 管理者 ----

    def list_requests(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status is not None and status not in STATUSES:
            raise AuthError(400, "状態の指定が正しくありません")
        sql = ("SELECT r.*, u.profile AS requester_profile FROM invite_requests r"
               " LEFT JOIN users u ON u.id = r.requester_id")
        params: List[Any] = []
        if status:
            sql += " WHERE r.status = ?"
            params.append(status)
        sql += " ORDER BY r.status = 'pending' DESC, r.id DESC LIMIT 200"
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._json(row, with_requester=True) for row in rows]

    def approve(self, admin_id: int, request_id: int) -> Dict[str, Any]:
        return self._decide(admin_id, request_id, "approved")

    def reject(self, admin_id: int, request_id: int) -> Dict[str, Any]:
        return self._decide(admin_id, request_id, "rejected")

    def _decide(self, admin_id: int, request_id: int, status: str) -> Dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM invite_requests WHERE id = ?", (request_id,)).fetchone()
            if not row:
                raise AuthError(404, "申請が見つかりません")
            if row["status"] != "pending":
                raise AuthError(409, "この申請はすでに処理されています")
            if status == "approved":
                if conn.execute("SELECT 1 FROM users WHERE email = ?", (row["email"],)).fetchone():
                    raise AuthError(409, "このメールアドレスはすでに登録されています。却下してください")
                conn.execute(
                    "INSERT INTO invitations (email, user_id, created_at) VALUES (?, NULL, ?)"
                    " ON CONFLICT (email) DO NOTHING",
                    (row["email"], _now()),
                )
            conn.execute(
                "UPDATE invite_requests SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
                (status, _now(), admin_id, request_id),
            )
            if row["requester_id"] is not None:
                notify(conn, row["requester_id"],
                       "invite_approved" if status == "approved" else "invite_rejected",
                       None, {"email": row["email"]})
            # 同じ申請の通知を受けた他の管理者にも、処理済みとわかるよう既読にしておく
            conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE type = 'invite_request'"
                " AND json_extract(data, '$.requestId') = ?", (request_id,))
            updated = conn.execute(
                "SELECT r.*, u.profile AS requester_profile FROM invite_requests r"
                " LEFT JOIN users u ON u.id = r.requester_id WHERE r.id = ?", (request_id,)
            ).fetchone()
        return self._json(updated, with_requester=True)

    @staticmethod
    def _json(row: sqlite3.Row, with_requester: bool) -> Dict[str, Any]:
        result = {
            "id": row["id"],
            "email": row["email"],
            "message": row["message"],
            "kind": row["kind"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "decidedAt": row["decided_at"],
        }
        if with_requester:
            requester = None
            if row["requester_id"] is not None and row["requester_profile"]:
                requester = {"id": row["requester_id"],
                             "nickname": json.loads(row["requester_profile"]).get("nickname", "")}
            result["requester"] = requester
        return result
