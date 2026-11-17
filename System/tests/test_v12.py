"""ver1.2 で足したもの：ほかの人の教材の一覧と自分の教材への登録（コピー）、
手動入力・ストップウォッチの記録（日時の指定）と、その API を検証する。"""

from datetime import datetime, timedelta, timezone

import pytest

from System.auth import AuthError
from System.tests.test_social import PNG, Client, Stores


@pytest.fixture
def s(tmp_path):
    return Stores(tmp_path / "t.db")


@pytest.fixture
def api(tmp_path):
    db = tmp_path / "t.db"
    client = Client(db, Stores(db))
    yield client
    client.close()


def iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


# ============ 教材のコピー ============

def test_copy_material_with_and_without_image(s):
    taro, jiro = s.user("taro"), s.user("jiro")
    book = s.material(taro, "青チャート", image=(PNG, "image/png"))
    cat = s.materials.add_category(jiro, "数学", "#27ae60")["id"]

    copied = s.materials.copy_material(jiro, book, cat, with_image=False)
    assert copied["title"] == "青チャート" and copied["categoryId"] == cat and not copied["hasImage"]

    # 同じ名前の教材はもう持っている
    with pytest.raises(AuthError) as err:
        s.materials.copy_material(jiro, book, cat, with_image=True)
    assert err.value.status == 409

    s.materials.delete_material(jiro, copied["id"])
    copied = s.materials.copy_material(jiro, book, cat, with_image=True)
    assert copied["hasImage"]
    assert s.materials.get_image(jiro, copied["id"]) == (PNG, "image/png")


def test_copy_material_errors(s):
    taro, jiro = s.user("taro"), s.user("jiro")
    book = s.material(taro)
    taro_cat = s.materials.list_for_user(taro)["categories"][0]["id"]
    with pytest.raises(AuthError) as err:
        s.materials.copy_material(taro, book, taro_cat, with_image=True)
    assert err.value.status == 409  # 自分の教材
    with pytest.raises(AuthError) as err:
        s.materials.copy_material(jiro, book, taro_cat, with_image=True)
    assert err.value.field == "categoryId"  # 他人のカテゴリには入れられない
    with pytest.raises(AuthError) as err:
        s.materials.copy_material(jiro, 9999, taro_cat, with_image=True)
    assert err.value.status == 404


def test_list_public_hides_images(s):
    taro = s.user("taro")
    s.material(taro, "青チャート", image=(PNG, "image/png"))
    [hidden] = s.materials.list_public(taro, with_images=False)
    [shown] = s.materials.list_public(taro, with_images=True)
    assert hidden["title"] == "青チャート" and hidden["categoryName"] == "数学" and hidden["color"] == "#5b5bd6"
    assert not hidden["hasImage"] and shown["hasImage"]


def test_material_api(api):
    st = api.stores
    taro, jiro = st.user("taro"), st.user("jiro")
    book = st.material(taro, "青チャート", image=(PNG, "image/png"))
    api.as_user(jiro)
    cat = api.call("POST", "/api/categories", {"name": "数学", "color": "#27ae60"})[1]["category"]["id"]

    # フォローしていないと画像は見えず、コピーもされない
    status, body, _ = api.call("GET", f"/api/users/{taro}/materials")
    assert status == 200 and [m["title"] for m in body["materials"]] == ["青チャート"]
    assert not body["materials"][0]["hasImage"]
    status, body, _ = api.call("POST", f"/api/materials/{book}/copy", {"categoryId": cat})
    assert status == 201 and not body["material"]["hasImage"]
    assert api.call("POST", f"/api/materials/{book}/copy", {"categoryId": cat})[0] == 409
    api.call("DELETE", f"/api/materials/{body['material']['id']}")

    # フォローすると画像も見え、コピーされる
    api.call("PUT", f"/api/users/{taro}/follow", {})
    assert api.call("GET", f"/api/users/{taro}/materials")[1]["materials"][0]["hasImage"]
    status, body, _ = api.call("POST", f"/api/materials/{book}/copy", {"categoryId": cat})
    assert status == 201 and body["material"]["hasImage"]
    status, raw, _ = api.call("GET", f"/api/materials/{body['material']['id']}/image")
    assert status == 200 and raw == PNG

    assert api.call("GET", "/api/users/9999/materials")[0] == 404
    assert api.call("POST", "/api/materials/9999/copy", {"categoryId": cat})[0] == 404


def test_record_material_has_isbn(s):
    taro = s.user("taro")
    cat = s.materials.add_category(taro, "英語", "#2d9cdb")["id"]
    book = s.materials.add_material(taro, "ターゲット", cat, isbn="9784101010014")["id"]
    record = s.records.add_record(taro, {"materialId": book, "durationMin": 10})
    assert record["material"]["isbn"] == "9784101010014"


# ============ 手動入力・ストップウォッチ ============

def test_record_created_at(s):
    taro = s.user("taro")
    book = s.material(taro)
    past = iso(timedelta(days=-2))
    record = s.records.add_record(
        taro, {"materialId": book, "durationMin": 45, "source": "stopwatch", "createdAt": past})
    assert record["source"] == "stopwatch" and record["createdAt"] == past

    # 少しだけ未来（端末の時計のずれ）は今として受け付ける
    record = s.records.add_record(
        taro, {"materialId": book, "durationMin": 5, "createdAt": iso(timedelta(minutes=2))})
    assert datetime.fromisoformat(record["createdAt"]) <= datetime.now(timezone.utc)

    for bad in (iso(timedelta(hours=1)), "yesterday", "1999-12-31T00:00:00+00:00"):
        with pytest.raises(AuthError) as err:
            s.records.add_record(taro, {"materialId": book, "durationMin": 5, "createdAt": bad})
        assert err.value.field == "createdAt"

    with pytest.raises(AuthError) as err:
        s.records.add_record(taro, {"materialId": book, "durationMin": 5, "source": "mock"})
    assert err.value.field == "source"
