/**
 * store.js : アプリのデータを一手に引き受ける場所
 *
 *  - data/mock.json を読み込む（記事・目標など、まだサーバーに無いもの）
 *  - ログイン中のユーザー（プロフィール込み）と、そのユーザーの教材・カテゴリ・学習記録を API から受け取って持つ
 *  - フォロー・通知・メッセージ・招待申請は、その都度 API を呼ぶ（画面ごとに最新を取りに行く）
 *  - いいねだけは localStorage にユーザーごとに保存する
 *  - 画面側は store の関数だけを呼び、localStorage や API を直接触らない
 *
 * 学習記録は以前 localStorage に保存していた。ログイン時に残っていればサーバーへ一度だけ送り、
 * 送れたら localStorage からは消す（サーバーはブラウザ側の ID で重複を弾くので、送り直しても増えない）。
 */

import { toDateKey, average } from "./utils.js";
import { api } from "./api.js";

/** localStorage のキーの頭。実際のキーは "onsta.v1.u<ユーザーID>"。データ構造を変えたらバージョンを上げる。 */
const STORAGE_PREFIX = "onsta.v1";

/** プロフィールでアイコン色を選ばなかったときの色 */
export const DEFAULT_AVATAR_COLOR = "#5b5bd6";

/** 週の目標を設定していないときに使う値（分） */
const DEFAULT_WEEKLY_GOAL_MIN = 1800;

/** ヘッダーのバッジが変わったときに window に投げるイベント（app.js が描き直す） */
export const BADGES_EVENT = "onsta:badges";

/** localStorage に保存する側の初期値 */
const emptyLocal = {
  records: [],   // 以前の版で保存していた学習記録（サーバーへ移したら空になる）
  likes: {},     // { 投稿ID: true } いいね状態
  createdAt: null,
};

/** mock.json の中身（読み込み後にセットされる） */
let mock = null;
/** localStorage の中身 */
let local = { ...emptyLocal };

/** ログイン中のユーザー { id, email, profileCompleted, isAdmin, profile }。未ログインなら null */
let currentUser = null;
/** 今のユーザーの localStorage キー（未ログインなら null で、保存しない） */
let storageKey = null;
/** 認証 API に届いたか（python -m http.server だけで配信していると false） */
let authAvailable = true;

/** 自分のカテゴリ（教科）と教材。サーバーの値をそのまま持つ */
let myCategories = [];
let myMaterials = [];
/** 自分の学習記録（サーバーの値を画面で使う形にしたもの） */
let myRecords = [];
/** ヘッダーの未読数 */
let badges = { notifications: 0, messages: 0 };

/** 消した教材・見つからない教材を指す記録に使う表示 */
const UNKNOWN_COLOR = "#9aa0b5";

/* ============ localStorage の読み書き ============ */

function loadLocal() {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return { ...emptyLocal, createdAt: new Date().toISOString() };
    const parsed = JSON.parse(raw);
    // 以前は初回にモックのサンプル記録を入れていた。存在しない教材を指すので取り除く
    const records = (parsed.records || []).filter((r) => r.source !== "seed");
    return { ...emptyLocal, ...parsed, records };
  } catch (err) {
    // 壊れたデータが入っていても起動できるようにする
    console.warn("[store] localStorage の読み込みに失敗したので初期化します", err);
    return { ...emptyLocal, createdAt: new Date().toISOString() };
  }
}

function saveLocal() {
  if (!storageKey) return;
  try {
    localStorage.setItem(storageKey, JSON.stringify(local));
  } catch (err) {
    console.warn("[store] localStorage への保存に失敗しました", err);
  }
}

/* ============ 初期化 ============ */

/**
 * mock.json を読み込み、ログイン中ならそのユーザーのデータを開く。
 * file:// で開くと fetch が失敗するので、必ず http サーバー越しに開くこと。
 */
