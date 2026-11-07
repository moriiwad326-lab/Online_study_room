"""いま勉強中（live.py）と、その API を検証する。"""

import pytest

from System.auth import AuthError
from System.live import STALE_SEC, LiveStore
from System.tests.test_social import Client, Stores

STATE = {"status": "studying", "elapsedSec": 125, "loc": 72.4, "recent": [60, 65.5, 72.4]}


@pytest.fixture
def s(tmp_path):
    stores = Stores(tmp_path / "t.db")
    stores.now = [1_000_000.0]
    stores.live = LiveStore(tmp_path / "t.db", clock=lambda: stores.now[0])
    return stores


def test_followers_see_live_sessions(s):
    taro, jiro, saburo = s.user("taro"), s.user("jiro"), s.user("saburo")
    m = s.material(taro)
    s.live.update(taro, {"materialId": m, **STATE})

    mine = s.live.list_for(taro)
    assert len(mine) == 1
    live = mine[0]
    assert live["user"]["nickname"] == "taro" and "email" not in live["user"]
    assert live["material"]["title"] == "青チャート" and live["material"]["categoryName"] == "数学"
    assert (live["status"], live["elapsedSec"], live["loc"], live["recent"]) == ("studying", 125, 72, [60, 66, 72])

    # フォローしている人にだけ見える
    assert s.live.list_for(jiro) == []
    s.social.follow(jiro, taro)
    assert [x["user"]["id"] for x in s.live.list_for(jiro)] == [taro]
    assert s.live.list_for(saburo) == []

    # 更新すると上書き、終えると消える
    s.now[0] += 5
    s.live.update(taro, {"materialId": m, **STATE, "status": "paused", "loc": None})
    live = s.live.list_for(jiro)[0]
    assert live["status"] == "paused" and live["loc"] is None
    s.live.end(taro)
    assert s.live.list_for(jiro) == []


def test_stale_sessions_disappear(s):
    taro = s.user("taro")
    s.live.update(taro, {"materialId": s.material(taro), **STATE})
    s.now[0] += STALE_SEC - 1
    assert s.live.list_for(taro)[0]["secondsSinceUpdate"] == STALE_SEC - 1
    s.now[0] += 2
    assert s.live.list_for(taro) == []


def test_only_own_materials(s):
    taro, jiro = s.user("taro"), s.user("jiro")
    m = s.material(jiro)
    with pytest.raises(AuthError) as err:
        s.live.update(taro, {"materialId": m, **STATE})
    assert err.value.field == "materialId"


@pytest.mark.parametrize("patch,field", [
    ({"status": "dancing"}, "status"),
    ({"elapsedSec": -1}, "elapsedSec"),
    ({"elapsedSec": "10"}, "elapsedSec"),
    ({"loc": 101}, "loc"),
    ({"loc": True}, "loc"),
    ({"recent": [50] * 61}, "recent"),
    ({"recent": [-1]}, "recent"),
    ({"materialId": None}, "materialId"),
])
def test_validation(s, patch, field):
    taro = s.user("taro")
    with pytest.raises(AuthError) as err:
        s.live.update(taro, {"materialId": s.material(taro), **STATE, **patch})
    assert err.value.field == field


def test_live_api(tmp_path):
    stores = Stores(tmp_path / "t.db")
    api = Client(tmp_path / "t.db", stores)
    try:
        taro, jiro = stores.user("taro"), stores.user("jiro")
        m = stores.material(taro)
        assert api.call("GET", "/api/live")[0] == 401

        api.as_user(taro)
        assert api.call("PUT", "/api/live", {"materialId": m, **STATE})[0] == 200
        assert api.call("PUT", "/api/live", {"materialId": m, **STATE, "loc": 500})[0] == 400

        api.as_user(jiro)
        api.call("PUT", f"/api/users/{taro}/follow", {})
        sessions = api.call("GET", "/api/live")[1]["sessions"]
        assert [x["user"]["nickname"] for x in sessions] == ["taro"]

        api.as_user(taro)
        assert api.call("DELETE", "/api/live")[0] == 200
        api.as_user(jiro)
        assert api.call("GET", "/api/live")[1]["sessions"] == []
    finally:
        api.close()
