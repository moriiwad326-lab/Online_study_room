/**
 * pages/admin.js : 管理（#/admin?tab=pending|all）… 管理者だけ
 *
 *  招待申請を承認・却下する。承認するとそのメールアドレスの招待が作られ、
 *  その Google アカウントで初めてログインしたときに登録される。
 *  管理者は `python -m System.admin grant-admin <ユーザーID>` で決める（Web からは変えられない）。
 */

import { el, esc, formatRelative, toast, openModal } from "../utils.js";
import * as store from "../store.js";

export const meta = {
  title: "管理",
  tabs: [
    { key: "pending", label: "承認待ち" },
    { key: "all", label: "すべての申請" },
  ],
};

const STATUS_LABELS = { pending: "承認待ち", approved: "承認済み", rejected: "却下" };

export async function render({ tab }) {
  if (!store.isAdmin()) {
    return { node: el(`<div class="notice notice--error">この画面は管理者だけが使えます</div>`) };
  }

  let requests;
  try {
    requests = await store.getInviteRequests(tab === "all" ? null : "pending");
  } catch (err) {
    return { node: el(`<div class="notice notice--error">${esc(err.message)}</div>`) };
  }

  const node = el(`
    <section class="section">
      <p class="u-small u-muted" style="margin-bottom:10px">
        承認すると、そのメールアドレスの Google アカウントで登録できるようになります（メールは送られません。申請した人に伝えてください）。
      </p>
      <div data-list></div>
    </section>`);
  const list = node.querySelector("[data-list]");

  if (requests.length === 0) {
    list.appendChild(el(`<div class="empty">${tab === "all" ? "申請はまだありません" : "承認待ちの申請はありません"}</div>`));
    return { node };
  }

  requests.forEach((r) => list.appendChild(renderRequest(r)));
  return { node };
}

function renderRequest(r) {
  const card = el(`<article class="card request"></article>`);

  const draw = () => {
    const who = r.kind === "self"
      ? "本人からの申請（Google で確認済みのメールアドレス）"
      : r.requester
        ? `<a class="link-btn" href="#/users/${r.requester.id}">${esc(r.requester.nickname)}</a>さんの紹介`
        : "紹介（紹介した人は退会済み）";
    card.innerHTML = `
      <div class="u-spread">
        <div class="u-bold request__email">${esc(r.email)}</div>
        <span class="status-chip status-chip--${r.status}">${STATUS_LABELS[r.status]}</span>
      </div>
      <div class="u-small u-muted">${who}・${formatRelative(r.createdAt)}</div>
      ${r.message ? `<p class="request__message">${esc(r.message)}</p>` : ""}
      ${r.status === "pending" ? `
        <div class="u-row request__actions">
          <button class="btn btn--sm" type="button" data-act="approve">承認する</button>
          <button class="btn btn--ghost btn--sm" type="button" data-act="reject">却下する</button>
        </div>` : ""}
    `;
  };
  draw();

  card.addEventListener("click", async (e) => {
    const action = e.target.closest("[data-act]")?.dataset.act;
    if (!action) return;
    const approve = action === "approve";
    const ok = await openModal({
      title: approve ? "この申請を承認しますか？" : "この申請を却下しますか？",
      okLabel: approve ? "承認する" : "却下する",
      bodyHtml: `<p class="u-muted">${esc(r.email)}${approve
        ? " の Google アカウントで登録できるようになります。"
        : " は招待されません（あとで申請し直すことはできます）。"}</p>`,
    });
    if (!ok) return;
    card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      r = await store.decideInviteRequest(r.id, action);
      toast(approve ? "承認しました" : "却下しました");
      draw();
    } catch (err) {
      toast(err.message);
      draw();
    }
  });

  return card;
}
