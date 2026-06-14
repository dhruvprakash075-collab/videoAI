"""test_config_helpers.py - Unit tests for deterministic config helpers."""


from config.config import _safe_filename, dict_merge, get_language


def test_safe_filename_basic():
    assert _safe_filename("hello world") == "hello_world"


def test_safe_filename_special_chars():
    assert _safe_filename("...#$@") == "_"


def test_safe_filename_truncation():
    assert len(_safe_filename("a" * 100)) == 80


def test_safe_filename_leading_dots():
    assert _safe_filename("._hello") == "hello"


def test_dict_merge_deep():
    d1 = {"a": 1, "b": {"c": 2, "d": [1]}}
    d2 = {"b": {"d": [2], "e": 3}, "f": 4}
    res = dict_merge(d1, d2)
    assert res == {"a": 1, "b": {"c": 2, "d": [2], "e": 3}, "f": 4}


def test_dict_merge_empty():
    assert dict_merge({"a": 1}, {}) == {"a": 1}
    assert dict_merge({}, {"a": 1}) == {"a": 1}


def test_get_language_top_level():
    assert get_language({"language": "en", "tts": {"lang": "hi"}}) == "en"


def test_get_language_fallback_to_tts():
    assert get_language({"tts": {"lang": "en"}}) == "en"


def test_get_language_default():
    assert get_language({}) == "hi"


def test_get_language_prefers_top_level():
    assert get_language({"language": "hi", "tts": {"lang": "en"}}) == "hi"
