/**
 * pages/profileForm.js : プロフィールの入力フォーム
 *
 *  初回ログイン時の設定（#/setup）とプロフィール編集（#/profile/edit）の両方で使う。
 *  ニックネームだけ必須で、ほかはすべて任意。
 *  保存はサーバー側でも検証するので、ここでのチェックは入力しやすさのためのもの。
 */

import { el, esc, toast } from "../utils.js";
import * as store from "../store.js";

/** アイコンの色の候補（科目色と同じトーンでそろえる） */
const AVATAR_COLORS = [
  "#5b5bd6", "#8e6fe0", "#eb5a8c", "#e8724c",
  "#f2994a", "#27ae60", "#2d9cdb", "#3f4660",
];

const GRADES = ["中学1年生", "中学2年生", "中学3年生", "高校1年生", "高校2年生", "高校3年生",
  "既卒生", "大学生", "社会人", "その他"];

const GENDERS = ["男性", "女性", "その他", "回答しない"];

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
  const thisYear = new Date().getFullYear();
  const goalHours = typeof p.weeklyGoalMinutes === "number" ? p.weeklyGoalMinutes / 60 : "";

  const node = el(`
    <form class="profile-form" novalidate>
      <!-- 見た目 -->
      <section class="card">
        <h2 class="card__title">アイコンと名前</h2>
        <div class="profile-form__avatar">
          <div class="avatar avatar--lg" data-preview style="background:${color}"></div>
          <div class="color-picker" role="radiogroup" aria-label="アイコンの色">
            ${AVATAR_COLORS.map((c) => `
              <label class="color-picker__item" style="--swatch:${c}">
                <input type="radio" name="avatarColor" value="${c}" ${c === color ? "checked" : ""} />
                <span class="sr-only">${c}</span>
              </label>`).join("")}
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
  const updatePreview = () => {
    const picked = node.querySelector('[name="avatarColor"]:checked')?.value || color;
    preview.style.background = picked;
    preview.textContent = Array.from(nicknameInput.value.trim())[0] || "?";
  };
  updatePreview();
  node.addEventListener("input", updatePreview);
  node.addEventListener("change", updatePreview);

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
      const user = await store.saveProfile(profile);
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
