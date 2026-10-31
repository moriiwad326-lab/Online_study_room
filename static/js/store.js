/**
 * store.js : アプリのデータを一手に引き受ける場所
 *
 *  - data/mock.json を読み込む（サーバーの代わり）
 *  - ログイン中のユーザー（プロフィール込み）と、そのユーザーの教材・カテゴリを API から受け取って持つ
 *  - ユーザーの操作で増えるデータ（自分の学習記録・いいね）は localStorage にユーザーごとに保存する
 *  - 画面側は store の関数だけを呼び、localStorage や API を直接触らない
 */

import { toDateKey, uid, average } from "./utils.js";
import { api } from "./api.js";

/** localStorage のキーの頭。実際のキーは "onsta.v1.u<ユーザーID>"。データ構造を変えたらバージョンを上げる。 */
const STORAGE_PREFIX = "onsta.v1";

/** プロフィールでアイコン色を選ばなかったときの色 */
export const DEFAULT_AVATAR_COLOR = "#5b5bd6";

/** 週の目標を設定していないときに使う値（分） */
const DEFAULT_WEEKLY_GOAL_MIN = 1800;

/** localStorage に保存する側の初期値 */
const emptyLocal = {
  records: [],   // 自分が計測・手入力した学習記録
  likes: {},     // { 投稿ID: true } いいね状態
  createdAt: null,
};

/** mock.json の中身（読み込み後にセットされる） */
let mock = null;
/** localStorage の中身 */
let local = { ...emptyLocal };

/** ログイン中のユーザー { id, loginId, profileCompleted, profile }。未ログインなら null */
let currentUser = null;
/** 今のユーザーの localStorage キー（未ログインなら null で、保存しない） */
let storageKey = null;
/** 認証 API に届いたか（python -m http.server だけで配信していると false） */
let authAvailable = true;

/** 自分のカテゴリ（教科）と教材。サーバーの値をそのまま持つ */
let myCategories = [];
let myMaterials = [];

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

