/**
 * charts.js : グラフと可視化パーツをまとめる
 *
 *  - Chart.js を使うもの（棒・円・折れ線）
 *  - Chart.js を使わない自前のもの（円形LoCゲージ＝SVG、スパークライン＝Canvas）
 *
 * Chart.js は index.html で CDN から読み込んでいるので、ここでは window.Chart を使う。
 */

import { cssVar, locColor, locLevelClass, clampLoc, formatDuration } from "./utils.js";

/** Chart.js 全体の既定値（フォントと色をアプリに合わせる） */
function applyDefaults() {
  if (!window.Chart || applyDefaults.done) return;
  const Chart = window.Chart;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 11;
  Chart.defaults.color = cssVar("--c-text-sub") || "#6b7089";
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 10;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  applyDefaults.done = true;
}

/* =========================================================
   円形LoCゲージ（SVG・Chart.js は使わない）
   ========================================================= */

/**
 * ゲージのHTMLを返す。
 * @param {number} value  LoC（0〜100）
 * @param {number} size   直径(px)
 * @param {number} stroke リングの太さ(px)
 */
export function gaugeHtml(value, size = 64, stroke = 6) {
  const v = clampLoc(value);
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const offset = circumference * (1 - v / 100);
  const fontSize = Math.round(size * 0.3);

  return `
    <div class="gauge ${locLevelClass(v)}" data-gauge style="width:${size}px;height:${size}px">
      <svg width="${size}" height="${size}">
        <circle class="gauge__track" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${stroke}"/>
        <circle class="gauge__bar" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${stroke}"
                stroke-dasharray="${circumference.toFixed(2)}"
                stroke-dashoffset="${offset.toFixed(2)}"/>
      </svg>
      <div class="gauge__center">
        <span class="gauge__value loc-color" style="font-size:${fontSize}px" data-gauge-value>${v}</span>
      </div>
    </div>`;
}

/**
 * すでに描かれているゲージの値を書き換える（1秒ごとの更新用）。
 * @param {HTMLElement} gaugeEl gaugeHtml で作った要素
 */
export function updateGauge(gaugeEl, value) {
  if (!gaugeEl) return;
  const v = clampLoc(value);
  const bar = gaugeEl.querySelector(".gauge__bar");
  const label = gaugeEl.querySelector("[data-gauge-value]");

  if (bar) {
    const circumference = Number(bar.getAttribute("stroke-dasharray"));
    bar.setAttribute("stroke-dashoffset", (circumference * (1 - v / 100)).toFixed(2));
  }
  if (label) label.textContent = v;

  // 3段階の色クラスを付け替える
  gaugeEl.classList.remove("is-loc-high", "is-loc-mid", "is-loc-low");
  gaugeEl.classList.add(locLevelClass(v));
}

/* =========================================================
   スパークライン（Canvas に直接描く小さな折れ線）
   ========================================================= */

/**
 * 投稿カードの LoC 推移用。Chart.js を使うには小さすぎるので手で描く。
 * @param {HTMLCanvasElement} canvas
 * @param {number[]} series LoC の並び
 */
export function drawSparkline(canvas, series) {
  if (!canvas || !series || series.length < 2) return;

  // 画面の解像度に合わせてぼやけないようにする
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 200;
  const h = canvas.clientHeight || 28;
  canvas.width = w * dpr;
  canvas.height = h * dpr;

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const pad = 3;
  const toX = (i) => pad + (i / (series.length - 1)) * (w - pad * 2);
  const toY = (v) => h - pad - (clampLoc(v) / 100) * (h - pad * 2);

  // 平均値の色で塗る（カード全体の印象と揃える）
  const color = locColor(series.reduce((a, b) => a + b, 0) / series.length);

  // 下側の塗りつぶし
  ctx.beginPath();
  ctx.moveTo(toX(0), h);
  series.forEach((v, i) => ctx.lineTo(toX(i), toY(v)));
  ctx.lineTo(toX(series.length - 1), h);
  ctx.closePath();
  ctx.fillStyle = color + "26"; // 末尾2桁は不透明度（約15%）
  ctx.fill();

  // 折れ線
  ctx.beginPath();
  series.forEach((v, i) => (i === 0 ? ctx.moveTo(toX(i), toY(v)) : ctx.lineTo(toX(i), toY(v))));
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.8;
  ctx.lineJoin = "round";
  ctx.stroke();
}

