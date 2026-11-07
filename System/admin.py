"""招待（新規登録を許すメールアドレス）を管理する CLI。

Web の API からは招待を操作できない。サーバーの DB に触れる管理者だけがこれで操作する。

使い方:
    python -m System.admin invite someone@gmail.com          # 新しい人を招待する
    python -m System.admin invite someone@gmail.com --user 2 # 旧アカウント（ID 2）に結び付ける
    python -m System.admin uninvite someone@gmail.com        # 未使用の招待を取り消す
    python -m System.admin list                              # 招待とユーザーの一覧
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .auth import AuthError, AuthStore
from .db import DEFAULT_DB_PATH


def _print_list(store: AuthStore) -> None:
    print("未使用の招待:")
    invitations = store.list_invitations()
    if not invitations:
        print("  （なし）")
    for invite in invitations:
        link = f"  → ユーザー {invite['user_id']} に結び付ける" if invite["user_id"] else ""
        print(f"  {invite['email']}{link}")

    print("ユーザー:")
    users = store.list_users()
    if not users:
        print("  （なし）")
    for user in users:
        name = user["nickname"] or "（ニックネーム未設定）"
        if user["linked"]:
            print(f"  {user['id']}: {user['email']} / {name}")
        else:
            print(f"  {user['id']}: [未結び付け・ログイン不可] 旧ログインID={user['legacyLoginId']} / {name}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m System.admin", description="Onsta の招待を管理する")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="ユーザー情報を保存する SQLite ファイル")
    commands = parser.add_subparsers(dest="command", required=True)

    invite = commands.add_parser("invite", help="Google アカウントのメールアドレスを招待する")
    invite.add_argument("email")
    invite.add_argument("--user", type=int, help="このメールアドレスを結び付ける旧アカウントの ID（list で確認）")

    uninvite = commands.add_parser("uninvite", help="未使用の招待を取り消す")
    uninvite.add_argument("email")

    commands.add_parser("list", help="招待とユーザーの一覧を出す")
    args = parser.parse_args(argv)

    # 出力をファイルにリダイレクトしても、絵文字入りのニックネームなどで止まらないように
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    store = AuthStore(Path(args.db))
    try:
        if args.command == "invite":
            email = store.invite(args.email, args.user)
            target = f"（ログインするとユーザー {args.user} に結び付きます）" if args.user else ""
            print(f"{email} を招待しました{target}")
        elif args.command == "uninvite":
            if not store.uninvite(args.email):
                print(f"{args.email} の招待はありません")
                return 1
            print(f"{args.email} の招待を取り消しました")
        else:
            _print_list(store)
    except AuthError as err:
        print(f"エラー: {err.message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
