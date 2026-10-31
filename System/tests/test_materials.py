"""教材・カテゴリ（materials.py）、ISBN と書誌検索（books.py）、教材 API を検証する。"""

import base64
import json
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

import pytest

from System.auth import AuthError, AuthStore
from System.books import BookLookup, normalize_isbn
from System.materials import MaterialStore, decode_image
from System.server import make_server

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def data_url(raw, mime="image/png"):
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


@pytest.fixture
def stores(tmp_path):
    db = tmp_path / "t.db"
    auth = AuthStore(db, iterations=1_000)
    return auth, MaterialStore(db)


# ============ ISBN ============

@pytest.mark.parametrize("raw,expected", [
    ("9784101010014", "9784101010014"),
    ("978-4-10-101001-4", "9784101010014"),
    ("4101010013", "9784101010014"),       # ISBN-10 → 13
    ("4-10-101001-3", "9784101010014"),
])
def test_normalize_isbn(raw, expected):
    assert normalize_isbn(raw) == expected


@pytest.mark.parametrize("raw", [
    "9784101010015",   # チェックディジット違い
    "1920037009008",   # 日本の書籍の下段バーコード
    "4901234567894",   # 書籍ではない JAN
    "12345",
    "4101010014",      # ISBN-10 のチェックディジット違い
])
def test_normalize_isbn_rejects(raw):
    with pytest.raises(AuthError) as err:
        normalize_isbn(raw)
    assert err.value.field == "isbn"


# ============ 書誌検索 ============

def fake_fetch(responses):
    def fetch(url):
        for key, value in responses.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(url)
    return fetch


def test_lookup_prefers_openbd():
    lookup = BookLookup(fetch_json=fake_fetch({
        "openbd": [{"summary": {"title": "青チャート", "volume": "数学I+A", "publisher": "数研出版",
                                "author": "チャート研究所", "cover": "https://cover.openbd.jp/9784101010014.jpg"}}],
    }))
    book = lookup.lookup("9784101010014")
    assert book["title"] == "青チャート 数学I+A"
    assert book["coverUrl"] == "https://cover.openbd.jp/9784101010014.jpg"
    assert book["source"] == "openBD"


def test_lookup_falls_back_to_google():
    lookup = BookLookup(fetch_json=fake_fetch({
        "openbd": [None],
        "googleapis": {"items": [{"volumeInfo": {
            "title": "Target 1900", "authors": ["A", "B"],
            "imageLinks": {"thumbnail": "http://books.google.com/books/content?id=x&img=1"}}}]},
    }))
    book = lookup.lookup("9784101010014")
    assert book["title"] == "Target 1900"
    assert book["author"] == "A、B"
    # http は https にそろえる
    assert book["coverUrl"].startswith("https://books.google.com/")


def test_lookup_survives_source_errors_and_reports_not_found():
    lookup = BookLookup(fetch_json=fake_fetch({
        "openbd": OSError("down"),
        "googleapis": {"totalItems": 0},
    }))
    with pytest.raises(AuthError) as err:
        lookup.lookup("9784101010014")
    assert err.value.status == 404


def test_cover_download_only_from_allowed_hosts():
    fetched = []
    lookup = BookLookup(fetch_bytes=lambda url: fetched.append(url) or JPEG)
    assert lookup.download_cover("https://cover.openbd.jp/x.jpg") == JPEG
    assert lookup.download_cover("https://evil.example.com/x.jpg") is None
    assert lookup.download_cover("http://169.254.169.254/latest") is None
    assert lookup.download_cover("file:///etc/passwd") is None
    assert fetched == ["https://cover.openbd.jp/x.jpg"]


# ============ 画像 ============

def test_decode_image():
    assert decode_image(data_url(PNG)) == (PNG, "image/png")
    assert decode_image(data_url(JPEG, "image/jpeg"))[1] == "image/jpeg"
    for bad in ["not a data url", data_url(b"<svg></svg>", "image/svg+xml"), "data:image/png;base64,@@@"]:
        with pytest.raises(AuthError):
            decode_image(bad)


# ============ カテゴリ・教材 ============

def test_starts_empty(stores):
    auth, materials = stores
    user = auth.register("taro", "secret123")
    assert materials.list_for_user(user["id"]) == {"categories": [], "materials": []}


def test_add_category_and_material(stores):
    auth, materials = stores
    user = auth.register("taro", "secret123")
    cat = materials.add_category(user["id"], " 数学 ", "#5B5BD6")
    assert cat["name"] == "数学" and cat["color"] == "#5b5bd6"

    m = materials.add_material(user["id"], "青チャート", cat["id"], "9784101010014", (PNG, "image/png"))
    assert m["hasImage"] and m["imageVersion"] == 1 and m["categoryId"] == cat["id"]
    assert materials.get_image(user["id"], m["id"]) == (PNG, "image/png")

    plain = materials.add_material(user["id"], "自作プリント", cat["id"])
    assert not plain["hasImage"]
    listed = materials.list_for_user(user["id"])
    assert [x["title"] for x in listed["materials"]] == ["青チャート", "自作プリント"]


