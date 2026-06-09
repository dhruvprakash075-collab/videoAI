"""test_sanitize_meta.py - W3: _sanitize_narration strips meta-commentary."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def _sanitize(text: str) -> str:
    from core.pipeline_long import _sanitize_narration

    return _sanitize_narration(text)


# ---------------------------------------------------------------------------
# Meta-commentary removal
# ---------------------------------------------------------------------------


def test_removes_in_response_to_critique():
    raw = "In response to your critique, I have revised the script. The hero walked forward."
    result = _sanitize(raw)
    assert "In response to" not in result
    assert "The hero walked forward." in result


def test_removes_this_version_aims():
    raw = "This version aims to capture the emotional depth. The ancient temple stood silent."
    result = _sanitize(raw)
    assert "This version aims" not in result
    assert "The ancient temple stood silent." in result


def test_removes_revised_script_label():
    raw = "Revised Script:\n\nThe hero arrived at dawn."
    result = _sanitize(raw)
    assert "Revised Script" not in result
    assert "The hero arrived at dawn." in result


def test_removes_heres_the_revised():
    raw = "Here's the revised script:\n\nShadows fell across the valley."
    result = _sanitize(raw)
    assert "Here's the revised" not in result
    assert "Shadows fell across the valley." in result


def test_removes_changes_reflect():
    raw = "The changes reflect the Director's feedback. The journey continued."
    result = _sanitize(raw)
    assert "The changes reflect" not in result
    assert "The journey continued." in result


def test_removes_end_of_text_token():
    raw = "The story ends here. [END_OF_TEXT]"
    result = _sanitize(raw)
    assert "[END_OF_TEXT]" not in result
    assert "The story ends here." in result


def test_removes_bold_markers():
    raw = "**The hero** stood tall and **faced the darkness**."
    result = _sanitize(raw)
    assert "**" not in result
    assert "The hero" in result
    assert "faced the darkness" in result


def test_removes_html_comments():
    raw = "The valley was quiet. <!-- Director note: add more tension --> The wind howled."
    result = _sanitize(raw)
    assert "<!--" not in result
    assert "Director note" not in result
    assert "The valley was quiet." in result
    assert "The wind howled." in result


def test_removes_html_tags():
    raw = "<section>The hero walked.</section> <span class='x'>Into the light.</span>"
    result = _sanitize(raw)
    assert "<section>" not in result
    assert "<span" not in result
    assert "The hero walked." in result
    assert "Into the light." in result


# ---------------------------------------------------------------------------
# Devanagari must be preserved
# ---------------------------------------------------------------------------


def test_devanagari_preserved():
    raw = "In response to your critique, I revised this. नायक आगे बढ़ा और अंधेरे का सामना किया।"
    result = _sanitize(raw)
    assert "नायक आगे बढ़ा" in result
    assert "In response to" not in result


def test_devanagari_only_unchanged():
    raw = "नायक ने मंदिर में प्रवेश किया। वहाँ एक रहस्यमय प्रकाश था।"
    result = _sanitize(raw)
    assert result.strip() == raw.strip()


# ---------------------------------------------------------------------------
# Real fixture from the actual pipeline run
# ---------------------------------------------------------------------------


def test_messy_fixture_cleaned():
    fixture = Path(__file__).parent / "fixtures" / "messy_writer_output.txt"
    if not fixture.exists():
        pytest.skip("messy_writer_output.txt fixture not found")
    raw = fixture.read_text(encoding="utf-8")
    result = _sanitize(raw)
    # Meta phrases must be gone
    assert "In response to your critique" not in result
    assert "The changes reflect" not in result
    assert "This version aims" not in result
    assert "[END_OF_TEXT]" not in result
    assert "<!--" not in result
    # Real narration must survive
    assert "ancient temple" in result or "hero" in result or "valley" in result


def test_reject_unsafe_narration_json_leftover():
    from core.pre_production import _reject_unsafe_narration

    assert _reject_unsafe_narration('{"narration": "hello"}') is None
    assert _reject_unsafe_narration('Hello world. {"segment": 1}') is None
    assert _reject_unsafe_narration("Hello world") == "Hello world"
    assert _reject_unsafe_narration("Short") is None  # < 10 chars


def test_reject_unsafe_narration_remaining_tags():
    from core.pre_production import _reject_unsafe_narration

    assert _reject_unsafe_narration("Hello [/narration] world") is None
    assert _reject_unsafe_narration("Hello [section] world") is None


def test_normalize_hindi_for_tts():
    from core.pre_production import _normalize_hindi_for_tts

    # ऋ → रि
    assert _normalize_hindi_for_tts("\u090b") == "\u0930\u093f"
    # ॠ → री
    assert _normalize_hindi_for_tts("\u0960") == "\u0930\u0940"
    # ऌ → लि
    assert _normalize_hindi_for_tts("\u090c") == "\u0932\u093f"
    # Normal text unchanged
    assert _normalize_hindi_for_tts("नमस्ते") == "नमस्ते"
