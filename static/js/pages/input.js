/**
 * pages/input.js : 記録の入力（#/input, #/input/<教材ID>?tab=manual|stopwatch）
 *
 *  カメラを使わずに学習記録をつける全画面のページ（Studyplus の「記録の入力」にならった形）。
 *   手動入力       … 教材・日時（「現時刻」で今に戻す）・学習時間・メモを入れて右上の「記録」で保存
 *   ストップウォッチ … スタート／一時停止／再開・リセット。右上の「完了」で止めて、
 *                     計った時間を入れた手動入力の画面に移る（そこで「記録」を押して保存）
 *
 *  ストップウォッチの状態はこの端末の localStorage に持つので、ほかの画面に移ったり
 *  ページを閉じたりしても計り続ける（LoC計測の教材一覧に「計測中」の入口が出る）。
 *  ストップウォッチは1つだけで、選んでいる教材をそのまま使う。
 *
 *  サイドバー・ヘッダーは出さない（app.js の fullRoutes）。
 */

import { el, esc, formatClock, formatDuration, thumbHtml, toast, openModal } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";

export const meta = { title: "記録の入力" };

const TABS = [
  { key: "manual", label: "手動入力" },
  { key: "stopwatch", label: "ストップウォッチ" },
];

/** 学習時間として入れられる最大（サーバーの上限と同じ24時間） */
const MAX_MINUTES = 24 * 60;

/* ============ ストップウォッチの保存 ============ */

/**
 * 保存する形：{ materialId, startedAt, accumulatedMs }
 *   startedAt     … 動いているときはスタート（再開）した時刻（ms）。止まっているときは null
 *   accumulatedMs … それまでに計った時間（一時停止までの合計）
 */
function stopwatchKey() {
  const user = store.getUser();
  return user ? `onsta.stopwatch.u${user.id}` : null;
}

export function loadStopwatch() {
  const empty = { materialId: null, startedAt: null, accumulatedMs: 0 };
  const key = stopwatchKey();
  if (!key) return empty;
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "null");
    if (!saved || typeof saved.accumulatedMs !== "number") return empty;
    return {
      materialId: saved.materialId == null ? null : String(saved.materialId),
      startedAt: typeof saved.startedAt === "number" ? saved.startedAt : null,
      accumulatedMs: Math.max(0, saved.accumulatedMs),
    };
  } catch {
    return empty;
  }
}

function saveStopwatch(sw) {
  const key = stopwatchKey();
  if (!key) return;
  try {
    if (!sw.startedAt && !sw.accumulatedMs) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(sw));
  } catch { /* 保存できなくても、この画面にいるあいだは計れる */ }
}

/** 今までに計った時間（ms） */
function elapsedMs(sw) {
  return sw.accumulatedMs + (sw.startedAt ? Math.max(0, Date.now() - sw.startedAt) : 0);
}

/** 計測中（または一時停止中で時間が残っている）ストップウォッチがあるか */
function hasStopwatch(sw) {
  return Boolean(sw.startedAt || sw.accumulatedMs);
}

/**
 * ストップウォッチが動いている・時間が残っているときに、ほかの画面に出す入口（なければ ""）。
 * LoC計測の教材一覧の一番上に出している。
 */
export function stopwatchBannerHtml() {
  const sw = loadStopwatch();
  if (!hasStopwatch(sw)) return "";
  const material = sw.materialId ? store.getMaterial(sw.materialId) : null;
  return `
    <a class="sw-banner" href="#/input/${material && !material.missing ? material.id : ""}?tab=stopwatch">
      ${icon("clock", 18)}
      <span class="sw-banner__text">
        <b>${sw.startedAt ? "ストップウォッチ計測中" : "ストップウォッチ一時停止中"}</b>
        <span class="u-small">${esc(material && !material.missing ? material.title : "教材未選択")}
          ・${formatClock(elapsedMs(sw) / 1000)}</span>
      </span>
      ${icon("chevronRight", 18)}
    </a>`;
}

/* ============ 日時 ============ */

