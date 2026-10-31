/**
 * barcode.js : カメラで書籍のバーコード（ISBN / EAN-13）を読み取る
 *
 *  - ブラウザに BarcodeDetector があればそれを使う（Android の Chrome、macOS など）
 *  - 無ければ ZXing（@zxing/browser）を CDN から読み込んで使う（Windows の Chrome など）
 *    ZXing は読み取りを始めたときに初めて読み込むので、ほかの画面は重くならない
 *
 *  日本の書籍には上下2段のバーコードがあり、下段（192…）は価格のコード。
 *  ここでは 978 / 979 で始まる上段だけを拾う。
 */

const ZXING_URL = "https://cdn.jsdelivr.net/npm/@zxing/browser@0.1.5/umd/zxing-browser.min.js";

/** 読み取りの間隔（BarcodeDetector を使うとき） */
const DETECT_INTERVAL_MS = 250;

/** 書籍の ISBN-13 として拾うコードか */
export function isBookCode(text) {
  return /^97[89]\d{10}$/.test(text);
}

/**
 * カメラを起動して読み取りを始める。
 * @param {HTMLVideoElement} video プレビューを映す video 要素
 * @param {(code:string) => void} onCode 書籍のコードを読めたら1回だけ呼ばれる
 * @returns {Promise<() => void>} 読み取りとカメラを止める関数
 */
export async function startScanner(video, onCode) {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
    audio: false,
  });

  let stopped = false;
  let stopDecoder = () => {};
  const stop = () => {
    if (stopped) return;
    stopped = true;
    stopDecoder();
    stream.getTracks().forEach((t) => t.stop());
    video.srcObject = null;
  };
  const found = (text) => {
    if (stopped || !isBookCode(text)) return;
    stop();
    onCode(text);
  };

  try {
    if (await hasNativeDetector()) {
      video.srcObject = stream;
      await video.play();
      stopDecoder = runNativeDetector(video, found);
    } else {
      await loadZxing();
      const reader = new window.ZXingBrowser.BrowserMultiFormatReader();
      const controls = await reader.decodeFromStream(stream, video, (result) => {
        if (result) found(result.getText());
      });
      stopDecoder = () => controls.stop();
    }
  } catch (err) {
    stop();
    throw err;
  }
  return stop;
}

async function hasNativeDetector() {
  if (!("BarcodeDetector" in window)) return false;
  try {
    const formats = await window.BarcodeDetector.getSupportedFormats();
    return formats.includes("ean_13");
  } catch {
    return false;
  }
}

function runNativeDetector(video, found) {
  const detector = new window.BarcodeDetector({ formats: ["ean_13"] });
  let timer = null;
  let busy = false;

  timer = setInterval(async () => {
    if (busy || video.readyState < 2) return;
    busy = true;
    try {
      const codes = await detector.detect(video);
      codes.forEach((c) => found(c.rawValue));
    } catch {
      // 1回失敗しても次のフレームで読めばよい
    } finally {
      busy = false;
    }
  }, DETECT_INTERVAL_MS);

  return () => clearInterval(timer);
}

let zxingPromise = null;

/** ZXing を1回だけ読み込む */
function loadZxing() {
  if (window.ZXingBrowser) return Promise.resolve();
  if (!zxingPromise) {
    zxingPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = ZXING_URL;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => {
        zxingPromise = null;
        script.remove();
        reject(new Error("バーコード読み取りの部品を読み込めませんでした（ネットワークを確認してください）"));
      };
      document.head.appendChild(script);
    });
  }
  return zxingPromise;
}
