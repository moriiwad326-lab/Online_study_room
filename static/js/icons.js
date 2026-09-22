/**
 * icons.js : 画面で使うアイコンをすべてインラインSVGで自作している。
 * 外部のアイコン画像は一切使わない。currentColor なので CSS の color で色が変わる。
 */

/** SVG の共通ラッパー。size でピクセルサイズを変えられる。 */
const svg = (inner, size = 22) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
        stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;

export const icons = {
  /* --- ナビゲーション --- */

  // タイムライン：積み重なったカード
  timeline: (s) => svg(`
    <rect x="3" y="4" width="18" height="6" rx="2"/>
    <rect x="3" y="14" width="18" height="6" rx="2"/>
    <path d="M7 7h.01M7 17h.01"/>`, s),

  // 記録する：ペンと本
  record: (s) => svg(`
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H18v14H6.5A2.5 2.5 0 0 0 4 19.5z"/>
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H18v4H6.5A2.5 2.5 0 0 1 4 19.5z"/>
    <path d="M14.5 6.5 10 11l-.6 2.1 2.1-.6 4.5-4.5z"/>`, s),

  // レポート：棒グラフ
  report: (s) => svg(`
    <path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>`, s),

  // さがす：虫めがね
  search: (s) => svg(`
    <circle cx="11" cy="11" r="7"/>
    <path d="m20 20-3.5-3.5"/>`, s),

  // もっと見る：三点
  more: (s) => svg(`
    <circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none"/>
    <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/>
    <circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none"/>`, s),

  /* --- ヘッダー --- */

  // 通知ベル
  bell: (s) => svg(`
    <path d="M18 9a6 6 0 0 0-12 0c0 5-2 6-2 6h16s-2-1-2-6"/>
    <path d="M10.5 20a1.9 1.9 0 0 0 3 0"/>`, s),

  // メッセージ（吹き出し）
  message: (s) => svg(`
    <path d="M21 12a8 8 0 0 1-8 8H8l-4 2 1-4.2A8 8 0 1 1 21 12z"/>`, s),

  /* --- 操作 --- */

  like: (s) => svg(`
    <path d="M12 20s-7-4.4-7-9.2A4 4 0 0 1 12 8a4 4 0 0 1 7-.8c.6 1 .6 2.3 0 3.6C17.6 15.6 12 20 12 20z"/>`, s),

  comment: (s) => svg(`
    <path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H8l-4 2 1.2-3.7A7.5 7.5 0 1 1 20 11.5z"/>`, s),

  plus: (s) => svg(`<path d="M12 5v14M5 12h14"/>`, s),

  camera: (s) => svg(`
    <path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h1.8l1.2-2h7l1.2 2h1.8A2.5 2.5 0 0 1 21 8.5v9A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5z"/>
    <circle cx="12" cy="13" r="3.4"/>`, s),

  play: (s) => svg(`<path d="M7 5.5 18.5 12 7 18.5z" fill="currentColor"/>`, s),

  pause: (s) => svg(`<path d="M8.5 5v14M15.5 5v14" stroke-width="2.6"/>`, s),

  stop: (s) => svg(`<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" stroke="none"/>`, s),

  eye: (s) => svg(`
    <path d="M2 12s3.8-6.5 10-6.5S22 12 22 12s-3.8 6.5-10 6.5S2 12 2 12z"/>
    <circle cx="12" cy="12" r="2.8"/>`, s),

  eyeOff: (s) => svg(`
    <path d="M4 4 20 20"/>
    <path d="M9.6 6.1A9.8 9.8 0 0 1 12 5.5c6.2 0 10 6.5 10 6.5a18 18 0 0 1-3.3 4"/>
    <path d="M6.3 8.2A17.6 17.6 0 0 0 2 12s3.8 6.5 10 6.5a10 10 0 0 0 3.6-.7"/>`, s),

  clock: (s) => svg(`
    <circle cx="12" cy="12" r="8.5"/>
    <path d="M12 7.5V12l3 1.8"/>`, s),

  /* --- さがす画面のカード用 --- */

  user: (s) => svg(`
    <circle cx="12" cy="8.5" r="3.7"/>
    <path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>`, s),

  book: (s) => svg(`
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v15H6.5A2.5 2.5 0 0 0 4 20.5z"/>
    <path d="M9 7.5h6"/>`, s),

  school: (s) => svg(`
    <path d="M12 4 2.5 9 12 14l9.5-5z"/>
    <path d="M6 11.3V16c0 1.7 2.7 3 6 3s6-1.3 6-3v-4.7"/>`, s),

  /* --- その他 --- */

  target: (s) => svg(`
    <circle cx="12" cy="12" r="8.5"/>
    <circle cx="12" cy="12" r="4.5"/>
    <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>`, s),

  spark: (s) => svg(`
    <path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12z"/>`, s),
};

/** 名前でアイコンを取り出す（未定義なら空文字） */
export function icon(name, size = 22) {
  const fn = icons[name];
  return fn ? fn(size) : "";
}
