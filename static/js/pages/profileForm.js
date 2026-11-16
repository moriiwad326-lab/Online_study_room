/**
 * pages/profileForm.js : プロフィールの入力フォーム
 *
 *  初回ログイン時の設定（#/setup）とプロフィール編集（#/profile/edit）の両方で使う。
 *  ニックネームだけ必須で、ほかはすべて任意。
 *  保存はサーバー側でも検証するので、ここでのチェックは入力しやすさのためのもの。
 *
 *  プロフィール画像は、選んだ画像の真ん中を正方形に切り抜いて AVATAR_SIZE px に縮め、
 *  JPEG にしてから送る（スマホの写真をそのまま送ると大きすぎるため）。
 *  送るのは「保存」を押したとき（プロフィールを保存したあと）。画像を設定しない人は色の丸のまま。
 */

import { el, esc, toast } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";

/** アイコンの色の候補（科目色と同じトーンでそろえる） */
const AVATAR_COLORS = [
  "#5b5bd6", "#8e6fe0", "#eb5a8c", "#e8724c",
  "#f2994a", "#27ae60", "#2d9cdb", "#3f4660",
];

const GRADES = ["中学1年生", "中学2年生", "中学3年生", "高校1年生", "高校2年生", "高校3年生",
  "既卒生", "大学生", "社会人", "その他"];

const GENDERS = ["男性", "女性", "その他", "回答しない"];

/** プロフィール画像の一辺（px）と JPEG の画質 */
const AVATAR_SIZE = 256;
const AVATAR_QUALITY = 0.85;

const PREFECTURES = [
  "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
  "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
  "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
  "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
  "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
  "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
  "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
];

/**
 * フォームを作る。
 * @param {object} opts
 * @param {string} opts.submitLabel  保存ボタンの文言
 * @param {string} [opts.cancelHref] 指定するとキャンセルボタンを出す
 * @param {(user:object) => void} opts.onSaved 保存できたあとに呼ばれる
 */
