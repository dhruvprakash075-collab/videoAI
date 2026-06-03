"""test_config.py - Unit tests for config/config.py"""

from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from config.config import _safe_filename, dict_merge, get_character, load_config


def test_safe_filename():
    assert _safe_filename("hello world") == "hello_world"
    assert _safe_filename("...#$@") == "_"
    assert _safe_filename("a" * 100) == "a" * 80


def test_dict_merge():
    d1 = {"a": 1, "b": {"c": 2}}
    d2 = {"b": {"d": 3}, "e": 4}
    res = dict_merge(d1, d2)
    assert res == {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}


def test_load_config_defaults_only():
    with (
        patch.object(Path, "exists", return_value=False),
        patch("logging.Logger.warning") as mock_warning,
    ):
        cfg = load_config(Path("non_existent_config.yaml"))
        mock_warning.assert_called_with("config.yaml missing — using defaults")
        assert "models" in cfg


def test_load_config_with_project_exists():
    def side_effect_exists(self):
        if "test_project.yaml" in str(self):
            return True
        return "config.yaml" in str(self)

    mock_yaml_load = MagicMock(return_value={"models": {"director": "custom-director"}})

    with (
        patch.object(Path, "exists", side_effect_exists),
        patch("builtins.open", mock_open(read_data="models:\n  director: custom-director")),
        patch("yaml.safe_load", mock_yaml_load),
    ):
        cfg = load_config(Path("config.yaml"), project_name="test_project")
        assert cfg["models"]["director"] == "custom-director"


def test_load_config_project_missing():
    def side_effect_exists(self):
        return "config.yaml" in str(self)

    with (
        patch.object(Path, "exists", side_effect_exists),
        patch("builtins.open", mock_open(read_data="")),
        patch("logging.Logger.warning") as mock_warning,
    ):
        cfg = load_config(Path("config.yaml"), project_name="missing_project")
        # should warn about missing project
        mock_warning.assert_any_call(
            "Project configuration not found: projects\\missing_project.yaml"
        )
        assert "models" in cfg


def test_load_config_validation_failure():
    with (
        patch("config.config.validate_config", side_effect=ValueError("validation error")),
        patch("logging.Logger.warning") as mock_warn,
    ):
        cfg = load_config(Path("non_existent.yaml"))
        assert "models" in cfg
        mock_warn.assert_any_call(
            "Configuration validation failed: validation error — falling back to raw configuration"
        )


def test_get_character_success():
    cfg = {"characters": {"hero": {"name": "Hero Description"}}}
    assert get_character(cfg, "hero") == {"name": "Hero Description"}


def test_get_character_missing_characters():
    with pytest.raises(ValueError, match=r"config\.yaml missing 'characters'"):
        get_character({}, "hero")


def test_get_character_fallback():
    cfg = {"characters": {"first_char": {"name": "First"}, "second_char": {"name": "Second"}}}
    with patch("logging.Logger.warning") as mock_warn:
        res = get_character(cfg, "hero")
        assert res == {"name": "First"}
        mock_warn.assert_called_with(
            "Character 'hero' not found — using first. Available: ['first_char', 'second_char']"
        )
