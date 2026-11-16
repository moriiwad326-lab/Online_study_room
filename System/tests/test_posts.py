"""ver1.1 で足したもの：いいね・返信（posts.py）、投稿の削除、プロフィール画像、
運営アカウントの検索、更新のお知らせ（announcements.py）と、その API を検証する。"""

import base64

import pytest

from System.announcements import announce_release
from System.auth import AuthError
from System.posts import PostStore
from System.tests.helpers import create_user
from System.tests.test_social import PNG, Client, Stores


class PostStores(Stores):
    def __init__(self, db):
        super().__init__(db)
        self.posts = PostStore(db)
        self._materials = {}

    def record(self, user_id, minutes=30):
        if user_id not in self._materials:
            self._materials[user_id] = self.material(user_id)
        return self.records.add_record(
            user_id, {"materialId": self._materials[user_id], "durationMin": minutes})["id"]


@pytest.fixture
def s(tmp_path):
    return PostStores(tmp_path / "t.db")


def notes_of(s, user_id):
    return [(n["type"], n["actor"]["id"] if n["actor"] else None, n["data"])
            for n in s.social.list_notifications(user_id)]


# ============ いいね ============

def test_like_notifies_owner_once(s):
    taro, jiro = s.user("taro"), s.user("jiro")
    rec = s.record(jiro)

    assert s.posts.like(taro, rec) == {"likeCount": 1, "liked": True}
    assert s.posts.like(taro, rec) == {"likeCount": 1, "liked": True}  # 2回押しても1つ
    s.posts.unlike(taro, rec)
    s.posts.like(taro, rec)
    assert notes_of(s, jiro) == [("like", taro, {"recordId": rec})]

    # 自分の投稿へのいいねは通知しない
    s.posts.like(jiro, rec)
    assert len(s.social.list_notifications(jiro)) == 1

    record = s.records.timeline(jiro)[0]
    assert record["likeCount"] == 2 and record["liked"] and record["replyCount"] == 0
    assert s.records.get_record(taro, rec)["liked"]
    assert s.posts.unlike(taro, rec) == {"likeCount": 1, "liked": False}

    with pytest.raises(AuthError) as err:
        s.posts.like(taro, 999)
    assert err.value.status == 404


# ============ 返信 ============

def test_threaded_replies_and_notifications(s):
    taro, jiro, hana = s.user("taro"), s.user("jiro"), s.user("hana")
    rec = s.record(jiro)

    r1 = s.posts.add_reply(taro, rec, "すごい！")
    assert r1["parentId"] is None and r1["user"]["id"] == taro and r1["canDelete"]
    r2 = s.posts.add_reply(hana, rec, " ありがとう ", r1["id"])
    assert r2["body"] == "ありがとう" and r2["parentId"] == r1["id"]

    # 投稿者（jiro）には2件、返信先を書いた taro には返信への返信の1件
    assert [(t, a, d["toReply"]) for t, a, d in notes_of(s, jiro)] == [("reply", hana, False), ("reply", taro, False)]
    assert [(t, a, d["toReply"]) for t, a, d in notes_of(s, taro)] == [("reply", hana, True)]
    # 投稿者が自分の投稿の返信に返信しても、自分には通知しない
    s.posts.add_reply(jiro, rec, "どういたしまして", r2["id"])
    assert len(s.social.list_notifications(jiro)) == 2

    assert s.records.get_record(taro, rec)["replyCount"] == 3
    replies = s.posts.list_replies(taro, rec)
    assert [r["parentId"] for r in replies] == [None, r1["id"], r2["id"]]
    assert [r["canDelete"] for r in replies] == [True, False, False]
    # 投稿者は全部消せる
    assert all(r["canDelete"] for r in s.posts.list_replies(jiro, rec))


