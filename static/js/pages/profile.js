/**
 * pages/profile.js : プロフィール（#/profile）、プロフィール編集（#/profile/edit）
 *
 *  登録教材数・基本情報・自己紹介・タグを表示する。
 *  一番下に、ログアウトと保存データ（localStorage）を初期化するボタンを置いている。
 */

import { el, esc, formatDuration, toast, openModal } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";
import { renderProfileForm } from "./profileForm.js";

export const meta = { title: "プロフィール" };

/** 未設定の項目に出す文字 */
const UNSET = "未設定";

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
            <div class="u-small u-weak">${esc(me.handle)}</div>
            <div class="u-small u-muted">${esc(joinSet([me.grade, me.targetSchool && `志望：${me.targetSchool}`]))}</div>
          </div>
          <a class="btn btn--ghost btn--sm profile-head__edit" href="#/profile/edit">${icon("edit", 16)}編集</a>
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
          ${infoRow("ログインID", me.loginId)}
          ${infoRow("性別", me.gender)}
          ${infoRow("生まれた年", me.birthYear && `${me.birthYear}年（${thisYear - me.birthYear}歳ごろ）`)}
          ${infoRow("都道府県", me.prefecture)}
          ${infoRow("学年", me.grade)}
          ${infoRow("志望", me.targetSchool)}
          ${infoRow("目標", joinSet([me.examName, me.examDate && `（${me.examDate}）`], ""))}
          ${infoRow("週の目標", me.weeklyGoalMinutes == null ? "" : formatDuration(me.weeklyGoalMinutes))}
        </div>
      </section>

      <!-- 設定 -->
      <section class="card">
        <h2 class="card__title">設定</h2>
        <p class="u-small u-muted" style="margin-bottom:10px">
          学習記録といいねは、このブラウザの localStorage に保存されています。
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
      bodyHtml: `<p class="u-muted">自分の学習記録といいねがすべて消え、最初のサンプルデータに戻ります。</p>`,
    });
    if (!ok) return;
    store.resetLocal();
    toast("初期化しました。再読み込みします");
    setTimeout(() => location.reload(), 800);
  });

  node.querySelector('[data-act="logout"]').addEventListener("click", async () => {
    const ok = await openModal({
      title: "ログアウトしますか？",
      okLabel: "ログアウト",
      bodyHtml: `<p class="u-muted">学習記録はこのブラウザに残り、次にログインしたときにまた表示されます。</p>`,
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

  return { node };
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
