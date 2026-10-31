/**
 * pages/setup.js : 初回ログイン時のマイページ設定（#/setup）
 *
 *  プロフィールを一度も保存していないユーザーは、app.js のガードでここに連れてこられる。
 *  ニックネームを入れて保存すると設定済みになり、タイムラインへ進む。
 *  ほかの項目は任意で、あとからプロフィール画面の「編集」でも変えられる。
 */

import { esc, toast } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";
import { authShell } from "./login.js";
import { renderProfileForm } from "./profileForm.js";

export const meta = { title: "マイページの設定" };

export async function render({ navigate }) {
  const user = store.getUser();
  const node = authShell(`
    <div class="auth__welcome">
      <h1 class="auth__title">ようこそ、${esc(user.loginId)} さん</h1>
      <p class="u-muted">
        はじめにマイページを設定しましょう。<span class="u-bold">ニックネームだけ必須</span>で、
        ほかの項目は空欄のままでもあとから設定できます。
      </p>
    </div>
    <div data-form></div>
    <p class="auth__switch">
      <button class="link-btn" type="button" data-act="logout">${icon("logout", 15)}別のアカウントでログイン</button>
    </p>
  `, { wide: true });

  const form = renderProfileForm({
    submitLabel: "保存してはじめる",
    onSaved: () => {
      toast("マイページを設定しました");
      navigate("#/timeline");
    },
  });
  node.querySelector("[data-form]").replaceWith(form);

  node.querySelector('[data-act="logout"]').addEventListener("click", async () => {
    await store.logout().catch(() => {});
    navigate("#/login");
  });

  return { node };
}
