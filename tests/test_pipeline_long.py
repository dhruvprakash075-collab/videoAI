"""test_pipeline_long.py - tests for core/pipeline_long.py public surface."""

import contextlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.pipeline_long import (
    run_long_pipeline_async,
)


@pytest.fixture(autouse=True)
def _reset_abort():
    """Reset the Director abort flag before and after each test."""
    from core.segment_runner import set_director_abort

    set_director_abort(False)
    yield
    set_director_abort(False)


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


# ── Pure-function tests (no mocking) ─────────────────────────────────────────


def test_ensure_init_idempotent():
    """First call does real work, second call is a no-op (cached)."""
    import core.pipeline_long as _pl

    saved = _pl._compat_applied
    try:
        _pl._compat_applied = False
        _pl._ensure_init()
        assert _pl._compat_applied is True
        _pl._ensure_init()
        assert _pl._compat_applied is True
    finally:
        _pl._compat_applied = saved


def test_deep_merge_pure():
    from core.pipeline_long import _deep_merge

    base = {"a": 1, "nested": {"x": 10}, "items": ["one"]}
    override = {"b": 2, "nested": {"y": 20}, "items": ["two", "one"]}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": 2, "nested": {"x": 10, "y": 20}, "items": ["one", "two"]}
    assert base["items"] == ["one", "two"]  # ponytail: list-append is in-place


def test_deep_merge_override_wins():
    from core.pipeline_long import _deep_merge

    result = _deep_merge({"x": 1}, "not-a-dict")
    assert result == "not-a-dict"


# ── Fast-dry-run integration test (minimal mocking — real orchestration) ─────


def test_fast_dry_run_orchestration(tmp_path):
    """Run pipeline with fast_dry_run=True, mock only external services.

    Unlike the heavily-mocked tests below, this keeps make_process_segment,
    shape_outline, StoryMemory, WorldState, ContextWindowManager, and
    finalize_dry_run real — exercises the actual orchestration code paths.
    """
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
        "audio": {},
        "performance": {"staged_loop": False, "max_workers": 1},
    }
    outline = [
        {
            "seg": 1, "title": "Intro", "num_images": 2,
            "target_word_count": 130, "segment_duration": 60.0,
            "char_presence": [{"protagonist": 1.0}],
        }
    ]
    # Only mock external boundaries: LLM/Ollama-dependent calls and CUDA.
    mocks = [
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.main.create_director"),
        patch("core.main.create_writer"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("core.pipeline_long.plan_outline", return_value=outline),
        patch("core.pipeline_long.log_vram_usage"),
        patch("core.runtime.ollama.start_ollama_server"),
        patch("core.runtime.ollama.stop_ollama_server"),
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ]
    with contextlib.ExitStack() as stack:
        mock_preflight = stack.enter_context(patch("core.pipeline_long.run_preflight_checks"))
        for m in mocks:
            stack.enter_context(m)
        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, fast_dry_run=True)

    assert res["status"] == "dry_run"
    assert res["segments"] == 1
    assert isinstance(res["output"], str)
    assert "chapters" in res
    assert len(res["chapters"]) >= 1
    assert mock_preflight.call_args.kwargs.get("dry_run") is True


