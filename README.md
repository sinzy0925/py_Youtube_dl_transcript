# py_Youtube_dl_transcript

YouTube 動画を **音声ダウンロード → 文字起こし → 要約 & おすすめシーン 10 選** まで一括で行う CLI ツールです。

長い動画は自動で分割して Gemini で文字起こしし、字幕があれば時刻を補正します。最後に「お勧めを基準にした要約」と、**開始時刻付きの YouTube リンク** が付いた 10 選を Markdown / JSON で出力します。

## できること

| ステップ | 内容 |
|----------|------|
| 1 | yt-dlp で音声と字幕（あれば）を取得 |
| 2 | ffmpeg でモノラル化し、指定分単位に分割（既定 20 分） |
| 3 | **gemini-3.5-transcribe** で時刻付き文字起こし |
| 4 | 字幕 VTT と突き合わせてタイムスタンプを補正 |
| 5 | **gemini-3.5-flash-lite** で要約と「お勧め 10 選」を生成 |

## 必要な環境

- **Python 3.11+**（3.10 でも動く想定ですが、開発は 3.11 以降を推奨）
- **ffmpeg**（PATH に通す）
- **Deno** — yt-dlp の YouTube JS チャレンジ解決に使用  
  Windows 例: `winget install DenoLand.Deno`（インストール後はターミナルを開き直す）
- **Google AI（Gemini）API キー** — `.env` に設定（下記）

## セットアップ

```powershell
cd py_Youtube_dl_transcript
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

プロジェクト直下に `.env` を作成します（**Git にコミットしない** — `.gitignore` 済み）。

```env
GOOGLE_API_KEY_1=あなたのAPIキー
GOOGLE_API_KEY_2=別のキー（任意・クォータ用）
# … GOOGLE_API_KEY_10 まで。API 呼び出しごとにキーを順に使い分け（429 時も次キーへ）
```

キーは [Google AI Studio](https://aistudio.google.com/apikey) などから取得できます。1 本だけでも動作します。

## 使い方

### 基本（Windows 推奨: Cookie 自動更新付き）

```powershell
.\main.ps1 "https://www.youtube.com/watch?v=VIDEO_ID"
```

`main.ps1` は実行のたびに次を行い、**YouTube の bot 判定（Cookie 不足）を自動で回避しやすく**します。

1. **`scripts/launch_chrome_cdp.py`** … 専用プロファイルで Chrome を CDP モード起動し、指定 URL（YouTube）を表示  
2. **YouTube タブが CDP 上に現れるまで待機**  
3. **`scripts/export_cookies_cdp.py`** … 起動中の Chrome から Netscape 形式の `cookies.txt` を書き出し  
4. **`main.py`** … `--cookies cookies.txt` 付きで従来どおり文字起こしパイプラインを実行  

Chrome を**閉じたり**拡張で手動エクスポートしたりする必要はありません（初回ログインは下記「初回セットアップ」参照）。

```powershell
# CDP 更新をスキップ（既に cookies.txt がある場合など）
.\main.ps1 -SkipCookieRefresh "https://youtu.be/VIDEO_ID"

# --cookies / --cookies-from-browser を付けた場合も CDP 更新は行わない
.\main.ps1 "URL" --cookies cookies.txt
```

### 基本（Python のみ）

```powershell
python main.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

オプションを省略した場合、次の順で **cookies.txt を自動探索**します（YouTube 用 Cookie が多いファイルを優先）。

1. カレントディレクトリの `cookies.txt`
2. 本リポジトリ直下の `cookies.txt`
3. 兄弟プロジェクト **`../py_youtube_dl_mp4/cookies.txt`**（MP4 ダウンローダーと同じファイルを共有可能）

`main.ps1` を使わない場合は、事前に `cookies.txt` を用意するか、下記「手動で Cookie を用意する」を参照してください。

### よく使うオプション

