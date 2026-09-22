/**
 * pages/report.js : レポート（#/report）
 *
 *  タブ「記録」… 学習時間のサマリー、週間の積み上げ棒、教材別の円、日別の平均LoC
 *  タブ「自分」… カウントダウンと今週の目標
 */

import { el, esc, formatDuration, shortDateLabel, weekdayLabel, daysUntil, clampLoc } from "../utils.js";
import { icon } from "../icons.js";
import {
  createStackedBarChart,
  createDoughnutChart,
  createDailyLocChart,
  createGoalChart,
} from "../charts.js";
import * as store from "../store.js";

export const meta = {
  title: "レポート",
  tabs: [
    { key: "records", label: "記録" },
    { key: "me", label: "自分" },
  ],
};

export async function render(ctx) {
  const charts = [];
  const node = ctx.tab === "me" ? renderMeTab(charts) : renderRecordsTab(charts);

  return {
    node,
    // Chart.js のインスタンスはページを離れるときに必ず破棄する
    destroy() { charts.forEach((c) => c?.destroy?.()); },
  };
}

/* =========================================================
   タブ「記録」
   ========================================================= */

function renderRecordsTab(charts) {
  const summary = store.getSummary();
  const weekly = store.getWeeklyByMaterial(7);
  const breakdown = store.getMaterialBreakdown(7);
  const dailyLoc = store.getDailyAvgLoc(7);

  const node = el(`
    <div>
      <!-- サマリー -->
      <section class="grid-3" style="margin-bottom:18px">
        ${statCard("今日", summary.todayMin)}
        ${statCard("今月", summary.monthMin)}
        ${statCard("総学習時間", summary.totalMin)}
      </section>

      <!-- 週間の積み上げ棒グラフ -->
      <section class="card">
        <h2 class="card__title">今週の学習時間（教材別）</h2>
        <div class="chart-box"><canvas data-chart="weekly"></canvas></div>
      </section>

      <!-- 教材別の時間配分 -->
      <section class="card">
        <h2 class="card__title">教材別の時間配分（直近7日）</h2>
        <div class="chart-box"><canvas data-chart="breakdown"></canvas></div>
      </section>

      <!-- 日別の平均LoC -->
      <section class="card">
        <div class="u-spread" style="margin-bottom:10px">
          <h2 class="card__title" style="margin:0">日別の平均LoC</h2>
          <span class="u-small u-weak">学習時間で重み付けした平均</span>
        </div>
        <div class="chart-box chart-box--sm"><canvas data-chart="dailyLoc"></canvas></div>
        <p class="u-small u-weak" style="margin-top:6px">
          記録がない日はグラフが途切れます。
        </p>
      </section>
    </div>
  `);

  // Chart.js はキャンバスが DOM に入ってからサイズを測るので次フレームで作る
  requestAnimationFrame(() => {
    if (weekly.datasets.length) {
      charts.push(createStackedBarChart(node.querySelector('[data-chart="weekly"]'), {
        labels: weekly.labels,
        // 軸ラベルは "9/22(月)" の形にする
        xLabels: weekly.labels.map((k) => `${shortDateLabel(k)}(${weekdayLabel(k)})`),
        datasets: weekly.datasets,
      }));
    } else {
      replaceWithEmpty(node, "weekly", "今週の記録がまだありません");
    }

    if (breakdown.length) {
      charts.push(createDoughnutChart(node.querySelector('[data-chart="breakdown"]'), breakdown));
    } else {
      replaceWithEmpty(node, "breakdown", "直近7日の記録がまだありません");
    }

    charts.push(createDailyLocChart(node.querySelector('[data-chart="dailyLoc"]'), {
      labels: dailyLoc.labels.map((k) => `${shortDateLabel(k)}(${weekdayLabel(k)})`),
      values: dailyLoc.values,
    }));
  });

  return node;
}

/** サマリーカード1枚ぶんの HTML */
function statCard(label, minutes) {
  return `
    <div class="stat">
      <div class="stat__label">${esc(label)}</div>
      <div class="stat__value tabular">${formatDuration(minutes)}</div>
    </div>`;
}

