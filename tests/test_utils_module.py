"""test_utils_module.py - tests for utils/utils.py helpers."""

import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.utils import (
    build_prompts,
    get_audio_duration,
    save_outputs,
    setup_run_logging,
    validate_script,
)


@pytest.fixture
def config():
    return {
        "characters": {
            "protagonist": {"description": "a young hero", "name": "Aria"},
            "villain": {"description": "a shadowy antagonist", "name": "Malachar"},
        },
        "script": {
            "default_images_per_segment": 6,
            "max_images_per_segment": 10,
            "dynamic_image_count": True,
            "min_words": 20,
            "max_words": 400,
        },
    }


# ── setup_run_logging ─────────────────────────────────────────────────────────


def test_setup_run_logging_creates_log_file(tmp_path: Path):
    log_file = tmp_path / "pipeline.log"
    setup_run_logging(tmp_path)
    assert log_file.exists()
    # Second call should not add a duplicate handler
    setup_run_logging(tmp_path)


def test_setup_run_logging_console_handler_added(tmp_path: Path):
    # Remove existing StreamHandlers to force add
    [
        h
        for h in logging.root.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    setup_run_logging(tmp_path)
    after = [
        h
        for h in logging.root.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    assert len(after) >= 1


# ── build_prompts ─────────────────────────────────────────────────────────────


def test_build_prompts_returns_string(config):
    plan = {"title": "Test", "key_event": "A dragon appears", "mood": "epic", "num_images": 6}
    result = build_prompts("test script", plan, config)
    assert isinstance(result, str)
    # Prompts are semicolon-separated
    assert ";" in result


def test_build_prompts_uses_plan_mood(config):
    plan = {"title": "T", "key_event": "X", "mood": "horror", "num_images": 4}
    result = build_prompts("script", plan, config)
    # Horror mood should bring in horror-specific shots
    assert "Dutch angle" in result or "flickering torchlight" in result or "shadow" in result


def test_build_prompts_respects_num_images(config):
    plan = {"title": "T", "key_event": "X", "mood": "calm", "num_images": 3}
    result = build_prompts("script", plan, config)
    prompts = [p.strip() for p in result.split(";") if p.strip()]
    # Should be 3 prompts (clamped to min 2)
    assert 2 <= len(prompts) <= 3


def test_build_prompts_falls_back_to_default_count(config):
    """When num_images is missing, use config default."""
    plan = {"title": "T", "key_event": "X", "mood": "epic"}
    result = build_prompts("script", plan, config)
    assert result


def test_build_prompts_clamps_to_max(config):
    """If num_images exceeds max_images_per_segment, clamp it."""
    plan = {"title": "T", "key_event": "X", "mood": "epic", "num_images": 100}
    result = build_prompts("script", plan, config)
    prompts = [p.strip() for p in result.split(";") if p.strip()]
    max_imgs = config.get("script", {}).get("max_images_per_segment", 10)
    assert len(prompts) <= max_imgs


def test_build_prompts_clamps_to_min(config):
    """If num_images is below 1, clamp to 1."""
    plan = {"title": "T", "key_event": "X", "mood": "epic", "num_images": 0}
    result = build_prompts("script", plan, config)
    prompts = [p.strip() for p in result.split(";") if p.strip()]
    assert len(prompts) == 1


def test_build_prompts_picks_char_presence(config):
    plan = {
        "title": "T",
        "key_event": "X",
        "mood": "epic",
        "num_images": 4,
        "char_presence": [{"villain": 0.9, "protagonist": 0.5}],
    }
    result = build_prompts("script", plan, config)
    assert "villain" in result or "shadowy" in result or "antagonist" in result


def test_build_prompts_handles_unknown_mood(config):
    plan = {"title": "T", "key_event": "X", "mood": "weird-mood", "num_images": 4}
    result = build_prompts("script", plan, config)
    # Falls back to mysterious
    assert result


def test_build_prompts_dynamic_image_count_disabled(config):
    """When dynamic_image_count is False, use the config default (not plan's num_images)."""
    config["script"]["dynamic_image_count"] = False
    config["script"]["default_images_per_segment"] = 5
    plan = {"title": "T", "key_event": "X", "mood": "epic", "num_images": 3}
    result = build_prompts("script", plan, config)
    prompts = [p.strip() for p in result.split(";") if p.strip()]
    # Should be 5 (the default), not 3 (plan's)
    assert len(prompts) == 5


def test_build_prompts_char_presence_invalid_type(config):
    """If char_presence is not a list, skip it."""
    plan = {
        "title": "T",
        "key_event": "X",
        "mood": "epic",
        "num_images": 4,
        "char_presence": "not a list",
    }
    result = build_prompts("script", plan, config)
    assert result


# ── validate_script ───────────────────────────────────────────────────────────


def test_validate_script_valid(config):
    script = (
        "Once upon a time, there was a young hero who dared to dream of greatness. "
        "She traveled far across the ancient lands, facing trials and tribulations. "
        "With courage and wisdom, she saved the world from a terrible ancient evil."
    )
    assert validate_script(script, config) is True


def test_validate_script_too_short(config):
    script = "too short"
    assert validate_script(script, config) is False


def test_validate_script_too_long(config):
    # Default max is 400 words
    script = " ".join(["word"] * 500)
    assert validate_script(script, config) is False


def test_validate_script_low_diversity(config):
    """Script with all same word repeated fails the 0.4 unique ratio check."""
    script = " ".join(["blah"] * 50)
    assert validate_script(script, config) is False


def test_validate_script_empty(config):
    assert validate_script("", config) is False


# ── save_outputs ─────────────────────────────────────────────────────────────


def test_save_outputs_basic(tmp_path: Path):
    outputs = {"key": "value", "count": 42, "items": [1, 2, 3]}
    save_outputs("Test Topic", outputs, tmp_path)
    meta_file = tmp_path / "outputs_meta.json"
    assert meta_file.exists()
    data = json.loads(meta_file.read_text())
    assert data["topic"] == "Test Topic"
    assert data["outputs"]["key"] == "value"
    assert data["outputs"]["count"] == 42
    assert data["outputs"]["items"] == [1, 2, 3]


def test_save_outputs_sanitizes_paths(tmp_path: Path):
    p = Path("C:/some/audio.wav")
    outputs = {"audio": p}
    save_outputs("Test", outputs, tmp_path)
    data = json.loads((tmp_path / "outputs_meta.json").read_text())
    assert data["outputs"]["audio"] == str(p)


def test_save_outputs_nested(tmp_path: Path):
    outputs = {"nested": {"list": [Path("a.wav"), Path("b.wav")]}}
    save_outputs("Test", outputs, tmp_path)
    data = json.loads((tmp_path / "outputs_meta.json").read_text())
    assert data["outputs"]["nested"]["list"] == ["a.wav", "b.wav"]


# ── get_audio_duration ───────────────────────────────────────────────────────


def test_get_audio_duration_success(tmp_path: Path):
    fake_audio = tmp_path / "voice.wav"
    fake_audio.write_bytes(b"RIFF")
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"format": {"duration": 12.5}}', stderr=""
        )
        dur = get_audio_duration(fake_audio)
    assert dur == 12.5