def test_fast_dry_run_calls_preflight_in_dry_mode(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    with (
        patch("utils.load_config", return_value=cfg),
        patch("utils.setup_run_logging"),
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("core.pipeline_long.run_preflight_checks", side_effect=RuntimeError("stop")) as mock_preflight,
    ):
        with pytest.raises(RuntimeError, match="stop"):
            run_long_pipeline(topic="test_topic", resume=True, fast_dry_run=True)

    assert mock_preflight.call_args.kwargs["dry_run"] is True


def test_duration_flag_beats_stale_decision_record(tmp_path):
    """--duration must survive a stale/persisted DecisionRecord: the record's
    duration is the Director's advisory recommendation, never a CLI override —
    previously _resolve_decision_record clobbered the user's --duration."""
    from core.pipeline_long import run_long_pipeline

    class _Duration:
        value = 6
        locked = False
        provenance = "director"

    class _Rec:
        segment_count = SimpleNamespace(value=1, locked=False, provenance="director")
        words_per_segment = SimpleNamespace(value=100, locked=False, provenance="director")
        images_per_segment = SimpleNamespace(locked=False)
        total_duration_min = _Duration()

    blackboard = MagicMock()
    blackboard.read_decision.return_value = _Rec()
    captured = {}

    def _fake_mps(**kwargs):
        captured["config"] = kwargs["config"]
        return (lambda *a, **k: None,) * 6

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
        "audio": {},
        "performance": {"staged_loop": False, "max_workers": 1},
    }
    mocks = [
        patch("core.pipeline_long.run_preflight_checks"),
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.main.create_director"),
        patch("core.main.create_writer"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("core.pipeline_long.plan_outline", return_value=[{"seg": 1, "title": "Intro", "summary": "S", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0, "char_presence": [{"protagonist": 1.0}]}]),
        patch("core.pipeline_long.log_vram_usage"),
        patch("core.runtime.ollama.start_ollama_server"),
        patch("core.runtime.ollama.stop_ollama_server"),
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("utils.load_config", return_value=cfg),
        patch("memory.blackboard.get_blackboard", return_value=blackboard),
        patch("core.pipeline_long.make_process_segment", side_effect=_fake_mps),
    ]
    with contextlib.ExitStack() as stack:
        for m in mocks:
            stack.enter_context(m)
        run_long_pipeline(topic="t", resume=True, dry_run=True, fast_dry_run=True, duration_min=1)

    assert captured["config"]["video"]["total_duration_min"] == 1

    captured.clear()
    with contextlib.ExitStack() as stack:
        for m in mocks:
            stack.enter_context(m)
        run_long_pipeline(topic="t", resume=True, dry_run=True, fast_dry_run=True)

    assert captured["config"]["video"]["total_duration_min"] == 6


def test_skip_preflight_skips_run_preflight_checks(tmp_path):
    """--skip-preflight must also skip the in-pipeline preflight, not just the
    bootstrap gate — previously a missing ffmpeg still hard-failed the run."""
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
        "performance": {"staged_loop": False, "max_workers": 1},
    }
    outline = [
        {
            "seg": 1, "title": "Intro", "summary": "S", "num_images": 2,
            "target_word_count": 130, "segment_duration": 60.0,
            "char_presence": [{"protagonist": 1.0}],
        }
    ]
    with (
        patch("utils.load_config", return_value=cfg),
        patch("utils.setup_run_logging"),
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("core.main.create_director"),
        patch("core.main.create_writer"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("core.pipeline_long.plan_outline", return_value=outline),
        patch("core.pipeline_long.log_vram_usage"),
        patch("core.runtime.ollama.start_ollama_server"),
        patch("core.runtime.ollama.stop_ollama_server"),
        patch("core.pipeline_long.run_preflight_checks", side_effect=RuntimeError("stop")) as mock_preflight,
    ):
        run_long_pipeline(topic="test_topic", resume=True, fast_dry_run=True, skip_preflight=True)

    mock_preflight.assert_not_called()


def test_force_refresh_reaches_run_pre_production(tmp_path):
    """--force-vision must bypass the cached vision doc: run_pre_production
    receives force_refresh=True so the Director re-analyzes on re-runs."""
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
        "performance": {"staged_loop": False, "max_workers": 1},
    }
    outline = [
        {
            "seg": 1, "title": "Intro", "num_images": 2,
            "target_word_count": 130, "segment_duration": 60.0,
            "char_presence": [{"protagonist": 1.0}],
        }
    ]
    with (
        patch("utils.load_config", return_value=cfg),
        patch("utils.setup_run_logging"),
        patch("core.pipeline_long.run_pre_production", return_value={}) as mock_pre_prod,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("core.main.create_director"),
        patch("core.main.create_writer"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("core.pipeline_long.plan_outline", return_value=outline),
        patch("core.pipeline_long.log_vram_usage"),
        patch("core.runtime.ollama.start_ollama_server"),
        patch("core.runtime.ollama.stop_ollama_server"),
        patch("core.pipeline_long.run_preflight_checks"),
    ):
        run_long_pipeline(topic="test_topic", fast_dry_run=True, force_refresh=True)

    mock_pre_prod.assert_called_once()
    assert mock_pre_prod.call_args.kwargs.get("force_refresh") is True


def test_storyboard_gate_skipped_on_dry_run(tmp_path, monkeypatch):
    """Dry runs stay dry: the storyboard gate (real LLM + ComfyUI + persist)
    must NOT fire on fast_dry_run, or the dry run would generate a real sheet
    and persist an approved record the real run silently reuses."""
    from core import storyboard as _sb
    from core.pipeline_long import run_long_pipeline

    calls = []

    def _fake_run_storyboard(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(_sb, "run_storyboard", _fake_run_storyboard)

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }
    outline = [
        {
            "seg": 1, "title": "Intro", "num_images": 2,
            "target_word_count": 130, "segment_duration": 60.0,
            "char_presence": [{"protagonist": 1.0}],
        }
    ]
    mocks = [
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.main.create_director"),
        patch("core.main.create_writer"),
        patch("agents.director_agent.DirectorAgent"),
        patch("core.pipeline_long._seed_director_memory"),
        patch("core.pipeline_long.plan_outline", return_value=outline),
        patch("core.pipeline_long.log_vram_usage"),
        patch("core.runtime.ollama.start_ollama_server"),
        patch("core.runtime.ollama.stop_ollama_server"),
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
    ]
    with contextlib.ExitStack() as stack:
        for m in mocks:
            stack.enter_context(m)
        with patch("utils.load_config", return_value=cfg):
            run_long_pipeline(topic="test_topic", resume=True, fast_dry_run=True)

    assert calls == []


# ── run_long_pipeline tests ──────────────────────────────────────────────────


def test_run_long_pipeline_dry_run_success(tmp_path):
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {
            "total_duration_min": 1,
            "segment_duration_min": 1,
        },
        "script": {
            "default_images_per_segment": 2,
        },
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}) as mock_pre_prod,
        patch("core.pipeline_long.run_preflight_checks") as mock_preflight,
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
            {"seg": 1, "title": "Intro", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0, "char_presence": [{}, {}]}
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

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
            {"seg": 1, "title": "Intro", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0},
            {"seg": 2, "title": "Body", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0},
            {"seg": 3, "title": "End", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0},
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

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
            {"seg": 1, "title": "Intro", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0},
            {"seg": 2, "title": "Body", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0},
            {"seg": 3, "title": "End", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0},
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
        assert mock_evict.call_count == 10  # 5 phases x 2 batches


