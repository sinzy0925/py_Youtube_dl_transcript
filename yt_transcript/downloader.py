"""yt-dlp で音声と字幕をダウンロードする。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadError

log = logging.getLogger(__name__)

_BOT_MARKERS = ("not a bot", "sign in to confirm")
_FALLBACK_BROWSERS = ("edge", "chrome")
_AUTH_COOKIE_KEYS = ("LOGIN_INFO", "SID", "__Secure-1PSID", "__Secure-3PSID")


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def discover_cookie_files() -> list[Path]:
    """ローカルと sibling の py_youtube_dl_mp4/cookies.txt を探す（mp4 プロジェクトと同様）。"""
    root = project_root()
    paths = [
        Path("cookies.txt"),
        root / "cookies.txt",
        root.parent / "py_youtube_dl_mp4" / "cookies.txt",
    ]
    found: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        rp = p.resolve()
        if rp.is_file() and rp not in seen:
            seen.add(rp)
            found.append(rp)
    return found


def cookie_file_score(path: Path) -> int:
    names = youtube_cookie_names(path)
    score = len(names)
    for key in _AUTH_COOKIE_KEYS:
        if key in names:
            score += 10
    return score


def pick_cookie_file(explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    files = discover_cookie_files()
    if not files:
        return None
    return max(files, key=cookie_file_score)


def build_ytdlp_common(*, with_youtube_auth: bool = False) -> dict:
    """py_youtube_dl_mp4/download_channel.py の build_ydl_opts に合わせた共通設定。"""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "js_runtimes": {"deno": {}},
        "remote_components": {"ejs:github"},
    }
    if with_youtube_auth:
        # Cookie 利用時の tv_downgraded 系不具合回避 (yt-dlp#17389)
        opts["extractor_args"] = {"youtube": {"player_client": ["default", "web_embedded"]}}
    return opts


def _browser_cookie_tuple(spec: str) -> tuple[str, ...]:
    # chrome / edge / firefox / chrome:Profile 1
    return tuple(spec.split(":", 1))


def youtube_cookie_names(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 6 and parts[0].endswith("youtube.com"):
            names.add(parts[5])
    return names


def warn_if_youtube_cookies_incomplete(path: Path) -> None:
    names = youtube_cookie_names(path)
    if not names:
        log.warning(
            "%s に .youtube.com の Cookie がありません。"
            "YouTube を開いた状態で cookies.txt を再エクスポートしてください。",
            path.name,
        )
        return
    if not ({"LOGIN_INFO", "SID", "__Secure-1PSID"} & names):
        log.warning(
            "%s に YouTube ログイン Cookie (LOGIN_INFO / SID 等) がありません。"
            "ログイン済みの YouTube タブから再エクスポートしないと bot 判定で失敗しやすいです。",
            path.name,
        )


def _is_bot_error(exc: BaseException) -> bool:
    msg = str(exc).casefold()
    return any(m in msg for m in _BOT_MARKERS)


def _bot_help_message() -> str:
    return (
        "YouTube の bot 判定により取得できませんでした。\n"
        "対処:\n"
        "  1) Chrome / Edge をすべて終了して "
        "`python main.py \"URL\" --cookies-from-browser edge` を試す\n"
        "     （ブラウザ起動中は Cookie DB を読めず失敗します）\n"
        "  2) YouTube にログインしたタブで cookies.txt を再エクスポート "
        "（.youtube.com に LOGIN_INFO が含まれること。拡張「Get cookies.txt LOCALLY」推奨）\n"
        "     または ../py_youtube_dl_mp4/cookies.txt を更新して共有\n"
        "  3) yt-dlp を更新: `.venv\\Scripts\\python.exe -m pip install -U \"yt-dlp[default]\"`"
    )


def _auth_strategies(
    cookies_file: Path | None,
    cookies_from_browser: str | None,
) -> list[tuple[str, dict]]:
    if cookies_from_browser:
        return [
            (
                f"browser:{cookies_from_browser}",
                {"cookiesfrombrowser": _browser_cookie_tuple(cookies_from_browser)},
            )
        ]
    strategies: list[tuple[str, dict]] = []
    files: list[Path] = []
    seen: set[Path] = set()
    for p in ([cookies_file] if cookies_file else []) + discover_cookie_files():
        if p is None:
            continue
        rp = p.resolve()
        if rp.is_file() and rp not in seen:
            seen.add(rp)
            files.append(rp)
    for p in sorted(files, key=cookie_file_score, reverse=True):
        strategies.append((f"file:{p.name}", {"cookiefile": str(p)}))
    env = os.environ.get("YT_COOKIES_BROWSER", ",".join(_FALLBACK_BROWSERS))
    for browser in env.split(","):
        browser = browser.strip()
        if browser:
            strategies.append(
                (f"browser:{browser}", {"cookiesfrombrowser": _browser_cookie_tuple(browser)}),
            )
    return strategies


def _extract_info(url: str, auth: dict) -> dict:
    opts = {**build_ytdlp_common(with_youtube_auth=bool(auth)), **auth}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def extract_info_with_auth(
    url: str,
    cookies_file: Path | None = None,
    cookies_from_browser: str | None = None,
) -> tuple[dict, dict]:
    """認証方法を順に試し、(動画 info, yt-dlp 共通オプション) を返す。"""
    ensure_js_runtime()
    if cookies_file and not cookies_from_browser:
        warn_if_youtube_cookies_incomplete(cookies_file)

    strategies = _auth_strategies(cookies_file, cookies_from_browser)
    if not strategies:
        strategies = [("none", {})]

    last_err: BaseException | None = None
    common: dict = build_ytdlp_common()
    for label, auth in strategies:
        try:
            log.info("YouTube 取得を試行: %s", label)
            info = _extract_info(url, auth)
            common = {**build_ytdlp_common(with_youtube_auth=bool(auth)), **auth}
            return info, common
        except (DownloadError, yt_dlp.utils.ExtractorError) as e:
            last_err = e
            if auth.get("cookiesfrombrowser"):
                log.warning(
                    "ブラウザ Cookie 取得失敗 (%s): %s",
                    label,
                    str(e).split("\n")[0][:120],
                )
                continue
            if _is_bot_error(e):
                log.warning("bot 判定 (%s): 次の方法を試します", label)
                continue
            raise
        except Exception as e:
            last_err = e
            if auth.get("cookiesfrombrowser"):
                log.warning("ブラウザ Cookie 取得失敗 (%s): %s", label, str(e).split("\n")[0][:120])
                continue
            raise

    raise RuntimeError(_bot_help_message()) from last_err


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
    info, common = extract_info_with_auth(url, cookies_file, cookies_from_browser)
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
