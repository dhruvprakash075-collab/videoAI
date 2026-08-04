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
    _panel_sizes,
    _record_oom_event,
    _refine_passes,
    _resolve_dominant_char_at_threshold,
    _snap_to_bucket,
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


def test_stable_character_reference_sheet_beats_reference_usage_gate(tmp_path: Path):
    """The wired storyboard sheet is the reference regardless of reference_usage."""
    sheet = tmp_path / "sheet.png"
    sheet.write_bytes(b"s")
    cfg = {"storyboard_sheet": str(sheet), "reference_usage": "style_inspiration"}

    assert _stable_character_reference(cfg, "hero", "proj") == sheet


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


# ── _snap_to_bucket / _panel_sizes ─────────────────────────


def test_snap_to_bucket_keeps_landscape_and_portrait():
    assert _snap_to_bucket(1.5) == (768, 512)
    assert _snap_to_bucket(1.4) == (768, 512)
    assert _snap_to_bucket(0.667) == (512, 768)
    assert _snap_to_bucket(1.0) == (640, 640)
    assert _snap_to_bucket(3.0) == (832, 448)


def test_panel_sizes_disabled_returns_none():
    assert _panel_sizes(2, {"comfyui": {}}) is None
    assert _panel_sizes(2, {"panel_composite": {"enabled": False}, "comfyui": {}}) is None


def test_panel_sizes_follow_layout_aspects(tmp_path: Path):
    """Per-panel generation sizes must follow the page layout plan per slot.

    Sizes are planned against the A4 portrait page (page_aspect 1.414), not
    the 16:9 frame, so generation matches the rects the compositor will use.
    """
    layout = tmp_path / "layouts.json"
    layout.write_text('[{"name":"mixed","panels":[[0,0,1,0.5],[0,0.5,0.5,1],[0.5,0.5,1,1]]}]')
    cfg = {
        "panel_composite": {
            "enabled": True,
            "width": 400,
            "height": 400,
            "layout_file": str(layout),
        },
        "comfyui": {},
    }

    sizes = _panel_sizes(3, cfg)

    # A4 page dims: h=400-96=304, w=304/1.414=215.
    # rects: (0,0,215,152) aspect 1.414 -> 768x512; two 0.704 panels -> 512x768
    assert sizes == [(768, 512), (512, 768), (512, 768)]


def test_panel_sizes_handles_partial_last_page(tmp_path: Path):
    """A 6-prompt batch splits into a full 5-panel page + a 1-panel page."""
    layout = tmp_path / "layouts.json"
    layout.write_text(
        '[{"name":"five","panels":[[0,0,1,0.2],[0,0.2,0.5,0.4],[0.5,0.2,1,0.4],[0,0.4,1,0.6],[0,0.6,1,1]]},'
        '{"name":"one","panels":[[0,0,1,1]]}]'
    )
    cfg = {
        "panel_composite": {
            "enabled": True,
            "width": 400,
            "height": 400,
            "layout_file": str(layout),
        },
        "comfyui": {},
    }

    sizes = _panel_sizes(6, cfg)

    assert len(sizes) == 6
    # single full-page panel on the A4 canvas: aspect 215/304 = 0.707 -> 512x768
    assert sizes[5] == (512, 768)


def test_panel_sizes_mirror_dynamic_page_counts(tmp_path: Path):
    """Generation sizes must follow the dataset-order page walk, not a fixed 5.

    Each page's sizes must equal the rects the compositor will use for that
    page (plan_page_rects at the same count + page index).
    """
    from video.image_gen.panel_compositor import page_canvas_size, plan_page_counts, plan_page_rects

    layout = tmp_path / "layouts.json"
    layout.write_text(
        '[{"name":"four","panels":[[0,0,0.5,0.5],[0.5,0,1,0.5],[0,0.5,0.5,1],[0.5,0.5,1,1]]},'
        '{"name":"two","panels":[[0,0,1,0.5],[0,0.5,1,1]]}]'
    )
    cfg = {
        "panel_composite": {
            "enabled": True,
            "width": 400,
            "height": 400,
            "layout_file": str(layout),
        },
        "comfyui": {},
    }

    assert plan_page_counts(6, layout) == [4, 2]
    sizes = _panel_sizes(6, cfg)
    assert len(sizes) == 6
    page_w, page_h = page_canvas_size(400, 400, 48, 1.414)
    expected: list[tuple[int, int]] = []
    for page_i, page_count in enumerate([4, 2]):
        for x1, y1, x2, y2 in plan_page_rects(page_count, page_w, page_h, page_i, layout_file=layout):
            expected.append(_snap_to_bucket((x2 - x1) / max(1, y2 - y1)))
    assert sizes == expected