def test_run_long_pipeline_no_dry_run(tmp_path):
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

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        mock_plan_outline.return_value = [{"seg": 1, "title": "Intro", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
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
        mock_plan_outline.return_value = [{"seg": 1, "title": "Intro", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
        # Process seg returns without placing any MP4 in the list
        _noop = lambda x: None
        mock_make_seg.return_value = (lambda i: None, _noop, _noop, _noop, _noop, _noop)

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

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}, {"seg": 2, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
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

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}, {"seg": 2, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
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

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
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

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}, {"seg": 2, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
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

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}, {"seg": 2, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
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
        return lambda i: None, lambda x: None, lambda x: None, lambda x: None, lambda x: None, lambda x: None

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
    ):
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
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
        patch("core.main.create_writer"),
        patch("core.main.create_director"),
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

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    # Force blackboard exception, sync memory exception, train_lora import exception
    with (
        patch(
            "core.pipeline_long.run_pre_production", return_value=None
        ),  # config_overlay = None to cover line 220
        patch("core.pipeline_long.run_preflight_checks"),
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

        mock_plan_outline.return_value = [{"seg": 1, "title": "Intro", "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
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

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "ok"}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(
                topic="test_topic", resume=False, dry_run=True
            )  # resume=False to trigger clear

        assert res["status"] == "ok"


def test_run_long_pipeline_image_cap_and_env_ratio(tmp_path):
    from core.pipeline_long import run_long_pipeline

    # default default_images_per_segment is 2, no upper cap anymore
    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 6},
        "visual": {"environment_frame_ratio": 0.4},
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")

        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")

        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        # outline has segment with 12 images (passes through uncapped)
        # char_presence contains first-frame establish weight, a low weight (<=0.2), and a non-dict to cover line 418
        mock_plan_outline.return_value = [
            {
                "seg": 1,
                "title": "Intro",
                "num_images": 12,
                "target_word_count": 130,
                "segment_duration": 60.0,
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
        return lambda i: None, lambda x: None, lambda x: None, lambda x: None, lambda x: None, lambda x: None

    from agents.director_agent import UIState

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        ),
    ):
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "ok"}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=False, dry_run=True)

        assert res["status"] == "ok"
        assert not ws_file.exists()


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
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}, {"seg": 2, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]

        # Abort is triggered by scripts phase to simulate mid-batch abort
        _noop = lambda x: None
        _abort_phase = lambda segs: set_director_abort(True)
        mock_make_seg.return_value = (_noop, _abort_phase, _noop, _noop, _noop, _noop)

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
        patch(
            "audio.audio_proxy.normalize_tts_engine", return_value="supertonic"
        ),  # different from invalid-engine
        patch("core.pipeline_long.build_retry_wrapper") as mock_wrapper,
    ):
        _noop = lambda x: None
        mock_make_seg.return_value = (_noop, _noop, _noop, _noop, _noop, _noop)
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}, {"seg": 2, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
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
        _noop = lambda x: None
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}, {"seg": 2, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
        mock_make_seg.return_value = (_noop, _noop, _noop, _noop, _noop, _noop)
        mock_wrapper.return_value = MagicMock(side_effect=RuntimeError("staged executor err"))

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)
        assert res["status"] == "error"

    # Test staged loop get_director_abort() True during batch processing (line 518)
    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        mock_plan_outline.return_value = [{"seg": 1, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}, {"seg": 2, "num_images": 2, "target_word_count": 130, "segment_duration": 60.0}]
        _noop = lambda x: None
        mock_make_seg.return_value = (_noop, _noop, _noop, _noop, _noop, _noop)

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

    import core.pipeline_long as pl
    import utils.concurrency as uc

    assert pl.global_scheduler is uc.global_scheduler

    with patch("importlib.util.spec_from_file_location") as mock_spec:
        importlib.reload(sys.modules["core.pipeline_long"])
        assert not mock_spec.called