def test_duplicate_category_name(stores):
    auth, materials = stores
    user = auth.register("taro", "secret123")
    materials.add_category(user["id"], "数学", "#5b5bd6")
    with pytest.raises(AuthError) as err:
        materials.add_category(user["id"], "数学", "#e8724c")
    assert err.value.status == 409
    # 別のユーザーなら同じ名前でもよい
    other = auth.register("jiro", "secret123")
    materials.add_category(other["id"], "数学", "#5b5bd6")


def test_cannot_touch_other_users_data(stores):
    auth, materials = stores
    taro = auth.register("taro", "secret123")
    jiro = auth.register("jiro", "secret123")
    cat = materials.add_category(taro["id"], "数学", "#5b5bd6")
    m = materials.add_material(taro["id"], "青チャート", cat["id"], image=(PNG, "image/png"))

    with pytest.raises(AuthError):  # 他人のカテゴリには入れられない
        materials.add_material(jiro["id"], "x", cat["id"])
    with pytest.raises(AuthError):
        materials.get_image(jiro["id"], m["id"])
    with pytest.raises(AuthError):
        materials.delete_material(jiro["id"], m["id"])
    assert materials.list_for_user(jiro["id"])["materials"] == []

    materials.delete_material(taro["id"], m["id"])
    assert materials.list_for_user(taro["id"])["materials"] == []


@pytest.mark.parametrize("title,category,field", [
    ("", 1, "title"),
    ("あ" * 81, 1, "title"),
    ("ok", None, "categoryId"),
    ("ok", "1", "categoryId"),
    ("ok", 999, "categoryId"),
])
def test_material_validation(stores, title, category, field):
    auth, materials = stores
    user = auth.register("taro", "secret123")
    materials.add_category(user["id"], "数学", "#5b5bd6")
    with pytest.raises(AuthError) as err:
        materials.add_material(user["id"], title, category)
    assert err.value.field == field


# ============ API ============

@pytest.fixture
def client(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    db = tmp_path / "t.db"
    books = BookLookup(
        fetch_json=fake_fetch({"openbd": [{"summary": {
            "title": "青チャート", "cover": "https://cover.openbd.jp/9784101010014.jpg"}}]}),
        fetch_bytes=lambda url: JPEG,
    )
    httpd = make_server("127.0.0.1", 0, static, AuthStore(db, iterations=1_000), MaterialStore(db), books)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with opener.open(req) as res:
                raw = res.read()
                is_json = res.headers["Content-Type"].startswith("application/json")
                return res.status, json.loads(raw) if is_json else raw, res.headers
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read()), err.headers

    yield call
    httpd.shutdown()
    httpd.server_close()


def test_material_api_flow(client):
    assert client("GET", "/api/materials")[0] == 401
    client("POST", "/api/auth/register", {"loginId": "taro", "password": "secret123"})
    assert client("GET", "/api/materials")[1] == {"categories": [], "materials": []}

    status, body, _ = client("POST", "/api/categories", {"name": "数学", "color": "#5b5bd6"})
    assert status == 201
    cat_id = body["category"]["id"]

    # バーコードで追加：書誌検索 → 表紙はサーバーが取りに行く
    status, body, _ = client("GET", "/api/books/978-4-10-101001-4")
    assert status == 200 and body["book"]["isbn"] == "9784101010014"
    status, body, _ = client("POST", "/api/materials", {
        "title": body["book"]["title"], "categoryId": cat_id,
        "isbn": "9784101010014", "coverUrl": body["book"]["coverUrl"]})
    assert status == 201 and body["material"]["hasImage"]
    book_id = body["material"]["id"]

    status, raw, headers = client("GET", f"/api/materials/{book_id}/image")
    assert status == 200 and raw == JPEG and headers["Content-Type"] == "image/jpeg"

    # 独自教材：画像をアップロード
    status, body, _ = client("POST", "/api/materials", {
        "title": "自作プリント", "categoryId": cat_id, "image": data_url(PNG)})
    assert status == 201 and body["material"]["hasImage"]

    assert len(client("GET", "/api/materials")[1]["materials"]) == 2
    assert client("DELETE", f"/api/materials/{book_id}")[0] == 200
    assert len(client("GET", "/api/materials")[1]["materials"]) == 1


def test_material_api_errors(client):
    client("POST", "/api/auth/register", {"loginId": "taro", "password": "secret123"})
    status, body, _ = client("GET", "/api/books/9784101010015")
    assert status == 400 and body["field"] == "isbn"
    status, body, _ = client("POST", "/api/materials", {"title": "x", "categoryId": 1})
    assert status == 400 and body["field"] == "categoryId"
    assert client("DELETE", "/api/materials/12345")[0] == 404
