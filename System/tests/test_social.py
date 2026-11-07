"""フォロー・通知・メッセージ（social.py）、学習記録（records.py）、招待申請（invites.py）と、その API を検証する。"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from System.auth import AuthError, AuthStore
from System.books import BookLookup
from System.invites import InviteRequestStore
from System.materials import MaterialStore
from System.records import RecordStore
from System.server import SESSION_COOKIE, make_server
from System.social import SocialStore
from System.tests.helpers import create_user, dummy_google

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class Stores:
    def __init__(self, db):
        self.auth = AuthStore(db)
        self.materials = MaterialStore(db)
        self.social = SocialStore(db)
        self.records = RecordStore(db)
        self.invites = InviteRequestStore(db)

    def user(self, name, **profile):
        user = create_user(self.auth, name)
        self.auth.update_profile(user["id"], {"nickname": name, **profile})
        return user["id"]

    def material(self, user_id, title="青チャート", image=None):
        cat = self.materials.add_category(user_id, "数学", "#5b5bd6")
        return self.materials.add_material(user_id, title, cat["id"], image=image)["id"]


@pytest.fixture
def s(tmp_path):
    return Stores(tmp_path / "t.db")


# ============ フォロー ============

def test_follow_and_unfollow(s):
    taro, jiro = s.user("taro"), s.user("jiro")
    user = s.social.follow(taro, jiro)
    assert user["isFollowing"] and not user["followsMe"] and not user["canMessage"]
    assert user["followerCount"] == 1
    assert [u["id"] for u in s.social.list_followers(taro, jiro)] == [taro]
    assert [u["id"] for u in s.social.list_following(jiro, taro)] == [jiro]

    # フォローし直しても未読の通知は1つだけ
    s.social.unfollow(taro, jiro)
    s.social.follow(taro, jiro)
    notes = s.social.list_notifications(jiro)
    assert [(n["type"], n["actor"]["id"]) for n in notes] == [("follow", taro)]
    assert s.social.badges(jiro)["notifications"] == 1
    s.social.mark_notifications_read(jiro)
    assert s.social.badges(jiro)["notifications"] == 0

    assert not s.social.unfollow(taro, jiro)["isFollowing"]


def test_follow_rules(s):
    taro = s.user("taro")
    with pytest.raises(AuthError):
        s.social.follow(taro, taro)
    with pytest.raises(AuthError) as err:
        s.social.follow(taro, 999)
    assert err.value.status == 404
    # 初回設定前のユーザーは見えない
    newbie = create_user(s.auth, "newbie")["id"]
    with pytest.raises(AuthError):
        s.social.follow(taro, newbie)
    assert s.social.search_users(taro, "newbie") == []


def test_public_profile_hides_private_fields(s):
    taro = s.user("taro", gender="男性", birthYear=2008, prefecture="東京都", bio="よろしく")
    jiro = s.user("jiro")
    user = s.social.get_user(jiro, taro)
    assert user["bio"] == "よろしく"
    for key in ("email", "gender", "birthYear", "prefecture"):
        assert key not in user


def test_search_users(s):
    me = s.user("taro")
    s.user("hanako")
    s.user("hana_50%")
    assert [u["nickname"] for u in s.social.search_users(me, "HANA")] == ["hanako", "hana_50%"]
    # LIKE の記号はそのままの文字として扱う
    assert [u["nickname"] for u in s.social.search_users(me, "_50%")] == ["hana_50%"]
    assert s.social.search_users(me, "taro") == []  # 自分は出さない
    assert s.social.search_users(me, "  ") == []


# ============ メッセージ ============

def test_messages_only_between_mutual_followers(s):
    taro, jiro = s.user("taro"), s.user("jiro")
    with pytest.raises(AuthError) as err:
        s.social.send_message(taro, jiro, "こんにちは")
    assert err.value.status == 403

    s.social.follow(taro, jiro)
    with pytest.raises(AuthError):  # 片思いでは送れない
        s.social.send_message(taro, jiro, "こんにちは")

    s.social.follow(jiro, taro)
    first = s.social.send_message(taro, jiro, " こんにちは ")
    assert first["body"] == "こんにちは" and first["senderId"] == taro
    s.social.send_message(jiro, taro, "どうも")
    s.social.send_message(taro, jiro, "一緒にがんばろう")

    assert s.social.badges(jiro)["messages"] == 2
    convs = s.social.list_conversations(jiro)
    assert len(convs) == 1 and convs[0]["partner"]["id"] == taro
    assert convs[0]["unread"] == 2 and convs[0]["lastMessage"]["body"] == "一緒にがんばろう"

    thread = s.social.get_thread(jiro, taro)
    assert [m["body"] for m in thread["messages"]] == ["こんにちは", "どうも", "一緒にがんばろう"]
    assert s.social.badges(jiro)["messages"] == 0  # 開いたら既読
    assert s.social.get_thread(jiro, taro, after_id=first["id"])["messages"][0]["body"] == "どうも"

    # 相互でなくなったら、読めるが送れない
    s.social.unfollow(jiro, taro)
    assert len(s.social.get_thread(taro, jiro)["messages"]) == 3
    with pytest.raises(AuthError):
        s.social.send_message(taro, jiro, "まだいる？")


@pytest.mark.parametrize("body", ["", "   ", None, 1, "あ" * 1001])
def test_message_validation(s, body):
    taro, jiro = s.user("taro"), s.user("jiro")
    s.social.follow(taro, jiro)
    s.social.follow(jiro, taro)
    with pytest.raises(AuthError) as err:
        s.social.send_message(taro, jiro, body)
    assert err.value.field == "body"


# ============ 学習記録 ============

def test_records_and_timeline(s):
    taro, jiro, saburo = s.user("taro"), s.user("jiro"), s.user("saburo")
    m_taro = s.material(taro)
    m_jiro = s.material(jiro, "Target 1900")
    s.material(saburo)

    rec = s.records.add_record(taro, {"materialId": str(m_taro), "durationMin": 30.4,
                                      "memo": "微分", "locSeries": [60, 70.6], "source": "camera"})
    assert rec["durationMin"] == 30 and rec["avgLoc"] == 66 and rec["locSeries"] == [60, 71]
    assert rec["material"]["title"] == "青チャート" and rec["user"]["nickname"] == "taro"
    s.records.add_record(jiro, {"materialId": m_jiro, "durationMin": 10})
    s.records.add_record(saburo, {"materialId": s.materials.list_for_user(saburo)["materials"][0]["id"],
                                  "durationMin": 5})

    # 他人の教材では記録できない
    with pytest.raises(AuthError) as err:
        s.records.add_record(taro, {"materialId": m_jiro, "durationMin": 10})
    assert err.value.field == "materialId"

    # タイムラインは自分＋フォロー中の人
    assert [r["user"]["nickname"] for r in s.records.timeline(taro)] == ["taro"]
    s.social.follow(taro, jiro)
    assert [r["user"]["nickname"] for r in s.records.timeline(taro)] == ["jiro", "taro"]

    # 教材を消しても記録は残る
    s.materials.delete_material(taro, m_taro)
    mine = s.records.list_for_user(taro)
    assert len(mine) == 1 and mine[0]["material"] is None

    s.records.delete_all(taro)
    assert s.records.list_for_user(taro) == []


@pytest.mark.parametrize("patch,field", [
    ({"durationMin": 0}, "durationMin"),
    ({"durationMin": "30"}, "durationMin"),
    ({"memo": "あ" * 201}, "memo"),
    ({"locSeries": [101]}, "locSeries"),
    ({"locSeries": [True]}, "locSeries"),
    ({"source": "hack"}, "source"),
    ({"materialId": None}, "materialId"),
])
def test_record_validation(s, patch, field):
    taro = s.user("taro")
    m = s.material(taro)
    with pytest.raises(AuthError) as err:
        s.records.add_record(taro, {"materialId": m, "durationMin": 10, **patch})
    assert err.value.field == field


def test_import_records_is_idempotent(s):
    taro, jiro = s.user("taro"), s.user("jiro")
    m_taro, m_jiro = s.material(taro), s.material(jiro)
    items = [
        {"id": "rec_a", "materialId": str(m_taro), "durationMin": 25, "memo": "あ" * 300,
         "locSeries": [50, 60], "createdAt": "2026-10-01T11:00:00.000Z", "source": "camera"},
        {"id": "rec_b", "materialId": str(m_jiro), "durationMin": 0, "createdAt": "2026-10-02T11:00:00Z"},
        {"id": "rec_c", "materialId": "1", "durationMin": 10, "createdAt": "not a date"},
    ]
    assert s.records.import_records(taro, items) == {"imported": 2, "skipped": 1}
    assert s.records.import_records(taro, items) == {"imported": 0, "skipped": 1}

    records = s.records.list_for_user(taro)
    assert [r["createdAt"][:10] for r in records] == ["2026-10-02", "2026-10-01"]
    assert records[0]["material"] is None  # 他人の教材は指さない
    assert len(records[1]["memo"]) == 200


def test_material_image_visible_to_followers(tmp_path):
    db = tmp_path / "t.db"
    s = Stores(db)
    app = Client(db, s)
    try:
        taro = s.user("taro")
        jiro = s.user("jiro")
        m = s.material(taro, image=(PNG, "image/png"))
        app.as_user(jiro)
        assert app.call("GET", f"/api/materials/{m}/image")[0] == 404
        s.records.add_record(taro, {"materialId": m, "durationMin": 10})
        records = app.call("GET", f"/api/users/{taro}/records")[1]["records"]
        assert records[0]["material"]["hasImage"] is False  # フォロー前は画像を出さない

        s.social.follow(jiro, taro)
        status, raw, _ = app.call("GET", f"/api/materials/{m}/image")
        assert status == 200 and raw == PNG
        records = app.call("GET", f"/api/users/{taro}/records")[1]["records"]
        assert records[0]["material"]["hasImage"] is True
    finally:
        app.close()


# ============ 招待申請 ============

def test_referral_flow(s):
    taro = s.user("taro")
    admin = s.user("admin")
    s.auth.set_admin(admin, True)

    req = s.invites.refer(taro, " Friend@Example.com ", "高校の友だちです")
    assert req["email"] == "friend@example.com" and req["kind"] == "referral" and req["status"] == "pending"
    assert [n["type"] for n in s.social.list_notifications(admin)] == ["invite_request"]

    with pytest.raises(AuthError) as err:  # 同じアドレスの申請は1つだけ
        s.invites.refer(taro, "friend@example.com", "")
    assert err.value.status == 409
    with pytest.raises(AuthError) as err:  # 登録済み
        s.invites.refer(taro, "admin@example.com", "")
    assert err.value.status == 409

    listed = s.invites.list_requests("pending")
    assert listed[0]["requester"] == {"id": taro, "nickname": "taro"}

    approved = s.invites.approve(admin, req["id"])
    assert approved["status"] == "approved"
    assert [i["email"] for i in s.auth.list_invitations()] == ["friend@example.com"]
    assert [n["type"] for n in s.social.list_notifications(taro)] == ["invite_approved"]
    assert s.social.badges(admin)["notifications"] == 0  # 処理した申請の通知は既読に
    with pytest.raises(AuthError) as err:
        s.invites.reject(admin, req["id"])
    assert err.value.status == 409

    # 承認されたので、その Google アカウントで登録できる
    s.auth.login_with_google("sub-friend", "friend@example.com")
    assert s.invites.list_mine(taro)[0]["status"] == "approved"


def test_referral_reject_and_limit(s):
    taro = s.user("taro")
    admin = s.user("admin")
    s.auth.set_admin(admin, True)
    req = s.invites.refer(taro, "a@example.com", "")
    s.invites.reject(admin, req["id"])
    assert s.auth.list_invitations() == []
    assert [n["type"] for n in s.social.list_notifications(taro)] == ["invite_rejected"]
    # 却下されたあとはまた申請できる
    for i in range(5):
        s.invites.refer(taro, f"f{i}@example.com", "")
    with pytest.raises(AuthError) as err:
        s.invites.refer(taro, "f9@example.com", "")
    assert err.value.status == 429


def test_self_application_with_ticket(s):
    admin = s.user("admin")
    s.auth.set_admin(admin, True)
    ticket = s.invites.create_ticket("Stranger@Example.com")
    assert s.invites.ticket_status(ticket) == {"email": "stranger@example.com", "pending": False}

    req = s.invites.apply_with_ticket(ticket, "参加したいです")
    assert req["kind"] == "self" and req["email"] == "stranger@example.com"
    with pytest.raises(AuthError) as err:  # 引換券は1回きり
        s.invites.apply_with_ticket(ticket, "")
    assert err.value.status == 404
    for bad in [None, "", "forged"]:
        with pytest.raises(AuthError):
            s.invites.ticket_status(bad)


def test_ticket_expires(tmp_path):
    now = [1000.0]
    invites = InviteRequestStore(tmp_path / "t.db", clock=lambda: now[0])
    AuthStore(tmp_path / "t.db")
    ticket = invites.create_ticket("x@example.com")
    now[0] += 31 * 60
    with pytest.raises(AuthError):
        invites.apply_with_ticket(ticket, "")


# ============ API ============

class Client:
    def __init__(self, db, stores):
        static = db.parent / "static"
        static.mkdir(exist_ok=True)
        self.stores = stores
        self.httpd = make_server("127.0.0.1", 0, static, stores.auth, stores.materials, BookLookup(),
                                 dummy_google(), social=stores.social, records=stores.records,
                                 invites=stores.invites)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.cookies = {}

    def as_user(self, user_id):
        self.cookies = {SESSION_COOKIE: self.stores.auth.create_session(user_id)}

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.cookies:
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        try:
            with urllib.request.urlopen(req) as res:
                raw = res.read()
                is_json = res.headers["Content-Type"].startswith("application/json")
                return res.status, json.loads(raw) if is_json else raw, res.headers
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read()), err.headers

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def api(tmp_path):
    db = tmp_path / "t.db"
    client = Client(db, Stores(db))
    yield client
    client.close()


def test_social_api_flow(api):
    s = api.stores
    taro, jiro = s.user("taro"), s.user("jiro")
    assert api.call("GET", "/api/users?q=jiro")[0] == 401

    api.as_user(taro)
    status, body, _ = api.call("GET", "/api/users?q=ji")
    assert status == 200 and [u["id"] for u in body["users"]] == [jiro]
    status, body, _ = api.call("PUT", f"/api/users/{jiro}/follow", {})
    assert status == 200 and body["user"]["isFollowing"]
    assert api.call("GET", f"/api/users/{jiro}/followers")[1]["users"][0]["id"] == taro
    assert api.call("POST", f"/api/messages/{jiro}", {"body": "やあ"})[0] == 403

    api.as_user(jiro)
    assert api.call("GET", "/api/badges")[1] == {"notifications": 1, "messages": 0}
    notes = api.call("GET", "/api/notifications")[1]["notifications"]
    assert notes[0]["type"] == "follow" and notes[0]["actor"]["nickname"] == "taro"
    assert api.call("POST", "/api/notifications/read", {})[0] == 200
    api.call("PUT", f"/api/users/{taro}/follow", {})
    status, body, _ = api.call("POST", f"/api/messages/{taro}", {"body": "よろしく"})
    assert status == 201
    msg_id = body["message"]["id"]

    api.as_user(taro)
    assert api.call("GET", "/api/badges")[1]["messages"] == 1
    assert api.call("GET", "/api/messages")[1]["conversations"][0]["unread"] == 1
    body = api.call("GET", f"/api/messages/{jiro}")[1]
    assert body["partner"]["canMessage"] and [m["body"] for m in body["messages"]] == ["よろしく"]
    assert api.call("GET", f"/api/messages/{jiro}?after={msg_id}")[1]["messages"] == []
    assert api.call("GET", f"/api/messages/{jiro}?after=x")[0] == 400
    assert api.call("DELETE", f"/api/users/{jiro}/follow")[1]["user"]["isFollowing"] is False


def test_records_api_flow(api):
    s = api.stores
    taro, jiro = s.user("taro"), s.user("jiro")
    m = s.material(jiro)
    api.as_user(jiro)
    status, body, _ = api.call("POST", "/api/records", {"materialId": m, "durationMin": 45})
    assert status == 201 and body["record"]["durationMin"] == 45
    status, body, _ = api.call("POST", "/api/records/import", {"records": [
        {"id": "rec_x", "materialId": m, "durationMin": 5, "createdAt": "2026-01-01T00:00:00Z"}]})
    assert body == {"imported": 1, "skipped": 0}
    assert len(api.call("GET", "/api/records")[1]["records"]) == 2

    api.as_user(taro)
    assert api.call("GET", "/api/timeline")[1]["records"] == []
    assert len(api.call("GET", f"/api/users/{jiro}/records")[1]["records"]) == 2
    api.call("PUT", f"/api/users/{jiro}/follow", {})
    assert len(api.call("GET", "/api/timeline")[1]["records"]) == 2

    api.as_user(jiro)
    assert api.call("DELETE", "/api/records")[0] == 200
    assert api.call("GET", "/api/records")[1]["records"] == []


def test_invite_request_api(api):
    s = api.stores
    taro, admin = s.user("taro"), s.user("admin")

    # 引換券なしでは申請できない
    assert api.call("GET", "/api/apply")[0] == 404
    api.cookies = {"onsta_apply": s.invites.create_ticket("stranger@example.com")}
    assert api.call("GET", "/api/apply")[1] == {"email": "stranger@example.com", "pending": False}
    status, body, headers = api.call("POST", "/api/apply", {"message": "入れてください"})
    assert status == 201 and body["request"]["kind"] == "self"
    assert any(c.startswith("onsta_apply=;") for c in headers.get_all("Set-Cookie"))

    api.as_user(taro)
    status, body, _ = api.call("POST", "/api/invite-requests", {"email": "friend@example.com"})
    assert status == 201
    assert len(api.call("GET", "/api/invite-requests")[1]["requests"]) == 1
    assert api.call("GET", "/api/admin/invite-requests")[0] == 403
    assert api.call("POST", f"/api/admin/invite-requests/{body['request']['id']}/approve", {})[0] == 403

    s.auth.set_admin(admin, True)
    api.as_user(admin)
    assert api.call("GET", "/api/auth/me")[1]["user"]["isAdmin"] is True
    requests = api.call("GET", "/api/admin/invite-requests?status=pending")[1]["requests"]
    assert {r["email"] for r in requests} == {"stranger@example.com", "friend@example.com"}
    assert api.call("GET", "/api/admin/invite-requests?status=bogus")[0] == 400
    for r in requests:
        action = "approve" if r["kind"] == "self" else "reject"
        status, body, _ = api.call("POST", f"/api/admin/invite-requests/{r['id']}/{action}", {})
        assert status == 200 and body["request"]["status"] == action + ("d" if action == "approve" else "ed")
    assert [i["email"] for i in s.auth.list_invitations()] == ["stranger@example.com"]
