/**
 * pages/profile.js : プロフィール（#/profile）、プロフィール編集（#/profile/edit）
 *
 *  登録教材数・フォロー数・基本情報・自己紹介・タグを表示する。
 *  「友だちを招待」から友だちのメールアドレスを申請でき（管理者が承認すると招待される）、申請の結果も見られる。
 *  一番下に、ログアウトと保存データ（学習記録・いいね）を初期化するボタンを置いている。
 */

import { el, esc, formatDuration, formatRelative, toast, openModal } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";
import { renderProfileForm } from "./profileForm.js";

export const meta = { title: "プロフィール" };

/** 未設定の項目に出す文字 */
const UNSET = "未設定";

/** 招待申請の状態の表示 */
const REQUEST_STATUS = { pending: "承認待ち", approved: "承認済み", rejected: "見送り" };

export async function render({ param, navigate }) {
  if (param === "edit") return renderEdit(navigate);

  const me = store.getMe();
  const summary = store.getSummary();
  const materialCount = store.getMaterials().length;
  const thisYear = new Date().getFullYear();

  const node = el(`
    <div>
      <!-- 見出し -->
      <section class="card">
        <div class="profile-head">
          <div class="avatar avatar--lg" style="background:${me.avatarColor}">${esc(me.initial)}</div>
          <div>
            <div class="profile-head__name">${esc(me.name)}</div>
            <div class="u-small u-muted">${esc(joinSet([me.grade, me.targetSchool && `志望：${me.targetSchool}`]))}</div>
          </div>
          <a class="btn btn--ghost btn--sm profile-head__edit" href="#/profile/edit">${icon("edit", 16)}編集</a>
        </div>

        <div class="u-row follow-counts" data-follow-counts>
          <a href="#/users/${me.id}?tab=following"><b class="tabular" data-count="following">-</b> フォロー中</a>
          <a href="#/users/${me.id}?tab=followers"><b class="tabular" data-count="followers">-</b> フォロワー</a>
          <a class="link-btn" href="#/users/${me.id}">ほかの人からの見え方</a>
        </div>

        <div class="grid-3" style="margin-top:14px">
          <div class="stat">
            <div class="stat__label">登録教材</div>
            <div class="stat__value tabular">${materialCount}<span class="stat__unit">冊</span></div>
          </div>
          <div class="stat">
            <div class="stat__label">記録した回数</div>
            <div class="stat__value tabular">${summary.recordCount}<span class="stat__unit">回</span></div>
          </div>
          <div class="stat">
            <div class="stat__label">総学習時間</div>
            <div class="stat__value tabular">${formatDuration(summary.totalMin)}</div>
          </div>
        </div>
      </section>

      <!-- 自己紹介 -->
      <section class="card">
        <h2 class="card__title">自己紹介</h2>
        ${me.bio
          ? `<p class="u-muted" style="white-space:pre-wrap">${esc(me.bio)}</p>`
          : `<p class="u-weak">まだ自己紹介がありません</p>`}
        ${me.tags.length ? `
          <div style="margin-top:12px">
            ${me.tags.map((t) => `<span class="tag">#${esc(t)}</span>`).join("")}
          </div>` : ""}
      </section>

      <!-- 基本情報 -->
      <section class="card">
        <h2 class="card__title">基本情報</h2>
        <div class="info-list">
          ${infoRow("Google アカウント", me.email)}
          ${infoRow("性別", me.gender)}
          ${infoRow("生まれた年", me.birthYear && `${me.birthYear}年（${thisYear - me.birthYear}歳ごろ）`)}
          ${infoRow("都道府県", me.prefecture)}
          ${infoRow("学年", me.grade)}
          ${infoRow("志望", me.targetSchool)}
          ${infoRow("目標", joinSet([me.examName, me.examDate && `（${me.examDate}）`], ""))}
          ${infoRow("週の目標", me.weeklyGoalMinutes == null ? "" : formatDuration(me.weeklyGoalMinutes))}
        </div>
      </section>

      <!-- 友だちを招待 -->
      <section class="card">
        <h2 class="card__title">友だちを招待</h2>
        <p class="u-small u-muted" style="margin-bottom:10px">
          Onsta は招待制です。友だちの Google アカウントのメールアドレスを申請し、管理者が承認すると、
          友だちはそのアカウントでログインできるようになります（メールは送られないので、承認されたら伝えてください）。
        </p>
        <form class="invite-form" data-invite-form>
          <div class="field">
            <label class="field__label" for="invite-email">メールアドレス</label>
            <input class="input" id="invite-email" type="email" maxlength="254" required
                   placeholder="friend@gmail.com" autocomplete="off">
          </div>
          <div class="field">
            <label class="field__label" for="invite-message">管理者へのひとこと（任意）</label>
            <input class="input" id="invite-message" maxlength="200" placeholder="例）同じ塾の友だちです">
          </div>
          <div data-invite-error></div>
          <button class="btn btn--sm" type="submit">申請する</button>
        </form>
        <div data-invite-list></div>
      </section>

      <!-- 設定 -->
      <section class="card">
        <h2 class="card__title">設定</h2>
        <p class="u-small u-muted" style="margin-bottom:10px">
          学習記録はサーバーに保存され、フォローしている人のタイムラインにも表示されます。いいねはこのブラウザにだけ保存されます。
        </p>
        <div class="u-row" style="flex-wrap:wrap">
          <button class="btn btn--ghost btn--sm" type="button" data-act="reset">
            ${icon("more", 16)}保存データを初期化
          </button>
          <button class="btn btn--ghost btn--sm" type="button" data-act="logout">
            ${icon("logout", 16)}ログアウト
          </button>
        </div>
      </section>
    </div>
  `);

  // 保存データの初期化（確認してから消す）
  node.querySelector('[data-act="reset"]').addEventListener("click", async () => {
    const ok = await openModal({
      title: "保存データを初期化しますか？",
      okLabel: "初期化する",
      bodyHtml: `<p class="u-muted">自分の学習記録（サーバー上のものも）といいねがすべて消えます。元に戻せません。</p>`,
    });
    if (!ok) return;
    try {
      await store.resetData();
    } catch (err) {
      toast(`初期化できませんでした：${err.message}`);
      return;
    }
    toast("初期化しました。再読み込みします");
    setTimeout(() => location.reload(), 800);
  });

  node.querySelector('[data-act="logout"]').addEventListener("click", async () => {
    const ok = await openModal({
      title: "ログアウトしますか？",
      okLabel: "ログアウト",
      bodyHtml: `<p class="u-muted">学習記録はサーバーに残り、次にログインしたときにまた表示されます。</p>`,
    });
    if (!ok) return;
    try {
      await store.logout();
    } catch {
      // サーバー側の削除に失敗しても、画面上はログアウトさせる
    }
    toast("ログアウトしました");
    navigate("#/login");
  });

  loadFollowCounts(node, me.id);
  setupInviteForm(node);
  return { node };
}

