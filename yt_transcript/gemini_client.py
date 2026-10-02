"""Gemini API クライアント。.env の GOOGLE_API_KEY_1〜10 をローテーションして使う。"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, TypeVar

from dotenv import load_dotenv
from google import genai
from google.genai import errors

log = logging.getLogger(__name__)

TRANSCRIBE_MODEL = "gemini-3.5-transcribe"
ANALYZE_MODEL = "gemini-3.5-flash-lite"

T = TypeVar("T")


def load_api_keys() -> list[str]:
    """GOOGLE_API_KEY_1 〜 GOOGLE_API_KEY_10 のうち設定されているものを順に返す。"""
    load_dotenv()
    keys: list[str] = []
    for i in range(1, 11):
        v = os.getenv(f"GOOGLE_API_KEY_{i}", "").strip().strip('"').strip("'")
        if v:
            keys.append(v)
    if not keys:
        raise RuntimeError(".env に GOOGLE_API_KEY_1〜10 が見つかりません")
    return keys


class RotatingGemini:
    """クォータ超過(429)等が出たら次の API キーに切り替えて再試行する薄いラッパー。"""

    def __init__(self, keys: list[str] | None = None, max_retries_per_key: int = 2):
        self.keys = keys or load_api_keys()
        self.idx = 0
        self.max_retries_per_key = max_retries_per_key
        self._clients: dict[int, genai.Client] = {}
        log.info("API キー %d 本をロード", len(self.keys))

    @property
    def client(self) -> genai.Client:
        if self.idx not in self._clients:
            self._clients[self.idx] = genai.Client(api_key=self.keys[self.idx])
        return self._clients[self.idx]

    def _rotate(self) -> None:
        self.idx = (self.idx + 1) % len(self.keys)
        log.warning("API キーを #%d に切り替え", self.idx + 1)

    def call(self, fn: Callable[[genai.Client], T]) -> T:
        """fn(client) を実行。429/503/403(クォータ)なら全キーを順に試す。"""
        attempts = 0
        total = len(self.keys) * self.max_retries_per_key
        last_err: Exception | None = None
        while attempts < total:
            try:
                return fn(self.client)
            except errors.APIError as e:  # type: ignore[attr-defined]
                last_err = e
                code = getattr(e, "code", None)
                msg = str(e)
                quota_like = code in (429, 503) or "quota" in msg.lower() or "RESOURCE_EXHAUSTED" in msg
                if not quota_like:
                    raise
                attempts += 1
                log.warning("API エラー(code=%s)。キー #%d → ローテーション (%d/%d)", code, self.idx + 1, attempts, total)
                self._rotate()
                time.sleep(min(2 * attempts, 15))
        raise RuntimeError(f"全 API キーで失敗しました: {last_err}")
