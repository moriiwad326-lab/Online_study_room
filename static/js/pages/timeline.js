/**
 * pages/timeline.js : タイムライン（#/timeline）
 *
 *  - 「いま勉強中」：自分とフォロー中の人のうち LoC を計測中の人を横並びで出す。
 *    LIVE_POLL_MS ごとにサーバーへ聞き直して LoC・直近の推移を更新し、継続時間は1秒ごとに進める
 *  - 自分とフォロー中の人の学習記録を投稿カードとして並べ、平均LoC とスパークラインを添える
 *    （投稿カードは user.js のユーザーページでも使う）
 */

import { el, esc, formatRelative, formatDuration, formatClock, locLevelClass, thumbHtml } from "../utils.js";
import { icon } from "../icons.js";
import { gaugeHtml, updateGauge, drawSparkline } from "../charts.js";
import * as store from "../store.js";

/** 「いま勉強中」をサーバーに聞き直す間隔（ms）。計測画面が送ってくるのも約5秒ごと */
const LIVE_POLL_MS = 5000;

/** 計測の状態の表示（LoC の数字の代わりに出す） */
const LIVE_STATUS_LABEL = { warmup: "準備中", paused: "一時停止中" };

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
    // --- いま勉強中（誰も計測していないときはセクションごと隠す） ---
    const live = createLiveSection();
    node.appendChild(live.section);
    await live.refresh();
    timers.push(setInterval(live.refresh, LIVE_POLL_MS));
    timers.push(setInterval(live.tick, 1000));

    // --- 投稿一覧（自分＋フォロー中の人） ---
    const posts = await store.getTimelinePosts();
    const list = el(`<div class="section"></div>`);
    if (posts.length === 0) {
      list.appendChild(el(`
        <div class="empty">
          まだ投稿がありません。<br>
          勉強を記録するか、<a class="link-btn" href="#/search">ユーザーをさがして</a>フォローすると、ここに並びます。
        </div>`));
    } else {
      posts.forEach((post) => list.appendChild(renderPostCard(post)));
      if (!posts.some((p) => !p.isMine)) {
        list.appendChild(el(`
          <div class="empty">
            <a class="link-btn" href="#/search">ユーザーをさがして</a>フォローすると、その人の記録もここに並びます。
          </div>`));
      }
    }
    node.appendChild(list);
    drawSparklines(node);
  }

  return {
    node,
    destroy() { timers.forEach(clearInterval); },
  };
}

/* ============ いま勉強中 ============ */

/**
 * 「いま勉強中」のセクション。refresh() でサーバーの最新に合わせ、tick() で継続時間を1秒進める。
 * カードはユーザーごとに使い回し、増えた人は足し、終わった人は消す。
 */
function createLiveSection() {
  const section = el(`
    <section class="live u-hidden">
      <div class="live__head">
        <span class="live__dot"></span>
        <h2 class="live__title">いま勉強中</h2>
        <span class="u-small u-weak" data-live-count></span>
      </div>
      <div class="live__list"></div>
    </section>
  `);
  const list = section.querySelector(".live__list");
  const countEl = section.querySelector("[data-live-count]");
  /** ユーザーID → { card, session, fetchedAt } */
  const entries = new Map();
  let refreshing = false;

  async function refresh() {
    if (refreshing || document.visibilityState !== "visible") return;
    refreshing = true;
    try {
      const sessions = await store.getLiveSessions();
      const fetchedAt = Date.now();
      // 幅が取れないとスパークラインが描けないので、先に表示しておく
      section.classList.toggle("u-hidden", sessions.length === 0);
      countEl.textContent = `${sessions.length}人`;

      const alive = new Set();
      sessions.forEach((session) => {
        alive.add(session.user.id);
        let entry = entries.get(session.user.id);
        if (!entry) {
          entry = { card: el(liveCardHtml(session)) };
          entries.set(session.user.id, entry);
          list.appendChild(entry.card);
        }
        entry.session = session;
        entry.fetchedAt = fetchedAt;
        updateLiveCard(entry);
      });
      entries.forEach((entry, userId) => {
        if (!alive.has(userId)) {
          entry.card.remove();
          entries.delete(userId);
        }
      });
    } catch (err) {
      console.warn("[timeline] いま勉強中を取れませんでした", err);
    } finally {
      refreshing = false;
    }
  }

  function tick() {
    entries.forEach((entry) => {
      entry.card.querySelector("[data-live-timer]").textContent = formatClock(liveElapsedSec(entry));
    });
  }

  return { section, refresh, tick };
}

