"""test_segment_runner_extended.py - Extended unit tests for core/segment_runner.py"""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.segment_runner import make_process_segment


@pytest.fixture
def mock_dependencies(tmp_path):
    mp4s = [None] * 2
    counter = [0]
    cfg = {
        "critic": {"threshold": 60},
        "script": {"word_count_tolerance": 0.25, "words_per_segment": 50},
        "video": {"output_path": str(tmp_path / "final.mp4")},
        "checkpoint": {"enabled": True, "dir": str(tmp_path)},
    }
    outline = [{"title": "Intro"}, {"title": "Body"}]

    mock_sched = MagicMock()
    mock_sched.active_heavy_count = 0

    return {
        "topic": "test_segment_runner_extended",
        "config": cfg,
        "outline": outline,
        "n_segs": 2,
        "out_base": tmp_path,
        "tts_cfg": {"lang": "hi"},
        "cp_mgr": MagicMock(),
        "world_state": MagicMock(),
        "mem": MagicMock(),
        "ctx_mgr": MagicMock(),
        "director_agent_instance": MagicMock(),
        "writer_agent": MagicMock(),

        "resume": False,
        "dry_run": True,
        "director_mode": False,
        "preview_mode": False,
        "skip_rvc": True,
        "words_per_seg": 50,
        "seg_min": 2,
        "shared_prompt_executor": MagicMock(),
        "global_scheduler": mock_sched,
        "_crewai_lock": threading.RLock(),
        "crewai_lock": threading.RLock(),
        "completed_segs_counter_holder": counter,
        "completed_segs_lock": threading.Lock(),
        "mp4s": mp4s,
        "mp4s_lock": threading.Lock(),
        "run_start_ts": time.time(),
    }


def test_write_script_structured_writer_failure(mock_dependencies):
    # Setup structured writer exception to force CrewAI fallback
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = "crewai script response"

    with (
        patch(
            "utils.crewai_breaker.guarded_ollama_call", side_effect=Exception("guarded ollama fail")
        ),
        patch("crewai.Task"),
        patch("crewai.Crew", return_value=mock_crew),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script") as mock_score,
    ):
        mock_score_obj = MagicMock()
        mock_score_obj.total = 80
        mock_score_obj.issues = []
        mock_score_obj.suggestions = []
        mock_score.return_value = mock_score_obj

        process_seg = make_process_segment(**mock_dependencies)
        process_seg(1)

        mock_crew.kickoff.assert_called()


def test_critic_node_reject_and_rewrite(mock_dependencies):
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = "unsatisfactory script"

    score1 = MagicMock(total=40, issues=["too short"], suggestions=["add detail"])
    score2 = MagicMock(total=80, issues=[], suggestions=[])

    with (
        patch("utils.crewai_breaker.guarded_ollama_call", side_effect=Exception("ollama fail")),
        patch("crewai.Task"),
        patch("crewai.Crew", return_value=mock_crew),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script", side_effect=[score1, score2]),
    ):
        process_seg = make_process_segment(**mock_dependencies)
        process_seg(1)

        # Crew should be kicked off twice (once for initial draft, once for rewrite)
        assert mock_crew.kickoff.call_count == 2


def test_translate_node_word_count_trimming(mock_dependencies):
    long_script = "Word word word. " * 30
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = long_script

    with (
        patch("utils.crewai_breaker.guarded_ollama_call", side_effect=Exception("ollama fail")),
        patch("crewai.Task"),
        patch("crewai.Crew", return_value=mock_crew),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script") as mock_score,
        patch("core.pre_production._sanitize_narration", side_effect=lambda x: x) as mock_sanitize,
    ):
        mock_score_obj = MagicMock()
        mock_score_obj.total = 80
        mock_score.return_value = mock_score_obj

        process_seg = make_process_segment(**mock_dependencies)
        process_seg(1)

        called_script = mock_sanitize.call_args[0][0]
        assert len(called_script.split()) <= 62


def test_translate_node_translation_failure(mock_dependencies):
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = "hello world"
    mock_dependencies["director_agent_instance"].translate_to_devanagari.side_effect = Exception(
        "trans fail"
    )

    with (
        patch("utils.crewai_breaker.guarded_ollama_call", side_effect=Exception("ollama fail")),
        patch("crewai.Task"),
        patch("crewai.Crew", return_value=mock_crew),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script") as mock_score,
    ):
        mock_score_obj = MagicMock()
        mock_score_obj.total = 80
        mock_score.return_value = mock_score_obj

        process_seg = make_process_segment(**mock_dependencies)
        process_seg(1)

        assert mock_dependencies["director_agent_instance"].translate_to_devanagari.called


def test_tts_node_resume_cache(mock_dependencies):
    audio_file = mock_dependencies["out_base"] / "audio_existing.wav"
    audio_file.touch()

    mock_dependencies["resume"] = True
    mock_dependencies["cp_mgr"].get.return_value = {
        "audio": {"data": str(audio_file), "word_timestamps": "dummy timestamps"}
    }

    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = "hello world"

    with (
        patch("utils.crewai_breaker.guarded_ollama_call", side_effect=Exception("ollama fail")),
        patch("crewai.Task"),
        patch("crewai.Crew", return_value=mock_crew),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script") as mock_score,
    ):
        mock_score_obj = MagicMock()
        mock_score_obj.total = 80
        mock_score.return_value = mock_score_obj

        process_seg = make_process_segment(**mock_dependencies)
        process_seg(1)

        for call in mock_dependencies["cp_mgr"].save.call_args_list:
            assert call[0][1] != "audio"


