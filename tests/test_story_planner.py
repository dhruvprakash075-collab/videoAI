"""test_story_planner.py - plan_story, _plan_batch, build_segment_prompt, _parse_outline, _default_outline."""

import json
from unittest.mock import MagicMock, patch

from utils import story_planner
from utils.story_planner import (
    SegmentPlan,
    StoryOutline,
    _default_outline,
    _parse_outline,
    build_segment_prompt,
    plan_story,
)

# ── _default_outline ──────────────────────────────────────────────────────────


def test_default_outline_basic():
    out = _default_outline("my topic", 3)
    assert len(out) == 3
    assert out[0]["seg"] == 1
    assert out[2]["seg"] == 3


def test_default_outline_uses_topic():
    out = _default_outline("specific topic xyz", 2)
    assert "specific topic xyz" in out[0]["summary"]


def test_default_outline_mood_rotates():
    out = _default_outline("t", 6)
    moods = [s["mood"] for s in out]
    # 6 segments should cover all 6 mood keys
    assert len(set(moods)) == 6


def test_default_outline_char_presence_length():
    out = _default_outline("t", 1)
    # num_images=6, char_presence should have 6 entries
    assert len(out[0]["char_presence"]) == 6


def test_default_outline_zero_segments():
    out = _default_outline("t", 0)
    assert out == []


# ── build_segment_prompt ──────────────────────────────────────────────────────


def test_build_segment_prompt_basic():
    plan = {"seg": 1, "title": "T", "summary": "S", "key_event": "K", "mood": "epic"}
    out = build_segment_prompt(plan, "ctx", 10, 200)
    assert "segment 1/10" in out
    assert "Title: T" in out
    assert "EXACTLY 200 words" in out


def test_build_segment_prompt_includes_world_state():
    plan = {"seg": 1, "title": "T", "summary": "S"}
    out = build_segment_prompt(plan, "ctx", 10, 200, world_state_block="WS_BLOCK")
    assert "WS_BLOCK" in out


def test_build_segment_prompt_includes_persona():
    plan = {"seg": 1}
    out = build_segment_prompt(plan, "ctx", 10, 200, narrator_persona="wise sage")
    assert "wise sage" in out
    assert "NARRATOR PERSONA" in out


def test_build_segment_prompt_no_character_descriptions():
    plan = {"seg": 1}
    out = build_segment_prompt(plan, "ctx", 10, 200)
    # Default: exclude character descriptions
    assert "Do NOT insert character visual descriptions" in out


def test_build_segment_prompt_with_character_descriptions():
    plan = {"seg": 1}
    out = build_segment_prompt(plan, "ctx", 10, 200, include_character_descriptions=True)
    assert "anchor their visual identity" in out


def test_build_segment_prompt_handles_missing_keys():
    plan = {}
    out = build_segment_prompt(plan, "ctx", 10, 200)
    assert "Untitled" in out
    assert "segment 1/10" in out


def test_build_segment_prompt_includes_narration_tags():
    plan = {"seg": 1}
    out = build_segment_prompt(plan, "ctx", 10, 200)
    assert "[narration]" in out
    assert "[/narration]" in out


def test_build_segment_prompt_includes_mood():
    plan = {"seg": 1, "mood": "horror"}
    out = build_segment_prompt(plan, "ctx", 10, 200)
    assert "Mood: horror" in out


def test_build_segment_prompt_default_mood():
    plan = {"seg": 1}
    out = build_segment_prompt(plan, "ctx", 10, 200)
    # Default mood is "mysterious"
    assert "Mood: mysterious" in out


# ── _parse_outline ────────────────────────────────────────────────────────────


def test_parse_outline_direct_json():
    raw = json.dumps([{"seg": 1, "title": "T1"}, {"seg": 2, "title": "T2"}])
    out = _parse_outline(raw, 2)
    assert len(out) == 2
    assert out[0]["title"] == "T1"


def test_parse_outline_too_far_off_returns_default():
    raw = json.dumps([{"seg": 1}])
    # 1 segment when expected 5 — too far off
    out = _parse_outline(raw, 5)
    # Falls back to default
    assert len(out) == 5


def test_parse_outline_within_tolerance():
    raw = json.dumps([{"seg": 1}, {"seg": 2}, {"seg": 3}])
    # expected 3, got 3 — within tolerance
    out = _parse_outline(raw, 3)
    assert len(out) == 3


