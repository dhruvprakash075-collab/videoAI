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
    _face_inspiration_prompt,
    _maybe_upscale,
    _prompt_cache_key,
    _record_oom_event,
    _resolve_dominant_char_at_threshold,
    _stable_character_reference,
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


def test_stable_character_reference_persists_to_project_store(tmp_path: Path, monkeypatch):
    from memory import project_store
    from memory.project_store import ProjectStore

    monkeypatch.setattr(project_store, "PROJECTS_ROOT", tmp_path / "projects")
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "b.jpg").write_bytes(b"b")
    (refs / "a.jpg").write_bytes(b"a")
    cfg = {"reference_image_dir": str(refs), "reference_usage": "direct"}

    first = _stable_character_reference(cfg, "hero", "proj")
    second = _stable_character_reference(cfg, "hero", "proj")
    store = ProjectStore("proj", root=tmp_path / "projects")

    assert first == second
    assert Path(store.get_master_portrait_path("hero")) == first
    assert store.get_master_portrait_hash("hero")


def test_stable_character_reference_disabled_for_style_inspiration(tmp_path: Path):
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "a.jpg").write_bytes(b"a")
    cfg = {"reference_image_dir": str(refs), "reference_usage": "style_inspiration"}

    assert _stable_character_reference(cfg, "hero", "proj") is None


def test_face_inspiration_prompt_uses_prompt_bank(tmp_path: Path):
    bank = tmp_path / "bank.json"
    bank.write_text('["big eyes", "clean linework", "cel shading"]', encoding="utf-8")
    cfg = {"face_inspiration": {"enabled": True, "prompt_bank": str(bank), "phrases_per_prompt": 2}}

    prompt = _face_inspiration_prompt(cfg, "hero", 0)

    assert prompt
    assert len(prompt.split(", ")) == 2


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
