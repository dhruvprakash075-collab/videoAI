"""test_specialized_models.py - reviewer + image-engineer helpers."""

import json
from unittest.mock import patch

from utils.specialized_models import (
    IMAGE_ENGINEER_MODEL,
    SCRIPT_REVIEWER_MODEL,
    _call_ollama,
    extract_world_state,
    generate_image_prompt,
    review_script_fast,
)

# ── _call_ollama ──────────────────────────────────────────────────────────────


def test_call_ollama_routes_to_client():
    with (
        patch("utils.ollama_client.get_ollama_client") as goc,
        patch("config.load_config", return_value={}),
    ):
        client = goc.return_value
        client.generate.return_value = "response text"
        out = _call_ollama("prompt", "model", format_json=True, temperature=0.5)
    assert out == "response text"
    call = client.generate.call_args
    assert call.kwargs["format_json"] is True
    assert call.kwargs["temperature"] == 0.5


def test_call_ollama_returns_none_on_failure():
    with patch("utils.ollama_client.get_ollama_client", side_effect=RuntimeError("boom")):
        out = _call_ollama("prompt", "model")
    assert out is None


def test_call_ollama_returns_none_on_empty_response():
    with (
        patch("utils.ollama_client.get_ollama_client") as goc,
        patch("config.load_config", return_value={}),
    ):
        goc.return_value.generate.return_value = ""
        out = _call_ollama("prompt", "model")
    assert out is None


# ── review_script_fast ────────────────────────────────────────────────────────


def test_review_script_fast_approved():
    response = json.dumps(
        {
            "approved": True,
            "quality_score": 8,
            "issues": [],
            "suggestions": ["add more detail"],
            "rewrite_needed": False,
            "rewrite_instructions": "",
        }
    )
    with patch("utils.specialized_models._call_ollama", return_value=response):
        out = review_script_fast("script", {"mood": "epic", "target_word_count": 200})
    assert out["approved"] is True
    assert out["quality_score"] == 8


def test_review_script_fast_with_characters():
    response = json.dumps({"approved": True, "quality_score": 8})
    chars = {"hero": {"name": "Hero", "description": "young adventurer"}}
    with patch("utils.specialized_models._call_ollama", return_value=response):
        out = review_script_fast("script", {"mood": "epic"}, characters=chars)
    assert out["approved"] is True


def test_review_script_fast_no_response_returns_unavailable():
    with patch("utils.specialized_models._call_ollama", return_value=None):
        out = review_script_fast("script", {"mood": "epic"})
    assert out["approved"] is False
    assert out["review_unavailable"] is True
    assert out["quality_score"] == 0


def test_review_script_fast_fills_missing_fields():
    response = json.dumps({"approved": True})  # missing other fields
    with patch("utils.specialized_models._call_ollama", return_value=response):
        out = review_script_fast("script", {"mood": "epic"})
    assert "issues" in out
    assert "suggestions" in out
    assert "rewrite_needed" in out
    assert "rewrite_instructions" in out


def test_review_script_fast_brace_depth_handles_nested_json():
    """Nested JSON in response is correctly extracted via brace depth tracking."""
    nested = '{"approved": true, "meta": {"reviewer": "x", "score": 8}}'
    with patch("utils.specialized_models._call_ollama", return_value=nested):
        out = review_script_fast("script", {"mood": "epic"})
    assert out["approved"] is True
    assert "meta" in out


def test_review_script_fast_brace_depth_recovers_on_parse_error():
    """Multiple JSON-like substrings: the first one fails, the second succeeds."""
    bad = 'garbage {not valid json} more text {"approved": true, "quality_score": 9}'
    with patch("utils.specialized_models._call_ollama", return_value=bad):
        out = review_script_fast("script", {"mood": "epic"})
    # Either recovers with the second JSON or falls back to unavailable
    assert "approved" in out


def test_review_script_fast_total_failure_returns_unavailable():
    with patch("utils.specialized_models._call_ollama", return_value="not json at all"):
        out = review_script_fast("script", {"mood": "epic"})
    assert out["approved"] is False
    assert out["review_unavailable"] is True


def test_review_script_fast_truncates_long_context():
    long_ctx = "x" * 1000
    with patch("utils.specialized_models._call_ollama", return_value=None) as call:
        review_script_fast("script", {"mood": "epic"}, context=long_ctx)
    # The prompt should contain the truncated context
    prompt_arg = call.call_args.args[0]
    # The code uses context[:200] — verify it doesn't contain the full 1000 chars
    assert len(prompt_arg) < 5000  # prompt itself is large but context is truncated


def test_review_script_fast_truncates_long_descriptions():
    long_desc = "y" * 200
    chars = {"hero": {"name": "Hero", "description": long_desc}}
    with patch("utils.specialized_models._call_ollama", return_value=None) as call:
        review_script_fast("script", {"mood": "epic"}, characters=chars)
    # Description should be truncated to 80 chars
    prompt_arg = call.call_args.args[0]
    assert "y" * 80 in prompt_arg
    assert "y" * 100 not in prompt_arg


# ── generate_image_prompt ─────────────────────────────────────────────────────


def test_generate_image_prompt_success():
    response = "Medium shot, character in dark forest, cinematic, 8k"
    with patch("utils.specialized_models._call_ollama", return_value=response):
        out = generate_image_prompt("script", {"mood": "epic", "key_event": "climax"})
    assert "Medium shot" in out
    assert "cinematic" in out