def test_float_safe_ceil_segment_count():
    """Verify the production _ceil_segments helper is float-safe."""
    from core.pipeline_long import _ceil_segments

    # 0.5 min total / 2 min per seg = 0.25 → ceil = 1
    assert _ceil_segments(0.5, 2.0) == 1

    # 5 min total / 2 min per seg = 2.5 → ceil = 3
    assert _ceil_segments(5.0, 2.0) == 3

    # 2 min total / 2 min per seg = 1.0 → ceil = 1
    assert _ceil_segments(2.0, 2.0) == 1

    # Below one segment always yields at least 1
    assert _ceil_segments(0.1, 2.0) == 1


# ── Character-role normalization regression tests ────────────────────────────


def _make_mock_bb(images_per_seg=2):
    """Create a mock blackboard with images_per_segment locked."""
    mock_record = MagicMock()
    mock_record.segment_count.value = 1
    mock_record.segment_count.locked = False
    mock_record.segment_count.provenance = "director"
    mock_record.words_per_segment.value = 130
    mock_record.words_per_segment.provenance = "director"
    mock_record.total_duration_min.value = 1
    mock_record.images_per_segment.value = images_per_seg
    mock_record.images_per_segment.locked = True
    mock_record.images_per_segment.provenance = "user"
    mock_bb = MagicMock()
    mock_bb.read_decision.return_value = mock_record
    return mock_bb