```powershell
# 出力先・言語・分割長
python main.py "https://youtu.be/VIDEO_ID" --out output --lang ja --segment-min 20

# 詳細ログ
python main.py "URL" -v

# Cookie ファイルを明示（mp4 プロジェクトと共有する例）
python main.py "URL" --cookies ..\py_youtube_dl_mp4\cookies.txt

# ブラウザから Cookie を読む（Chrome / Edge は完全終了してから）
python main.py "URL" --cookies-from-browser edge
python main.py "URL" --cookies-from-browser "chrome:Profile 1"
```

| オプション | 説明 | 既定 |
|------------|------|------|
| `url` | YouTube の URL | （必須） |
| `--lang` | 音声の言語 `ja` / `en` / `auto` | `ja` |
| `--out` | 出力ルートディレクトリ | `output` |
| `--segment-min` | 文字起こし用の分割長（分） | `20` |
| `--cookies-from-browser` | `chrome` / `edge` / `firefox` / `chrome:Profile 1` など | なし |
| `--cookies` | Netscape 形式の `cookies.txt` | 自動探索（上記） |
| `-v` / `--verbose` | DEBUG ログ | オフ |

**注意:** `--cookies` と `--cookies-from-browser` は **同時に指定できません**。

### YouTube Cookie（bot 判定）の問題と解決策

#### 背景

2024 年以降、YouTube は Cookie なし／不十分な Cookie だと **`Sign in to confirm you're not a bot`** で yt-dlp が失敗することが多いです。ログイン済みセッション（`LOGIN_INFO` や `__Secure-1PSID` など）を yt-dlp に渡す必要があります。

従来よく使う **`--cookies-from-browser`** は、Chrome / Edge **起動中は Cookie データベースがロック**され読み込めず失敗しがちです。拡張機能で `cookies.txt` を毎回手動エクスポートする方法も手間がかかります。

#### 本リポジトリでの解決（CDP 経由・推奨）

**Chrome DevTools Protocol（CDP）** で起動した Chrome から、**ブラウザを閉じずに** Cookie を取得します。

| 手段 | 説明 |
|------|------|
| **`main.ps1`** | 上記「基本（Windows 推奨）」のとおり、起動 → 待機 → エクスポート → `main.py` を一括実行 |
| **`launch_chrome_cdp.py` + `export_cookies_cdp.py`** | 手動・自動化の部品。mp4 プロジェクトと共有する場合は `--shared-mp4` |

CDP 用 Chrome は兄弟プロジェクト **`../py_jobutore-kaijoken/.chrome-cdp-profile`**（既定プロファイル `Default`）を使います。`launch_chrome_cdp.py` と同じ設定です。

```powershell
# 部品だけ使う例
python scripts/launch_chrome_cdp.py --url "https://www.youtube.com/"
python scripts/export_cookies_cdp.py
python scripts/export_cookies_cdp.py --shared-mp4   # ../py_youtube_dl_mp4/cookies.txt
```

環境変数（任意）: `CDP_ENDPOINT`（既定 `http://127.0.0.1:9222`）、`CDP_USER_DATA_DIR`、`CHROME_PATH`

#### 初回セットアップ（CDP プロファイル）

