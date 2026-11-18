"""ver1.3 で足したもの：「いま勉強中」で計測中のプレビュー（静止画）を相互フォローの人に共有する。"""

import base64

import pytest

from System.announcements import APP_VERSION
from System.auth import AuthError
from System.live import MAX_PREVIEW_BYTES, STALE_SEC, LiveStore
from System.tests.test_social import PNG, Client, Stores

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
STATE = {"status": "studying", "elapsedSec": 60, "loc": 70, "recent": [70]}


def data_url(data, kind="jpeg"):
    return f"data:image/{kind};base64," + base64.b64encode(data).decode()


@pytest.fixture
def s(tmp_path):
    stores = Stores(tmp_path / "t.db")
    stores.now = [1_000_000.0]
    stores.live = LiveStore(tmp_path / "t.db", clock=lambda: stores.now[0])
    return stores


def test_version():
    assert APP_VERSION == "1.3"


def test_preview_only_for_self_and_mutual_followers(s):
    taro, jiro, saburo = s.user("taro"), s.user("jiro"), s.user("saburo")
    m = s.material(taro)
    s.social.follow(jiro, taro)    # jiro → taro（片方向）
    s.social.follow(saburo, taro)  # saburo ⇄ taro（相互）
    s.social.follow(taro, saburo)
    s.live.update(taro, {"materialId": m, **STATE, "preview": data_url(JPEG)})

    assert s.live.list_for(taro)[0]["previewAt"] == 1_000_000_000
    assert s.live.get_preview(taro, taro) == JPEG
    assert s.live.list_for(saburo)[0]["previewAt"] == 1_000_000_000
    assert s.live.get_preview(saburo, taro) == JPEG

    # 片方向のフォローでは、計測は見えてもプレビューは見えない（共有していることも知らせない）
    assert s.live.list_for(jiro)[0]["previewAt"] is None
    with pytest.raises(AuthError) as err:
        s.live.get_preview(jiro, taro)
    assert err.value.status == 404


def test_preview_stops_when_sharing_is_off_or_ended(s):
    taro, saburo = s.user("taro"), s.user("saburo")
    m = s.material(taro)
    s.social.follow(saburo, taro)
    s.social.follow(taro, saburo)

    # 共有しない計測
    s.live.update(taro, {"materialId": m, **STATE})
    assert s.live.list_for(saburo)[0]["previewAt"] is None

    s.live.update(taro, {"materialId": m, **STATE, "preview": data_url(JPEG)})
    assert s.live.get_preview(saburo, taro) == JPEG
    # 次の送信でプレビューが無ければ、共有をやめたことになる
    s.live.update(taro, {"materialId": m, **STATE, "preview": None})
    assert s.live.list_for(saburo)[0]["previewAt"] is None
    with pytest.raises(AuthError):
        s.live.get_preview(saburo, taro)

    s.live.update(taro, {"materialId": m, **STATE, "preview": data_url(JPEG)})
    s.live.end(taro)
    with pytest.raises(AuthError):
        s.live.get_preview(saburo, taro)


def test_stale_preview_disappears(s):
    taro = s.user("taro")
    s.live.update(taro, {"materialId": s.material(taro), **STATE, "preview": data_url(JPEG)})
    s.now[0] += STALE_SEC + 1
    with pytest.raises(AuthError):
        s.live.get_preview(taro, taro)
    assert s.live.list_for(taro) == []


@pytest.mark.parametrize("preview", [
    data_url(PNG, "png"),                              # JPEG 以外
    "not a data url",
    data_url(b"\xff\xd8\xff" + b"\x00" * MAX_PREVIEW_BYTES),  # 大きすぎる
], ids=["png", "text", "too-large"])
def test_preview_validation(s, preview):
    taro = s.user("taro")
    m = s.material(taro)
    with pytest.raises(AuthError) as err:
        s.live.update(taro, {"materialId": m, **STATE, "preview": preview})
    assert err.value.field == "preview"
    # 不正なプレビューの送信では、計測の状態も書かない
    assert s.live.list_for(taro) == []


def test_preview_api(tmp_path):
    stores = Stores(tmp_path / "t.db")
    api = Client(tmp_path / "t.db", stores)
    try:
        taro, jiro = stores.user("taro"), stores.user("jiro")
        m = stores.material(taro)
        api.as_user(taro)
        assert api.call("PUT", "/api/live", {"materialId": m, **STATE, "preview": data_url(JPEG)})[0] == 200
        status, body, headers = api.call("GET", f"/api/live/{taro}/preview")
        assert status == 200 and body == JPEG
        assert headers["Content-Type"] == "image/jpeg" and headers["Cache-Control"] == "no-store"

        # 片方向のフォローでは見られない
        api.as_user(jiro)
        api.call("PUT", f"/api/users/{taro}/follow", {})
        assert api.call("GET", "/api/live")[1]["sessions"][0]["previewAt"] is None
        assert api.call("GET", f"/api/live/{taro}/preview")[0] == 404

        # 相互フォローになると見られる
        api.as_user(taro)
        api.call("PUT", f"/api/users/{jiro}/follow", {})
        api.as_user(jiro)
        assert api.call("GET", "/api/live")[1]["sessions"][0]["previewAt"] is not None
        assert api.call("GET", f"/api/live/{taro}/preview")[1] == JPEG

        api.cookies = {}
        assert api.call("GET", f"/api/live/{taro}/preview")[0] == 401
    finally:
        api.close()
