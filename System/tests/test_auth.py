"""認証ストア（auth.py）、Google ログイン（google_oauth.py）、認証 API（server.py）を検証する。"""

import base64
import hashlib
import json
import socket
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

import pytest

from System import admin
from System.auth import (
    AuthError, AuthStore, NotInvitedError, migrate_from_password_auth, normalize_profile,
)
from System.books import BookLookup
from System.google_oauth import FLOW_TTL_SEC, MAX_PENDING_FLOWS, TOKEN_ENDPOINT, GoogleOAuth, OAuthFailure
from System.materials import MaterialStore
from System.server import make_server
from System.tests.helpers import create_user

CLIENT_ID = "client-id.apps.googleusercontent.com"
NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path):
    return AuthStore(tmp_path / "test.db")


# ============ Google の偽物 ============

def make_id_token(claims):
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


class FakeGoogle:
    """トークンエンドポイントの代わり。ログインするアカウントを identity で決める。"""

    def __init__(self):
        self.requests = []
        self.nonce = ""
        self.identity = {"sub": "sub-taro", "email": "taro@example.com", "email_verified": True}
        self.overrides = {}
        self.raw_response = None

    def post_form(self, url, fields):
        self.requests.append((url, fields))
        if isinstance(self.raw_response, Exception):
            raise self.raw_response
        if self.raw_response is not None:
            return self.raw_response
        return {"access_token": "must-not-be-kept", "id_token": make_id_token({
            "iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": NOW + 3600,
            "nonce": self.nonce, **self.identity, **self.overrides,
        })}

    def remember_nonce(self, auth_url):
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(auth_url).query))
        self.nonce = params["nonce"]
        return params


@pytest.fixture
def fake():
    return FakeGoogle()


@pytest.fixture
def oauth(fake):
    return GoogleOAuth(CLIENT_ID, "client-secret", "http://localhost:8000/",
                       post_form=fake.post_form, clock=lambda: NOW)


# ============ Google ログイン（OpenID Connect の検証） ============

def test_authorization_url_has_security_parameters(oauth):
    state, url = oauth.begin()
    parts = urllib.parse.urlsplit(url)
    params = dict(urllib.parse.parse_qsl(parts.query))
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert params["client_id"] == CLIENT_ID
    assert params["response_type"] == "code"
    assert params["scope"] == "openid email"  # 名前や写真は求めない
    assert params["redirect_uri"] == "http://localhost:8000/api/auth/google/callback"
    assert params["state"] == state
    assert params["nonce"] and params["nonce"] != state
    assert params["code_challenge_method"] == "S256"
    assert "client-secret" not in url


