/**
 * pages/user.js : ユーザーページ（#/users/<ID>?tab=records|followers|following）
 *
 *  公開プロフィール（ニックネーム・自己紹介・タグ・学年・志望・目標）、フォロー数、
 *  フォローボタン、相互フォローならメッセージへの入口を出す。
 *  タブで、その人の学習記録・フォロワー・フォロー中を切り替える。
 */

import { el, esc } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";
import { renderPostCard, drawSparklines } from "./timeline.js";
import { avatarHtml, personSubline, followButtonHtml, personRowHtml, bindFollowButtons } from "./people.js";

export const meta = {
  title: "ユーザー",
  tabs: [
    { key: "records", label: "記録" },
    { key: "followers", label: "フォロワー" },
    { key: "following", label: "フォロー中" },
  ],
};

export async function render({ param, tab, navigate }) {
  const userId = Number(param);
  if (!Number.isInteger(userId) || userId <= 0) {
    navigate("#/search");
    return { node: el(`<div></div>`) };
  }

  let person;
  try {
    person = await store.getPerson(userId);
  } catch (err) {
    return { node: el(`<div class="notice notice--error">${esc(err.status === 404 ? "ユーザーが見つかりません" : err.message)}</div>`) };
  }

  const node = el(`<div></div>`);
  const head = el(`<section class="card"></section>`);
  node.appendChild(head);

  const drawHead = () => {
    const sub = personSubline(person);
    head.innerHTML = `
      <div class="profile-head">
        ${avatarHtml(person, "avatar--lg")}
        <div>
          <div class="profile-head__name">${esc(person.name)}</div>
          ${sub ? `<div class="u-small u-muted">${esc(sub)}</div>` : ""}
          ${person.examName ? `<div class="u-small u-muted">目標：${esc(person.examName)}</div>` : ""}
          ${person.followsMe && !person.isMe ? '<span class="chip">フォローされています</span>' : ""}
        </div>
        <div class="profile-head__edit u-row">
          ${person.isMe
            ? `<a class="btn btn--ghost btn--sm" href="#/profile/edit">${icon("edit", 16)}編集</a>`
            : `${person.canMessage ? `<a class="btn btn--ghost btn--sm" href="#/messages/${person.id}">${icon("message", 16)}メッセージ</a>` : ""}
               ${followButtonHtml(person)}`}
        </div>
      </div>
      <div class="u-row follow-counts">
        <a href="#/users/${person.id}?tab=following"><b class="tabular">${person.followingCount}</b> フォロー中</a>
        <a href="#/users/${person.id}?tab=followers"><b class="tabular">${person.followerCount}</b> フォロワー</a>
      </div>
      ${person.bio ? `<p class="u-muted" style="white-space:pre-wrap;margin-top:12px">${esc(person.bio)}</p>` : ""}
      ${person.tags.length ? `
        <div style="margin-top:10px">${person.tags.map((t) => `<span class="tag">#${esc(t)}</span>`).join("")}</div>` : ""}
      ${!person.isMe && !person.canMessage ? `
        <p class="u-small u-weak" style="margin-top:10px">メッセージは相互フォローになると送れます。</p>` : ""}
    `;
  };
  drawHead();

  // 自分のフォローを切り替えたら、フォロワー数とメッセージの入口を取り直す
  bindFollowButtons(head, async () => {
    person = await store.getPerson(userId);
    drawHead();
  });

  const body = el(`<section class="section"></section>`);
  node.appendChild(body);

  try {
    if (tab === "followers" || tab === "following") {
      const people = tab === "followers" ? await store.getFollowers(userId) : await store.getFollowing(userId);
      body.appendChild(people.length
        ? el(`<div class="card">${people.map(personRowHtml).join("")}</div>`)
        : el(`<div class="empty">${tab === "followers" ? "まだフォロワーがいません" : "まだ誰もフォローしていません"}</div>`));
      bindFollowButtons(body);
    } else {
      const posts = await store.getUserPosts(userId);
      if (posts.length === 0) {
        body.appendChild(el(`<div class="empty">まだ学習記録がありません</div>`));
      } else {
        posts.forEach((post) => body.appendChild(renderPostCard(post)));
        drawSparklines(body);
      }
    }
  } catch (err) {
    body.appendChild(el(`<div class="notice notice--error">${esc(err.message)}</div>`));
  }

  return { node };
}
