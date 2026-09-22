/**
 * utils.js : どのページからも使う小さな道具箱
 *  - DOM を作るヘルパー
 *  - 時間や日付の整形
 *  - LoC の色分け判定
 */

/* ============ DOM ============ */

/** querySelector の短縮形。root を渡せばその中だけ探す。 */
export const $ = (selector, root = document) => root.querySelector(selector);
/** querySelectorAll の短縮形。配列で返すので map や forEach がそのまま使える。 */
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

/**
 * HTML 文字列から要素を作る。
 * テンプレートリテラルで組み立てた HTML を DOM にするときに使う。
 */
export function el(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html.trim();
  return tpl.content.firstElementChild;
}

/**
 * ユーザー入力などをそのまま HTML に埋めると危ないので、必ずこれを通す。
 * （メモ欄や検索語など、自由入力の値に使う）
 */
export function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ============ 時間の整形 ============ */

/** 秒 -> "HH:MM:SS"（ライブ表示や計測タイマー用） */
export function formatClock(totalSec) {
  const s = Math.max(0, Math.floor(totalSec));
  const h = String(Math.floor(s / 3600)).padStart(2, "0");
  const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const sec = String(s % 60).padStart(2, "0");
  return `${h}:${m}:${sec}`;
}

/** 分 -> "2時間15分"（0分のときは "0分"） */
export function formatDuration(totalMin) {
  const m = Math.max(0, Math.round(totalMin));
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (h === 0) return `${rest}分`;
  if (rest === 0) return `${h}時間`;
  return `${h}時間${rest}分`;
}

/** 投稿時刻 -> "3分前" / "2時間前" / "3日前" / "5月4日" */
export function formatRelative(isoString) {
  const then = new Date(isoString).getTime();
  const diffMin = Math.floor((Date.now() - then) / 60000);
  if (diffMin < 1) return "たった今";
  if (diffMin < 60) return `${diffMin}分前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}時間前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay}日前`;
  const d = new Date(then);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

/** Date -> "2026-09-22"（localStorage のキーや日別集計に使う） */
export function toDateKey(date) {
  const d = new Date(date);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** "2026-09-22" -> "9/22"（グラフの軸ラベル用） */
export function shortDateLabel(dateKey) {
  const [, m, d] = dateKey.split("-");
  return `${Number(m)}/${Number(d)}`;
}

/** 曜日（0=日）を日本語1文字で返す */
export function weekdayLabel(dateKey) {
  const names = ["日", "月", "火", "水", "木", "金", "土"];
  return names[new Date(`${dateKey}T00:00:00`).getDay()];
}

/** 今日から目標日まで残り何日か */
export function daysUntil(dateString) {
  const target = new Date(`${dateString}T00:00:00`).getTime();
  const today = new Date(toDateKey(new Date()) + "T00:00:00").getTime();
  return Math.max(0, Math.round((target - today) / 86400000));
}

/* ============ LoC（集中度）まわり ============ */

/** LoC を 0〜100 に丸める */
export function clampLoc(value) {
  return Math.min(100, Math.max(0, Math.round(Number(value) || 0)));
}

/**
 * LoC を3段階に分類する。
 * high: 70以上 / mid: 40〜69 / low: 40未満
 */
export function locLevel(loc) {
  const v = clampLoc(loc);
  if (v >= 70) return "high";
  if (v >= 40) return "mid";
  return "low";
}

/** ゲージなどに付ける状態クラス名 */
export function locLevelClass(loc) {
  return `is-loc-${locLevel(loc)}`;
}

/** 実際の色コード（Canvas に直接描くスパークライン用） */
export function locColor(loc) {
  const styles = getComputedStyle(document.documentElement);
  const map = {
    high: "--c-loc-high",
    mid: "--c-loc-mid",
    low: "--c-loc-low",
  };
  return styles.getPropertyValue(map[locLevel(loc)]).trim() || "#5b5bd6";
}

/** CSS 変数の値を取り出す（グラフの色を CSS 側と揃えるため） */
export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* ============ その他 ============ */

/** 配列の平均（空なら0） */
export function average(numbers) {
  if (!numbers || numbers.length === 0) return 0;
  return numbers.reduce((a, b) => a + b, 0) / numbers.length;
}

/** ざっくりユニークなID */
export function uid(prefix = "id") {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

/** 画面下に短いメッセージを出す */
export function toast(message) {
  const root = $("#toast-root");
  if (!root) return;
  const node = el(`<div class="toast">${esc(message)}</div>`);
  root.appendChild(node);
  setTimeout(() => node.remove(), 2600);
}

/**
 * 確認ダイアログ風のモーダルを開く。
 * body には HTML 文字列を渡せる。OK が押されたら true を解決する。
 */
export function openModal({ title, bodyHtml, okLabel = "OK", cancelLabel = "キャンセル", onMount }) {
  return new Promise((resolve) => {
    const backdrop = el(`
      <div class="modal-backdrop">
        <div class="modal" role="dialog" aria-modal="true">
          <h2 class="modal__title">${esc(title)}</h2>
          <div class="modal__body">${bodyHtml || ""}</div>
          <div class="modal__foot">
            <button class="btn btn--ghost" data-act="cancel">${esc(cancelLabel)}</button>
            <button class="btn" data-act="ok">${esc(okLabel)}</button>
          </div>
        </div>
      </div>
    `);

    const close = (result) => {
      backdrop.remove();
      document.removeEventListener("keydown", onKey);
      resolve(result);
    };
    const onKey = (e) => { if (e.key === "Escape") close(false); };

    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) close(false);
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "cancel") close(false);
      if (act === "ok") close(true);
    });
    document.addEventListener("keydown", onKey);

    $("#modal-root").appendChild(backdrop);
    // 追加のイベント（同意チェックで OK を有効化する等）を呼び出し側で仕込めるようにする
    if (onMount) onMount(backdrop, { close });
  });
}