/** Date → datetime-local の値（"2026-11-17T23:08"、端末の時刻で） */
function toLocalInput(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Date → "2026年11月17日 23:08" */
function formatDateTime(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/* ============ ページ ============ */

export async function render(ctx) {
  const materials = store.getMaterials();
  let sw = loadStopwatch();

  // 選んでいる教材。動いているストップウォッチがあれば、その教材にそろえる
  const findMine = (id) => (id ? materials.find((m) => m.id === String(id)) || null : null);
  let material = findMine(ctx.param) || null;
  if (hasStopwatch(sw)) {
    const swMaterial = findMine(sw.materialId);
    if (swMaterial && material && swMaterial.id !== material.id) {
      toast("ストップウォッチで計測中の教材を表示しています");
    }
    material = swMaterial || material;
  }
  if (!material && !ctx.param) material = materials[0] || null;

  const state = {
    tab: ctx.tab === "stopwatch" || (!ctx.tab && hasStopwatch(sw)) ? "stopwatch" : "manual",
    date: new Date(),
    hours: 0,
    minutes: 0,
    fromStopwatch: false, // 「完了」で手動入力に移ってきた（記録の種類を stopwatch にする）
    saving: false,
    timer: null,
  };

  const node = el(`
    <div class="rec-input">
      <header class="rec-input__bar">
        <button class="rec-input__close" type="button" data-act="close" aria-label="閉じる">${icon("close", 22)}</button>
        <h1 class="rec-input__title">記録の入力</h1>
        <button class="rec-input__action" type="button" data-act="action"></button>
      </header>

      <div class="rec-input__tabs">
        <div class="rec-tabs" role="tablist">
          ${TABS.map((t) => `
            <button class="rec-tabs__item" type="button" role="tab" data-tab="${t.key}">${t.label}</button>`).join("")}
        </div>
      </div>

      <!-- 手動入力 -->
      <div class="rec-input__panel" data-panel="manual">
        <section class="rec-group">
          <div class="rec-group__inner">
            <button class="rec-row rec-row--material" type="button" data-act="pick-material"></button>
            <div class="rec-row">
              <span class="rec-row__icon">${icon("calendar", 20)}</span>
              <label class="rec-row__date">
                <span data-date-label></span>
                <input type="datetime-local" class="rec-row__date-input" data-date-input aria-label="勉強した日時" />
              </label>
              <button class="link-btn rec-row__now" type="button" data-act="now">現時刻</button>
            </div>
            <div class="rec-row">
              <span class="rec-row__icon">${icon("clock", 20)}</span>
              <span class="rec-row__label">学習時間</span>
              <span class="rec-row__duration">
                <select class="rec-select" data-hours aria-label="時間">
                  ${Array.from({ length: 25 }, (_, h) => `<option value="${h}">${h}</option>`).join("")}
                </select><span>時間</span>
                <select class="rec-select" data-minutes aria-label="分">
                  ${Array.from({ length: 60 }, (_, m) => `<option value="${m}">${String(m).padStart(2, "0")}</option>`).join("")}
                </select><span>分</span>
              </span>
            </div>
          </div>
        </section>

        <section class="rec-group">
          <div class="rec-group__inner">
            <textarea class="rec-memo" data-memo maxlength="200" placeholder="要点・ひとことメモ"></textarea>
          </div>
        </section>
        <div class="rec-group__inner">
          <p class="u-small u-weak rec-input__note">カメラを使わない記録には LoC は付きません。</p>
          <div data-error></div>
        </div>
      </div>

      <!-- ストップウォッチ -->
      <div class="rec-input__panel" data-panel="stopwatch">
        <div class="sw">
          <button class="sw__material" type="button" data-act="pick-material" data-sw-material></button>
          <div class="sw__time tabular" data-sw-time>00:00:00</div>
          <div class="sw__controls">
            <button class="btn sw__start" type="button" data-act="toggle"></button>
            <button class="link-btn sw__reset" type="button" data-act="reset">リセット</button>
          </div>
        </div>
      </div>
    </div>
  `);

  const q = (sel) => node.querySelector(sel);
  const actionBtn = q('[data-act="action"]');
  const dateInput = q("[data-date-input]");
  const hoursSel = q("[data-hours]");
  const minutesSel = q("[data-minutes]");
  const memoInput = q("[data-memo]");
  const errorBox = q("[data-error]");

  /* ---- 描画 ---- */

  function totalMinutes() {
    return state.hours * 60 + state.minutes;
  }

  function drawTabs() {
    node.querySelectorAll("[data-tab]").forEach((b) => {
      const on = b.dataset.tab === state.tab;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", String(on));
    });
    node.querySelectorAll("[data-panel]").forEach((p) => p.classList.toggle("u-hidden", p.dataset.panel !== state.tab));
    drawAction();
  }

  /** 右上のボタン：手動入力では「記録」、ストップウォッチでは「完了」 */
  function drawAction() {
    if (state.tab === "manual") {
      actionBtn.textContent = state.saving ? "保存中…" : "記録";
      actionBtn.disabled = state.saving || !material || totalMinutes() < 1;
    } else {
      actionBtn.textContent = "完了";
      actionBtn.disabled = elapsedMs(sw) < 1000;
    }
  }

  function drawMaterial() {
    const row = q(".rec-row--material");
    row.innerHTML = material
      ? `${thumbHtml(material, "thumb--xs")}
         <span class="rec-row__material">
           <span class="rec-row__material-title">${esc(material.title)}</span>
           <span class="u-small u-weak">${esc(material.subjectName)}</span>
         </span>
         ${icon("chevronRight", 18)}`
      : `<span class="rec-row__icon">${icon("book", 20)}</span>
         <span class="rec-row__label rec-row__label--empty">教材を選択</span>
         ${icon("chevronRight", 18)}`;

    q("[data-sw-material]").innerHTML = material
      ? `${thumbHtml(material, "thumb--xl")}<span class="sw__title">${esc(material.title)}</span>`
      : `<span class="thumb thumb--xl sw__placeholder">${icon("book", 40)}</span>
         <span class="sw__title u-weak">教材を選択</span>`;
    drawAction();
  }

  function drawDate() {
    q("[data-date-label]").textContent = formatDateTime(state.date);
    dateInput.value = toLocalInput(state.date);
    dateInput.max = toLocalInput(new Date());
  }

  function drawDuration() {
    hoursSel.value = String(state.hours);
    minutesSel.value = String(state.minutes);
    // 24時間のときは分を選べない（上限が24時間のため）
    minutesSel.disabled = state.hours >= 24;
    drawAction();
  }

  function drawStopwatch() {
    q("[data-sw-time]").textContent = formatClock(elapsedMs(sw) / 1000);
    const toggle = q('[data-act="toggle"]');
    const running = Boolean(sw.startedAt);
    toggle.textContent = running ? "一時停止" : (sw.accumulatedMs ? "再開" : "スタート");
    toggle.classList.toggle("sw__start--running", running);
    q('[data-act="reset"]').disabled = !hasStopwatch(sw);
    if (state.tab === "stopwatch") drawAction();
  }

  /* ---- 操作 ---- */

  node.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    state.tab = b.dataset.tab;
    history.replaceState(null, "", `#/input/${material ? material.id : ""}?tab=${state.tab}`);
    drawTabs();
  }));

  node.querySelectorAll('[data-act="pick-material"]').forEach((b) => b.addEventListener("click", async () => {
    const picked = await pickMaterial(materials, material?.id);
    if (!picked) return;
    material = picked;
    // ストップウォッチは選んでいる教材を計る
    if (hasStopwatch(sw)) {
      sw.materialId = material.id;
      saveStopwatch(sw);
    }
    drawMaterial();
  }));

  q('[data-act="close"]').addEventListener("click", () => {
    if (history.length > 1) history.back();
    else ctx.navigate("#/record");
  });

  // 日時：行を押すと端末の日時ピッカーを開く（input は行の上に透明で重ねてある）
  dateInput.addEventListener("click", () => {
    try { dateInput.showPicker?.(); } catch { /* 開けないブラウザは input のままで選べる */ }
  });
  dateInput.addEventListener("change", () => {
    if (!dateInput.value) return;
    const picked = new Date(dateInput.value);
    if (Number.isNaN(picked.getTime())) return;
    if (picked.getTime() > Date.now()) {
      toast("未来の日時は選べません");
      state.date = new Date();
    } else {
      state.date = picked;
    }
    drawDate();
  });
  q('[data-act="now"]').addEventListener("click", () => {
    state.date = new Date();
    drawDate();
  });

  hoursSel.addEventListener("change", () => {
    state.hours = Number(hoursSel.value);
    if (state.hours >= 24) state.minutes = 0;
    drawDuration();
  });
  minutesSel.addEventListener("change", () => {
    state.minutes = Number(minutesSel.value);
    drawDuration();
  });

  /* ---- ストップウォッチ ---- */

  q('[data-act="toggle"]').addEventListener("click", async () => {
    if (!material) {
      const picked = await pickMaterial(materials, null);
      if (!picked) return;
      material = picked;
      drawMaterial();
    }
    if (sw.startedAt) {
      sw.accumulatedMs = elapsedMs(sw);
      sw.startedAt = null;
    } else {
      sw.startedAt = Date.now();
      sw.materialId = material.id;
    }
    saveStopwatch(sw);
    drawStopwatch();
  });

  q('[data-act="reset"]').addEventListener("click", async () => {
    if (!hasStopwatch(sw)) return;
    const ok = await openModal({
      title: "ストップウォッチをリセットしますか？",
      okLabel: "リセット",
      bodyHtml: `<p class="u-muted">計った時間（${formatClock(elapsedMs(sw) / 1000)}）は記録されずに 0 に戻ります。</p>`,
    });
    if (!ok) return;
    sw = { materialId: null, startedAt: null, accumulatedMs: 0 };
    saveStopwatch(sw);
    drawStopwatch();
  });

  /* ---- 右上のボタン ---- */

  actionBtn.addEventListener("click", async () => {
    if (state.tab === "stopwatch") {
      // 完了：止めて、計った時間を手動入力に入れる（保存は「記録」で）
      if (sw.startedAt) {
        sw.accumulatedMs = elapsedMs(sw);
        sw.startedAt = null;
        saveStopwatch(sw);
      }
      const minutes = Math.min(MAX_MINUTES, Math.max(1, Math.round(sw.accumulatedMs / 60000)));
      state.hours = Math.floor(minutes / 60);
      state.minutes = minutes % 60;
      state.date = new Date();
      state.fromStopwatch = true;
      state.tab = "manual";
      history.replaceState(null, "", `#/input/${material ? material.id : ""}?tab=manual`);
      drawDate();
      drawDuration();
      drawTabs();
      drawStopwatch();
      toast(`計った時間（${formatDuration(minutes)}）を入れました。「記録」で保存します`);
      return;
    }
    await save();
  });

  async function save() {
    if (state.saving || !material || totalMinutes() < 1) return;
    errorBox.innerHTML = "";
    state.saving = true;
    drawAction();
    try {
      await store.addRecord({
        materialId: material.id,
        durationMin: Math.min(MAX_MINUTES, totalMinutes()),
        memo: memoInput.value.trim(),
        source: state.fromStopwatch ? "stopwatch" : "manual",
        createdAt: state.date.toISOString(),
      });
    } catch (err) {
      errorBox.innerHTML = `<div class="notice notice--error" role="alert">記録を保存できませんでした：${esc(err.message)}</div>`;
      state.saving = false;
      drawAction();
      return;
    }
    // ストップウォッチの時間を記録したら 0 に戻す
    if (state.fromStopwatch) {
      sw = { materialId: null, startedAt: null, accumulatedMs: 0 };
      saveStopwatch(sw);
    }
    toast("記録しました");
    ctx.navigate("#/timeline");
  }

  /* ---- はじめの描画 ---- */

  drawMaterial();
  drawDate();
  drawDuration();
  drawStopwatch();
  drawTabs();
  state.timer = setInterval(() => {
    drawStopwatch();
    // 「現時刻」の上限を進めておく
    dateInput.max = toLocalInput(new Date());
  }, 250);

  if (materials.length === 0) {
    errorBox.innerHTML = `
      <div class="notice notice--warn">まだ教材がありません。
        <a class="link-btn" href="#/record/add">教材を追加</a>してから記録してください。</div>`;
  }

  return {
    node,
    destroy() { clearInterval(state.timer); },
  };
}

