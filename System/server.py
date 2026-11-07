"""Onsta のアプリサーバー：フロント（../static）の配信と API。

API（リクエスト・レスポンスとも JSON。エラーは {"error": メッセージ, "field": 項目名|null}）:

    GET    /api/auth/me              ログイン中のユーザー（未ログインなら 401）
    GET    /api/auth/google/start    Google の同意画面へリダイレクトする（JSON ではない）
    GET    /api/auth/google/callback Google からの戻り先。セッションを発行して / へリダイレクトする。
                                     失敗したら /#/login?error=<コード> へ（JSON ではない）
    POST   /api/auth/logout
    PUT    /api/me/profile           プロフィールを保存（初回設定・編集の両方）

    GET    /api/materials            自分のカテゴリと教材 {categories, materials}
    POST   /api/categories           {name, color} → カテゴリを作る
    POST   /api/materials            {title, categoryId, isbn?, image?(data URL), coverUrl?}
    DELETE /api/materials/<id>
    GET    /api/materials/<id>/image 教材の画像（JSON ではなく画像そのもの）
    GET    /api/books/<isbn>         バーコードの番号から書名・表紙を調べる

    GET    /loc                      WebSocket。LoC 推論サーバー（LoC/server.py）へそのまま中継する。
                                     ngrok などで外から開いたときに、ポート1つ（このサーバー）だけ
                                     公開すれば済むようにするため。ログイン中で、同じオリジンの
                                     ページからの接続だけ通す

ユーザーは {id, email, profileCompleted, profile} の形で返す。
セッションは HttpOnly の Cookie で持つ。書き込み系は Content-Type: application/json を必須にして、
SameSite=Lax と合わせて他サイトのフォームから勝手に送られないようにしている。
Google からの戻りは他サイトからの遷移なので、state の Cookie も SameSite=Lax（Strict だと届かない）。
"""

from __future__ import annotations

import functools
import hmac
import http.server
import json
import re
import socket
import threading
import urllib.parse
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from .auth import AuthError, AuthStore, NotInvitedError
from .books import BookLookup, normalize_isbn
from .google_oauth import FLOW_TTL_SEC, START_PATH, GoogleOAuth, OAuthFailure
from .materials import MaterialStore, decode_image, sniff_image_type

SESSION_COOKIE = "onsta_session"
# ログイン途中のブラウザを見分ける Cookie（中身は state）。Google まわりのパスにだけ送らせる
OAUTH_COOKIE = "onsta_oauth_state"
OAUTH_COOKIE_PATH = "/api/auth/google"
MAX_BODY_BYTES = 16 * 1024
# 画像を data URL（base64 で 4/3 倍）で受け取るので、教材の追加だけ大きめに
MAX_MATERIAL_BODY_BYTES = 3 * 1024 * 1024
# 推論サーバーへ中継しないヘッダー（Cookie は推論サーバーに渡す必要がない）
LOC_DROP_HEADERS = {"host", "cookie"}
CRLF = "\r\n"


