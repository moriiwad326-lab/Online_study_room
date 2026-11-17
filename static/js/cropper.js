/**
 * cropper.js : 画像を正方形に切り抜くモーダル（プロフィール画像用）
 *
 *  cropSquareImage(file, { size, quality }) でモーダルを開き、
 *  決定したら切り抜いた画像（size px 四方の JPEG の data URL）、やめたら null を返す。
 *
 *  操作：ドラッグで動かす／スライダー・マウスホイール・2本指のピンチで拡大縮小。
 *  丸い枠の外側は暗くして、アイコンで見える範囲を示す（切り抜く範囲は枠を含む正方形）。
 *  画像は枠からはみ出さない（必ず枠いっぱいに写る）ように動きを制限する。
 */

import { openModal } from "./utils.js";

/** 一番小さい（枠にちょうど収まる）大きさの何倍まで拡大できるか */
const MAX_ZOOM = 5;

/** ファイルを画像として読み込む */
function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      if (!img.naturalWidth || !img.naturalHeight) {
        URL.revokeObjectURL(url);
        reject(new Error("画像の大きさが取れません"));
        return;
      }
      resolve({ img, url });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("画像を読み込めません"));
    };
    img.src = url;
  });
}

/**
 * @param {File} file
 * @param {{size?: number, quality?: number}} [opts]
 * @returns {Promise<string|null>}
 */
export async function cropSquareImage(file, { size = 256, quality = 0.85 } = {}) {
  const { img, url } = await loadImage(file);
  const w = img.naturalWidth;
  const h = img.naturalHeight;

  // 画像の表示位置：枠（一辺 view px）の左上を原点に、画像の左上が (x, y)、倍率 scale
  const s = { view: 0, minScale: 1, scale: 1, x: 0, y: 0 };
  let result = null;

  const ok = await openModal({
    title: "画像の範囲を決める",
    okLabel: "決定",
    bodyHtml: `
      <div class="cropper" data-cropper>
        <img class="cropper__img" src="${url}" alt="" draggable="false" data-crop-img />
        <div class="cropper__mask" aria-hidden="true"></div>
      </div>
      <div class="cropper__zoom">
        <span aria-hidden="true">－</span>
        <input type="range" min="1" max="${MAX_ZOOM}" step="0.01" value="1" aria-label="拡大" data-crop-zoom />
        <span aria-hidden="true">＋</span>
      </div>
      <p class="u-small u-weak" style="text-align:center">ドラッグで位置を、スライダー（2本指・ホイール）で大きさを変えられます</p>`,
    onMount(root) {
      const stage = root.querySelector("[data-cropper]");
      const image = root.querySelector("[data-crop-img]");
      const zoom = root.querySelector("[data-crop-zoom]");

      /** 画像が枠からはみ出さないように位置を戻す */
      const clamp = () => {
        s.x = Math.min(0, Math.max(s.view - w * s.scale, s.x));
        s.y = Math.min(0, Math.max(s.view - h * s.scale, s.y));
      };
      const draw = () => {
        clamp();
        image.style.width = `${w * s.scale}px`;
        image.style.height = `${h * s.scale}px`;
        image.style.transform = `translate(${s.x}px, ${s.y}px)`;
        zoom.value = String(s.scale / s.minScale);
      };
      /** 枠の中の点 (cx, cy) を動かさずに倍率を変える */
      const zoomTo = (scale, cx = s.view / 2, cy = s.view / 2) => {
        const next = Math.min(s.minScale * MAX_ZOOM, Math.max(s.minScale, scale));
        s.x = cx - ((cx - s.x) / s.scale) * next;
        s.y = cy - ((cy - s.y) / s.scale) * next;
        s.scale = next;
        draw();
      };

      // 枠の大きさが決まってから、真ん中を枠いっぱいに出す
      const layout = () => {
        const view = stage.getBoundingClientRect().width;
        if (!view) return;
        const ratio = s.view ? view / s.view : 1;
        s.view = view;
        const prevZoom = s.scale / s.minScale;
        s.minScale = view / Math.min(w, h);
        if (ratio === 1 && prevZoom === 1 && s.x === 0 && s.y === 0) {
          s.scale = s.minScale;
          s.x = (view - w * s.scale) / 2;
          s.y = (view - h * s.scale) / 2;
        } else {
          s.scale = s.minScale * prevZoom;
          s.x *= ratio;
          s.y *= ratio;
        }
        draw();
      };
      requestAnimationFrame(layout);
      const observer = new ResizeObserver(layout);
      observer.observe(stage);

      zoom.addEventListener("input", () => zoomTo(s.minScale * Number(zoom.value)));

      stage.addEventListener("wheel", (e) => {
        e.preventDefault();
        const rect = stage.getBoundingClientRect();
        zoomTo(s.scale * Math.exp(-e.deltaY * 0.0015), e.clientX - rect.left, e.clientY - rect.top);
      }, { passive: false });

      // ドラッグ（1本指・マウス）とピンチ（2本指）
      const pointers = new Map();
      let pinch = null;
      stage.addEventListener("pointerdown", (e) => {
        stage.setPointerCapture(e.pointerId);
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pointers.size === 2) {
          const [a, b] = [...pointers.values()];
          pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y), scale: s.scale };
        }
      });
      stage.addEventListener("pointermove", (e) => {
        const prev = pointers.get(e.pointerId);
        if (!prev) return;
        const now = { x: e.clientX, y: e.clientY };
        pointers.set(e.pointerId, now);
        if (pointers.size === 1) {
          s.x += now.x - prev.x;
          s.y += now.y - prev.y;
          draw();
        } else if (pointers.size === 2 && pinch) {
          const [a, b] = [...pointers.values()];
          const rect = stage.getBoundingClientRect();
          const dist = Math.hypot(a.x - b.x, a.y - b.y);
          zoomTo(pinch.scale * (dist / pinch.dist),
                 (a.x + b.x) / 2 - rect.left, (a.y + b.y) / 2 - rect.top);
        }
      });
      const release = (e) => {
        pointers.delete(e.pointerId);
        if (pointers.size < 2) pinch = null;
      };
      stage.addEventListener("pointerup", release);
      stage.addEventListener("pointercancel", release);

      // 「決定」を押した時点の範囲で切り抜く（モーダルが閉じる前に読む）
      root.querySelector('[data-act="ok"]').addEventListener("click", () => {
        observer.disconnect();
        const side = s.view / s.scale;
        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext("2d");
        // 透過 PNG の背景が黒くならないように白で塗っておく
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, size, size);
        ctx.drawImage(img, -s.x / s.scale, -s.y / s.scale, side, side, 0, 0, size, size);
        result = canvas.toDataURL("image/jpeg", quality);
      });
    },
  });

  URL.revokeObjectURL(url);
  return ok ? result : null;
}
