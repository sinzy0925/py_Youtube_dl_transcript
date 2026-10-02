"""gemini-3.5-transcribe で分割音声を文字起こしし、動画全体の絶対時刻付きセグメントにまとめる。"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from google.genai import types

from .audio import AudioChunk
from .gemini_client import TRANSCRIBE_MODEL, RotatingGemini
from .subtitles import Cue, find_cue_for_text

log = logging.getLogger(__name__)


@dataclass
class Word:
    word: str
    start: float  # 動画全体での絶対秒
    end: float


@dataclass
class Segment:
    start: float  # 動画全体での絶対秒
    end: float
    text: str
    part: int  # 何番目の分割ファイル由来か
    synced: bool = False  # 字幕で時刻補正したか


def parse_offset(v: str | None) -> float | None:
    """'12.500s' / '12s' / '0.5' などを秒(float)に。"""
    if v is None:
        return None
    m = re.match(r"^\s*([\d.]+)\s*s?\s*$", str(v))
    return float(m.group(1)) if m else None


def fmt_ts(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _upload_and_wait(client, path: Path):
    f = client.files.upload(file=str(path))
    while getattr(getattr(f, "state", None), "name", str(getattr(f, "state", ""))) == "PROCESSING":
        time.sleep(2)
        f = client.files.get(name=f.name)
    return f


def transcribe_chunk(g: RotatingGemini, chunk: AudioChunk, language_codes: list[str]) -> tuple[str, list[Word]]:
    """1 分割ファイルを文字起こし。単語タイムスタンプはチャンクのオフセットを足して絶対時刻に。"""

    def _do(client):
        f = _upload_and_wait(client, chunk.path)
        try:
            return client.models.generate_content(
                model=TRANSCRIBE_MODEL,
                contents=[f],
                config=types.GenerateContentConfig(
                    audio_transcription_config=types.AudioTranscriptionConfig(
                        language_codes=language_codes,
                        word_timestamp=True,
                    ),
                ),
            )
        finally:
            try:
                client.files.delete(name=f.name)
            except Exception:
                pass

    resp = g.call(_do)
    words: list[Word] = []
    texts: list[str] = []
    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else []) or []:
            if part.text:
                texts.append(part.text)
            tr = getattr(part, "audio_transcription", None)
            if tr and tr.words:
                for w in tr.words:
                    s, e = parse_offset(w.start_offset), parse_offset(w.end_offset)
                    if w.word and s is not None:
                        words.append(Word(w.word, s + chunk.offset_sec, (e if e is not None else s) + chunk.offset_sec))
    text = (resp.text or " ".join(texts) or "").strip()
    log.info("part_%03d: %d 文字 / %d 単語(時刻付き)", chunk.index, len(text), len(words))
    return text, words


def words_to_segments(words: list[Word], part: int, max_words: int = 25, gap_sec: float = 1.0, max_dur: float = 15.0) -> list[Segment]:
    """単語列を、無音ギャップ/句点/語数/長さで区切ったセグメントにまとめる。"""
    segs: list[Segment] = []
    buf: list[Word] = []

    def flush():
        if buf:
            # 日本語は空白なしで、英数字を含む語間は空白で結合
            out = ""
            for w in buf:
                t = w.word.strip()
                if not t:
                    continue
                # 英数字・記号で終わる語の後に ASCII 英数字が続く場合は空白を挟む（日本語同士は結合）
                if out and out[-1].isascii() and t[0].isascii() and t[0].isalnum():
                    out += " "
                out += t
            segs.append(Segment(buf[0].start, buf[-1].end, out, part))
            buf.clear()

    for w in words:
        if buf:
            gap = w.start - buf[-1].end
            dur = w.end - buf[0].start
            ends_sentence = buf[-1].word.strip()[-1:] in "。．.!?！？"
            if gap > gap_sec or len(buf) >= max_words or dur > max_dur or ends_sentence:
                flush()
        buf.append(w)
    flush()
    return segs


def sync_with_subtitles(segments: list[Segment], cues: list[Cue], max_shift: float = 20.0) -> list[Segment]:
    """字幕キューとテキスト照合し、近い時刻に字幕があればその開始時刻を採用して補正する。"""
    if not cues:
        return segments
    shifted = 0
    for s in segments:
        c = find_cue_for_text(cues, s.text, s.start)
        if c and abs(c.start - s.start) <= max_shift:
            delta = c.start - s.start
            if abs(delta) > 0.3:
                s.end += delta
                s.start = c.start
                shifted += 1
            s.synced = True
    log.info("字幕同期: %d/%d セグメントが一致、%d 件を時刻補正", sum(s.synced for s in segments), len(segments), shifted)
    return segments


def transcribe_all(g: RotatingGemini, chunks: list[AudioChunk], cues: list[Cue], language_codes: list[str], cache_dir: Path) -> list[Segment]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    all_segs: list[Segment] = []
    for ch in chunks:
        cache = cache_dir / f"part_{ch.index:03d}.json"
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            words = [Word(**w) for w in data["words"]]
            text = data["text"]
            log.info("part_%03d: キャッシュ使用", ch.index)
        else:
            text, words = transcribe_chunk(g, ch, language_codes)
            cache.write_text(json.dumps({"text": text, "words": [asdict(w) for w in words]}, ensure_ascii=False), encoding="utf-8")
        if words:
            all_segs.extend(words_to_segments(words, ch.index))
        elif text:
            # 単語時刻が返らなかった場合はチャンク先頭の時刻で 1 セグメントに
            all_segs.append(Segment(ch.offset_sec, ch.offset_sec, text, ch.index))
    all_segs.sort(key=lambda s: s.start)
    return sync_with_subtitles(all_segs, cues)


def segments_to_markdown(segments: list[Segment]) -> str:
    return "\n".join(f"[{fmt_ts(s.start)}] {s.text}" for s in segments)
