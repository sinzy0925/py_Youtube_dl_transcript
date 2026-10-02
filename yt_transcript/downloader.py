"""yt-dlp で音声と字幕をダウンロードする。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp

log = logging.getLogger(__name__)


def ensure_js_runtime() -> None:
    """yt-dlp の YouTube チャレンジ解決に必要な Deno を PATH に通す（未反映の新規ターミナル対策）。"""
    import os
    import shutil

    if shutil.which("deno"):
        return
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
        Path.home() / ".deno" / "bin",
    ]
    pkg_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if pkg_root.exists():
        candidates += [p for p in pkg_root.glob("DenoLand.Deno*")]
    for d in candidates:
        if (d / "deno.exe").exists() or (d / "deno").exists():
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            log.info("Deno を検出: %s", d)
            return
    log.warning(
        "Deno が見つかりません。YouTube の取得で「The page needs to be reloaded」等が出る場合は "
        "`winget install DenoLand.Deno` を実行し、ターミナルを開き直してください。"
    )


@dataclass
class VideoInfo:
    video_id: str
    title: str
    duration: float  # 秒
    url: str
    audio_path: Path
    subtitle_paths: list[Path] = field(default_factory=list)  # .vtt (手動字幕優先、次に自動字幕)


def _find_subs(workdir: Path, video_id: str, sub_langs: tuple[str, ...] = ("ja", "en")) -> list[Path]:
    # 手動字幕 (video_id.ja.vtt など) を先に、自動字幕を後に。同種内では sub_langs の指定順
    def lang_idx(p: Path) -> int:
        for i, l in enumerate(sub_langs):
            if f".{l}." in p.name:
                return i
        return len(sub_langs)

    manual = sorted((p for p in workdir.glob(f"{video_id}.*.vtt") if ".auto." not in p.name), key=lang_idx)
    auto = sorted(workdir.glob(f"{video_id}.auto.*.vtt"), key=lang_idx)
    return manual + auto


def download(
    url: str,
    workdir: Path,
    sub_langs: tuple[str, ...] = ("ja", "en"),
    cookies_from_browser: str | None = None,
    cookies_file: Path | None = None,
) -> VideoInfo:
    workdir.mkdir(parents=True, exist_ok=True)
    ensure_js_runtime()
    common: dict = {"quiet": True, "no_warnings": True, "noprogress": True}
    # 「Sign in to confirm you're not a bot」対策
    if cookies_file:
        common["cookiefile"] = str(cookies_file)  # Netscape 形式 (ブラウザ拡張 "Get cookies.txt LOCALLY" 等でエクスポート)
    elif cookies_from_browser:
        common["cookiesfrombrowser"] = (cookies_from_browser,)  # 例: "chrome", "edge", "firefox"（ブラウザは閉じておく）

    # 1) メタデータ取得
    with yt_dlp.YoutubeDL(common) as ydl:
        info = ydl.extract_info(url, download=False)
    video_id = info["id"]
    title = info.get("title", video_id)
    duration = float(info.get("duration") or 0)
    log.info("動画: %s (%s) %.0f 秒", title, video_id, duration)

    audio_path = workdir / f"{video_id}.m4a"
    existing_subs = _find_subs(workdir, video_id)
    if audio_path.exists() and existing_subs:
        log.info("ダウンロード済みのためスキップ")
        return VideoInfo(video_id, title, duration, url, audio_path, existing_subs)

    # 2) 音声（最良音質の音声のみ）
    if not audio_path.exists():
        opts_audio = {
            **common,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(workdir / f"{video_id}.%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
        }
        with yt_dlp.YoutubeDL(opts_audio) as ydl:
            ydl.download([url])
        if not audio_path.exists():
            cands = [p for p in workdir.glob(f"{video_id}.*") if p.suffix in (".m4a", ".webm", ".opus", ".mp3")]
            if not cands:
                raise RuntimeError("音声ファイルのダウンロードに失敗しました")
            audio_path = cands[0]

    # 3) 字幕（手動字幕 → 自動字幕）。別々に取得してファイル名で区別する
    for auto in (False, True):
        opts_sub = {
            **common,
            "skip_download": True,
            "writesubtitles": not auto,
            "writeautomaticsub": auto,
            "subtitleslangs": list(sub_langs),
            "subtitlesformat": "vtt",
            "outtmpl": str(workdir / (f"{video_id}.auto" if auto else video_id)),
        }
        try:
            with yt_dlp.YoutubeDL(opts_sub) as ydl:
                ydl.download([url])
        except Exception as e:  # 字幕は無くても続行
            log.warning("字幕取得(auto=%s)失敗: %s", auto, e)

    subs = _find_subs(workdir, video_id)
    log.info("字幕ファイル: %s", [p.name for p in subs] or "なし")
    return VideoInfo(video_id, title, duration, url, audio_path, subs)