/* =========================================================
   Chart.js を使うグラフ
   ========================================================= */

/**
 * 計測中のリアルタイム折れ線グラフ。
 * 作ったあと pushLoc(値) を呼ぶと点が増え、直近 maxPoints 件だけ表示される。
 */
export function createRealtimeLocChart(canvas, { maxPoints = 60 } = {}) {
  applyDefaults();
  const primary = cssVar("--c-primary") || "#5b5bd6";

  const chart = new window.Chart(canvas, {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        data: [],
        borderColor: primary,
        backgroundColor: primary + "22",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.35,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false, // 1秒ごとに更新するのでアニメーションは切る
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        y: { min: 0, max: 100, ticks: { stepSize: 25 }, grid: { color: cssVar("--c-border") } },
        x: { grid: { display: false }, ticks: { maxTicksLimit: 6 } },
      },
    },
  });

  return {
    chart,
    /** 新しい LoC を1点追加する */
    push(loc, label) {
      const d = chart.data;
      d.labels.push(label ?? "");
      d.datasets[0].data.push(clampLoc(loc));
      if (d.labels.length > maxPoints) {
        d.labels.shift();
        d.datasets[0].data.shift();
      }
      chart.update("none");
    },
    destroy() { chart.destroy(); },
  };
}

/** 1週間分の日別・教材別 積み上げ棒グラフ */
export function createStackedBarChart(canvas, { labels, datasets, xLabels }) {
  applyDefaults();

  return new window.Chart(canvas, {
    type: "bar",
    data: {
      labels: xLabels || labels,
      datasets: datasets.map((ds) => ({
        label: ds.label,
        data: ds.data,
        backgroundColor: ds.color,
        borderRadius: 3,
        borderSkipped: false,
        barPercentage: 0.7,
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom" },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${formatDuration(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: {
          stacked: true,
          beginAtZero: true,
          grid: { color: cssVar("--c-border") },
          ticks: { callback: (v) => `${Math.round(v / 60)}h` },
        },
      },
    },
  });
}

/** 教材別の時間配分 円（ドーナツ）グラフ */
export function createDoughnutChart(canvas, items) {
  applyDefaults();

  return new window.Chart(canvas, {
    type: "doughnut",
    data: {
      labels: items.map((i) => i.title),
      datasets: [{
        data: items.map((i) => i.minutes),
        backgroundColor: items.map((i) => i.color),
        borderWidth: 2,
        borderColor: cssVar("--c-surface") || "#fff",
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "58%",
      plugins: {
        legend: { position: "right", labels: { padding: 10 } },
        tooltip: {
          callbacks: { label: (ctx) => `${ctx.label}: ${formatDuration(ctx.parsed)}` },
        },
      },
    },
  });
}

/** 日別の平均LoC 折れ線グラフ（レポート用） */
export function createDailyLocChart(canvas, { labels, values }) {
  applyDefaults();
  const primary = cssVar("--c-primary") || "#5b5bd6";

  return new window.Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "平均LoC",
        data: values,
        borderColor: primary,
        backgroundColor: primary + "1f",
        borderWidth: 2.5,
        tension: 0.35,
        fill: true,
        spanGaps: false, // 記録がない日は線を途切れさせる
        // 点ごとに LoC の3段階の色を付ける
        pointBackgroundColor: values.map((v) => (v == null ? "transparent" : locColor(v))),
        pointBorderColor: cssVar("--c-surface") || "#fff",
        pointBorderWidth: 2,
        pointRadius: 5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: (ctx) => (ctx.parsed.y == null ? "記録なし" : `平均LoC ${ctx.parsed.y}`) },
        },
      },
      scales: {
        y: { min: 0, max: 100, ticks: { stepSize: 25 }, grid: { color: cssVar("--c-border") } },
        x: { grid: { display: false } },
      },
    },
  });
}

/** 今週の目標の達成率を示すドーナツ（中央の数字は HTML 側で重ねる） */
export function createGoalChart(canvas, rate) {
  applyDefaults();
  const value = Math.min(100, Math.max(0, rate));
  const primary = cssVar("--c-primary") || "#5b5bd6";

  return new window.Chart(canvas, {
    type: "doughnut",
    data: {
      labels: ["達成", "残り"],
      datasets: [{
        data: [value, 100 - value],
        backgroundColor: [primary, cssVar("--c-border") || "#e5e7f0"],
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "72%",
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
}
