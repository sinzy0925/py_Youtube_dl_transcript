"""CLI: python main.py <YouTube URL> [--lang ja] [--out output]

YouTube 動画 → 音声DL → モノラル変換 → 20分分割 → gemini-3.5-transcribe で文字起こし
→ 字幕で時刻同期 → gemini-3.5-flash-lite で「お勧め基準の要約」と「お勧め10選(時刻+URL)」
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yt_dlp

from yt_transcript import audio, downloader
from yt_transcript.analyzer import analyze, verify_pick_times
from yt_transcript.downloader import pick_cookie_file
from yt_transcript.gemini_client import RotatingGemini
from yt_transcript.subtitles import Cue, parse_vtt
from yt_transcript.transcriber import segments_to_markdown, transcribe_all


def build_url(video_id: str, sec: int) -> str:
    return f"https://youtu.be/{video_id}?t={sec}"


def resolve_cookies(
    cookies: str | None,
    cookies_from_browser: str | None,
) -> tuple[Path | None, str | None]:
    """--cookies / --cookies-from-browser が無いとき、最も有効そうな cookies.txt を選ぶ。"""
    if cookies_from_browser:
        return None, cookies_from_browser
    if cookies:
        return Path(cookies), None
    picked = pick_cookie_file()
    return picked, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="YouTube 動画の文字起こし + お勧め要約/10選")
    ap.add_argument("url", help="YouTube 動画 URL")
    ap.add_argument("--lang", default="ja", help="音声の言語コード (ja / en / auto)。既定: ja")
    ap.add_argument("--out", default="output", help="出力ディレクトリ")
    ap.add_argument("--segment-min", type=int, default=20, help="分割の長さ(分)。既定 20")
    ap.add_argument(
        "--cookies-from-browser", default=None, metavar="BROWSER",
        help="YouTube の bot 判定回避（chrome / edge / firefox / chrome:Profile 1 など。ブラウザは閉じる）",
    )
    ap.add_argument("--cookies", default=None, metavar="FILE", help="Netscape 形式の cookies.txt（bot 判定回避）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.cookies_from_browser and args.cookies:
        ap.error("--cookies-from-browser と --cookies は同時に指定できません")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "google_genai", "google_genai.models", "google_genai.types"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    log = logging.getLogger("main")

    g = RotatingGemini()

    cookies_file, cookies_from_browser = resolve_cookies(args.cookies, args.cookies_from_browser)
    if cookies_file:
        log.info("Cookie ファイルを使用: %s", cookies_file.resolve())
    elif cookies_from_browser:
        log.info("ブラウザ Cookie を使用: %s", cookies_from_browser)
    else:
        log.warning(
            "Cookie 未指定です。YouTube の bot 判定で失敗する場合は "
            "`--cookies cookies.txt` または `--cookies-from-browser chrome` を指定してください。"
        )

    # 1. ダウンロード
    tmp_root = Path(args.out) / "_work"
    info = downloader.download(
        args.url, tmp_root,
        cookies_from_browser=cookies_from_browser,
        cookies_file=cookies_file,
    )
    outdir = Path(args.out) / info.video_id
    outdir.mkdir(parents=True, exist_ok=True)
    work = tmp_root / info.video_id
    work.mkdir(parents=True, exist_ok=True)

    # 2. モノラル変換 → 3. 20分分割
    mono = audio.to_mono(info.audio_path, work / "mono.mp3")
    chunks = audio.split(mono, work / "parts", segment_sec=args.segment_min * 60)

    # 字幕（あれば）
    # --lang に一致する字幕を優先（手動字幕 > 自動字幕 の順は downloader 側で担保済み）
    def _lang_rank(p: Path) -> int:
        return 0 if args.lang != "auto" and f".{args.lang}." in p.name else 1

    cues: list[Cue] = []
    for sp in sorted(info.subtitle_paths, key=_lang_rank):
        cues = parse_vtt(sp)
        if cues:
            log.info("同期に使う字幕: %s", sp.name)
            break

    # 4. 文字起こし（時刻付き、分割オフセット加算済み）＋字幕同期
    lang_codes = [] if args.lang == "auto" else [{"ja": "ja-JP", "en": "en-US"}.get(args.lang, args.lang)]
    segments = transcribe_all(g, chunks, cues, lang_codes, work / "transcripts")
    transcript_md = segments_to_markdown(segments)
    (outdir / "transcript.md").write_text(f"# {info.title}\n\n{info.url}\n\n{transcript_md}\n", encoding="utf-8")
    (outdir / "transcript.json").write_text(
        json.dumps([s.__dict__ for s in segments], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    log.info("文字起こし: %d セグメント → %s", len(segments), outdir / "transcript.md")

    # 5. 要約 + 10選
    analysis = analyze(g, info.title, transcript_md)
    picks = verify_pick_times(analysis, segments, cues)
    for p in picks:
        p["url"] = build_url(info.video_id, p["start_sec"])

    # 出力
    lines = [f"# {info.title}", "", info.url, "", "## お勧めを基準にした要約", "", analysis.summary, "", "## お勧め 10 選", ""]
    for p in picks:
        lines += [
            f"### {p['rank']}. {p['title']}",
            f"- 開始時刻: **{p['start']}**  {p['url']}",
            f"- 理由: {p['reason']}",
            f"- 発言: 「{p['quote']}」",
            f"- 時刻の根拠: {p['time_source']} (一致率 {p['match_ratio']})",
            "",
        ]
    (outdir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    (outdir / "picks.json").write_text(
        json.dumps({"video_id": info.video_id, "title": info.title, "summary": analysis.summary, "picks": picks}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    print("\n" + "\n".join(lines))
    print(f"\n出力先: {outdir.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except yt_dlp.cookies.CookieLoadError as exc:
        print(f"Cookie 読み込みエラー: {exc}", file=sys.stderr)
        print(
            "Chrome / Edge は起動中だと Cookie DB をロックします。\n"
            "  1) 対象ブラウザを完全に終了して `--cookies-from-browser edge` を再実行\n"
            "  2) または YouTube から cookies.txt を再エクスポート（../py_youtube_dl_mp4 と共有可能）",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