/** ログインしたユーザーに切り替え、そのユーザーの保存データと教材を開く */
async function setUser(user) {
  currentUser = user;
  storageKey = `${STORAGE_PREFIX}.u${user.id}`;
  local = loadLocal();
  await reloadMaterials();
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

export async function login(loginId, password) {
  const { user } = await api("POST", "/api/auth/login", { loginId, password });
  await setUser(user);
  return user;
}

/** 新規登録（成功するとそのままログイン状態になる） */
export async function register(loginId, password) {
  const { user } = await api("POST", "/api/auth/register", { loginId, password });
  await setUser(user);
  return user;
}

export async function logout() {
  try {
    await api("POST", "/api/auth/logout", {});
  } finally {
    currentUser = null;
    storageKey = null;
    local = { ...emptyLocal };
    myCategories = [];
    myMaterials = [];
  }
}

/** プロフィールを保存する（初回設定と編集の両方）。保存後のユーザーを返す */
export async function saveProfile(profile) {
  const { user } = await api("PUT", "/api/me/profile", { profile });
  currentUser = user;
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

/* ============ 参照系 ============ */

/**
 * 画面に出す「自分」。プロフィールの未設定項目は空にそろえて返す。
 * name はニックネーム（未設定ならログインID）、initial はその1文字目。
 */
export function getMe() {
  const p = currentUser?.profile || {};
  const name = p.nickname || currentUser?.loginId || "ゲスト";
  return {
    id: currentUser ? `u${currentUser.id}` : "guest",
    loginId: currentUser?.loginId || "",
    name,
    handle: currentUser ? `@${currentUser.loginId}` : "",
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

/** 保存しているプロフィールそのもの（編集フォームの初期値用） */
export const getProfile = () => ({ ...(currentUser?.profile || {}) });
/** 自分のカテゴリ（作った順） */
export const getCategories = () => [...myCategories];
/** 自分の教材（追加した順） */
export const getMaterials = () => myMaterials.map(toMaterial);
export const getArticles = () =>
  mock.articles.map((a) => ({ ...a, createdAt: daysAgoToIso(a.daysAgo) }));
export const getFindTargets = () => mock.findTargets;
export const getBadges = () => ({ notifications: mock.notifications, messages: mock.messages });

/**
 * 教材IDから教材（科目名・科目色つき）を引く。
 * 自分の教材を先に探し、なければモックの教材（他ユーザーの投稿用）を探す。
 */
export function getMaterial(materialId) {
  const mine = myMaterials.find((x) => String(x.id) === String(materialId));
  if (mine) return toMaterial(mine);

  const m = mock.materials.find((x) => x.id === materialId);
  if (!m) {
    return { id: materialId, title: "削除した教材", subjectId: "", subjectName: "その他",
             color: UNKNOWN_COLOR, imageUrl: null, missing: true };
  }
  const subject = mock.subjects.find((s) => s.id === m.subjectId);
  return { ...m, subjectName: subject?.name ?? "その他", color: subject?.color ?? UNKNOWN_COLOR, imageUrl: null };
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

/**
 * タイムラインに並べる投稿。
 * モックの他ユーザー投稿と、自分の学習記録を時刻順にまとめる。
 */
export function getTimelinePosts() {
  const others = mock.posts.map((p) => {
    // localStorage 側のいいね状態があればそちらを優先し、いいね数もその分だけ増減させる
    const liked = local.likes[p.id] ?? p.liked;
    const diff = (liked ? 1 : 0) - (p.liked ? 1 : 0);
    return {
      ...p,
      createdAt: minutesAgoToIso(p.minutesAgo),
      material: getMaterial(p.materialId),
      liked,
      likes: p.likes + diff,
      isMine: false,
    };
  });

  const me = getMe();
  const mine = local.records.map((r) => ({
    id: r.id,
    userId: me.id,
    userName: me.name,
    initial: me.initial,
    avatarColor: me.avatarColor,
    createdAt: r.createdAt,
    material: getMaterial(r.materialId),
    durationMin: r.durationMin,
    memo: r.memo,
    likes: 0,
    comments: 0,
    liked: false,
    avgLoc: r.avgLoc,
    locSeries: r.locSeries || [],
    isMine: true,
  }));

  return [...others, ...mine].sort(
    (a, b) => new Date(b.createdAt) - new Date(a.createdAt)
  );
}

/** 自分の学習記録（新しい順） */
export function getRecords() {
  return [...local.records]
    .map((r) => ({ ...r, material: getMaterial(r.materialId) }))
    .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
}

/* ============ 更新系 ============ */

/**
 * 学習記録を1件追加する。計測終了時と手動記録の両方から呼ばれる。
 * @param {{materialId:string, durationMin:number, memo?:string, locSeries?:number[]}} input
 */
export function addRecord(input) {
  const series = (input.locSeries || []).map((v) => Math.round(v));
  const record = {
    id: uid("rec"),
    materialId: input.materialId,
    durationMin: Math.max(0, Math.round(input.durationMin)),
    memo: input.memo || "",
    locSeries: series,
    // LoC を計測していない記録は avgLoc を null にして、UI 側で非表示にする
    avgLoc: series.length ? Math.round(average(series)) : null,
    createdAt: new Date().toISOString(),
    source: input.source || "manual",
  };
  local.records.push(record);
  saveLocal();
  return record;
}

/** いいねのオン・オフを切り替え、切り替え後の状態を返す */
export function toggleLike(postId, currentLiked) {
  const next = !currentLiked;
  local.likes[postId] = next;
  saveLocal();
  return next;
}

/** 今のユーザーの保存データを全部消す（README の「初期化したいとき」用） */
export function resetLocal() {
  if (storageKey) localStorage.removeItem(storageKey);
  local = { ...emptyLocal, createdAt: new Date().toISOString() };
}

/* ============ 集計（レポート用） ============ */

/** 指定日の合計学習時間（分） */
function minutesOnDate(dateKey) {
  return local.records
    .filter((r) => toDateKey(r.createdAt) === dateKey)
    .reduce((sum, r) => sum + r.durationMin, 0);
}

/** 今日・今月・累計のサマリー */
export function getSummary() {
  const todayKey = toDateKey(new Date());
  const now = new Date();
  const monthPrefix = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

  const monthMin = local.records
    .filter((r) => toDateKey(r.createdAt).startsWith(monthPrefix))
    .reduce((sum, r) => sum + r.durationMin, 0);
  const totalMin = local.records.reduce((sum, r) => sum + r.durationMin, 0);

  return {
    todayMin: minutesOnDate(todayKey),
    monthMin,
    totalMin,
    recordCount: local.records.length,
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
  const inRange = local.records.filter((r) => labels.includes(toDateKey(r.createdAt)));

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
  const inRange = local.records.filter((r) => labels.includes(toDateKey(r.createdAt)));

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
    const day = local.records.filter(
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
  const doneMin = local.records
    .filter((r) => labels.includes(toDateKey(r.createdAt)))
    .reduce((sum, r) => sum + r.durationMin, 0);
  const goalMin = getMe().weeklyGoalMinutes || DEFAULT_WEEKLY_GOAL_MIN;
  return {
    goalMin,
    doneMin,
    rate: Math.min(100, Math.round((doneMin / goalMin) * 100)),
  };
}