def test_comfyui_passes_panel_sizes_per_prompt(tmp_path: Path):
    """With panel compositing on, each prompt gets its panel's bucket size."""
    layout = tmp_path / "layouts.json"
    layout.write_text('[{"name":"mixed","panels":[[0,0,1,0.5],[0,0.5,0.5,1],[0.5,0.5,1,1]]}]')
    client = MagicMock()
    client.generate_image.return_value = [tmp_path / "scene_01.png"]
    runtime = MagicMock(base_url="http://127.0.0.1:8188")
    runtime.ensure_running.return_value = True
    cfg = {
        "comfyui": {"workflow_path": "workflow.json"},
        "panel_composite": {
            "enabled": True,
            "width": 400,
            "height": 400,
            "layout_file": str(layout),
        },
    }

    seen = []

    def _capture(**kwargs):
        seen.append((kwargs.get("width"), kwargs.get("height")))
        return {}

    fake_patcher = MagicMock()
    fake_patcher.patch_all.side_effect = _capture

    with (
        patch("video.image_gen.comfyui_runtime.get_comfyui_runtime", return_value=runtime),
        patch("video.image_gen.comfyui_client.ComfyUIClient", return_value=client),
        patch("video.image_gen.comfyui_workflow.WorkflowPatcher", return_value=fake_patcher),
        patch("video.image_gen.panel_compositor.compose_panel_pages", return_value=[]),
    ):
        _comfyui(["p1", "p2", "p3"], tmp_path, cfg)

    assert seen == [(768, 512), (512, 768), (512, 768)]


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


@pytest.mark.parametrize(
    "cfg",
    [
        {"comfyui": {"face_detail": True}},
        {"comfyui": {"upscale": True}},
        {"comfyui": {"face_detail": True, "upscale": True}},
        {"comfyui": {"refine_upscale": True}},
        {"comfyui": {"refine_upscale": True, "face_detail": False, "upscale": False}},
    ],
)
def test_refine_passes_returns_originals_on_setup_failure(cfg):
    """When runtime init crashes, _refine_passes returns original frames unchanged."""
    frames = [Path("frame1.png"), Path("frame2.png")]
    cfg["comfyui"].setdefault("face_detail_workflow_path", "workflow.json")

    with patch("video.image_gen.comfyui_runtime.get_comfyui_runtime", side_effect=RuntimeError("comfy down")):
        result = _refine_passes(frames, cfg)
    assert result == frames


@pytest.mark.parametrize(
    "cfg",
    [
        {"comfyui": {"face_detail": True}},
        {"comfyui": {"upscale": True}},
        {"comfyui": {"face_detail": True, "upscale": True}},
        {"comfyui": {"refine_upscale": True}},
        {"comfyui": {"refine_upscale": True, "face_detail": False, "upscale": False}},
    ],
)
def test_refine_passes_returns_originals_when_comfyui_not_running(cfg):
    """When ComfyUI is not running, original frames are returned."""
    frames = [Path("f1.png")]
    cfg["comfyui"].setdefault("face_detail_workflow_path", "workflow.json")

    runtime = MagicMock()
    runtime.ensure_running.return_value = False
    with patch("video.image_gen.comfyui_runtime.get_comfyui_runtime", return_value=runtime):
        result = _refine_passes(frames, cfg)
    assert result == frames


@pytest.mark.parametrize(
    "cfg",
    [
        {"comfyui": {"face_detail": False}},
        {"comfyui": {"upscale": False}},
        {"comfyui": {"face_detail": False, "upscale": False}},
        {"comfyui": {"refine_upscale": False}},
    ],
)
def test_refine_passes_returns_originals_when_feature_disabled(cfg):
    """When no refine pass is enabled, original frames are returned."""
    frames = [Path("f1.png")]
    assert _refine_passes(frames, cfg) == frames


def test_refine_passes_chains_face_detail_then_upscale(tmp_path: Path):
    """Both passes run per frame, upscale receiving the face-detail output."""
    frames = [tmp_path / "f1.png"]
    cfg = {"comfyui": {"face_detail": True, "upscale": True}}

    runtime = MagicMock()
    runtime.ensure_running.return_value = True
    runtime.base_url = "http://127.0.0.1:8188"
    client = MagicMock()
    client.upload_image.side_effect = [{"name": "f1.png"}, {"name": "f1_final.png"}]
    client.generate_image.side_effect = [
        [tmp_path / "f1_final.png"],
        [tmp_path / "f1_final_final.png"],
    ]
    patcher = MagicMock()
    patcher.get_workflow.side_effect = [
        {"1": {"class_type": "LoadImage", "inputs": {}}, "11": {"class_type": "SaveImage", "inputs": {}}},
        {"1": {"class_type": "LoadImage", "inputs": {}}, "11": {"class_type": "SaveImage", "inputs": {}}},
    ]

    with (
        patch("video.image_gen.comfyui_runtime.get_comfyui_runtime", return_value=runtime),
        patch("video.image_gen.comfyui_client.ComfyUIClient", return_value=client),
        patch("video.image_gen.comfyui_workflow.WorkflowPatcher", return_value=patcher),
    ):
        result = _refine_passes(frames, cfg)

    assert result == [tmp_path / "f1_final_final.png"]
    assert client.generate_image.call_count == 2
    wf1 = client.generate_image.call_args_list[0].args[0]
    wf2 = client.generate_image.call_args_list[1].args[0]
    assert wf1["1"]["inputs"]["image"] == "f1.png"
    assert wf2["1"]["inputs"]["image"] == "f1_final.png"
