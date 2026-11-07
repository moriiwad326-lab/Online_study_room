"""Google アカウントでのログイン（OpenID Connect の認可コードフロー + PKCE）。

流れ:
    1. begin()  : state / nonce / PKCE を作ってサーバーに控え、Google の同意画面の URL を返す
    2. Google が redirect_uri へ ?state=...&code=... を付けて戻してくる
    3. finish() : state を照合して控えを取り出し（1回限り）、code を ID トークンに交換して検証する

ID トークンの署名は検証しない。トークンは TLS 越しに Google のトークンエンドポイントから直接、
クライアントシークレットを使って受け取ったものなので、OpenID Connect Core 3.1.3.7 により
TLS のサーバー検証で署名の検証に代えられる。ほかの経路から来た ID トークンを受け取ってはいけない。

要求するスコープは "openid email" だけで、アクセストークンは保存しない。
HTTP の扱いは `server.py`、アカウントの扱いは `auth.py` が持つ。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
SCOPE = "openid email"
START_PATH = "/api/auth/google/start"
CALLBACK_PATH = "/api/auth/google/callback"

FLOW_TTL_SEC = 10 * 60
# /start を連打されてもメモリが増え続けないように。超えたら古いものから捨てる
MAX_PENDING_FLOWS = 1024
TIMEOUT_SEC = 10
MAX_TOKEN_RESPONSE_BYTES = 64 * 1024


class OAuthFailure(Exception):
    """ログインを続けられないとき。code は画面側（pages/login.js）の文言に対応する。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail  # サーバーのログ用（画面には出さない）


class GoogleIdentity(NamedTuple):
    sub: str  # Google アカウントの不変の ID。本人の識別にはこちらを使う
    email: str


class _Flow(NamedTuple):
    nonce: str
    code_verifier: str
    expires_at: float


def _post_form(url: str, fields: Dict[str, str]) -> Dict[str, Any]:
    """フォームを POST して JSON を受け取る。証明書は urllib の既定どおり検証される。"""
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
        return json.loads(res.read(MAX_TOKEN_RESPONSE_BYTES))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class GoogleOAuth:
    def __init__(self, client_id: str, client_secret: str, public_url: str,
                 post_form: Callable[[str, Dict[str, str]], Dict[str, Any]] = _post_form,
                 clock: Callable[[], float] = time.time):
        self.client_id = client_id
        self.client_secret = client_secret
        self.public_url = public_url.rstrip("/")
        self.redirect_uri = self.public_url + CALLBACK_PATH
        self._post_form = post_form
        self._clock = clock
        self._flows: Dict[str, _Flow] = {}
        self._lock = threading.Lock()

    def begin(self) -> Tuple[str, str]:
        """ログインを始める。(state, Google の同意画面の URL) を返す。"""
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        now = self._clock()
        with self._lock:
            for key in [k for k, flow in self._flows.items() if flow.expires_at < now]:
                del self._flows[key]
            while len(self._flows) >= MAX_PENDING_FLOWS:
                del self._flows[next(iter(self._flows))]
            self._flows[state] = _Flow(nonce, code_verifier, now + FLOW_TTL_SEC)

        query = urllib.parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest()),
            "code_challenge_method": "S256",
            "prompt": "select_account",  # 共用の端末でも、どのアカウントで入るかを毎回選ばせる
        })
        return state, f"{AUTH_ENDPOINT}?{query}"

    def finish(self, state: str, code: str, error: str = "") -> GoogleIdentity:
        """Google から戻ってきた値を検証し、ログインした本人を返す。だめなら OAuthFailure。"""
        flow = self._take_flow(state)
        if error:
            raise OAuthFailure("cancelled" if error == "access_denied" else "failed",
                               f"Google から error={error[:64]!r}")
        if not code:
            raise OAuthFailure("failed", "code がありません")

        try:
            tokens = self._post_form(TOKEN_ENDPOINT, {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code_verifier": flow.code_verifier,
            })
        except urllib.error.HTTPError as err:
            raise OAuthFailure("failed", f"トークン交換に失敗: {err.code} {_oauth_error_name(err)}") from None
        except (OSError, ValueError) as err:
            raise OAuthFailure("failed", f"トークン交換に失敗: {type(err).__name__}") from None
        return self._verify_id_token(tokens.get("id_token") if isinstance(tokens, dict) else None, flow.nonce)

    def _take_flow(self, state: str) -> _Flow:
        """控えを1回限りで取り出す。同じ state は二度使えない。"""
        with self._lock:
            flow = self._flows.pop(state, None)
        if flow is None or flow.expires_at < self._clock():
            raise OAuthFailure("expired")
        return flow

    def _verify_id_token(self, id_token: Any, nonce: str) -> GoogleIdentity:
        claims = _decode_claims(id_token)
        if claims.get("iss") not in ISSUERS:
            raise OAuthFailure("failed", "ID トークンの iss が不正です")
        if claims.get("aud") != self.client_id:
            raise OAuthFailure("failed", "ID トークンの aud が不正です")

        expires_at = claims.get("exp")
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) \
                or expires_at <= self._clock():
            raise OAuthFailure("failed", "ID トークンの期限が切れています")

        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) \
                or not hmac.compare_digest(token_nonce.encode("utf-8"), nonce.encode("utf-8")):
            raise OAuthFailure("failed", "ID トークンの nonce が一致しません")

        sub, email = claims.get("sub"), claims.get("email")
        if not isinstance(sub, str) or not sub or not isinstance(email, str) or not email:
            raise OAuthFailure("failed", "ID トークンに sub / email がありません")
        if claims.get("email_verified") is not True:
            raise OAuthFailure("unverified")
        return GoogleIdentity(sub, email)


def _decode_claims(id_token: Any) -> Dict[str, Any]:
    parts = id_token.split(".") if isinstance(id_token, str) else []
    if len(parts) != 3:
        raise OAuthFailure("failed", "ID トークンの形式が不正です")
    try:
        claims: Optional[Any] = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        raise OAuthFailure("failed", "ID トークンの形式が不正です") from None
    if not isinstance(claims, dict):
        raise OAuthFailure("failed", "ID トークンの形式が不正です")
    return claims


def _oauth_error_name(err: urllib.error.HTTPError) -> str:
    """invalid_client など、設定ミスの切り分けに使う error の名前だけを取り出す。"""
    try:
        body = json.loads(err.read(MAX_TOKEN_RESPONSE_BYTES))
    except (OSError, ValueError):
        return ""
    name = body.get("error") if isinstance(body, dict) else None
    return name[:64] if isinstance(name, str) else ""