class AppHandler(http.server.SimpleHTTPRequestHandler):
    # make_handler() で差し込む
    store: AuthStore
    materials: MaterialStore
    books: BookLookup
    google: GoogleOAuth
    secure_cookie = False
    loc_upstream: Optional[Tuple[str, int]] = None  # 推論サーバーの (host, port)。None なら中継しない

    # (メソッド, パスの正規表現, メソッド名)。グループは数字なら int にして渡す
    ROUTES = [
        ("GET", r"/api/auth/me", "_me"),
        ("GET", r"/api/auth/google/start", "_google_start"),
        ("GET", r"/api/auth/google/callback", "_google_callback"),
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
        elif self.path.split("?", 1)[0] == "/loc":
            try:
                self._proxy_loc()
            except AuthError as err:
                self._send_json(err.status, {"error": err.message, "field": err.field})
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

    def _google_start(self) -> None:
        # 127.0.0.1 などで開かれていたら公開 URL に寄せる（Cookie は redirect_uri のホストに付ける必要がある）
        public_host = urllib.parse.urlsplit(self.google.public_url).netloc
        if self.headers.get("Host", "").lower() != public_host.lower():
            self._redirect(self.google.public_url + START_PATH)
            return
        state, url = self.google.begin()
        self._redirect(url, [self._cookie_header(OAUTH_COOKIE, state, FLOW_TTL_SEC, OAUTH_COOKIE_PATH)])

    def _google_callback(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        state = query.get("state", [""])[0]
        started = self._cookie(OAUTH_COOKIE) or ""
        clear_state = self._cookie_header(OAUTH_COOKIE, "", 0, OAUTH_COOKIE_PATH)
        try:
            # ログインを始めたブラウザ自身への戻りか確かめる（他人のコードを踏ませる login CSRF を防ぐ）
            if not started or not hmac.compare_digest(state.encode("utf-8"), started.encode("utf-8")):
                raise OAuthFailure("expired")
            identity = self.google.finish(state, query.get("code", [""])[0], query.get("error", [""])[0])
            user = self.store.login_with_google(identity.sub, identity.email)
        except OAuthFailure as err:
            if err.detail:
                self.log_message("Google ログインに失敗: %s", err.detail)
            self._redirect(f"/#/login?error={err.code}", [clear_state])
            return
        except NotInvitedError:
            self.log_message("招待されていない Google アカウントのログインを拒否しました")
            self._redirect("/#/login?error=not_invited", [clear_state])
            return
        except AuthError as err:
            self.log_message("Google ログインに失敗: %s", err.message)
            self._redirect("/#/login?error=failed", [clear_state])
            return

        # ログイン前のセッションを引き継がない（セッション固定を防ぐ）
        self.store.delete_session(self._session_token())
        session = self.store.create_session(user["id"])
        self._redirect("/", [clear_state, self._session_cookie_header(session)])

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

    # ---- LoC 推論の中継（WebSocket） ----

    def _proxy_loc(self) -> None:
        """ハンドシェイクをそのまま推論サーバーへ送り、以降は両方向のバイト列を流すだけ。"""
        if self.headers.get("Upgrade", "").lower() != "websocket":
            raise AuthError(400, "WebSocket で接続してください")
        if not self._same_origin():
            raise AuthError(403, "別のサイトからは接続できません")
        self._require_user()
        if not self.loc_upstream:
            raise AuthError(503, "LoC 推論サーバーの中継は無効です")

        try:
            upstream = socket.create_connection(self.loc_upstream, timeout=5)
        except OSError:
            raise AuthError(502, "LoC 推論サーバーに接続できません（LoC/server.py を起動してください）") from None
        upstream.settimeout(None)

        host, port = self.loc_upstream
        lines = ["GET /loc HTTP/1.1", f"Host: {host}:{port}"]
        lines += [f"{name}: {value}" for name, value in self.headers.items()
                  if name.lower() not in LOC_DROP_HEADERS]
        self.close_connection = True
        self.log_message('"%s" 推論サーバーへ中継', self.requestline)
        with upstream:
            upstream.sendall((CRLF.join(lines) + CRLF + CRLF).encode("latin-1"))
            # ブラウザは 101 応答を受け取るまでフレームを送らないので、rfile に読み残しは無い。
            # ここからは生のソケット同士をつなぐ
            client = self.connection
            back = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
            back.start()
            _pipe(client, upstream)
            back.join()

    def _same_origin(self) -> bool:
        """Origin（接続元ページ）と Host（このサーバー）が同じか。ngrok でも Host は公開名のまま届く。"""
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if not origin or not host:
            return False
        return urllib.parse.urlsplit(origin).netloc.lower() == host.lower()

    # ---- 小道具 ----

    def _cookie(self, name: str) -> Optional[str]:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = cookie.get(name)
        return morsel.value if morsel else None

    def _session_token(self) -> Optional[str]:
        return self._cookie(SESSION_COOKIE)

    def _cookie_header(self, name: str, value: str, max_age: int, path: str = "/") -> str:
        attrs = f"Path={path}; Max-Age={max_age}; HttpOnly; SameSite=Lax"
        if self.secure_cookie:
            attrs += "; Secure"
        return f"{name}={value}; {attrs}"

    def _session_cookie_header(self, session: str) -> str:
        """session が "" なら Cookie を消す指定になる。"""
        return self._cookie_header(SESSION_COOKIE, session, int(self.store.session_ttl) if session else 0)

    def _redirect(self, location: str, cookies: Iterable[str] = ()) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        # 認可コード付きの URL をリファラで外に出さない
        self.send_header("Referrer-Policy", "no-referrer")
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

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
            self.send_header("Set-Cookie", self._session_cookie_header(session))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        # 開発中に JS を書き換えたとき古いものが使われないように（API の応答もキャッシュさせない）
        self.send_header("Cache-Control", getattr(self, "cache_control", "no-store"))
        super().end_headers()

    def log_request(self, code="-", size="-"):
        # クエリは記録しない（Google からの戻りには認可コードが入っている）
        self.log_message('"%s %s" %s', self.command, self.path.split("?", 1)[0], code)

    def log_message(self, format, *args):  # noqa: A002 - 親クラスのシグネチャ
        if self.path.startswith(("/api/", "/loc")):
            super().log_message(format, *args)


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    """src から読んだものを dst に書く。どちらかが閉じたら、相手側の送信も止めて終わる。"""
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def make_server(host: str, port: int, static_dir: Path, store: AuthStore,
                materials: MaterialStore, books: BookLookup, google: GoogleOAuth,
                secure_cookie: bool = False,
                loc_upstream: Optional[Tuple[str, int]] = None) -> http.server.ThreadingHTTPServer:
    handler_cls = type("BoundAppHandler", (AppHandler,), {
        "store": store,
        "materials": materials,
        "books": books,
        "google": google,
        "secure_cookie": secure_cookie,
        "loc_upstream": loc_upstream,
    })
    handler = functools.partial(handler_cls, directory=str(static_dir))
    return http.server.ThreadingHTTPServer((host, port), handler)
