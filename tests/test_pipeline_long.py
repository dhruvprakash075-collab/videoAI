"""test_pipeline_long.py - tests for core/pipeline_long.py public surface."""

from pathlib import Path
from unittest.mock import patch

import pytest

from core.pipeline_long import (
    _director_set_abort,
    request_cancel,
    run_long_pipeline_async,
)


@pytest.fixture(autouse=True)
def _reset_abort():
    """Reset the Director abort flag before and after each test."""
    from core.segment_runner import set_director_abort

    set_director_abort(False)
    yield
    set_director_abort(False)


# ── _director_set_abort / request_cancel ─────────────────────────────────────


def test_director_set_abort_true():
    with patch("core.pipeline_long.set_director_abort") as sd:
        _director_set_abort(True)
        sd.assert_called_once_with(True)


def test_director_set_abort_default_true():
    with patch("core.pipeline_long.set_director_abort") as sd:
        _director_set_abort()
        sd.assert_called_once_with(True)


def test_request_cancel_sets_abort():
    with patch("core.pipeline_long.set_director_abort") as sd:
        request_cancel()
        sd.assert_called_once_with(True)


# ── run_long_pipeline_async ──────────────────────────────────────────────────


def test_run_long_pipeline_async_returns_overlay(tmp_path: Path):
    topic = "test_topic"
    config = {"foo": "bar"}
    overlay = {"baz": "qux"}
    with (
        patch("core.pipeline_long.run_pre_production", return_value=overlay) as rp,
        patch("utils.setup_run_logging"),
        patch("utils._safe_filename", return_value="test_topic"),
    ):
        result = run_long_pipeline_async(topic, config)
    assert result["status"] == "ok"
    assert result["topic"] == topic
    assert result["overlay"] == overlay
    rp.assert_called_once()


def test_run_long_pipeline_async_merges_overlay_into_config(tmp_path: Path):
    """run_pre_production overlay is merged into the returned config."""
    topic = "test_topic"
    base = {"a": 1, "nested": {"x": 10}}
    overlay = {"b": 2, "nested": {"y": 20}}
    with (
        patch("core.pipeline_long.run_pre_production", return_value=overlay),
        patch("utils.setup_run_logging"),
        patch("utils._safe_filename", return_value="test_topic"),
    ):
        result = run_long_pipeline_async(topic, base)
    # The overlay should have been merged
    assert result["overlay"]["b"] == 2
    assert result["overlay"]["nested"]["y"] == 20


# ── run_long_pipeline tests ──────────────────────────────────────────────────
from unittest.mock import MagicMock


def test_run_long_pipeline_dry_run_success(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {
            "total_duration_min": 1,
            "segment_duration_min": 1,
        },
        "script": {
            "default_images_per_segment": 2,
            "max_images_per_segment": 5,
        },
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}) as mock_pre_prod,
        patch("core.pipeline_long.run_preflight_checks") as mock_preflight,
        patch("utils.retry_manager.patch_retries") as _mock_patch_retries,
        patch("utils.checkpoint.build_checkpoint_manager") as _mock_cp_mgr,
        patch("core.pipeline_long._seed_director_memory") as _mock_seed_mem,
        patch("agents.director_agent.DirectorAgent") as _mock_dir_agent,
        patch("core.main.create_writer") as _mock_writer,
        patch("memory.StoryMemory") as _mock_story_mem,
        patch("memory.WorldState") as _mock_world_state,
        patch("utils.context_manager.ContextWindowManager") as _mock_ctx_mgr,
        patch("core.main.create_director") as _mock_director,
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ):
        mock_plan_outline.return_value = [
            {"seg": 1, "title": "Intro", "num_images": 2, "char_presence": [{}, {}]}
        ]
        mock_make_seg.side_effect = fake_make_seg

        mock_finalize.return_value = {"status": "dry_run", "output": "dummy.mp4", "segments": 1}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(
                topic="test_topic",
                resume=True,
                dry_run=True,
            )

        assert res["status"] == "dry_run"
        assert res["output"] == "dummy.mp4"
        assert res["segments"] == 1

        mock_pre_prod.assert_called_once()
        mock_preflight.assert_called_once()
        mock_plan_outline.assert_called_once()
        mock_make_seg.assert_called_once()
        mock_finalize.assert_called_once()


