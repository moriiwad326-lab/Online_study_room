"""Onsta のアプリサーバー：フロント（../static）の配信と API。

API（リクエスト・レスポンスとも JSON。エラーは {"error": メッセージ, "field": 項目名|null}）:

    GET    /api/auth/me              ログイン中のユーザー（未ログインなら 401）
    POST   /api/auth/register        {loginId, password} → 登録してそのままログイン
    POST   /api/auth/login           {loginId, password}
    POST   /api/auth/logout
    PUT    /api/me/profile           プロフィールを保存（初回設定・編集の両方）

    GET    /api/materials            自分のカテゴリと教材 {categories, materials}
    POST   /api/categories           {name, color} → カテゴリを作る
    POST   /api/materials            {title, categoryId, isbn?, image?(data URL), coverUrl?}
    DELETE /api/materials/<id>
    GET    /api/materials/<id>/image 教材の画像（JSON ではなく画像そのもの）
    GET    /api/books/<isbn>         バーコードの番号から書名・表紙を調べる

ユーザーは {id, loginId, profileCompleted, profile} の形で返す。
セッションは HttpOnly の Cookie で持つ。書き込み系は Content-Type: application/json を必須にして、
SameSite=Lax と合わせて他サイトのフォームから勝手に送られないようにしている。
"""

from __future__ import annotations

import functools
import http.server
import json
import re
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any, Dict, Optional

from .auth import AuthError, AuthStore
from .books import BookLookup, normalize_isbn
from .materials import MaterialStore, decode_image, sniff_image_type

SESSION_COOKIE = "onsta_session"
MAX_BODY_BYTES = 16 * 1024
# 画像を data URL（base64 で 4/3 倍）で受け取るので、教材の追加だけ大きめに
MAX_MATERIAL_BODY_BYTES = 3 * 1024 * 1024