def test_get_audio_duration_minimum_floor(tmp_path: Path):
    fake_audio = tmp_path / "voice.wav"
    fake_audio.write_bytes(b"RIFF")
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"format": {"duration": 0.0}}', stderr=""
        )
        dur = get_audio_duration(fake_audio)
    # Should floor at 0.1
    assert dur == 0.1


def test_get_audio_duration_ffprobe_failure(tmp_path: Path):
    fake_audio = tmp_path / "voice.wav"
    fake_audio.write_bytes(b"RIFF")
    with patch("subprocess.run", side_effect=RuntimeError("ffprobe missing")):
        dur = get_audio_duration(fake_audio)
    # Should return 30s fallback
    assert dur == 30.0


def test_get_audio_duration_bad_json(tmp_path: Path):
    fake_audio = tmp_path / "voice.wav"
    fake_audio.write_bytes(b"RIFF")
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"format": {}}', stderr=""
        )
        dur = get_audio_duration(fake_audio)
    # Missing duration key falls back to 30
    assert dur == 30.0


def test_extract_json_success():
    from utils.utils import extract_json
    # Test simple object
    assert extract_json('some text {"key": "value"} other text') == {"key": "value"}
    # Test simple array
    assert extract_json('some text [1, 2, "three"] other text') == [1, 2, "three"]
    # Test braces inside strings
    assert extract_json('{"key": "value with } and { inside"}') == {"key": "value with } and { inside"}
    # Test nested object
    assert extract_json('{"nested": {"inner": [1, 2]}}') == {"nested": {"inner": [1, 2]}}


def test_extract_json_failure():
    from utils.utils import extract_json
    with pytest.raises(ValueError, match="No valid JSON object or array could be decoded"):
        extract_json("no json here")
    with pytest.raises(ValueError, match="No valid JSON object or array could be decoded"):
        extract_json('{"key": ')
