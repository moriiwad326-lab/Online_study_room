/**
 * pages/materialAdd.js : 教材を追加（#/record/add）
 *
 *  追加のしかたは2通り。どちらも最後は同じ「教材の情報」フォームで保存する。
 *   1. バーコード … カメラで読み取るか13桁を手入力 → 書名・表紙を検索してフォームに入れる
 *   2. 独自教材   … 教材名・画像・カテゴリを自分で入れる
 *
 *  カテゴリ（教科）はユーザーが自分で作る。フォームの中から新しく作ることもできる。
 *  画像はアップロード前にこの端末で縮小し（長辺 600px の JPEG）、data URL で送る。
 */

import { el, esc, toast, thumbHtml } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";
import { startScanner } from "../barcode.js";

/** カテゴリの色の候補 */
const CATEGORY_COLORS = [
  "#5b5bd6", "#2d9cdb", "#27ae60", "#e8724c",
  "#eb5a8c", "#f2994a", "#8e6fe0", "#3f4660",
];

const IMAGE_MAX_SIDE = 600;
const IMAGE_QUALITY = 0.85;
const NEW_CATEGORY = "__new";

export function renderAdd({ navigate, tab }) {
  const state = {
    mode: tab === "custom" ? "custom" : "barcode",
    isbn: "",        // バーコードで見つけた本の ISBN
    coverUrl: "",    // 検索で見つけた表紙（サーバーが取りに行く）
    image: "",       // アップロードした画像（data URL）。coverUrl より優先
    stopScan: null,
    scanId: 0,       // カメラ起動待ちのあいだに止められたかを見分ける番号
  };

  const node = el(`
    <div>
      <a class="u-small u-muted" href="#/record">&larr; 教材一覧にもどる</a>

      <div class="segmented" role="tablist" style="margin-top:10px">
        <button class="segmented__item" type="button" role="tab" data-mode="barcode">
          ${icon("camera", 17)}バーコードで追加
        </button>
        <button class="segmented__item" type="button" role="tab" data-mode="custom">
          ${icon("book", 17)}独自教材
        </button>
      </div>

      <!-- 1. バーコード -->
      <section class="card" data-panel="barcode">
        <h2 class="card__title">バーコードを読み取る</h2>
        <p class="u-small u-muted" style="margin-bottom:10px">
          本の裏表紙にある <strong>978</strong>（または 979）で始まるバーコードを読み取ります。
        </p>

        <div class="camera-box scan-box u-hidden" data-scan-box>
          <video playsinline muted autoplay data-scan-video></video>
          <div class="scan-box__guide" aria-hidden="true"></div>
        </div>
        <div class="u-row" style="flex-wrap:wrap;margin-bottom:14px">
          <button class="btn" type="button" data-act="scan">${icon("camera", 18)}カメラで読み取る</button>
          <button class="btn btn--ghost u-hidden" type="button" data-act="scan-stop">${icon("stop", 16)}読み取りをやめる</button>
        </div>

        <form class="isbn-form" data-isbn-form novalidate>
          <label class="field__label" for="isbn-input">バーコードの下の番号（13桁）を入力</label>
          <div class="u-row">
            <input class="input tabular" id="isbn-input" name="isbn" inputmode="numeric"
                   autocomplete="off" maxlength="17" placeholder="9784..." />
            <button class="btn btn--ghost" type="submit">${icon("search", 16)}検索</button>
          </div>
        </form>
        <div data-lookup style="margin-top:12px"></div>
      </section>

      <!-- 教材の情報（バーコードは検索で見つかってから出す） -->
      <form class="card" data-material-form novalidate>
        <h2 class="card__title" data-form-title>教材の情報</h2>

        <div class="material-form__image">
          <div data-preview></div>
          <div>
            <label class="btn btn--ghost btn--sm" for="mat-image">${icon("plus", 15)}画像を選ぶ</label>
            <input class="sr-only" id="mat-image" type="file" accept="image/jpeg,image/png,image/webp,image/gif" />
            <button class="link-btn u-small u-hidden" type="button" data-act="clear-image">画像を外す</button>
            <div class="field__hint">表紙の写真など（任意）。なければカテゴリの色で表示します</div>
            <div class="u-small u-weak" data-book-meta></div>
          </div>
        </div>

        <div class="field" data-field="title">
          <label class="field__label" for="mat-title">教材名<span class="field__req">必須</span></label>
          <input class="input" id="mat-title" name="title" maxlength="80" required
                 placeholder="例）青チャート 数学I+A" />
        </div>

        <div class="field" data-field="categoryId">
          <label class="field__label" for="mat-category">カテゴリ（教科）<span class="field__req">必須</span></label>
          <select class="select" id="mat-category" name="categoryId"></select>
        </div>

        <div class="new-category u-hidden" data-new-category>
          <div class="field" data-field="categoryName">
            <label class="field__label" for="mat-category-name">新しいカテゴリの名前</label>
            <input class="input" id="mat-category-name" name="categoryName" maxlength="20"
                   placeholder="例）数学、英単語、資格" />
          </div>
          <div class="field" data-field="categoryColor">
            <span class="field__label">色</span>
            <div class="color-picker" role="radiogroup" aria-label="カテゴリの色" data-colors></div>
          </div>
        </div>

        <div class="notice notice--error u-hidden" data-error role="alert"></div>

        <div class="profile-form__foot">
          <a class="btn btn--ghost" href="#/record">キャンセル</a>
          <button class="btn" type="submit">${icon("plus", 17)}教材を追加</button>
        </div>
      </form>
    </div>
  `);

  const $ = (sel) => node.querySelector(sel);
  const form = $("[data-material-form]");
  const lookupBox = $("[data-lookup]");
  const errorBox = $("[data-error]");

  /* ---------- モードの切り替え ---------- */

  function setMode(mode, { keepBook = false } = {}) {
    state.mode = mode;
    stopScan();
    node.querySelectorAll("[data-mode]").forEach((b) => {
      const on = b.dataset.mode === mode;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", String(on));
    });
    $('[data-panel="barcode"]').classList.toggle("u-hidden", mode !== "barcode");

    if (!keepBook) {
      Object.assign(state, { isbn: "", coverUrl: "", image: "" });
      form.elements.title.value = "";
      lookupBox.innerHTML = "";
    }
    $("[data-form-title]").textContent = mode === "barcode" ? "見つかった教材" : "教材の情報";
    // バーコードは本が見つかるまでフォームを出さない
    form.classList.toggle("u-hidden", mode === "barcode" && !state.isbn);
    renderPreview();
    clearErrors();
  }

  node.querySelectorAll("[data-mode]").forEach((b) =>
    b.addEventListener("click", () => setMode(b.dataset.mode)));

  /* ---------- バーコード：カメラ ---------- */

  function stopScan() {
    state.scanId += 1;
    state.stopScan?.();
    state.stopScan = null;
    $("[data-scan-box]").classList.add("u-hidden");
    $('[data-act="scan"]').classList.remove("u-hidden");
    $('[data-act="scan-stop"]').classList.add("u-hidden");
  }

  $('[data-act="scan"]').addEventListener("click", async () => {
    lookupBox.innerHTML = "";
    $("[data-scan-box]").classList.remove("u-hidden");
    $('[data-act="scan"]').classList.add("u-hidden");
    $('[data-act="scan-stop"]').classList.remove("u-hidden");
    const scanId = state.scanId;
    try {
      const stop = await startScanner($("[data-scan-video]"), (code) => {
        state.stopScan = null;
        stopScan();
        $("#isbn-input").value = code;
        lookup(code);
      });
      // 許可ダイアログなどを待つあいだに「やめる」やページ移動があったら、すぐ止める
      if (scanId !== state.scanId) stop();
      else state.stopScan = stop;
    } catch (err) {
      if (scanId !== state.scanId) return;
      stopScan();
      lookupBox.innerHTML = `<div class="notice notice--error">${esc(cameraErrorMessage(err))}<br>
        バーコードの下の13桁を入力して検索することもできます。</div>`;
    }
  });
  $('[data-act="scan-stop"]').addEventListener("click", stopScan);

  /* ---------- バーコード：番号で検索 ---------- */

  $("[data-isbn-form]").addEventListener("submit", (e) => {
    e.preventDefault();
    const raw = $("#isbn-input").value.trim();
    if (!raw) {
      lookupBox.innerHTML = `<div class="notice notice--error">番号を入力してください</div>`;
      return;
    }
    lookup(raw);
  });

  async function lookup(raw) {
    Object.assign(state, { isbn: "", coverUrl: "", image: "" });
    form.classList.add("u-hidden");
    lookupBox.innerHTML = `<div class="notice">「${esc(raw)}」を検索しています…</div>`;
    try {
      const book = await store.lookupBook(raw);
      Object.assign(state, { isbn: book.isbn, coverUrl: book.coverUrl || "" });
      form.elements.title.value = book.title;
      $("[data-book-meta]").textContent =
        [book.author, book.publisher, `ISBN ${book.isbn}`].filter(Boolean).join(" ／ ");
      lookupBox.innerHTML = `<div class="notice">${icon("spark", 15)} ${esc(book.source)} で見つかりました。
        内容を確かめて、カテゴリを選んでください。</div>`;
      form.classList.remove("u-hidden");
      renderPreview();
      form.elements.title.focus();
    } catch (err) {
      const notFound = err.status === 404;
      lookupBox.innerHTML = `
        <div class="notice ${notFound ? "notice--warn" : "notice--error"}">
          ${esc(err.message)}
          ${notFound ? `<div style="margin-top:8px">
            <button class="btn btn--sm" type="button" data-act="as-custom">${icon("book", 15)}独自教材として登録する</button>
          </div>` : ""}
        </div>`;
      lookupBox.querySelector('[data-act="as-custom"]')?.addEventListener("click", () => setMode("custom"));
    }
  }

  /* ---------- 画像 ---------- */

  function renderPreview() {
    const title = form.elements.title.value.trim() || "画像なし";
    const color = selectedCategoryColor();
    const imageUrl = state.image || state.coverUrl || null;
    $("[data-preview]").innerHTML = thumbHtml({ title, color, imageUrl });
    $('[data-act="clear-image"]').classList.toggle("u-hidden", !imageUrl);
    $("[data-book-meta]").classList.toggle("u-hidden", !state.isbn);
  }

  $("#mat-image").addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      state.image = await resizeImage(file);
      renderPreview();
    } catch {
      showError("画像を読み込めませんでした（JPEG / PNG / WebP / GIF を選んでください）", null);
    }
  });

  $('[data-act="clear-image"]').addEventListener("click", () => {
    state.image = "";
    state.coverUrl = "";
    renderPreview();
  });

  form.elements.title.addEventListener("input", renderPreview);

  /* ---------- カテゴリ ---------- */

  const select = form.elements.categoryId;
  const newBox = $("[data-new-category]");

  function renderCategories(selectedId = null) {
    const categories = store.getCategories();
    select.innerHTML = `
      ${categories.length ? "" : `<option value="" disabled>まだカテゴリがありません</option>`}
      ${categories.map((c) => `
        <option value="${c.id}" ${c.id === selectedId ? "selected" : ""}>${esc(c.name)}</option>`).join("")}
      <option value="${NEW_CATEGORY}" ${categories.length ? "" : "selected"}>＋ 新しいカテゴリを作る</option>`;

    // まだ使われていない色を最初に選んでおく
    const used = new Set(categories.map((c) => c.color));
    const firstFree = CATEGORY_COLORS.find((c) => !used.has(c)) || CATEGORY_COLORS[0];
    $("[data-colors]").innerHTML = CATEGORY_COLORS.map((c) => `
      <label class="color-picker__item" style="--swatch:${c}">
        <input type="radio" name="categoryColor" value="${c}" ${c === firstFree ? "checked" : ""} />
        <span class="sr-only">${c}</span>
      </label>`).join("");
    syncNewCategory();
  }

  function syncNewCategory() {
    newBox.classList.toggle("u-hidden", select.value !== NEW_CATEGORY);
    renderPreview();
  }

  function selectedCategoryColor() {
    if (select.value === NEW_CATEGORY) {
      return form.querySelector('[name="categoryColor"]:checked')?.value || CATEGORY_COLORS[0];
    }
    return store.getCategories().find((c) => String(c.id) === select.value)?.color || CATEGORY_COLORS[0];
  }

  select.addEventListener("change", () => {
    syncNewCategory();
    if (select.value === NEW_CATEGORY) form.elements.categoryName.focus();
  });
  $("[data-colors]").addEventListener("change", renderPreview);

  /* ---------- 保存 ---------- */

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearErrors();
    const title = form.elements.title.value.trim();
    if (!title) return showError("教材名を入力してください", "title");

    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      let categoryId = Number(select.value);
      if (select.value === NEW_CATEGORY) {
        const name = form.elements.categoryName.value.trim();
        if (!name) {
          showError("新しいカテゴリの名前を入力してください", "categoryName");
          return;
        }
        const category = await store.addCategory(name, selectedCategoryColor());
        categoryId = category.id;
        // 教材の保存で失敗しても、作ったカテゴリは選べるようにしておく
        renderCategories(category.id);
      }

      const material = await store.addMaterial({
        title,
        categoryId,
        isbn: state.isbn || undefined,
        image: state.image || undefined,
        coverUrl: !state.image && state.coverUrl ? state.coverUrl : undefined,
      });
      toast(`「${material.title}」を追加しました`);
      navigate("#/record");
    } catch (err) {
      showError(err.message, err.field);
    } finally {
      submit.disabled = false;
    }
  });

  function showError(message, fieldName) {
    errorBox.textContent = message;
    errorBox.classList.remove("u-hidden");
    const wrap = fieldName && form.querySelector(`[data-field="${fieldName}"]`);
    if (wrap) {
      wrap.classList.add("is-invalid");
      wrap.querySelector("input, select")?.focus();
    }
  }

  function clearErrors() {
    errorBox.classList.add("u-hidden");
    form.querySelectorAll(".field.is-invalid").forEach((f) => f.classList.remove("is-invalid"));
  }

  renderCategories();
  setMode(state.mode);

  return { node, destroy: stopScan };
}

/** カメラが使えなかった理由を日本語にする */
function cameraErrorMessage(err) {
  switch (err?.name) {
    case "NotAllowedError": return "カメラの使用が許可されていません。";
    case "NotFoundError": return "カメラが見つかりません。";
    case "NotReadableError": return "カメラがほかのアプリで使われています。";
    default: return err?.message || "カメラを起動できませんでした。";
  }
}

/**
 * 画像ファイルを長辺 IMAGE_MAX_SIDE 以下の JPEG（data URL）にする。
 * スマホの写真をそのまま送ると数MBになるため、送る前に縮める。
 */
async function resizeImage(file) {
  if (!file.type.startsWith("image/")) throw new Error("not an image");
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, IMAGE_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const ctx = canvas.getContext("2d");
  // 透過 PNG が黒くならないよう白で塗ってから描く
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close?.();
  return canvas.toDataURL("image/jpeg", IMAGE_QUALITY);
}
