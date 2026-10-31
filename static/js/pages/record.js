/**
 * pages/record.js : 記録する（#/record, #/record/add, #/record/<教材ID>）
 *
 *  #/record            … カテゴリごとの教材カード一覧＋教材の追加・新規記録ボタン
 *  #/record/add        … 教材を追加（バーコード／独自教材。pages/materialAdd.js）
 *  #/record/<教材ID>   … LoC計測モード（カメラ＋リアルタイムLoC）と手動記録
 *
 *  教材は最初は1冊もなく、ユーザーが自分で追加する。
 */

import { el, esc, formatClock, formatDuration, clampLoc, locLevelClass, toast, openModal, average, thumbHtml } from "../utils.js";
import { icon } from "../icons.js";
import { createRealtimeLocChart } from "../charts.js";
import { LocClient, LOC_CONFIG, LOC_STATUS, statusLabel } from "../locClient.js";
import * as store from "../store.js";
import { renderAdd } from "./materialAdd.js";

export const meta = { title: "記録する" };

/* LoC が低い状態が続いたときの自動一時停止（継続時間を 0 に戻して止める） */
const LOW_LOC = 15;              // これを下回ると「集中していない」とみなす
const LOW_LIMIT_SEC = 5 * 60;    // その状態がこれだけ続いたら一時停止する
const ZERO_LIMIT_SEC = 60;       // LoC が 0（席を離れているなど）のままこれだけ続いたら一時停止する
const RESUME_LOC = 30;           // 一時停止後、これより上に戻ったら 0 から数え直す

/* 自動一時停止の理由ごとの案内 */
const AUTO_PAUSE_MESSAGE = {
  zero: `LoCが0の状態が${ZERO_LIMIT_SEC / 60}分続いたため、計測を一時停止しています。`,
  low: `LoCが${LOW_LOC}未満の状態が${LOW_LIMIT_SEC / 60}分続いたため、計測を一時停止しています。`,
};

export async function render(ctx) {
  // add なら教材の追加、教材IDが付いていれば計測画面、なければ一覧画面
  if (ctx.param === "add") return renderAdd(ctx);
  return ctx.param ? renderMeasure(ctx) : renderList(ctx);
}

/* =========================================================
   1. 教材一覧
   ========================================================= */

function renderList(ctx) {
  const groups = store.getMaterialsBySubject();
  const count = store.getMaterials().length;

  const node = el(`
    <div>
      <div class="u-spread" style="margin-bottom:14px">
        <span class="u-small u-weak">${count ? `${count}冊の教材` : ""}</span>
        <a class="btn btn--ghost btn--sm" href="#/record/add">${icon("plus", 16)}教材を追加</a>
      </div>
    </div>
  `);

  // 教材がまだ無いときは追加の案内だけを出す
  if (count === 0) {
    node.appendChild(el(`
      <section class="card empty-state">
        <div class="empty-state__icon">${icon("book", 30)}</div>
        <h2 class="empty-state__title">まだ教材がありません</h2>
        <p class="u-muted">使っている参考書や問題集を追加して、教材ごとに学習を記録しましょう。</p>
        <div class="empty-state__actions">
          <a class="btn" href="#/record/add">${icon("camera", 18)}バーコードで追加</a>
          <a class="btn btn--ghost" href="#/record/add?tab=custom">${icon("book", 18)}独自教材を追加</a>
        </div>
      </section>
    `));
    return { node };
  }

  groups.forEach((group) => {
    const section = el(`
      <section class="section">
        <div class="section__head">
          <span class="subject-chip" style="--chip-color:${group.color}">${esc(group.name)}</span>
          <span class="u-small u-weak">${group.materials.length}冊</span>
        </div>
        <div class="grid-auto"></div>
      </section>
    `);

    const grid = section.querySelector(".grid-auto");
    group.materials.forEach((m) => {
      grid.appendChild(el(`
        <a class="material-card" href="#/record/${m.id}">
          ${thumbHtml(m, "thumb--sm")}
          <div>
            <div class="material-card__title">${esc(m.title)}</div>
            <div class="material-card__meta">${esc(group.name)}</div>
          </div>
        </a>
      `));
    });

    node.appendChild(section);
  });

  // 右下のフローティングボタン（教材を選ばずに手入力で記録する）
  const fab = el(`<button class="fab" type="button">${icon("plus", 20)}新規記録</button>`);
  fab.addEventListener("click", () => openManualRecordModal());
  node.appendChild(fab);

  return { node };
}