def test_code_exchange_sends_pkce_verifier(oauth, fake):
    state, url = oauth.begin()
    params = fake.remember_nonce(url)
    identity = oauth.finish(state, "the-code")
    assert identity == ("sub-taro", "taro@example.com")

    endpoint, fields = fake.requests[0]
    assert endpoint == TOKEN_ENDPOINT
    assert fields["code"] == "the-code"
    assert fields["client_secret"] == "client-secret"
    assert fields["grant_type"] == "authorization_code"
    assert fields["redirect_uri"] == params["redirect_uri"]
    digest = hashlib.sha256(fields["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == params["code_challenge"]


def test_state_can_only_be_used_once(oauth, fake):
    state, url = oauth.begin()
    fake.remember_nonce(url)
    oauth.finish(state, "code")
    with pytest.raises(OAuthFailure) as err:
        oauth.finish(state, "code")
    assert err.value.code == "expired"
    assert len(fake.requests) == 1


def test_unknown_state_never_reaches_google(oauth, fake):
    with pytest.raises(OAuthFailure) as err:
        oauth.finish("made-up-state", "code")
    assert err.value.code == "expired"
    assert fake.requests == []


def test_state_expires(fake):
    now = [NOW]
    oauth = GoogleOAuth(CLIENT_ID, "s", "http://localhost:8000", post_form=fake.post_form, clock=lambda: now[0])
    state, _ = oauth.begin()
    now[0] += FLOW_TTL_SEC + 1
    with pytest.raises(OAuthFailure) as err:
        oauth.finish(state, "code")
    assert err.value.code == "expired"
    assert fake.requests == []


def test_pending_flows_are_bounded(oauth):
    first, _ = oauth.begin()
    for _ in range(MAX_PENDING_FLOWS + 10):
        oauth.begin()
    assert len(oauth._flows) == MAX_PENDING_FLOWS
    assert first not in oauth._flows


def test_error_from_google_consumes_state(oauth, fake):
    state, _ = oauth.begin()
    with pytest.raises(OAuthFailure) as err:
        oauth.finish(state, "", "access_denied")
    assert err.value.code == "cancelled"
    assert fake.requests == []
    with pytest.raises(OAuthFailure) as err:
        oauth.finish(state, "code")
    assert err.value.code == "expired"


@pytest.mark.parametrize("overrides,code", [
    ({"iss": "https://evil.example"}, "failed"),
    ({"iss": None}, "failed"),
    ({"aud": "someone-elses-client"}, "failed"),
    ({"aud": [CLIENT_ID, "other"]}, "failed"),
    ({"exp": NOW - 1}, "failed"),
    ({"exp": "9999999999"}, "failed"),
    ({"nonce": "replayed-nonce"}, "failed"),
    ({"nonce": None}, "failed"),
    ({"sub": ""}, "failed"),
    ({"sub": None}, "failed"),
    ({"email": None}, "failed"),
    ({"email_verified": False}, "unverified"),
    ({"email_verified": "true"}, "unverified"),
    ({"email_verified": None}, "unverified"),
])
def test_bad_id_token_is_rejected(oauth, fake, overrides, code):
    state, url = oauth.begin()
    fake.remember_nonce(url)
    fake.overrides = overrides
    with pytest.raises(OAuthFailure) as err:
        oauth.finish(state, "code")
    assert err.value.code == code


@pytest.mark.parametrize("response", [
    {},
    [],
    {"id_token": 123},
    {"id_token": "not-a-jwt"},
    {"id_token": "a.!!!.c"},
    {"id_token": "a.WzEsIDJd.c"},  # ペイロードがオブジェクトでない
    OSError("network down"),
    ValueError("not json"),
    urllib.error.HTTPError(TOKEN_ENDPOINT, 400, "Bad Request", {}, None),
])
def test_token_endpoint_failures(oauth, fake, response):
    state, url = oauth.begin()
    fake.remember_nonce(url)
    fake.raw_response = response
    with pytest.raises(OAuthFailure) as err:
        oauth.finish(state, "code")
    assert err.value.code == "failed"


# ============ 招待制の登録 ============

def count_users(store):
    return len(store.list_users())


def test_uninvited_account_cannot_register(store):
    with pytest.raises(NotInvitedError):
        store.login_with_google("sub-x", "stranger@example.com")
    assert count_users(store) == 0


def test_invited_account_registers_and_logs_in_again(store):
    assert store.invite("  Taro@Example.COM ") == "taro@example.com"
    user = store.login_with_google("sub-taro", "taro@example.com")
    assert user["email"] == "taro@example.com"
    assert user["profileCompleted"] is False
    # 招待は消費されるが、登録済みなので sub で入れる
    assert store.list_invitations() == []
    assert store.login_with_google("sub-taro", "taro@example.com")["id"] == user["id"]
    assert count_users(store) == 1


def test_email_is_matched_case_insensitively(store):
    store.invite("taro@example.com")
    assert store.login_with_google("sub-taro", "TARO@example.com")["email"] == "taro@example.com"


def test_identity_is_sub_not_email(store):
    """Google 側でメールアドレスを変えても同じアカウント。"""
    user = create_user(store, "taro")
    changed = store.login_with_google("sub-taro", "new-address@example.com")
    assert changed["id"] == user["id"]
    assert changed["email"] == "new-address@example.com"


def test_used_invitation_cannot_be_reused_by_another_google_account(store):
    """同じメールアドレスが別の Google アカウント（別の sub）に渡っても登録できない。"""
    create_user(store, "taro")
    with pytest.raises(NotInvitedError):
        store.login_with_google("sub-someone-else", "taro@example.com")
    assert count_users(store) == 1


def test_concurrent_first_logins_consume_one_invitation(store):
    store.invite("taro@example.com")
    results, barrier = [], threading.Barrier(8)

    def attempt(sub):
        barrier.wait()
        try:
            results.append(store.login_with_google(sub, "taro@example.com")["id"])
        except NotInvitedError:
            results.append(None)

    threads = [threading.Thread(target=attempt, args=(f"sub-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1
    assert count_users(store) == 1


def test_uninvite(store):
    store.invite("taro@example.com")
    assert store.uninvite("TARO@example.com") is True
    assert store.uninvite("taro@example.com") is False
    with pytest.raises(NotInvitedError):
        store.login_with_google("sub-taro", "taro@example.com")


@pytest.mark.parametrize("email", ["", "taro", "taro@", "@example.com", "ta ro@example.com",
                                   "a@b@example.com", "taro@example", None, 123,
                                   "a" * 250 + "@example.com"])
def test_invite_validates_email(store, email):
    with pytest.raises(AuthError) as err:
        store.invite(email)
    assert err.value.status == 400


def test_cannot_invite_registered_email(store):
    create_user(store, "taro")
    with pytest.raises(AuthError) as err:
        store.invite("taro@example.com")
    assert err.value.status == 409


# ============ パスワード認証だった頃の DB の移行 ============

OLD_SCHEMA = """
CREATE TABLE users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    login_id          TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash     TEXT    NOT NULL,
    profile           TEXT    NOT NULL DEFAULT '{}',
    profile_completed INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL
);
CREATE TABLE sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL    NOT NULL,
    expires_at REAL    NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);
"""


@pytest.fixture
def old_db(tmp_path):
    """旧スキーマの DB。教材とカテゴリは今と同じスキーマなので MaterialStore で作る。"""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    for user_id, login_id, profile in [
        (1, "victim@example.com", '{"nickname": "たろう"}'),
        (2, "NBoy", '{"nickname": "じろう"}'),
        (7, "seven", "{}"),
    ]:
        conn.execute(
            "INSERT INTO users (id, login_id, password_hash, profile, profile_completed, created_at)"
            " VALUES (?, ?, ?, ?, 1, '2026-01-01T00:00:00+00:00')",
            (user_id, login_id, "pbkdf2_sha256$600000$00$" + "ab" * 32, profile))
    conn.execute("INSERT INTO sessions VALUES ('old-session-hash', 1, 0, 9999999999)")
    conn.commit()
    conn.close()

    materials = MaterialStore(path)
    conn = sqlite3.connect(path)  # 外部キーを有効にせず、旧 users に対して入れる
    conn.execute("INSERT INTO categories (id, user_id, name, color, created_at)"
                 " VALUES (1, 1, '数学', '#5b5bd6', 'x')")
    conn.execute("INSERT INTO materials (id, user_id, category_id, title, created_at)"
                 " VALUES (1, 1, 1, '青チャート', 'x')")
    conn.commit()
    conn.close()
    return path, materials


def test_migration_removes_passwords_and_sessions(old_db):
    path, _ = old_db
    assert b"pbkdf2_sha256" in path.read_bytes()
    # AuthStore を通すと新しいテーブルが空きページを埋めて上書きしてしまうので、移行だけを実行して見る
    assert migrate_from_password_auth(path) is True

    # ファイルの中身にもハッシュを残さない（DROP しただけだと空きページに残る）
    assert b"pbkdf2_sha256" not in path.read_bytes()
    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    assert "password_hash" not in columns and "login_id" not in columns
    # 古いセッションの Cookie では入れない
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    conn.close()


def test_migration_keeps_user_data(old_db):
    path, materials = old_db
    store = AuthStore(path)
    users = {u["id"]: u for u in store.list_users()}
    assert sorted(users) == [1, 2, 7]
    assert users[1]["nickname"] == "たろう"
    assert users[2]["legacyLoginId"] == "NBoy"
    assert not users[1]["linked"]
    assert store.get_user(1)["profileCompleted"] is True
    # DROP TABLE users で教材が連鎖削除されていない
    listed = materials.list_for_user(1)
    assert [m["title"] for m in listed["materials"]] == ["青チャート"]
    assert [c["name"] for c in listed["categories"]] == ["数学"]


def test_migration_is_idempotent_and_keeps_links(old_db):
    path, _ = old_db
    store = AuthStore(path)
    store.invite("taro@gmail.com", user_id=1)
    store.login_with_google("sub-taro", "taro@gmail.com")

    again = AuthStore(path)
    assert again.login_with_google("sub-taro", "taro@gmail.com")["id"] == 1


def test_migrated_db_keeps_id_sequence_and_foreign_keys(old_db):
    path, materials = old_db
    store = AuthStore(path)
    new_user = create_user(store, "newcomer")
    assert new_user["id"] == 8  # 旧ユーザーの ID を使い回さない（localStorage のキーになっている）

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO categories (user_id, name, color, created_at) VALUES (999, 'x', '#000000', 'x')")
    conn.close()
    assert materials.add_category(new_user["id"], "英語", "#5b5bd6")["name"] == "英語"


def test_legacy_account_is_never_linked_by_matching_login_id(old_db):
    """ログインIDはメール確認をしていないので、同じ文字列のメールでは旧アカウントに入れない。"""
    path, materials = old_db
    store = AuthStore(path)
    # 招待が無ければ登録も結び付けも起きない
    with pytest.raises(NotInvitedError):
        store.login_with_google("sub-real-owner", "victim@example.com")
    # user_id なしの招待なら、新しい別のアカウントになる
    store.invite("victim@example.com")
    user = store.login_with_google("sub-real-owner", "victim@example.com")
    assert user["id"] != 1
    assert materials.list_for_user(user["id"])["materials"] == []


def test_legacy_account_is_linked_explicitly(old_db):
    path, materials = old_db
    store = AuthStore(path)
    store.invite("taro@gmail.com", user_id=1)
    user = store.login_with_google("sub-taro", "taro@gmail.com")
    assert user["id"] == 1
    assert user["profile"]["nickname"] == "たろう"
    assert materials.list_for_user(1)["materials"][0]["title"] == "青チャート"
    linked = next(u for u in store.list_users() if u["id"] == 1)
    assert linked["linked"] and linked["legacyLoginId"] is None
    assert store.list_invitations() == []


def test_invite_for_link_validates_target(old_db):
    path, _ = old_db
    store = AuthStore(path)
    with pytest.raises(AuthError) as err:
        store.invite("taro@gmail.com", user_id=999)
    assert err.value.status == 404

    store.invite("taro@gmail.com", user_id=1)
    store.login_with_google("sub-taro", "taro@gmail.com")
    # 結び付け済みのアカウントを別の Google アカウントに付け替えられない
    with pytest.raises(AuthError) as err:
        store.invite("other@gmail.com", user_id=1)
    assert err.value.status == 409


def test_only_latest_invitation_links_a_legacy_account(old_db):
    path, _ = old_db
    store = AuthStore(path)
    store.invite("typo@gmail.com", user_id=1)
    store.invite("taro@gmail.com", user_id=1)
    assert [i["email"] for i in store.list_invitations()] == ["taro@gmail.com"]
    with pytest.raises(NotInvitedError):
        store.login_with_google("sub-typo", "typo@gmail.com")


# ============ 招待の CLI ============

def test_admin_cli(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    assert admin.main(["--db", db, "invite", "taro@example.com"]) == 0
    assert admin.main(["--db", db, "list"]) == 0
    assert "taro@example.com" in capsys.readouterr().out

    assert admin.main(["--db", db, "uninvite", "taro@example.com"]) == 0
    assert admin.main(["--db", db, "uninvite", "taro@example.com"]) == 1
    assert admin.main(["--db", db, "invite", "not-an-email"]) == 1
    assert admin.main(["--db", db, "invite", "taro@example.com", "--user", "42"]) == 1
    assert AuthStore(tmp_path / "t.db").list_invitations() == []


def test_admin_cli_lists_unlinked_accounts(old_db, capsys):
    path, _ = old_db
    assert admin.main(["--db", str(path), "list"]) == 0
    out = capsys.readouterr().out
    assert "NBoy" in out and "じろう" in out
    assert "pbkdf2" not in out


# ============ セッション ============

def test_session_lifecycle(tmp_path):
    now = [1000.0]
    store = AuthStore(tmp_path / "t.db", session_ttl=60, clock=lambda: now[0])
    user = create_user(store, "taro")
    token = store.create_session(user["id"])
    assert store.user_for_session(token)["id"] == user["id"]
    assert store.user_for_session("not-a-token") is None
    assert store.user_for_session(None) is None

    now[0] += 61
    assert store.user_for_session(token) is None

    token = store.create_session(user["id"])
    store.delete_session(token)
    assert store.user_for_session(token) is None


# ============ プロフィール ============

def test_profile_only_nickname_required(store):
    user = create_user(store, "taro")
    updated = store.update_profile(user["id"], {"nickname": "  たろう  "})
    assert updated["profileCompleted"] is True
    assert updated["profile"]["nickname"] == "たろう"
    assert updated["profile"]["tags"] == []
    assert updated["profile"]["birthYear"] is None


def test_profile_full(store):
    user = create_user(store, "taro")
    profile = store.update_profile(user["id"], {
        "nickname": "たろう",
        "avatarColor": "#E8724C",
        "bio": "数学がんばる",
        "tags": ["#理系", "数学", "数学", " "],
        "grade": "高校3年生",
        "targetSchool": "東京大学",
        "examName": "共通テスト",
        "examDate": "2027-01-16",
        "weeklyGoalMinutes": 1800,
        "gender": "回答しない",
        "birthYear": 2008,
        "prefecture": "東京都",
        "unknownField": "捨てられる",
    })["profile"]
    assert profile["avatarColor"] == "#e8724c"
    assert profile["tags"] == ["理系", "数学"]
    assert profile["weeklyGoalMinutes"] == 1800
    assert "unknownField" not in profile


@pytest.mark.parametrize("patch,field", [
    ({"nickname": ""}, "nickname"),
    ({"nickname": "あ" * 21}, "nickname"),
    ({"avatarColor": "red; background:url(x)"}, "avatarColor"),
    ({"examDate": "2027-02-30"}, "examDate"),
    ({"birthYear": 1800}, "birthYear"),
    ({"birthYear": "2008"}, "birthYear"),
    ({"weeklyGoalMinutes": 1.5}, "weeklyGoalMinutes"),
    ({"tags": [str(i) for i in range(11)]}, "tags"),
])
def test_profile_validation(patch, field):
    with pytest.raises(AuthError) as err:
        normalize_profile({"nickname": "たろう", **patch})
    assert err.value.field == field


# ============ HTTP API ============

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class App:
    def __init__(self, tmp_path, secure_cookie=False):
        static = tmp_path / "static"
        static.mkdir(exist_ok=True)
        (static / "index.html").write_text("<p>ok</p>", encoding="utf-8")
        db = tmp_path / "t.db"
        port = free_port()
        self.base = f"http://127.0.0.1:{port}"
        self.fake = FakeGoogle()
        self.store = AuthStore(db)
        self.google = GoogleOAuth(CLIENT_ID, "client-secret", self.base, post_form=self.fake.post_form,
                                  clock=lambda: NOW)
        self.httpd = make_server("127.0.0.1", port, static, self.store, MaterialStore(db), BookLookup(),
                                 self.google, secure_cookie=secure_cookie)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def app(tmp_path):
    instance = App(tmp_path)
    yield instance
    instance.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Response:
    def __init__(self, status, headers, raw):
        self.status = status
        self.headers = headers
        self.raw = raw

    @property
    def json(self):
        return json.loads(self.raw)

    @property
    def location(self):
        return self.headers["Location"]

    @property
    def cookies(self):
        return self.headers.get_all("Set-Cookie") or []


class Browser:
    """Cookie を持ち、リダイレクトは追わないブラウザ。"""

    def __init__(self, app):
        self.app = app
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None, content_type="application/json", headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.app.base + path, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", content_type)
        try:
            with self.opener.open(req) as res:
                return Response(res.status, res.headers, res.read())
        except urllib.error.HTTPError as err:
            return Response(err.code, err.headers, err.read())

    def start(self):
        """ログイン開始。Google の同意画面の URL のパラメータを返す。"""
        res = self.call("GET", "/api/auth/google/start")
        assert res.status == 303 and res.location.startswith("https://accounts.google.com/")
        return self.app.fake.remember_nonce(res.location)

    def come_back(self, **params):
        return self.call("GET", "/api/auth/google/callback?" + urllib.parse.urlencode(params))

    def login(self):
        params = self.start()
        return self.come_back(state=params["state"], code="the-code")

    def cookie_names(self):
        return {c.name for c in self.jar}


def test_login_flow_with_invited_account(app):
    browser = Browser(app)
    assert browser.call("GET", "/api/auth/me").status == 401

    app.store.invite("taro@example.com")
    res = browser.login()
    assert res.status == 303 and res.location == "/"
    assert browser.cookie_names() == {"onsta_session"}  # state の Cookie は消えている

    me = browser.call("GET", "/api/auth/me").json["user"]
    assert me["email"] == "taro@example.com"
    assert me["profileCompleted"] is False
    assert set(me) == {"id", "email", "profileCompleted", "profile"}

    res = browser.call("PUT", "/api/me/profile", {"profile": {"nickname": "たろう"}})
    assert res.status == 200 and res.json["user"]["profile"]["nickname"] == "たろう"

    assert browser.call("POST", "/api/auth/logout", {}).status == 200
    assert browser.call("GET", "/api/auth/me").status == 401

    # 招待は使い切っているが、登録済みなのでまた入れる
    assert browser.login().location == "/"
    assert browser.call("GET", "/api/auth/me").json["user"]["profile"]["nickname"] == "たろう"


def test_uninvited_account_gets_no_session(app):
    browser = Browser(app)
    res = browser.login()
    assert res.status == 303 and res.location == "/#/login?error=not_invited"
    assert browser.cookie_names() == set()
    assert browser.call("GET", "/api/auth/me").status == 401
    assert app.store.list_users() == []


def test_callback_from_another_browser_is_rejected(app):
    """攻撃者が自分のログインの戻り URL を被害者に踏ませても（login CSRF）、被害者はログインされない。"""
    app.store.invite("taro@example.com")
    attacker, victim = Browser(app), Browser(app)
    params = attacker.start()
    res = victim.come_back(state=params["state"], code="attackers-code")
    assert res.location == "/#/login?error=expired"
    assert "onsta_session" not in victim.cookie_names()
    assert app.fake.requests == []  # コードの交換までいかない


@pytest.mark.parametrize("state", ["wrong-state", "", "日本語の state"])
def test_callback_with_wrong_state_is_rejected(app, state):
    app.store.invite("taro@example.com")
    browser = Browser(app)
    browser.start()
    res = browser.come_back(state=state, code="code")
    assert res.location == "/#/login?error=expired"
    assert app.fake.requests == []
    assert app.store.list_invitations() != []


def test_replayed_callback_is_rejected(app):
    app.store.invite("taro@example.com")
    browser = Browser(app)
    params = browser.start()
    browser.come_back(state=params["state"], code="code")
    state_cookie = f"onsta_oauth_state={params['state']}"
    other = Browser(app)
    res = other.call("GET", "/api/auth/google/callback?" + urllib.parse.urlencode(
        {"state": params["state"], "code": "code"}), headers={"Cookie": state_cookie})
    assert res.location == "/#/login?error=expired"
    assert len(app.fake.requests) == 1


def test_user_cancelling_at_google(app):
    browser = Browser(app)
    params = browser.start()
    res = browser.come_back(state=params["state"], error="access_denied")
    assert res.location == "/#/login?error=cancelled"
    assert browser.cookie_names() == set()


def test_failure_redirects_use_fixed_codes(app):
    """Google から来た文字列は画面に渡さない。"""
    browser = Browser(app)
    params = browser.start()
    res = browser.come_back(state=params["state"], error="<script>alert(1)</script>")
    assert res.location == "/#/login?error=failed"


def test_unverified_email_is_rejected(app):
    app.store.invite("taro@example.com")
    app.fake.overrides = {"email_verified": False}
    browser = Browser(app)
    assert browser.login().location == "/#/login?error=unverified"
    assert app.store.list_users() == []
    assert app.store.list_invitations() != []  # 招待は消費されない


def test_malformed_email_from_google_is_a_login_failure(app):
    app.fake.identity = {**app.fake.identity, "email": "not-an-email"}
    browser = Browser(app)
    assert browser.login().location == "/#/login?error=failed"
    assert browser.cookie_names() == set()


def test_non_ascii_session_cookie_is_just_not_logged_in(app):
    res = Browser(app).call("GET", "/api/auth/me", headers={"Cookie": "onsta_session=éé"})
    assert res.status == 401


def test_login_rotates_session(app):
    app.store.invite("taro@example.com")
    browser = Browser(app)
    browser.login()
    old = next(c.value for c in browser.jar if c.name == "onsta_session")
    browser.login()
    new = next(c.value for c in browser.jar if c.name == "onsta_session")
    assert new != old
    assert app.store.user_for_session(old) is None
    assert app.store.user_for_session(new) is not None


def test_cookies_are_hardened(app):
    browser = Browser(app)
    app.store.invite("taro@example.com")
    res = browser.call("GET", "/api/auth/google/start")
    state_cookie = res.cookies[0]
    assert state_cookie.startswith("onsta_oauth_state=")
    assert "HttpOnly" in state_cookie and "SameSite=Lax" in state_cookie
    assert "Path=/api/auth/google" in state_cookie and "Max-Age=600" in state_cookie
    assert "Secure" not in state_cookie

    params = app.fake.remember_nonce(res.location)
    res = browser.come_back(state=params["state"], code="code")
    session_cookie = next(c for c in res.cookies if c.startswith("onsta_session="))
    assert "HttpOnly" in session_cookie and "SameSite=Lax" in session_cookie
    assert res.headers["Referrer-Policy"] == "no-referrer"
    assert res.headers["Cache-Control"] == "no-store"


def test_cookies_are_secure_on_https(tmp_path):
    app = App(tmp_path, secure_cookie=True)
    try:
        res = Browser(app).call("GET", "/api/auth/google/start")
        assert all("; Secure" in c for c in res.cookies) and res.cookies
    finally:
        app.close()


def test_start_redirects_other_hosts_to_public_url(app):
    """redirect_uri のホストと違う名前で開かれていたら、Cookie を付ける前に公開 URL へ寄せる。"""
    browser = Browser(app)
    res = browser.call("GET", "/api/auth/google/start", headers={"Host": "evil.example"})
    assert res.status == 303
    assert res.location == app.base + "/api/auth/google/start"
    assert res.cookies == []


def test_authorization_code_is_not_logged(app, capsys):
    app.store.invite("taro@example.com")
    browser = Browser(app)
    params = browser.start()
    browser.come_back(state=params["state"], code="SECRET-AUTH-CODE")
    logged = capsys.readouterr().err
    assert "/api/auth/google/callback" in logged
    assert "SECRET-AUTH-CODE" not in logged
    assert params["state"] not in logged


def test_token_exchange_failure_is_logged_without_secrets(app, capsys):
    app.fake.raw_response = OSError("connection refused")
    browser = Browser(app)
    assert browser.login().location == "/#/login?error=failed"
    logged = capsys.readouterr().err
    assert "トークン交換に失敗" in logged
    assert "client-secret" not in logged


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/auth/register"),
    ("POST", "/api/auth/login"),
])
def test_password_endpoints_are_gone(app, method, path):
    res = Browser(app).call(method, path, {"loginId": "taro", "password": "secret123"})
    assert res.status == 404
    assert app.store.list_users() == []


def test_no_invitation_api_exists(app):
    """招待は CLI からだけ。Web からは自分を招待できない。"""
    app.store.invite("taro@example.com")
    browser = Browser(app)
    browser.login()
    for path in ("/api/invitations", "/api/admin/invite", "/api/auth/invite"):
        assert browser.call("POST", path, {"email": "x@example.com"}).status == 404
    assert app.store.list_invitations() == []


def test_api_errors(app):
    browser = Browser(app)
    # フォーム送信（JSON 以外）は受け付けない
    assert browser.call("POST", "/api/auth/logout", {}, content_type="text/plain").status == 415
    # 未ログインでプロフィールは保存できない
    assert browser.call("PUT", "/api/me/profile", {"profile": {"nickname": "a"}}).status == 401
    assert browser.call("GET", "/api/nothing").status == 404


def test_static_files_are_served(app):
    with urllib.request.urlopen(app.base + "/index.html") as res:
        assert b"ok" in res.read()