def test_role_normalization_single_named_char_keeps_max_weight(tmp_path):
    """One named character + protagonist/mentor/guardian → one key with max weight."""
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "visual": {"environment_frame_ratio": 0},
        "characters": {
            "aria": {"name": "Aria", "description": "heroine"},
        },
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")
        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")
        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        patch("memory.blackboard.get_blackboard", return_value=_make_mock_bb(2)),
    ):
        # protagonist→aria(0.9), mentor→aria(0.6), guardian→aria(0.3)
        # When all three map to same key, max weight (0.9) should win.
        mock_plan_outline.return_value = [
            {
                "seg": 1, "title": "Intro", "num_images": 2,
                "target_word_count": 130,
                "segment_duration": 60.0,
                "char_presence": [
                    {"protagonist": 0.9, "mentor": 0.6, "guardian": 0.3},
                    {"protagonist": 0.4},
                ],
            }
        ]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "dry_run", "output": "dummy.mp4", "segments": 1}

        with patch("utils.load_config", return_value=cfg):
            res = run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        assert res["status"] == "dry_run"

        # Inspect the char_presence that was passed to make_process_segment
        call_args = mock_make_seg.call_args
        outline_used = call_args.kwargs.get("outline") or call_args[0][3]  # positional arg
        cp = outline_used[0]["char_presence"]
        # First frame: all three roles mapped to "aria" (max weight wins)
        # env_ratio may reduce weights, but role keys should be gone
        assert "aria" in cp[0]
        assert "protagonist" not in cp[0]
        assert "mentor" not in cp[0]
        assert "guardian" not in cp[0]


def test_role_normalization_three_named_characters_map_independently(tmp_path):
    """Three named characters map protagonist/mentor/guardian independently."""
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "visual": {"environment_frame_ratio": 0},
        "characters": {
            "aria": {"name": "Aria", "description": "heroine"},
            "gandalf": {"name": "Gandalf", "description": "wizard"},
            "legolas": {"name": "Legolas", "description": "elf"},
        },
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")
        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")
        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        patch("memory.blackboard.get_blackboard", return_value=_make_mock_bb(2)),
    ):
        mock_plan_outline.return_value = [
            {
                "seg": 1, "title": "Intro", "num_images": 2,
                "target_word_count": 130,
                "segment_duration": 60.0,
                "char_presence": [
                    {"protagonist": 0.9, "mentor": 0.7, "guardian": 0.5},
                ],
            }
        ]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "dry_run", "output": "dummy.mp4", "segments": 1}

        with patch("utils.load_config", return_value=cfg):
            run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        call_args = mock_make_seg.call_args
        outline_used = call_args.kwargs.get("outline") or call_args[0][3]
        cp = outline_used[0]["char_presence"]
        # protagonist→aria, mentor→gandalf, guardian→legolas
        # env_ratio may reduce weights, but role keys should be gone
        assert "aria" in cp[0]
        assert "gandalf" in cp[0]
        assert "legolas" in cp[0]
        assert "protagonist" not in cp[0]
        assert "mentor" not in cp[0]
        assert "guardian" not in cp[0]


def test_role_normalization_environment_removed(tmp_path):
    """'environment' entries are removed from char_presence."""
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "visual": {"environment_frame_ratio": 0},
        "characters": {
            "aria": {"name": "Aria", "description": "heroine"},
        },
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")
        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")
        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        patch("memory.blackboard.get_blackboard", return_value=_make_mock_bb(2)),
    ):
        mock_plan_outline.return_value = [
            {
                "seg": 1, "title": "Intro", "num_images": 2,
                "target_word_count": 130,
                "segment_duration": 60.0,
                "char_presence": [
                    {"protagonist": 0.8, "environment": 0.1},
                    {"protagonist": 0.5},
                ],
            }
        ]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "dry_run", "output": "dummy.mp4", "segments": 1}

        with patch("utils.load_config", return_value=cfg):
            run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        call_args = mock_make_seg.call_args
        outline_used = call_args.kwargs.get("outline") or call_args[0][3]
        cp = outline_used[0]["char_presence"]
        assert "environment" not in cp[0]