def test_parse_outline_truncates_extras():
    raw = json.dumps([{"seg": 1}, {"seg": 2}, {"seg": 3}, {"seg": 4}])
    out = _parse_outline(raw, 2)
    assert len(out) == 2


def test_parse_outline_pads_shortfalls():
    """Padding happens in _plan_batch, not _parse_outline."""
    raw = json.dumps([{"seg": 1}])
    out = _parse_outline(raw, 3)
    # Within 2 of expected → returned as-is (1 item)
    assert len(out) == 1


def test_parse_outline_handles_markdown_codeblock():
    raw = "```json\n" + json.dumps([{"seg": 1}, {"seg": 2}]) + "\n```"
    out = _parse_outline(raw, 2)
    assert len(out) == 2


def test_parse_outline_handles_plain_markdown_codeblock():
    raw = "```\n" + json.dumps([{"seg": 1}, {"seg": 2}]) + "\n```"
    out = _parse_outline(raw, 2)
    assert len(out) == 2


def test_parse_outline_bracket_search():
    raw = "Some text before " + json.dumps([{"seg": 1}, {"seg": 2}]) + " after"
    out = _parse_outline(raw, 2)
    assert len(out) == 2


def test_parse_outline_nested_arrays():
    raw = json.dumps(
        [
            {"seg": 1, "char_presence": [{"a": 0.5}, {"b": 0.7}]},
            {"seg": 2, "char_presence": [{"c": 0.3}]},
        ]
    )
    out = _parse_outline(raw, 2)
    assert len(out[0]["char_presence"]) == 2


def test_parse_outline_total_failure_returns_default():
    out = _parse_outline("not json at all", 3)
    # Falls back to default outline
    assert len(out) == 3
    assert out[0]["seg"] == 1


def test_parse_outline_handles_empty_string():
    out = _parse_outline("", 3)
    assert len(out) == 3


# ── _plan_batch ───────────────────────────────────────────────────────────────


def _make_agent():
    agent = MagicMock()
    agent.llm.model = "test-model"
    return agent


def _patch_crewai():
    """Patch Crew and Task so they don't validate real agent types."""
    return [
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ]


