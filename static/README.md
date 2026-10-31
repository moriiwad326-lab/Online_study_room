# Onsta フロントエンド

受験生向け学習記録アプリ **Onsta（オンスタ）** の Web フロントエンドです。

Onsta の特徴は、勉強中の手の動きをカメラでとらえて
**LoC（Level of Concentration：集中度、0〜100）** を推定し、リアルタイムに表示すること。
このリポジトリはフロントエンドだけで、LoC を推定するモデルは別（`../LoC/`）で動かす想定です。
`../LoC/server.py` を起動すると学習済みモデルの LoC が、起動していなければモックの LoC が表示されます。

- HTML / CSS / Vanilla JavaScript のみ（React などのフレームワーク・ビルドツールなし）
- グラフ描画のみ [Chart.js](https://www.chartjs.org/) を CDN から読み込み
- バーコード読み取りに、ブラウザに BarcodeDetector が無いとき（Windows の Chrome など）だけ
  [ZXing](https://github.com/zxing-js/browser) を CDN から読み込む（読み取りを始めたときに初めて読み込む）
- ハッシュベースの SPA（`#/timeline` など）
- ログインは `../main.py`（`../System/`）の API。ユーザーは SQLite に保存
- それ以外のデータはモック JSON ＋ `localStorage`

---

## 起動方法（Windows / PowerShell）

カメラ（`getUserMedia`）はセキュアなオリジンでしか使えないため、
**ファイルを直接開くのではなく `localhost` で動かしてください。**

**アプリサーバー**（フロントの配信＋ログイン API）を起動します。ログインに必要なので必ず起動してください。

```powershell
cd C:\Users\morii\develop\Online_Study_Room
python main.py
```

ブラウザで <http://localhost:8000/> を開き、「新規登録」からアカウントを作ります。
ユーザー情報は `System/data/onsta.db`（git 管理外）に保存されます。

**学習済みモデルで LoC も出す場合**は、もう1つのターミナルで推論サーバーを
フロント配信なし（`--no-static`。8000番はアプリサーバーが使うため）で起動します。

```powershell
cd C:\Users\morii\develop\Online_Study_Room
LoC\.venv\Scripts\python.exe LoC\server.py --no-static
```

「記録する」→（最初は「教材を追加」）→ 教材 → 「LoC計測を開始」。最初の LoC が出るまで約60秒かかります（その間は「準備中 n/12」と表示）。
カメラは学習データと同じく**机の真上〜斜め上から手元を写す**向きにしてください。
推論サーバーを起動していなければ、接続に失敗して自動でモックの LoC に切り替わります。

> `index.html` をダブルクリックで開いたり、`python -m http.server` で配信したりすると
> ログイン API が無いため、ログイン画面に「サーバーに接続できません」と表示されます。
> 必ず `python main.py` 越しに開いてください。

停止するときは `Ctrl + C` です。

---

## 画面一覧

| ルート | 画面 | 内容 |
| --- | --- | --- |
| `#/login` | ログイン | ログインID＋パスワード。未ログインで他の画面を開くとここへ移動 |
| `#/signup` | 新規登録 | ログインID（半角英数字と `_ . - @` の4〜64文字。メールアドレスも可）＋パスワード（8文字以上・英字と数字を含む） |
| `#/setup` | マイページの設定 | 初回ログイン時（プロフィール未保存）だけ表示。ニックネームのみ必須、他は任意 |
| `#/timeline` | タイムライン | 「いま勉強中」のライブ表示（LoC＋継続時間）、投稿カード（平均LoC＋スパークライン）。タブ：フォロー／目標 |
| `#/record` | 記録する | カテゴリごとの自分の教材一覧（最初は空で追加の案内）、教材を追加、右下に新規記録ボタン |
| `#/record/add` | 教材を追加 | バーコード（カメラ読み取り／13桁の手入力 → 書名・表紙を検索）か、独自教材（教材名・画像・カテゴリ）で追加 |
| `#/record/<教材ID>` | LoC計測 | カメラプレビューの上に継続時間・現在のLoC・リアルタイム折れ線（区間ごとにLoCの色）を重ねて表示、一時停止／再開／終了、教材の削除。LoCが0のまま1分（または15未満のまま5分）続くと継続時間を0に戻して止め、30より上に戻ると0から数え直す |
| `#/report` | レポート | サマリー、週間の積み上げ棒、教材別の円、日別の平均LoC。タブ：記録／自分 |
| `#/search` | さがす | 検索バー、ユーザー／教材／大学をさがす、記事一覧 |
| `#/profile` | プロフィール | 登録教材数、基本情報、自己紹介、タグ、保存データの初期化、ログアウト |
| `#/profile/edit` | プロフィール編集 | 初回設定と同じフォームで、あとから変更する |

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
    api.js                アプリサーバー（ログイン・教材 API）を呼ぶクライアント
    store.js              mock.json の読み込み、ログイン状態、自分の教材、localStorage、レポート用の集計
    barcode.js            カメラで書籍のバーコードを読む（BarcodeDetector / ZXing）
    locClient.js          LoC 取得クライアント（モック / WebSocket）
    charts.js             Chart.js のラッパー＋自前のゲージ・スパークライン
    utils.js              DOM・時刻整形・LoC の色分けなどの小道具
    icons.js              アイコン（すべてインラインSVGで自作）
    pages/
      timeline.js  record.js  report.js  search.js  profile.js
      login.js  signup.js  setup.js  profileForm.js（初回設定と編集で共用）
      materialAdd.js（教材の追加。record.js から呼ぶ）
  data/
    mock.json             モックデータ（他ユーザーの投稿とその教材・記事など）
  docs/reference/         デザインの参考資料を置く場所
  README.md
```

### データの持ち方

- `data/mock.json` … 他ユーザーの投稿とその教材、記事など「サーバーから来るはずのもの」
- アプリサーバー（`System/data/onsta.db`）… アカウント、ログインセッション、プロフィール、自分のカテゴリと教材（画像も）
- `localStorage`（キー `onsta.v1.u<ユーザーID>`）… 自分の学習記録といいね（ユーザーごと）

新しいユーザーは教材も学習記録も空から始まります（以前の版で入れていたサンプル学習記録は読み込み時に取り除きます）。
学習記録を消したいときはプロフィール画面の「保存データを初期化」を押してください。

---

## アプリ API の仕様

実装は `../System/server.py`（HTTP）、`../System/auth.py`（アカウント）、`../System/materials.py`（教材）、
`../System/books.py`（書誌検索）。リクエスト・レスポンスとも JSON です。

### ログイン

| メソッド | パス | 内容 |
| --- | --- | --- |
| `GET` | `/api/auth/me` | ログイン中のユーザー。未ログインなら 401 |
| `POST` | `/api/auth/register` | `{loginId, password}` で登録し、そのままログイン |
| `POST` | `/api/auth/login` | `{loginId, password}` でログイン |
| `POST` | `/api/auth/logout` | ログアウト |
| `PUT` | `/api/me/profile` | `{profile}` を保存（初回設定・編集とも）。保存すると `profileCompleted` が true になる |

- ユーザーは `{ id, loginId, profileCompleted, profile }` の形で返る
- エラーは `{ "error": "画面に出せるメッセージ", "field": "loginId" など | null }`
- `profile` の項目：`nickname`（必須・20文字）、`avatarColor`、`bio`、`tags`（10個まで）、`grade`、
  `targetSchool`、`examName`、`examDate`（`YYYY-MM-DD`）、`weeklyGoalMinutes`、`gender`、`birthYear`、`prefecture`

セキュリティまわり：

- パスワードは PBKDF2-HMAC-SHA256（60万回・ソルト付き）でハッシュ化して保存
- セッションは `HttpOnly; SameSite=Lax` の Cookie（30日）。DB にはトークンの SHA-256 だけを保存
- 書き込み系は `Content-Type: application/json` 必須（他サイトのフォームから送らせない）
- 同じログインIDで5回続けて失敗すると15分ログインできない
- HTTPS で公開するときは `python main.py --secure-cookie` で Cookie に `Secure` を付ける

### 教材

| メソッド | パス | 内容 |
| --- | --- | --- |
| `GET` | `/api/materials` | 自分のカテゴリと教材 `{categories, materials}` |
| `POST` | `/api/categories` | `{name, color}` でカテゴリ（教科）を作る。同じ名前は1つだけ |
| `POST` | `/api/materials` | `{title, categoryId, isbn?, image?, coverUrl?}` で教材を追加 |
| `DELETE` | `/api/materials/<id>` | 教材を削除（学習記録は残り「削除した教材」と表示） |
| `GET` | `/api/materials/<id>/image` | 教材の画像（本人だけ取得できる） |
| `GET` | `/api/books/<ISBN>` | バーコードの番号から書名・著者・出版社・表紙URLを検索 |

- カテゴリはユーザーが自分で作る（既定の教科はなし）。教材の追加フォームの中から作れる
- `image` はアップロード画像の data URL。フロントで長辺600pxの JPEG に縮めてから送る（サーバーは2MBまで、JPEG/PNG/WebP/GIF のみ）
- `coverUrl` は書誌検索で見つけた表紙。サーバーがダウンロードして保存する（取りに行くのは openBD / Google Books の画像ホストだけ）
- ISBN は ISBN-13 / ISBN-10（ハイフン可）を受け付け、チェックディジットを検査する。日本の書籍の下段バーコード（192…）は弾く
- 書誌検索は openBD → Google Books の順。Google Books はキーなしだと制限（429）にかかりやすいので、
  使うときは `python main.py --google-books-key <キー>` か環境変数 `GOOGLE_BOOKS_API_KEY` でキーを渡す
- openBD は書名は取れても表紙が無い本が多い。そのときは追加画面で画像を選んでもらう

テスト：`python -m pytest System/tests`

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
