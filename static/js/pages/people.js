/**
 * pages/people.js : ユーザーの一覧の1行とフォローボタン（さがす・ユーザーページ・メッセージで共用）
 *
 *  personRowHtml(person) で1行を作り、bindFollowButtons(root) でその中のフォローボタンを動かす。
 *  person は store.toPerson() の形（isMe / isFollowing / followsMe が付いているもの）。
 */

import { esc, toast } from "../utils.js";
import * as store from "../store.js";

/** アイコン（ニックネームの1文字目を色の丸に載せる） */
export function avatarHtml(person, extraClass = "") {
  return `<div class="avatar ${extraClass}" style="background:${esc(person.avatarColor)}">${esc(person.initial)}</div>`;
}

/** 学年・志望など、名前の下に出す1行 */
export function personSubline(person) {
  return [person.grade, person.targetSchool && `志望：${person.targetSchool}`].filter(Boolean).join("／");
}

/** フォローボタン（自分には出さない） */
export function followButtonHtml(person) {
  if (person.isMe) return "";
  const label = person.isFollowing ? "フォロー中" : (person.followsMe ? "フォローバック" : "フォローする");
  return `
    <button class="btn btn--sm ${person.isFollowing ? "btn--ghost" : ""}" type="button"
            data-follow="${person.id}" data-following="${person.isFollowing ? "1" : "0"}">${label}</button>`;
}

/** 一覧の1行（名前を押すとユーザーページへ） */
export function personRowHtml(person) {
  const sub = personSubline(person);
  return `
    <div class="person-row" data-person="${person.id}">
      <a class="person-row__main" href="#/users/${person.id}">
        ${avatarHtml(person)}
        <div class="person-row__text">
          <div class="person-row__name">
            ${esc(person.name)}${person.isMe ? '<span class="u-small u-weak">（自分）</span>' : ""}
            ${person.followsMe && !person.isMe ? '<span class="chip">フォローされています</span>' : ""}
          </div>
          ${sub ? `<div class="u-small u-weak">${esc(sub)}</div>` : ""}
        </div>
      </a>
      ${followButtonHtml(person)}
    </div>`;
}

/**
 * root の中のフォローボタンを押せるようにする。
 * onChange(person) には、フォロー／解除したあとの相手（関係つき）が渡る。
 */
export function bindFollowButtons(root, onChange) {
  root.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-follow]");
    if (!btn || btn.disabled) return;
    const follow = btn.dataset.following !== "1";
    btn.disabled = true;
    try {
      const person = await store.setFollowing(Number(btn.dataset.follow), follow);
      btn.outerHTML = followButtonHtml(person);
      toast(follow ? `${person.name}さんをフォローしました` : `${person.name}さんのフォローをやめました`);
      onChange?.(person);
    } catch (err) {
      btn.disabled = false;
      toast(err.message);
    }
  });
}
