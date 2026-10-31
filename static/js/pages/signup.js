/**
 * pages/signup.js : 新規登録（#/signup）
 *
 *  ログインIDとパスワードを決めてアカウントを作る。
 *  作ったらそのままログイン状態になり、初回設定（#/setup）へ進む。
 *  入力ルールはサーバー（System/auth.py）と同じものをここでも先にチェックする。
 */

import { icon } from "../icons.js";
import * as store from "../store.js";
import { authShell, serverNotice, showError } from "./login.js";

export const meta = { title: "新規登録" };

const LOGIN_ID_RE = /^[A-Za-z0-9_.@-]{4,64}$/;

export async function render({ navigate }) {
  const node = authShell(`
    <form class="card auth__card" novalidate>
      <h1 class="auth__title">新規登録</h1>
      ${serverNotice()}

      <div class="field" data-field="loginId">
        <label class="field__label" for="signup-id">ログインID</label>
        <input class="input" id="signup-id" name="loginId" autocomplete="username"
               autocapitalize="off" spellcheck="false" maxlength="64" required autofocus />
        <div class="field__hint">半角英数字と _ . - @ の4〜64文字（メールアドレスも可）。ログインにだけ使い、ほかの人には表示されません</div>
      </div>
      <div class="field" data-field="password">
        <label class="field__label" for="signup-password">パスワード</label>
        <input class="input" id="signup-password" name="password" type="password"
               autocomplete="new-password" maxlength="128" required />
        <div class="field__hint">8文字以上で、英字と数字を両方含めてください</div>
      </div>
      <div class="field" data-field="confirm">
        <label class="field__label" for="signup-confirm">パスワード（確認）</label>
        <input class="input" id="signup-confirm" name="confirm" type="password"
               autocomplete="new-password" maxlength="128" required />
      </div>

      <div class="notice notice--error u-hidden" data-error role="alert"></div>

      <button class="btn btn--wide auth__submit" type="submit">${icon("user", 18)}登録してはじめる</button>

      <p class="auth__switch">
        アカウントをお持ちの方は <a href="#/login">ログイン</a>
      </p>
    </form>
  `);

  const form = node.querySelector("form");
  const errorBox = node.querySelector("[data-error]");

  const fail = (message, fieldName) => {
    showError(errorBox, message);
    const wrap = fieldName && form.querySelector(`[data-field="${fieldName}"]`);
    if (wrap) {
      wrap.classList.add("is-invalid");
      wrap.querySelector("input")?.focus();
    }
  };

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    form.querySelectorAll(".field.is-invalid").forEach((f) => f.classList.remove("is-invalid"));
    errorBox.classList.add("u-hidden");

    const loginId = form.elements.loginId.value.trim();
    const password = form.elements.password.value;
    const confirm = form.elements.confirm.value;

    if (!LOGIN_ID_RE.test(loginId)) {
      return fail("ログインIDは半角英数字と _ . - @ の4〜64文字で入力してください", "loginId");
    }
    if (password.length < 8 || !/[A-Za-z]/.test(password) || !/\d/.test(password)) {
      return fail("パスワードは8文字以上で、英字と数字を両方含めてください", "password");
    }
    if (password !== confirm) {
      return fail("確認用のパスワードが一致しません", "confirm");
    }

    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      await store.register(loginId, password);
      navigate("#/setup");
    } catch (err) {
      fail(err.message, err.field);
      submit.disabled = false;
    }
  });

  return { node };
}