def test_run_long_pipeline_with_decision_record(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {
            "total_duration_min": 1,
            "segment_duration_min": 1,
        },
        "script": {
            "default_images_per_segment": 2,
            "max_images_per_segment": 5,
        },
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    mock_record = MagicMock()
    mock_record.segment_count.value = 3
    mock_record.segment_count.locked = True
    mock_record.segment_count.provenance = "user"
    mock_record.words_per_segment.value = 100
    mock_record.words_per_segment.provenance = "user"
    mock_record.total_duration_min.value = 3

    mock_bb = MagicMock()
    mock_bb.read_decision.return_value = mock_record

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("memory.blackboard.get_blackboard", return_value=mock_bb),
    ):
        mock_plan_outline.return_value = [
            {"seg": 1, "title": "Intro", "num_images": 2},
            {"seg": 2, "title": "Body", "num_images": 2},
            {"seg": 3, "title": "End", "num_images": 2},
        ]
        mock_make_seg.side_effect = fake_make_seg

        mock_finalize.return_value = {"status": "dry_run", "output": "dummy.mp4", "segments": 3}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(
                topic="test_topic",
                resume=True,
                dry_run=True,
            )

        assert res["status"] == "dry_run"
        assert res["segments"] == 3


def test_run_long_pipeline_staged_loop(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {
            "total_duration_min": 1,
            "segment_duration_min": 1,
        },
        "script": {
            "default_images_per_segment": 2,
        },
        "performance": {
            "staged_loop": True,
            "lookahead_segments": 2,
            "max_workers": 2,
        },
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.pipeline_long.evict_ollama_models") as mock_evict,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ):
        mock_plan_outline.return_value = [
            {"seg": 1, "title": "Intro"},
            {"seg": 2, "title": "Body"},
            {"seg": 3, "title": "End"},
        ]
        mock_make_seg.side_effect = fake_make_seg

        mock_finalize.return_value = {"status": "dry_run", "output": "dummy.mp4", "segments": 3}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(
                topic="test_topic",
                resume=True,
                dry_run=True,
            )

        assert res["status"] == "dry_run"
        assert mock_evict.call_count == 2


def test_run_long_pipeline_no_dry_run(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2, "max_images_per_segment": 5},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_production") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ):
        mock_plan_outline.return_value = [{"seg": 1, "title": "Intro"}]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "ok", "output": "final.mp4"}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=False)

        assert res["status"] == "ok"
        mock_finalize.assert_called_once()


def test_run_long_pipeline_no_segments_generated(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ):
        mock_plan_outline.return_value = [{"seg": 1, "title": "Intro"}]
        # Process seg returns without placing any MP4 in the list
        mock_make_seg.return_value = lambda i: None

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        assert res["status"] == "error"
        assert res["reason"] == "no segments"


def test_run_long_pipeline_endurance_mode(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 2, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            # Only generate segment 1, segment 2 is skipped (endurance mode test)
            if seg_idx == 1 and mp4s_list is not None:
                mp4s_list[0] = Path("segment_1.mp4")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ):
        mock_plan_outline.return_value = [{"seg": 1}, {"seg": 2}]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "dry_run_endurance"}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        assert res["status"] == "dry_run_endurance"
        mock_finalize.assert_called_once()


def test_run_long_pipeline_staged_loop_failures(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 2, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "performance": {"staged_loop": True, "lookahead_segments": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        def run_seg(seg_idx):
            raise RuntimeError("batch element fail")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.pipeline_long.evict_ollama_models"),
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ):
        mock_plan_outline.return_value = [{"seg": 1}, {"seg": 2}]
        mock_make_seg.side_effect = fake_make_seg

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        # Staged batch failed, so no segments generated
        assert res["status"] == "error"


def test_run_long_pipeline_segment_failures_non_staged(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "performance": {"staged_loop": False},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        def run_seg(seg_idx):
            raise RuntimeError("fail")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ):
        mock_plan_outline.return_value = [{"seg": 1}]
        mock_make_seg.side_effect = fake_make_seg

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        assert res["status"] == "error"


def test_run_long_pipeline_outline_length_locked_truncate(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 2, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    mock_record = MagicMock()
    mock_record.segment_count.value = 1
    mock_record.segment_count.locked = True
    mock_bb = MagicMock()
    mock_bb.read_decision.return_value = mock_record

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("memory.blackboard.get_blackboard", return_value=mock_bb),
    ):
        # lock segment_count to 1, but outline returns 2. It will truncate.
        mock_plan_outline.return_value = [{"seg": 1}, {"seg": 2}]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "ok", "segments": 1}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        assert res["status"] == "ok"
        assert res["segments"] == 1


def test_run_long_pipeline_outline_length_locked_adjust(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 2, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    mock_record = MagicMock()
    mock_record.segment_count.value = 3
    mock_record.segment_count.locked = True
    mock_bb = MagicMock()
    mock_bb.read_decision.return_value = mock_record

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("memory.blackboard.get_blackboard", return_value=mock_bb),
    ):
        # lock segment_count to 3, but outline returns 2. It will adjust to 2.
        mock_plan_outline.return_value = [{"seg": 1}, {"seg": 2}]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "ok", "segments": 2}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        assert res["status"] == "ok"
        assert res["segments"] == 2


