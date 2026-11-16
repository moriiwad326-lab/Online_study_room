/**
 * app.js : アプリの入口。ハッシュルーティングと共通レイアウトを担当する。
 *
 * URL の形： #/ルート名/追加パラメータ?tab=タブ名
 *   例) #/timeline?tab=goal     #/record/m01     #/report?tab=me
 *
 * 各ページは js/pages/*.js にあり、次の形を守っている：
 *   export const meta = { title, tabs? }        … ヘッダーに出す情報
 *   export async function render(ctx)           … { node, destroy?, shouldConfirmLeave? } を返す
 *
 * shouldConfirmLeave() が true を返すあいだ（LoC計測中）は、別の画面へ移ろうとすると
 * 「LoC計測を中断しますか？」と確かめる。ページを閉じる・再読み込みするときの確認は
 * ブラウザの標準のダイアログになる（文言は変えられない）ので、そのページ側で beforeunload を使う。
 *
 * ログインまわりの画面（authRoutes）はサイドバー・ヘッダーなしの全画面で出す。
 * 未ログインなら #/login へ、プロフィール未設定（初回ログイン）なら #/setup へ寄せる。
 *
 * ヘッダーの通知・メッセージの未読数は、ページを移るたびと BADGE_POLL_MS ごとにサーバーへ聞く。
 */

import { $, el, esc, openModal } from "./utils.js";
import { icon } from "./icons.js";
import * as store from "./store.js";
import { APP_VERSION } from "./version.js";
import { avatarHtml } from "./pages/people.js";

import * as timelinePage from "./pages/timeline.js";
import * as recordPage from "./pages/record.js";
import * as reportPage from "./pages/report.js";
import * as searchPage from "./pages/search.js";
import * as profilePage from "./pages/profile.js";
import * as loginPage from "./pages/login.js";
import * as setupPage from "./pages/setup.js";
import * as notificationsPage from "./pages/notifications.js";
import * as messagesPage from "./pages/messages.js";
import * as userPage from "./pages/user.js";
import * as adminPage from "./pages/admin.js";
import * as postPage from "./pages/post.js";
import * as helpPage from "./pages/help.js";

/** 未読数を聞き直す間隔（ms） */
const BADGE_POLL_MS = 30000;

/** ルート名 → ページモジュールの対応表 */
const routes = {
  timeline: timelinePage,
  record: recordPage,
  report: reportPage,
  search: searchPage,
  profile: profilePage,
  notifications: notificationsPage,
  messages: messagesPage,
  users: userPage,
  admin: adminPage,
  posts: postPage,
  help: helpPage,
};

/** サイドバー・ヘッダーを出さない全画面のページ */
const authRoutes = {
  login: loginPage,
  setup: setupPage,
};

/**
 * サイドバー（スマホでは下部タブバー）に並べる項目。
 * LoC計測は真ん中に置き、スマホでは丸い大きなボタンで目立たせる（primary）。
 */
const navItems = [
  { route: "timeline", label: "タイムライン", icon: "timeline" },
  { route: "report",   label: "レポート",     icon: "report" },
  { route: "record",   label: "LoC計測",      icon: "loc", primary: true },
  { route: "search",   label: "さがす",       icon: "search" },
  { route: "profile",  label: "もっと見る",   icon: "more" },
];

/** 管理者にだけ出す項目 */
const adminNavItem = { route: "admin", label: "管理", icon: "lock", className: "nav__item--admin" };

/** 今表示しているページの後片付け関数（タイマー解除など） */
let currentDestroy = null;
/** 今表示しているページの「離れる前に確かめるか」（LoC計測中なら true を返す） */
let currentLeaveGuard = null;
/** 今表示しているページのハッシュ（移動を取りやめたときに戻す先） */
let currentHash = "";

/* ============ ハッシュの解析 ============ */

/**
 * "#/report/xyz?tab=me" を { route:"report", param:"xyz", tab:"me", query } にする
 */
function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/timeline";
  const [pathPart, queryPart] = raw.split("?");
  const segments = pathPart.split("/").filter(Boolean);
  const query = new URLSearchParams(queryPart || "");

  return {
    route: segments[0] || "timeline",
    param: segments[1] || null,
    tab: query.get("tab"),
    query,
  };
}