export async function init() {
  const res = await fetch("data/mock.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`mock.json の読み込みに失敗しました (${res.status})`);
  mock = await res.json();

  let user = null;
  try {
    ({ user } = await api("GET", "/api/auth/me"));
  } catch (err) {
    // 401 は単に未ログイン。それ以外はサーバーに API が無い（ログイン画面で案内する）
    authAvailable = err.status === 401;
  }
  if (user) await setUser(user);
}

/** ログインしたユーザーに切り替え、そのユーザーの保存データ・教材・学習記録を開く */
async function setUser(user) {
  currentUser = user;
  storageKey = `${STORAGE_PREFIX}.u${user.id}`;
  local = loadLocal();
  if (!user.profileCompleted) return;  // 初回設定の前は教材も記録も無い
  await reloadMaterials();
  await migrateLocalRecords();
  await reloadRecords();
  refreshBadges();
}

/** localStorage に残っている学習記録をサーバーへ移す。失敗したら残しておき、次の起動でまた試す */
async function migrateLocalRecords() {
  if (!local.records.length) return;
  try {
    const result = await api("POST", "/api/records/import", { records: local.records });
    if (result.skipped) console.warn(`[store] 読めなかった学習記録 ${result.skipped} 件は移さずに捨てました`);
    local.records = [];
    saveLocal();
  } catch (err) {
    console.warn("[store] 学習記録をサーバーへ移せませんでした。次の起動でもう一度試します", err);
  }
}

/** 「n日前」を ISO 文字列にする */
function daysAgoToIso(daysAgo) {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  d.setHours(20, 0, 0, 0);
  return d.toISOString();
}

/** 「n分前」を ISO 文字列にする */
function minutesAgoToIso(minutesAgo) {
  return new Date(Date.now() - minutesAgo * 60000).toISOString();
}

/* ============ ログイン ============ */

export const getUser = () => currentUser;
export const isAuthAvailable = () => authAvailable;
export const isAdmin = () => Boolean(currentUser?.isAdmin);

export async function logout() {
  try {
    await api("POST", "/api/auth/logout", {});
  } finally {
    currentUser = null;
    storageKey = null;
    local = { ...emptyLocal };
    myCategories = [];
    myMaterials = [];
    myRecords = [];
    badges = { notifications: 0, messages: 0 };
  }
}

/** プロフィールを保存する（初回設定と編集の両方）。保存後のユーザーを返す */
export async function saveProfile(profile) {
  const wasCompleted = currentUser?.profileCompleted;
  const { user } = await api("PUT", "/api/me/profile", { profile });
  // 初回設定が済んだら、教材と学習記録を読み込む
  if (!wasCompleted) await setUser(user);
  else currentUser = user;
  return user;
}

/* ============ 教材・カテゴリ ============ */

async function reloadMaterials() {
  const data = await api("GET", "/api/materials");
  myCategories = data.categories;
  myMaterials = data.materials;
}

/** カテゴリ（教科）を作る。返り値は { id, name, color } */
export async function addCategory(name, color) {
  const { category } = await api("POST", "/api/categories", { name, color });
  myCategories.push(category);
  return category;
}

/**
 * 教材を追加する。
 * @param {{title:string, categoryId:number, isbn?:string, image?:string, coverUrl?:string}} input
 *   image は data URL（独自教材でアップロードした画像）、coverUrl はバーコード検索で見つけた表紙
 */
export async function addMaterial(input) {
  const { material } = await api("POST", "/api/materials", input);
  myMaterials.push(material);
  return toMaterial(material);
}

export async function deleteMaterial(materialId) {
  await api("DELETE", `/api/materials/${encodeURIComponent(materialId)}`);
  myMaterials = myMaterials.filter((m) => String(m.id) !== String(materialId));
}

/** バーコードの番号から書名・表紙を調べる。見つからなければ 404 の ApiError */
export async function lookupBook(isbn) {
  const { book } = await api("GET", `/api/books/${encodeURIComponent(isbn)}`);
  return book;
}

/** サーバーの教材を画面で使う形（科目名・科目色・画像URLつき）にする */
function toMaterial(m) {
  const category = myCategories.find((c) => c.id === m.categoryId);
  return {
    id: String(m.id),
    title: m.title,
    isbn: m.isbn,
    subjectId: m.categoryId,
    subjectName: category?.name ?? "その他",
    color: category?.color ?? UNKNOWN_COLOR,
    imageUrl: m.hasImage ? `/api/materials/${m.id}/image?v=${m.imageVersion}` : null,
  };
}

/** 消した教材を指す記録に出す表示 */
function missingMaterial(materialId) {
  return { id: materialId == null ? "" : String(materialId), title: "削除した教材", subjectId: "",
           subjectName: "その他", color: UNKNOWN_COLOR, imageUrl: null, missing: true };
}

/** 記録に付いてくる教材（他の人の教材のこともある）を画面で使う形にする */
function recordMaterial(record) {
  const m = record.material;
  if (!m) return missingMaterial(record.materialId);
  return {
    id: String(m.id),
    title: m.title,
    subjectId: "",
    subjectName: m.categoryName || "その他",
    color: m.color || UNKNOWN_COLOR,
    imageUrl: m.hasImage ? `/api/materials/${m.id}/image?v=${m.imageVersion}` : null,
  };
}

/* ============ 学習記録 ============ */

/** サーバーの記録を、レポートの集計で使う形にする */
function toRecord(r) {
  return {
    id: r.id,
    materialId: r.materialId == null ? null : String(r.materialId),
    durationMin: r.durationMin,
    memo: r.memo,
    locSeries: r.locSeries,
    avgLoc: r.avgLoc,
    createdAt: r.createdAt,
    source: r.source,
  };
}

async function reloadRecords() {
  const { records } = await api("GET", "/api/records");
  myRecords = records.map(toRecord);
}

/**
 * 学習記録を1件追加する。計測終了時と手動記録の両方から呼ばれる。
 * @param {{materialId:string, durationMin:number, memo?:string, locSeries?:number[], source?:string}} input
 */
export async function addRecord(input) {
  const { record } = await api("POST", "/api/records", {
    materialId: Number(input.materialId),
    durationMin: Math.max(1, Math.round(input.durationMin)),
    memo: input.memo || "",
    locSeries: (input.locSeries || []).map((v) => Math.round(v)),
    source: input.source || "manual",
  });
  const saved = toRecord(record);
  myRecords.push(saved);
  return saved;
}

/** 自分の学習記録（新しい順） */
export function getRecords() {
  return [...myRecords]
    .map((r) => ({ ...r, material: getMaterial(r.materialId) }))
    .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
}

/** 自分の学習記録（サーバー）といいね（localStorage）を全部消す */
export async function resetData() {
  await api("DELETE", "/api/records");
  myRecords = [];
  if (storageKey) localStorage.removeItem(storageKey);
  local = { ...emptyLocal, createdAt: new Date().toISOString() };
}

/* ============ 参照系 ============ */

/**
 * 画面に出す「自分」。プロフィールの未設定項目は空にそろえて返す。
 * name はニックネーム、initial はその1文字目。
 * email はログインした Google アカウントで、本人にだけ見せる（name の代わりに使わない）。
 */
export function getMe() {
  const p = currentUser?.profile || {};
  const name = p.nickname || "ゲスト";
  return {
    id: currentUser ? currentUser.id : null,
    email: currentUser?.email || "",
    name,
    initial: Array.from(name)[0] || "?",
    avatarColor: p.avatarColor || DEFAULT_AVATAR_COLOR,
    bio: p.bio || "",
    tags: p.tags || [],
    gender: p.gender || "",
    birthYear: p.birthYear ?? null,
    prefecture: p.prefecture || "",
    grade: p.grade || "",
    targetSchool: p.targetSchool || "",
    examName: p.examName || "",
    examDate: p.examDate || "",
    weeklyGoalMinutes: p.weeklyGoalMinutes ?? null,
  };
}

/**
 * 他の人（API の公開プロフィール）を画面で使う形にする。
 * 関係（isFollowing など）やフォロー数が付いていればそのまま残す。
 */
export function toPerson(u) {
  const name = u.nickname || "（名前なし）";
  return {
    ...u,
    name,
    initial: Array.from(name)[0] || "?",
    avatarColor: u.avatarColor || DEFAULT_AVATAR_COLOR,
    tags: u.tags || [],
  };
}

/** 保存しているプロフィールそのもの（編集フォームの初期値用） */
export const getProfile = () => ({ ...(currentUser?.profile || {}) });
/** 自分のカテゴリ（作った順） */
export const getCategories = () => [...myCategories];
/** 自分の教材（追加した順） */
export const getMaterials = () => myMaterials.map(toMaterial);
export const getArticles = () =>
  mock.articles.map((a) => ({ ...a, createdAt: daysAgoToIso(a.daysAgo) }));
export const getFindTargets = () => mock.findTargets;

/** 教材IDから自分の教材（科目名・科目色つき）を引く。見つからなければ「削除した教材」 */
export function getMaterial(materialId) {
  const mine = myMaterials.find((x) => String(x.id) === String(materialId));
  return mine ? toMaterial(mine) : missingMaterial(materialId);
}

/** カテゴリごとに自分の教材をまとめた配列を返す（記録するページのグリッド用） */
export function getMaterialsBySubject() {
  const materials = getMaterials();
  return myCategories
    .map((category) => ({
      ...category,
      materials: materials.filter((m) => m.subjectId === category.id),
    }))
    .filter((group) => group.materials.length > 0);
}

/** 「いま勉強中」のユーザー一覧。elapsedSec の起点を実時刻に直して返す */
export function getLiveUsers() {
  return mock.liveUsers.map((u) => ({
    ...u,
    material: getMaterial(u.materialId),
    startedAt: Date.now() - u.elapsedSec * 1000,
  }));
}

/** 目標タブの投稿 */
export function getGoalPosts() {
  return mock.goalPosts.map((g) => ({ ...g, createdAt: minutesAgoToIso(g.minutesAgo) }));
}

/** サーバーの記録を、タイムラインの投稿カードの形にする */
function toPost(r) {
  const person = toPerson(r.user);
  const isMine = r.user.id === currentUser?.id;
  return {
    id: r.id,
    userId: person.id,
    userName: person.name,
    initial: person.initial,
    avatarColor: person.avatarColor,
    createdAt: r.createdAt,
    // 自分の教材は手元の一覧から引く（カテゴリ名を変えたときなども最新になる）
    material: isMine && r.materialId != null ? getMaterial(r.materialId) : recordMaterial(r),
    durationMin: r.durationMin,
    memo: r.memo,
    // いいねはまだこのブラウザの中だけ（相手には届かない）
    likes: local.likes[r.id] ? 1 : 0,
    comments: 0,
    liked: Boolean(local.likes[r.id]),
    avgLoc: r.avgLoc,
    locSeries: r.locSeries || [],
    isMine,
  };
}

/** タイムライン（フォロータブ）の投稿：自分とフォロー中の人の学習記録（新しい順） */
export async function getTimelinePosts() {
  const { records } = await api("GET", "/api/timeline");
  return records.map(toPost);
}

/** その人の学習記録を投稿カードの形で（新しい順） */
export async function getUserPosts(userId) {
  const { records } = await api("GET", `/api/users/${userId}/records`);
  return records.map(toPost);
}

/* ============ 更新系 ============ */

/** いいねのオン・オフを切り替え、切り替え後の状態を返す */
export function toggleLike(postId, currentLiked) {
  const next = !currentLiked;
  local.likes[postId] = next;
  saveLocal();
  return next;
}

/* ============ ユーザー・フォロー ============ */

export async function searchUsers(query) {
  const { users } = await api("GET", `/api/users?q=${encodeURIComponent(query)}`);
  return users.map(toPerson);
}

/** 公開プロフィール＋フォロー数＋自分との関係（isMe / isFollowing / followsMe / canMessage） */
export async function getPerson(userId) {
  const { user } = await api("GET", `/api/users/${userId}`);
  return toPerson(user);
}

export async function getFollowers(userId) {
  const { users } = await api("GET", `/api/users/${userId}/followers`);
  return users.map(toPerson);
}

export async function getFollowing(userId) {
  const { users } = await api("GET", `/api/users/${userId}/following`);
  return users.map(toPerson);
}

/** フォローする／やめる。更新後の相手（関係つき）を返す */
export async function setFollowing(userId, follow) {
  const { user } = follow
    ? await api("PUT", `/api/users/${userId}/follow`, {})
    : await api("DELETE", `/api/users/${userId}/follow`);
  return toPerson(user);
}

/* ============ 通知・バッジ ============ */

export const getBadges = () => ({ ...badges });

/** 未読の数をサーバーから取り直す（変わっていたら BADGES_EVENT を投げる） */
export async function refreshBadges() {
  if (!currentUser?.profileCompleted) return badges;
  try {
    const next = await api("GET", "/api/badges");
    if (next.notifications !== badges.notifications || next.messages !== badges.messages) {
      badges = next;
      window.dispatchEvent(new Event(BADGES_EVENT));
    }
  } catch (err) {
    console.warn("[store] 未読の数を取れませんでした", err);
  }
  return badges;
}

export async function getNotifications() {
  const { notifications } = await api("GET", "/api/notifications");
  return notifications.map((n) => ({ ...n, actor: n.actor ? toPerson(n.actor) : null }));
}

export async function markNotificationsRead() {
  await api("POST", "/api/notifications/read", {});
  await refreshBadges();
}

/* ============ メッセージ ============ */

export async function getConversations() {
  const { conversations } = await api("GET", "/api/messages");
  return conversations.map((c) => ({ ...c, partner: toPerson(c.partner) }));
}

/** 相手とのメッセージ。afterId より新しいものだけ。開くと相手からの分は既読になる */
export async function getThread(userId, afterId = 0) {
  const data = await api("GET", `/api/messages/${userId}?after=${afterId}`);
  return { partner: toPerson(data.partner), messages: data.messages };
}

export async function sendMessage(userId, body) {
  const { message } = await api("POST", `/api/messages/${userId}`, { body });
  return message;
}

/* ============ 招待申請 ============ */

/** 招待されていない人の申請用：Google で確認したメールアドレスと申請の状況（無ければ null） */
export async function getApplication() {
  try {
    return await api("GET", "/api/apply");
  } catch (err) {
    if (err.status === 404 || err.status === 0) return null;
    throw err;
  }
}

export async function submitApplication(message) {
  const { request } = await api("POST", "/api/apply", { message });
  return request;
}

/** 友だちの招待を申請する */
export async function referFriend(email, message) {
  const { request } = await api("POST", "/api/invite-requests", { email, message });
  return request;
}

export async function getMyInviteRequests() {
  const { requests } = await api("GET", "/api/invite-requests");
  return requests;
}

/** 管理者：招待申請の一覧（status は "pending" / "approved" / "rejected" / 省略で全部） */
export async function getInviteRequests(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const { requests } = await api("GET", `/api/admin/invite-requests${query}`);
  return requests;
}

/** 管理者：申請を承認（"approve"）／却下（"reject"）する */
export async function decideInviteRequest(requestId, action) {
  const { request } = await api("POST", `/api/admin/invite-requests/${requestId}/${action}`, {});
  refreshBadges();
  return request;
}

/* ============ 集計（レポート用） ============ */

/** 指定日の合計学習時間（分） */
function minutesOnDate(dateKey) {
  return myRecords
    .filter((r) => toDateKey(r.createdAt) === dateKey)
    .reduce((sum, r) => sum + r.durationMin, 0);
}

/** 今日・今月・累計のサマリー */
export function getSummary() {
  const todayKey = toDateKey(new Date());
  const now = new Date();
  const monthPrefix = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

  const monthMin = myRecords
    .filter((r) => toDateKey(r.createdAt).startsWith(monthPrefix))
    .reduce((sum, r) => sum + r.durationMin, 0);
  const totalMin = myRecords.reduce((sum, r) => sum + r.durationMin, 0);

  return {
    todayMin: minutesOnDate(todayKey),
    monthMin,
    totalMin,
    recordCount: myRecords.length,
  };
}

/** 直近 n 日分の日付キーを古い順に返す */
export function recentDateKeys(days = 7) {
  const keys = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    keys.push(toDateKey(d));
  }
  return keys;
}

