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
- **Deno** — yt-dlp の YouTube 取得で必要になることがあります  
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
# … GOOGLE_API_KEY_10 まで。設定したキーを 429 等でローテーション
```

キーは [Google AI Studio](https://aistudio.google.com/apikey) などから取得できます。1 本だけでも動作します。

## 使い方

### 基本

```powershell
python main.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

### よく使うオプション

```powershell
# 出力先・言語・分割長
python main.py "https://youtu.be/VIDEO_ID" --out output --lang ja --segment-min 20

# 詳細ログ
python main.py "URL" -v

# YouTube の bot 判定で失敗する場合（ブラウザの Cookie を使う）
python main.py "URL" --cookies-from-browser chrome
# または Netscape 形式の cookies.txt（リポジトリには含めない）
python main.py "URL" --cookies cookies.txt
```

| オプション | 説明 | 既定 |
|------------|------|------|
| `url` | YouTube の URL | （必須） |
| `--lang` | 音声の言語 `ja` / `en` / `auto` | `ja` |
| `--out` | 出力ルートディレクトリ | `output` |
| `--segment-min` | 文字起こし用の分割長（分） | `20` |
| `--cookies-from-browser` | `chrome` / `edge` / `firefox` など | なし |
| `--cookies` | Netscape 形式の `cookies.txt` | なし |
| `-v` / `--verbose` | DEBUG ログ | オフ |

`--cookies-from-browser` を使うときは、**対象ブラウザを閉じてから**実行すると安定しやすいです。

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
main.py                 # CLI エントリポイント
yt_transcript/
  downloader.py         # yt-dlp（音声・字幕）
  audio.py              # ffmpeg（モノラル・分割）
  transcriber.py        # Gemini 文字起こし
  subtitles.py          # VTT パース・時刻同期
  analyzer.py           # 要約・10 選・時刻検証
  gemini_client.py      # API キーローテーション
```

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| YouTube で「再読み込みが必要」等 | Deno をインストールし、ターミナルを再起動 |
| ダウンロードが拒否される | `--cookies-from-browser` または `--cookies` を試す |
| `GOOGLE_API_KEY_1〜10 が見つかりません` | `.env` のキー名・配置場所を確認 |
| 429 / クォータ超過 | `.env` に複数キーを追加（自動ローテーション） |
| 文字起こしが遅い・高コスト | `--segment-min` を変える、動画が長いほど API 呼び出しが増える |

## セキュリティ・注意

- **`.env`** と **`cookies.txt`** には認証情報が含まれます。**GitHub などに push しない**でください（本リポジトリでは `.gitignore` 対象）。
- `cookies.txt` はブラウザ拡張「Get cookies.txt LOCALLY」などでエクスポートできますが、**個人のログイン状態そのもの**なので取り扱いに注意してください。
- YouTube の利用規約・著作権に従い、取得したコンテンツの利用範囲は自己責任でお願いします。

## ライセンス

[MIT License](LICENSE) — Copyright (c) 2026 sinzy0925
