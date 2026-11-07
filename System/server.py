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

    GET    /api/records              自分の学習記録 {records}（新しい順）
    POST   /api/records              {materialId, durationMin, memo?, locSeries?, source?} → 記録を追加
    POST   /api/records/import       {records:[...]} localStorage に残っていた記録を一度だけ取り込む
    DELETE /api/records              自分の学習記録をすべて消す
    GET    /api/timeline             自分とフォロー中の人の学習記録 {records}

    GET    /api/live                 自分とフォロー中の人の、いま続いている LoC 計測 {sessions}
    PUT    /api/live                 {materialId, status, elapsedSec, loc, recent} 計測中の今の状態（約5秒ごと）
    DELETE /api/live                 計測を終えた（画面を離れた）

    GET    /api/users?q=<文字>        ニックネームでユーザーを探す {users}
    GET    /api/users/<id>           公開プロフィール＋フォロー数＋自分との関係 {user}
    GET    /api/users/<id>/records   その人の学習記録 {records}
    GET    /api/users/<id>/followers フォロワー {users}
    GET    /api/users/<id>/following フォロー中 {users}
    PUT    /api/users/<id>/follow    フォローする（承認なし）{user}
    DELETE /api/users/<id>/follow    フォローをやめる {user}

    GET    /api/badges               未読の数 {notifications, messages}
    GET    /api/notifications        通知 {notifications}（新しい順）
    POST   /api/notifications/read   通知をすべて既読にする

    GET    /api/messages             やりとりのある相手ごとの最新メッセージ {conversations}
    GET    /api/messages/<id>?after=<メッセージID>  相手とのメッセージ {partner, messages}（相手からの分は既読になる）
    POST   /api/messages/<id>        {body} → 送る（相互フォローの相手だけ）{message}

    GET    /api/apply                招待申請の引換券の状態 {email, pending}（Cookie が無効なら 404）
    POST   /api/apply                {message?} → 招待されていない本人が申請する（ログイン不要）
    GET    /api/invite-requests      自分が紹介した申請 {requests}
    POST   /api/invite-requests      {email, message?} → 友だちの招待を申請する

    GET    /api/admin/invite-requests?status=pending   招待申請の一覧 {requests}（管理者だけ）
    POST   /api/admin/invite-requests/<id>/approve     承認して招待を作る（管理者だけ）
    POST   /api/admin/invite-requests/<id>/reject      却下する（管理者だけ）

    GET    /loc                      WebSocket。LoC 推論サーバー（LoC/server.py）へそのまま中継する。
                                     ngrok などで外から開いたときに、ポート1つ（このサーバー）だけ
                                     公開すれば済むようにするため。ログイン中で、同じオリジンの
                                     ページからの接続だけ通す

自分は {id, email, profileCompleted, isAdmin, profile} の形で返す。他の人は公開してよい項目だけ
（social.PUBLIC_PROFILE_KEYS）で、メールアドレスは出さない。
セッションは HttpOnly の Cookie で持つ。書き込み系は Content-Type: application/json を必須にして、
SameSite=Lax と合わせて他サイトのフォームから勝手に送られないようにしている。
Google からの戻りは他サイトからの遷移なので、state の Cookie も SameSite=Lax（Strict だと届かない）。
招待されていない Google アカウントで戻ってきたら、確認済みのメールアドレスの引換券を
/api/apply にだけ送られる Cookie で渡す（申請するメールアドレスを入力させないため）。
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
from .invites import TICKET_TTL_SEC, InviteRequestStore
from .live import LiveStore
from .materials import MaterialStore, decode_image, sniff_image_type
from .records import USER_RECORDS_LIMIT, RecordStore
from .social import SocialStore

SESSION_COOKIE = "onsta_session"
# ログイン途中のブラウザを見分ける Cookie（中身は state）。Google まわりのパスにだけ送らせる
OAUTH_COOKIE = "onsta_oauth_state"
OAUTH_COOKIE_PATH = "/api/auth/google"
# 招待申請の引換券（中身はトークン）。申請の API にだけ送らせる
APPLY_COOKIE = "onsta_apply"
APPLY_COOKIE_PATH = "/api/apply"
MAX_BODY_BYTES = 16 * 1024
# 画像を data URL（base64 で 4/3 倍）で受け取るので、教材の追加だけ大きめに
MAX_MATERIAL_BODY_BYTES = 3 * 1024 * 1024
# localStorage の記録をまとめて取り込むときだけ大きめに
MAX_IMPORT_BODY_BYTES = 2 * 1024 * 1024
# 推論サーバーへ中継しないヘッダー（Cookie は推論サーバーに渡す必要がない）
LOC_DROP_HEADERS = {"host", "cookie"}
CRLF = "\r\n"