/** ルート遷移用のヘルパー（ページ側からも使う） */
export function navigate(hash) {
  location.hash = hash.startsWith("#") ? hash : `#${hash}`;
}

/* ============ サイドバー ============ */

function renderSidebar(activeRoute) {
  const me = store.getMe();
  const items = store.isAdmin() ? [...navItems, adminNavItem] : navItems;

  const html = `
    <div class="logo">
      <div class="logo__mark">O</div>
      <div class="logo__text">On<span>sta</span></div>
      <a class="logo__version" href="#/help?tab=updates" title="更新情報">ver${APP_VERSION}</a>
    </div>

    <a class="side-profile" href="#/profile">
      ${avatarHtml(me, "avatar--sm")}
      <div class="side-profile__name">${esc(me.name)}</div>
    </a>

    <nav class="nav">
      ${items.map((item) => `
        <a class="nav__item ${item.primary ? "nav__item--primary" : ""} ${item.className || ""} ${item.route === activeRoute ? "is-active" : ""}"
           href="#/${item.route}">
          <span class="nav__icon">${icon(item.icon, item.primary ? 24 : 21)}</span>
          <span class="nav__label">${item.label}</span>
        </a>`).join("")}
    </nav>

    <div class="side-cta">
      <a class="btn btn--wide" href="#/record">${icon("loc", 18)}<span>LoC計測</span></a>
    </div>
  `;

  $("#sidebar").innerHTML = html;
}

/* ============ ヘッダー ============ */

/**
 * ヘッダー（タイトル・通知・タブ）を描く。
 * タブをクリックすると ?tab=... を書き換えるだけで、再描画はルーターに任せる。
 */
function renderHeader(page, ctx) {
  const tabs = page.meta?.tabs || [];
  const activeTab = ctx.tab || tabs[0]?.key;

  const html = `
    <div class="header__top">
      <h1 class="header__title">${page.meta?.title || "Onsta"}</h1>
      <div class="header__actions">
        <button class="icon-btn" type="button" aria-label="LoC とは？" title="LoC とは？" data-act="help">
          ${icon("help", 21)}
        </button>
        <button class="icon-btn" type="button" aria-label="通知" data-act="notifications">
          ${icon("bell", 21)}<span data-badge="notifications"></span>
        </button>
        <button class="icon-btn" type="button" aria-label="メッセージ" data-act="messages">
          ${icon("message", 21)}<span data-badge="messages"></span>
        </button>
      </div>
    </div>
    ${tabs.length ? `
      <div class="tabs">
        ${tabs.map((t) => `
          <a class="tabs__item ${t.key === activeTab ? "is-active" : ""}"
             href="#/${ctx.route}${ctx.param ? "/" + ctx.param : ""}?tab=${t.key}">${t.label}</a>
        `).join("")}
      </div>` : ""}
  `;

  const header = $("#header");
  header.innerHTML = html;
  renderBadges();

  // ヘッダー要素は使い回すので、addEventListener ではなく onclick で毎回上書きする
  // （そうしないとページを移るたびにハンドラが重なってしまう）。
  header.onclick = (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act) navigate(`#/${act}`);
  };
}

/** ヘッダーの未読バッジだけを描き直す（store が BADGES_EVENT を投げたとき） */
function renderBadges() {
  const badges = store.getBadges();
  document.querySelectorAll("#header [data-badge]").forEach((slot) => {
    const count = badges[slot.dataset.badge] || 0;
    slot.className = count ? "icon-btn__badge" : "";
    slot.textContent = count ? (count > 99 ? "99+" : String(count)) : "";
  });
}

/* ============ ルーティング ============ */

/**
 * ログイン状態に合わない画面なら、行くべき画面のハッシュを返す（そのままでよければ null）
 *  - 未ログイン               → ログイン画面
 *  - ログイン済み・初回設定前 → 初回設定画面
 *  - ログイン済み・設定済み   → ログイン・初回設定の画面には行かせない
 */
