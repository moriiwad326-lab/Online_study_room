/**
 * pages/profile.js : プロフィール（#/profile）
 *
 *  登録教材数・基本情報・自己紹介・タグを表示する。
 *  一番下に、保存データ（localStorage）を初期化するボタンを置いている。
 */

import { el, esc, formatDuration, toast, openModal } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";

export const meta = { title: "プロフィール" };

export async function render() {
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
            <div class="u-small u-muted">${esc(me.grade)}／志望：${esc(me.targetSchool)}</div>
          </div>
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
        <p class="u-muted" style="white-space:pre-wrap">${esc(me.bio)}</p>
        <div style="margin-top:12px">
          ${me.tags.map((t) => `<span class="tag">#${esc(t)}</span>`).join("")}
        </div>
      </section>

      <!-- 基本情報 -->
      <section class="card">
        <h2 class="card__title">基本情報</h2>
        <div class="info-list">
          ${infoRow("性別", me.gender)}
          ${infoRow("生まれた年", `${me.birthYear}年（${thisYear - me.birthYear}歳）`)}
          ${infoRow("都道府県", me.prefecture)}
          ${infoRow("学年", me.grade)}
          ${infoRow("志望", me.targetSchool)}
          ${infoRow("目標", `${me.examName}（${me.examDate}）`)}
          ${infoRow("週の目標", formatDuration(me.weeklyGoalMinutes))}
        </div>
      </section>

      <!-- 設定 -->
      <section class="card">
        <h2 class="card__title">設定</h2>
        <p class="u-small u-muted" style="margin-bottom:10px">
          学習記録といいねは、このブラウザの localStorage に保存されています。
        </p>
        <button class="btn btn--ghost btn--sm" type="button" data-act="reset">
          ${icon("more", 16)}保存データを初期化
        </button>
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

  return { node };
}

/** 基本情報の1行 */
function infoRow(key, value) {
  return `
    <div class="info-list__row">
      <span class="info-list__key">${esc(key)}</span>
      <span>${esc(value)}</span>
    </div>`;
}
