"""テストで共通して使う小道具。"""

from System.auth import AuthStore
from System.google_oauth import GoogleOAuth


def dummy_google() -> GoogleOAuth:
    """Google ログインを通らないテストのサーバーに渡す、使われない設定。"""
    return GoogleOAuth("test-client", "test-secret", "http://localhost")


def create_user(auth: AuthStore, name: str) -> dict:
    """招待 → 初回ログインの順でユーザーを作る。"""
    email = f"{name}@example.com"
    auth.invite(email)
    return auth.login_with_google(f"sub-{name}", email)