/** データが無いときにグラフの代わりに出す表示 */
function replaceWithEmpty(node, chartKey, message) {
  const canvas = node.querySelector(`[data-chart="${chartKey}"]`);
  const box = canvas?.closest(".chart-box");
  if (box) box.innerHTML = `<div class="empty">${esc(message)}</div>`;
}

/* =========================================================
   タブ「自分」
   ========================================================= */

function renderMeTab(charts) {
  const me = store.getMe();
  const goal = store.getWeeklyGoal();
  const summary = store.getSummary();
  const rest = daysUntil(me.examDate);
  const dailyLoc = store.getDailyAvgLoc(7);

  // 直近7日で記録がある日だけを使って、平均LoC を1つの数字にまとめる
  const locValues = dailyLoc.values.filter((v) => typeof v === "number");
  const avgLoc = locValues.length
    ? clampLoc(locValues.reduce((a, b) => a + b, 0) / locValues.length)
    : null;

  const node = el(`
    <div>
      <!-- カウントダウン -->
      <section class="countdown" style="margin-bottom:14px">
        <div>
          <div class="countdown__label">${esc(me.examName)}まで</div>
          <div class="u-small" style="opacity:.8">${esc(me.examDate)}</div>
        </div>
        <div class="u-center">
          <div class="countdown__num tabular">${rest}</div>
          <div class="countdown__label">日</div>
        </div>
      </section>

      <!-- 今週の目標 -->
      <section class="card">
        <h2 class="card__title">今週の目標</h2>
        <div class="u-row" style="gap:18px">
          <div style="position:relative;width:120px;height:120px;flex:0 0 auto">
            <canvas data-chart="goal"></canvas>
            <div style="position:absolute;inset:0;display:grid;place-items:center;line-height:1.2">
              <div class="u-center">
                <div style="font-size:24px;font-weight:800">${goal.rate}<span style="font-size:13px">%</span></div>
                <div class="u-small u-weak">達成</div>
              </div>
            </div>
          </div>
          <div>
            <div class="u-small u-muted">目標</div>
            <div class="u-bold">週 ${formatDuration(goal.goalMin)}</div>
            <div class="u-small u-muted" style="margin-top:8px">実績</div>
            <div class="u-bold">${formatDuration(goal.doneMin)}</div>
            <div class="u-small u-weak" style="margin-top:8px">
              残り ${formatDuration(Math.max(0, goal.goalMin - goal.doneMin))}
            </div>
          </div>
        </div>
      </section>

      <!-- 直近の調子 -->
      <section class="card">
        <h2 class="card__title">直近7日の調子</h2>
        <div class="grid-3">
          <div class="stat">
            <div class="stat__label">平均LoC</div>
            <div class="stat__value tabular">${avgLoc ?? "--"}</div>
          </div>
          <div class="stat">
            <div class="stat__label">記録の合計</div>
            <div class="stat__value tabular">${summary.recordCount}<span class="stat__unit">回</span></div>
          </div>
          <div class="stat">
            <div class="stat__label">登録教材</div>
            <div class="stat__value tabular">${store.getMaterials().length}<span class="stat__unit">冊</span></div>
          </div>
        </div>
      </section>

      <!-- プロフィールへの導線 -->
      <section class="card">
        <div class="u-spread">
          <div class="u-row">
            <div class="avatar" style="background:${me.avatarColor}">${esc(me.initial)}</div>
            <div>
              <div class="u-bold">${esc(me.name)}</div>
              <div class="u-small u-weak">${esc(me.grade)}／${esc(me.prefecture)}</div>
            </div>
          </div>
          <a class="btn btn--ghost btn--sm" href="#/profile">${icon("user", 16)}プロフィール</a>
        </div>
      </section>
    </div>
  `);

  requestAnimationFrame(() => {
    charts.push(createGoalChart(node.querySelector('[data-chart="goal"]'), goal.rate));
  });

  return node;
}
