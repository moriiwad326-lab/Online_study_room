/**
 * pages/notifications.js : 通知（#/notifications）
 *
 *  新しい順に最大50件。開いたら全部既読にする（未読だったものは、この画面を開いている間は色付きのまま）。
 *  通知の種類：
 *    follow           … フォローされた
 *    invite_request   … 招待申請が届いた（管理者だけ）
 *    invite_approved  … 紹介した友だちの招待申請が承認された
 *    invite_rejected  … 紹介した友だちの招待申請が見送られた
 */

import { el, esc, formatRelative } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";
import { avatarHtml } from "./people.js";

export const meta = { title: "通知" };

export async function render() {
  let notifications;
  try {
    notifications = await store.getNotifications();
  } catch (err) {
    return { node: el(`<div class="notice notice--error">${esc(err.message)}</div>`) };
  }

  const node = el(`<section class="section"></section>`);
  if (notifications.length === 0) {
    node.appendChild(el(`<div class="empty">通知はまだありません</div>`));
    return { node };
  }

  node.appendChild(el(`<div class="card">${notifications.map(itemHtml).join("")}</div>`));
  if (notifications.some((n) => !n.isRead)) {
    store.markNotificationsRead().catch((err) => console.warn("[notifications] 既読にできませんでした", err));
  }
  return { node };
}

/** 通知1件。押すと関係する画面へ */
function itemHtml(n) {
  const { href, iconHtml, text } = describe(n);
  return `
    <a class="notif ${n.isRead ? "" : "is-unread"}" href="${href}">
      ${iconHtml}
      <div class="notif__text">
        <div>${text}</div>
        <div class="u-small u-weak">${formatRelative(n.createdAt)}</div>
      </div>
    </a>`;
}

function describe(n) {
  const circle = (name) => `<div class="notif__icon">${icon(name, 18)}</div>`;
  switch (n.type) {
    case "follow":
      return {
        href: n.actor ? `#/users/${n.actor.id}` : "#/notifications",
        iconHtml: n.actor ? avatarHtml(n.actor) : circle("user"),
        text: `<b>${esc(n.actor?.name ?? "退会したユーザー")}</b>さんにフォローされました`,
      };
    case "invite_request":
      return {
        href: "#/admin",
        iconHtml: circle("user"),
        text: n.actor
          ? `<b>${esc(n.actor.name)}</b>さんから友だちの招待申請が届きました`
          : "参加したい人から招待申請が届きました",
      };
    case "invite_approved":
      return {
        href: "#/profile",
        iconHtml: circle("spark"),
        text: `<b>${esc(n.data.email)}</b> の招待申請が承認されました。その Google アカウントでログインできることを伝えてください`,
      };
    case "invite_rejected":
      return {
        href: "#/profile",
        iconHtml: circle("bell"),
        text: `<b>${esc(n.data.email)}</b> の招待申請は見送られました`,
      };
    default:
      return { href: "#/notifications", iconHtml: circle("bell"), text: "お知らせがあります" };
  }
}