class AppHandler(http.server.SimpleHTTPRequestHandler):
    # make_handler() で差し込む
    store: AuthStore
    materials: MaterialStore
    books: BookLookup
    secure_cookie = False

    # (メソッド, パスの正規表現, メソッド名)。グループは数字なら int にして渡す
    ROUTES = [
        ("GET", r"/api/auth/me", "_me"),
        ("POST", r"/api/auth/register", "_register"),
        ("POST", r"/api/auth/login", "_login"),
        ("POST", r"/api/auth/logout", "_logout"),
        ("PUT", r"/api/me/profile", "_update_profile"),
        ("GET", r"/api/materials", "_list_materials"),
        ("POST", r"/api/categories", "_add_category"),
        ("POST", r"/api/materials", "_add_material"),
        ("DELETE", r"/api/materials/(\d+)", "_delete_material"),
        ("GET", r"/api/materials/(\d+)/image", "_material_image"),
        ("GET", r"/api/books/([0-9Xx-]{10,17})", "_lookup_book"),
    ]

    # ---- 振り分け ----

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._dispatch("GET")
        else:
            super().do_GET()

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        for route_method, pattern, name in self.ROUTES:
            match = re.fullmatch(pattern, path)
            if route_method == method and match:
                args = [int(g) if g.isdigit() and len(g) < 10 else g for g in match.groups()]
                try:
                    getattr(self, name)(*args)
                except AuthError as err:
                    self._send_json(err.status, {"error": err.message, "field": err.field})
                return
        self._send_json(404, {"error": "見つかりません"})

    # ---- 認証 ----

    def _me(self) -> None:
        self._send_json(200, {"user": self._require_user()})

    def _register(self) -> None:
        body = self._read_json()
        user = self.store.register(body.get("loginId"), body.get("password"))
        self._send_json(201, {"user": user}, session=self.store.create_session(user["id"]))

    def _login(self) -> None:
        body = self._read_json()
        user = self.store.authenticate(body.get("loginId"), body.get("password"))
        self._send_json(200, {"user": user}, session=self.store.create_session(user["id"]))

    def _logout(self) -> None:
        self._read_json()
        self.store.delete_session(self._session_token())
        self._send_json(200, {"ok": True}, session="")

    def _update_profile(self) -> None:
        user = self._require_user()
        body = self._read_json()
        self._send_json(200, {"user": self.store.update_profile(user["id"], body.get("profile"))})

    # ---- 教材 ----

    def _list_materials(self) -> None:
        user = self._require_user()
        self._send_json(200, self.materials.list_for_user(user["id"]))

    def _add_category(self) -> None:
        user = self._require_user()
        body = self._read_json()
        category = self.materials.add_category(user["id"], body.get("name"), body.get("color"))
        self._send_json(201, {"category": category})

    def _add_material(self) -> None:
        user = self._require_user()
        body = self._read_json(MAX_MATERIAL_BODY_BYTES)
        isbn = normalize_isbn(body["isbn"]) if body.get("isbn") else ""

        # 画像：アップロードされたものを優先し、なければ書誌検索で見つけた表紙を取りに行く
        image = None
        if body.get("image"):
            image = decode_image(body["image"])
        elif body.get("coverUrl"):
            data = self.books.download_cover(body["coverUrl"])
            image_type = sniff_image_type(data) if data else ""
            if image_type:
                image = (data, image_type)

        material = self.materials.add_material(
            user["id"], body.get("title"), body.get("categoryId"), isbn, image)
        self._send_json(201, {"material": material})

    def _delete_material(self, material_id: int) -> None:
        user = self._require_user()
        self.materials.delete_material(user["id"], material_id)
        self._send_json(200, {"ok": True})

    def _material_image(self, material_id: int) -> None:
        user = self._require_user()
        data, image_type = self.materials.get_image(user["id"], material_id)
        self.send_response(200)
        self.send_header("Content-Type", image_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        # 画像の URL には ?v=<image_version> が付くので、本人のブラウザにはキャッシュさせてよい
        self.cache_control = "private, max-age=604800"
        self.end_headers()
        self.wfile.write(data)

    def _lookup_book(self, raw_isbn: str) -> None:
        self._require_user()
        self._send_json(200, {"book": self.books.lookup(raw_isbn)})

    # ---- 小道具 ----

    def _session_token(self) -> Optional[str]:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _require_user(self) -> Dict[str, Any]:
        user = self.store.user_for_session(self._session_token())
        if not user:
            raise AuthError(401, "ログインしてください")
        return user

    def _read_json(self, max_bytes: int = MAX_BODY_BYTES) -> Dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise AuthError(415, "Content-Type は application/json にしてください")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > max_bytes:
            raise AuthError(413, "リクエストが大きすぎます")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AuthError(400, "JSON の形式が正しくありません") from None
        if not isinstance(body, dict):
            raise AuthError(400, "JSON の形式が正しくありません")
        return body

    def _send_json(self, status: int, payload: Dict[str, Any], session: Optional[str] = None) -> None:
        """session に文字列を渡すと Cookie を発行する（"" なら削除）。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if session is not None:
            max_age = int(self.store.session_ttl) if session else 0
            attrs = f"Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"
            if self.secure_cookie:
                attrs += "; Secure"
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}={session}; {attrs}")
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        # 開発中に JS を書き換えたとき古いものが使われないように（API の応答もキャッシュさせない）
        self.send_header("Cache-Control", getattr(self, "cache_control", "no-store"))
        super().end_headers()

    def log_message(self, format, *args):  # noqa: A002 - 親クラスのシグネチャ
        if self.path.startswith("/api/"):
            super().log_message(format, *args)


def make_server(host: str, port: int, static_dir: Path, store: AuthStore,
                materials: MaterialStore, books: BookLookup,
                secure_cookie: bool = False) -> http.server.ThreadingHTTPServer:
    handler_cls = type("BoundAppHandler", (AppHandler,), {
        "store": store,
        "materials": materials,
        "books": books,
        "secure_cookie": secure_cookie,
    })
    handler = functools.partial(handler_cls, directory=str(static_dir))
    return http.server.ThreadingHTTPServer((host, port), handler)