**一度だけ**、CDP で起動した Chrome で [YouTube](https://www.youtube.com/) に **Google アカウントでログイン**してください。以降は `main.ps1` 実行時に Cookie が自動更新されます。

エクスポート後、`cookies.txt` に **`LOGIN_INFO`**（または `SID` / `__Secure-1PSID`）が含まれることを確認します（`export_cookies_cdp.py` が警告を出す場合は未ログインの可能性があります）。

#### 手動で Cookie を用意する（代替）

1. Chrome / Edge で **YouTube にログイン**したタブを開く  
2. 拡張 **「Get cookies.txt LOCALLY」** などで Netscape 形式をエクスポート  
3. 本リポ直下の `cookies.txt` または `../py_youtube_dl_mp4/cookies.txt` に保存  

#### `main.py` 単体・フォールバック

`cookies.txt` で失敗した場合、`main.py` / `downloader.py` は **別の cookies.txt → Edge → Chrome** の順で自動リトライします。`--cookies-from-browser` を使うときだけ **対象ブラウザをすべて終了**してください。

環境変数 `YT_COOKIES_BROWSER`（例: `edge,chrome`）で、自動リトライするブラウザの順序を変更できます。

## 出力ファイル

動画 ID ごとに `{--out}/{video_id}/` に成果物ができます。

```
output/
  VIDEO_ID/
    transcript.md    # 時刻付き文字起こし（Markdown）
    transcript.json  # セグメント一覧（JSON）
    summary.md       # 要約 + お勧め 10 選（時刻・youtu.be リンク付き）
    picks.json       # 10 選の構造化データ
  _work/             # 音声・分割ファイル・字幕などの作業用（再実行用に残る）
```

コンソールにも `summary.md` と同内容が表示され、最後に出力パスが表示されます。

### 出力例（summary.md のイメージ）

- **お勧めを基準にした要約** … 動画全体の要約文
- **お勧め 10 選** … ランク・タイトル・開始時刻・`https://youtu.be/...?t=秒`・理由・引用・時刻の根拠

## プロジェクト構成

```
main.ps1                # Windows ラッパー（CDP Cookie 更新 + main.py）
main.py                 # CLI エントリポイント
scripts/
  launch_chrome_cdp.py  # CDP モードで Chrome 起動
  export_cookies_cdp.py # CDP から cookies.txt を書き出し
yt_transcript/
  downloader.py         # yt-dlp（音声・字幕・Cookie 認証）
  audio.py              # ffmpeg（モノラル・分割）
  transcriber.py        # Gemini 文字起こし
  subtitles.py          # VTT パース・時刻同期
  analyzer.py           # 要約・10 選・時刻検証
  gemini_client.py      # API キーローテーション
```

`downloader.py` は [py_youtube_dl_mp4](../py_youtube_dl_mp4/) の `download_channel.py` と同様に、yt-dlp で **Deno + ejs** と Cookie 利用時の **player_client** 設定を使います。

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `Sign in to confirm you're not a bot` | **`.\main.ps1 "URL"`** で CDP から Cookie を更新。または CDP Chrome で YouTube にログイン後 `export_cookies_cdp.py` |
| `Cookie 読み込みエラー` / `Could not copy Chrome cookie database` | **`--cookies-from-browser` 使用時**は Chrome / Edge を完全終了。通常は **`main.ps1` または `cookies.txt`** を使う |
| `cookies.txt に YouTube ログイン Cookie がありません`（警告） | CDP プロファイルで YouTube にログインしてから `main.ps1` または `export_cookies_cdp.py` を再実行 |
| CDP に接続できない | `python scripts/launch_chrome_cdp.py` で起動。ポート 9222 が他プロセスと競合していないか確認 |
| `main.ps1` が毎回 Chrome を終了する | `launch_chrome_cdp.py` は同ポートの既存 CDP を置き換える仕様。`-SkipCookieRefresh` で Cookie 更新のみスキップ可能 |
| YouTube で「再読み込みが必要」等 | Deno をインストールし、ターミナルを再起動 |
| `GOOGLE_API_KEY_1〜10 が見つかりません` | `.env` のキー名・配置場所を確認 |
| 429 / クォータ超過 | `.env` に複数キーを追加（呼び出しごとに分散、429 時は次キーで再試行） |
| 文字起こしが遅い・高コスト | `--segment-min` を変える。動画が長いほど API 呼び出しが増える |
| yt-dlp が古い | `.venv\Scripts\python.exe -m pip install -U "yt-dlp[default]" yt-dlp-ejs` |

## セキュリティ・注意

- **`.env`** と **`cookies.txt`** には認証情報が含まれます。**GitHub などに push しない**でください（本リポジトリでは `.gitignore` 対象）。
- `cookies.txt` は **個人の Google / YouTube ログイン状態**に相当します。取り扱い・共有範囲に注意してください。
- YouTube の利用規約・著作権に従い、取得したコンテンツの利用範囲は自己責任でお願いします。

## ライセンス

[MIT License](LICENSE) — Copyright (c) 2026 sinzy0925
