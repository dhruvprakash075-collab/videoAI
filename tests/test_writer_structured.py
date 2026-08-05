"""test_writer_structured.py - Guards the W2 structured-writer extraction.

The W2 block in core.segment_runner asks the Ollama writer for a JSON reply and
pulls the "narration" key. These tests exercise the real production helper so a
broken extraction (wrong key, malformed JSON) fails CI instead of silently
falling back to CrewAI in production.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.segment_runner import _extract_structured_narration


def test_extracts_narration_from_valid_json():
    assert _extract_structured_narration('{"narration": "Hello world"}') == "Hello world"


def test_extracts_narration_with_surrounding_whitespace():
    assert _extract_structured_narration('{"narration": "  Trimmed  "}') == "Trimmed"


def test_returns_empty_when_narration_key_missing():
    assert _extract_structured_narration('{"script": "no narration key"}') == ""


def test_returns_empty_when_narration_is_whitespace():
    assert _extract_structured_narration('{"narration": "   "}') == ""


def test_raises_on_malformed_json():
    with pytest.raises(json.JSONDecodeError):
        _extract_structured_narration("this is not json")


def test_raises_on_non_object_payload():
    with pytest.raises(TypeError):
        _extract_structured_narration("[1, 2, 3]")
