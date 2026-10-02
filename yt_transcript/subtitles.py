"""WebVTT 字幕のパースと、文字起こしセグメントとの時刻同期。"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

log = logging.getLogger(__name__)

_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})|(\d{1,2}):(\d{2})[.,](\d{3})")
_TAG = re.compile(r"<[^>]+>")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _parse_ts(s: str) -> float | None:
    m = _TS.match(s.strip())
    if not m:
        return None
    if m.group(1) is not None:
        h, mi, se, ms = (int(m.group(i)) for i in (1, 2, 3, 4))
    else:
        h, mi, se, ms = 0, int(m.group(5)), int(m.group(6)), int(m.group(7))
    return h * 3600 + mi * 60 + se + ms / 1000


def parse_vtt(path: Path) -> list[Cue]:
    """VTT を Cue のリストに。YouTube 自動字幕の重複行（ロールアップ）は除去する。"""
    cues: list[Cue] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "-->" in line:
            a, b = line.split("-->", 1)
            start, end = _parse_ts(a), _parse_ts(b.split()[0] if b.split() else b)
            i += 1
            buf: list[str] = []
            while i < len(lines) and lines[i].strip():
                buf.append(_TAG.sub("", lines[i]).strip())
                i += 1
            text = " ".join(t for t in buf if t).strip()
            if start is not None and end is not None and text:
                cues.append(Cue(start, end, text))
        else:
            i += 1

    # 自動字幕は同じテキストが前後のキューで繰り返されるので、直前と同じ行は捨てる
    dedup: list[Cue] = []
    for c in cues:
        if dedup and (c.text == dedup[-1].text or dedup[-1].text.endswith(c.text)):
            continue
        if dedup and c.text.startswith(dedup[-1].text) and c.start - dedup[-1].start < 0.05:
            dedup[-1] = c
            continue
        dedup.append(c)
    log.info("字幕 %s: %d キュー", path.name, len(dedup))
    return dedup


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[\s\W_]+", "", text)


def similarity(a: str, b: str) -> float:
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def find_cue_for_text(cues: list[Cue], text: str, around_sec: float, window_sec: float = 45.0, min_ratio: float = 0.55) -> Cue | None:
    """around_sec 付近 ±window_sec の字幕から、text に最も近いキューを返す。"""
    best: Cue | None = None
    best_r = 0.0
    key = normalize(text)
    for c in cues:
        if abs(c.start - around_sec) > window_sec:
            continue
        nc = normalize(c.text)
        # 部分一致（短い方が長い方に含まれる）は高評価
        if nc and key and (nc in key or key in nc):
            r = 0.95
        else:
            r = similarity(c.text, text)
        if r > best_r:
            best, best_r = c, r
    return best if best_r >= min_ratio else None