/**
 * 1週間分の「日 × 教材」の積み上げ棒グラフ用データ。
 * 返り値: { labels: 日付キー[], datasets: [{ label, color, data:[分,...] }] }
 */
export function getWeeklyByMaterial(days = 7) {
  const labels = recentDateKeys(days);
  const inRange = myRecords.filter((r) => labels.includes(toDateKey(r.createdAt)));

  // 期間内に登場した教材だけを積み上げ対象にする
  const materialIds = [...new Set(inRange.map((r) => r.materialId))];

  const datasets = materialIds.map((materialId) => {
    const material = getMaterial(materialId);
    return {
      label: material.title,
      color: material.color,
      data: labels.map((key) =>
        inRange
          .filter((r) => r.materialId === materialId && toDateKey(r.createdAt) === key)
          .reduce((sum, r) => sum + r.durationMin, 0)
      ),
    };
  });

  return { labels, datasets };
}

/** 教材別の合計時間（円グラフ用）。多い順。 */
export function getMaterialBreakdown(days = 7) {
  const labels = recentDateKeys(days);
  const inRange = myRecords.filter((r) => labels.includes(toDateKey(r.createdAt)));

  const map = new Map();
  inRange.forEach((r) => {
    map.set(r.materialId, (map.get(r.materialId) || 0) + r.durationMin);
  });

  const items = [...map.entries()]
    .map(([materialId, minutes]) => {
      const m = getMaterial(materialId);
      return { title: m.title, color: m.color, minutes };
    })
    .sort((a, b) => b.minutes - a.minutes);

  // 同じ科目の教材は色が重なってしまうので、2冊目以降は少しずつ明るくして区別する
  const seen = new Map();
  return items.map((item) => {
    const n = seen.get(item.color) || 0;
    seen.set(item.color, n + 1);
    return n === 0 ? item : { ...item, color: lighten(item.color, n * 0.22) };
  });
}

