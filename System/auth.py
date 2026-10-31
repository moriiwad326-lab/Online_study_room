"""Onsta のアカウントとログインセッションを SQLite で管理する。

- パスワードは PBKDF2-HMAC-SHA256（ソルト付き）でハッシュ化して保存し、平文は持たない
- セッションはランダムなトークンを Cookie で渡し、DB には SHA-256 したものだけを保存する
  （DB が漏れてもそのままセッションに使えないように）
- 同じログインIDで短時間にパスワードを間違え続けたら、しばらくログインを止める
- プロフィール（ニックネームなど）は JSON で保存し、受け付ける項目と長さはここで検証する

HTTP の扱いは `server.py`、ここは純粋なデータ操作だけを持つ。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .db import connect

# OWASP の推奨値（2023）。テストでは小さくして速くする
PBKDF2_ITERATIONS = 600_000
SESSION_TTL_SEC = 30 * 24 * 3600

# メールアドレスもそのまま使えるように @ を許可し、長さも 64 文字まで取る
LOGIN_ID_RE = re.compile(r"^[A-Za-z0-9_.@-]{4,64}$")
PASSWORD_MIN = 8
PASSWORD_MAX = 128

# この回数だけ連続で失敗したら、LOCK_WINDOW_SEC のあいだログインを受け付けない
MAX_LOGIN_FAILURES = 5
LOCK_WINDOW_SEC = 15 * 60

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    login_id          TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash     TEXT    NOT NULL,
    profile           TEXT    NOT NULL DEFAULT '{}',
    profile_completed INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL    NOT NULL,
    expires_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
"""


class AuthError(Exception):
    """画面にそのまま出せるメッセージと HTTP ステータスを持つエラー。"""

    def __init__(self, status: int, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.field = field


# ============ パスワード ============

def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


# ============ 入力の検証 ============

def validate_login_id(login_id: Any) -> str:
    if not isinstance(login_id, str) or not LOGIN_ID_RE.match(login_id):
        raise AuthError(400, "ログインIDは半角英数字と _ . - @ の4〜64文字で入力してください", "loginId")
    return login_id


def validate_password(password: Any) -> str:
    if not isinstance(password, str) or not (PASSWORD_MIN <= len(password) <= PASSWORD_MAX):
        raise AuthError(400, f"パスワードは{PASSWORD_MIN}〜{PASSWORD_MAX}文字で入力してください", "password")
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise AuthError(400, "パスワードには英字と数字を両方含めてください", "password")
    return password


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


# ============ ストア ============

class AuthStore:
    def __init__(self, db_path: Path, iterations: int = PBKDF2_ITERATIONS,
                 session_ttl: float = SESSION_TTL_SEC, clock=time.time):
        self.db_path = Path(db_path)
        self.iterations = iterations
        self.session_ttl = session_ttl
        self.clock = clock
        self._failures: Dict[str, Tuple[int, float]] = {}
        self._failures_lock = threading.Lock()
        # 存在しないIDでも同じだけ時間をかけ、ID の有無を応答時間で推測されないようにする
        self._dummy_hash = hash_password(secrets.token_hex(8), iterations)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    # ---- アカウント ----

    def register(self, login_id: Any, password: Any) -> Dict[str, Any]:
        login_id = validate_login_id(login_id)
        password = validate_password(password)
        password_hash = hash_password(password, self.iterations)
        now = datetime.now(timezone.utc).isoformat()
        try:
            with closing(self._connect()) as conn, conn:
                cur = conn.execute(
                    "INSERT INTO users (login_id, password_hash, created_at) VALUES (?, ?, ?)",
                    (login_id, password_hash, now),
                )
                user_id = cur.lastrowid
        except sqlite3.IntegrityError:
            raise AuthError(409, "このログインIDはすでに使われています", "loginId") from None
        return self.get_user(user_id)

    def authenticate(self, login_id: Any, password: Any) -> Dict[str, Any]:
        if not isinstance(login_id, str) or not isinstance(password, str) or not login_id or not password:
            raise AuthError(400, "ログインIDとパスワードを入力してください")
        key = login_id.lower()
        self._check_lock(key)

        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, password_hash FROM users WHERE login_id = ?", (login_id,)
            ).fetchone()
        ok = verify_password(password, row["password_hash"] if row else self._dummy_hash)
        if not row or not ok:
            self._record_failure(key)
            raise AuthError(401, "ログインIDまたはパスワードが正しくありません")
        with self._failures_lock:
            self._failures.pop(key, None)
        return self.get_user(row["id"])

    def get_user(self, user_id: int) -> Dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, login_id, profile, profile_completed FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if not row:
            raise AuthError(404, "ユーザーが見つかりません")
        return {
            "id": row["id"],
            "loginId": row["login_id"],
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

    # ---- ログイン試行の制限 ----

    def _check_lock(self, key: str) -> None:
        with self._failures_lock:
            count, first = self._failures.get(key, (0, 0.0))
            if self.clock() - first > LOCK_WINDOW_SEC:
                self._failures.pop(key, None)
                return
            if count >= MAX_LOGIN_FAILURES:
                raise AuthError(429, "ログインに続けて失敗したため、しばらくしてからお試しください")

    def _record_failure(self, key: str) -> None:
        with self._failures_lock:
            count, first = self._failures.get(key, (0, self.clock()))
            self._failures[key] = (count + 1, first)

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