/** 継続時間。一時停止中でなければ、最後に届いてからの時間も足して進める */
function liveElapsedSec({ session, fetchedAt }) {
  if (session.status === "paused") return session.elapsedSec;
  return session.elapsedSec + session.secondsSinceUpdate + (Date.now() - fetchedAt) / 1000;
}

function liveCardHtml(session) {
  const { user } = session;
  return `
    <a class="live-card" href="#/users/${user.id}">
      ${gaugeHtml(0, 62, 6)}
      <div class="live-card__name">${esc(user.name)}${session.isMine ? '<span class="u-weak">（自分）</span>' : ""}</div>
      <div class="live-card__material" data-live-material></div>
      <div class="live-card__state" data-live-state></div>
      <canvas class="live-card__spark" data-live-spark aria-hidden="true"></canvas>
      <div class="live-card__timer tabular" data-live-timer></div>
    </a>`;
}

/** カードを今の状態に合わせる（LoC・状態・直近の推移・教材・継続時間） */
function updateLiveCard(entry) {
  const { card, session } = entry;
  const gauge = card.querySelector("[data-gauge]");
  const hasLoc = typeof session.loc === "number";
  updateGauge(gauge, hasLoc ? session.loc : 0);
  gauge.classList.toggle("gauge--none", !hasLoc);
  if (!hasLoc) {
    gauge.classList.remove("is-loc-high", "is-loc-mid", "is-loc-low");
    gauge.querySelector("[data-gauge-value]").textContent = "—";
  }
  card.classList.toggle("is-paused", session.status === "paused");

  card.querySelector("[data-live-material]").textContent = session.material.title;
  card.querySelector("[data-live-state]").textContent =
    LIVE_STATUS_LABEL[session.status] || (hasLoc ? "" : "LoC計測なし");

  const spark = card.querySelector("[data-live-spark]");
  const showSpark = session.recent.length >= 2;
  spark.classList.toggle("u-hidden", !showSpark);
  if (showSpark) drawSparkline(spark, session.recent);

  card.querySelector("[data-live-timer]").textContent = formatClock(liveElapsedSec(entry));
}

/* ============ 投稿カード ============ */

/** 投稿カードのスパークラインを描く。DOM に入ってからでないと幅が取れないので次フレームで描く */
export function drawSparklines(root) {
  requestAnimationFrame(() => {
    root.querySelectorAll("[data-spark]").forEach((canvas) => {
      drawSparkline(canvas, JSON.parse(canvas.dataset.spark));
    });
  });
}

export function renderPostCard(post) {
  const hasLoc = typeof post.avgLoc === "number" && post.locSeries?.length > 1;

  const card = el(`
    <article class="card post">
      <a class="post__head post__head--link" href="#/users/${post.userId}">
        <div class="avatar" style="background:${esc(post.avatarColor)}">${esc(post.initial)}</div>
        <div>
          <div class="post__name">${esc(post.userName)}${post.isMine ? '<span class="u-small u-weak">（自分）</span>' : ""}</div>
          <div class="post__time">${formatRelative(post.createdAt)}</div>
        </div>
      </a>

      <div class="post__body">
        <!-- 教材サムネイル：画像が無ければ科目色のプレースホルダで表す -->
        ${thumbHtml(post.material)}
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

  // いいねの切り替え（このブラウザの localStorage にだけ保存され、相手には届かない）
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

  if (goals.length === 0) {
    wrap.appendChild(el(`<div class="empty">まだ目標の投稿がありません</div>`));
    return wrap;
  }

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