/** "#5b5bd6" を ratio（0〜1）だけ白に近づける */
function lighten(hex, ratio) {
  const num = parseInt(hex.replace("#", ""), 16);
  const mix = (channel) => Math.round(channel + (255 - channel) * Math.min(0.8, ratio));
  const r = mix((num >> 16) & 0xff);
  const g = mix((num >> 8) & 0xff);
  const b = mix(num & 0xff);
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

/**
 * 日別の平均LoC（折れ線グラフ用）。
 * 学習時間で重み付けして平均するので、長く勉強した記録の影響が大きくなる。
 * LoC を計測していない記録は無視し、その日の記録が0件なら null（グラフが途切れる）。
 */
export function getDailyAvgLoc(days = 7) {
  const labels = recentDateKeys(days);
  const values = labels.map((key) => {
    const day = myRecords.filter(
      (r) => toDateKey(r.createdAt) === key && typeof r.avgLoc === "number"
    );
    if (day.length === 0) return null;
    const weighted = day.reduce((sum, r) => sum + r.avgLoc * r.durationMin, 0);
    const totalMin = day.reduce((sum, r) => sum + r.durationMin, 0);
    return totalMin > 0 ? Math.round(weighted / totalMin) : null;
  });
  return { labels, values };
}

/** 今週の目標に対する達成率（0〜100） */
export function getWeeklyGoal() {
  const labels = recentDateKeys(7);
  const doneMin = myRecords
    .filter((r) => labels.includes(toDateKey(r.createdAt)))
    .reduce((sum, r) => sum + r.durationMin, 0);
  const goalMin = getMe().weeklyGoalMinutes || DEFAULT_WEEKLY_GOAL_MIN;
  return {
    goalMin,
    doneMin,
    rate: Math.min(100, Math.round((doneMin / goalMin) * 100)),
  };
}
