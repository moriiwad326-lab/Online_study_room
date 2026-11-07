"""いま LoC を計測している人（タイムラインの「いま勉強中」）を SQLite で管理する。

- 計測画面が計測中ずっと、約5秒ごとに今の状態（教材・継続時間・今の LoC・直近の LoC・状態）を送ってくる
  （`update`）。計測を終えたり画面を離れたりしたら消す（`end`）
- タブを閉じたなどで送られてこなくなった計測は、STALE_SEC 秒たったら終わったものとして扱う
- 見せる相手は、本人とその人をフォローしている人（フォローは承認なしなので、実質ログインしている誰でも）
- 推論サーバーにつながらずモックの LoC で計測しているときは、LoC を送らない（loc は None）。
  本物ではない値を他の人に見せないため

HTTP の扱いは `server.py`、ここは純粋なデータ操作だけを持つ。
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .auth import AuthError
from .db import connect
from .materials import SCHEMA as MATERIALS_SCHEMA
from .social import SCHEMA as SOCIAL_SCHEMA, public_profile

# これより長く更新が無い計測は、終わったものとみなす（秒）
STALE_SEC = 30
# 直近の LoC として受け付ける点の数（5秒おきで5分ぶん）
MAX_RECENT = 60
MAX_ELAPSED_SEC = 24 * 3600
STATUSES = ("warmup", "studying", "paused")

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_sessions (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    material_id INTEGER NOT NULL,
    status      TEXT    NOT NULL,
    elapsed_sec INTEGER NOT NULL,
    loc         INTEGER,
    recent      TEXT    NOT NULL DEFAULT '[]',
    started_at  TEXT    NOT NULL,
    updated_at  REAL    NOT NULL
);
"""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def normalize_live(data: Any) -> Dict[str, Any]:
    """計測画面から来た今の状態を検証し、保存する形にそろえる（教材の持ち主の確認は別）。"""
    if not isinstance(data, dict):
        raise AuthError(400, "計測の状態の形式が正しくありません")

    material_id = data.get("materialId")
    if isinstance(material_id, str) and material_id.isdigit():
        material_id = int(material_id)
    if isinstance(material_id, bool) or not isinstance(material_id, int):
        raise AuthError(400, "教材の指定が正しくありません", "materialId")

    status = data.get("status")
    if status not in STATUSES:
        raise AuthError(400, "計測の状態が正しくありません", "status")

    elapsed = data.get("elapsedSec")
    if not _is_number(elapsed) or not (0 <= elapsed <= MAX_ELAPSED_SEC):
        raise AuthError(400, "継続時間が正しくありません", "elapsedSec")

    loc = data.get("loc")
    if loc is not None and (not _is_number(loc) or not (0 <= loc <= 100)):
        raise AuthError(400, "LoC の値が正しくありません", "loc")

    recent = data.get("recent") or []
    if (not isinstance(recent, list) or len(recent) > MAX_RECENT
            or not all(_is_number(v) and 0 <= v <= 100 for v in recent)):
        raise AuthError(400, "LoC の値が正しくありません", "recent")

    return {
        "material_id": material_id,
        "status": status,
        "elapsed_sec": round(elapsed),
        "loc": None if loc is None else round(loc),
        "recent": [round(v) for v in recent],
    }


class LiveStore:
    def __init__(self, db_path: Path, clock=time.time):
        self.db_path = Path(db_path)
        self.clock = clock
        with closing(connect(self.db_path)) as conn, conn:
            # 教材・フォローの表と JOIN するので、先に作られていなくても困らないようにする
            conn.executescript(MATERIALS_SCHEMA + SOCIAL_SCHEMA + SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    def update(self, user_id: int, data: Any) -> None:
        """計測中の今の状態を書く（はじめての送信なら計測の開始）。"""
        live = normalize_live(data)
        now = self.clock()
        with closing(self._connect()) as conn, conn:
            if not conn.execute(
                "SELECT 1 FROM materials WHERE id = ? AND user_id = ?", (live["material_id"], user_id)
            ).fetchone():
                raise AuthError(400, "教材の指定が正しくありません", "materialId")
            # 教材を変えた・前の計測が古くなっていたら、別の計測として始め直す
            conn.execute(
                "DELETE FROM live_sessions WHERE user_id = ? AND (material_id <> ? OR updated_at < ?)",
                (user_id, live["material_id"], now - STALE_SEC),
            )
            conn.execute(
                "INSERT INTO live_sessions (user_id, material_id, status, elapsed_sec, loc, recent,"
                " started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (user_id) DO UPDATE SET status = excluded.status,"
                " elapsed_sec = excluded.elapsed_sec, loc = excluded.loc, recent = excluded.recent,"
                " updated_at = excluded.updated_at",
                (user_id, live["material_id"], live["status"], live["elapsed_sec"], live["loc"],
                 json.dumps(live["recent"]), datetime.now(timezone.utc).isoformat(), now),
            )

    def end(self, user_id: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM live_sessions WHERE user_id = ?", (user_id,))

    def list_for(self, viewer_id: int) -> List[Dict[str, Any]]:
        """本人とフォロー中の人の、いま続いている計測。始めた順。"""
        now = self.clock()
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM live_sessions WHERE updated_at < ?", (now - STALE_SEC,))
            rows = conn.execute(
                "SELECT s.*, u.profile, m.title AS m_title, c.name AS c_name, c.color AS c_color"
                " FROM live_sessions s"
                " JOIN users u ON u.id = s.user_id"
                " LEFT JOIN materials m ON m.id = s.material_id AND m.user_id = s.user_id"
                " LEFT JOIN categories c ON c.id = m.category_id"
                " WHERE s.user_id = :me"
                "    OR s.user_id IN (SELECT followee_id FROM follows WHERE follower_id = :me)"
                " ORDER BY s.started_at",
                {"me": viewer_id},
            ).fetchall()
        return [{
            "user": public_profile({"id": row["user_id"], "profile": row["profile"]}),
            "material": {
                "title": row["m_title"] or "削除した教材",
                "categoryName": row["c_name"] or "",
                "color": row["c_color"] or "",
            },
            "status": row["status"],
            "elapsedSec": row["elapsed_sec"],
            "loc": row["loc"],
            "recent": json.loads(row["recent"]),
            "startedAt": row["started_at"],
            # 最後に届いてから何秒たったか（画面で継続時間を進めるのに使う）
            "secondsSinceUpdate": max(0, round(now - row["updated_at"])),
        } for row in rows]