export function renderProfileForm({ submitLabel, cancelHref, onSaved }) {
  const p = store.getProfile();
  const color = p.avatarColor || store.DEFAULT_AVATAR_COLOR;
  /** 画像：今の画像の URL と、保存時にすること（"upload" で pendingImage を送る / "delete" で消す） */
  let currentImageUrl = store.getMe().avatarUrl;
  let pendingImage = null;
  let imageAction = null;
  const thisYear = new Date().getFullYear();
  const goalHours = typeof p.weeklyGoalMinutes === "number" ? p.weeklyGoalMinutes / 60 : "";

  const node = el(`
    <form class="profile-form" novalidate>
      <!-- 見た目 -->
      <section class="card">
        <h2 class="card__title">アイコンと名前</h2>
        <div class="profile-form__avatar">
          <div class="avatar avatar--lg" data-preview style="background:${color}"></div>
          <div class="profile-form__avatar-controls">
            <div class="u-row" style="gap:8px;flex-wrap:wrap">
              <label class="btn btn--ghost btn--sm">
                ${icon("image", 16)}<span data-image-label>画像を選ぶ</span>
                <input type="file" accept="image/*" class="sr-only" data-image-input />
              </label>
              <button class="btn btn--ghost btn--sm u-hidden" type="button" data-act="remove-image">画像をやめる</button>
            </div>
            <div class="field__hint">画像を設定しないときは、下の色の丸にニックネームの1文字目が入ります</div>
            <div class="color-picker" role="radiogroup" aria-label="アイコンの色">
              ${AVATAR_COLORS.map((c) => `
                <label class="color-picker__item" style="--swatch:${c}">
                  <input type="radio" name="avatarColor" value="${c}" ${c === color ? "checked" : ""} />
                  <span class="sr-only">${c}</span>
                </label>`).join("")}
            </div>
          </div>
        </div>

        ${field("nickname", "ニックネーム", `
          <input class="input" id="pf-nickname" name="nickname" maxlength="20" required
                 autocomplete="nickname" value="${esc(p.nickname)}" placeholder="例）もりい" />`,
          { required: true, hint: "タイムラインなどでほかの人に表示されます（20文字まで）" })}

        ${field("bio", "自己紹介", `
          <textarea class="textarea" id="pf-bio" name="bio" maxlength="200"
                    placeholder="例）数学と物理を重点的にやっています">${esc(p.bio)}</textarea>`,
          { hint: "200文字まで" })}

        ${field("tags", "タグ", `
          <input class="input" id="pf-tags" name="tags" value="${esc((p.tags || []).join(" "))}"
                 placeholder="例）理系 数学 共通テスト" />`,
          { hint: "スペースかカンマで区切って10個まで" })}
      </section>

      <!-- 受験 -->
      <section class="card">
        <h2 class="card__title">受験・目標</h2>
        <div class="grid-2">
          ${field("grade", "学年", select("pf-grade", "grade", GRADES, p.grade))}
          ${field("targetSchool", "志望校", `
            <input class="input" id="pf-targetSchool" name="targetSchool" maxlength="50"
                   value="${esc(p.targetSchool)}" placeholder="例）東京大学 理科一類" />`)}
          ${field("examName", "目標の試験", `
            <input class="input" id="pf-examName" name="examName" maxlength="50"
                   value="${esc(p.examName)}" placeholder="例）共通テスト" />`)}
          ${field("examDate", "試験日", `
            <input class="input" id="pf-examDate" name="examDate" type="date" value="${esc(p.examDate)}" />`)}
        </div>
        ${field("weeklyGoalMinutes", "週の学習目標", `
          <div class="u-row">
            <input class="input" id="pf-weeklyGoalMinutes" name="weeklyGoalHours" type="number"
                   min="0" max="168" step="0.5" inputmode="decimal" style="max-width:140px"
                   value="${goalHours}" placeholder="例）30" />
            <span class="u-muted">時間</span>
          </div>`, { hint: "レポートの達成率に使います（未設定なら30時間）" })}
      </section>

      <!-- 基本情報 -->
      <section class="card">
        <h2 class="card__title">基本情報</h2>
        <div class="grid-2">
          ${field("gender", "性別", select("pf-gender", "gender", GENDERS, p.gender))}
          ${field("birthYear", "生まれた年", `
            <input class="input" id="pf-birthYear" name="birthYear" type="number" inputmode="numeric"
                   min="1900" max="${thisYear}" value="${p.birthYear ?? ""}" placeholder="例）2008" />`)}
          ${field("prefecture", "都道府県", select("pf-prefecture", "prefecture", PREFECTURES, p.prefecture))}
        </div>
      </section>

      <div class="notice notice--error u-hidden" data-error role="alert"></div>

      <div class="profile-form__foot">
        ${cancelHref ? `<a class="btn btn--ghost" href="${cancelHref}">キャンセル</a>` : ""}
        <button class="btn" type="submit">${esc(submitLabel)}</button>
      </div>
    </form>
  `);

  const preview = node.querySelector("[data-preview]");
  const nicknameInput = node.querySelector('[name="nickname"]');
  const imageInput = node.querySelector("[data-image-input]");
  const removeImageBtn = node.querySelector('[data-act="remove-image"]');
  const updatePreview = () => {
    const picked = node.querySelector('[name="avatarColor"]:checked')?.value || color;
    preview.style.background = picked;
    const imageUrl = pendingImage || (imageAction === "delete" ? null : currentImageUrl);
    preview.classList.toggle("avatar--img", Boolean(imageUrl));
    if (imageUrl) preview.innerHTML = `<img src="${esc(imageUrl)}" alt="" />`;
    else preview.textContent = Array.from(nicknameInput.value.trim())[0] || "?";
    removeImageBtn.classList.toggle("u-hidden", !imageUrl);
    node.querySelector("[data-image-label]").textContent = imageUrl ? "画像を変える" : "画像を選ぶ";
  };
  updatePreview();
  node.addEventListener("input", updatePreview);
  node.addEventListener("change", updatePreview);

  imageInput.addEventListener("change", async () => {
    const file = imageInput.files?.[0];
    imageInput.value = "";  // 同じ画像を選び直しても change が起きるように
    if (!file) return;
    try {
      pendingImage = await squareImage(file);
      imageAction = "upload";
    } catch {
      toast("この画像は読み込めませんでした。JPEG か PNG の画像を選んでください");
    }
    updatePreview();
  });

  removeImageBtn.addEventListener("click", () => {
    pendingImage = null;
    imageAction = currentImageUrl ? "delete" : null;
    updatePreview();
  });

  node.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorBox = node.querySelector("[data-error]");
    const submit = node.querySelector('button[type="submit"]');
    clearFieldErrors(node);
    errorBox.classList.add("u-hidden");

    const profile = readForm(node);
    if (!profile.nickname) {
      showError(node, errorBox, "ニックネームを入力してください", "nickname");
      return;
    }

    submit.disabled = true;
    try {
      let user = await store.saveProfile(profile);
      // 画像はプロフィールを保存したあとに送る（初回設定ではユーザーがまだ設定前のため）
      if (imageAction === "upload") user = await store.uploadAvatar(pendingImage);
      if (imageAction === "delete") user = await store.deleteAvatar();
      currentImageUrl = store.getMe().avatarUrl;
      pendingImage = null;
      imageAction = null;
      onSaved(user);
    } catch (err) {
      showError(node, errorBox, err.message, err.field);
      if (err.status === 401) {
        toast("ログインが切れました。もう一度ログインしてください");
        setTimeout(() => location.reload(), 1200);
      }
    } finally {
      submit.disabled = false;
    }
  });

  return node;
}

