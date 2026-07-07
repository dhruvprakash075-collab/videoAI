"""test_pre_production.py - tests for core/pre_production.py helpers."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.pre_production import (
    _deep_merge,
    _sanitize_narration,
    format_chapters_time,
    format_time_hms,
    get_video_duration,
    plan_outline,
)

# ── _deep_merge ──────────────────────────────────────────────────────────────


def test_deep_merge_simple_override():
    base = {"a": 1, "b": 2}
    override = {"b": 3, "c": 4}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": 3, "c": 4}


def test_deep_merge_nested_dicts():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    override = {"a": {"y": 20, "z": 30}}
    result = _deep_merge(base, override)
    assert result == {"a": {"x": 1, "y": 20, "z": 30}, "b": 3}


def test_deep_merge_lists_union():
    base = {"items": [1, 2, 3]}
    override = {"items": [3, 4, 5]}
    result = _deep_merge(base, override)
    # Lists are deduplicated, not replaced
    assert sorted(result["items"]) == [1, 2, 3, 4, 5]


def test_deep_merge_override_non_dict():
    """If either side is not a dict, override wins."""
    assert _deep_merge("a", "b") == "b"
    assert _deep_merge({"a": 1}, "x") == "x"
    assert _deep_merge([1, 2], [3, 4]) == [3, 4]


# ── _sanitize_narration ──────────────────────────────────────────────────────


def test_sanitize_narration_empty():
    assert _sanitize_narration("") == ""
    assert _sanitize_narration(None) == ""


def test_sanitize_narration_strips_think_tags():
    s = "<think>internal</think>Hello world."
    assert "<think>" not in _sanitize_narration(s)


def test_sanitize_narration_strips_xml_tags():
    s = "<answer>Hello</answer> world"
    assert "<answer>" not in _sanitize_narration(s)


def test_sanitize_narration_strips_html_comments():
    s = "Hello <!-- a comment --> world"
    assert "<!--" not in _sanitize_narration(s)


def test_sanitize_narration_strips_special_tokens():
    for tok in ["[END_OF_TEXT]", "[END]", "[STOP]"]:
        s = f"Hello {tok} world"
        assert tok not in _sanitize_narration(s)


def test_sanitize_narration_strips_bold():
    s = "**bold text** normal"
    out = _sanitize_narration(s)
    assert "**" not in out
    assert "bold text" in out


def test_sanitize_narration_strips_section_tags():
    for tag in ["[narration]", "[/narration]", "[section]", "[pause]", "[scene]", "[sfx:bang]"]:
        s = f"Hello {tag} world"
        out = _sanitize_narration(s)
        assert "[" not in out or "]" not in out


def test_sanitize_narration_strips_leading_labels():
    s = "Narration: Hello world"
    out = _sanitize_narration(s)
    assert not out.lower().startswith("narration:")


def test_sanitize_narration_removes_meta_commentary():
    """W3: LLMs sometimes self-reply 'In response to your feedback...'"""
    s = "In response to your feedback, I have made changes. The story continues..."
    out = _sanitize_narration(s)
    assert "In response to your feedback" not in out


def test_sanitize_narration_collapses_whitespace():
    s = "Hello    world\n\n\nfoo"
    out = _sanitize_narration(s)
    assert "  " not in out
    assert "\n" not in out


def test_sanitize_narration_removes_instruction_leak_and_word_break():
    s = (
        "The hero contin-\nued forward. "
        "mentor ####5815: They're not actually in the text I have to work with, "
        "you've introduced new characters and concepts. "
        "The lamp flickered."
    )
    out = _sanitize_narration(s)
    assert "continued forward" in out
    assert "not actually in the text" not in out
    assert "you've introduced" not in out
    assert "The lamp flickered." in out


# ── format_time_hms ──────────────────────────────────────────────────────────


def test_format_time_hms_seconds():
    assert format_time_hms(0) == "0s"
    assert format_time_hms(45) == "45s"


def test_format_time_hms_minutes():
    assert format_time_hms(60) == "1m 0s"
    assert format_time_hms(125) == "2m 5s"


def test_format_time_hms_hours():
    assert format_time_hms(3600) == "1h 0m 0s"
    assert format_time_hms(3725) == "1h 2m 5s"


# ── format_chapters_time ─────────────────────────────────────────────────────


def test_format_chapters_time_seconds():
    assert format_chapters_time(45) == "00:45"


def test_format_chapters_time_minutes():
    assert format_chapters_time(125) == "02:05"


def test_format_chapters_time_hours():
    assert format_chapters_time(3725) == "01:02:05"


# ── get_video_duration ───────────────────────────────────────────────────────


def test_get_video_duration_success(tmp_path: Path):
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"x")
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"format": {"duration": 120.5}}', stderr=""
        )
        dur = get_video_duration(mp4)
    assert dur == 120.5


def test_get_video_duration_ffprobe_failure(tmp_path: Path):
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"x")
    with patch("subprocess.run", side_effect=RuntimeError("ffprobe missing")):
        dur = get_video_duration(mp4)
    assert dur == 30.0


def test_get_video_duration_bad_json(tmp_path: Path):
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"x")
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        )
        dur = get_video_duration(mp4)
    assert dur == 30.0


# ── plan_outline ─────────────────────────────────────────────────────────────


def test_plan_outline_resumes_from_checkpoint():
    """When a checkpoint exists, do NOT call plan_story again."""
    topic = "test_topic"
    n_segs = 5
    config = {}
    director_agent = MagicMock()
    cp_mgr = MagicMock()
    cp_mgr.get.return_value = {"outline": {"data": [{"seg": 1}, {"seg": 2}]}}
    outline = plan_outline(topic, n_segs, config, director_agent, cp_mgr, resume=True)
    assert outline == [{"seg": 1}, {"seg": 2}]
    # plan_story should NOT have been called
    director_agent.assert_not_called()
    # save should NOT have been called either
    cp_mgr.save.assert_not_called()


def test_plan_outline_calls_plan_story_when_no_resume():
    """When resume is False, always call plan_story."""
    with patch("utils.story_planner.plan_story", return_value=[{"seg": 1}]) as plan_story:
        topic = "test_topic"
        director_agent = MagicMock()
        cp_mgr = MagicMock()
        outline = plan_outline(topic, 1, {}, director_agent, cp_mgr, resume=False)
        assert outline == [{"seg": 1}]
        plan_story.assert_called_once()
        cp_mgr.save.assert_called_once()


def test_plan_outline_calls_plan_story_when_checkpoint_empty():
    """When resume is True but checkpoint is empty, fall through to plan_story."""
    with patch("utils.story_planner.plan_story") as plan_story:
        plan_story.return_value = [{"seg": 1}]
        topic = "test_topic"
        director_agent = MagicMock()
        cp_mgr = MagicMock()
        cp_mgr.get.return_value = None
        outline = plan_outline(topic, 1, {}, director_agent, cp_mgr, resume=True)
        assert outline == [{"seg": 1}]
        plan_story.assert_called_once()


# ── run_preflight_checks ──────────────────────────────────────────────────────


def test_run_preflight_checks_success():
    config = {
        "ollama": {"host": "http://localhost:11434"},
        "models": {"director": "hermes-director", "writer": "zephyr-writer"},
        "tts": {"engine": "omnivoice"},
    }
    with (
        patch("shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("pathlib.Path.exists", return_value=True),
        patch("shutil.disk_usage", return_value=(100, 20, 50 * (1024**3))),
        patch("urllib.request.urlopen") as urlopen_mock,
    ):
        # Mock urllib response
        mock_response = MagicMock()
        mock_response.read.return_value = (
            b'{"models": [{"name": "hermes-director"}, {"name": "zephyr-writer"}]}'
        )
        mock_response.__enter__.return_value = mock_response
        urlopen_mock.return_value = mock_response

        from core.pre_production import run_preflight_checks

        run_preflight_checks(config, dry_run=False)  # Should not raise


def test_run_preflight_checks_missing_ffmpeg_raises():
    config = {
        "ollama": {"host": "http://localhost:11434"},
        "models": {"director": "hermes-director", "writer": "zephyr-writer"},
        "tts": {"engine": "omnivoice"},
    }
    with (
        patch("shutil.which", return_value=None),
        patch("pathlib.Path.exists", return_value=True),
        patch("shutil.disk_usage", return_value=(100, 20, 50 * (1024**3))),
        patch("urllib.request.urlopen") as urlopen_mock,
    ):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"models": []}'
        mock_response.__enter__.return_value = mock_response
        urlopen_mock.return_value = mock_response

        import pytest

        from core.pre_production import run_preflight_checks

        with pytest.raises(RuntimeError, match="FFmpeg is missing"):
            run_preflight_checks(config, dry_run=False)



# ── _seed_director_memory ──────────────────────────────────────────────────────


def test_seed_director_memory():
    overlay = {
        "characters": {"hero": {"name": "Hero", "description": "Description"}},
        "_director_vision": {"theme": "epic", "emotions": "horror", "visual_style": "cinematic"},
        "production_notes": {"recommendations": ["Do this", "Do that"]},
    }
    config = {
        "checkpoint": {"dir": "studio_checkpoints"},
        "memory": {"memory_file": "studio_checkpoints/story_memory.json"},
    }
    with (
        patch("memory.permanent_memory.PermanentMemoryLog") as MockPerm,
        patch("memory.WorldState") as MockWS,
        patch("memory.StoryMemory") as MockSM,
    ):
        from core.pre_production import _seed_director_memory

        _seed_director_memory("my_topic", overlay, config)

        MockPerm.return_value.log_character.assert_called_with("Hero", "Description", "")
        MockPerm.return_value._save_memory.assert_called_once()
        MockWS.return_value._save.assert_called_once()
        MockSM.return_value.save.assert_called_once()


# ── run_pre_production ──────────────────────────────────────────────────────────


def test_run_pre_production():
    config = {"video": {"total_duration_min": 10}, "models": {"director": "d", "writer": "w"}}
    with (
        patch("agents.director_agent.DirectorAgent") as MockDirector,
        patch("core.pre_production._seed_director_memory"),
        patch("agents.decision_engine.build_decision_record") as MockBDR,
        patch("memory.blackboard.get_blackboard") as _MockBB,
    ):
        director_inst = MockDirector.return_value
        director_inst.ask_search_online.return_value = False
        director_inst.ask_create_from_scratch.return_value = (True, "scratch notes")
        director_inst.invent_story.return_value = "story text"
        director_inst.analyze_with_research.return_value = {"theme": "epic", "characters": []}
        director_inst.consult_on_config.return_value = ({}, {})
        director_inst.produce_runtime_config.return_value = {
            "video": {"total_duration_min": 10},
            "characters": {
                "hero": {
                    "name": "Hero",
                    "description": "This is a detailed description of the Hero with sword and armor.",
                }
            },
        }

        # Mock DecisionRecord
        mock_rec = MagicMock()
        mock_rec.to_overlay.return_value = {}
        mock_rec.segment_count.value = 5
        mock_rec.total_duration_min.value = 10
        mock_rec.words_per_segment.value = 100
        MockBDR.return_value = mock_rec

        from core.pre_production import run_pre_production

        res = run_pre_production("topic", config)
        assert isinstance(res, dict)
        assert "_invented_story" in res


def test_run_pre_production_adaptation():
    config = {"video": {"total_duration_min": 10}, "models": {"director": "d", "writer": "w"}}
    with (
        patch("agents.director_agent.DirectorAgent") as MockDirector,
        patch("core.pre_production._seed_director_memory"),
        patch("agents.decision_engine.build_decision_record") as MockBDR,
        patch("memory.blackboard.get_blackboard") as _MockBB,
        patch("pathlib.Path.exists", return_value=False),
    ):  # No prev overlay
        director_inst = MockDirector.return_value
        director_inst.ask_search_online.return_value = True
        director_inst.ask_create_from_scratch.return_value = (False, "")
        director_inst.research_story.return_value = {
            "topic": "t",
            "combined_summary": "summary",
            "result_count": 1,
        }
        director_inst.analyze_with_research.return_value = {
            "theme": "epic",
            "characters": [{"name": "Aria"}],
            "recommended_duration_min": 8,
        }
        director_inst.consult_on_config.return_value = ({}, {})
        director_inst.consult_with_writer.return_value = {}
        director_inst.produce_runtime_config.return_value = {
            "video": {"total_duration_min": 8},
            "characters": {"aria": {"name": "Aria"}},
        }

        # Mock DecisionRecord
        mock_rec = MagicMock()
        mock_rec.to_overlay.return_value = {}
        mock_rec.segment_count.value = 4
        mock_rec.total_duration_min.value = 8
        mock_rec.words_per_segment.value = 100
        MockBDR.return_value = mock_rec

        # Mock open
        from unittest.mock import mock_open

        with patch("builtins.open", mock_open()):
            from core.pre_production import run_pre_production

            res = run_pre_production("topic", config)
            assert isinstance(res, dict)


def test_run_pre_production_series_resume():
    config = {"video": {"total_duration_min": 10}, "models": {"director": "d", "writer": "w"}}
    with (
        patch("agents.director_agent.DirectorAgent") as MockDirector,
        patch("core.pre_production._seed_director_memory"),
        patch("agents.decision_engine.build_decision_record") as MockBDR,
        patch("memory.blackboard.get_blackboard") as _MockBB,
        patch("pathlib.Path.exists", return_value=True),
        patch(
            "pathlib.Path.read_text",
            return_value='{"video": {"total_duration_min": 5}, "characters": {"aria": {"name": "Aria"}}}',
        ),
    ):
        director_inst = MockDirector.return_value
        director_inst.ask_search_online.return_value = False
        director_inst.ask_create_from_scratch.return_value = (False, "")
        director_inst.analyze_with_research.return_value = {"recommended_duration_min": 5}
        director_inst.consult_with_writer.return_value = {}
        director_inst.produce_runtime_config.return_value = {
            "video": {"total_duration_min": 5},
            "characters": {"aria": {"name": "Aria"}},
        }

        # Mock DecisionRecord
        mock_rec = MagicMock()
        mock_rec.to_overlay.return_value = {}
        mock_rec.segment_count.value = 3
        mock_rec.total_duration_min.value = 5
        mock_rec.words_per_segment.value = 100
        MockBDR.return_value = mock_rec

        # Mock open
        from unittest.mock import mock_open

        with patch("builtins.open", mock_open()):
            from core.pre_production import run_pre_production

            res = run_pre_production("topic", config, skip_consultation=True)
            assert isinstance(res, dict)
