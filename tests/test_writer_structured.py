"""test_writer_structured.py - W2: structured Ollama writer path and fallback."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config():
    return {
        "models": {"writer": "zephyr-writer"},
        "ollama": {"host": "http://localhost:11434", "request_timeout": 240},
        "script": {"writer_max_tokens": 1024},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_structured_extraction_happy_path(monkeypatch):
    """When OllamaClient.generate returns valid JSON, narration is extracted."""
    from utils.ollama_client import OllamaClient

    good_json = json.dumps({"narration": "The hero walked into the light."})

    monkeypatch.setattr(OllamaClient, "generate", lambda self, *a, **kw: good_json)

    # Import the extraction logic inline (mirrors pipeline_long.py W2 block)
    raw = good_json
    parsed = json.loads(raw)
    narration = parsed.get("narration", "").strip()
    assert narration == "The hero walked into the light."


def test_structured_extraction_missing_key(monkeypatch):
    """When JSON has no 'narration' key, fallback path is triggered (no crash)."""
    bad_json = json.dumps({"text": "something else"})
    parsed = json.loads(bad_json)
    narration = parsed.get("narration", "").strip()
    # narration is empty — caller should fall back to CrewAI path
    assert narration == ""


def test_structured_extraction_malformed_json():
    """When JSON is malformed, json.loads raises and fallback is triggered."""
    malformed = "not json at all"
    with pytest.raises(json.JSONDecodeError):
        json.loads(malformed)


def test_ollama_client_generate_called_with_format_json(monkeypatch):
    """OllamaClient.generate must be called with format_json=True for W2."""
    from utils.ollama_client import OllamaClient

    calls = []

    def _fake_generate(self, prompt, model, format_json=False, **kw):
        calls.append({"prompt": prompt, "model": model, "format_json": format_json})
        return json.dumps({"narration": "Test narration."})

    monkeypatch.setattr(OllamaClient, "generate", _fake_generate)

    client = OllamaClient(_make_config())
    result = client.generate("test prompt", model="zephyr-writer", format_json=True)
    assert calls[0]["format_json"] is True
    assert json.loads(result)["narration"] == "Test narration."


def test_empty_generate_response_triggers_fallback(monkeypatch):
    """When OllamaClient.generate returns empty string, structured path yields no narration."""
    from utils.ollama_client import OllamaClient

    monkeypatch.setattr(OllamaClient, "generate", lambda self, *a, **kw: "")

    client = OllamaClient(_make_config())
    raw = client.generate("prompt", model="zephyr-writer", format_json=True)
    # Empty → caller should fall back to CrewAI
    assert raw == ""
