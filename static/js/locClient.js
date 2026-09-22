/**
 * locClient.js : LoC（集中度）を取得するクライアント
 *
 * 画面側は「LocClient を作って start() し、onLoc で値を受け取る」だけでよい。
 * 中身がモックでも本番の WebSocket でも、使い方は変わらない。
 *
 * ── 本番サーバーとのやりとり（Python 側と合わせる） ───────────────
 *   接続先 : ws://localhost:8765/loc
 *   送信   : { "type": "frame", "timestamp": <ms>, "image": "<JPEGのBase64>" }   約5fps
 *   受信   : { "type": "loc",   "timestamp": <ms>, "loc": 0〜100 }
 * ──────────────────────────────────────────────────────────────
 */

import { clampLoc } from "./utils.js";

/**
 * ★ モック / 本番の切り替えはこの1行だけ ★
 *   "mock" … カメラ映像は送らず、ランダムウォークで LoC を作る
 *   "live" … WebSocket で推論サーバーに接続し、カメラフレームを送る
 */
export const LOC_CONFIG = {
  mode: "mock",

  // --- 本番モードの設定 ---
  url: "ws://localhost:8765/loc",
  fps: 5,                 // 1秒あたり何枚フレームを送るか
  frameWidth: 320,        // 送信するJPEGの横幅（縦は映像の比率に合わせる）
  jpegQuality: 0.6,       // JPEG の品質（0〜1）。上げると精度は上がるが通信量も増える
  connectTimeoutMs: 4000, // これだけ待って繋がらなければ失敗とみなす

  // --- モックモードの設定 ---
  mockIntervalMs: 1000,   // 何ミリ秒ごとに LoC を返すか
  mockStart: 60,          // 開始時の LoC
  mockStep: 7,            // 1ステップで動く最大幅（大きいほどガタガタする）
  mockCenter: 68,         // ランダムウォークが引き寄せられる中心値

  // 接続に失敗したとき、自動でモックに切り替えるか
  autoFallbackToMock: true,
};

/**
 * 接続状態の一覧。UI のバッジ表示に使う。
 *   idle       … 未接続
 *   connecting … 接続中
 *   live       … 推論サーバーに接続済み
 *   mock       … モックで動作中
 *   error      … 接続失敗・切断
 */
export const LOC_STATUS = {
  IDLE: "idle",
  CONNECTING: "connecting",
  LIVE: "live",
  MOCK: "mock",
  ERROR: "error",
};

export class LocClient {
  /**
   * @param {object} handlers
   * @param {(payload:{loc:number, timestamp:number})=>void} handlers.onLoc    LoC を受け取るたびに呼ばれる
   * @param {(status:string, detail?:string)=>void}          handlers.onStatus 接続状態が変わったときに呼ばれる
   */
  constructor({ onLoc, onStatus } = {}) {
    this.onLoc = onLoc || (() => {});
    this.onStatus = onStatus || (() => {});

    this.mode = LOC_CONFIG.mode;
    this.status = LOC_STATUS.IDLE;
    this.paused = false;

    this._ws = null;
    this._video = null;
    this._canvas = null;
    this._frameTimer = null;
    this._mockTimer = null;
    this._mockValue = LOC_CONFIG.mockStart;
    this._connectTimer = null;
  }

  /* ============ 公開API ============ */

  /**
   * 計測を開始する。
   * @param {{video?: HTMLVideoElement}} options 本番モードではカメラの video 要素が必要
   */
  start({ video } = {}) {
    this._video = video || null;
    this.paused = false;

    if (this.mode === "live") {
      this._startLive();
    } else {
      this._startMock();
    }
  }

  /** 一時停止（接続は保ったまま、フレーム送信とモック生成だけ止める） */
  pause() {
    this.paused = true;
  }

  /** 一時停止から再開 */
  resume() {
    this.paused = false;
  }

  /** 計測を終了し、後片付けをする */
  stop() {
    clearInterval(this._frameTimer);
    clearInterval(this._mockTimer);
    clearTimeout(this._connectTimer);
    this._frameTimer = this._mockTimer = this._connectTimer = null;

    if (this._ws) {
      // 切断時のエラーハンドラが走らないように外してから閉じる
      this._ws.onclose = null;
      this._ws.onerror = null;
      try { this._ws.close(); } catch { /* すでに閉じている場合は無視 */ }
      this._ws = null;
    }
    this._setStatus(LOC_STATUS.IDLE);
  }

  /**
   * 手動でモックに切り替える（接続エラー時の「モックで続ける」ボタン用）。
   * 進行中の計測を止めずにそのまま続けられる。
   */
  switchToMock() {
    clearInterval(this._frameTimer);
    clearTimeout(this._connectTimer);
    if (this._ws) {
      this._ws.onclose = null;
      this._ws.onerror = null;
      try { this._ws.close(); } catch { /* 無視 */ }
      this._ws = null;
    }
    this.mode = "mock";
    this._startMock();
  }