/**
 * 手動で学習記録を追加するモーダル。
 * カメラが使えない環境でも記録できるようにするための入口でもある。
 */
async function openManualRecordModal(defaultMaterialId = null) {
  const materials = store.getMaterials();

  const bodyHtml = `
    <div class="field">
      <label class="field__label" for="mr-material">教材</label>
      <select class="select" id="mr-material">
        ${materials.map((m) => `
          <option value="${m.id}" ${m.id === defaultMaterialId ? "selected" : ""}>
            ${esc(m.subjectName)}／${esc(m.title)}
          </option>`).join("")}
      </select>
    </div>
    <div class="field">
      <label class="field__label" for="mr-minutes">学習時間（分）</label>
      <input class="input" id="mr-minutes" type="number" min="1" max="1440" value="30">
    </div>
    <div class="field">
      <label class="field__label" for="mr-memo">メモ（任意）</label>
      <textarea class="textarea" id="mr-memo" placeholder="今日やったこと、気づいたことなど"></textarea>
    </div>
    <p class="u-small u-weak">この記録には LoC は付きません（カメラ計測なしのため）。</p>
  `;

  let values = null;
  const ok = await openModal({
    title: "学習記録を追加",
    bodyHtml,
    okLabel: "保存する",
    onMount(root) {
      // OK を押した時点の入力値を拾えるよう、閉じる前に読み取っておく
      root.querySelector('[data-act="ok"]').addEventListener("click", () => {
        values = {
          materialId: root.querySelector("#mr-material").value,
          durationMin: Number(root.querySelector("#mr-minutes").value),
          memo: root.querySelector("#mr-memo").value.trim(),
        };
      });
    },
  });

  if (!ok || !values || !values.durationMin) return;
  store.addRecord({ ...values, source: "manual" });
  toast("記録を保存しました");
  location.hash = "#/timeline";
}

/* =========================================================
   2. 計測画面
   ========================================================= */

