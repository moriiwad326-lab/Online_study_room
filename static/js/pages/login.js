/**
 * pages/login.js : ログイン（#/login）
 *
 *  Google アカウントでログインする。ログイン方法はこれだけで、パスワードは扱わない。
 *  招待されたメールアドレスなら、はじめてのログインでアカウントが作られる。
 *  ボタンはサーバーの /api/auth/google/start へ移動するだけで、以降はサーバーと Google がやりとりする。
 *  戻ってきたあとの行き先（タイムライン or 初回設定）は app.js のガードに任せる。
 *  初回設定の画面も同じ見た目の枠（authShell）を使う。
 */

import { el, esc } from "../utils.js";
import * as store from "../store.js";

export const meta = { title: "ログイン" };

/** サーバーが /#/login?error=<コード> で返す失敗の理由（URL の文字をそのまま画面に出さない） */
const ERROR_MESSAGES = {
  cancelled: "Google でのログインがキャンセルされました。",
  expired: "ログインの有効期限が切れました。もう一度お試しください。",
  unverified: "メールアドレスが確認されていない Google アカウントではログインできません。",
  not_invited: "この Google アカウントは登録を許可されていません。Onsta は招待制です。"
    + "招待を受けている場合や、以前のログインIDからの移行は、管理者に連絡してください。",
  failed: "Google でのログインに失敗しました。しばらくしてからもう一度お試しください。",
};

/** Google のブランドガイドラインにあるロゴ（色を変えてはいけない） */
const GOOGLE_LOGO = `
  <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
    <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
  </svg>`;

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
function serverNotice() {
  if (store.isAuthAvailable()) return "";
  return `
    <div class="notice notice--warn" style="margin-bottom:14px">
      ログイン用のサーバーに接続できません。プロジェクトのフォルダで
      <code>python main.py</code> を起動し、<code>http://localhost:8000/</code> を開いてください。
    </div>`;
}

export async function render({ query }) {
  const code = query.get("error");
  const message = code && (Object.hasOwn(ERROR_MESSAGES, code) ? ERROR_MESSAGES[code] : ERROR_MESSAGES.failed);
  // 再読み込みで同じエラーが出続けないように、URL からは消す
  if (code) history.replaceState(null, "", "#/login");

  const node = authShell(`
    <div class="card auth__card">
      <h1 class="auth__title">ログイン</h1>
      ${serverNotice()}
      ${message ? `<div class="notice notice--error" role="alert">${esc(message)}</div>` : ""}

      <a class="btn-google" href="/api/auth/google/start">${GOOGLE_LOGO}Google でログイン</a>

      <p class="auth__switch">
        Onsta は招待制です。招待された Google アカウントは、はじめてのログインで登録されます。
      </p>
    </div>
  `);

  return { node };
}