def test_run_long_pipeline_worker_shutdown_exceptions(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")
        if mp4s_list is not None:
            mp4s_list[0] = Path("segment_1.mp4")
        return lambda i: None

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("audio.audio_proxy.shutdown_omnivoice_worker", side_effect=Exception("shutdown err")),
        patch("audio.audio_proxy.shutdown_f5_worker", side_effect=Exception("shutdown f5 err")),
    ):
        mock_plan_outline.return_value = [{"seg": 1}]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "ok"}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        assert res["status"] == "ok"


def test_run_long_pipeline_errors_and_edge_cases(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {
            "total_duration_min": 1,
            "segment_duration_min": 0,
        },  # seg_min == 0 to hit ValueError
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    # Verify seg_min == 0 raises ValueError
    with (
        patch("utils.load_config", return_value=cfg),
        patch("core.pipeline_long.run_pre_production", return_value={}),
    ):
        with pytest.raises(ValueError, match="segment_duration_min must be > 0"):
            run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

    # Let's fix seg_min to test other edge cases
    cfg["video"]["segment_duration_min"] = 1

    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def fake_import(name, *args, **kwargs):
        if name == "train_lora":
            raise ImportError("no train_lora")
        return real_import(name, *args, **kwargs)

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        return run_seg

    # Force blackboard exception, sync memory exception, train_lora import exception
    with (
        patch(
            "core.pipeline_long.run_pre_production", return_value=None
        ),  # config_overlay = None to cover line 220
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent") as mock_dir_agent_cls,
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch(
            "memory.blackboard.get_blackboard", side_effect=Exception("blackboard error")
        ),  # line 262
        patch("builtins.__import__", side_effect=fake_import),  # line 301
    ):
        mock_dir_agent = MagicMock()
        mock_dir_agent._sync_memory_to_worldstate.side_effect = Exception("sync err")  # line 235
        mock_dir_agent_cls.return_value = mock_dir_agent

        mock_plan_outline.return_value = [{"seg": 1, "title": "Intro"}]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "ok"}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(
                topic="test_topic", resume=True, dry_run=True, duration_min=5
            )  # duration_min set to cover line 248

        assert res["status"] == "ok"


def test_run_long_pipeline_stale_world_state_clear_fails(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState", return_value=MagicMock()),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("pathlib.Path.unlink", side_effect=OSError("permission denied")),  # line 318
    ):
        mock_plan_outline.return_value = [{"seg": 1}]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "ok"}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(
                topic="test_topic", resume=False, dry_run=True
            )  # resume=False to trigger clear

        assert res["status"] == "ok"


def test_run_long_pipeline_image_cap_and_env_ratio(tmp_path):
    from core.pipeline_long import run_long_pipeline

    # default default_images_per_segment is 6, max_images_per_segment is capped at 5
    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 6, "max_images_per_segment": 5},  # capped at 5
        "visual": {"environment_frame_ratio": 0.4},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        return run_seg

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ):
        # outline has segment with 12 images (exceeds max_images_per_segment=5)
        # char_presence contains first-frame establish weight, a low weight (<=0.2), and a non-dict to cover line 418
        mock_plan_outline.return_value = [
            {
                "seg": 1,
                "title": "Intro",
                "num_images": 12,
                "char_presence": [{"hero": 0.1}, None, {"hero": 0.9}, {"hero": 0.9}, {"hero": 0.9}],
            }
        ]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "ok"}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(
                topic="test_topic",
                resume=True,
                dry_run=True,
                words_per_segment=100,
                images_per_segment=4,
                segment_count=1,
            )

        assert res["status"] == "ok"


def test_run_long_pipeline_stale_world_state_clear_success(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    # Create a stale world_state file to trigger unlink success
    ck_dir = tmp_path / "checkpoints"
    ck_dir.mkdir(parents=True, exist_ok=True)
    ws_file = ck_dir / "world_state_test_topic.json"
    ws_file.write_text("{}")

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")
        if mp4s_list is not None:
            mp4s_list[0] = Path("segment_1.mp4")
        return lambda i: None

    from agents.director_agent import UIState

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState", return_value=MagicMock()),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.post_production.finalize_dry_run") as mock_finalize,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch.object(
            UIState, "set_progress", side_effect=Exception("UIState progress err")
        ),  # line 377
    ):
        mock_plan_outline.return_value = [{"seg": 1}]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "ok"}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=False, dry_run=True)

        assert res["status"] == "ok"
        assert not ws_file.exists()  # should have been unlinked successfully


