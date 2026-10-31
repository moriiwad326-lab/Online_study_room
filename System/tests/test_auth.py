"""認証ストア（auth.py）と認証 API（server.py）を検証する。"""

import json
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

from System.auth import (
    MAX_LOGIN_FAILURES, AuthError, AuthStore, hash_password, normalize_profile, verify_password,
)
from System.books import BookLookup
from System.materials import MaterialStore
from System.server import make_server

FAST = 1_000  # テストでは PBKDF2 の反復回数を減らす


@pytest.fixture
def store(tmp_path):
    return AuthStore(tmp_path / "test.db", iterations=FAST)


# ============ パスワード ============

def test_password_hash_roundtrip():
    stored = hash_password("abc12345", FAST)
    assert "abc12345" not in stored
    assert verify_password("abc12345", stored)
    assert not verify_password("abc12346", stored)
    assert not verify_password("abc12345", "garbage")


def test_same_password_gets_different_salt():
    assert hash_password("abc12345", FAST) != hash_password("abc12345", FAST)


# ============ 登録・ログイン ============

def test_register_then_authenticate(store):
    user = store.register("morii_01", "secret123")
    assert user["loginId"] == "morii_01"
    assert user["profileCompleted"] is False
    assert store.authenticate("morii_01", "secret123")["id"] == user["id"]
    # ログインIDは大文字小文字を区別しない
    assert store.authenticate("MORII_01", "secret123")["id"] == user["id"]


def test_email_can_be_login_id(store):
    user = store.register("Morii.Taro@example.co.jp", "secret123")
    assert store.authenticate("morii.taro@example.co.jp", "secret123")["id"] == user["id"]
    long_email = "a" * 52 + "@example.com"  # 64文字
    assert store.register(long_email, "secret123")["loginId"] == long_email


def test_duplicate_login_id_is_rejected(store):
    store.register("taro", "secret123")
    with pytest.raises(AuthError) as err:
        store.register("TARO", "secret456")
    assert err.value.status == 409


@pytest.mark.parametrize("login_id,password,field", [
    ("ab", "secret123", "loginId"),
    ("has space", "secret123", "loginId"),
    ("日本語のID", "secret123", "loginId"),
    ("a" * 53 + "@example.com", "secret123", "loginId"),  # 65文字
    ("taro+1@example.com", "secret123", "loginId"),
    ("taro", "short1", "password"),
    ("taro", "onlyletters", "password"),
    ("taro", "12345678", "password"),
])
def test_register_validation(store, login_id, password, field):
    with pytest.raises(AuthError) as err:
        store.register(login_id, password)
    assert err.value.status == 400
    assert err.value.field == field


def test_wrong_password_and_unknown_user_look_the_same(store):
    store.register("taro", "secret123")
    with pytest.raises(AuthError) as wrong:
        store.authenticate("taro", "secret999")
    with pytest.raises(AuthError) as unknown:
        store.authenticate("jiro", "secret123")
    assert wrong.value.status == unknown.value.status == 401
    assert wrong.value.message == unknown.value.message


def test_lockout_after_repeated_failures(tmp_path):
    now = [1000.0]
    store = AuthStore(tmp_path / "t.db", iterations=FAST, clock=lambda: now[0])
    store.register("taro", "secret123")
    for _ in range(MAX_LOGIN_FAILURES):
        with pytest.raises(AuthError):
            store.authenticate("taro", "wrong0000")
    # 正しいパスワードでも止める
    with pytest.raises(AuthError) as err:
        store.authenticate("taro", "secret123")
    assert err.value.status == 429
    # 時間がたてば解除される
    now[0] += 16 * 60
    assert store.authenticate("taro", "secret123")["loginId"] == "taro"


# ============ セッション ============

def test_session_lifecycle(tmp_path):
    now = [1000.0]
    store = AuthStore(tmp_path / "t.db", iterations=FAST, session_ttl=60, clock=lambda: now[0])
    user = store.register("taro", "secret123")
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
    user = store.register("taro", "secret123")
    updated = store.update_profile(user["id"], {"nickname": "  たろう  "})
    assert updated["profileCompleted"] is True
    assert updated["profile"]["nickname"] == "たろう"
    assert updated["profile"]["tags"] == []
    assert updated["profile"]["birthYear"] is None


def test_profile_full(store):
    user = store.register("taro", "secret123")
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

@pytest.fixture
def server(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<p>ok</p>", encoding="utf-8")
    db = tmp_path / "t.db"
    httpd = make_server("127.0.0.1", 0, static, AuthStore(db, iterations=FAST),
                        MaterialStore(db), BookLookup())
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None, content_type="application/json"):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", content_type)
        try:
            with self.opener.open(req) as res:
                return res.status, json.loads(res.read()), res.headers
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read()), err.headers


def test_api_flow(server):
    client = Client(server)
    assert client.call("GET", "/api/auth/me")[0] == 401

    status, body, headers = client.call("POST", "/api/auth/register",
                                        {"loginId": "taro", "password": "secret123"})
    assert status == 201
    assert body["user"]["profileCompleted"] is False
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie

    status, body, _ = client.call("PUT", "/api/me/profile", {"profile": {"nickname": "たろう"}})
    assert status == 200 and body["user"]["profile"]["nickname"] == "たろう"

    status, body, _ = client.call("GET", "/api/auth/me")
    assert status == 200 and body["user"]["profileCompleted"] is True

    assert client.call("POST", "/api/auth/logout", {})[0] == 200
    assert client.call("GET", "/api/auth/me")[0] == 401

    status, body, _ = client.call("POST", "/api/auth/login", {"loginId": "taro", "password": "secret123"})
    assert status == 200 and body["user"]["profile"]["nickname"] == "たろう"


def test_api_errors(server):
    client = Client(server)
    status, body, _ = client.call("POST", "/api/auth/login", {"loginId": "nobody", "password": "secret123"})
    assert status == 401 and body["error"]
    status, body, _ = client.call("POST", "/api/auth/register", {"loginId": "x", "password": "secret123"})
    assert status == 400 and body["field"] == "loginId"
    # フォーム送信（JSON 以外）は受け付けない
    assert client.call("POST", "/api/auth/login", {"loginId": "a"}, content_type="text/plain")[0] == 415
    # 未ログインでプロフィールは保存できない
    assert client.call("PUT", "/api/me/profile", {"profile": {"nickname": "a"}})[0] == 401
    assert client.call("GET", "/api/nothing")[0] == 404


def test_static_files_are_served(server):
    with urllib.request.urlopen(server + "/index.html") as res:
        assert b"ok" in res.read()