/**
 * 自分の教材から1冊選ぶモーダル。選んだ教材を返す（やめたら null）。
 */
async function pickMaterial(materials, currentId) {
  if (materials.length === 0) {
    toast("まだ教材がありません。先に教材を追加してください");
    return null;
  }
  let picked = null;
  const groups = store.getMaterialsBySubject();
  await openModal({
    title: "教材を選択",
    cancelLabel: "閉じる",
    bodyHtml: `
      <div class="pick-list">
        ${groups.map((g) => `
          <div class="pick-list__group">
            <span class="subject-chip" style="--chip-color:${esc(g.color)}">${esc(g.name)}</span>
            ${g.materials.map((m) => `
              <button class="pick-list__item ${m.id === currentId ? "is-active" : ""}" type="button" data-pick="${esc(m.id)}">
                ${thumbHtml(m, "thumb--xs")}
                <span class="pick-list__title">${esc(m.title)}</span>
                ${m.id === currentId ? icon("check", 18) : ""}
              </button>`).join("")}
          </div>`).join("")}
      </div>
      <a class="link-btn u-small" href="#/record/add" data-pick-add>${icon("plus", 14)}教材を追加</a>`,
    onMount(root, { close }) {
      root.querySelector('[data-act="ok"]').remove();
      root.addEventListener("click", (e) => {
        if (e.target.closest("[data-pick-add]")) { close(false); return; }
        const btn = e.target.closest("[data-pick]");
        if (!btn) return;
        picked = materials.find((m) => m.id === btn.dataset.pick) || null;
        close(true);
      });
    },
  });
  return picked;
}
