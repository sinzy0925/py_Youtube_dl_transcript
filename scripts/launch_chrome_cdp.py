"""
py_jobutore-kaijoken の .chrome-cdp-profile（既定: Default）で
Chrome を CDP モードで起動する。

例:
  python scripts/launch_chrome_cdp.py
  python scripts/launch_chrome_cdp.py --url about:blank
  python scripts/launch_chrome_cdp.py --profile 1

環境変数（任意）:
  CDP_ENDPOINT      既定 http://127.0.0.1:9222
  CDP_START_URL     起動時 URL（未指定時は請求書入力画面）
  CDP_USER_DATA_DIR 既定 ../py_jobutore-kaijoken/.chrome-cdp-profile
  CHROME_PATH       chrome.exe のパス
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CDP = "http://127.0.0.1:9222"
DEFAULT_START_URL = "https://youtu.be/-S9x0s9tTlA"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_USER_DATA_DIR = (
    PROJECT_ROOT / ".." / "py_jobutore-kaijoken" / ".chrome-cdp-profile"
).resolve()


def find_chrome_exe() -> Path:
    candidates = [
        Path(os.environ.get("CHROME_PATH", "")),
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    raise SystemExit(
        "Chrome が見つかりません。CHROME_PATH に chrome.exe を設定してください。"
    )


def cdp_user_data_dir() -> Path:
    raw = os.environ.get("CDP_USER_DATA_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_USER_DATA_DIR


def cdp_profile_directory_name(profile_number: int) -> str:
    if profile_number <= 0:
        return "Default"
    return f"Profile {profile_number}"


def resolve_cdp_profile_directory(
    user_data: Path,
    profile_number: int | None,
) -> str | None:
    if profile_number is not None:
        name = cdp_profile_directory_name(profile_number)
        profile_path = user_data / name
        if not profile_path.is_dir():
            raise SystemExit(
                f"CDP プロファイルがありません: {profile_path}\n"
                f"（user-data-dir={user_data} 内に {name} があるか確認）"
            )
        return name
    default = user_data / "Default"
    if default.is_dir():
        return "Default"
    return None


def cdp_port(endpoint: str) -> int:
    port = 9222
    if "://" in endpoint:
        hostport = endpoint.split("://", 1)[1]
        if ":" in hostport:
            try:
                port = int(hostport.rsplit(":", 1)[-1].split("/")[0])
            except ValueError:
                port = 9222
    return port


def cdp_ready(endpoint: str, timeout_sec: float = 1.5) -> bool:
    url = endpoint.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def kill_listeners_on_port(port: int) -> None:
    if sys.platform != "win32":
        return
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            errors="replace",
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return
    pids: set[str] = set()
    suffix = f":{port}"
    for line in out.splitlines():
        if "LISTENING" not in line or suffix not in line:
            continue
        parts = line.split()
        if parts and parts[-1].isdigit():
            pids.add(parts[-1])
    for pid in pids:
        subprocess.run(  # noqa: S603
            ["taskkill", "/F", "/PID", pid],
            check=False,
            timeout=30,
        )
    if pids:
        time.sleep(2.0)


def stop_cdp_endpoint(endpoint: str) -> None:
    if not cdp_ready(endpoint, timeout_sec=0.5):
        return
    kill_listeners_on_port(cdp_port(endpoint))
    for _ in range(20):
        if not cdp_ready(endpoint, timeout_sec=0.5):
            return
        time.sleep(0.25)


def launch_chrome_cdp(
    endpoint: str,
    *,
    start_url: str,
    profile_number: int | None = None,
    replace_existing: bool = True,
) -> subprocess.Popen[bytes]:
    if replace_existing and cdp_ready(endpoint):
        print("既存の CDP Chrome を終了します。")
        stop_cdp_endpoint(endpoint)

    port = cdp_port(endpoint)
    user_data = cdp_user_data_dir()
    if not user_data.is_dir():
        raise SystemExit(
            f"user-data-dir が見つかりません: {user_data}\n"
            "py_jobutore-kaijoken の .chrome-cdp-profile があるか確認してください。"
        )

    profile_directory = resolve_cdp_profile_directory(user_data, profile_number)
    chrome = find_chrome_exe()
    args = [
        str(chrome),
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if profile_directory:
        args.append(f"--profile-directory={profile_directory}")
    if start_url:
        args.append(start_url)

    print(f"Chrome 起動: {chrome}")
    print(f"  CDP: {endpoint}")
    print(f"  user-data-dir={user_data}")
    if profile_directory:
        print(f"  profile-directory={profile_directory}")
    if start_url:
        print(f"  start-url={start_url}")

    proc = subprocess.Popen(args)  # noqa: S603
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if cdp_ready(endpoint):
            print(f"CDP 準備完了: {endpoint.rstrip('/')}/json/version")
            return proc
        time.sleep(0.25)

    proc.terminate()
    raise SystemExit(
        f"CDP に接続できませんでした: {endpoint}\n"
        f"{endpoint.rstrip('/')}/json/version が開くか確認してください。"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="kaijoken の .chrome-cdp-profile で Chrome を CDP 起動"
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("CDP_ENDPOINT", DEFAULT_CDP),
        help=f"CDP エンドポイント（既定: {DEFAULT_CDP}）",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("CDP_START_URL", DEFAULT_START_URL),
        help=f"起動時に開く URL（既定: {DEFAULT_START_URL}）",
    )
    parser.add_argument(
        "--profile",
        type=int,
        default=None,
        metavar="N",
        help="プロファイル番号（未指定時は Default）",
    )
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="既に CDP が動いていれば終了せず、そのまま終了する",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="起動後、Enter まで Chrome プロセスを維持する",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    endpoint = str(args.endpoint).strip().strip('"').strip("'") or DEFAULT_CDP

    if args.no_replace and cdp_ready(endpoint):
        print(f"既に CDP が動作中です: {endpoint}")
        return

    proc = launch_chrome_cdp(
        endpoint,
        start_url=args.url,
        profile_number=args.profile,
        replace_existing=not args.no_replace,
    )

    if args.wait:
        try:
            input("Enter で Chrome を終了します…\n")
        except KeyboardInterrupt:
            pass
        proc.terminate()
        stop_cdp_endpoint(endpoint)


if __name__ == "__main__":
    main()