class AppHandler(http.server.SimpleHTTPRequestHandler):
    # make_handler() で差し込む
    store: AuthStore
    materials: MaterialStore
    books: BookLookup
    google: GoogleOAuth
    social: SocialStore
    records: RecordStore
    invites: InviteRequestStore
    live: LiveStore
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
        ("GET", r"/api/records", "_list_records"),
        ("POST", r"/api/records", "_add_record"),
        ("POST", r"/api/records/import", "_import_records"),
        ("DELETE", r"/api/records", "_delete_records"),
        ("GET", r"/api/timeline", "_timeline"),
        ("GET", r"/api/live", "_list_live"),
        ("PUT", r"/api/live", "_update_live"),
        ("DELETE", r"/api/live", "_end_live"),
        ("GET", r"/api/users", "_search_users"),
        ("GET", r"/api/users/(\d+)", "_get_user"),
        ("GET", r"/api/users/(\d+)/records", "_user_records"),
        ("GET", r"/api/users/(\d+)/followers", "_followers"),
        ("GET", r"/api/users/(\d+)/following", "_following"),
        ("PUT", r"/api/users/(\d+)/follow", "_follow"),
        ("DELETE", r"/api/users/(\d+)/follow", "_unfollow"),
        ("GET", r"/api/badges", "_badges"),
        ("GET", r"/api/notifications", "_list_notifications"),
        ("POST", r"/api/notifications/read", "_read_notifications"),
        ("GET", r"/api/messages", "_list_conversations"),
        ("GET", r"/api/messages/(\d+)", "_get_thread"),
        ("POST", r"/api/messages/(\d+)", "_send_message"),
        ("GET", r"/api/apply", "_apply_status"),
        ("POST", r"/api/apply", "_apply"),
        ("GET", r"/api/invite-requests", "_list_my_requests"),
        ("POST", r"/api/invite-requests", "_refer"),
        ("GET", r"/api/admin/invite-requests", "_admin_list_requests"),
        ("POST", r"/api/admin/invite-requests/(\d+)/(approve|reject)", "_admin_decide"),
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
            try:
                user = self.store.login_with_google(identity.sub, identity.email)
            except NotInvitedError:
                # Google が確認したメールアドレスで招待申請できるよう、引換券を渡しておく
                ticket = self.invites.create_ticket(identity.email)
                self.log_message("招待されていない Google アカウントのログインを拒否しました")
                self._redirect("/#/login?error=not_invited", [
                    clear_state, self._cookie_header(APPLY_COOKIE, ticket, TICKET_TTL_SEC, APPLY_COOKIE_PATH)])
                return
        except OAuthFailure as err:
            if err.detail:
                self.log_message("Google ログインに失敗: %s", err.detail)
            self._redirect(f"/#/login?error={err.code}", [clear_state])
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
        # 自分の教材か、フォロー中の人の教材（タイムラインの記録に出る）だけ見られる
        owner = self.materials.owner_of(material_id)
        if owner is None or (owner != user["id"] and not self.social.is_following(user["id"], owner)):
            raise AuthError(404, "画像がありません")
        data, image_type = self.materials.get_image(owner, material_id)
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

    # ---- 学習記録 ----

    def _list_records(self) -> None:
        user = self._require_user()
        self._send_json(200, {"records": self.records.list_for_user(user["id"])})

    def _add_record(self) -> None:
        user = self._require_user()
        self._send_json(201, {"record": self.records.add_record(user["id"], self._read_json())})

    def _import_records(self) -> None:
        user = self._require_user()
        body = self._read_json(MAX_IMPORT_BODY_BYTES)
        self._send_json(200, self.records.import_records(user["id"], body.get("records")))

    def _delete_records(self) -> None:
        user = self._require_user()
        self.records.delete_all(user["id"])
        self._send_json(200, {"ok": True})

    def _timeline(self) -> None:
        user = self._require_user()
        self._send_json(200, {"records": self.records.timeline(user["id"])})

    # ---- いま勉強中 ----

    def _list_live(self) -> None:
        user = self._require_user()
        self._send_json(200, {"sessions": self.live.list_for(user["id"])})

    def _update_live(self) -> None:
        user = self._require_user()
        self.live.update(user["id"], self._read_json())
        self._send_json(200, {"ok": True})

    def _end_live(self) -> None:
        user = self._require_user()
        self.live.end(user["id"])
        self._send_json(200, {"ok": True})

    # ---- ユーザー・フォロー ----

    def _search_users(self) -> None:
        user = self._require_user()
        self._send_json(200, {"users": self.social.search_users(user["id"], self._query_param("q"))})

    def _get_user(self, user_id: int) -> None:
        user = self._require_user()
        self._send_json(200, {"user": self.social.get_user(user["id"], user_id)})

    def _user_records(self, user_id: int) -> None:
        user = self._require_user()
        target = self.social.get_user(user["id"], user_id)  # 見えない（初回設定前など）ユーザーなら 404
        records = self.records.list_for_user(user_id, limit=USER_RECORDS_LIMIT)
        # 教材の画像はフォロー中の人にだけ見せるので、それ以外には画像なしとして渡す
        if not (target["isMe"] or target["isFollowing"]):
            for record in records:
                if record["material"]:
                    record["material"]["hasImage"] = False
        self._send_json(200, {"records": records})

    def _followers(self, user_id: int) -> None:
        user = self._require_user()
        self._send_json(200, {"users": self.social.list_followers(user["id"], user_id)})

    def _following(self, user_id: int) -> None:
        user = self._require_user()
        self._send_json(200, {"users": self.social.list_following(user["id"], user_id)})

    def _follow(self, user_id: int) -> None:
        user = self._require_user()
        self._read_json()
        self._send_json(200, {"user": self.social.follow(user["id"], user_id)})

    def _unfollow(self, user_id: int) -> None:
        user = self._require_user()
        self._send_json(200, {"user": self.social.unfollow(user["id"], user_id)})

    # ---- 通知 ----

    def _badges(self) -> None:
        user = self._require_user()
        self._send_json(200, self.social.badges(user["id"]))

    def _list_notifications(self) -> None:
        user = self._require_user()
        self._send_json(200, {"notifications": self.social.list_notifications(user["id"])})

    def _read_notifications(self) -> None:
        user = self._require_user()
        self._read_json()
        self.social.mark_notifications_read(user["id"])
        self._send_json(200, {"ok": True})

    # ---- メッセージ ----

    def _list_conversations(self) -> None:
        user = self._require_user()
        self._send_json(200, {"conversations": self.social.list_conversations(user["id"])})

    def _get_thread(self, partner_id: int) -> None:
        user = self._require_user()
        after = self._query_param("after") or "0"
        if not after.isdigit() or len(after) > 18:
            raise AuthError(400, "after の指定が正しくありません")
        self._send_json(200, self.social.get_thread(user["id"], partner_id, int(after)))

    def _send_message(self, partner_id: int) -> None:
        user = self._require_user()
        body = self._read_json()
        self._send_json(201, {"message": self.social.send_message(user["id"], partner_id, body.get("body"))})

    # ---- 招待申請 ----

    def _apply_status(self) -> None:
        self._send_json(200, self.invites.ticket_status(self._cookie(APPLY_COOKIE)))

    def _apply(self) -> None:
        body = self._read_json()
        request = self.invites.apply_with_ticket(self._cookie(APPLY_COOKIE), body.get("message"))
        self._send_json(201, {"request": request}, extra_cookies=[
            self._cookie_header(APPLY_COOKIE, "", 0, APPLY_COOKIE_PATH)])

    def _list_my_requests(self) -> None:
        user = self._require_user()
        self._send_json(200, {"requests": self.invites.list_mine(user["id"])})

    def _refer(self) -> None:
        user = self._require_user()
        body = self._read_json()
        self._send_json(201, {"request": self.invites.refer(user["id"], body.get("email"), body.get("message"))})

    def _admin_list_requests(self) -> None:
        self._require_admin()
        self._send_json(200, {"requests": self.invites.list_requests(self._query_param("status") or None)})

    def _admin_decide(self, request_id: int, action: str) -> None:
        admin = self._require_admin()
        self._read_json()
        decide = self.invites.approve if action == "approve" else self.invites.reject
        self._send_json(200, {"request": decide(admin["id"], request_id)})

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

    def _require_admin(self) -> Dict[str, Any]:
        user = self._require_user()
        if not user["isAdmin"]:
            raise AuthError(403, "管理者だけが使えます")
        return user

    def _query_param(self, name: str) -> Optional[str]:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        values = query.get(name)
        return values[0] if values else None

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

    def _send_json(self, status: int, payload: Dict[str, Any], session: Optional[str] = None,
                   extra_cookies: Iterable[str] = ()) -> None:
        """session に文字列を渡すと Cookie を発行する（"" なら削除）。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if session is not None:
            self.send_header("Set-Cookie", self._session_cookie_header(session))
        for cookie in extra_cookies:
            self.send_header("Set-Cookie", cookie)
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
                loc_upstream: Optional[Tuple[str, int]] = None,
                social: Optional[SocialStore] = None,
                records: Optional[RecordStore] = None,
                invites: Optional[InviteRequestStore] = None,
                live: Optional[LiveStore] = None) -> http.server.ThreadingHTTPServer:
    """social / records / invites / live を省略すると、store と同じ DB ファイルで作る。"""
    handler_cls = type("BoundAppHandler", (AppHandler,), {
        "store": store,
        "materials": materials,
        "books": books,
        "google": google,
        "social": social or SocialStore(store.db_path),
        "records": records or RecordStore(store.db_path),
        "invites": invites or InviteRequestStore(store.db_path),
        "live": live or LiveStore(store.db_path),
        "secure_cookie": secure_cookie,
        "loc_upstream": loc_upstream,
    })
    handler = functools.partial(handler_cls, directory=str(static_dir))
    return http.server.ThreadingHTTPServer((host, port), handler)
