/**
 * pages/setup.js : 初回ログイン時のマイページ設定（#/setup）
 *
 *  プロフィールを一度も保存していないユーザーは、app.js のガードでここに連れてこられる。
 *  ニックネームを入れて保存すると設定済みになり、タイムラインへ進む。
 *  ほかの項目は任意で、あとからプロフィール画面の「編集」でも変えられる。
 *
 *  運営アカウント（molix。サーバーの social.FEATURED_NICKNAME）が見つかれば、
 *  「フォローする」のチェックを出す（任意。最初はチェック済み）。保存できたらフォローする。
 */

import { el, esc, toast } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";
import { authShell } from "./login.js";
import { renderProfileForm } from "./profileForm.js";
import { avatarHtml, personSubline } from "./people.js";

export const meta = { title: "マイページの設定" };

export async function render({ navigate }) {
  const user = store.getUser();
  const node = authShell(`
    <div class="auth__welcome">
      <h1 class="auth__title">ようこそ、${esc(user.email)} さん</h1>
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

  // 運営アカウント（見つからない・取れないときは何も出さない）
  let featured = [];
  try {
    featured = (await store.getFeaturedUsers()).filter((p) => !p.isFollowing);
  } catch (err) {
    console.warn("[setup] 運営アカウントを取れませんでした", err);
  }

  const form = renderProfileForm({
    submitLabel: "保存してはじめる",
    onSaved: async () => {
      const toFollow = featured.filter((p) => followBox?.querySelector(`[data-featured="${p.id}"]`)?.checked);
      for (const person of toFollow) {
        try {
          await store.setFollowing(person.id, true);
        } catch (err) {
          console.warn("[setup] フォローできませんでした", err);
        }
      }
      toast(toFollow.length
        ? `マイページを設定し、${toFollow.map((p) => p.name).join("・")}をフォローしました`
        : "マイページを設定しました");
      navigate("#/timeline");
    },
  });

  // 保存ボタンのすぐ上に「運営アカウントをフォローする」を差し込む
  let followBox = null;
  if (featured.length) {
    followBox = el(`
      <section class="card featured-follow">
        <h2 class="card__title">運営アカウントをフォロー（任意）</h2>
        <p class="u-small u-muted" style="margin-bottom:10px">
          フォローすると、運営の勉強記録やお知らせがタイムラインに流れてきます。あとからいつでもやめられます。
        </p>
        ${featured.map((p) => `
          <label class="featured-follow__row">
            <input type="checkbox" data-featured="${p.id}" checked />
            ${avatarHtml(p)}
            <span class="featured-follow__text">
              <span class="u-bold">${esc(p.name)}</span><span class="chip">運営</span>
              ${personSubline(p) ? `<span class="u-small u-weak">${esc(personSubline(p))}</span>` : ""}
              ${p.bio ? `<span class="u-small u-muted featured-follow__bio">${esc(p.bio)}</span>` : ""}
            </span>
            <span class="u-small u-weak">フォローする</span>
          </label>`).join("")}
      </section>`);
    form.querySelector("[data-error]").before(followBox);
  }

  node.querySelector("[data-form]").replaceWith(form);

  node.querySelector('[data-act="logout"]').addEventListener("click", async () => {
    await store.logout().catch(() => {});
    navigate("#/login");
  });

  return { node };
}