/**
 * 画像ファイルの真ん中を正方形に切り抜き、AVATAR_SIZE px の JPEG（data URL）にする。
 * スマホの写真の向き（EXIF）は、いまのブラウザは img に描くときに直してくれる。
 */
function squareImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const side = Math.min(img.naturalWidth, img.naturalHeight);
      if (!side) return reject(new Error("画像の大きさが取れません"));
      const canvas = document.createElement("canvas");
      canvas.width = AVATAR_SIZE;
      canvas.height = AVATAR_SIZE;
      const ctx = canvas.getContext("2d");
      // 透過 PNG の背景が黒くならないように白で塗っておく
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, AVATAR_SIZE, AVATAR_SIZE);
      ctx.drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side,
                    0, 0, AVATAR_SIZE, AVATAR_SIZE);
      resolve(canvas.toDataURL("image/jpeg", AVATAR_QUALITY));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("画像を読み込めません"));
    };
    img.src = url;
  });
}

/** フォームの値を API に送る形にする */
function readForm(form) {
  const value = (name) => form.elements[name]?.value.trim() ?? "";
  const number = (name) => (value(name) === "" ? null : Number(value(name)));
  const hours = number("weeklyGoalHours");

  return {
    nickname: value("nickname"),
    avatarColor: form.querySelector('[name="avatarColor"]:checked')?.value || "",
    bio: value("bio"),
    tags: value("tags").split(/[\s,、，]+/).filter(Boolean),
    grade: value("grade"),
    targetSchool: value("targetSchool"),
    examName: value("examName"),
    examDate: value("examDate"),
    weeklyGoalMinutes: hours === null || Number.isNaN(hours) ? hours : Math.round(hours * 60),
    gender: value("gender"),
    birthYear: number("birthYear"),
    prefecture: value("prefecture"),
  };
}

function showError(form, box, message, fieldName) {
  box.textContent = message;
  box.classList.remove("u-hidden");
  const wrap = fieldName && form.querySelector(`[data-field="${fieldName}"]`);
  if (wrap) {
    wrap.classList.add("is-invalid");
    wrap.querySelector("input, select, textarea")?.focus();
  } else {
    box.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function clearFieldErrors(form) {
  form.querySelectorAll(".field.is-invalid").forEach((f) => f.classList.remove("is-invalid"));
}

/** ラベル付きの入力欄1つ分 */
function field(name, label, control, { required = false, hint = "" } = {}) {
  return `
    <div class="field" data-field="${name}">
      <label class="field__label" for="pf-${name}">
        ${esc(label)}${required ? `<span class="field__req">必須</span>` : ""}
      </label>
      ${control}
      ${hint ? `<div class="field__hint">${esc(hint)}</div>` : ""}
    </div>`;
}

/** 未選択を含むセレクトボックス */
function select(id, name, options, current) {
  return `
    <select class="select" id="${id}" name="${name}">
      <option value="">未設定</option>
      ${options.map((o) => `<option ${o === current ? "selected" : ""}>${esc(o)}</option>`).join("")}
    </select>`;
}
