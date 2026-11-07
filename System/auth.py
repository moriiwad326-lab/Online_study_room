"""Onsta のアカウントとログインセッションを SQLite で管理する。

- 本人確認は Google に任せ（`google_oauth.py`）、ここには Google アカウントの ID（sub）だけを持つ。
  パスワードは一切扱わない
- 新規登録は招待制。invitations にあるメールアドレスの Google アカウントだけが登録でき、
  招待は登録に使うと消える。招待の操作は `admin.py`（サーバー上の CLI）からだけ行う
- セッションはランダムなトークンを Cookie で渡し、DB には SHA-256 したものだけを保存する
  （DB が漏れてもそのままセッションに使えないように）
- プロフィール（ニックネームなど）は JSON で保存し、受け付ける項目と長さはここで検証する

パスワード認証だった頃の DB は、起動時に移行してパスワードハッシュと全セッションを消す。
旧アカウントのログインIDはメール確認をしていないので、同じ文字列のメールアドレスで
Google ログインされても自動では結び付けない（他人が先に作ったアカウントに入ってしまうため）。
管理者が `admin.py invite <メール> --user <ID>` で明示的に結び付ける。

HTTP の扱いは `server.py`、ここは純粋なデータ操作だけを持つ。
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .db import connect

SESSION_TTL_SEC = 30 * 24 * 3600

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LEN = 254

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# プロフィールの文字列項目と最大文字数
PROFILE_TEXT_LIMITS = {
    "nickname": 20,
    "bio": 200,
    "grade": 30,
    "targetSchool": 50,
    "examName": 50,
    "gender": 20,
    "prefecture": 10,
}
MAX_TAGS = 10
MAX_TAG_LEN = 20

# google_sub が NULL なのは、まだ Google アカウントを結び付けていない旧アカウント（ログインできない）
USERS_COLUMNS = """(
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    google_sub        TEXT    UNIQUE,
    email             TEXT,
    legacy_login_id   TEXT,
    profile           TEXT    NOT NULL DEFAULT '{}',
    profile_completed INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL
)"""

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users {USERS_COLUMNS};
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL    NOT NULL,
    expires_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS invitations (
    email      TEXT PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
"""