function redirectFor(route) {
  const user = store.getUser();
  if (!user) return route === "login" ? null : "#/login";
  if (!user.profileCompleted) return route === "setup" ? null : "#/setup";
  return authRoutes[route] ? "#/timeline" : null;
}

/**
 * ハッシュが変わったとき。LoC計測中なら URL をいったん元に戻して確かめ、
 * 「中断して移動」なら移り、「計測を続ける」ならそのままにする。
 */
async function onHashChange() {
  if (currentLeaveGuard?.() && location.hash !== currentHash) {
    const target = location.hash;
    // replaceState は hashchange を起こさないので、ここで router は動かない
    history.replaceState(null, "", currentHash || "#/timeline");
    if (!(await confirmLeaveMeasurement())) return;
    currentLeaveGuard = null;
    history.replaceState(null, "", target);
  }
  router();
}

/** 「LoC計測を中断しますか？」。中断して移るなら true */
export function confirmLeaveMeasurement() {
  return openModal({
    title: "LoC計測を中断しますか？",
    okLabel: "中断して移動",
    cancelLabel: "計測を続ける",
    bodyHtml: `<p class="u-muted">このページを離れると LoC計測は中断され、ここまでの計測は記録されません。<br>
      記録するときは「終了して記録」を押してください。</p>`,
  });
}

async function router() {
  const ctx = parseHash();
  currentHash = location.hash;
  currentLeaveGuard = null;

  // 前のページのタイマーやカメラを必ず止める
  if (currentDestroy) {
    try { currentDestroy(); } catch (err) { console.warn("[app] 後片付けに失敗", err); }
    currentDestroy = null;
  }

  const redirect = redirectFor(ctx.route);
  if (redirect) {
    // hashchange で router がもう一度呼ばれる
    navigate(redirect);
    return;
  }

  const isAuthPage = Boolean(authRoutes[ctx.route]);
  const page = authRoutes[ctx.route] || routes[ctx.route] || routes.timeline;
  document.body.classList.toggle("is-auth", isAuthPage);

  if (isAuthPage) {
    $("#sidebar").innerHTML = "";
    $("#header").innerHTML = "";
  } else {
    renderSidebar(ctx.route);
    renderHeader(page, ctx);
    store.refreshBadges();
  }

  // タブが未指定なら最初のタブを既定値にする
  const tabs = page.meta?.tabs || [];
  const context = { ...ctx, tab: ctx.tab || tabs[0]?.key || null, navigate };

  const pageRoot = $("#page");
  pageRoot.innerHTML = "";

  try {
    const result = await page.render(context);
    pageRoot.appendChild(result.node);
    currentDestroy = result.destroy || null;
    currentLeaveGuard = result.shouldConfirmLeave || null;
  } catch (err) {
    console.error("[app] ページの描画に失敗", err);
    pageRoot.appendChild(el(`<div class="notice notice--error">
      ページの表示に失敗しました：${err.message}
    </div>`));
  }

  // ページを切り替えたら先頭にスクロールを戻す
  window.scrollTo({ top: 0 });
}

/* ============ 起動 ============ */

async function boot() {
  try {
    await store.init();
  } catch (err) {
    // fetch が失敗する主な原因は file:// で開いていること
    document.body.innerHTML = `
      <div style="max-width:620px;margin:80px auto;padding:24px;font-family:sans-serif">
        <h1 style="font-size:18px;margin-bottom:12px">データを読み込めませんでした</h1>
        <p style="line-height:1.8">${err.message}</p>
        <p style="line-height:1.8">
          index.html を直接ダブルクリックで開くとこのエラーになります。<br>
          プロジェクトのフォルダで <code>python -m http.server 8000</code> を実行し、
          <code>http://localhost:8000/</code> を開いてください。
        </p>
      </div>`;
    return;
  }

  window.addEventListener("hashchange", onHashChange);
  window.addEventListener(store.BADGES_EVENT, renderBadges);
  // 開いていないタブでは聞きに行かない
  setInterval(() => {
    if (document.visibilityState === "visible" && store.getUser()) store.refreshBadges();
  }, BADGE_POLL_MS);

  // ハッシュが無いときはタイムラインへ
  if (!location.hash) location.hash = "#/timeline";
  else router();
}

boot();
