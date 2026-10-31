"""ユーザーごとの教材とカテゴリ（教科）を SQLite で管理する。

- カテゴリはユーザーが自分で作る（名前＋色）。同じ名前は1ユーザーに1つだけ
- 教材は 教材名・カテゴリ・ISBN（バーコードで追加したときだけ）・画像 を持つ
- 画像は DB に BLOB で保存し、`image_version` を上げて画面側のキャッシュを切り替える
  （受け付けるのは JPEG / PNG / WebP / GIF のみ。中身の先頭バイトで判定する）
"""

from __future__ import annotations

import base64
import binascii
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .auth import AuthError
from .db import connect

MAX_TITLE_LEN = 80
MAX_CATEGORY_LEN = 20
MAX_CATEGORIES = 30
MAX_MATERIALS = 300
MAX_IMAGE_BYTES = 2 * 1024 * 1024

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DATA_URL_RE = re.compile(r"^data:image/[a-z+.-]+;base64,(.+)$", re.S)

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL COLLATE NOCASE,
    color      TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    UNIQUE (user_id, name)
);
CREATE TABLE IF NOT EXISTS materials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category_id   INTEGER NOT NULL REFERENCES categories(id),
    title         TEXT    NOT NULL,
    isbn          TEXT    NOT NULL DEFAULT '',
    image         BLOB,
    image_type    TEXT    NOT NULL DEFAULT '',
    image_version INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS materials_user ON materials(user_id);
"""


def sniff_image_type(data: bytes) -> str:
    """先頭バイトから画像の MIME タイプを判定する（対応外なら ""）。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return ""


def decode_image(data_url: Any) -> Tuple[bytes, str]:
    """画面から来た data URL（base64）を画像のバイト列にする。"""
    match = isinstance(data_url, str) and DATA_URL_RE.match(data_url)
    if not match:
        raise AuthError(400, "画像の形式が正しくありません", "image")
    try:
        data = base64.b64decode(match.group(1), validate=True)
    except (binascii.Error, ValueError):
        raise AuthError(400, "画像の形式が正しくありません", "image") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise AuthError(400, "画像が大きすぎます（2MBまで）", "image")
    image_type = sniff_image_type(data)
    if not image_type:
        raise AuthError(400, "画像は JPEG / PNG / WebP / GIF にしてください", "image")
    return data, image_type


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MaterialStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        with closing(connect(self.db_path)) as conn, conn:
            conn.executescript(SCHEMA)

    # ---- 一覧 ----

    def list_for_user(self, user_id: int) -> Dict[str, List[Dict[str, Any]]]:
        with closing(connect(self.db_path)) as conn:
            categories = conn.execute(
                "SELECT id, name, color FROM categories WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
            materials = conn.execute(
                "SELECT id, category_id, title, isbn, image_type, image_version, created_at"
                " FROM materials WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
        return {
            "categories": [dict(row) for row in categories],
            "materials": [self._material_json(row) for row in materials],
        }

    @staticmethod
    def _material_json(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "categoryId": row["category_id"],
            "title": row["title"],
            "isbn": row["isbn"],
            "hasImage": bool(row["image_type"]),
            "imageVersion": row["image_version"],
            "createdAt": row["created_at"],
        }

    # ---- カテゴリ ----

    def add_category(self, user_id: int, name: Any, color: Any) -> Dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            raise AuthError(400, "カテゴリ名を入力してください", "categoryName")
        name = name.strip()
        if len(name) > MAX_CATEGORY_LEN:
            raise AuthError(400, f"カテゴリ名は{MAX_CATEGORY_LEN}文字以内にしてください", "categoryName")
        if not isinstance(color, str) or not HEX_COLOR_RE.match(color):
            raise AuthError(400, "カテゴリの色が正しくありません", "categoryColor")

        with closing(connect(self.db_path)) as conn, conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM categories WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_CATEGORIES:
                raise AuthError(400, f"カテゴリは{MAX_CATEGORIES}個までです", "categoryName")
            try:
                cur = conn.execute(
                    "INSERT INTO categories (user_id, name, color, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, name, color.lower(), _now()),
                )
            except sqlite3.IntegrityError:
                raise AuthError(409, "同じ名前のカテゴリがすでにあります", "categoryName") from None
        return {"id": cur.lastrowid, "name": name, "color": color.lower()}

    # ---- 教材 ----

    def add_material(self, user_id: int, title: Any, category_id: Any, isbn: str = "",
                     image: Optional[Tuple[bytes, str]] = None) -> Dict[str, Any]:
        if not isinstance(title, str) or not title.strip():
            raise AuthError(400, "教材名を入力してください", "title")
        title = title.strip()
        if len(title) > MAX_TITLE_LEN:
            raise AuthError(400, f"教材名は{MAX_TITLE_LEN}文字以内にしてください", "title")
        if isinstance(category_id, bool) or not isinstance(category_id, int):
            raise AuthError(400, "カテゴリを選んでください", "categoryId")

        data, image_type = image if image else (None, "")
        with closing(connect(self.db_path)) as conn, conn:
            owner = conn.execute(
                "SELECT user_id FROM categories WHERE id = ?", (category_id,)).fetchone()
            if not owner or owner["user_id"] != user_id:
                raise AuthError(400, "カテゴリを選んでください", "categoryId")
            count = conn.execute(
                "SELECT COUNT(*) FROM materials WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_MATERIALS:
                raise AuthError(400, f"教材は{MAX_MATERIALS}冊までです")
            cur = conn.execute(
                "INSERT INTO materials (user_id, category_id, title, isbn, image, image_type,"
                " image_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, category_id, title, isbn, data, image_type, 1 if data else 0, _now()),
            )
            row = conn.execute(
                "SELECT id, category_id, title, isbn, image_type, image_version, created_at"
                " FROM materials WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._material_json(row)

    def delete_material(self, user_id: int, material_id: int) -> None:
        with closing(connect(self.db_path)) as conn, conn:
            cur = conn.execute(
                "DELETE FROM materials WHERE id = ? AND user_id = ?", (material_id, user_id))
        if cur.rowcount == 0:
            raise AuthError(404, "教材が見つかりません")

    def get_image(self, user_id: int, material_id: int) -> Tuple[bytes, str]:
        with closing(connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT image, image_type FROM materials WHERE id = ? AND user_id = ?",
                (material_id, user_id)).fetchone()
        if not row or not row["image_type"]:
            raise AuthError(404, "画像がありません")
        return bytes(row["image"]), row["image_type"]