class AuthError(Exception):
    """画面にそのまま出せるメッセージと HTTP ステータスを持つエラー。"""

    def __init__(self, status: int, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.field = field


class NotInvitedError(AuthError):
    """未登録で、招待もされていない Google アカウント。"""

    def __init__(self):
        super().__init__(403, "このアカウントは登録を許可されていません")


def _hash_token(token: str) -> str:
    # token は Cookie から来るので、ASCII とは限らない
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ============ 入力の検証 ============

def normalize_email(email: Any) -> str:
    if not isinstance(email, str):
        raise AuthError(400, "メールアドレスの形式が正しくありません", "email")
    email = email.strip().lower()
    if len(email) > MAX_EMAIL_LEN or not EMAIL_RE.match(email):
        raise AuthError(400, "メールアドレスの形式が正しくありません", "email")
    return email


def normalize_profile(data: Any) -> Dict[str, Any]:
    """画面から来たプロフィールを検証し、保存する形にそろえる。

    ニックネーム以外は任意。空欄は "" / [] / None にそろえる。知らない項目は捨てる。
    """
    if not isinstance(data, dict):
        raise AuthError(400, "プロフィールの形式が正しくありません")

    profile: Dict[str, Any] = {}
    for key, limit in PROFILE_TEXT_LIMITS.items():
        value = data.get(key) or ""
        if not isinstance(value, str):
            raise AuthError(400, "入力の形式が正しくありません", key)
        value = value.strip()
        if len(value) > limit:
            raise AuthError(400, f"{limit}文字以内で入力してください", key)
        profile[key] = value
    if not profile["nickname"]:
        raise AuthError(400, "ニックネームを入力してください", "nickname")

    color = data.get("avatarColor") or ""
    if color and (not isinstance(color, str) or not HEX_COLOR_RE.match(color)):
        raise AuthError(400, "アイコンの色が正しくありません", "avatarColor")
    profile["avatarColor"] = color.lower()

    tags = data.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise AuthError(400, "タグの形式が正しくありません", "tags")
    cleaned = []
    for tag in tags:
        tag = tag.strip().lstrip("#")
        if tag and tag not in cleaned:
            cleaned.append(tag)
    if len(cleaned) > MAX_TAGS:
        raise AuthError(400, f"タグは{MAX_TAGS}個までです", "tags")
    if any(len(t) > MAX_TAG_LEN for t in cleaned):
        raise AuthError(400, f"タグは1つ{MAX_TAG_LEN}文字以内にしてください", "tags")
    profile["tags"] = cleaned

    exam_date = data.get("examDate") or ""
    if exam_date:
        try:
            if not isinstance(exam_date, str) or not DATE_RE.match(exam_date):
                raise ValueError
            date.fromisoformat(exam_date)
        except ValueError:
            raise AuthError(400, "試験日の形式が正しくありません", "examDate") from None
    profile["examDate"] = exam_date

    profile["birthYear"] = _optional_int(
        data.get("birthYear"), 1900, datetime.now().year, "生まれた年", "birthYear")
    profile["weeklyGoalMinutes"] = _optional_int(
        data.get("weeklyGoalMinutes"), 0, 7 * 24 * 60, "週の目標", "weeklyGoalMinutes")
    return profile


def _optional_int(value: Any, low: int, high: int, label: str, field: str) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise AuthError(400, f"{label}は整数で入力してください", field)
    if not (low <= value <= high):
        raise AuthError(400, f"{label}は{low}〜{high}の範囲で入力してください", field)
    return int(value)


# ============ 旧 DB の移行 ============

def migrate_from_password_auth(db_path: Path) -> bool:
    """パスワード認証だった頃の DB からパスワードハッシュと全セッションを消す。移行したら True。

    教材やプロフィールは user_id で紐付いているので、ID を変えずに users だけを作り直す。
    """
    with closing(sqlite3.connect(db_path, isolation_level=None)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "password_hash" not in columns:
            return False

        # DROP TABLE users で教材まで連鎖削除されないように、外部キーを切っておく
        # （この PRAGMA はトランザクションの外でしか効かない）
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f"CREATE TABLE users_new {USERS_COLUMNS}")
            conn.execute(
                "INSERT INTO users_new (id, legacy_login_id, profile, profile_completed, created_at)"
                " SELECT id, login_id, profile, profile_completed, created_at FROM users"
            )
            conn.execute("DROP TABLE users")
            conn.execute("ALTER TABLE users_new RENAME TO users")
            conn.execute("DELETE FROM sessions")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("移行後の外部キーに不整合があります")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        # DROP したページにハッシュが残らないよう、ファイルごと詰め直す
        conn.execute("VACUUM")
    return True


# ============ ストア ============

class AuthStore:
    def __init__(self, db_path: Path, session_ttl: float = SESSION_TTL_SEC, clock=time.time):
        self.db_path = Path(db_path)
        self.session_ttl = session_ttl
        self.clock = clock

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        migrate_from_password_auth(self.db_path)
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    # ---- アカウント ----

    def login_with_google(self, sub: str, email: str) -> Dict[str, Any]:
        """確認済みの Google アカウントでログインする。

        登録済みなら sub で本人を引く。未登録なら招待が必要で、招待はここで消費する。
        招待に user_id があれば、その旧アカウントに結び付ける。
        """
        email = normalize_email(email)
        with closing(self._connect()) as conn, conn:
            # 同じ招待を2つの要求が同時に使えないよう、最初に書き込みロックを取る
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM users WHERE google_sub = ?", (sub,)).fetchone()
            if row:
                user_id = row["id"]
                conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
            else:
                invite = conn.execute(
                    "SELECT user_id FROM invitations WHERE email = ?", (email,)).fetchone()
                if not invite:
                    raise NotInvitedError()
                user_id = invite["user_id"]
                if user_id is None:
                    user_id = conn.execute(
                        "INSERT INTO users (google_sub, email, created_at) VALUES (?, ?, ?)",
                        (sub, email, datetime.now(timezone.utc).isoformat()),
                    ).lastrowid
                else:
                    conn.execute(
                        "UPDATE users SET google_sub = ?, email = ?, legacy_login_id = NULL WHERE id = ?",
                        (sub, email, user_id),
                    )
                conn.execute("DELETE FROM invitations WHERE email = ?", (email,))
        return self.get_user(user_id)

    def get_user(self, user_id: int) -> Dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, email, profile, profile_completed FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if not row:
            raise AuthError(404, "ユーザーが見つかりません")
        return {
            "id": row["id"],
            "email": row["email"],
            "profileCompleted": bool(row["profile_completed"]),
            "profile": json.loads(row["profile"]),
        }

    def update_profile(self, user_id: int, data: Any) -> Dict[str, Any]:
        profile = normalize_profile(data)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE users SET profile = ?, profile_completed = 1 WHERE id = ?",
                (json.dumps(profile, ensure_ascii=False), user_id),
            )
        return self.get_user(user_id)

    # ---- 招待（admin.py から使う） ----

    def invite(self, email: Any, user_id: Optional[int] = None) -> str:
        """メールアドレスを招待する。user_id を渡すと、そのアカウントへの結び付けになる。"""
        email = normalize_email(email)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise AuthError(409, f"{email} はすでに登録されています")
            if user_id is not None:
                target = conn.execute(
                    "SELECT google_sub FROM users WHERE id = ?", (user_id,)).fetchone()
                if not target:
                    raise AuthError(404, f"ユーザー {user_id} が見つかりません")
                if target["google_sub"] is not None:
                    raise AuthError(409, f"ユーザー {user_id} はすでに Google アカウントと結び付いています")
                # 1つのアカウントへの招待は最後の1つだけ有効にする
                conn.execute("DELETE FROM invitations WHERE user_id = ?", (user_id,))
            conn.execute(
                "INSERT INTO invitations (email, user_id, created_at) VALUES (?, ?, ?)"
                " ON CONFLICT (email) DO UPDATE SET user_id = excluded.user_id",
                (email, user_id, datetime.now(timezone.utc).isoformat()),
            )
        return email

    def uninvite(self, email: Any) -> bool:
        email = normalize_email(email)
        with closing(self._connect()) as conn, conn:
            return conn.execute("DELETE FROM invitations WHERE email = ?", (email,)).rowcount > 0

    def list_invitations(self) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT email, user_id, created_at FROM invitations ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def list_users(self) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, email, legacy_login_id, google_sub IS NOT NULL AS linked, profile, created_at"
                " FROM users ORDER BY id").fetchall()
        return [{
            "id": row["id"],
            "email": row["email"],
            "legacyLoginId": row["legacy_login_id"],
            "linked": bool(row["linked"]),
            "nickname": json.loads(row["profile"]).get("nickname", ""),
            "createdAt": row["created_at"],
        } for row in rows]

    # ---- セッション ----

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_hash_token(token), user_id, now, now + self.session_ttl),
            )
        return token

    def user_for_session(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT user_id FROM sessions WHERE token_hash = ? AND expires_at >= ?",
                (_hash_token(token), self.clock()),
            ).fetchone()
        return self.get_user(row["user_id"]) if row else None

    def delete_session(self, token: Optional[str]) -> None:
        if not token:
            return
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