  /* ============ モック実装 ============ */

  /**
   * ランダムウォークでなめらかな LoC を作る。
   * 単純な乱数だと毎回跳ねてしまうので、
   *   ・前回値からの差分を小さく保つ
   *   ・中心値（mockCenter）へゆるやかに引き戻す
   * の2点で「それっぽい」動きにしている。
   */
  _startMock() {
    clearInterval(this._mockTimer);
    this._setStatus(LOC_STATUS.MOCK);

    this._mockTimer = setInterval(() => {
      if (this.paused) return;

      const drift = (LOC_CONFIG.mockCenter - this._mockValue) * 0.08; // 中心に戻る力
      const noise = (Math.random() - 0.5) * 2 * LOC_CONFIG.mockStep;  // ゆらぎ
      this._mockValue = clampLoc(this._mockValue + drift + noise);

      this.onLoc({ loc: this._mockValue, timestamp: Date.now() });
    }, LOC_CONFIG.mockIntervalMs);
  }

  /* ============ 本番（WebSocket）実装 ============ */

  _startLive() {
    this._setStatus(LOC_STATUS.CONNECTING);

    let ws;
    try {
      ws = new WebSocket(LOC_CONFIG.url);
    } catch (err) {
      this._failLive(`WebSocket を作成できませんでした: ${err.message}`);
      return;
    }
    this._ws = ws;

    // 一定時間つながらなければ失敗扱いにする
    this._connectTimer = setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        try { ws.close(); } catch { /* 無視 */ }
        this._failLive("接続がタイムアウトしました");
      }
    }, LOC_CONFIG.connectTimeoutMs);

    ws.onopen = () => {
      clearTimeout(this._connectTimer);
      this._setStatus(LOC_STATUS.LIVE);
      this._startFrameLoop();
    };

    ws.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return; // JSON でないメッセージは無視する
      }
      if (data.type === "loc") {
        this.onLoc({
          loc: clampLoc(data.loc),
          timestamp: data.timestamp || Date.now(),
        });
      }
    };

    ws.onerror = () => this._failLive("推論サーバーに接続できませんでした");
    ws.onclose = () => {
      // 正常終了（stop()）のときは onclose を外してあるので、ここに来たら異常切断
      this._failLive("推論サーバーとの接続が切れました");
    };
  }

  /** カメラ映像を一定間隔で JPEG に変換して送り続ける */
  _startFrameLoop() {
    clearInterval(this._frameTimer);
    if (!this._video) {
      this._failLive("カメラ映像がないためフレームを送信できません");
      return;
    }

    const intervalMs = Math.round(1000 / LOC_CONFIG.fps);
    this._frameTimer = setInterval(() => {
      if (this.paused) return;
      if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return;

      const base64 = this._captureFrame();
      if (!base64) return;

      this._ws.send(JSON.stringify({
        type: "frame",
        timestamp: Date.now(),
        image: base64,
      }));
    }, intervalMs);
  }

  /**
   * video の現在のコマを canvas に描き、JPEG の Base64（ヘッダを除いた部分）を返す。
   * 映像がまだ来ていないときは null を返す。
   */
  _captureFrame() {
    const video = this._video;
    if (!video || video.readyState < 2 || !video.videoWidth) return null;

    if (!this._canvas) this._canvas = document.createElement("canvas");
    const canvas = this._canvas;

    const width = LOC_CONFIG.frameWidth;
    const height = Math.round((video.videoHeight / video.videoWidth) * width);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, width, height);

    // "data:image/jpeg;base64,xxxx" の xxxx 部分だけを送る
    return canvas.toDataURL("image/jpeg", LOC_CONFIG.jpegQuality).split(",")[1];
  }

  /** 本番モードの失敗処理。設定に応じてモックへ自動フォールバックする。 */
  _failLive(detail) {
    clearInterval(this._frameTimer);
    clearTimeout(this._connectTimer);
    this._frameTimer = this._connectTimer = null;

    if (this._ws) {
      this._ws.onclose = null;
      this._ws.onerror = null;
      try { this._ws.close(); } catch { /* 無視 */ }
      this._ws = null;
    }

    this._setStatus(LOC_STATUS.ERROR, detail);

    if (LOC_CONFIG.autoFallbackToMock) {
      this.mode = "mock";
      this._startMock();
    }
  }

  /* ============ 内部ユーティリティ ============ */

  _setStatus(status, detail) {
    this.status = status;
    this.onStatus(status, detail);
  }
}

/** 状態バッジに出す日本語ラベル */
export function statusLabel(status) {
  switch (status) {
    case LOC_STATUS.LIVE:       return "推論サーバー接続中";
    case LOC_STATUS.MOCK:       return "モックで動作中";
    case LOC_STATUS.CONNECTING: return "接続しています…";
    case LOC_STATUS.ERROR:      return "接続エラー";
    default:                    return "待機中";
  }
}
