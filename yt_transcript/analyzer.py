"""gemini-3.5-flash-lite で「お勧め基準の要約」と「お勧め10選(開始時刻+URL)」を生成する。"""

from __future__ import annotations

import logging

from google.genai import types
from pydantic import BaseModel, Field

from .gemini_client import ANALYZE_MODEL, RotatingGemini
from .subtitles import Cue, find_cue_for_text, similarity
from .transcriber import Segment, fmt_ts

log = logging.getLogger(__name__)


class Pick(BaseModel):
    rank: int = Field(description="1〜10 の順位")
    title: str = Field(description="お勧め内容の短い見出し（日本語）")
    reason: str = Field(description="なぜお勧めなのか、動画内の根拠（日本語、1〜2文）")
    start_timestamp: str = Field(description="話し始める時刻。文字起こしの行頭にある [H:MM:SS] または [MM:SS] をそのまま書く")
    quote: str = Field(description="その時刻の文字起こし行から抜き出した原文（15〜40文字程度、改変しない）")


class Analysis(BaseModel):
    summary: str = Field(description="動画の要約（日本語、お勧めを基準に 300〜600 文字、Markdown 可）")
    picks: list[Pick] = Field(description="お勧め10選。時系列順ではなくお勧め度の高い順")


def parse_ts(ts: str) -> float | None:
    ts = ts.strip().strip("[]")
    parts = ts.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 1:
        return nums[0]
    return None


_PROMPT = """あなたは動画コンテンツの編集者です。以下は YouTube 動画「{title}」の、時刻付き文字起こしです。
各行は [時刻] 発話 の形式で、時刻は動画先頭からの経過時間です。

タスク:
1. この動画で「お勧めされているもの・こと」（商品、場所、方法、考え方、作品など、話者が勧めている対象）を基準に、動画全体を日本語で要約してください。
2. お勧めの中から特に価値の高いものを 10 個選び、順位をつけてください（10 個に満たない場合はある分だけ）。
   各項目について、その話題を話し始める行の [時刻] を start_timestamp に、その行の原文の一部を quote に、そのまま写してください。
   時刻は絶対に推測せず、必ず文字起こしの行頭にある時刻を使ってください。これは最重要です。

出力はすべて日本語で。

=== 文字起こし ===
{transcript}
"""


def analyze(g: RotatingGemini, title: str, transcript_md: str) -> Analysis:
    def _do(client):
        return client.models.generate_content(
            model=ANALYZE_MODEL,
            contents=_PROMPT.format(title=title, transcript=transcript_md),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Analysis,
                temperature=0.2,
            ),
        )

    resp = g.call(_do)
    parsed = resp.parsed
    if isinstance(parsed, Analysis):
        return parsed
    return Analysis.model_validate_json(resp.text)


def verify_pick_times(analysis: Analysis, segments: list[Segment], cues: list[Cue]) -> list[dict]:
    """モデルが返した時刻を、quote と文字起こし/字幕を照合して検証・補正する。"""
    results: list[dict] = []
    for p in analysis.picks:
        t_model = parse_ts(p.start_timestamp)
        # 1) quote に最も近い文字起こしセグメントを探す（モデル時刻の ±90 秒を優先、無ければ全体）
        best_seg: Segment | None = None
        best_r = 0.0
        for window in (90.0, None):
            for s in segments:
                if window is not None and t_model is not None and abs(s.start - t_model) > window:
                    continue
                r = similarity(s.text, p.quote)
                if p.quote and (p.quote in s.text):
                    r = 1.0
                if r > best_r:
                    best_seg, best_r = s, r
            if best_r >= 0.6:
                break

        if best_seg is not None and best_r >= 0.6:
            t = best_seg.start
            source = "transcript"
        elif t_model is not None:
            t = t_model
            source = "model"
        else:
            t = 0.0
            source = "unknown"

        # 2) 字幕があれば、さらに字幕キューで微調整
        if cues:
            c = find_cue_for_text(cues, p.quote, t, window_sec=30.0)
            if c is not None:
                t = c.start
                source += "+subtitle"

        results.append({
            "rank": p.rank,
            "title": p.title,
            "reason": p.reason,
            "quote": p.quote,
            "start_sec": int(t),
            "start": fmt_ts(t),
            "model_timestamp": p.start_timestamp,
            "time_source": source,
            "match_ratio": round(best_r, 2),
        })
    results.sort(key=lambda r: r["rank"])
    return results
