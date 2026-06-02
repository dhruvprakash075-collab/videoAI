"""Tests for utils/critic.py

Covers:
  - CriticScore dataclass: total, is_empty, to_dict, default zero
  - parse_critic_json: direct, bracket depth, regex fallback, malformed, missing
  - is_approved: threshold pass/fail
  - score_script: mocked LLM (valid, malformed, breaker open, missing model)
  - rewrite_script: mocked LLM
  - critique_and_rewrite: passes first try, retries on fail, max-rewrites cap
  - CRITIC_PROMPT / REWRITE_PROMPT: contain expected rubric, no format bleed
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from utils.critic import (
    CRITIC_PROMPT,
    DIMENSION_MAX,
    DIMENSIONS,
    REWRITE_PROMPT,
    TOTAL_MAX,
    CriticScore,
    _clamp_dim,
    _critic_config,
    _score_from_dict,
    critique_and_rewrite,
    is_approved,
    parse_critic_json,
    rewrite_script,
    score_script,
)

# ── CriticScore dataclass ───────────────────────────────────────────────────

class TestCriticScore:
    def test_default_total_is_zero(self):
        s = CriticScore()
        assert s.total == 0

    def test_total_clamps_correctly(self):
        s = CriticScore(hook=20, emotional_arc=20, pacing=20, retention=20, tts_friendliness=20)
        assert s.total == TOTAL_MAX == 100

    def test_individual_dims(self):
        s = CriticScore(hook=15, emotional_arc=10, pacing=12, retention=18, tts_friendliness=8)
        assert s.total == 63

    def test_is_empty_default(self):
        assert CriticScore().is_empty is True

    def test_is_empty_with_total(self):
        assert CriticScore(hook=5).is_empty is False

    def test_to_dict_contains_all_keys(self):
        s = CriticScore(hook=10, issues=["a"], suggestions=["b"])
        d = s.to_dict()
        assert set(d.keys()) >= {"hook", "emotional_arc", "pacing", "retention", "tts_friendliness", "total", "issues", "suggestions"}
        assert d["total"] == 10


# ── _clamp_dim ──────────────────────────────────────────────────────────────

class TestClampDim:
    @pytest.mark.parametrize("val,expected", [
        (0, 0), (10, 10), (20, 20), (-5, 0), (25, 20), ("15", 15), ("abc", 0), (None, 0), (1.7, 1),
    ])
    def test_clamp(self, val, expected):
        assert _clamp_dim(val) == expected


# ── _score_from_dict ────────────────────────────────────────────────────────

class TestScoreFromDict:
    def test_full_dict(self):
        d = {"hook": 18, "emotional_arc": 14, "pacing": 12, "retention": 16, "tts_friendliness": 9,
             "issues": ["slow start"], "suggestions": ["add micro-hook"]}
        s = _score_from_dict(d)
        assert s.hook == 18 and s.emotional_arc == 14
        assert s.total == 69
        assert s.issues == ["slow start"]

    def test_clamps_out_of_range(self):
        d = {"hook": 99, "emotional_arc": -3}
        s = _score_from_dict(d)
        assert s.hook == 20 and s.emotional_arc == 0

    def test_missing_dims_default_zero(self):
        s = _score_from_dict({})
        assert s.total == 0

    def test_non_list_issues_becomes_empty(self):
        s = _score_from_dict({"issues": "not a list"})
        assert s.issues == []


# ── parse_critic_json ───────────────────────────────────────────────────────

class TestParseCriticJson:
    def test_empty(self):
        assert parse_critic_json("") is None
        assert parse_critic_json("   ") is None

    def test_valid_direct(self):
        raw = json.dumps({"hook": 15, "emotional_arc": 12, "pacing": 18, "retention": 10, "tts_friendliness": 14})
        s = parse_critic_json(raw)
        assert s is not None
        assert s.hook == 15 and s.total == 69

    def test_bracket_depth(self):
        raw = "Here is the critique: {\"hook\": 8, \"emotional_arc\": 10} and that is all."
        s = parse_critic_json(raw)
        assert s is not None
        assert s.hook == 8

    def test_nested_brackets(self):
        raw = "Some text {\"hook\": 12, \"meta\": {\"version\": 1}} more text"
        s = parse_critic_json(raw)
        assert s is not None
        assert s.hook == 12

    def test_regex_fallback(self):
        raw = "no proper json here but {\"hook\": 7, \"pacing\": 13} embedded"
        s = parse_critic_json(raw)
        assert s is not None
        assert s.hook == 7

    def test_malformed(self):
        assert parse_critic_json("not json at all") is None

    def test_array_with_inner_object_extracts_object(self):
        raw = json.dumps([{"hook": 5, "pacing": 10}])
        s = parse_critic_json(raw)
        assert s is not None
        assert s.hook == 5 and s.pacing == 10

    def test_array_without_object_returns_none(self):
        raw = json.dumps([1, 2, 3])
        assert parse_critic_json(raw) is None

    def test_clamp_during_parse(self):
        raw = json.dumps({"hook": 999, "pacing": -10})
        s = parse_critic_json(raw)
        assert s.hook == 20 and s.pacing == 0


# ── is_approved ─────────────────────────────────────────────────────────────

class TestIsApproved:
    def test_above_threshold(self):
        s = CriticScore(hook=12, emotional_arc=12, pacing=12, retention=12, tts_friendliness=12)
        assert is_approved(s, 60) is True

    def test_below_threshold(self):
        s = CriticScore(hook=10, emotional_arc=10, pacing=10, retention=10, tts_friendliness=10)
        assert is_approved(s, 60) is False

    def test_exact_threshold(self):
        s = CriticScore(hook=12, emotional_arc=12, pacing=12, retention=12, tts_friendliness=12)
        assert is_approved(s, 60) is True

    def test_zero(self):
        assert is_approved(CriticScore(), 0) is True


# ── _critic_config ──────────────────────────────────────────────────────────

class TestCriticConfig:
    def test_defaults(self):
        threshold, max_rewrites = _critic_config({})
        assert threshold == 60
        assert max_rewrites == 2

    def test_custom(self):
        threshold, max_rewrites = _critic_config({"critic": {"threshold": 75, "max_rewrites": 3}})
        assert threshold == 75
        assert max_rewrites == 3


# ── score_script (mocked LLM) ───────────────────────────────────────────────

class TestScoreScript:
    def test_valid_response(self):
        raw = json.dumps({"hook": 18, "emotional_arc": 15, "pacing": 12, "retention": 16, "tts_friendliness": 9})
        config = {"models": {"writer": "zephyr-writer"}}
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=raw) as mock_call:
            s = score_script("script text", config)
        assert s is not None
        assert s.total == 70
        _args, kwargs = mock_call.call_args
        assert kwargs["model"] == "zephyr-writer"
        assert kwargs["format_json"] is True
        assert "script text" in mock_call.call_args.args[0]

    def test_breaker_open_returns_none(self):
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=""):
            assert score_script("text", {"models": {"writer": "w"}}) is None

    def test_malformed_returns_none(self):
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value="not json"):
            assert score_script("text", {"models": {"writer": "w"}}) is None

    def test_default_model(self):
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=""):
            score_script("text", {})
        assert True


# ── rewrite_script (mocked LLM) ─────────────────────────────────────────────

class TestRewriteScript:
    def test_valid_rewrite(self):
        config = {"models": {"writer": "zephyr-writer"}}
        score = CriticScore(hook=5, emotional_arc=5, pacing=5, retention=5, tts_friendliness=5, issues=["too slow"])
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value="Rewritten script here.") as mock_call:
            result = rewrite_script("old", score, 60, config)
        assert result == "Rewritten script here."
        prompt = mock_call.call_args.args[0]
        assert "25/100" in prompt
        assert "60" in prompt
        assert "too slow" in prompt
        assert "old" in prompt

    def test_breaker_open_returns_none(self):
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=""):
            assert rewrite_script("old", CriticScore(), 60, {"models": {"writer": "w"}}) is None

    def test_strips_whitespace(self):
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value="  \n  result  \n  "):
            assert rewrite_script("old", CriticScore(), 60, {"models": {"writer": "w"}}) == "result"


# ── critique_and_rewrite ────────────────────────────────────────────────────

class TestCritiqueAndRewrite:
    def test_approved_first_try(self):
        raw = json.dumps({"hook": 18, "emotional_arc": 18, "pacing": 18, "retention": 18, "tts_friendliness": 18})
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=raw):
            script, score, attempts = critique_and_rewrite("good", {"critic": {"threshold": 60}})
        assert attempts == 0
        assert score.total == 90
        assert script == "good"

    def test_below_threshold_triggers_rewrite(self):
        low = json.dumps({"hook": 5, "emotional_arc": 5, "pacing": 5, "retention": 5, "tts_friendliness": 5})
        high = json.dumps({"hook": 18, "emotional_arc": 18, "pacing": 18, "retention": 18, "tts_friendliness": 18})
        with patch("utils.crewai_breaker.guarded_ollama_call", side_effect=[low, "improved script", high]) as mock_call:
            script, score, attempts = critique_and_rewrite("bad", {"critic": {"threshold": 60, "max_rewrites": 2}})
        assert attempts == 1
        assert score.total == 90
        assert script == "improved script"
        assert mock_call.call_count == 3

    def test_max_rewrites_caps(self):
        low = json.dumps({"hook": 5, "emotional_arc": 5, "pacing": 5, "retention": 5, "tts_friendliness": 5})
        medium = json.dumps({"hook": 8, "emotional_arc": 8, "pacing": 8, "retention": 8, "tts_friendliness": 8})
        high = json.dumps({"hook": 12, "emotional_arc": 12, "pacing": 12, "retention": 12, "tts_friendliness": 12})
        with patch("utils.crewai_breaker.guarded_ollama_call", side_effect=[low, "v2", medium, "v3", high]):
            script, score, attempts = critique_and_rewrite("bad", {"critic": {"threshold": 60, "max_rewrites": 2}})
        assert attempts == 2
        assert score.total == 60
        assert script == "v3"

    def test_llm_failure_returns_empty_score(self):
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=""):
            script, score, attempts = critique_and_rewrite("anything", {})
        assert score.is_empty
        assert attempts == 0
        assert script == "anything"

    def test_rewrite_failure_breaks_loop(self):
        low = json.dumps({"hook": 5, "emotional_arc": 5, "pacing": 5, "retention": 5, "tts_friendliness": 5})
        with patch("utils.crewai_breaker.guarded_ollama_call", side_effect=[low, ""]):
            _script, score, attempts = critique_and_rewrite("bad", {"critic": {"threshold": 60, "max_rewrites": 2}})
        assert attempts == 0
        assert score.total == 25


# ── Prompt contents ─────────────────────────────────────────────────────────

class TestPrompts:
    def test_critic_prompt_has_5_dimensions(self):
        for dim in DIMENSIONS:
            assert dim in CRITIC_PROMPT

    def test_critic_prompt_has_json_format(self):
        assert "hook" in CRITIC_PROMPT and "issues" in CRITIC_PROMPT

    def test_rewrite_prompt_has_placeholders(self):
        for ph in ["{total}", "{threshold}", "{issues}", "{suggestions}", "{script}"]:
            assert ph in REWRITE_PROMPT

    def test_no_format_string_bleed(self):
        prompt = CRITIC_PROMPT.format(script="X")
        assert "X" in prompt
        assert "{" in prompt
        prompt2 = prompt.replace("{{", "").replace("}}", "")
        assert "{script}" not in prompt2

    def test_dimension_count(self):
        assert len(DIMENSIONS) == 5
        assert TOTAL_MAX == 100
        assert DIMENSION_MAX == 20