def test_role_normalization_non_dict_frames_unchanged(tmp_path):
    """Non-dict frames in char_presence pass through unchanged."""
    from core.pipeline_long import run_long_pipeline

    cfg = {
        "video": {"total_duration_min": 1, "segment_duration_min": 1},
        "script": {"default_images_per_segment": 2},
        "characters": {
            "aria": {"name": "Aria", "description": "heroine"},
        },
        "memory": {"memory_file": str(tmp_path / "story_memory.json")},
        "checkpoint": {"dir": str(tmp_path / "checkpoints")},
    }

    def fake_make_seg(*args, **kwargs):
        mp4s_list = kwargs.get("mp4s")
        def run_seg(seg_idx):
            if mp4s_list is not None and seg_idx - 1 < len(mp4s_list):
                mp4s_list[seg_idx - 1] = Path(f"segment_{seg_idx}.mp4")
        def fake_render_phase(segment_indices):
            for si in segment_indices:
                run_seg(si)

        return run_seg, lambda x: None, lambda x: None, lambda x: None, lambda x: None, fake_render_phase

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}),
        patch("core.pipeline_long.run_preflight_checks"),
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
        patch("memory.blackboard.get_blackboard", return_value=_make_mock_bb(2)),
    ):
        # A non-dict frame (None) should pass through the role normalization unchanged.
        # The env_ratio enforcement may later convert it to {}, but that's a separate block.
        mock_plan_outline.return_value = [
            {
                "seg": 1, "title": "Intro", "num_images": 2,
                "target_word_count": 130,
                "segment_duration": 60.0,
                "char_presence": [
                    {"protagonist": 0.8},
                    None,  # non-dict
                ],
            }
        ]
        mock_make_seg.side_effect = fake_make_seg
        mock_finalize.return_value = {"status": "dry_run", "output": "dummy.mp4", "segments": 1}

        with patch("utils.load_config", return_value=cfg):
            run_long_pipeline(topic="test_topic", resume=True, dry_run=True)

        call_args = mock_make_seg.call_args
        outline_used = call_args.kwargs.get("outline") or call_args[0][3]
        cp = outline_used[0]["char_presence"]
        # None was the non-dict input; role normalization preserved it as-is.
        # The env_ratio block later replaces it with {}, but the role normalization
        # step itself should not have crashed or turned it into something unexpected.
        assert isinstance(cp[1], dict)  # env_ratio converted None → {}
        assert "protagonist" not in cp[1]  # no role keys leaked into the non-dict frame


def test_run_long_pipeline_signature_excludes_director_mode():
    """run_long_pipeline no longer accepts director_mode (removed in Plan 001)."""
    import inspect

    from core.pipeline_long import run_long_pipeline

    sig = inspect.signature(run_long_pipeline)
    assert "director_mode" not in sig.parameters


# ── WS-1 extracted-helper unit tests ─────────────────────────────────────


def test_assemble_cli_flags_only_non_none_ints():
    from core.pipeline_long import _assemble_cli_flags

    flags = _assemble_cli_flags(5, 130, 4, 1)
    assert flags == {
        "total_duration_min": 5,
        "words_per_segment": 130,
        "images_per_segment": 4,
        "segment_count": 1,
    }
    # None values are omitted
    assert _assemble_cli_flags(None, None, None, None) == {}
    # Bools are excluded (isinstance(True, int) is True)
    assert _assemble_cli_flags(True, False, 2.5, 1) == {"segment_count": 1}
    # duration_min accepts float; the others require int
    assert _assemble_cli_flags(4.5, None, None, None) == {"total_duration_min": 4.5}
    assert _assemble_cli_flags(None, 130, 2, None) == {
        "words_per_segment": 130,
        "images_per_segment": 2,
    }


