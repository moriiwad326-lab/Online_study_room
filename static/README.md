# Onsta フロントエンド

受験生向け学習記録アプリ **Onsta（オンスタ）** の Web フロントエンドです。

Onsta の特徴は、勉強中の手の動きをカメラでとらえて
**LoC（Level of Concentration：集中度、0〜100）** を推定し、リアルタイムに表示すること。
このリポジトリはフロントエンドだけで、LoC を推定するモデルは別（`../LoC/`）で動かす想定です。
`../LoC/server.py` を起動すると学習済みモデルの LoC が、起動していなければモックの LoC が表示されます。

- HTML / CSS / Vanilla JavaScript のみ（React などのフレームワーク・ビルドツールなし）
- グラフ描画のみ [Chart.js](https://www.chartjs.org/) を CDN から読み込み
- ハッシュベースの SPA（`#/timeline` など）
- データはモック JSON ＋ `localStorage`

---

## 起動方法（Windows / PowerShell）

カメラ（`getUserMedia`）はセキュアなオリジンでしか使えないため、
**ファイルを直接開くのではなく `localhost` で動かしてください。**

**学習済みモデルで LoC を出す場合**（推奨）。推論サーバーがこのフロントも配信します。

```powershell
cd C:\Users\morii\develop\Online_Study_Room
LoC\.venv\Scripts\python.exe LoC\server.py
```

ブラウザで <http://localhost:8000/> を開き、「記録する」→ 教材 → 「LoC計測を開始」。
最初の LoC が出るまで約60秒かかります（その間は「準備中 n/12」と表示）。
カメラは学習データと同じく**机の真上〜斜め上から手元を写す**向きにしてください。

**フロントだけ動かす場合**（推論サーバーなし。接続に失敗して自動でモックに切り替わる）

```powershell
cd C:\Users\morii\develop\Online_Study_Room\static
python -m http.server 8000
```

ブラウザで <http://localhost:8000/> を開きます。

> `index.html` をダブルクリックで開くと `data/mock.json` の読み込み（fetch）が
> ブラウザにブロックされ、エラー画面が出ます。必ず HTTP サーバー越しに開いてください。

Python が無い場合は、Node.js があれば次でも動きます。

```powershell
npx serve -l 8000
```

停止するときは `Ctrl + C` です。

---

## 画面一覧

| ルート | 画面 | 内容 |
| --- | --- | --- |
| `#/timeline` | タイムライン | 「いま勉強中」のライブ表示（LoC＋継続時間）、投稿カード（平均LoC＋スパークライン）。タブ：フォロー／目標 |
| `#/record` | 記録する | 科目ごとの教材カード一覧、右下に新規記録ボタン |
| `#/record/<教材ID>` | LoC計測 | カメラプレビュー、経過時間、現在のLoC、リアルタイム折れ線、一時停止／再開／終了 |
| `#/report` | レポート | サマリー、週間の積み上げ棒、教材別の円、日別の平均LoC。タブ：記録／自分 |
| `#/search` | さがす | 検索バー、ユーザー／教材／大学をさがす、記事一覧 |
| `#/profile` | プロフィール | 登録教材数、基本情報、自己紹介、タグ、保存データの初期化 |

スマホ幅（〜400px）では、左サイドバーが画面下部のタブバーに切り替わります。

---

## ファイル構成

```
static/
  index.html              アプリのシェル（サイドバー・ヘッダー・ページ領域）
  css/
    base.css              CSS変数（配色）、リセット、共通ユーティリティ
    layout.css            サイドバー・ヘッダー・中央カラム・レスポンシブ
    components.css        カード、ボタン、ゲージ、フォームなど部品
  js/
    app.js                ハッシュルーティングと共通レイアウトの描画
    store.js              mock.json の読み込み、localStorage、レポート用の集計
    locClient.js          LoC 取得クライアント（モック / WebSocket）
    charts.js             Chart.js のラッパー＋自前のゲージ・スパークライン
    utils.js              DOM・時刻整形・LoC の色分けなどの小道具
    icons.js              アイコン（すべてインラインSVGで自作）
    pages/
      timeline.js  record.js  report.js  search.js  profile.js
  data/
    mock.json             モックデータ（ユーザー・教材・投稿・学習記録など）
  docs/reference/         デザインの参考資料を置く場所
  README.md
```

### データの持ち方

- `data/mock.json` … 他ユーザーの投稿、教材マスタ、記事など「サーバーから来るはずのもの」
- `localStorage`（キー `onsta.v1`）… 自分の学習記録といいね

初回起動時に `mock.json` のサンプル学習記録が `localStorage` へ取り込まれ、
レポートのグラフが最初から見られる状態になります。
消したいときはプロフィール画面の「保存データを初期化」を押してください。

---

## LoC API の仕様

フロントは `js/locClient.js` の `LocClient` だけを使い、
値の出どころ（モックか推論サーバーか）を意識しません。

### 本番（WebSocket）

```
接続先: ws://localhost:8765/loc
```

**フロント → サーバー**（カメラフレームを約 5fps で送信）

```json
{ "type": "frame", "timestamp": 1758512345678, "image": "<JPEGのBase64>" }
```

- `image` は `data:image/jpeg;base64,` のヘッダを除いた本体のみ
- 既定では横幅 320px・品質 0.6 の JPEG に縮小して送る

**サーバー → フロント**

```json
{ "type": "loc", "timestamp": 1758512345900, "loc": 73 }
```

```json
{ "type": "warmup", "timestamp": 1758512345900, "remaining": 12, "total": 20, "seconds": 60 }
```

- `warmup` … モデルは直近12サンプル（5秒間隔＝約60秒ぶん）がたまるまで LoC を出せないので、その間の進み具合

- `loc` は 0〜100 の数値
- `type` が `loc` 以外のメッセージ、JSON でないメッセージは無視される

> サーバーは `../LoC/server.py`。モデルはフロントと同じ 0〜100 の LoC を出すので、そのまま（小数第1位に丸めて）返します。
> 送られてきたフレームのうち推論に使うのは、`timestamp` で5秒（学習時の `--rate`）おきの1枚だけです。
> そのため `LOC_CONFIG.fps` は 0.2 以上なら何でもよく、LoC が届くのも5秒ごとです。

### モック

- カメラ映像は送らず、ランダムウォークで 0〜100 のなめらかな LoC を **1秒ごと** に返す
- 中心値（既定 68）へゆるやかに引き戻しつつ、毎回小さく揺らすので実際の推移に似た形になる

### モック / 本番の切り替え

`js/locClient.js` の先頭にある `LOC_CONFIG.mode` を書き換えるだけです。

```js
export const LOC_CONFIG = {
  mode: "live",   // "mock" … モック / "live" … 推論サーバーに接続
  url: "ws://localhost:8765/loc",
  fps: 5,
  ...
};
```

主な設定項目：

| キー | 既定値 | 意味 |
| --- | --- | --- |
| `mode` | `"live"` | `"mock"` / `"live"` の切り替え |
| `url` | `ws://localhost:8765/loc` | 推論サーバーの接続先 |
| `fps` | `5` | 1秒あたりに送るフレーム数 |
| `frameWidth` | `640` | 送信する JPEG の横幅（px）。小さいと手の検出が落ちる |
| `jpegQuality` | `0.8` | JPEG の品質（0〜1） |
| `connectTimeoutMs` | `4000` | 接続待ちの上限（ms） |
| `mockIntervalMs` | `1000` | モックが LoC を返す間隔（ms） |
| `autoFallbackToMock` | `true` | 接続失敗時に自動でモックへ切り替えるか |

### 接続に失敗したとき

`"live"` で接続できなかった・途中で切れた場合は、計測画面にエラーを表示します。
`autoFallbackToMock` が `true` ならそのままモックに切り替えて計測を続けます。
`false` にすると「モックで続ける」ボタンが出て、手動で切り替えられます。

---

## カメラについて

- 計測開始前に、映像が保存されないことの説明と同意チェックを表示します
- 同意しないと開始ボタンは押せません
- 計測中はプレビューの表示／非表示を切り替えられます（映像自体は止めないので LoC は取れ続けます）
- カメラが使えない場合（不許可・未接続・他アプリが使用中）は、
  学習時間を手入力する **LoC なしの通常記録** にフォールバックできます
- 画面を離れると、カメラ・タイマー・WebSocket はすべて自動で停止します

---

## デザインについて

- 配色はすべて `css/base.css` の `:root` にまとめています。`--c-primary` を変えると全体の印象が変わります
- ロゴは画像を使わず、テキストと CSS のグラデーションで作っています
- アイコンは `js/icons.js` にインライン SVG として自作したものだけを使っています
- LoC は 3 段階で色分けしています：70以上＝高（`--c-loc-high`）／40〜69＝中（`--c-loc-mid`）／40未満＝低（`--c-loc-low`）
