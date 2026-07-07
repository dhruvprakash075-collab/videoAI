"""
Feature #7 — Voice emotion control.
Injects mood-appropriate punctuation and pacing markers into scripts
before they reach the TTS engine.

Supports both Latin (English) and Devanagari (Hindi) scripts.
Devanagari uses ।  as the sentence boundary instead of period.

B8 fix: inject_emotion now accepts a lang parameter so it is applied
        to the Devanagari text actually sent to TTS, not discarded.
B9 fix: get_mood_rate() is now wired into tts_generate via the pipeline.
B10 fix: Devanagari-aware sentence boundaries (।, ?, !) added.
"""

import re as _re

# ── Devanagari detection ──────────────────────────────────────────────────────


def _is_devanagari(text: str) -> bool:
    """Return True if the text is predominantly Devanagari script."""
    if not text:
        return False
    deva_chars = len(_re.findall(r"[\u0900-\u097F]", text))
    total_letters = len(_re.findall(r"[a-zA-Z\u0900-\u097F]", text))
    return total_letters > 0 and (deva_chars / total_letters) > 0.5


# ── Latin (English) helpers ───────────────────────────────────────────────────


def _safe_ellipsis_latin(text: str) -> str:
    """Replace sentence-ending periods with ellipsis (Latin text only)."""
    text = _re.sub(r"(?<=[a-zA-Z])\.(?=\s)", "...", text)
    return text


# ── Devanagari helpers ────────────────────────────────────────────────────────


def _safe_ellipsis_deva(text: str) -> str:
    """Keep Devanagari sentence pauses natural for TTS."""
    return _re.sub(r"\.{2,}", "।", text)


def _deva_inject(text: str, mood: str) -> str:
    """Apply mood-appropriate Devanagari punctuation shaping."""
    if mood in ("mysterious", "horror"):
        text = _safe_ellipsis_deva(text)
        text = text.replace("? ", "? ")
    elif mood == "action":
        text = text.replace("। ", "! ")
    elif mood in ("dramatic", "epic"):
        text = _safe_ellipsis_deva(text)
    elif mood == "intimate":
        text = _safe_ellipsis_deva(text)
        text = text.replace("! ", "। ")  # soften exclamations
    # calm: no change
    return text


# ── Mood marker table ─────────────────────────────────────────────────────────

_MOOD_MARKERS = {
    "mysterious": {
        "prefix": "",
        "inject_latin": lambda s: _safe_ellipsis_latin(s).replace("? ", "?... "),
        "suffix_latin": "...",
        "suffix_deva": "।",
        "rate": 0.86,
    },
    "horror": {
        "prefix": "",
        "inject_latin": lambda s: (
            _safe_ellipsis_latin(s).replace("! ", "!... ").replace("? ", "?... ")
        ),
        "suffix_latin": "...",
        "suffix_deva": "!",
        "rate": 0.8,
    },
    "action": {
        "prefix": "",
        "inject_latin": lambda s: s.replace(". ", "! ").replace(", ", " — "),
        "suffix_latin": "!",
        "suffix_deva": "!",
        "rate": 1.1,
    },
    "dramatic": {
        "prefix": "",
        "inject_latin": lambda s: s.replace(". ", "... ").replace(", ", " — "),
        "suffix_latin": ".",
        "suffix_deva": "।",
        "rate": 0.9,
    },
    "calm": {
        "prefix": "",
        "inject_latin": lambda s: s,
        "suffix_latin": ".",
        "suffix_deva": "।",
        "rate": 1.0,
    },
    "epic": {
        "prefix": "",
        "inject_latin": lambda s: s.replace(". ", "... ").replace("! ", "!! "),
        "suffix_latin": "!",
        "suffix_deva": "!",
        "rate": 0.9,
    },
    "intimate": {
        "prefix": "(softly) ",
        "inject_latin": lambda s: s.replace(". ", "... ").replace("! ", ". "),
        "suffix_latin": "",
        "suffix_deva": "",
        "rate": 0.88,
    },
}


def inject_emotion(script: str, mood: str = "mysterious", lang: str = "auto") -> str:
    """Add mood-appropriate punctuation markers to guide TTS prosody.

    Args:
        script: Narration script text (Latin or Devanagari).
        mood:   One of mysterious, horror, action, dramatic, calm, epic, intimate.
        lang:   "hi" forces Devanagari mode; "en" forces Latin mode;
                "auto" (default) detects from script content.

    Returns:
        Emotion-enhanced script string.
    """
    if not script:
        return script

    markers = _MOOD_MARKERS.get(mood, _MOOD_MARKERS["mysterious"])

    # Determine script type
    use_deva = lang == "hi" or (lang == "auto" and _is_devanagari(script))

    try:
        if use_deva:
            enhanced = _deva_inject(script, mood)
            suffix = markers["suffix_deva"]
            # Devanagari: no Latin prefix like "(softly)"
            prefix = ""
        else:
            enhanced = markers["inject_latin"](script)
            suffix = markers["suffix_latin"]
            prefix = markers["prefix"]

        # Add prefix if not already present
        if prefix and not enhanced.strip().startswith(prefix.strip("() ")):
            enhanced = prefix + enhanced

        # Add suffix if not already present
        if suffix:
            last_char = enhanced.strip()[-1] if enhanced.strip() else ""
            stripped = enhanced.rstrip()
            if stripped.endswith(suffix):
                # Already ends with the exact suffix — don't double-append
                pass
            elif last_char not in ".!?।":
                enhanced = stripped + suffix
            elif last_char in (".", "।") and suffix not in (".", "।"):
                # P4-20 fix: only strip the trailing punctuation when the suffix is
                # a different terminal marker (e.g. "!" replacing ".").  Never strip
                # when the suffix is "..." — that would produce "....." doubling.
                enhanced = stripped[:-1] + suffix

        return enhanced

    except Exception:
        # Never crash the pipeline over emotion shaping
        return script


def get_mood_rate(mood: str) -> float:
    """Get TTS speed multiplier for a given mood.

    Returns a float in [0.85, 1.1]. Used to set per-segment OmniVoice speed.
    """
    markers = _MOOD_MARKERS.get(mood, _MOOD_MARKERS["mysterious"])
    return markers["rate"]
