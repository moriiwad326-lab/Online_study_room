/**
 * pages/materialSheet.js : ほかの人の教材を見て、自分の教材に登録する（ver1.2）
 *
 *  openMaterialSheet(material, owner) … 教材の詳細のモーダル。自分の教材に登録できる
 *     （カテゴリは自分のものから選ぶか、その人のカテゴリと同じ名前・色で新しく作る）
 *  materialGridHtml(materials, opts)  … 教材をカテゴリごとに並べたカード（ユーザーページ・プロフィール）
 *  bindMaterialGrid(root, materials, owner) … カードを押したら詳細を開く
 *
 *  登録すると教材名・ISBN と、フォロー中の人の教材なら画像も自分の教材に写る（サーバーが決める）。
 *  同じ名前の教材をもう持っていれば「登録済み」にして、自分の教材の計測・記録に進めるようにする。
 *  自分の教材は詳細を出さず、LoC計測の画面（#/record/<ID>）へ行く。
 */

import { el, esc, toast, thumbHtml, openModal } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";

const NEW_CATEGORY = "__new";
const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/;
const DEFAULT_CATEGORY_COLOR = "#5b5bd6";

/**
 * 教材の詳細を開き、登録できたら自分の教材（store の形）を返す（やめたら null）。
 * @param {{id:string, title:string, isbn?:string, subjectName:string, color:string, imageUrl?:string|null}} material
 * @param {{id:number, name:string}} owner その教材を持っている人
 */
export async function openMaterialSheet(material, owner) {
  const mine = store.findMyMaterialByTitle(material.title);
  const categories = store.getCategories();
  const sameName = categories.find((c) => c.name.toLowerCase() === (material.subjectName || "").toLowerCase());
  const newName = (material.subjectName || "").slice(0, 20);
  const defaultCategory = sameName ? String(sameName.id) : (newName ? NEW_CATEGORY : String(categories[0]?.id ?? NEW_CATEGORY));

  const head = `
    <div class="mat-sheet">
      ${thumbHtml(material, "thumb--sm")}
      <div class="mat-sheet__info">
        <div class="mat-sheet__title">${esc(material.title)}</div>
        <span class="subject-chip" style="--chip-color:${esc(material.color)}">${esc(material.subjectName)}</span>
        ${material.isbn ? `<div class="u-small u-weak tabular">ISBN ${esc(material.isbn)}</div>` : ""}
        <div class="u-small u-muted">${esc(owner.name)}さんの教材</div>
      </div>
    </div>`;

  // もう登録してある
  if (mine) {
    const go = await openModal({
      title: "教材",
      okLabel: "この教材で記録する",
      cancelLabel: "閉じる",
      bodyHtml: `${head}
        <div class="notice" style="margin-top:12px">${icon("check", 16)} 同じ名前の教材が自分の教材に登録済みです。</div>`,
    });
    if (go) location.hash = `#/record/${mine.id}`;
    return null;
  }

  let choice = null;
  const ok = await openModal({
    title: "教材",
    okLabel: "自分の教材に登録",
    cancelLabel: "閉じる",
    bodyHtml: `${head}
      <div class="field" style="margin-top:14px">
        <label class="field__label" for="ms-category">登録するカテゴリ（教科）</label>
        <select class="select" id="ms-category">
          ${categories.map((c) => `
            <option value="${c.id}" ${String(c.id) === defaultCategory ? "selected" : ""}>${esc(c.name)}</option>`).join("")}
          <option value="${NEW_CATEGORY}" ${defaultCategory === NEW_CATEGORY ? "selected" : ""}>＋ 新しいカテゴリを作る</option>
        </select>
      </div>
      <div class="field" data-ms-new>
        <label class="field__label" for="ms-category-name">新しいカテゴリの名前</label>
        <input class="input" id="ms-category-name" maxlength="20" value="${esc(newName)}" placeholder="例）数学" />
      </div>
      <p class="u-small u-weak">教材名${material.isbn ? "・ISBN" : ""}${material.imageUrl ? "・画像" : ""}が自分の教材に登録されます。</p>`,
    onMount(root) {
      const select = root.querySelector("#ms-category");
      const newBox = root.querySelector("[data-ms-new]");
      const sync = () => newBox.classList.toggle("u-hidden", select.value !== NEW_CATEGORY);
      select.addEventListener("change", sync);
      sync();
      root.querySelector('[data-act="ok"]').addEventListener("click", () => {
        choice = { category: select.value, name: root.querySelector("#ms-category-name").value.trim() };
      });
    },
  });
  if (!ok || !choice) return null;

  try {
    let categoryId = Number(choice.category);
    if (choice.category === NEW_CATEGORY) {
      if (!choice.name) {
        toast("新しいカテゴリの名前を入力してください");
        return null;
      }
      const existing = store.getCategories().find((c) => c.name.toLowerCase() === choice.name.toLowerCase());
      const color = HEX_COLOR_RE.test(material.color) ? material.color : DEFAULT_CATEGORY_COLOR;
      categoryId = existing ? existing.id : (await store.addCategory(choice.name, color)).id;
    }
    const added = await store.copyMaterial(material.id, categoryId);
    toast(`「${added.title}」を自分の教材に登録しました`);
    return added;
  } catch (err) {
    toast(`登録できませんでした：${err.message}`);
    return null;
  }
}

