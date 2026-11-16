/**
 * pages/post.js : 投稿の詳細と返信欄（#/posts/<記録ID>?reply=<返信ID>）
 *
 *  投稿カード（timeline.js と同じもの）の下に返信を木の形で並べる。
 *  返信には返信できる（スレッド）。見た目の字下げは MAX_INDENT 段までで、それより深いものは同じ段に並べる。
 *  返信は書いた本人と投稿者が消せる。下に返信が残っているものは「削除された返信」として残る。
 *  ?reply=<返信ID> が付いていたら（通知から来たとき）、その返信までスクロールして目立たせる。
 */

import { el, esc, formatRelative, toast, openModal } from "../utils.js";
import { icon } from "../icons.js";
import * as store from "../store.js";
import { renderPostCard, drawSparklines } from "./timeline.js";
import { avatarHtml } from "./people.js";

export const meta = { title: "投稿" };

/** 返信の字下げの段数の上限（スマホで幅が細くなりすぎないように） */
const MAX_INDENT = 3;
/** 返信の最大文字数（サーバーと同じ） */
const MAX_REPLY_LEN = 500;

export async function render({ param, query, navigate }) {
  const recordId = Number(param);
  const back = `<button class="link-btn u-small" type="button" data-act="back">&larr; もどる</button>`;
  if (!Number.isInteger(recordId) || recordId <= 0) {
    navigate("#/timeline");
    return { node: el(`<div></div>`) };
  }

  let post;
  try {
    post = await store.getPost(recordId);
  } catch (err) {
    const node = el(`<div>${back}
      <div class="empty">${esc(err.status === 404 ? "この投稿は削除されたか、見つかりません" : err.message)}</div></div>`);
    bindBack(node, navigate);
    return { node };
  }

  const node = el(`
    <div>
      ${back}
      <div class="post-detail" data-post></div>
      <section class="card replies">
        <h2 class="card__title">返信 <span class="u-weak u-small" data-reply-count></span></h2>
        <form class="reply-form" data-reply-form>
          ${avatarHtml(store.getMe(), "avatar--sm")}
          <textarea class="textarea" data-reply-input maxlength="${MAX_REPLY_LEN}" rows="2"
                    placeholder="${post.isMine ? "返信を書く" : `${esc(post.userName)}さんに返信する`}" aria-label="返信"></textarea>
          <button class="btn btn--sm" type="submit">返信</button>
        </form>
        <div class="replies__list" data-replies></div>
      </section>
    </div>
  `);
  bindBack(node, navigate);

  const card = renderPostCard(post, { detail: true, onDeleted: () => navigate("#/timeline") });
  node.querySelector("[data-post]").appendChild(card);
  drawSparklines(node);

  const listBox = node.querySelector("[data-replies]");
  const countEl = node.querySelector("[data-reply-count]");
  let highlightId = Number(query.get("reply")) || null;

  /** 返信を取り直して描き直す */
  async function reload() {
    let replies;
    try {
      replies = await store.getReplies(recordId);
    } catch (err) {
      listBox.innerHTML = `<div class="notice notice--error">${esc(err.message)}</div>`;
      return;
    }
    const alive = replies.filter((r) => !r.isDeleted).length;
    countEl.textContent = alive ? `${alive}件` : "";
    const cardCount = card.querySelector("[data-comment-count]");
    if (cardCount) cardCount.textContent = alive;

    listBox.innerHTML = replies.length
      ? treeHtml(replies)
      : `<div class="empty replies__empty">まだ返信はありません。最初の返信をしてみましょう</div>`;

    if (highlightId) {
      const target = listBox.querySelector(`[data-reply="${highlightId}"]`);
      highlightId = null;
      if (target) {
        target.classList.add("is-highlight");
        // ルーターがページの先頭へスクロールし終えてから動かす
        setTimeout(() => target.scrollIntoView({ block: "center", behavior: "smooth" }), 50);
      }
    }
  }

  /* ---- 投稿への返信 ---- */
  const form = node.querySelector("[data-reply-form]");
  bindReplyForm(form, null);

  /** フォームを送ると返信する（parentId が null なら投稿への返信） */
  function bindReplyForm(formEl, parentId) {
    const input = formEl.querySelector("textarea");
    const button = formEl.querySelector('button[type="submit"]');
    // Ctrl/⌘ + Enter でも送れる
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) formEl.requestSubmit();
    });
    formEl.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = input.value.trim();
      if (!body || button.disabled) return;
      button.disabled = true;
      try {
        const reply = await store.addReply(recordId, body, parentId);
        input.value = "";
        highlightId = reply.id;
        await reload();
      } catch (err) {
        toast(err.message);
      } finally {
        button.disabled = false;
      }
    });
  }

  /* ---- 返信ごとの「返信」「削除」 ---- */
  listBox.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const item = btn.closest("[data-reply]");
    const replyId = Number(item.dataset.reply);

    if (btn.dataset.act === "reply-to") {
      // その返信のすぐ下に入力欄を開く（開いていたら閉じる）
      // 最初に見つかる .reply__body-wrap がその返信自身のもの（子の返信はその後ろに並ぶ）
      const wrap = item.querySelector(".reply__body-wrap");
      const open = wrap.querySelector(":scope > [data-inline-form]");
      listBox.querySelectorAll("[data-inline-form]").forEach((f) => f.remove());
      if (open) return;
      const name = item.querySelector(".reply__name")?.textContent.trim() || "";
      const inline = el(`
        <form class="reply-form reply-form--inline" data-inline-form>
          <textarea class="textarea" maxlength="${MAX_REPLY_LEN}" rows="2"
                    placeholder="${esc(name)}さんに返信する" aria-label="返信"></textarea>
          <div class="u-row" style="gap:6px">
            <button class="btn btn--ghost btn--sm" type="button" data-act="cancel-inline">やめる</button>
            <button class="btn btn--sm" type="submit">返信</button>
          </div>
        </form>`);
      wrap.appendChild(inline);
      bindReplyForm(inline, replyId);
      inline.querySelector("textarea").focus();
      return;
    }

    if (btn.dataset.act === "cancel-inline") {
      btn.closest("[data-inline-form]").remove();
      return;
    }

    if (btn.dataset.act === "delete-reply") {
      const ok = await openModal({
        title: "この返信を削除しますか？",
        okLabel: "削除する",
        bodyHtml: `<p class="u-muted">元に戻せません。この返信への返信は残ります。</p>`,
      });
      if (!ok) return;
      try {
        await store.deleteReply(replyId);
        toast("返信を削除しました");
        await reload();
      } catch (err) {
        toast(err.message);
      }
    }
  });

  await reload();
  return { node };
}

