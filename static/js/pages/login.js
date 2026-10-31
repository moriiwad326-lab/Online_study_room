/**
 * pages/login.js : ログイン（#/login）
 *
 *  ログインIDとパスワードでログインする。
 *  ログイン後の行き先（タイムライン or 初回設定）は app.js のガードに任せる。
 *  新規登録・初回設定の画面も同じ見た目の枠（authShell）を使う。
 */

import { el, esc } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";

export const meta = { title: "ログイン" };

/** ロゴ付きの全画面の枠。inner に中身の HTML を渡す */
export function authShell(inner, { wide = false } = {}) {
  return el(`
    <div class="auth">
      <div class="auth__inner ${wide ? "auth__inner--wide" : ""}">
        <div class="logo auth__logo">
          <div class="logo__mark">O</div>
          <div class="logo__text">On<span>sta</span></div>
        </div>
        ${inner}
      </div>
    </div>
  `);
}

/** API が無いサーバーで開いているときの案内 */
export function serverNotice() {
  if (store.isAuthAvailable()) return "";
  return `
    <div class="notice notice--warn" style="margin-bottom:14px">
      ログイン用のサーバーに接続できません。プロジェクトのフォルダで
      <code>python main.py</code> を起動し、<code>http://localhost:8000/</code> を開いてください。
    </div>`;
}

export async function render({ navigate }) {
  const node = authShell(`
    <form class="card auth__card" novalidate>
      <h1 class="auth__title">ログイン</h1>
      ${serverNotice()}

      <div class="field">
        <label class="field__label" for="login-id">ログインID</label>
        <input class="input" id="login-id" name="loginId" autocomplete="username"
               autocapitalize="off" spellcheck="false" required autofocus />
      </div>
      <div class="field">
        <label class="field__label" for="login-password">パスワード</label>
        <input class="input" id="login-password" name="password" type="password"
               autocomplete="current-password" required />
      </div>

      <div class="notice notice--error u-hidden" data-error role="alert"></div>

      <button class="btn btn--wide auth__submit" type="submit">${icon("lock", 18)}ログイン</button>

      <p class="auth__switch">
        はじめての方は <a href="#/signup">新規登録</a>
      </p>
    </form>
  `);

  const form = node.querySelector("form");
  const errorBox = node.querySelector("[data-error]");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const loginId = form.elements.loginId.value.trim();
    const password = form.elements.password.value;
    if (!loginId || !password) {
      showError(errorBox, "ログインIDとパスワードを入力してください");
      return;
    }

    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    errorBox.classList.add("u-hidden");
    try {
      const user = await store.login(loginId, password);
      navigate(user.profileCompleted ? "#/timeline" : "#/setup");
    } catch (err) {
      showError(errorBox, err.message);
      form.elements.password.value = "";
      form.elements.password.focus();
      submit.disabled = false;
    }
  });

  return { node };
}

export function showError(box, message) {
  box.innerHTML = esc(message);
  box.classList.remove("u-hidden");
}
