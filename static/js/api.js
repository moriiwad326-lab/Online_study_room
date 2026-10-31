/**
 * api.js : アプリサーバー（../main.py）の API を呼ぶ小さなクライアント
 *
 *  - リクエスト・レスポンスとも JSON
 *  - ログイン状態は HttpOnly の Cookie で持つので、JS からトークンは触らない
 *  - 失敗したら ApiError を投げる（message は画面にそのまま出せる日本語）
 */

export class ApiError extends Error {
  /**
   * @param {number} status  HTTP ステータス（サーバーに届かなかったときは 0）
   * @param {string} message 画面に出すメッセージ
   * @param {string|null} field 入力エラーのときの項目名（loginId など）
   */
  constructor(status, message, field = null) {
    super(message);
    this.status = status;
    this.field = field;
  }
}

export async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "サーバーに接続できませんでした");
  }

  let data = null;
  try {
    data = await res.json();
  } catch {
    // python -m http.server などで配信していて API が無いと、HTML の 404 が返ってくる
  }
  if (!res.ok || data === null) {
    throw new ApiError(res.status, data?.error || `サーバーエラー (${res.status})`, data?.field ?? null);
  }
  return data;
}
