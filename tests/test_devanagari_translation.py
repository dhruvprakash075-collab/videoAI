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

# ponytail: skip bootstrap — venv guard calls sys.exit(1) outside venv.
# sys.path is already set above, tests don't need signal handlers.

from agents.director_agent import _devanagari_ratio
from agents.hinglish_glossary import protect_hinglish

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


def test_tts_awkward_words_not_glossary_protected():
    """Let translator choose natural Hindi for words that TTS mispronounces as loanwords."""
    protected, token_map = protect_hinglish("city light map truth")

    assert protected == "city light map truth"
    assert token_map == {}


def test_protect_hinglish_target_ratio_expands_content_words():
    protected, token_map = protect_hinglish(
        "The hero stepped onto the moonlit rooftop and listened quietly.",
        target_ratio=0.40,
    )

    assert len(token_map) >= 4
    assert len(token_map) / 9 >= 0.40
    assert "@@0@@" in protected


def test_glossary_covers_manga_domain_words():
    """Domain words that the naive mapper mangled now have curated spellings."""
    from agents.hinglish_glossary import transliterate_latin_runs

    assert transliterate_latin_runs("manga creation") == "मैंगा क्रिएशन"
    assert transliterate_latin_runs("early days") == "अर्ली डेज़"
    assert transliterate_latin_runs("industry standards") == "इंडस्ट्री स्टैंडर्ड"
    assert transliterate_latin_runs("comic panel style") == "कॉमिक पैनल स्टाइल"


def test_naive_mapper_orthography_rules():
    """Non-glossary words get the high-yield rules (tion, silent-e, ck, qu)."""
    from agents.hinglish_glossary import _roman_word_to_devanagari

    assert _roman_word_to_devanagari("fiction") == "फिक्शुन"
    assert _roman_word_to_devanagari("brave") == "ब्रव"  # silent-e stripped
    assert _roman_word_to_devanagari("trick") == "ट्रिक"


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
    """Latin-heavy first result is repaired locally instead of falling back."""
    agent = _make_director()
    plan = {"mood": "action", "title": "T", "key_event": "E"}

    with patch.object(agent, "_call_ollama_chat", return_value=_LATIN_HEAVY) as mock_llm:
        result = agent.translate_to_devanagari("Hello world.", plan)

    assert result
    assert _devanagari_ratio(result) >= 0.90
    assert mock_llm.call_count == 1


def test_translate_output_has_zero_latin_no_exemptions():
    """NO EXEMPTIONS: any non-None translation must be 100% Latin-free —
    every English word is transliterated into Devanagari before TTS."""
    import re

    agent = _make_director()
    plan = {"mood": "calm", "title": "T", "key_event": "E"}

    for candidate in (_CLEAN_DEVA, _LATIN_HEAVY):
        with patch.object(agent, "_call_ollama_chat", return_value=candidate):
            result = agent.translate_to_devanagari("Hello world.", plan)
        assert result is not None
        assert not re.search(r"[A-Za-z]", result), f"Latin leaked into TTS input: {result!r}"


def test_roman_hinglish_transliterated_for_hindi_tts():
    """Romanized Hindi from the translator is converted instead of falling back to English."""
    agent = _make_director()
    plan = {"mood": "action", "title": "T", "key_event": "E"}

    with patch.object(agent, "_call_ollama_chat", return_value="Arjun ne door khola"):
        result = agent.translate_to_devanagari("Arjun opened the door.", plan)

    assert result
    assert _devanagari_ratio(result) >= 0.90
    assert "Arjun" not in result


def test_retry_cap_respected():
    """Always Latin-heavy → local repair avoids fallback."""
    agent = _make_director()
    plan = {"mood": "horror", "title": "T", "key_event": "E"}
    with patch.object(agent, "_call_ollama_chat", return_value=_LATIN_HEAVY) as mock_llm:
        result = agent.translate_to_devanagari("Hello world.", plan)

    assert mock_llm.call_count == 1
    assert result
    assert _devanagari_ratio(result) >= 0.90


def test_below_threshold_best_result_repaired():
    """Mixed Roman/Hindi output is repaired for Hindi TTS."""
    agent = _make_director()
    plan = {"mood": "dramatic", "title": "T", "key_event": "E"}

    # _medium: more Devanagari than _LATIN_HEAVY but still below threshold
    _medium = "यह some Latin mixed देवनागरी text here और भी कुछ है।"
    with patch.object(
        agent, "_call_ollama_chat", side_effect=[_LATIN_HEAVY, _medium, _LATIN_HEAVY]
    ):
        result = agent.translate_to_devanagari("Hello world.", plan)

    assert result
    assert _devanagari_ratio(result) >= 0.90


def test_oversized_retranslate_candidate_rejected():
    """Strict retry prompt leakage must not replace the first failed translation."""
    agent = _make_director()
    plan = {"mood": "dramatic", "title": "T", "key_event": "E"}
    leaked = "निर्देश " * 200 + "real story"

    with patch.object(agent, "_call_ollama_chat", return_value=leaked):
        result = agent.translate_to_devanagari("Hello world.", plan)

    assert result is None


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
