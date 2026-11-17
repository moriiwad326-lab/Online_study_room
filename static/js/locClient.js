/**
 * locClient.js : LoC（集中度）を取得するクライアント
 *
 * 画面側は「LocClient を作って start() し、onLoc で値を受け取る」だけでよい。
 * 推論サーバーにつながらない・切れたときは onStatus に error が来る。モックの LoC は作らない
 * （ver1.2 で廃止。画面は「接続できなかった」と出し、LoC なしで時間だけ計り続ける）。
 * つなぎ直すときは restart() を呼ぶ。
 *
 * ── 本番サーバーとのやりとり（Python 側と合わせる） ───────────────
 *   接続先 : localhost で開いたとき … ws://localhost:8765/loc（推論サーバーに直接）
 *            それ以外（ngrok など）  … wss://<開いているホスト>/loc（main.py が推論サーバーへ中継）
 *   送信   : { "type": "frame", "timestamp": <ms>, "image": "<JPEGのBase64>" }   約5fps
 *   受信   : { "type": "loc",   "timestamp": <ms>, "loc": 0〜100 }
 *            { "type": "warmup", "timestamp": <ms>, "remaining": <残り枚数>, "total": <窓の長さ>, "seconds": <残り秒> }
 *            （モデルは直近12サンプル＝約60秒ぶんの映像がたまるまで LoC を出せない）
 * ──────────────────────────────────────────────────────────────
 */

import { clampLoc } from "./utils.js";

/**
 * 推論サーバーの接続先をページの URL から決める。
 *   localhost で開いたとき … 推論サーバー（8765番）に直接つなぐ。LoC/server.py 単体でフロントを配信しても動く
 *   それ以外のとき         … ページと同じホストの /loc。アプリサーバー（main.py）が推論サーバーへ中継する。
 *                            スマホから ngrok の https:// で開いたときは wss:// になる
 */
function defaultLocUrl() {
  const { protocol, hostname, host } = window.location;
  if (hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]") {
    return "ws://localhost:8765/loc";
  }
  return `${protocol === "https:" ? "wss" : "ws"}://${host}/loc`;
}

/** 推論サーバーとのやりとりの設定 */
export const LOC_CONFIG = {
  url: defaultLocUrl(),   // 固定したいときは "ws://localhost:8765/loc" などを直接書く
  fps: 5,                 // 1秒あたり何枚フレームを送るか
  frameWidth: 640,        // 送信するJPEGの横幅（縦は映像の比率に合わせる）。小さいと手の検出が落ちる
  jpegQuality: 0.8,       // JPEG の品質（0〜1）。上げると精度は上がるが通信量も増える
  connectTimeoutMs: 4000, // これだけ待って繋がらなければ失敗とみなす
};

/**
 * 接続状態の一覧。UI のバッジ表示に使う。
 *   idle       … 未接続
 *   connecting … 接続中
 *   live       … 推論サーバーに接続済み
 *   error      … 接続失敗・切断（LoC は届かない）
 */
export const LOC_STATUS = {
  IDLE: "idle",
  CONNECTING: "connecting",
  LIVE: "live",
  ERROR: "error",
};

export class LocClient {
  /**
   * @param {object} handlers
   * @param {(payload:{loc:number, timestamp:number})=>void} handlers.onLoc    LoC を受け取るたびに呼ばれる
   * @param {(status:string, detail?:string)=>void}          handlers.onStatus 接続状態が変わったときに呼ばれる
   * @param {(payload:{remaining:number, total:number, seconds:number})=>void} handlers.onWarmup
   *        推論サーバーが最初の LoC を出せるようになるまでの進み具合
   */
  constructor({ onLoc, onStatus, onWarmup } = {}) {
    this.onLoc = onLoc || (() => {});
    this.onStatus = onStatus || (() => {});
    this.onWarmup = onWarmup || (() => {});

    this.status = LOC_STATUS.IDLE;
    this.paused = false;

    this._ws = null;
    this._video = null;
    this._canvas = null;
    this._frameTimer = null;
    this._connectTimer = null;
  }

  /* ============ 公開API ============ */

  /**
   * 計測を開始する。
   * @param {{video?: HTMLVideoElement}} options カメラの video 要素
   */
  start({ video } = {}) {
    this._video = video || null;
    this.paused = false;
    this._startLive();
  }

  /** 一時停止（接続は保ったまま、フレーム送信だけ止める） */
  pause() {
    this.paused = true;
  }

  /** 一時停止から再開 */
  resume() {
    this.paused = false;
  }

  /**
   * 推論サーバーにつなぎ直して、最初から推定し直す（カメラを切り替えたとき・接続が切れたあとの再接続）。
   * サーバーは新しい接続が来ると直近の映像の窓を捨てるので、別のカメラの映像が混ざらない。
   * 一時停止の状態はそのまま。
   */
  restart() {
    const paused = this.paused;
    this.stop();
    this.start({ video: this._video });
    this.paused = paused;
  }

  /** 計測を終了し、後片付けをする */
  stop() {
    clearInterval(this._frameTimer);
    clearTimeout(this._connectTimer);
    this._frameTimer = this._connectTimer = null;

    if (this._ws) {
      // 切断時のエラーハンドラが走らないように外してから閉じる
      this._ws.onclose = null;
      this._ws.onerror = null;
      try { this._ws.close(); } catch { /* すでに閉じている場合は無視 */ }
      this._ws = null;
    }
    this._setStatus(LOC_STATUS.IDLE);
  }

  /* ============ WebSocket ============ */

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
      } else if (data.type === "warmup") {
        this.onWarmup({
          remaining: Number(data.remaining) || 0,
          total: Number(data.total) || 0,
          seconds: Number(data.seconds) || 0,
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

  /** 接続に失敗した・切れたとき。接続を片付けて error を知らせる（つなぎ直しは restart()） */
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
    case LOC_STATUS.CONNECTING: return "接続しています…";
    case LOC_STATUS.ERROR:      return "推論サーバー未接続";
    default:                    return "待機中";
  }
}
