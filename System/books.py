"""バーコード（ISBN）から書名・表紙を調べる。

1. openBD（日本の書籍に強い。API キー不要）
2. 見つからなければ Google Books（キーなしだと 429 になりやすいので、
   環境変数 GOOGLE_BOOKS_API_KEY か main.py --google-books-key でキーを渡せる）

表紙画像は外部 URL のまま持たず、教材を追加するときにサーバーがダウンロードして DB に保存する。
そのとき任意の URL を取りに行かないよう、ダウンロード先は COVER_HOSTS に限る。
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from .auth import AuthError

TIMEOUT_SEC = 6
MAX_COVER_BYTES = 2 * 1024 * 1024
USER_AGENT = "Onsta/0.1 (+http://localhost)"

# 表紙を取りに行ってよいホスト（openBD と Google Books の画像配信元）
COVER_HOSTS = {"cover.openbd.jp", "books.google.com", "books.googleusercontent.com"}


# ============ ISBN ============

def normalize_isbn(raw: Any) -> str:
    """ISBN-13 / ISBN-10（ハイフン可）を検査して ISBN-13 にそろえる。"""
    if not isinstance(raw, str):
        raise AuthError(400, "バーコードの番号を入力してください", "isbn")
    digits = re.sub(r"[\s-]", "", raw).upper()

    if re.fullmatch(r"\d{9}[\dX]", digits):
        if _isbn10_check(digits[:9]) != digits[9]:
            raise AuthError(400, "ISBN の番号が正しくありません（チェックディジット不一致）", "isbn")
        body = "978" + digits[:9]
        return body + _isbn13_check(body)

    if not re.fullmatch(r"\d{13}", digits):
        raise AuthError(400, "バーコードの下にある13桁の番号を入力してください", "isbn")
    if digits.startswith(("191", "192")):
        # 日本の書籍は2段バーコードで、下段（192…）は価格などのコード
        raise AuthError(400, "上段の 978 / 979 で始まるバーコードを読み取ってください", "isbn")
    if not digits.startswith(("978", "979")):
        raise AuthError(400, "書籍のバーコード（978 / 979 で始まる番号）ではありません", "isbn")
    if _isbn13_check(digits[:12]) != digits[12]:
        raise AuthError(400, "番号が正しくありません（読み取りか入力を確かめてください）", "isbn")
    return digits


def _isbn13_check(first12: str) -> str:
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def _isbn10_check(first9: str) -> str:
    total = sum(int(d) * (10 - i) for i, d in enumerate(first9))
    value = (11 - total % 11) % 11
    return "X" if value == 10 else str(value)


# ============ 書誌の検索 ============

class BookLookup:
    def __init__(self, google_api_key: Optional[str] = None, fetch_json=None, fetch_bytes=None):
        self.google_api_key = google_api_key
        # テストで差し替えられるように
        self._fetch_json = fetch_json or _fetch_json
        self._fetch_bytes = fetch_bytes or _fetch_bytes

    def lookup(self, raw_isbn: Any) -> Dict[str, Any]:
        isbn = normalize_isbn(raw_isbn)
        for source in (self._from_openbd, self._from_google):
            try:
                book = source(isbn)
            except Exception:  # noqa: BLE001 - 片方が落ちてももう片方で探す
                book = None
            if book and book.get("title"):
                return book
        raise AuthError(404, "この番号の書籍が見つかりませんでした。独自教材として登録できます", "isbn")

    def _from_openbd(self, isbn: str) -> Optional[Dict[str, Any]]:
        data = self._fetch_json(f"https://api.openbd.jp/v1/get?isbn={isbn}")
        summary = (data or [None])[0]
        summary = summary and summary.get("summary")
        if not summary:
            return None
        title = " ".join(x for x in (summary.get("title"), summary.get("volume")) if x).strip()
        return {
            "isbn": isbn,
            "title": title,
            "author": (summary.get("author") or "").strip(),
            "publisher": (summary.get("publisher") or "").strip(),
            "coverUrl": _safe_cover_url(summary.get("cover")),
            "source": "openBD",
        }

    def _from_google(self, isbn: str) -> Optional[Dict[str, Any]]:
        params = {"q": f"isbn:{isbn}"}
        if self.google_api_key:
            params["key"] = self.google_api_key
        data = self._fetch_json(
            "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(params))
        items = (data or {}).get("items") or []
        if not items:
            return None
        info = items[0].get("volumeInfo") or {}
        images = info.get("imageLinks") or {}
        title = " ".join(x for x in (info.get("title"), info.get("subtitle")) if x).strip()
        return {
            "isbn": isbn,
            "title": title,
            "author": "、".join(info.get("authors") or []),
            "publisher": info.get("publisher") or "",
            "coverUrl": _safe_cover_url(images.get("thumbnail") or images.get("smallThumbnail")),
            "source": "Google Books",
        }

    def download_cover(self, url: Any) -> Optional[bytes]:
        """許可したホストの表紙だけをダウンロードする（取れなければ None）。"""
        url = _safe_cover_url(url)
        if not url:
            return None
        try:
            data = self._fetch_bytes(url)
        except Exception:  # noqa: BLE001 - 表紙が取れなくても教材の追加は続ける
            return None
        return data if data and len(data) <= MAX_COVER_BYTES else None


def _safe_cover_url(url: Any) -> str:
    """表紙 URL を https にそろえ、許可したホスト以外なら "" を返す。"""
    if not isinstance(url, str) or not url:
        return ""
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in COVER_HOSTS:
        return ""
    return url


def _fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
        return json.loads(res.read().decode("utf-8"))


def _fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
        # リダイレクトで許可外のホストに飛ばされていないか確かめる
        if not _safe_cover_url(res.geturl()):
            return b""
        return res.read(MAX_COVER_BYTES + 1)