/**
 * 教材のカード一覧（カテゴリごと）。
 * @param {Array} materials store.getUserMaterials() か store.getMaterials() の形
 * @param {{isMe?: boolean}} [opts] isMe なら自分の教材（押すと LoC計測へ。登録ボタンは出さない）
 */
export function materialGridHtml(materials, { isMe = false } = {}) {
  const groups = new Map();
  materials.forEach((m) => {
    const key = `${m.subjectName}\u0000${m.color}`;
    if (!groups.has(key)) groups.set(key, { name: m.subjectName, color: m.color, items: [] });
    groups.get(key).items.push(m);
  });
  return [...groups.values()].map((g) => `
    <section class="section">
      <div class="section__head">
        <span class="subject-chip" style="--chip-color:${esc(g.color)}">${esc(g.name)}</span>
        <span class="u-small u-weak">${g.items.length}冊</span>
      </div>
      <div class="grid-auto">
        ${g.items.map((m) => isMe ? `
          <a class="material-card" href="#/record/${esc(m.id)}">
            ${thumbHtml(m, "thumb--sm")}
            <div class="material-card__text">
              <div class="material-card__title">${esc(m.title)}</div>
              <div class="material-card__meta">${esc(m.subjectName)}</div>
            </div>
          </a>` : `
          <button class="material-card" type="button" data-material="${esc(m.id)}">
            ${thumbHtml(m, "thumb--sm")}
            <div class="material-card__text">
              <div class="material-card__title">${esc(m.title)}</div>
              <div class="material-card__meta">${esc(m.subjectName)}</div>
              <span class="mat-badge ${m.mine ? "mat-badge--done" : ""}" data-material-badge>
                ${m.mine ? `${icon("check", 13)}登録済み` : `${icon("plus", 13)}登録する`}
              </span>
            </div>
          </button>`).join("")}
      </div>
    </section>`).join("");
}

/** materialGridHtml のカードを押せるようにする（ほかの人の教材） */
export function bindMaterialGrid(root, materials, owner) {
  root.addEventListener("click", async (e) => {
    const card = e.target.closest("[data-material]");
    if (!card) return;
    const material = materials.find((m) => m.id === card.dataset.material);
    if (!material) return;
    const added = await openMaterialSheet(material, owner);
    if (added) {
      material.mine = added;
      const badge = card.querySelector("[data-material-badge]");
      badge.classList.add("mat-badge--done");
      badge.innerHTML = `${icon("check", 13)}登録済み`;
    }
  });
}

/** 教材がないときの表示 */
export function emptyMaterialsNode(isMe) {
  return el(`<div class="empty">${isMe ? "まだ教材がありません" : "まだ教材が登録されていません"}</div>`);
}
