"""TTS word budgeting / script trimming."""
from __future__ import annotations

import re


def _tts_word_budget(config: dict, target_seconds: float, lang: str) -> int:
    """Max words a segment may contain to fit its spoken-duration target.

    Ties script length to the director's per-segment TIME budget instead of a
    fixed word count. Uses a configurable speaking rate (words/min); Hindi is
    slower AND expands vs English, so its default rate is lower. Returns 0 when
    no usable target is available (caller then keeps its existing word logic).

    The defaults below are CONSERVATIVE starting estimates. Tune them from a real
    run by comparing the '[DIRECTOR] ... N chars' / word-count logs against the
    final segment audio duration, via config keys:
        script.tts_words_per_minute_hi   (default 100)
        script.tts_words_per_minute_en   (default 150)
    """
    if not target_seconds or target_seconds <= 0:
        return 0
    _cfg = config.get("script", {}) if isinstance(config, dict) else {}
    wpm = float(
        _cfg.get(
            "tts_words_per_minute_hi" if lang == "hi" else "tts_words_per_minute_en",
            100.0 if lang == "hi" else 150.0,
        )
    )
    return max(1, int((target_seconds / 60.0) * wpm))


def _trim_script_to_word_limit(script: str, limit: int) -> str:
    """Trim narration at a sentence boundary, with a hard word-limit fallback."""
    if limit <= 0 or len(script.split()) <= limit:
        return script

    sentences = re.split(r"(?<=[.!?\u0964])\s+", script)
    parts: list[str] = []
    running = 0
    for sentence in sentences:
        sentence_words = len(sentence.split())
        if running + sentence_words > limit:
            break
        parts.append(sentence)
        running += sentence_words

    if parts:
        return " ".join(parts).strip()
    # ponytail: A hard cut is preferable to over-length audio when the LLM emits
    # one run-on sentence; upgrade to clause-aware trimming only if quality needs it.
    return " ".join(script.split()[:limit]).strip()
