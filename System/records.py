"""学習記録（いつ・どの教材を・何分・LoC の推移）を SQLite で管理する。

- 以前はブラウザの localStorage にだけ保存していた。フォロー中の人のタイムラインに出すため、サーバーに置く
- 教材は materials を指す。教材を消しても記録は残り、画面では「削除した教材」と出す
- タイムラインには、自分とフォロー中の人の記録を新しい順に並べる。
  フォローは承認なしなので、記録はログインしている誰からでも見えるものとして扱う
- localStorage に残っている記録は、ブラウザが一度だけ `import_records` で送ってくる。
  ブラウザ側の ID（client_id）で重複を弾くので、何度送られても増えない
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
from .materials import SCHEMA as MATERIALS_SCHEMA
from .social import SCHEMA as SOCIAL_SCHEMA, public_profile

MAX_DURATION_MIN = 24 * 60
MAX_MEMO_LEN = 200
MAX_LOC_POINTS = 120
MAX_CLIENT_ID_LEN = 64
MAX_IMPORT = 2000
TIMELINE_LIMIT = 100
USER_RECORDS_LIMIT = 50
SOURCES = ("manual", "camera")

SCHEMA = """
CREATE TABLE IF NOT EXISTS study_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    material_id  INTEGER,
    duration_min INTEGER NOT NULL,
    memo         TEXT    NOT NULL DEFAULT '',
    loc_series   TEXT    NOT NULL DEFAULT '[]',
    avg_loc      INTEGER,
    source       TEXT    NOT NULL,
    client_id    TEXT,
    created_at   TEXT    NOT NULL,
    UNIQUE (user_id, client_id)
);
CREATE INDEX IF NOT EXISTS study_records_user ON study_records(user_id, created_at);
"""

# 記録＋教材＋カテゴリ＋記録した人をまとめて引く
SELECT_RECORDS = """
SELECT r.id, r.user_id, r.material_id, r.duration_min, r.memo, r.loc_series, r.avg_loc,
       r.source, r.created_at,
       m.id AS m_id, m.title AS m_title, m.image_type AS m_image_type,
       m.image_version AS m_image_version, c.name AS c_name, c.color AS c_color,
       u.profile AS u_profile
FROM study_records r
JOIN users u ON u.id = r.user_id
LEFT JOIN materials m ON m.id = r.material_id AND m.user_id = r.user_id
LEFT JOIN categories c ON c.id = m.category_id
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_created_at(value: Any) -> str:
    """取り込む記録の日時。ISO 8601 を UTC にそろえる。未来の日時は受け付けない。"""
    if not isinstance(value, str):
        raise AuthError(400, "記録の日時が正しくありません", "createdAt")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AuthError(400, "記録の日時が正しくありません", "createdAt") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed > datetime.now(timezone.utc):
        raise AuthError(400, "記録の日時が正しくありません", "createdAt")
    return parsed.isoformat()


def normalize_record(data: Any, imported: bool = False) -> Dict[str, Any]:
    """画面から来た学習記録を検証し、保存する形にそろえる（教材の持ち主の確認は別）。

    imported なら localStorage からの取り込み用に、0分の記録と長すぎるメモ（切り詰める）も受け付ける。
    """
    if not isinstance(data, dict):
        raise AuthError(400, "記録の形式が正しくありません")

    material_id = data.get("materialId")
    if isinstance(material_id, str) and material_id.isdigit():
        material_id = int(material_id)
    if isinstance(material_id, bool) or not isinstance(material_id, int):
        raise AuthError(400, "教材を選んでください", "materialId")

    duration = data.get("durationMin")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise AuthError(400, "学習時間は数値で入力してください", "durationMin")
    duration = round(duration)
    if not ((0 if imported else 1) <= duration <= MAX_DURATION_MIN):
        raise AuthError(400, f"学習時間は1〜{MAX_DURATION_MIN}分にしてください", "durationMin")

    memo = data.get("memo") or ""
    if not isinstance(memo, str):
        raise AuthError(400, "メモの形式が正しくありません", "memo")
    memo = memo.strip()
    if imported:
        memo = memo[:MAX_MEMO_LEN]
    if len(memo) > MAX_MEMO_LEN:
        raise AuthError(400, f"メモは{MAX_MEMO_LEN}文字以内にしてください", "memo")

    series = data.get("locSeries") or []
    if (not isinstance(series, list) or len(series) > MAX_LOC_POINTS
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100
                       for v in series)):
        raise AuthError(400, "LoC の値が正しくありません", "locSeries")
    series = [round(v) for v in series]

    source = data.get("source") or "manual"
    if source not in SOURCES:
        raise AuthError(400, "記録の種類が正しくありません", "source")

    return {
        "material_id": material_id,
        "duration_min": duration,
        "memo": memo,
        "loc_series": series,
        "avg_loc": round(sum(series) / len(series)) if series else None,
        "source": source,
    }


class RecordStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        with closing(connect(self.db_path)) as conn, conn:
            # 教材・フォローの表と JOIN するので、先に作られていなくても困らないようにする
            conn.executescript(MATERIALS_SCHEMA + SOCIAL_SCHEMA + SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    # ---- 追加・削除 ----

    def add_record(self, user_id: int, data: Any) -> Dict[str, Any]:
        record = normalize_record(data)
        with closing(self._connect()) as conn, conn:
            if not self._owns_material(conn, user_id, record["material_id"]):
                raise AuthError(400, "教材を選んでください", "materialId")
            record_id = self._insert(conn, user_id, record, None, _now())
        return self._get(record_id)

    def import_records(self, user_id: int, items: Any) -> Dict[str, int]:
        """localStorage に残っていた記録を取り込む。{imported: 取り込んだ数, skipped: 読めなかった数}。

        教材が見つからない（消した・他人の）記録も、教材なし（「削除した教材」）で残す。
        壊れた記録が混ざっていても、他の記録は取り込む。
        """
        if not isinstance(items, list) or len(items) > MAX_IMPORT:
            raise AuthError(400, "記録の形式が正しくありません")
        prepared = []
        skipped = 0
        for item in items:
            try:
                record = normalize_record(item, imported=True)
                client_id = item.get("id")
                if not isinstance(client_id, str) or not client_id or len(client_id) > MAX_CLIENT_ID_LEN:
                    raise AuthError(400, "記録の形式が正しくありません")
                prepared.append((record, client_id, _parse_created_at(item.get("createdAt"))))
            except AuthError:
                skipped += 1

        added = 0
        with closing(self._connect()) as conn, conn:
            for record, client_id, created_at in prepared:
                if not self._owns_material(conn, user_id, record["material_id"]):
                    record["material_id"] = None
                exists = conn.execute(
                    "SELECT 1 FROM study_records WHERE user_id = ? AND client_id = ?", (user_id, client_id)
                ).fetchone()
                if not exists:
                    self._insert(conn, user_id, record, client_id, created_at)
                    added += 1
        return {"imported": added, "skipped": skipped}

    def delete_all(self, user_id: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM study_records WHERE user_id = ?", (user_id,))

    @staticmethod
    def _owns_material(conn: sqlite3.Connection, user_id: int, material_id: Optional[int]) -> bool:
        return material_id is not None and conn.execute(
            "SELECT 1 FROM materials WHERE id = ? AND user_id = ?", (material_id, user_id)
        ).fetchone() is not None

    @staticmethod
    def _insert(conn: sqlite3.Connection, user_id: int, record: Dict[str, Any],
                client_id: Optional[str], created_at: str) -> int:
        return conn.execute(
            "INSERT INTO study_records (user_id, material_id, duration_min, memo, loc_series, avg_loc,"
            " source, client_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, record["material_id"], record["duration_min"], record["memo"],
             json.dumps(record["loc_series"]), record["avg_loc"], record["source"], client_id, created_at),
        ).lastrowid

    # ---- 一覧 ----

    def _get(self, record_id: int) -> Dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute(SELECT_RECORDS + " WHERE r.id = ?", (record_id,)).fetchone()
        return self._record_json(row)

    def list_for_user(self, user_id: int, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """その人の記録（新しい順）。limit なしなら全部（自分のレポートの集計用）。"""
        sql = SELECT_RECORDS + " WHERE r.user_id = ? ORDER BY r.created_at DESC, r.id DESC"
        params: List[Any] = [user_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._record_json(row) for row in rows]

    def timeline(self, user_id: int) -> List[Dict[str, Any]]:
        """自分とフォロー中の人の記録（新しい順）。"""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                SELECT_RECORDS
                + " WHERE r.user_id = :me"
                  " OR r.user_id IN (SELECT followee_id FROM follows WHERE follower_id = :me)"
                  " ORDER BY r.created_at DESC, r.id DESC LIMIT :limit",
                {"me": user_id, "limit": TIMELINE_LIMIT},
            ).fetchall()
        return [self._record_json(row) for row in rows]

    @staticmethod
    def _record_json(row: sqlite3.Row) -> Dict[str, Any]:
        material = None
        if row["m_id"] is not None:
            material = {
                "id": row["m_id"],
                "title": row["m_title"],
                "categoryName": row["c_name"] or "",
                "color": row["c_color"] or "",
                "hasImage": bool(row["m_image_type"]),
                "imageVersion": row["m_image_version"],
            }
        return {
            "id": row["id"],
            "user": public_profile({"id": row["user_id"], "profile": row["u_profile"]}),
            "materialId": row["material_id"],
            "material": material,
            "durationMin": row["duration_min"],
            "memo": row["memo"],
            "locSeries": json.loads(row["loc_series"]),
            "avgLoc": row["avg_loc"],
            "source": row["source"],
            "createdAt": row["created_at"],
        }