function bindBack(node, navigate) {
  node.querySelector('[data-act="back"]').addEventListener("click", () => {
    // アプリの中から来たなら戻る。直接開いたならタイムラインへ
    if (history.length > 1) history.back();
    else navigate("#/timeline");
  });
}

/** 返信の一覧を木にして HTML にする */
function treeHtml(replies) {
  const children = new Map();
  replies.forEach((r) => {
    const key = r.parentId ?? 0;
    if (!children.has(key)) children.set(key, []);
    children.get(key).push(r);
  });
  // 親が一覧に無い（消えた）返信は、いちばん上の段に出す
  const ids = new Set(replies.map((r) => r.id));
  replies.forEach((r) => {
    if (r.parentId != null && !ids.has(r.parentId)) children.get(0).push(r);
  });

  const branch = (parentKey, depth) => (children.get(parentKey) || [])
    .map((r) => replyHtml(r, depth, branch(r.id, depth + 1)))
    .join("");
  return branch(0, 0);
}

function replyHtml(reply, depth, childrenHtml) {
  // 子の返信は親の中に入れ子にして1段ずつ字下げする。MAX_INDENT より深いものは字下げしない
  const flat = depth > MAX_INDENT ? "reply--flat" : "";
  if (reply.isDeleted) {
    return `
      <div class="reply reply--deleted ${flat}" data-reply="${reply.id}">
        <div class="reply__body-wrap"><div class="reply__body u-weak">この返信は削除されました</div></div>
        ${childrenHtml}
      </div>`;
  }
  const user = reply.user;
  return `
    <div class="reply ${flat}" data-reply="${reply.id}">
      <div class="reply__row">
        <a href="#/users/${user.id}">${avatarHtml(user, "avatar--sm")}</a>
        <div class="reply__body-wrap">
          <div class="reply__meta">
            <a class="reply__name" href="#/users/${user.id}">${esc(user.name)}</a>
            ${reply.isMine ? '<span class="u-small u-weak">（自分）</span>' : ""}
            <span class="u-small u-weak">${formatRelative(reply.createdAt)}</span>
          </div>
          <div class="reply__body">${esc(reply.body)}</div>
          <div class="reply__actions">
            <button class="link-btn u-small" type="button" data-act="reply-to">${icon("reply", 14)}返信</button>
            ${reply.canDelete ? `
              <button class="link-btn link-btn--danger u-small" type="button" data-act="delete-reply">${icon("trash", 14)}削除</button>` : ""}
          </div>
        </div>
      </div>
      ${childrenHtml}
    </div>`;
}
