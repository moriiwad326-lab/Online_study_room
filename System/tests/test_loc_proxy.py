"""/loc（LoC 推論サーバーへの WebSocket 中継）を検証する。"""

import asyncio
import json
import socket
import threading

import pytest

from System.auth import AuthStore
from System.books import BookLookup
from System.materials import MaterialStore
from System.server import SESSION_COOKIE, make_server
from System.tests.helpers import create_user, dummy_google


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def upstream_port():
    """受け取ったメッセージをそのまま返し、ハンドシェイクのヘッダーも1通目で返す偽の推論サーバー。"""
    websockets = pytest.importorskip("websockets.asyncio.server")
    port = free_port()
    ready = threading.Event()
    loop = asyncio.new_event_loop()
    stop = loop.create_future()

    async def handle(connection):
        headers = {k.lower(): v for k, v in connection.request.headers.raw_items()}
        await connection.send(json.dumps({"path": connection.request.path, "headers": headers}))
        async for message in connection:
            await connection.send(message)

    async def main():
        async with websockets.serve(handle, "127.0.0.1", port):
            ready.set()
            await stop

    thread = threading.Thread(target=loop.run_until_complete, args=(main(),), daemon=True)
    thread.start()
    ready.wait(5)
    yield port
    loop.call_soon_threadsafe(stop.set_result, None)
    thread.join(5)


@pytest.fixture
def app(tmp_path, upstream_port):
    static = tmp_path / "static"
    static.mkdir()
    db = tmp_path / "t.db"
    auth = AuthStore(db)
    httpd = make_server("127.0.0.1", 0, static, auth, MaterialStore(db), BookLookup(), dummy_google(),
                        loc_upstream=("127.0.0.1", upstream_port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{httpd.server_address[1]}", login_cookie(auth)
    httpd.shutdown()
    httpd.server_close()


def login_cookie(auth: AuthStore) -> str:
    return f"{SESSION_COOKIE}={auth.create_session(create_user(auth, 'taro')['id'])}"


def connect(host: str, **kwargs):
    from websockets.sync.client import connect as ws_connect
    return ws_connect(f"ws://{host}/loc", open_timeout=5, **kwargs)


def handshake_status(host: str, origin: str, cookie: str = "") -> int:
    """断られる場合のステータスコード。エラーは HTTP/1.0 で返るので websockets のクライアントでは読めず、
    生のソケットで見る（ブラウザは単に接続失敗として扱う）。"""
    name, port = host.rsplit(":", 1)
    lines = ["GET /loc HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade",
             "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version: 13",
             f"Origin: {origin}"]
    if cookie:
        lines.append(f"Cookie: {cookie}")
    with socket.create_connection((name, int(port)), timeout=10) as s:
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        status_line = s.makefile("rb").readline().decode()
    return int(status_line.split()[1])


def test_proxy_relays_messages_both_ways(app):
    host, cookie = app
    with connect(host, origin=f"http://{host}", additional_headers={"Cookie": cookie}) as ws:
        hello = json.loads(ws.recv(timeout=5))
        assert hello["path"] == "/loc"
        assert "cookie" not in hello["headers"]  # セッションは推論サーバーに渡さない
        frame = json.dumps({"type": "frame", "timestamp": 1, "image": "x" * 200_000})
        ws.send(frame)
        assert ws.recv(timeout=5) == frame


def test_proxy_requires_login(app):
    host, _ = app
    assert handshake_status(host, f"http://{host}") == 401


def test_proxy_rejects_other_origin(app):
    host, cookie = app
    status = handshake_status(host, "https://evil.example", cookie)
    assert status == 403


def test_proxy_reports_missing_upstream(tmp_path):
    pytest.importorskip("websockets")
    static = tmp_path / "static"
    static.mkdir()
    db = tmp_path / "t.db"
    auth = AuthStore(db)
    httpd = make_server("127.0.0.1", 0, static, auth, MaterialStore(db), BookLookup(), dummy_google(),
                        loc_upstream=("127.0.0.1", free_port()))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host = f"127.0.0.1:{httpd.server_address[1]}"
    try:
        assert handshake_status(host, f"http://{host}", login_cookie(auth)) == 502
    finally:
        httpd.shutdown()
        httpd.server_close()
