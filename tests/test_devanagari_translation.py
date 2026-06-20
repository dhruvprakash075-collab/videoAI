"""tests/test_devanagari_translation.py

Unit tests for task 4.6:
  - _devanagari_ratio helper
  - bounded re-translation in translate_to_devanagari

All LLM calls are mocked — no real Ollama required.
"""

import sys
from pathlib import Path
from unittest.mock import patch

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import bootstrap_pipeline as _bp

_bp.bootstrap()

from agents.director_agent import _devanagari_ratio

# ── Helper tests ──────────────────────────────────────────────────────────


def test_devanagari_ratio_pure():
    """Pure Devanagari text → ratio 1.0."""
    assert _devanagari_ratio("नमस्ते दोस्तों") == 1.0


def test_devanagari_ratio_mixed():
    """Mixed Devanagari + Latin → ratio between 0 and 1."""
    ratio = _devanagari_ratio("नमस्ते phone")
    assert 0.0 < ratio < 1.0


def test_devanagari_ratio_no_alpha():
    """No alphabetic chars (numbers/punctuation) → 1.0 (no false trigger)."""
    assert _devanagari_ratio("123 ... ! 456") == 1.0


def test_devanagari_ratio_all_latin():
    """All Latin → ratio 0.0."""
    assert _devanagari_ratio("hello world") == 0.0


# ── translate_to_devanagari bounded re-translation tests ──────────────────


def _make_director(config_override=None):
    """Build a minimal DirectorAgent with mocked LLM for testing."""
    from agents.director_agent import DirectorAgent

    cfg = {
        "models": {
            "director": "test-model",
            "translator": "test-translator",
        },
        "ollama": {"host": "http://localhost:11434"},
        "tts": {
            "devanagari": {
                "max_latin_ratio": 0.10,
                "max_retranslate_retries": 2,
            }
        },
        "characters": {},
    }
    if config_override:
        cfg.update(config_override)
    agent = DirectorAgent.__new__(DirectorAgent)
    agent.llm_config = cfg
    agent.config = cfg
    return agent


_CLEAN_DEVA = "यह एक परीक्षण है जो पूरी तरह देवनागरी में है।"  # ~100% Devanagari
# Latin-heavy but has enough Devanagari chars (>10) to pass the early guard,
# yet a high Latin ratio to trigger the re-translation loop.
_LATIN_HEAVY = (
    "यह " + "Latin text here " * 8 + "और यह भी देवनागरी है।"
)  # ~10 Deva chars, mostly Latin


def test_no_retranslate_when_clean():
    """Clean Devanagari on first try → _call_ollama_chat called exactly once."""
    agent = _make_director()
    plan = {"mood": "calm", "title": "T", "key_event": "E"}

    with patch.object(agent, "_call_ollama_chat", return_value=_CLEAN_DEVA) as mock_llm:
        result = agent.translate_to_devanagari("Hello world.", plan)

    assert result == _CLEAN_DEVA
    assert mock_llm.call_count == 1


def test_retranslate_triggers_on_latin_heavy():
    """Latin-heavy first result → retry fires; clean second result is returned."""
    agent = _make_director()
    plan = {"mood": "action", "title": "T", "key_event": "E"}

    # First call returns Latin-heavy; second returns clean Devanagari
    with patch.object(
        agent, "_call_ollama_chat", side_effect=[_LATIN_HEAVY, _CLEAN_DEVA]
    ) as mock_llm:
        result = agent.translate_to_devanagari("Hello world.", plan)

    assert result == _CLEAN_DEVA
    assert mock_llm.call_count == 2


def test_retry_cap_respected():
    """Always Latin-heavy → retries capped at max_retranslate_retries; no crash."""
    agent = _make_director()
    plan = {"mood": "horror", "title": "T", "key_event": "E"}
    max_retries = 2

    # All calls return Latin-heavy
    with patch.object(agent, "_call_ollama_chat", return_value=_LATIN_HEAVY) as mock_llm:
        result = agent.translate_to_devanagari("Hello world.", plan)

    # 1 initial call + max_retries retries
    assert mock_llm.call_count == 1 + max_retries
    # Still returns a string (best-effort, no crash)
    assert isinstance(result, str)
    assert len(result) > 0


def test_best_result_kept():
    """When retry improves ratio but not to threshold, best candidate is kept."""
    agent = _make_director()
    plan = {"mood": "dramatic", "title": "T", "key_event": "E"}

    # _medium: more Devanagari than _LATIN_HEAVY but still below threshold
    _medium = "यह some Latin mixed देवनागरी text here और भी कुछ है।"
    with patch.object(
        agent, "_call_ollama_chat", side_effect=[_LATIN_HEAVY, _medium, _LATIN_HEAVY]
    ):
        result = agent.translate_to_devanagari("Hello world.", plan)

    # Should keep _medium (better ratio than _LATIN_HEAVY)
    assert result == _medium


def test_empty_translation_signals_failure():
    """Empty translation signals the caller to use its English fallback."""
    agent = _make_director()
    plan = {"mood": "calm", "title": "T", "key_event": "E"}

    with patch.object(agent, "_call_ollama_chat", return_value=""):
        result = agent.translate_to_devanagari("Hello world.", plan)

    assert result is None


def test_exception_signals_failure():
    """LLM exception signals the caller to use its English fallback."""
    agent = _make_director()
    plan = {"mood": "calm", "title": "T", "key_event": "E"}

    with patch.object(agent, "_call_ollama_chat", side_effect=RuntimeError("LLM down")):
        result = agent.translate_to_devanagari("Hello world.", plan)

    assert result is None
