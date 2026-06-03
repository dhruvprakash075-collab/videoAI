"""test_pre_production_extended.py - Extended unit tests for core/pre_production.py"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pre_production import (
    _run_studio_session,
    _seed_director_memory,
    run_pre_production,
    run_preflight_checks,
)


def test_run_preflight_checks_edge_cases(monkeypatch):
    # 1. Test tts_engine="omnivoice" but worker NOT found
    with patch("pathlib.Path.exists", return_value=False):
        # run_preflight_checks(config, dry_run)
        run_preflight_checks(config={"tts": {"engine": "omnivoice"}}, dry_run=True)

    # 2. Test tts_engine="edge" and import edge_tts fails
    monkeypatch.setitem(sys.modules, "edge_tts", None)
    run_preflight_checks(config={"tts": {"engine": "edge"}}, dry_run=True)

    # 3. Test tts_engine="assumed_working"
    run_preflight_checks(config={"tts": {"engine": "assumed_working"}}, dry_run=True)

    # 4. Test Disk Space check low space
    with patch("shutil.disk_usage", return_value=(100, 95, (5 * 1024**3))):
        run_preflight_checks(config={}, dry_run=True)

    # 5. Test Disk Space check exception
    with patch("shutil.disk_usage", side_effect=Exception("disk error")):
        run_preflight_checks(config={}, dry_run=True)

    # 6. Test ffmpeg missing raises error when dry_run=False
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="FFmpeg is missing"):
            run_preflight_checks(config={}, dry_run=False)


def test_run_studio_session_edge_cases(tmp_path):
    ck_dir = tmp_path / "checkpoints"
    ck_dir.mkdir()

    config = {
        "checkpoint": {"dir": str(ck_dir)},
        "characters": {
            "char_non_visual": {
                "name": "Non Visual Character",
                "description": "very smart and intellectual and wise and helpful and a great reader of many old books and stories",
            },
            "char_existing_lora": {
                "name": "Existing Lora",
                "description": "detailed face portrait with black hair and green eyes and long dark coat, standing in front of building",
            },
            "char_gen_fail": {
                "name": "Gen Fail",
                "description": "detailed face portrait with brown eyes and black hair and blue shirt, close-up photo in the street",
            },
            "char_fewer_images": {
                "name": "Fewer Images",
                "description": "detailed face portrait with blue eyes and black hair and red scarf, highly focused studio lighting",
            },
            "char_train_fail": {
                "name": "Train Fail",
                "description": "detailed face portrait with green eyes and brown hair and yellow jacket, beautiful digital artwork",
            },
        },
    }

    existing_lora_file = ck_dir / "protagonist_existing_lora_lora.safetensors"
    existing_lora_file.touch()

    mock_scheduler = MagicMock()

    def mock_gen_images(prompts, ref_dir, seg_config):
        if "brown eyes" in prompts:
            raise Exception("gen fail")
        if "blue eyes" in prompts:
            return ["img1.png"]
        return ["img1.png", "img2.png", "img3.png", "img4.png", "img5.png"]

    def mock_train_lora(image_paths, char_name, output_dir, char_description, mock):
        if char_name == "Train Fail":
            return None
        return Path("dummy_lora.safetensors")

    with (
        patch("video.image_gen.image_gen.generate_images", side_effect=mock_gen_images),
        patch("train_lora.train_protagonist_lora", side_effect=mock_train_lora),
        patch("video.image_gen.image_gen.unload_sd_pipeline"),
    ):
        res = _run_studio_session(
            config=config,
            out_base=Path("dummy_out_base"),
            dry_run=True,
            global_scheduler=mock_scheduler,
        )
        assert "char_non_visual" not in res
        assert "char_existing_lora" in res
        assert "char_gen_fail" not in res
        assert "char_fewer_images" not in res
        assert "char_train_fail" not in res


def test_seed_director_memory_edge_cases(tmp_path):
    overlay = {
        "characters": {
            "char_no_desc": {"name": "NoDescCharacter", "description": ""},
            "char_no_name": {"name": "", "description": "some description"},
        },
        "_director_vision": {"theme": "darkness", "emotions": "fear"},
    }

    config = {"checkpoint": {"dir": str(tmp_path)}}

    topic = "test_memory_edges"
    ws_file = tmp_path / f"{topic}_world_state.json"
    import json

    ws_file.write_text(json.dumps({"world_facts": ["NoDescCharacter: "]}), encoding="utf-8")

    with patch("memory.permanent_memory.PermanentMemoryLog._save_memory"):
        _seed_director_memory(topic=topic, overlay=overlay, config=config)


def test_run_pre_production_scratch_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("DIRECTOR_MODE", "invalid_mode")

    mock_dir = MagicMock()
    mock_dir.ask_search_online.side_effect = Exception("search fail")
    mock_dir.ask_cache_ttl.side_effect = Exception("ttl fail")
    mock_dir.ask_create_from_scratch.return_value = (True, "scratch notes")
    mock_dir.invent_story.return_value = "Invented story text"
    mock_dir.analyze_with_research.return_value = {
        "theme": "fear",
        "emotions": "terror",
        "recommended_duration_min": 5,
    }
    mock_dir.consult_on_config.return_value = ({"user": "response"}, {"user": "input"})
    mock_dir.produce_runtime_config.return_value = {
        "characters": {"char_short": {"description": "too short"}},
        "video": {"total_duration_min": 10},
    }

    config = {"checkpoint": {"dir": str(tmp_path)}}

    with (
        patch("agents.director_agent.DirectorAgent", return_value=mock_dir),
        patch(
            "agents.decision_engine.build_decision_record",
            side_effect=Exception("decision engine fail"),
        ),
    ):
        res = run_pre_production(
            topic="test_scratch_fail",
            config=config,
            content_text=None,
            run_mode="scratch",
            project_name="my_proj",
        )
        assert "characters" not in res
        assert res["video"]["total_duration_min"] == 10


def test_run_pre_production_series_resume_edge_cases(tmp_path):
    mock_dir = MagicMock()
    mock_dir.analyze_with_research.return_value = {}
    mock_dir.consult_with_writer.return_value = {}
    mock_dir.produce_runtime_config.return_value = {
        "characters": {
            "char_simple": {"name": "char_simple", "description": "simple description string"}
        }
    }

    config = {"checkpoint": {"dir": str(tmp_path)}}

    overlay_path = Path("studio_checkpoints") / "config_overlay_test_series_resume.json"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    import json

    overlay_path.write_text(
        json.dumps({"characters": {"char_simple": "simple description string"}}), encoding="utf-8"
    )

    try:
        with (
            patch("agents.director_agent.DirectorAgent", return_value=mock_dir),
            patch(
                "agents.decision_engine.build_decision_record",
                side_effect=Exception("series decision fail"),
            ),
        ):
            res = run_pre_production(
                topic="test_series_resume",
                config=config,
                content_text=None,
                run_mode="series",
                project_name="my_proj",
                skip_consultation=True,
            )
            assert "char_simple" in res["characters"]
            assert isinstance(res["characters"]["char_simple"], dict)
            assert res["characters"]["char_simple"]["description"] == "simple description string"
    finally:
        if overlay_path.exists():
            overlay_path.unlink()


def test_run_pre_production_prev_overlay_parse_fail(tmp_path):
    mock_dir = MagicMock()
    mock_dir.ask_search_online.return_value = False
    mock_dir.ask_create_from_scratch.return_value = (False, "")
    mock_dir.analyze_with_research.return_value = {}
    mock_dir.consult_on_config.return_value = ({}, {})
    mock_dir.produce_runtime_config.return_value = {}

    config = {"checkpoint": {"dir": str(tmp_path)}}

    overlay_path = Path("studio_checkpoints") / "config_overlay_test_parse_fail.json"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text("invalid json config", encoding="utf-8")

    try:
        with patch("agents.director_agent.DirectorAgent", return_value=mock_dir):
            run_pre_production(
                topic="test_parse_fail",
                config=config,
                content_text="Some basic content",
                run_mode="scratch",
                skip_consultation=True,
            )
    finally:
        if overlay_path.exists():
            overlay_path.unlink()


def test_run_pre_production_duration_cliffhanger(tmp_path):
    mock_dir = MagicMock()
    mock_dir.ask_search_online.return_value = False
    mock_dir.ask_create_from_scratch.return_value = (False, "")
    mock_dir.analyze_with_research.return_value = {"recommended_duration_min": 10}
    mock_dir.consult_on_config.return_value = ({}, {})
    mock_dir.produce_runtime_config.return_value = {
        "video": {"total_duration_min": 10, "_cliffhanger_point": 50}
    }
    mock_dir.consult_with_writer.side_effect = Exception("writer fail")

    mock_dir.consult_on_duration.return_value = {"action": "cliffhanger"}
    mock_dir.suggest_cliffhangers.return_value = [
        {"outcome": "ending 1", "point": 50, "reason": "cliffhanger reason"}
    ]
    mock_dir.consult_user.return_value = "Option 1"

    config = {"checkpoint": {"dir": str(tmp_path)}}

    long_content = "Word " * 200

    with (
        patch("agents.director_agent.DirectorAgent", return_value=mock_dir),
        patch(
            "agents.decision_engine.build_decision_record", side_effect=Exception("decision fail")
        ),
    ):
        res = run_pre_production(
            topic="test_cliff", config=config, content_text=long_content, run_mode="scratch"
        )
        assert res["video"]["_cliffhanger_point"] == 50


def test_run_pre_production_duration_compact(tmp_path):
    mock_dir = MagicMock()
    mock_dir.ask_search_online.return_value = False
    mock_dir.ask_create_from_scratch.return_value = (False, "")
    mock_dir.analyze_with_research.return_value = {"recommended_duration_min": 10}
    mock_dir.consult_on_config.return_value = ({}, {})
    mock_dir.produce_runtime_config.return_value = {
        "video": {"total_duration_min": 4, "_content_compacted": True}
    }
    mock_dir.consult_with_writer.side_effect = Exception("writer fail")

    mock_dir.consult_on_duration.return_value = {"action": "compact", "target_minutes": 4}
    mock_dir.compact_story.return_value = "compacted story text"

    config = {"checkpoint": {"dir": str(tmp_path)}}

    long_content = "Word " * 200

    with (
        patch("agents.director_agent.DirectorAgent", return_value=mock_dir),
        patch(
            "agents.decision_engine.build_decision_record", side_effect=Exception("decision fail")
        ),
    ):
        res = run_pre_production(
            topic="test_compact", config=config, content_text=long_content, run_mode="scratch"
        )
        assert res["video"]["_content_compacted"] is True


def test_run_pre_production_duration_custom(tmp_path):
    mock_dir = MagicMock()
    mock_dir.ask_search_online.return_value = False
    mock_dir.ask_create_from_scratch.return_value = (False, "")
    mock_dir.analyze_with_research.return_value = {"recommended_duration_min": 10}
    mock_dir.consult_on_config.return_value = ({}, {})
    mock_dir.produce_runtime_config.return_value = {"video": {"total_duration_min": 6}}
    mock_dir.consult_with_writer.side_effect = Exception("writer fail")

    mock_dir.consult_on_duration.return_value = {"action": "custom", "target_minutes": 6}

    config = {"checkpoint": {"dir": str(tmp_path)}}

    long_content = "Word " * 200

    with (
        patch("agents.director_agent.DirectorAgent", return_value=mock_dir),
        patch(
            "agents.decision_engine.build_decision_record", side_effect=Exception("decision fail")
        ),
    ):
        res = run_pre_production(
            topic="test_custom", config=config, content_text=long_content, run_mode="scratch"
        )
        assert res["video"]["total_duration_min"] == 6


def test_run_pre_production_duration_adjusted(tmp_path):
    mock_dir = MagicMock()
    mock_dir.ask_search_online.return_value = False
    mock_dir.ask_create_from_scratch.return_value = (False, "")
    mock_dir.analyze_with_research.return_value = {"recommended_duration_min": 10}
    mock_dir.consult_on_config.return_value = ({}, {})
    mock_dir.produce_runtime_config.return_value = {
        "video": {"total_duration_min": 8, "_user_adjusted": True}
    }
    mock_dir.consult_with_writer.side_effect = Exception("writer fail")

    mock_dir.consult_on_duration.return_value = {"action": "adjusted", "target_minutes": 8}

    config = {"checkpoint": {"dir": str(tmp_path)}}

    long_content = "Word " * 200

    with (
        patch("agents.director_agent.DirectorAgent", return_value=mock_dir),
        patch(
            "agents.decision_engine.build_decision_record", side_effect=Exception("decision fail")
        ),
    ):
        res = run_pre_production(
            topic="test_adj", config=config, content_text=long_content, run_mode="scratch"
        )
        assert res["video"]["_user_adjusted"] is True


def test_run_pre_production_tts_normalization_failure(tmp_path):
    mock_dir = MagicMock()
    mock_dir.ask_search_online.return_value = False
    mock_dir.ask_create_from_scratch.return_value = (False, "")
    mock_dir.analyze_with_research.return_value = {}
    mock_dir.consult_on_config.return_value = ({}, {})
    mock_dir.produce_runtime_config.return_value = {"tts": {"engine": "custom_engine"}}

    config = {"checkpoint": {"dir": str(tmp_path)}}

    with (
        patch("agents.director_agent.DirectorAgent", return_value=mock_dir),
        patch(
            "audio.audio_proxy.normalize_tts_engine", side_effect=Exception("normalization error")
        ),
    ):
        res = run_pre_production(
            topic="test_norm_fail", config=config, content_text="Some content", run_mode="scratch"
        )
        assert res["tts"]["engine"] == "custom_engine"
