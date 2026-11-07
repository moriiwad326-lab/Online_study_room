/**
 * pages/messages.js : メッセージ（#/messages）、相手とのやりとり（#/messages/<ユーザーID>）
 *
 *  - 一覧：やりとりのある相手ごとに最新のメッセージと未読数。その下に、まだやりとりのない相互フォローの相手
 *  - やりとり：吹き出しで並べ、POLL_MS ごとに新しいメッセージを取りに行く（開いている間は既読になる）
 *  - メッセージは相互フォローの相手とだけ送れる（サーバーでも確かめる）
 */

import { el, esc, formatRelative, toast } from "../utils.js";
import * as store from "../store.js";
import { avatarHtml } from "./people.js";

export const meta = { title: "メッセージ" };

/** やりとりの画面で新しいメッセージを取りに行く間隔（ms） */
const POLL_MS = 5000;
const MAX_LEN = 1000;

export async function render({ param }) {
  return param ? renderThread(Number(param)) : renderList();
}

/* ============ 一覧 ============ */

async function renderList() {
  const node = el(`<div></div>`);
  const me = store.getMe();
  let conversations;
  let following;
  try {
    [conversations, following] = await Promise.all([store.getConversations(), store.getFollowing(me.id)]);
  } catch (err) {
    node.appendChild(el(`<div class="notice notice--error">${esc(err.message)}</div>`));
    return { node };
  }

  const list = el(`<section class="section"><div class="card" data-list></div></section>`);
  const box = list.querySelector("[data-list]");
  if (conversations.length === 0) {
    box.innerHTML = `<div class="empty">まだメッセージはありません</div>`;
  } else {
    box.innerHTML = conversations.map((c) => `
      <a class="person-row person-row__main conv-row" href="#/messages/${c.partner.id}">
        ${avatarHtml(c.partner)}
        <div class="person-row__text">
          <div class="u-spread">
            <span class="person-row__name">${esc(c.partner.name)}</span>
            <span class="u-small u-weak u-nowrap">${formatRelative(c.lastMessage.createdAt)}</span>
          </div>
          <div class="conv-row__last ${c.unread ? "u-bold" : "u-muted"}">
            ${c.lastMessage.senderId === me.id ? "あなた：" : ""}${esc(c.lastMessage.body)}
          </div>
        </div>
        ${c.unread ? `<span class="count-badge">${c.unread}</span>` : ""}
      </a>`).join("");
  }
  node.appendChild(list);

  // まだやりとりのない相互フォローの相手
  const talked = new Set(conversations.map((c) => c.partner.id));
  const mutual = following.filter((p) => p.canMessage && !talked.has(p.id));
  node.appendChild(el(`
    <section class="section">
      <div class="section__head"><h2 class="section__title">メッセージを送れる相手</h2></div>
      ${mutual.length ? `
        <div class="card">
          ${mutual.map((p) => `
            <a class="person-row person-row__main" href="#/messages/${p.id}">
              ${avatarHtml(p)}
              <div class="person-row__text"><div class="person-row__name">${esc(p.name)}</div></div>
            </a>`).join("")}
        </div>` : `
        <div class="empty">
          メッセージは相互フォローの相手とだけやりとりできます。<br>
          <a class="link-btn" href="#/search">ユーザーをさがす</a>
        </div>`}
    </section>`));

  return { node };
}

/* ============ やりとり ============ */

async function renderThread(partnerId) {
  let thread;
  try {
    thread = await store.getThread(partnerId);
  } catch (err) {
    const message = err.status === 404 ? "ユーザーが見つかりません" : err.message;
    return { node: el(`<div class="notice notice--error">${esc(message)}</div>`) };
  }
  store.refreshBadges();  // 開いたので相手からの分は既読になった

  const me = store.getMe();
  let partner = thread.partner;
  const seen = new Set();
  let lastId = 0;

  const node = el(`
    <div class="thread">
      <a class="card person-row person-row__main" href="#/users/${partner.id}">
        ${avatarHtml(partner)}
        <div class="person-row__text"><div class="person-row__name">${esc(partner.name)}</div></div>
      </a>
      <div class="thread__list" data-list></div>
      <div data-notice></div>
      <form class="thread__form" data-form>
        <textarea class="textarea" rows="2" maxlength="${MAX_LEN}" placeholder="メッセージを入力（Ctrl+Enter で送信）"
                  aria-label="メッセージ" data-input></textarea>
        <button class="btn" type="submit" data-send>送信</button>
      </form>
    </div>
  `);
  const list = node.querySelector("[data-list]");
  const form = node.querySelector("[data-form]");
  const input = node.querySelector("[data-input]");
  const sendBtn = node.querySelector("[data-send]");

  const append = (messages) => {
    const fresh = messages.filter((m) => !seen.has(m.id));
    if (!fresh.length) return;
    const nearBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
    fresh.forEach((m) => {
      seen.add(m.id);
      lastId = Math.max(lastId, m.id);
      const mine = m.senderId === me.id;
      list.appendChild(el(`
        <div class="bubble-row ${mine ? "bubble-row--me" : ""}">
          <div class="bubble ${mine ? "bubble--me" : ""}">${esc(m.body)}</div>
          <div class="bubble__time">${formatRelative(m.createdAt)}</div>
        </div>`));
    });
    if (nearBottom || fresh.some((m) => m.senderId === me.id)) {
      requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight }));
    }
  };

  // 相互フォローでなくなったら、読めるが送れない
  const updateCanSend = () => {
    const notice = node.querySelector("[data-notice]");
    notice.innerHTML = partner.canMessage ? "" : `
      <div class="notice notice--warn">
        メッセージは相互フォローの相手にだけ送れます。<a class="link-btn" href="#/users/${partner.id}">プロフィール</a>からフォローしてください。
      </div>`;
    input.disabled = sendBtn.disabled = !partner.canMessage;
  };

  if (!thread.messages.length) {
    list.appendChild(el(`<div class="empty" data-empty>まだメッセージはありません。最初のひとことを送ってみましょう</div>`));
  }
  append(thread.messages);
  updateCanSend();

  const send = async () => {
    const body = input.value.trim();
    if (!body || sendBtn.disabled) return;
    sendBtn.disabled = true;
    try {
      const message = await store.sendMessage(partner.id, body);
      list.querySelector("[data-empty]")?.remove();
      append([message]);
      input.value = "";
    } catch (err) {
      toast(err.message);
    } finally {
      sendBtn.disabled = !partner.canMessage;
      input.focus();
    }
  };
  form.addEventListener("submit", (e) => { e.preventDefault(); send(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
  });

  const timer = setInterval(async () => {
    if (document.visibilityState !== "visible") return;
    try {
      const next = await store.getThread(partner.id, lastId);
      if (next.messages.length) {
        list.querySelector("[data-empty]")?.remove();
        append(next.messages);
        store.refreshBadges();
      }
      if (next.partner.canMessage !== partner.canMessage) {
        partner = next.partner;
        updateCanSend();
      }
    } catch (err) {
      console.warn("[messages] 新しいメッセージを取れませんでした", err);
    }
  }, POLL_MS);

  requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight }));
  return { node, destroy() { clearInterval(timer); } };
}
