/**
 * pages/timeline.js : タイムライン（#/timeline）
 *
 *  - 「いま勉強中」のユーザーを横並びで表示し、LoC と継続時間を1秒ごとに更新する
 *  - 終了した学習記録は投稿カードとして並べ、平均LoC とスパークラインを添える
 */

import { el, esc, formatRelative, formatDuration, formatClock, clampLoc, locLevelClass } from "../utils.js";
import { icon } from "../icons.js";
import { gaugeHtml, updateGauge, drawSparkline } from "../charts.js";
import * as store from "../store.js";

export const meta = {
  title: "タイムライン",
  tabs: [
    { key: "follow", label: "フォロー" },
    { key: "goal", label: "目標" },
  ],
};

export async function render(ctx) {
  const node = el(`<div></div>`);
  const timers = [];

  if (ctx.tab === "goal") {
    node.appendChild(renderGoalTab());
  } else {
    // --- いま勉強中 ---
    const liveUsers = store.getLiveUsers();
    const liveSection = renderLiveSection(liveUsers);
    node.appendChild(liveSection);
    timers.push(startLiveTicker(liveSection, liveUsers));

    // --- 投稿一覧 ---
    const posts = store.getTimelinePosts();
    const list = el(`<div class="section"></div>`);
    if (posts.length === 0) {
      list.appendChild(el(`<div class="empty">まだ投稿がありません</div>`));
    } else {
      posts.forEach((post) => list.appendChild(renderPostCard(post)));
    }
    node.appendChild(list);

    // スパークラインは DOM に入ってからでないと幅が取れないので次フレームで描く
    requestAnimationFrame(() => {
      node.querySelectorAll("[data-spark]").forEach((canvas) => {
        const series = JSON.parse(canvas.dataset.spark);
        drawSparkline(canvas, series);
      });
    });
  }

  return {
    node,
    destroy() { timers.forEach(clearInterval); },
  };
}

/* ============ いま勉強中 ============ */

function renderLiveSection(liveUsers) {
  const section = el(`
    <section class="live">
      <div class="live__head">
        <span class="live__dot"></span>
        <h2 class="live__title">いま勉強中</h2>
        <span class="u-small u-weak">${liveUsers.length}人</span>
      </div>
      <div class="live__list"></div>
    </section>
  `);

  const list = section.querySelector(".live__list");
  liveUsers.forEach((user) => {
    list.appendChild(el(`
      <article class="live-card" data-live-id="${user.id}">
        ${gaugeHtml(user.loc, 62, 6)}
        <div class="live-card__name">${esc(user.name)}</div>
        <div class="live-card__material">${esc(user.material.title)}</div>
        <div class="live-card__timer tabular" data-live-timer>
          ${formatClock((Date.now() - user.startedAt) / 1000)}
        </div>
      </article>
    `));
  });

  return section;
}

/**
 * 1秒ごとに継続時間と LoC を更新する。
 * 本番では LoC はサーバーから配信される想定なので、ここではモックとして
 * 小さなランダムウォークで動かしている（locClient.js と同じ考え方）。
 */
function startLiveTicker(section, liveUsers) {
  const cards = new Map();
  liveUsers.forEach((user) => {
    const card = section.querySelector(`[data-live-id="${user.id}"]`);
    if (card) cards.set(user.id, { user, card, loc: user.loc });
  });

  return setInterval(() => {
    cards.forEach((entry) => {
      // 継続時間
      const elapsed = (Date.now() - entry.user.startedAt) / 1000;
      entry.card.querySelector("[data-live-timer]").textContent = formatClock(elapsed);

      // LoC（ゆるやかに揺らす）
      entry.loc = clampLoc(entry.loc + (Math.random() - 0.5) * 4);
      updateGauge(entry.card.querySelector("[data-gauge]"), entry.loc);
    });
  }, 1000);
}

/* ============ 投稿カード ============ */

function renderPostCard(post) {
  const hasLoc = typeof post.avgLoc === "number" && post.locSeries?.length > 1;

  const card = el(`
    <article class="card post">
      <header class="post__head">
        <div class="avatar" style="background:${post.avatarColor}">${esc(post.initial)}</div>
        <div>
          <div class="post__name">${esc(post.userName)}${post.isMine ? '<span class="u-small u-weak">（自分）</span>' : ""}</div>
          <div class="post__time">${formatRelative(post.createdAt)}</div>
        </div>
      </header>

      <div class="post__body">
        <!-- 教材サムネイル：画像を持たないので科目色のプレースホルダで表す -->
        <div class="thumb" style="background:${post.material.color}">${esc(post.material.title)}</div>
        <div class="post__info">
          <div class="post__material">${esc(post.material.title)}</div>
          <div class="u-small u-weak">${esc(post.material.subjectName)}</div>
          <div class="post__duration tabular">${formatDuration(post.durationMin)}</div>
          ${post.memo ? `<div class="post__memo">${esc(post.memo)}</div>` : ""}
        </div>
      </div>

      ${hasLoc ? `
        <div class="post__loc">
          <div>
            <div class="post__loc-label">平均LoC</div>
            <div class="post__loc-value loc-color ${locLevelClass(post.avgLoc)}">${post.avgLoc}</div>
          </div>
          <canvas class="sparkline" data-spark='${JSON.stringify(post.locSeries)}'></canvas>
        </div>` : ""}

      <footer class="post__foot">
        <button class="action-btn ${post.liked ? "is-on" : ""}" type="button" data-act="like">
          ${icon("like", 18)}<span data-like-count>${post.likes}</span>
        </button>
        <button class="action-btn" type="button" data-act="comment">
          ${icon("comment", 18)}<span>${post.comments}</span>
        </button>
      </footer>
    </article>
  `);

  // いいねの切り替え（localStorage に保存される）
  const likeBtn = card.querySelector('[data-act="like"]');
  let liked = post.liked;
  let count = post.likes;
  likeBtn.addEventListener("click", () => {
    liked = store.toggleLike(post.id, liked);
    count += liked ? 1 : -1;
    likeBtn.classList.toggle("is-on", liked);
    likeBtn.querySelector("[data-like-count]").textContent = Math.max(0, count);
  });

  card.querySelector('[data-act="comment"]').addEventListener("click", () => {
    import("../utils.js").then(({ toast }) => toast("コメント機能は準備中です"));
  });

  return card;
}

/* ============ 目標タブ ============ */

function renderGoalTab() {
  const wrap = el(`<div class="section"></div>`);
  const goals = store.getGoalPosts();

  goals.forEach((g) => {
    wrap.appendChild(el(`
      <article class="card">
        <header class="post__head">
          <div class="avatar" style="background:${g.avatarColor}">${esc(g.initial)}</div>
          <div>
            <div class="post__name">${esc(g.userName)}</div>
            <div class="post__time">${formatRelative(g.createdAt)}</div>
          </div>
        </header>
        <div class="u-row" style="gap:12px">
          <div style="color:var(--c-primary)">${icon("target", 26)}</div>
          <div style="flex:1">
            <div class="u-bold">${esc(g.goal)}</div>
            <div class="u-small u-muted">達成率 ${g.progress}%</div>
            <div style="height:6px;border-radius:3px;background:var(--c-border);margin-top:6px;overflow:hidden">
              <div style="height:100%;width:${g.progress}%;background:var(--c-primary)"></div>
            </div>
          </div>
        </div>
      </article>
    `));
  });

  return wrap;
}