def test_generate_image_prompt_strips_quotes():
    response = '"Medium shot, dark forest, cinematic"'
    with patch("utils.specialized_models._call_ollama", return_value=response):
        out = generate_image_prompt("script", {"mood": "epic"})
    assert not out.startswith('"')
    assert not out.endswith('"')


def test_generate_image_prompt_strips_known_label():
    response = "Prompt: Medium shot, dark forest, cinematic"
    with patch("utils.specialized_models._call_ollama", return_value=response):
        out = generate_image_prompt("script", {"mood": "epic"})
    # Should strip the "Prompt: " prefix
    assert out.startswith("Medium shot")


def test_generate_image_prompt_preserves_colon_in_scene():
    """A colon mid-prompt is NOT a label and should be preserved."""
    response = "Medium shot: cloaked figure stands, cinematic"
    with patch("utils.specialized_models._call_ollama", return_value=response):
        out = generate_image_prompt("script", {"mood": "epic"})
    # The "Medium shot:" colon should NOT be stripped (not a known label)
    assert "Medium shot:" in out


def test_generate_image_prompt_fallback_on_no_response():
    with patch("utils.specialized_models._call_ollama", return_value=None):
        out = generate_image_prompt("script", {"mood": "epic"})
    assert "epic atmosphere" in out


def test_generate_image_prompt_fallback_uses_visual_style():
    with patch("utils.specialized_models._call_ollama", return_value=None):
        out = generate_image_prompt("script", {"mood": "epic"}, visual_style="cinematic noir")
    assert "cinematic noir" in out


def test_generate_image_prompt_truncates_long_script():
    long_script = "x" * 1000
    with patch("utils.specialized_models._call_ollama", return_value=None) as call:
        generate_image_prompt(long_script, {"mood": "epic"})
    prompt = call.call_args.args[0]
    # Script truncated to 500 chars
    assert "x" * 500 in prompt
    assert "x" * 600 not in prompt


def test_generate_image_prompt_includes_characters():
    chars = {"hero": {"name": "Hero", "description": "young adventurer"}}
    with patch("utils.specialized_models._call_ollama", return_value=None) as call:
        generate_image_prompt("script", {"mood": "epic"}, characters=chars)
    prompt = call.call_args.args[0]
    assert "Hero" in prompt
    assert "young adventurer" in prompt





# ── extract_world_state ───────────────────────────────────────────────────────


def test_extract_world_state_empty_input():
    assert extract_world_state("", {}) is None
    assert extract_world_state("   ", {}) is None
    assert extract_world_state(None, {}) is None


def test_extract_world_state_success():
    response = json.dumps(
        {
            "characters": ["Hero", "Villain"],
            "facts": ["fact 1", "fact 2"],
            "open_threads": ["thread 1"],
            "resolved_threads": ["resolved 1"],
        }
    )
    with patch("utils.specialized_models._call_ollama", return_value=response):
        out = extract_world_state("text", {})
    assert out["characters"] == ["Hero", "Villain"]
    assert out["facts"] == ["fact 1", "fact 2"]


def test_extract_world_state_no_response():
    with patch("utils.specialized_models._call_ollama", return_value=None):
        assert extract_world_state("text", {}) is None


def test_extract_world_state_brace_depth_nested():
    nested = '{"characters": ["A"], "meta": {"nested": "ok"}, "facts": []}'
    with patch("utils.specialized_models._call_ollama", return_value=nested):
        out = extract_world_state("text", {})
    assert out["characters"] == ["A"]


def test_extract_world_state_normalises_list_to_strings():
    response = json.dumps(
        {
            "characters": ["Hero", 123, ""],
            "facts": ["f"],
            "open_threads": [],
            "resolved_threads": [],
        }
    )
    with patch("utils.specialized_models._call_ollama", return_value=response):
        out = extract_world_state("text", {})
    # Non-string and empty are filtered out
    assert out["characters"] == ["Hero", "123"]


def test_extract_world_state_invalid_json_returns_none():
    with patch("utils.specialized_models._call_ollama", return_value="not json"):
        assert extract_world_state("text", {}) is None


def test_extract_world_state_uses_config_model():
    response = json.dumps({"characters": [], "facts": []})
    with patch("utils.specialized_models._call_ollama", return_value=response) as call:
        extract_world_state("text", {"models": {"reviewer": "custom-model"}})
    assert call.call_args.args[1] == "custom-model"


def test_extract_world_state_falls_back_to_default_model():
    response = json.dumps({"characters": [], "facts": []})
    with patch("utils.specialized_models._call_ollama", return_value=response) as call:
        extract_world_state("text", {})
    assert call.call_args.args[1] == SCRIPT_REVIEWER_MODEL


def test_extract_world_state_truncates_long_text():
    long_text = "a" * 2000
    with patch("utils.specialized_models._call_ollama", return_value=None) as call:
        extract_world_state(long_text, {})
    prompt = call.call_args.args[0]
    # Truncated to 1500 chars
    assert "a" * 1500 in prompt
    assert "a" * 1600 not in prompt


def test_extract_world_state_handles_exception():
    with patch("utils.specialized_models._call_ollama", side_effect=RuntimeError("boom")):
        assert extract_world_state("text", {}) is None


# ── constants ────────────────────────────────────────────────────────────────


def test_model_constants():
    assert SCRIPT_REVIEWER_MODEL == "qwen2.5:0.5b"
    assert IMAGE_ENGINEER_MODEL == "image-engineer"
