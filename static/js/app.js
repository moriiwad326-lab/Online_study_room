/**
 * app.js : アプリの入口。ハッシュルーティングと共通レイアウトを担当する。
 *
 * URL の形： #/ルート名/追加パラメータ?tab=タブ名
 *   例) #/timeline?tab=goal     #/record/m01     #/report?tab=me
 *
 * 各ページは js/pages/*.js にあり、次の形を守っている：
 *   export const meta = { title, tabs? }        … ヘッダーに出す情報
 *   export async function render(ctx)           … { node, destroy? } を返す
 */

import { $, el } from "./utils.js";
import { icon } from "./icons.js";
import * as store from "./store.js";

import * as timelinePage from "./pages/timeline.js";
import * as recordPage from "./pages/record.js";
import * as reportPage from "./pages/report.js";
import * as searchPage from "./pages/search.js";
import * as profilePage from "./pages/profile.js";

/** ルート名 → ページモジュールの対応表 */
const routes = {
  timeline: timelinePage,
  record: recordPage,
  report: reportPage,
  search: searchPage,
  profile: profilePage,
};

/** サイドバーに並べる項目 */
const navItems = [
  { route: "timeline", label: "タイムライン", icon: "timeline" },
  { route: "record",   label: "記録する",     icon: "record" },
  { route: "report",   label: "レポート",     icon: "report" },
  { route: "search",   label: "さがす",       icon: "search" },
  { route: "profile",  label: "もっと見る",   icon: "more" },
];

/** 今表示しているページの後片付け関数（タイマー解除など） */
let currentDestroy = null;

/* ============ ハッシュの解析 ============ */

/**
 * "#/report/xyz?tab=me" を { route:"report", param:"xyz", tab:"me" } にする
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
  };
}

/** ルート遷移用のヘルパー（ページ側からも使う） */
export function navigate(hash) {
  location.hash = hash.startsWith("#") ? hash : `#${hash}`;
}

/* ============ サイドバー ============ */

function renderSidebar(activeRoute) {
  const me = store.getMe();

  const html = `
    <div class="logo">
      <div class="logo__mark">O</div>
      <div class="logo__text">On<span>sta</span></div>
    </div>

    <a class="side-profile" href="#/profile">
      <div class="avatar avatar--sm" style="background:${me.avatarColor}">${me.initial}</div>
      <div class="side-profile__name">${me.name}</div>
    </a>

    <nav class="nav">
      ${navItems.map((item) => `
        <a class="nav__item ${item.route === activeRoute ? "is-active" : ""}"
           href="#/${item.route}">
          ${icon(item.icon, 21)}
          <span class="nav__label">${item.label}</span>
        </a>`).join("")}
    </nav>

    <div class="side-cta">
      <a class="btn btn--wide" href="#/record">${icon("plus", 18)}<span>記録する</span></a>
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
  const badges = store.getBadges();
  const tabs = page.meta?.tabs || [];
  const activeTab = ctx.tab || tabs[0]?.key;

  const html = `
    <div class="header__top">
      <h1 class="header__title">${page.meta?.title || "Onsta"}</h1>
      <div class="header__actions">
        <button class="icon-btn" type="button" aria-label="通知" data-act="notifications">
          ${icon("bell", 21)}
          ${badges.notifications ? `<span class="icon-btn__badge">${badges.notifications}</span>` : ""}
        </button>
        <button class="icon-btn" type="button" aria-label="メッセージ" data-act="messages">
          ${icon("message", 21)}
          ${badges.messages ? `<span class="icon-btn__badge">${badges.messages}</span>` : ""}
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

  // 通知・メッセージは今回はモックなので、押されたことだけ知らせる。
  // ヘッダー要素は使い回すので、addEventListener ではなく onclick で毎回上書きする
  // （そうしないとページを移るたびにハンドラが重なってしまう）。
  header.onclick = (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    import("./utils.js").then(({ toast }) =>
      toast(act === "notifications" ? "通知はまだありません（モック）" : "メッセージ機能は準備中です")
    );
  };
}

/* ============ ルーティング ============ */

async function router() {
  const ctx = parseHash();
  const page = routes[ctx.route] || routes.timeline;

  // 前のページのタイマーやカメラを必ず止める
  if (currentDestroy) {
    try { currentDestroy(); } catch (err) { console.warn("[app] 後片付けに失敗", err); }
    currentDestroy = null;
  }

  renderSidebar(ctx.route);
  renderHeader(page, ctx);

  // タブが未指定なら最初のタブを既定値にする
  const tabs = page.meta?.tabs || [];
  const context = { ...ctx, tab: ctx.tab || tabs[0]?.key || null, navigate };

  const pageRoot = $("#page");
  pageRoot.innerHTML = "";

  try {
    const result = await page.render(context);
    pageRoot.appendChild(result.node);
    currentDestroy = result.destroy || null;
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

  window.addEventListener("hashchange", router);

  // ハッシュが無いときはタイムラインへ
  if (!location.hash) location.hash = "#/timeline";
  else router();
}

boot();