def test_delete_reply_keeps_thread(s):
    taro, jiro, hana = s.user("taro"), s.user("jiro"), s.user("hana")
    rec = s.record(jiro)
    r1 = s.posts.add_reply(taro, rec, "親")
    r2 = s.posts.add_reply(hana, rec, "子", r1["id"])

    # 他人の返信は消せない（投稿者でもない）
    with pytest.raises(AuthError):
        s.posts.delete_reply(hana, r1["id"])

    # 子があるので、消した返信は「削除された返信」として残る
    s.posts.delete_reply(taro, r1["id"])
    replies = s.posts.list_replies(hana, rec)
    assert [(r["id"], r["isDeleted"], r["body"], r["user"]) for r in replies][0] == (r1["id"], True, "", None)
    assert s.records.get_record(hana, rec)["replyCount"] == 1
    # 消した返信には返信できない
    with pytest.raises(AuthError):
        s.posts.add_reply(hana, rec, "まだある？", r1["id"])
    # 子も消えたら、どちらも一覧から外れ、返信の通知も消える
    s.posts.delete_reply(jiro, r2["id"])
    assert s.posts.list_replies(hana, rec) == []
    assert all(n["type"] != "reply" for n in s.social.list_notifications(taro))


@pytest.mark.parametrize("body,parent", [("", None), ("   ", None), ("あ" * 501, None), (1, None), ("ok", "1"), ("ok", 999)])
def test_reply_validation(s, body, parent):
    taro = s.user("taro")
    rec = s.record(taro)
    with pytest.raises(AuthError):
        s.posts.add_reply(taro, rec, body, parent)


def test_reply_parent_must_be_on_same_post(s):
    taro = s.user("taro")
    rec1, rec2 = s.record(taro), s.record(taro)
    r1 = s.posts.add_reply(taro, rec1, "こっち")
    with pytest.raises(AuthError):
        s.posts.add_reply(taro, rec2, "あっち", r1["id"])


# ============ 投稿の削除 ============

def test_delete_record_removes_reactions_and_notifications(s):
    taro, jiro = s.user("taro"), s.user("jiro")
    rec, keep = s.record(jiro), s.record(jiro)
    s.posts.like(taro, rec)
    s.posts.add_reply(taro, rec, "おつかれ")
    s.posts.like(taro, keep)

    with pytest.raises(AuthError) as err:
        s.records.delete_record(taro, rec)  # 他人の投稿は消せない
    assert err.value.status == 404

    s.records.delete_record(jiro, rec)
    assert [r["id"] for r in s.records.list_for_user(jiro)] == [keep]
    assert notes_of(s, jiro) == [("like", taro, {"recordId": keep})]
    with pytest.raises(AuthError):
        s.posts.list_replies(taro, rec)

    s.records.delete_all(jiro)
    assert s.social.list_notifications(jiro) == []


# ============ プロフィール画像・運営アカウント ============

def test_avatar_version_survives_profile_update(s):
    taro = s.user("taro")
    user = s.auth.set_avatar(taro, PNG, "image/png")
    assert user["profile"]["avatarVersion"] == 1
    assert s.auth.set_avatar(taro, PNG, "image/png")["profile"]["avatarVersion"] == 2
    # プロフィールを保存し直しても、画像の版数は消えない
    assert s.auth.update_profile(taro, {"nickname": "taro2"})["profile"]["avatarVersion"] == 2
    assert s.social.get_user(s.user("jiro"), taro)["avatarVersion"] == 2
    assert s.auth.get_avatar(taro) == (PNG, "image/png")

    user = s.auth.delete_avatar(taro)
    assert "avatarVersion" not in user["profile"] and s.auth.get_avatar(taro) is None


def test_featured_users(s):
    newbie = create_user(s.auth, "newbie")["id"]
    assert s.social.featured_users(newbie) == []
    molix = s.user("MoliX")
    s.user("molix-fan")
    assert s.social.featured_users(newbie) == []  # 管理者でなければ勧めない
    s.auth.set_admin(molix, True)
    assert [u["id"] for u in s.social.featured_users(newbie)] == [molix]
    assert s.social.featured_users(molix) == []  # 自分自身は出さない


# ============ 更新のお知らせ ============