def test_image_node_resume_cache(mock_dependencies):
    img_file1 = mock_dependencies["out_base"] / "image1.png"
    img_file1.touch()
    img_file2 = mock_dependencies["out_base"] / "image2.png"
    img_file2.touch()

    mock_dependencies["resume"] = True
    mock_dependencies["cp_mgr"].get.side_effect = lambda k: (
        {"images": {"data": [str(img_file1), str(img_file2)]}} if "seg01" in k else None
    )

    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = "hello world"

    with (
        patch("utils.crewai_breaker.guarded_ollama_call", side_effect=Exception("ollama fail")),
        patch("crewai.Task"),
        patch("crewai.Crew", return_value=mock_crew),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script") as mock_score,
        patch("video.image_gen.image_gen.generate_images") as mock_gen_img,
    ):
        mock_score_obj = MagicMock()
        mock_score_obj.total = 80
        mock_score.return_value = mock_score_obj

        process_seg = make_process_segment(**mock_dependencies)
        process_seg(1)

        mock_gen_img.assert_not_called()


def test_write_script_node_resume_cache(mock_dependencies):
    mock_dependencies["resume"] = True
    mock_dependencies["cp_mgr"].get.return_value = {"script": {"data": "cached script text"}}
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = "hello world"
    with (
        patch("utils.crewai_breaker.guarded_ollama_call", side_effect=Exception("ollama fail")),
        patch("crewai.Task"),
        patch("crewai.Crew", return_value=mock_crew),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script") as mock_score,
    ):
        mock_score_obj = MagicMock()
        mock_score_obj.total = 80
        mock_score.return_value = mock_score_obj

        process_seg = make_process_segment(**mock_dependencies)
        process_seg(1)
        mock_crew.kickoff.assert_not_called()


def test_critic_node_llm_unavailable(mock_dependencies):
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = "unsatisfactory script"
    with (
        patch("utils.crewai_breaker.guarded_ollama_call", side_effect=Exception("ollama fail")),
        patch("crewai.Task"),
        patch("crewai.Crew", return_value=mock_crew),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script", return_value=None),
    ):
        process_seg = make_process_segment(**mock_dependencies)
        process_seg(1)


def test_segment_runner_graph_nodes_live(mock_dependencies):
    mock_dependencies["dry_run"] = False
    mock_dependencies["resume"] = False
    mock_dependencies["director_agent_instance"].translate_to_devanagari.return_value = None

    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = "crewai script response"

    score_ok = MagicMock(total=80, issues=[], suggestions=[])

    with (
        patch("utils.crewai_breaker.guarded_ollama_call", side_effect=Exception("ollama fail")),
        patch("crewai.Task"),
        patch("crewai.Crew", return_value=mock_crew),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script", return_value=score_ok),
        patch(
            "video.image_gen.image_gen.generate_images",
            return_value=[Path("img1.png"), Path("img2.png")],
        ),
        patch(
            "audio.audio_proxy.tts_generate",
            return_value={"wav_path": "fake.wav", "word_timestamps": "fake_json"},
        ) as mock_tts,
        patch("video.renderer.renderer.render_with_assets", return_value=Path("out.mp4")),
        patch("utils.scene_director.enrich_prompts", return_value=(["prompt1"], "neg_prompt")),
        patch("torch.cuda.is_available", return_value=True),
        patch("torch.cuda.empty_cache"),
    ):
        process_seg = make_process_segment(**mock_dependencies)
        process_seg(1)

        # Verify that render_with_assets was successfully called and stored
        assert mock_dependencies["mp4s"][0] == Path("out.mp4")
        assert all(call.kwargs["lang"] == "en" for call in mock_tts.call_args_list)


def test_evict_ollama_models_exceptions():
    from core.segment_runner import evict_ollama_models

    # Mock gc.collect to raise an exception
    with patch("gc.collect", side_effect=Exception("gc error")):
        # Should catch exception and not crash
        evict_ollama_models(config={}, reason="test")


def test_evict_ollama_models_harder_evict():
    from core.segment_runner import evict_ollama_models

    mock_res = MagicMock()
    mock_res.__enter__.return_value = mock_res
    mock_res.read.return_value = b'{"models": [{"name": "writer-model"}]}'

    config = {
        "performance": {"vram_evict_wait_s": 0.05, "vram_sd_threshold_gb": 5.0},
        "ollama": {"host": "http://localhost:11434"},
    }

    with (
        patch("torch.cuda.is_available", return_value=True),
        patch(
            "torch.cuda.mem_get_info", return_value=(4 * (1024**3), 6 * (1024**3))
        ),  # 4GB free < 5GB threshold
        patch("time.sleep"),  # skip delay
        patch("urllib.request.urlopen", return_value=mock_res) as mock_urlopen,
    ):
        evict_ollama_models(config=config, reason="StableDiffusion")

        # Verify it fetched /api/ps and posted to /api/generate
        assert mock_urlopen.call_count >= 2