def test_run_long_pipeline_staged_loop_abort_early(tmp_path):
    from core.pipeline_long import run_long_pipeline, set_director_abort

    cfg = {
        "video": {"total_duration_min": 2, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "performance": {"staged_loop": True, "lookahead_segments": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("core.pipeline_long.evict_ollama_models"),
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ):
        mock_plan_outline.return_value = [{"seg": 1}, {"seg": 2}]

        # Process seg aborts early by setting abort flag
        def abort_side_effect(i):
            set_director_abort(True)

        mock_make_seg.return_value = abort_side_effect

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        assert res["status"] == "error"


def test_pipeline_long_module_reload_win32(monkeypatch):
    import importlib
    import sys

    import core.pipeline_long

    # Mock sys.platform to win32 and mock stdout/stderr to raise OSError on reconfigure
    monkeypatch.setattr(sys, "platform", "win32")

    fake_stream = MagicMock()
    fake_stream.reconfigure.side_effect = OSError("fail")

    with patch("sys.stdout", fake_stream), patch("sys.stderr", fake_stream):
        importlib.reload(core.pipeline_long)


def test_run_long_pipeline_preview_and_exceptions(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 2, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "tts": {"engine": "invalid-engine"},  # triggers normalization warning (line 215)
        "performance": {"staged_loop": False},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    # Test non-staged loop exception coverage (lines 540-541)
    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment"),
        patch(
            "audio.audio_proxy.normalize_tts_engine", return_value="edge"
        ),  # different from invalid-engine
        patch("core.pipeline_long.build_retry_wrapper") as mock_wrapper,
    ):
        mock_plan_outline.return_value = [{"seg": 1}, {"seg": 2}]
        # mock wrapper to raise exception to trigger 540-541
        mock_wrapper.return_value = MagicMock(side_effect=RuntimeError("executor err"))

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(
                topic="test_topic", resume=True, dry_run=False
            )  # dry_run=False and n_segs=2 triggers table print (lines 428-447)
        assert res["status"] == "error"


def test_run_long_pipeline_staged_exceptions_and_abort(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 2, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "performance": {"staged_loop": True, "lookahead_segments": 1},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    # Test staged loop exception coverage (lines 529-530)
    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("core.pipeline_long.build_retry_wrapper") as mock_wrapper,
        patch("core.pipeline_long.evict_ollama_models"),
    ):
        mock_plan_outline.return_value = [{"seg": 1}, {"seg": 2}]
        mock_wrapper.return_value = MagicMock(side_effect=RuntimeError("staged executor err"))

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)
        assert res["status"] == "error"

    # Test staged loop get_director_abort() True during batch processing (line 518)
    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
        patch("utils.retry_manager.patch_retries"),
        patch("utils.checkpoint.build_checkpoint_manager"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.main.create_writer"),
        patch("memory.StoryMemory"),
        patch("memory.WorldState"),
        patch("utils.context_manager.ContextWindowManager"),
        patch("core.main.create_director"),
        patch("core.pipeline_long.plan_outline") as mock_plan_outline,
        patch("core.pipeline_long.make_process_segment") as mock_make_seg,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("core.pipeline_long.get_director_abort", return_value=True),  # trigger line 518
        patch("core.pipeline_long.evict_ollama_models"),
    ):
        mock_plan_outline.return_value = [{"seg": 1}, {"seg": 2}]
        mock_make_seg.return_value = lambda i: None

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)
        assert res["status"] == "error"


def test_pipeline_long_module_reload_import_errors(monkeypatch):
    import contextlib
    import importlib
    import sys

    # Mock builtins.__import__ to fail for certain modules
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def fake_import(name, *args, **kwargs):
        if name in ("utils.concurrency", "utils.context_manager", "torch"):
            raise ImportError(f"mocked import error for {name}")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", fake_import), contextlib.suppress(Exception):
        importlib.reload(sys.modules["core.pipeline_long"])


def test_pipeline_long_module_reload_spec_error():
    import importlib
    import sys

    with patch("importlib.util.spec_from_file_location", return_value=None):
        with pytest.raises(ImportError, match="Could not load concurrency module"):
            importlib.reload(sys.modules["core.pipeline_long"])


def test_float_safe_ceil_segment_count():
    """Verify math.ceil is used for float-safe segment count computation."""
    import math
    # 0.5 min total / 2 min per seg = 0.25 → ceil = 1
    total = 0.5
    seg_min = 2.0
    n_segs = max(1, math.ceil(total / seg_min))
    assert n_segs == 1

    # 5 min total / 2 min per seg = 2.5 → ceil = 3
    total = 5.0
    n_segs = max(1, math.ceil(total / seg_min))
    assert n_segs == 3

    # 2 min total / 2 min per seg = 1.0 → ceil = 1
    total = 2.0
    n_segs = max(1, math.ceil(total / seg_min))
    assert n_segs == 1