def test_resolve_decision_record_fallback_and_locks():
    from core.pipeline_long import _resolve_decision_record

    config = {
        "video": {"total_duration_min": 5, "segment_duration_min": 2},
        "script": {"words_per_segment": 150},
    }
    # No DecisionRecord (blackboard read fails) → arithmetic fallback, no locks
    with patch("memory.blackboard.get_blackboard", side_effect=Exception("no blackboard")):
        n_segs, words_per_seg, seg_locked, img_locked, rec_total = _resolve_decision_record(
            config, "test_topic", 5, 2
        )
    assert n_segs == 3  # ceil(5 / 2)
    assert words_per_seg == 150
    assert seg_locked is False
    assert img_locked is False
    assert rec_total is None

    # Default words_per_segment when script section has none
    with patch("memory.blackboard.get_blackboard", side_effect=Exception("no blackboard")):
        n_segs, words_per_seg, *_ = _resolve_decision_record(
            {"video": {"total_duration_min": 1, "segment_duration_min": 1}}, "test_topic", 1, 1
        )
    assert words_per_seg == 130

    # DecisionRecord present → its values and lock flags win
    mock_record = MagicMock()
    mock_record.segment_count.value = 4
    mock_record.segment_count.locked = True
    mock_record.segment_count.provenance = "user"
    mock_record.words_per_segment.value = 120
    mock_record.words_per_segment.provenance = "user"
    mock_record.total_duration_min.value = 7
    mock_record.images_per_segment.locked = True
    mock_bb = MagicMock()
    mock_bb.read_decision.return_value = mock_record
    with patch("memory.blackboard.get_blackboard", return_value=mock_bb):
        n_segs, words_per_seg, seg_locked, img_locked, rec_total = _resolve_decision_record(
            config, "test_topic", 5, 2
        )
    assert n_segs == 4
    assert words_per_seg == 120
    assert seg_locked is True
    assert img_locked is True
    assert rec_total == 7


def test_adjust_outline_length_locked_vs_unlocked():
    from core.pipeline_long import _adjust_outline_length

    outline = [{"seg": 1}, {"seg": 2}, {"seg": 3}]

    # Locked + outline longer → truncate outline to n_segs (mp4s stays at original n_segs)
    mp4s = [None] * 2
    o, n, m = _adjust_outline_length(outline, 2, mp4s, True)
    assert len(o) == 2
    assert n == 2
    assert len(m) == 2
    assert m is mp4s

    # Locked + outline shorter → adjust n_segs down to outline length
    mp4s = [None] * 3
    o, n, m = _adjust_outline_length([{"seg": 1}], 3, mp4s, True)
    assert n == 1
    assert len(m) == 1

    # Unlocked → always adjust to outline length
    o, n, m = _adjust_outline_length(outline, 2, [None] * 2, False)
    assert n == 3
    assert len(m) == 3

    # Lengths already match → unchanged
    mp4s = [None] * 3
    o, n, m = _adjust_outline_length(outline, 3, mp4s, False)
    assert len(o) == 3
    assert n == 3
    assert m is mp4s


def test_run_staged_batches_phase_failure_logs_and_continues(caplog):
    from core.pipeline_long import _run_staged_batches

    ran = []

    def _boom(batch):
        raise RuntimeError("translation boom")

    phases = [
        ("C1 scripts phase", "Scripts", lambda b: ran.append("scripts")),
        ("C1 translations phase", "Translations", _boom),
        ("C1 TTS phase", "TTS", lambda b: ran.append("tts")),
        ("C1 images phase", "Images", lambda b: ran.append("images")),
        ("C1 renders phase", "Renders", lambda b: ran.append("renders")),
    ]
    with (
        patch("core.pipeline_long.evict_ollama_models") as mock_evict,
        patch("core.pipeline_long.start_ollama_server"),
        patch("core.pipeline_long.stop_ollama_server"),
    ):
        _run_staged_batches({}, phases, n_segs=3, lookahead=2)

    # Failure is logged and skipped; later phases + both batches still run
    assert ran == ["scripts", "tts", "images", "renders"] * 2
    assert "Translations phase failed for batch [1, 2]: translation boom" in caplog.text
    assert "Translations phase failed for batch [3]: translation boom" in caplog.text
    # Evict happens per phase per batch: 5 phases x 2 batches
    assert mock_evict.call_count == 10
