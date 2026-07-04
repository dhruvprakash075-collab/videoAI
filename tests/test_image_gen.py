"""test_image_gen.py - Test the testable surface of video/image_gen/image_gen.py.

ComfyUI is the image backend. We focus on the orchestrators and helpers
that ARE testable in pure Python.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video.image_gen.image_gen import (
    _comfyui,
    _comfyui_seed,
    _maybe_upscale,
    _prompt_cache_key,
    _record_oom_event,
    _resolve_dominant_char_at_threshold,
    clear_oom_events,
    generate_images,
    get_oom_report,
)


def test_comfyui_keeps_generated_images_when_memory_cleanup_fails(tmp_path: Path):
    image = tmp_path / "scene.png"
    client = MagicMock()
    client.generate_image.return_value = [image]
    client.free_memory.side_effect = RuntimeError("free failed")
    runtime = MagicMock(base_url="http://127.0.0.1:8188")
    runtime.ensure_running.return_value = True
    cfg = {"comfyui": {"unload_after_batch": True}}

    with (
        patch("video.image_gen.comfyui_runtime.get_comfyui_runtime", return_value=runtime),
        patch("video.image_gen.comfyui_client.ComfyUIClient", return_value=client),
        patch("video.image_gen.comfyui_workflow.create_default_workflow", return_value={}),
    ):
        assert _comfyui(["prompt"], tmp_path, cfg) == [image]


@pytest.fixture(autouse=True)
def _reset_oom():
    clear_oom_events()
    yield
    clear_oom_events()


# ── OOM ledger ────────────────────────────────────────────


def test_record_oom_event_appends():
    _record_oom_event(
        {"image_index": 0, "tier_failed": 1, "fallback_tier": 2, "steps_used": 2}
    )
    report = get_oom_report()
    assert len(report) == 1
    assert report[0]["tier_failed"] == 1


def test_get_oom_report_returns_copy():
    _record_oom_event({"x": 1})
    report = get_oom_report()
    report.append({"y": 2})
    # Original is unchanged
    assert len(get_oom_report()) == 1


def test_clear_oom_events():
    _record_oom_event({"x": 1})
    _record_oom_event({"x": 2})
    assert len(get_oom_report()) == 2
    clear_oom_events()
    assert get_oom_report() == []



# ── _prompt_cache_key ──────────────────────────────────────


def test_prompt_cache_key_returns_8_chars():
    cfg = {"steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}
    key = _prompt_cache_key("a hero standing", cfg)
    assert len(key) == 8
    assert all(c in "0123456789abcdef" for c in key)


def test_prompt_cache_key_includes_master_portrait_hash():
    """Same prompt + params but different portrait hash should produce different keys."""
    cfg = {"steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}
    k1 = _prompt_cache_key("a hero", cfg, master_portrait_hash="abc123")
    k2 = _prompt_cache_key("a hero", cfg, master_portrait_hash="def456")
    assert k1 != k2


def test_prompt_cache_key_differs_on_model_id():
    """Different sd_model_path should not collide in cache."""
    cfg_a = {"sd_model_path": "model-a", "steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}
    cfg_b = {"sd_model_path": "model-b", "steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}
    k1 = _prompt_cache_key("hero", cfg_a)
    k2 = _prompt_cache_key("hero", cfg_b)
    assert k1 != k2


def test_prompt_cache_key_uses_defaults():
    """When cfg omits steps/width/etc., defaults are consistent."""
    cfg = {}
    # Two identical calls must produce identical keys
    assert _prompt_cache_key("x", cfg) == _prompt_cache_key("x", cfg)


def test_prompt_cache_key_throttled_steps_included():
    """A throttled image must not be served as a full-quality cache hit."""
    cfg = {"steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}
    k_full = _prompt_cache_key("p", cfg, throttled_steps=4)
    k_throttled = _prompt_cache_key("p", cfg, throttled_steps=2)
    assert k_full != k_throttled


def test_prompt_cache_key_handles_list_prompt():
    cfg = {}
    k1 = _prompt_cache_key(["a", "b"], cfg)
    k2 = _prompt_cache_key("a;b", cfg)
    assert k1 == k2


# ── _comfyui_seed ──────────────────────────────────────


def test_comfyui_seed_explicit_is_reproducible_and_per_frame():
    cfg = {"seed": 1234, "lock_seed": True}
    s0 = _comfyui_seed(cfg, "a forest", 0)
    s1 = _comfyui_seed(cfg, "a forest", 1)
    # Reproducible across calls
    assert s0 == _comfyui_seed(cfg, "a forest", 0)
    # Explicit base is used verbatim for frame 0
    assert s0 == 1234
    # Distinct per frame so frames are not identical
    assert s0 != s1


def test_comfyui_seed_locked_is_prompt_and_frame_sensitive():
    cfg = {"seed": -1, "lock_seed": True}
    s_a0 = _comfyui_seed(cfg, "prompt A", 0)
    assert s_a0 == _comfyui_seed(cfg, "prompt A", 0)  # stable
    assert s_a0 != _comfyui_seed(cfg, "prompt B", 0)  # prompt-sensitive
    assert s_a0 != _comfyui_seed(cfg, "prompt A", 1)  # frame-sensitive
    assert 0 <= s_a0 < 2**32


def test_comfyui_seed_unlocked_returns_none():
    cfg = {"seed": -1, "lock_seed": False}
    assert _comfyui_seed(cfg, "prompt", 0) is None


def test_comfyui_passes_locked_seed_into_workflow(tmp_path: Path):
    """With lock_seed on, the same seed reaches the workflow patcher each run."""
    client = MagicMock()
    client.generate_image.return_value = [tmp_path / "scene_01.png"]
    runtime = MagicMock(base_url="http://127.0.0.1:8188")
    runtime.ensure_running.return_value = True
    cfg = {"lock_seed": True, "seed": -1, "comfyui": {}}

    seen_seeds = []

    def _capture(**kwargs):
        seen_seeds.append(kwargs.get("seed"))
        return {}

    with (
        patch("video.image_gen.comfyui_runtime.get_comfyui_runtime", return_value=runtime),
        patch("video.image_gen.comfyui_client.ComfyUIClient", return_value=client),
        patch("video.image_gen.comfyui_workflow.create_default_workflow", side_effect=_capture),
    ):
        _comfyui(["a forest"], tmp_path, cfg)
        _comfyui(["a forest"], tmp_path, cfg)

    assert seen_seeds[0] is not None
    assert seen_seeds[0] == seen_seeds[1]


def test_comfyui_passes_locked_seed_into_workflow_patcher(tmp_path: Path):
    """With a configured workflow_path, the locked seed reaches WorkflowPatcher.patch_all each run.

    Mirrors test_comfyui_passes_locked_seed_into_workflow but exercises the
    WorkflowPatcher branch (taken when comfyui.workflow_path is set, which the
    live config.yaml does), closing the seed-wiring coverage gap on that path.
    """
    client = MagicMock()
    client.generate_image.return_value = [tmp_path / "scene_01.png"]
    runtime = MagicMock(base_url="http://127.0.0.1:8188")
    runtime.ensure_running.return_value = True
    cfg = {"lock_seed": True, "seed": -1, "comfyui": {"workflow_path": "workflow.json"}}

    seen_seeds = []

    def _capture(**kwargs):
        seen_seeds.append(kwargs.get("seed"))
        patched = MagicMock()
        patched.get_workflow.return_value = {}
        return patched

    fake_patcher = MagicMock()
    fake_patcher.patch_all.side_effect = _capture

    with (
        patch("video.image_gen.comfyui_runtime.get_comfyui_runtime", return_value=runtime),
        patch("video.image_gen.comfyui_client.ComfyUIClient", return_value=client),
        patch("video.image_gen.comfyui_workflow.WorkflowPatcher", return_value=fake_patcher),
    ):
        _comfyui(["a forest"], tmp_path, cfg)
        _comfyui(["a forest"], tmp_path, cfg)

    assert fake_patcher.patch_all.call_count == 2
    assert seen_seeds[0] is not None
    assert seen_seeds[0] == seen_seeds[1]


# ── _resolve_dominant_char ──────────────────────────────────


def test_resolve_dominant_char_above_threshold():
    cp = {"marcus": 0.6, "elena": 0.2}
    key, weight = _resolve_dominant_char_at_threshold(cp, 0.3)
    assert key == "marcus"
    assert weight == 0.6


def test_resolve_dominant_char_below_threshold():
    """Weight < 0.3 means no dominant char (env frame)."""
    cp = {"marcus": 0.2, "elena": 0.1}
    key, weight = _resolve_dominant_char_at_threshold(cp, 0.3)
    assert key is None
    assert weight == 0.0


def test_resolve_dominant_char_empty():
    assert _resolve_dominant_char_at_threshold({}, 0.3) == (None, 0.0)
    assert _resolve_dominant_char_at_threshold(None, 0.3) == (None, 0.0)


def test_resolve_dominant_char_picks_max_above_threshold():
    cp = {"marcus": 0.4, "elena": 0.5}
    key, weight = _resolve_dominant_char_at_threshold(cp, 0.3)
    assert key == "elena"
    assert weight == 0.5


# ── generate_images dispatcher ────────────────────────────────


def test_generate_images_string_prompts(tmp_path: Path):
    """Semicolon-separated string is split into list."""
    cfg = {"image_gen": {"backend": "comfyui"}}
    with patch("video.image_gen.image_gen._comfyui", return_value=[]) as cfn:
        generate_images("a; b; c", tmp_path, cfg)
    prompts_arg = cfn.call_args.args[0]
    assert len(prompts_arg) == 3


def test_generate_images_empty_list(tmp_path: Path):
    """Empty prompts list still gets dispatched."""
    cfg = {"image_gen": {"backend": "comfyui"}}
    with patch("video.image_gen.image_gen._comfyui", return_value=[]) as cfn:
        generate_images([], tmp_path, cfg)
    assert cfn.call_args.args[0] == []


def test_generate_images_qwen_preflight_pass_dispatches_two_pass(tmp_path: Path):
    cfg = {
        "image_gen": {
            "backend": "comfyui",
            "composition_mode": "qwen_edit",
            "qwen_edit": {"enabled": True},
        }
    }
    with (
        patch("video.image_gen.image_gen._qwen_preflight_issues", return_value=[]) as preflight,
        patch("video.image_gen.image_gen._qwen_resource_issues", return_value=[]),
        patch("video.image_gen.image_gen._comfyui_qwen_edit", return_value=[]) as qwen,
        patch("video.image_gen.image_gen._comfyui", return_value=[]) as comfy,
    ):
        generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.1}], project_id="p")

    preflight.assert_called_once()
    qwen.assert_called_once()
    comfy.assert_not_called()


def test_generate_images_qwen_trigger_disabled_uses_one_pass(tmp_path: Path):
    """trigger=disabled must skip the Qwen two-pass entirely (incl. preflight)."""
    cfg = {
        "image_gen": {
            "backend": "comfyui",
            "composition_mode": "qwen_edit",
            "qwen_edit": {"enabled": True, "trigger": "disabled"},
        }
    }
    with (
        patch("video.image_gen.image_gen._qwen_preflight_issues", return_value=[]) as preflight,
        patch("video.image_gen.image_gen._comfyui_qwen_edit", return_value=[]) as qwen,
        patch("video.image_gen.image_gen._comfyui", return_value=[]) as comfy,
    ):
        generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.9}], project_id="p")

    preflight.assert_not_called()
    qwen.assert_not_called()
    comfy.assert_called_once()


def test_generate_images_qwen_preflight_failure_uses_one_pass_comfyui(tmp_path: Path):
    cfg = {
        "image_gen": {
            "backend": "comfyui",
            "composition_mode": "qwen_edit",
            "qwen_edit": {"enabled": True, "model_path": ""},
        }
    }
    with (
        patch("video.image_gen.image_gen._qwen_preflight_issues", return_value=["missing model"]) as preflight,
        patch("video.image_gen.image_gen._comfyui_qwen_edit", return_value=[]) as qwen,
        patch("video.image_gen.image_gen._comfyui", return_value=[]) as comfy,
    ):
        generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.1}], project_id="p")

    preflight.assert_called_once()
    qwen.assert_not_called()
    comfy.assert_called_once()


def test_generate_images_qwen_runtime_failure_raises(tmp_path: Path):
    cfg = {
        "image_gen": {
            "backend": "comfyui",
            "composition_mode": "qwen_edit",
            "qwen_edit": {"enabled": True},
        }
    }
    with (
        patch("video.image_gen.image_gen._qwen_preflight_issues", return_value=[]),
        patch("video.image_gen.image_gen._qwen_resource_issues", return_value=[]),
        patch(
            "video.image_gen.image_gen._comfyui_qwen_edit",
            side_effect=RuntimeError("qwen exploded"),
        ) as qwen,
    ):
        with pytest.raises(RuntimeError, match="qwen exploded"):
            generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.1}], project_id="p")

    qwen.assert_called_once()


def test_comfyui_qwen_edit_only_reposes_character_frames(tmp_path: Path):
    from video.image_gen import image_gen
    from video.image_gen.qwen_repose import QwenEditResult

    images = [tmp_path / "scene_01.png", tmp_path / "scene_02.png", tmp_path / "scene_03.png"]
    cfg = {"qwen_edit": {"character_threshold": 0.05}}
    with (
        patch.object(image_gen, "_comfyui", return_value=images),
        patch.object(image_gen, "_free_comfyui_memory") as free_memory,
        patch.object(image_gen, "_qwen_resource_issues", return_value=[]),
        patch("video.image_gen.qwen_repose.repose_character_detailed") as repose,
    ):
        repose.side_effect = [
            QwenEditResult(status="edited", output_path=str(tmp_path / "edited_01.png"), reason=""),
            QwenEditResult(status="edited", output_path=str(tmp_path / "edited_03.png"), reason=""),
        ]

        result = image_gen._comfyui_qwen_edit(
            ["first", "second", "third"],
            tmp_path,
            cfg,
            char_presence=[{"hero": 0.1}, {}, {"villain": 0.2}],
            project_id="project-a",
        )

    free_memory.assert_called_once_with(cfg)
    assert repose.call_count == 2
    assert repose.call_args_list[0].args[:4] == (
        str(images[0]),
        "hero",
        "first",
        str(images[0]),
    )
    assert repose.call_args_list[1].args[:4] == (
        str(images[2]),
        "villain",
        "third",
        str(images[2]),
    )
    assert result == [tmp_path / "edited_01.png", images[1], tmp_path / "edited_03.png"]


def test_comfyui_qwen_edit_records_degradation_on_failure(tmp_path: Path):
    """A failed repose keeps the background and records a degradation."""
    from video.image_gen import image_gen
    from video.image_gen.qwen_repose import QwenEditResult

    images = [tmp_path / "scene_01.png"]
    cfg = {"qwen_edit": {"character_threshold": 0.05}}
    with (
        patch.object(image_gen, "_comfyui", return_value=images),
        patch.object(image_gen, "_free_comfyui_memory"),
        patch.object(image_gen, "_qwen_resource_issues", return_value=[]),
        patch.object(image_gen, "_log_qwen_degradation") as degrade,
        patch(
            "video.image_gen.qwen_repose.repose_character_detailed",
            return_value=QwenEditResult(
                status="failed", output_path=str(images[0]), reason="comfyui error"
            ),
        ),
    ):
        result = image_gen._comfyui_qwen_edit(
            ["first"],
            tmp_path,
            cfg,
            char_presence=[{"hero": 0.9}],
            project_id="project-a",
        )

    degrade.assert_called_once()
    assert result == [images[0]]


def test_comfyui_qwen_edit_respects_character_threshold(tmp_path: Path):
    from video.image_gen import image_gen
    from video.image_gen.qwen_repose import QwenEditResult

    images = [tmp_path / "scene_01.png", tmp_path / "scene_02.png"]
    cfg = {"qwen_edit": {"character_threshold": 0.5}}
    with (
        patch.object(image_gen, "_comfyui", return_value=images),
        patch.object(image_gen, "_free_comfyui_memory"),
        patch.object(image_gen, "_qwen_resource_issues", return_value=[]),
        patch(
            "video.image_gen.qwen_repose.repose_character_detailed",
            return_value=QwenEditResult(status="edited", output_path=str(images[1]), reason=""),
        ) as repose,
    ):
        result = image_gen._comfyui_qwen_edit(
            ["low", "high"],
            tmp_path,
            cfg,
            char_presence=[{"hero": 0.49}, {"hero": 0.5}],
            project_id="project-a",
        )

    repose.assert_called_once()
    assert repose.call_args.args[1] == "hero"
    assert repose.call_args.args[2] == "high"
    assert result == images


def test_qwen_resource_gate_reports_low_headroom():
    from video.image_gen import image_gen

    cfg = {"qwen_edit": {"min_available_ram_gib": 8.0, "min_free_vram_mib": 5000}}
    with (
        patch.object(image_gen, "_available_ram_gib", return_value=4.5),
        patch.object(image_gen, "_free_vram_mib", return_value=4200),
    ):
        issues = image_gen._qwen_resource_issues(cfg)

    assert issues == [
        "available RAM 4.50 GiB is below 8.00 GiB",
        "free VRAM 4200 MiB is below 5000 MiB",
    ]


def test_qwen_resource_gate_allows_calibrated_headroom():
    from video.image_gen import image_gen

    with (
        patch.object(image_gen, "_available_ram_gib", return_value=8.5),
        patch.object(image_gen, "_free_vram_mib", return_value=5900),
    ):
        assert image_gen._qwen_resource_issues({"qwen_edit": {}}) == []


def test_qwen_post_background_resource_failure_keeps_backgrounds(tmp_path: Path):
    from video.image_gen import image_gen

    images = [tmp_path / "scene_01.png"]
    with (
        patch.object(image_gen, "_comfyui", return_value=images),
        patch.object(image_gen, "_free_comfyui_memory"),
        patch.object(image_gen, "_qwen_resource_issues", return_value=["low RAM"]),
        patch.object(image_gen, "_log_qwen_degradation") as degrade,
        patch("video.image_gen.qwen_repose.repose_character_detailed") as repose,
    ):
        result = image_gen._comfyui_qwen_edit(
            ["frame"],
            tmp_path,
            {"qwen_edit": {"character_threshold": 0.05}},
            char_presence=[{"hero": 0.9}],
        )

    assert result == images
    degrade.assert_called_once_with(0, "resource gate after background pass: low RAM")
    repose.assert_not_called()


def test_generate_images_passes_project_id(tmp_path: Path):
    """project_id is forwarded to comfyui for project-scoped lookups."""
    cfg = {"image_gen": {"backend": "comfyui"}}
    with patch("video.image_gen.image_gen._comfyui", return_value=[]) as cfn:
        generate_images(["p"], tmp_path, cfg, project_id="myproject")
    assert cfn.call_args is not None


def test_generate_images_wraps_misc_prompt_and_rejects_unknown_backend(tmp_path: Path):
    cfg = {"image_gen": {"backend": "comfyui"}}
    with patch("video.image_gen.image_gen._comfyui", return_value=[]) as cfn:
        generate_images(123, tmp_path, cfg)
    assert cfn.call_args.args[0] == ["123"]

    with pytest.raises(ValueError, match="Unsupported image backend"):
        generate_images(["p"], tmp_path, {"image_gen": {"backend": "nope"}})


def test_qwen_preflight_and_resource_probe_error_paths():
    from video.image_gen import image_gen

    with patch("video.image_gen.qwen_repose.preflight_qwen_edit", side_effect=RuntimeError("bad")):
        assert image_gen._qwen_preflight_issues({}) == ["qwen_edit preflight raised: bad"]

    with (
        patch.object(image_gen, "_available_ram_gib", return_value=None),
        patch.object(image_gen, "_free_vram_mib", return_value=None),
    ):
        assert image_gen._qwen_resource_issues({"qwen_edit": {}}) == [
            "available RAM could not be measured",
            "free NVIDIA VRAM could not be measured",
        ]


def test_local_resource_helpers_handle_missing_tools_and_bad_output():
    from video.image_gen import image_gen

    with patch("video.image_gen.image_gen.shutil.which", return_value=None):
        assert image_gen._free_vram_mib() is None

    bad_result = MagicMock(returncode=0, stdout="")
    with (
        patch("video.image_gen.image_gen.shutil.which", return_value="nvidia-smi"),
        patch("video.image_gen.image_gen.subprocess.run", return_value=bad_result),
    ):
        assert image_gen._free_vram_mib() is None

    with (
        patch("video.image_gen.image_gen.os.name", "posix"),
        patch("video.image_gen.image_gen.os.sysconf", side_effect=OSError("no sysconf"), create=True),
    ):
        assert image_gen._available_ram_gib() is None


def test_maybe_upscale_lanczos_and_failure_fallback():
    img = MagicMock()
    img.size = (10, 10)
    resized = MagicMock()
    resized.size = (20, 20)
    img.resize.return_value = resized

    assert _maybe_upscale(img, {"upscaler": {"target_width": 20, "target_height": 20}}) is img
    assert _maybe_upscale(
        img,
        {"upscaler": {"model": "lanczos", "target_width": 20, "target_height": 20}},
    ) is resized

    img.resize.side_effect = RuntimeError("resize failed")
    assert _maybe_upscale(
        img,
        {"upscaler": {"model": "lanczos", "target_width": 20, "target_height": 20}},
    ) is img


def test_free_memory_and_degradation_logging_are_best_effort():
    from video.image_gen import image_gen

    fake_client = MagicMock()
    fake_runtime = MagicMock(base_url="http://127.0.0.1:8188")
    with (
        patch("video.image_gen.comfyui_runtime.get_comfyui_runtime", return_value=fake_runtime),
        patch("video.image_gen.comfyui_client.ComfyUIClient", return_value=fake_client),
    ):
        image_gen._free_comfyui_memory({"comfyui": {"timeout_seconds": 7}})
    fake_client.free_memory.assert_called_once()

    with patch("video.image_gen.comfyui_runtime.get_comfyui_runtime", side_effect=RuntimeError("down")):
        image_gen._free_comfyui_memory({})

    with patch("agents.ui_state.UIState.add_degradation") as add:
        image_gen._log_qwen_degradation(2, "")
    add.assert_called_once_with(2, "qwen_edit_fallback", "qwen edit did not composite the character")

    with patch("agents.ui_state.UIState.add_degradation", side_effect=RuntimeError("ui down")):
        image_gen._log_qwen_degradation(3, "x")


def test_comfyui_qwen_edit_returns_empty_background_batch(tmp_path: Path):
    from video.image_gen import image_gen

    with patch.object(image_gen, "_comfyui", return_value=[]) as comfy:
        assert image_gen._comfyui_qwen_edit(["p"], tmp_path, {}, char_presence=[]) == []
    comfy.assert_called_once()