def test_plan_batch_returns_json_dict_result():
    agent = _make_agent()
    result = MagicMock()
    result.json_dict = {"segments": [{"seg": 1, "title": "T1"}]}
    with (
        patch("utils.story_planner.guarded_crewai_kickoff", return_value=result),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        out = plan_story("topic", 1, {}, agent)
    assert len(out) == 1
    assert out[0]["title"] == "T1"


def test_plan_batch_returns_pydantic_result():
    agent = _make_agent()
    # result.pydantic.segments
    result = MagicMock(spec=["pydantic"])
    result.pydantic = StoryOutline(
        segments=[
            SegmentPlan(
                seg=1,
                title="T1",
                summary="S",
                key_event="K",
                mood="epic",
                num_images=4,
                char_presence=[{}],
                target_word_count=100,
                segment_duration=30.0,
            )
        ]
    )
    # No json_dict attribute
    del result.json_dict
    with (
        patch("utils.story_planner.guarded_crewai_kickoff", return_value=result),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        out = plan_story("topic", 1, {}, agent)
    assert len(out) == 1


def test_plan_batch_returns_raw_text_parsed():
    agent = _make_agent()
    result = MagicMock(spec=["raw"])
    del result.json_dict
    del result.pydantic
    result.raw = json.dumps([{"seg": 1, "title": "T"}])
    with (
        patch("utils.story_planner.guarded_crewai_kickoff", return_value=result),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        out = plan_story("topic", 1, {}, agent)
    assert len(out) == 1


def test_plan_batch_breaker_open_returns_default():
    from utils.crewai_breaker import BreakerOpen

    agent = _make_agent()
    with (
        patch(
            "utils.story_planner.guarded_crewai_kickoff",
            side_effect=BreakerOpen("test-model", 60.0),
        ),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        out = plan_story("topic", 3, {}, agent)
    assert len(out) == 3


def test_plan_batch_exception_returns_default():
    agent = _make_agent()
    with (
        patch("utils.story_planner.guarded_crewai_kickoff", side_effect=RuntimeError("boom")),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        out = plan_story("topic", 2, {}, agent)
    assert len(out) == 2


def test_plan_batch_empty_result_returns_default():
    agent = _make_agent()
    result = MagicMock()
    result.raw = ""
    del result.json_dict
    del result.pydantic
    with (
        patch("utils.story_planner.guarded_crewai_kickoff", return_value=result),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        out = plan_story("topic", 2, {}, agent)
    assert len(out) == 2


def test_plan_batch_pads_when_short():
    agent = _make_agent()
    result = MagicMock()
    result.json_dict = {"segments": [{"seg": 1, "title": "T1"}]}
    with (
        patch("utils.story_planner.guarded_crewai_kickoff", return_value=result),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        out = plan_story("topic", 3, {}, agent)
    assert len(out) == 3
    assert story_planner._default_outline_used is False


def test_plan_batch_truncates_when_long():
    agent = _make_agent()
    result = MagicMock()
    result.json_dict = {"segments": [{"seg": i, "title": f"T{i}"} for i in range(1, 6)]}
    with (
        patch("utils.story_planner.guarded_crewai_kickoff", return_value=result),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        out = plan_story("topic", 3, {}, agent)
    assert len(out) == 3


def test_plan_batch_uses_world_lore_config():
    agent = _make_agent()
    result = MagicMock()
    result.json_dict = {"segments": [{"seg": 1, "title": "T"}]}
    cfg = {"world_lore": {"description": "A dark world", "rules": ["No magic"]}}
    with (
        patch("utils.story_planner.guarded_crewai_kickoff", return_value=result),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        plan_story("topic", 1, cfg, agent)
    # Just verify no exception — world lore should be incorporated into the prompt


def test_plan_batch_uses_plot_threads():
    agent = _make_agent()
    result = MagicMock()
    result.json_dict = {"segments": [{"seg": 1, "title": "T"}]}
    cfg = {"active_plot_threads": ["Find the artifact", "Defeat villain"]}
    with (
        patch("utils.story_planner.guarded_crewai_kickoff", return_value=result),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        plan_story("topic", 1, cfg, agent)


# ── plan_story batching ───────────────────────────────────────────────────────


def test_plan_story_batching_large_n():
    agent = _make_agent()
    # 30 segments triggers batching (BATCH_SIZE=25)
    result_batch1 = MagicMock()
    result_batch1.json_dict = {
        "segments": [{"seg": i, "title": f"T{i}", "key_event": f"E{i}"} for i in range(1, 26)]
    }
    result_batch2 = MagicMock()
    result_batch2.json_dict = {
        "segments": [{"seg": i, "title": f"T{i}", "key_event": f"E{i}"} for i in range(26, 31)]
    }
    with (
        patch(
            "utils.story_planner.guarded_crewai_kickoff", side_effect=[result_batch1, result_batch2]
        ),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        out = plan_story("topic", 30, {}, agent)
    assert len(out) == 30
    assert out[0]["seg"] == 1
    assert out[29]["seg"] == 30


def test_plan_story_single_batch():
    agent = _make_agent()
    result = MagicMock()
    result.json_dict = {"segments": [{"seg": 1, "title": "T"}]}
    with (
        patch("utils.story_planner.guarded_crewai_kickoff", return_value=result),
        patch("utils.story_planner.Crew", MagicMock()),
        patch("utils.story_planner.Task", MagicMock()),
    ):
        out = plan_story("topic", 3, {}, agent)
    assert len(out) == 3


# ── Pydantic models ───────────────────────────────────────────────────────────


def test_segment_plan_validation():
    sp = SegmentPlan(
        seg=1,
        title="T",
        summary="S",
        key_event="K",
        mood="epic",
        num_images=4,
        char_presence=[{"a": 0.5}],
        target_word_count=100,
        segment_duration=30.0,
    )
    assert sp.seg == 1
    assert sp.title == "T"


def test_story_outline_validation():
    outline = StoryOutline(
        segments=[
            SegmentPlan(
                seg=1,
                title="T",
                summary="S",
                key_event="K",
                mood="epic",
                num_images=4,
                char_presence=[{"a": 0.5}],
                target_word_count=100,
                segment_duration=30.0,
            )
        ]
    )
    assert len(outline.segments) == 1
