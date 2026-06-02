"""test_world_state.py - Tests for B3: LLM world-state extraction."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch


def test_extract_world_state_parses_llm_json():
    """extract_world_state should parse a valid JSON response from the LLM."""
    from utils.specialized_models import extract_world_state

    fake_response = json.dumps({
        "characters": ["Arjun", "Priya"],
        "facts": ["The ancient temple cannot be entered after sunset"],
        "open_threads": ["Who stole the sacred scroll?"],
        "resolved_threads": [],
    })

    with patch("utils.specialized_models._call_ollama", return_value=fake_response):
        result = extract_world_state("Some script text", {"ollama": {}, "models": {}})

    assert result is not None
    assert "Arjun" in result["characters"]
    assert "Priya" in result["characters"]
    assert len(result["facts"]) == 1
    assert len(result["open_threads"]) == 1
    assert result["resolved_threads"] == []


def test_extract_world_state_returns_none_on_bad_json():
    """extract_world_state should return None when the LLM returns invalid JSON."""
    from utils.specialized_models import extract_world_state

    with patch("utils.specialized_models._call_ollama", return_value="not json at all"):
        result = extract_world_state("Some script", {"ollama": {}, "models": {}})

    assert result is None


def test_extract_world_state_returns_none_on_empty_response():
    """extract_world_state should return None when the LLM returns nothing."""
    from utils.specialized_models import extract_world_state

    with patch("utils.specialized_models._call_ollama", return_value=None):
        result = extract_world_state("Some script", {"ollama": {}, "models": {}})

    assert result is None


def test_world_state_update_uses_llm_when_enabled(tmp_path):
    """WorldState.update should use LLM extraction when memory.llm_world_state=True."""
    from memory.memory import WorldState

    ws = WorldState("test_topic", tmp_path)
    config = {"memory": {"llm_world_state": True}, "ollama": {}, "models": {}}
    plan = {"seg": 1, "mood": "mysterious", "title": "Test", "key_event": ""}

    fake_result = {
        "characters": ["Vikram"],
        "facts": ["The forest is cursed"],
        "open_threads": ["What lies beyond the river?"],
        "resolved_threads": [],
    }

    with patch("utils.specialized_models.extract_world_state", return_value=fake_result):
        ws.update("Vikram walked into the cursed forest.", plan, config=config)

    assert "Vikram" in ws._data["characters"]
    assert any("cursed" in f for f in ws._data["world_facts"])


def test_world_state_update_falls_back_to_regex_on_llm_failure(tmp_path):
    """WorldState.update should fall back to regex when LLM extraction fails."""
    from memory.memory import WorldState

    ws = WorldState("test_topic", tmp_path)
    config = {"memory": {"llm_world_state": True}, "ollama": {}, "models": {}}
    plan = {"seg": 1, "mood": "mysterious", "title": "Test", "key_event": ""}

    with patch("utils.specialized_models.extract_world_state", return_value=None):
        # Should not raise; regex fallback should run
        ws.update("Arjun discovered the ancient secret.", plan, config=config)

    # Regex should have found "Arjun" as a capitalized word
    assert "Arjun" in ws._data["characters"]


def test_world_state_update_regex_only_when_disabled(tmp_path):
    """WorldState.update should use regex only when llm_world_state=False."""
    from memory.memory import WorldState

    ws = WorldState("test_topic", tmp_path)
    config = {"memory": {"llm_world_state": False}}
    plan = {"seg": 1, "mood": "calm", "title": "Test", "key_event": ""}

    ws.update("Meera walked through the ancient forest.", plan, config=config)
    assert "Meera" in ws._data["characters"]
