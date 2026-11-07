/**
 * pages/search.js : さがす（#/search）
 *
 *  - 検索バー（入力すると、ニックネームでユーザーを探し、自分の教材と記事を絞り込む）
 *  - 「ユーザー／教材／大学をさがす」カード（ユーザーは検索バーへ。教材・大学はモック）
 *  - 記事一覧（モックデータ）
 */

import { el, esc, formatRelative, thumbHtml } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";
import { personRowHtml, bindFollowButtons } from "./people.js";

export const meta = { title: "さがす" };

/** 「さがす」カードのキーとアイコンの対応 */
const findIcons = { user: "user", book: "book", school: "school" };

/** 入力が止まってからユーザーを検索するまでの時間（ms） */
const USER_SEARCH_DELAY_MS = 300;

export async function render() {
  const articles = store.getArticles();
  const materials = store.getMaterials();

  const node = el(`
    <div>
      <!-- 検索バー -->
      <div class="search-bar">
        ${icon("search", 18)}
        <input type="search" placeholder="ユーザー（ニックネーム）・教材・記事で検索" data-search aria-label="検索">
      </div>

      <!-- 何をさがすか -->
      <section class="section">
        <div class="grid-3">
          ${store.getFindTargets().map((t) => `
            <button class="find-card" type="button" data-find="${t.key}">
              <div class="find-card__icon">${icon(findIcons[t.key] || "search", 22)}</div>
              <div>${esc(t.label)}</div>
              <div class="u-small u-weak u-bold">${esc(t.desc)}</div>
            </button>`).join("")}
        </div>
      </section>

      <!-- 検索結果（入力があるときだけ表示） -->
      <section class="section u-hidden" data-user-results>
        <div class="section__head"><h2 class="section__title">ユーザー</h2></div>
        <div class="card" data-user-list></div>
      </section>

      <section class="section u-hidden" data-results>
        <div class="section__head"><h2 class="section__title">教材の検索結果</h2></div>
        <div class="card" data-results-list></div>
      </section>

      <!-- 記事一覧 -->
      <section class="section">
        <div class="section__head">
          <h2 class="section__title">記事</h2>
          <span class="u-small u-weak">Onsta Journal</span>
        </div>
        <div class="card" data-articles></div>
      </section>
    </div>
  `);

  /* ---- 記事の描画 ---- */
  const articleBox = node.querySelector("[data-articles]");
  const drawArticles = (keyword = "") => {
    const list = articles.filter((a) => match(a.title + a.tag, keyword));
    articleBox.innerHTML = list.length
      ? list.map((a) => `
          <article class="article">
            <div class="article__thumb">${esc(a.tag)}</div>
            <div>
              <div class="article__title">${esc(a.title)}</div>
              <div class="u-small u-weak">${esc(a.source)}・${formatRelative(a.createdAt)}</div>
            </div>
          </article>`).join("")
      : `<div class="empty">該当する記事がありません</div>`;
  };
  drawArticles();

  /* ---- 教材の絞り込み ---- */
  const resultsSection = node.querySelector("[data-results]");
  const resultsList = node.querySelector("[data-results-list]");

  /* ---- ユーザーの検索（サーバーに聞く。打つたびに聞かないよう少し待つ） ---- */
  const userSection = node.querySelector("[data-user-results]");
  const userList = node.querySelector("[data-user-list]");
  bindFollowButtons(userList);
  let userTimer = null;
  let latestQuery = "";

  const searchUsers = async (keyword) => {
    latestQuery = keyword;
    try {
      const users = await store.searchUsers(keyword);
      if (keyword !== latestQuery) return;  // 古い問い合わせの結果は捨てる
      userList.innerHTML = users.length
        ? users.map(personRowHtml).join("")
        : `<div class="empty">該当するユーザーがいません</div>`;
    } catch (err) {
      if (keyword === latestQuery) userList.innerHTML = `<div class="notice notice--error">${esc(err.message)}</div>`;
    }
  };

  const input = node.querySelector("[data-search]");
  input.addEventListener("input", (e) => {
    const keyword = e.target.value.trim();
    drawArticles(keyword);

    clearTimeout(userTimer);
    if (!keyword) {
      latestQuery = "";
      userSection.classList.add("u-hidden");
      resultsSection.classList.add("u-hidden");
      return;
    }
    userSection.classList.remove("u-hidden");
    userList.innerHTML = `<div class="empty">さがしています…</div>`;
    userTimer = setTimeout(() => searchUsers(keyword), USER_SEARCH_DELAY_MS);
    resultsSection.classList.remove("u-hidden");

    const hits = materials.filter((m) => match(m.title + m.subjectName, keyword));
    resultsList.innerHTML = hits.length
      ? hits.map((m) => `
          <a class="material-card" href="#/record/${m.id}" style="border:none;padding:8px 0">
            ${thumbHtml(m, "thumb--sm")}
            <div>
              <div class="material-card__title">${esc(m.title)}</div>
              <div class="material-card__meta">${esc(m.subjectName)}</div>
            </div>
          </a>`).join("")
      : `<div class="empty">該当する教材がありません</div>`;
  });

  /* ---- 「さがす」カード（ユーザーは検索バーへ。ほかはモック） ---- */
  node.querySelectorAll("[data-find]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.find === "user") {
        input.focus();
        import("../utils.js").then(({ toast }) => toast("ニックネームを入力してください"));
        return;
      }
      import("../utils.js").then(({ toast }) => toast("この検索はモックです"));
    });
  });

  return { node, destroy() { clearTimeout(userTimer); } };
}

/** 大文字小文字を無視した部分一致 */
function match(text, keyword) {
  if (!keyword) return true;
  return text.toLowerCase().includes(keyword.toLowerCase());
}
