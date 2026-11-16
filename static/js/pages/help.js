/**
 * pages/help.js : 「？」ページ（#/help?tab=loc|updates）
 *
 *  loc     … LoC（集中度）とは何か、どう測っているか、推定する AI の仕組み（LoC/core.py の LoCModel）
 *  updates … Onsta の更新情報（version.js の RELEASES）。更新のお知らせの通知からここへ来る
 *
 *  AI の説明の数字（768次元・12枚など）は LoC/README の設計に合わせている。モデルを変えたらここも直す。
 */

import { el, esc } from "../utils.js";
import { icon } from "../icons.js";
import { APP_VERSION, RELEASES } from "../version.js";

export const meta = {
  title: "LoC とは？",
  tabs: [
    { key: "loc", label: "LoC のしくみ" },
    { key: "updates", label: "更新情報" },
  ],
};

export async function render({ tab }) {
  return { node: tab === "updates" ? renderUpdates() : renderLoc() };
}

/* ============ LoC のしくみ ============ */

/** 図の1段（番号・名前・説明・出力の大きさ） */
function stage(num, name, desc, out, kind = "") {
  return `
    <div class="arch__box ${kind ? `arch__box--${kind}` : ""}">
      <div class="arch__num">${num}</div>
      <div class="arch__text">
        <div class="arch__name">${name}</div>
        <div class="arch__desc">${desc}</div>
      </div>
      ${out ? `<div class="arch__out">${out}</div>` : ""}
    </div>`;
}

const arrow = `<div class="arch__arrow" aria-hidden="true">↓</div>`;

function renderLoc() {
  return el(`
    <div class="help">
      <section class="card help__hero">
        <div class="help__hero-icon">${icon("loc", 30)}</div>
        <div>
          <h2 class="help__title">LoC（Level of Concentration）</h2>
          <p class="u-muted">
            勉強中の手元をカメラで写し、AI が<b>集中度を 0〜100 の数値</b>で推定したものです。
            「LoC計測」で教材を選んで計測を始めると、約5秒ごとに更新されます。
          </p>
        </div>
      </section>

      <section class="card">
        <h2 class="card__title">数値の目安</h2>
        <div class="loc-scale">
          <div class="loc-scale__item is-loc-high"><b>70〜100</b><span>集中している</span></div>
          <div class="loc-scale__item is-loc-mid"><b>40〜69</b><span>ふつう</span></div>
          <div class="loc-scale__item is-loc-low"><b>0〜39</b><span>集中が切れている</span></div>
        </div>
        <p class="u-small u-muted" style="margin-top:10px">
          ペンを動かして書いている・教材に向かっているほど高く、スマホを触っている・手が止まっている・
          席を離れているほど低くなります。
        </p>
      </section>

      <section class="card">
        <h2 class="card__title">計測の流れ</h2>
        <ol class="help__steps">
          <li><b>5秒ごとに1枚</b>、カメラの映像から画像を取り出して推論サーバーへ送ります。</li>
          <li>直近 <b>12枚（約60秒ぶん）</b>がそろうと最初の LoC が出ます（それまでは「準備中」）。</li>
          <li>その後は新しい1枚が届くたびに、直近60秒の様子から LoC を出し直します。</li>
          <li>直近60秒ずっと人が写っていないときは、AI を通さずに LoC を 0 にします。</li>
        </ol>
      </section>

      <section class="card">
        <h2 class="card__title">LoC を推定する AI のしくみ</h2>
        <p class="u-small u-muted" style="margin-bottom:12px">
          画像から「何が写っているか」と「手がどう動いているか」を別々に取り出し、
          時間の流れをまとめてから1つの数値にします。
        </p>
        <div class="arch">
          <div class="arch__input">${icon("camera", 18)}カメラの画像（5秒ごと・直近12枚）</div>
          ${arrow}
          <div class="arch__split">
            <div class="arch__col">
              ${stage("①", "ConvNeXt-Tiny", "画像を見て「何が写っているか」（教材・ペン・スマホなど）を数値の列にする画像認識 AI。大量の画像で学習済みのものをそのまま使う", "768次元", "img")}
              ${arrow}
              ${stage("③", "射影 MLP（画像）", "LoC の推定に役立つ形に圧縮する", "128次元", "img")}
            </div>
            <div class="arch__col">
              ${stage("②", "MediaPipe Hands", "両手の21個の関節の位置・手の大きさ・前の画像からの指先の動きを取り出す", "162次元", "hand")}
              ${arrow}
              ${stage("④", "射影 MLP（手）", "指の開き具合や手の配置の関係をとらえて圧縮する", "64次元", "hand")}
            </div>
          </div>
          ${arrow}
          ${stage("⑤", "結合", "画像と手の情報を1つにまとめる（1枚につき）", "192次元")}
          ${arrow}
          ${stage("⑥", "LSTM", "12枚ぶんを古い順に読み、「ずっと書き続けている」「途中でスマホを触った」といった<b>時間の流れ</b>をとらえる", "256次元", "time")}
          ${arrow}
          ${stage("⑦", "MLP ヘッド", "まとめた情報から集中度を1つの数値にする", "1つの値")}
          ${arrow}
          ${stage("⑧", "0〜100 に収める", "シグモイド関数 × 100 で、必ず 0〜100 の範囲にする", "LoC", "out")}
        </div>
      </section>

      <section class="card">
        <h2 class="card__title">AI の学習のしかた</h2>
        <p class="u-small u-muted">
          勉強している様子の動画を5秒ごとの画像に切り出し、画像を理解できる別の AI（Qwen3.5）に
          「どのくらい集中して見えるか（5段階）」と「ペン先が紙に触れているか」「スマホを手に持っているか」などの
          見て分かる事実を判定させて、正解の LoC を作りました。①②で取り出した情報からこの正解に近い値を出せるよう、
          ③〜⑦の部分（約60万個のパラメータ）を学習させています。
        </p>
      </section>

      <section class="card">
        <h2 class="card__title">計測のコツと注意</h2>
        <ul class="help__list">
          <li>カメラは<b>机の真上〜斜め上から手元が写る</b>向きにしてください。AI はその向きの映像で学習しています。</li>
          <li>LoC が 15 未満の状態が5分、または 0 の状態が1分続くと、計測を自動で一時停止します。戻れば再開します。</li>
          <li>計測中にほかの画面へ移るとLoC計測は中断されます（移る前に確認が出ます）。記録するときは「終了して記録」を押してください。</li>
          <li>計測中の映像は推論サーバーへ送られ、集中度推定の改善のために録画・保存されます。第三者には提供しません。</li>
          <li>LoC は AI による推定です。実際の集中度とずれることがあります。</li>
        </ul>
      </section>
    </div>
  `);
}

/* ============ 更新情報 ============ */

function renderUpdates() {
  return el(`
    <div class="help">
      ${RELEASES.map((r) => `
        <section class="card release ${r.version === APP_VERSION ? "release--current" : ""}">
          <div class="release__head">
            <h2 class="release__title">Onsta ver${esc(r.version)}</h2>
            ${r.version === APP_VERSION ? '<span class="chip">いまの版</span>' : ""}
            <span class="u-small u-weak">${esc(r.date)}</span>
          </div>
          <ul class="help__list">
            ${r.items.map((item) => `<li>${esc(item)}</li>`).join("")}
          </ul>
        </section>`).join("")}
    </div>
  `);
}