function renderMeasure(ctx) {
  const material = store.getMaterial(ctx.param);

  // 消した教材や他の人の教材の URL を開いたとき
  if (material.missing || !store.getMaterials().some((m) => m.id === material.id)) {
    return {
      node: el(`
        <div>
          <a class="u-small u-muted" href="#/record">&larr; 教材一覧にもどる</a>
          <div class="empty">この教材は見つかりませんでした</div>
        </div>`),
    };
  }

  const node = el(`
    <div>
      <a class="u-small u-muted" href="#/record">&larr; 教材一覧にもどる</a>

      <!-- 教材の情報 -->
      <section class="card" style="margin-top:10px">
        <div class="u-row" style="gap:12px">
          ${thumbHtml(material, "thumb--sm")}
          <div style="flex:1;min-width:0">
            <div class="u-bold">${esc(material.title)}</div>
            <div class="u-small u-weak">${esc(material.subjectName)}${material.isbn ? `　ISBN ${esc(material.isbn)}` : ""}</div>
          </div>
          <button class="btn btn--ghost btn--sm" type="button" data-act="delete-material">削除</button>
        </div>
      </section>

      <!-- 開始前：カメラ利用の説明と同意 -->
      <section class="card" data-view="intro">
        <h2 class="card__title">LoC計測モード</h2>
        <p class="u-small u-muted" style="margin-bottom:10px">
          Webカメラで手の動きをとらえ、集中度（LoC：0〜100）をリアルタイムに推定します。
        </p>
        <div class="notice" style="margin-bottom:12px">
          <strong>映像は保存されません。</strong><br>
          カメラの映像はこの端末の中で処理され、推定に必要な情報だけが使われます。
          録画・アップロード・第三者への提供は行いません。計測はいつでも停止できます。
        </div>
        <label class="checkbox" style="margin-bottom:12px">
          <input type="checkbox" data-consent>
          <span>上記の内容を理解し、カメラの使用に同意します</span>
        </label>
        <div class="u-row" style="flex-wrap:wrap">
          <button class="btn" type="button" data-act="start" disabled>${icon("camera", 18)}LoC計測を開始</button>
          <button class="btn btn--ghost" type="button" data-act="manual">${icon("clock", 18)}カメラを使わず記録</button>
        </div>
        <div data-intro-error style="margin-top:12px"></div>
      </section>

      <!-- 計測中：カメラ映像の上に継続時間・LoC・推移を重ねる -->
      <section class="card u-hidden" data-view="measure">
        <h2 class="card__title">計測中</h2>

        <div class="stage" data-camera>
          <video playsinline muted autoplay data-video></video>
          <div class="stage__off u-hidden" data-camera-off>プレビュー非表示中</div>

          <div class="stage__top">
            <div>
              <div class="stage__label">継続時間</div>
              <div class="stage__timer tabular" data-timer>00:00:00</div>
              <div class="stage__sub tabular">合計 <span data-total>00:00:00</span></div>
            </div>
            <span class="status" data-status>待機中</span>
          </div>

          <div class="stage__bottom">
            <div class="stage__chart"><canvas data-loc-chart></canvas></div>
            <div class="stage__loc">
              <div class="stage__label">現在のLoC</div>
              <div class="stage__loc-num loc-color is-loc-mid tabular" data-loc-num>--</div>
              <div class="stage__sub u-hidden" data-loc-warmup></div>
            </div>
          </div>
        </div>

        <div class="u-row" style="margin-top:12px;flex-wrap:wrap">
          <button class="btn btn--ghost" type="button" data-act="pause">${icon("pause", 18)}一時停止</button>
          <button class="btn btn--danger" type="button" data-act="finish">${icon("stop", 18)}終了して記録</button>
          <button class="btn btn--ghost btn--sm" type="button" data-act="toggle-preview" style="margin-left:auto">
            ${icon("eyeOff", 16)}<span>プレビューを隠す</span>
          </button>
        </div>
        <div class="notice notice--warn u-hidden" data-auto-paused style="margin-top:12px"></div>
        <div data-measure-error style="margin-top:12px"></div>
      </section>
    </div>
  `);

  /* ---- 画面の状態 ---- */
  const state = {
    elapsedSec: 0,      // 継続時間。一時停止中は増えず、自動一時停止でリセットされる
    totalSec: 0,        // 記録する学習時間。一時停止中は増えない
    paused: false,      // 手動の一時停止（終了確認中も含む）
    autoPaused: false,  // LoC が低い／0 の状態が続いたことによる一時停止
    lastLoc: null,      // 直近に受け取った LoC
    lowSec: 0,          // LoC が LOW_LOC 未満のまま経過した秒数
    zeroSec: 0,         // LoC が 0 のまま経過した秒数
    running: false,
    locSamples: [],     // 受け取った LoC をすべて残し、終了時の平均に使う
    stream: null,       // カメラの MediaStream
    client: null,       // LocClient
    chart: null,        // リアルタイム折れ線
    timer: null,        // 1秒ごとのタイマー
  };

  /* ---- 要素の取り出し ---- */
  const q = (sel) => node.querySelector(sel);
  const introView = q('[data-view="intro"]');
  const measureView = q('[data-view="measure"]');
  const video = q("[data-video]");
  const locNum = q("[data-loc-num]");
  const timerEl = q("[data-timer]");
  const statusEl = q("[data-status]");
  const warmupEl = q("[data-loc-warmup]");
  const totalEl = q("[data-total]");
  const autoPausedEl = q("[data-auto-paused]");

  /* ---- 同意チェックで開始ボタンを有効化 ---- */
  const consent = q("[data-consent]");
  const startBtn = q('[data-act="start"]');
  consent.addEventListener("change", () => { startBtn.disabled = !consent.checked; });

  /* ---- カメラを使わずに記録 ---- */
  q('[data-act="manual"]').addEventListener("click", () => openManualRecordModal(material.id));

  /* ---- 教材の削除（学習記録は残し、教材名は「削除した教材」になる） ---- */
  q('[data-act="delete-material"]').addEventListener("click", async () => {
    const ok = await openModal({
      title: "この教材を削除しますか？",
      okLabel: "削除する",
      bodyHtml: `<p class="u-muted">「${esc(material.title)}」を教材一覧から削除します。
        これまでの学習記録は残りますが、教材名は「削除した教材」と表示されます。</p>`,
    });
    if (!ok) return;
    try {
      await store.deleteMaterial(material.id);
      toast("教材を削除しました");
      location.hash = "#/record";
    } catch (err) {
      toast(err.message);
    }
  });

  /* ---- 計測開始 ---- */
  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;

    // 1) カメラを起動する。失敗したら手動記録にフォールバックする。
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      video.srcObject = state.stream;
    } catch (err) {
      showIntroError(err);
      startBtn.disabled = false;
      return;
    }

    // 2) 画面を計測中に切り替える
    introView.classList.add("u-hidden");
    measureView.classList.remove("u-hidden");
    state.running = true;

    // 3) リアルタイムグラフを用意する（直近5分ぶん＝1秒×300点）
    state.chart = createRealtimeLocChart(q("[data-loc-chart]"), { maxPoints: 300, overlay: true });

    // 4) LoC の取得を開始する（モック／本番は locClient.js の LOC_CONFIG.mode で決まる）
    state.client = new LocClient({
      onLoc: ({ loc }) => {
        if (state.paused) return;
        const v = clampLoc(loc);
        state.lastLoc = v;
        warmupEl.classList.add("u-hidden");

        // 自動一時停止中も LoC は受け取り続け、戻ったら再開する
        if (state.autoPaused && v > RESUME_LOC) setAutoPaused(false);
        // 一時停止中の LoC は学習中のものではないので平均には入れない
        if (!state.autoPaused) state.locSamples.push(v);

        locNum.textContent = v;
        locNum.className = `stage__loc-num loc-color tabular ${locLevelClass(v)}`;
        state.chart.push(v, formatClock(state.totalSec));
      },
      onStatus: (status, detail) => updateStatus(status, detail),
      // 推論サーバーは直近の映像が一定量たまるまで LoC を出せないので、その間の進み具合を出す
      onWarmup: ({ remaining, total, seconds }) => {
        if (remaining <= 0) return;
        warmupEl.textContent = `準備中 ${total - remaining}/${total}（最初のLoCまで約${seconds}秒）`;
        warmupEl.classList.remove("u-hidden");
      },
    });
    state.client.start({ video });

    // 5) 経過時間のカウント
    state.timer = setInterval(() => {
      if (state.paused || state.autoPaused) return;

      // LoC が 0 または低いまま一定時間たったら、継続時間をリセットして一時停止する
      const loc = state.lastLoc;
      state.zeroSec = loc === 0 ? state.zeroSec + 1 : 0;
      state.lowSec = loc !== null && loc < LOW_LOC ? state.lowSec + 1 : 0;
      if (state.zeroSec >= ZERO_LIMIT_SEC) {
        setAutoPaused(true, "zero");
        return;
      }
      if (state.lowSec >= LOW_LIMIT_SEC) {
        setAutoPaused(true, "low");
        return;
      }

      state.elapsedSec += 1;
      state.totalSec += 1;
      renderTimers();
    }, 1000);
  });

  /** LoC による自動一時停止の切り替え（reason: "zero" | "low"） */
  function setAutoPaused(on, reason) {
    state.autoPaused = on;
    state.lowSec = 0;
    state.zeroSec = 0;
    if (on) {
      state.elapsedSec = 0;
      autoPausedEl.innerHTML = `${AUTO_PAUSE_MESSAGE[reason]}<br>
        LoCが${RESUME_LOC}より上に戻ると、継続時間を0から数え直します。`;
    }
    autoPausedEl.classList.toggle("u-hidden", !on);
    renderTimers();
  }

  function renderTimers() {
    timerEl.textContent = formatClock(state.elapsedSec);
    totalEl.textContent = formatClock(state.totalSec);
  }

  /* ---- 一時停止・再開 ---- */
  const pauseBtn = q('[data-act="pause"]');
  pauseBtn.addEventListener("click", () => {
    state.paused = !state.paused;
    if (state.paused) {
      state.client?.pause();
      pauseBtn.innerHTML = `${icon("play", 18)}再開`;
    } else {
      state.client?.resume();
      pauseBtn.innerHTML = `${icon("pause", 18)}一時停止`;
    }
  });

  /* ---- プレビューの表示切り替え ---- */
  const previewBtn = q('[data-act="toggle-preview"]');
  previewBtn.addEventListener("click", () => {
    // 映像自体は止めない（止めると LoC が取れなくなるため）。見た目だけ隠す。
    const off = q("[data-camera-off]");
    const hidden = off.classList.toggle("u-hidden") === false;
    previewBtn.innerHTML = hidden
      ? `${icon("eye", 16)}<span>プレビューを表示</span>`
      : `${icon("eyeOff", 16)}<span>プレビューを隠す</span>`;
  });

  /* ---- 終了して記録 ---- */
  q('[data-act="finish"]').addEventListener("click", async () => {
    state.paused = true;
    state.client?.pause();

    const durationMin = Math.max(1, Math.round(state.totalSec / 60));
    const avgLoc = state.locSamples.length ? Math.round(average(state.locSamples)) : null;

    const ok = await openModal({
      title: "この内容で記録しますか？",
      okLabel: "記録する",
      bodyHtml: `
        <div class="info-list" style="margin-bottom:12px">
          <div class="info-list__row"><span class="info-list__key">教材</span><span>${esc(material.title)}</span></div>
          <div class="info-list__row"><span class="info-list__key">学習時間</span><span>${formatDuration(durationMin)}</span></div>
          <div class="info-list__row"><span class="info-list__key">平均LoC</span><span>${avgLoc ?? "計測なし"}</span></div>
        </div>
        <div class="field">
          <label class="field__label" for="fin-memo">メモ（任意）</label>
          <textarea class="textarea" id="fin-memo" placeholder="今日やったこと、気づいたことなど"></textarea>
        </div>`,
      onMount(root) {
        root.querySelector('[data-act="ok"]').addEventListener("click", () => {
          state.memo = root.querySelector("#fin-memo").value.trim();
        });
      },
    });

    if (!ok) {
      // キャンセルしたら計測を続ける
      state.paused = false;
      state.client?.resume();
      return;
    }

    store.addRecord({
      materialId: material.id,
      durationMin,
      memo: state.memo || "",
      // グラフが重くならないよう、保存する LoC は最大60点に間引く
      locSeries: thin(state.locSamples, 60),
      source: "camera",
    });

    cleanup();
    toast("記録を保存しました");
    location.hash = "#/timeline";
  });

  /* ---- 表示の更新まわり ---- */

  function updateStatus(status, detail) {
    statusEl.textContent = statusLabel(status);
    statusEl.className = "status";
    if (status === LOC_STATUS.LIVE) statusEl.classList.add("status--live");
    if (status === LOC_STATUS.MOCK) statusEl.classList.add("status--mock");
    if (status === LOC_STATUS.ERROR) statusEl.classList.add("status--error");
    if (status !== LOC_STATUS.LIVE) warmupEl.classList.add("u-hidden");

    const box = q("[data-measure-error]");
    if (status === LOC_STATUS.ERROR) {
      box.innerHTML = `
        <div class="notice notice--error">
          推論サーバー（${esc(LOC_CONFIG.url)}）に接続できませんでした。<br>
          ${esc(detail || "")}
          ${LOC_CONFIG.autoFallbackToMock ? "<br>モックのLoCに切り替えて計測を続けます。" : ""}
        </div>`;
      // 自動で切り替えない設定のときは、手動で切り替えるボタンを出す
      if (!LOC_CONFIG.autoFallbackToMock) {
        const btn = el(`<button class="btn btn--sm" type="button" style="margin-top:8px">モックで続ける</button>`);
        btn.addEventListener("click", () => state.client?.switchToMock());
        box.appendChild(btn);
      }
    } else if (status === LOC_STATUS.MOCK || status === LOC_STATUS.LIVE) {
      // 復帰したらエラー表示は数秒で消す
      setTimeout(() => { if (state.running) box.innerHTML = ""; }, 4000);
    }
  }

  /** カメラが使えなかったときの案内 */
  function showIntroError(err) {
    const reason = {
      NotAllowedError: "カメラの使用が許可されませんでした。",
      NotFoundError: "カメラが見つかりませんでした。",
      NotReadableError: "カメラが他のアプリで使用中の可能性があります。",
    }[err.name] || `カメラを起動できませんでした（${err.name}）。`;

    const box = q("[data-intro-error]");
    box.innerHTML = `
      <div class="notice notice--warn">
        ${esc(reason)}<br>
        LoCなしの通常記録に切り替えて、学習時間を手入力できます。
      </div>`;
    const btn = el(`<button class="btn btn--sm" type="button" style="margin-top:8px">手入力で記録する</button>`);
    btn.addEventListener("click", () => openManualRecordModal(material.id));
    box.appendChild(btn);
  }

  /** タイマー・カメラ・WebSocket をすべて止める */
  function cleanup() {
    state.running = false;
    clearInterval(state.timer);
    state.client?.stop();
    state.chart?.destroy();
    state.stream?.getTracks().forEach((track) => track.stop());
    state.stream = null;
  }

  return { node, destroy: cleanup };
}

/**
 * 配列を最大 max 個に間引く（等間隔に抜き出す）。
 * 例: 600点の計測結果 -> 60点のスパークライン用データ
 */
function thin(values, max) {
  if (values.length <= max) return values;
  const step = values.length / max;
  const out = [];
  for (let i = 0; i < max; i++) out.push(values[Math.floor(i * step)]);
  return out;
}