def test_announce_release_once(s):
    taro, jiro = s.user("taro"), s.user("jiro")
    assert announce_release(s.auth.db_path, "1.1", "更新しました") == 2
    assert announce_release(s.auth.db_path, "1.1", "更新しました") == 0
    assert notes_of(s, taro) == [("announcement", None, {"version": "1.1", "title": "更新しました"})]
    assert s.social.badges(jiro)["notifications"] == 1
    assert announce_release(s.auth.db_path, "1.2", "また更新") == 2


# ============ API ============

@pytest.fixture
def api(tmp_path):
    db = tmp_path / "t.db"
    client = Client(db, PostStores(db))
    yield client
    client.close()


def test_posts_api_flow(api):
    s = api.stores
    taro, jiro = s.user("taro"), s.user("jiro")
    rec = s.record(jiro)

    api.as_user(taro)
    status, body, _ = api.call("PUT", f"/api/records/{rec}/like", {})
    assert status == 200 and body == {"likeCount": 1, "liked": True}
    status, body, _ = api.call("POST", f"/api/records/{rec}/replies", {"body": "えらい"})
    assert status == 201
    reply_id = body["reply"]["id"]
    status, body, _ = api.call("POST", f"/api/records/{rec}/replies", {"body": "続き", "parentId": reply_id})
    assert status == 201 and body["reply"]["parentId"] == reply_id
    record = api.call("GET", f"/api/records/{rec}")[1]["record"]
    assert (record["likeCount"], record["liked"], record["replyCount"]) == (1, True, 2)
    # フォローしていない人の投稿なので、教材の画像は見せない
    assert record["material"]["hasImage"] is False
    assert api.call("DELETE", f"/api/records/{rec}")[0] == 404  # 他人の投稿は消せない
    assert api.call("DELETE", f"/api/records/{rec}/like")[1] == {"likeCount": 0, "liked": False}

    api.as_user(jiro)
    types = [n["type"] for n in api.call("GET", "/api/notifications")[1]["notifications"]]
    assert types == ["reply", "reply", "like"]
    assert len(api.call("GET", f"/api/records/{rec}/replies")[1]["replies"]) == 2
    assert api.call("DELETE", f"/api/replies/{reply_id}")[0] == 200
    assert api.call("DELETE", f"/api/records/{rec}")[0] == 200
    assert api.call("GET", f"/api/records/{rec}")[0] == 404


def test_member_only_writes(api):
    s = api.stores
    jiro = s.user("jiro")
    rec = s.record(jiro)
    newbie = create_user(s.auth, "newbie")["id"]
    api.as_user(newbie)
    assert api.call("PUT", f"/api/records/{rec}/like", {})[0] == 403
    assert api.call("POST", f"/api/records/{rec}/replies", {"body": "やあ"})[0] == 403


def test_avatar_api(api):
    s = api.stores
    taro, jiro = s.user("taro"), s.user("jiro")
    api.as_user(taro)
    assert api.call("GET", f"/api/users/{taro}/avatar")[0] == 404
    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    status, body, _ = api.call("PUT", "/api/me/avatar", {"image": data_url})
    assert status == 200 and body["user"]["profile"]["avatarVersion"] == 1
    assert api.call("PUT", "/api/me/avatar", {"image": "data:image/png;base64,AAAA"})[0] == 400

    api.as_user(jiro)
    status, raw, headers = api.call("GET", f"/api/users/{taro}/avatar")
    assert status == 200 and raw == PNG and headers["Content-Type"] == "image/png"

    api.as_user(taro)
    assert "avatarVersion" not in api.call("DELETE", "/api/me/avatar")[1]["user"]["profile"]


def test_featured_users_api(api):
    s = api.stores
    molix = s.user("molix")
    s.auth.set_admin(molix, True)
    newbie = create_user(s.auth, "newbie")["id"]
    api.as_user(newbie)
    users = api.call("GET", "/api/featured-users")[1]["users"]
    assert [(u["id"], u["isFollowing"]) for u in users] == [(molix, False)]
