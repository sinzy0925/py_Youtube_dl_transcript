"""ffmpeg でモノラル変換と 20 分分割を行う。"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SEGMENT_SECONDS = 20 * 60


@dataclass
class AudioChunk:
    index: int
    path: Path
    offset_sec: float  # 動画先頭からのオフセット（この分割ファイルの 0 秒が動画の何秒か）


def _run(cmd: list[str]) -> None:
    log.debug("ffmpeg: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失敗:\n{r.stderr[-2000:]}")


def to_mono(src: Path, dst: Path, sample_rate: int = 16000) -> Path:
    """モノラル 16kHz MP3 に変換（文字起こし向け）。"""
    if dst.exists():
        log.info("モノラル変換済み: %s", dst.name)
        return dst
    _run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), "-b:a", "64k", str(dst)])
    log.info("モノラル変換完了: %s", dst.name)
    return dst


def split(mono: Path, outdir: Path, segment_sec: int = SEGMENT_SECONDS) -> list[AudioChunk]:
    """segment_sec ごとに分割。既存があれば再利用。"""
    outdir.mkdir(parents=True, exist_ok=True)
    pattern = outdir / "part_%03d.mp3"
    existing = sorted(outdir.glob("part_*.mp3"))
    if not existing:
        _run([
            "ffmpeg", "-y", "-i", str(mono),
            "-f", "segment", "-segment_time", str(segment_sec),
            "-reset_timestamps", "1", "-c", "copy", str(pattern),
        ])
        existing = sorted(outdir.glob("part_*.mp3"))
    chunks = [AudioChunk(i, p, i * segment_sec) for i, p in enumerate(existing)]
    log.info("分割: %d ファイル (%d 秒ごと)", len(chunks), segment_sec)
    return chunks