/** フォロー数（自分の公開プロフィールから取る） */
async function loadFollowCounts(node, myId) {
  try {
    const person = await store.getPerson(myId);
    node.querySelector('[data-count="following"]').textContent = person.followingCount;
    node.querySelector('[data-count="followers"]').textContent = person.followerCount;
  } catch (err) {
    console.warn("[profile] フォロー数を取れませんでした", err);
  }
}

/** 友だちの招待申請のフォームと、これまでの申請の一覧 */
function setupInviteForm(node) {
  const form = node.querySelector("[data-invite-form]");
  const errorBox = node.querySelector("[data-invite-error]");
  const listBox = node.querySelector("[data-invite-list]");

  const drawList = async () => {
    try {
      const requests = await store.getMyInviteRequests();
      listBox.innerHTML = requests.length ? `
        <div class="info-list" style="margin-top:14px">
          ${requests.map((r) => `
            <div class="info-list__row">
              <span class="info-list__key request__email">${esc(r.email)}</span>
              <span><span class="status-chip status-chip--${r.status}">${REQUEST_STATUS[r.status]}</span>
                <span class="u-small u-weak">${formatRelative(r.createdAt)}</span></span>
            </div>`).join("")}
        </div>` : "";
    } catch (err) {
      console.warn("[profile] 招待申請の一覧を取れませんでした", err);
    }
  };
  drawList();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    errorBox.innerHTML = "";
    try {
      await store.referFriend(form.querySelector("#invite-email").value.trim(),
                              form.querySelector("#invite-message").value.trim());
      form.reset();
      toast("招待を申請しました。承認されたら通知でお知らせします");
      drawList();
    } catch (err) {
      errorBox.innerHTML = `<div class="notice notice--error" role="alert" style="margin-bottom:10px">${esc(err.message)}</div>`;
    } finally {
      button.disabled = false;
    }
  });
}

/** プロフィール編集（#/profile/edit） */
function renderEdit(navigate) {
  const node = renderProfileForm({
    submitLabel: "保存する",
    cancelHref: "#/profile",
    onSaved: () => {
      toast("プロフィールを保存しました");
      navigate("#/profile");
    },
  });
  return { node };
}

/** 空でない値だけを sep でつなぐ（全部空なら「未設定」） */
function joinSet(values, sep = "／") {
  return values.filter(Boolean).join(sep) || UNSET;
}

/** 基本情報の1行（空なら「未設定」を薄く出す） */
function infoRow(key, value) {
  const empty = value === "" || value == null || value === UNSET;
  return `
    <div class="info-list__row">
      <span class="info-list__key">${esc(key)}</span>
      <span class="${empty ? "u-weak" : ""}">${esc(empty ? UNSET : value)}</span>
    </div>`;
}
