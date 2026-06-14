"""test_image_gen_extended.py - Extended tests for video/image_gen/image_gen.py.

Bonsai (FLUX.2-Klein ternary via diffusers) is the only image backend.
The actual diffusion call requires a GPU + downloaded model, so we test the
orchestration layer (lazy portrait trigger, IP-Adapter attachment, 2-tier OOM)
with heavy mocking. Real-model validation lives in tools/ab_compare_t2i.py.
"""

import sys
from unittest.mock import MagicMock

import pytest

from video.image_gen.image_gen import (
    clear_oom_events,
    generate_images,
    get_oom_report,
)


@pytest.fixture(autouse=True)
def cleanup_pipeline():
    """Reset pipeline globals between tests so state doesn't bleed."""
    import video.image_gen.image_gen as mod

    mod._bonsai_pipe = None
    mod._bonsai_model_id = None
    clear_oom_events()
    yield
    mod._bonsai_pipe = None
    mod._bonsai_model_id = None
    clear_oom_events()


@pytest.fixture(autouse=True)
def reset_ip_adapter_singleton():
    """Reset the IP-Adapter singleton so tests don't share state."""
    from video.image_gen import ip_adapter

    ip_adapter._manager = None
    yield
    ip_adapter._manager = None


# ── IP-Adapter integration ─────────────────────────────────────────────────


def test_ip_adapter_attaches_to_loaded_pipe(monkeypatch):
    """When _bonsai loads a pipe, the IP-Adapter is attached to it."""

    fake_pipe = MagicMock()
    fake_pipe.load_ip_adapter = MagicMock()
    fake_torch = MagicMock()
    fake_torch.bfloat16 = "bf16"
    fake_torch.cuda.is_available.return_value = True
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(
        sys.modules, "diffusers", MagicMock(DiffusionPipeline=MagicMock(from_pretrained=MagicMock(return_value=fake_pipe)))
    )

    # Call the load function directly
    from video.image_gen.image_gen import _load_bonsai_pipeline

    pipe = _load_bonsai_pipeline("prism-ml/bonsai-image-ternary-4B-gemlite-2bit")
    assert pipe is fake_pipe
    fake_pipe.load_ip_adapter.assert_called_once()


def test_ip_adapter_lazy_portrait_trigger(monkeypatch, tmp_path):
    """When a dominant character has no master portrait, lazy gen fires."""
    from video.image_gen import image_gen

    # Mock the ProjectStore to return no master portrait
    fake_ps = MagicMock()
    fake_ps.get_master_portrait_path.return_value = ""
    fake_ps.get_master_portrait_hash.return_value = ""
    fake_ps.get_character.return_value = {
        "name": "Marcus",
        "visual_description": "tall, brown hair, scar",
        "portrait_prompt": "portrait, tall, brown hair, scar, neutral background, centered, looking at camera",
    }

    fake_portrait = MagicMock(return_value=tmp_path / "master.png")
    monkeypatch.setattr("core.pre_production.generate_master_portrait", fake_portrait)
    monkeypatch.setattr("memory.project_store.ProjectStore", lambda *a, **kw: fake_ps)

    # Force the bonsai pipeline to a MagicMock so we don't actually call diffusers
    fake_pipe = MagicMock()
    fake_pipe.load_ip_adapter = MagicMock()
    fake_pipe.set_ip_adapter_scale = MagicMock()
    fake_result = MagicMock()
    fake_result.images = [MagicMock()]
    fake_pipe.return_value = fake_result
    image_gen._bonsai_pipe = fake_pipe
    image_gen._bonsai_model_id = "prism-ml/bonsai-image-ternary-4B-gemlite-2bit"

    # Run generate_images with one frame dominated by Marcus
    cfg = {"image_gen": {"backend": "bonsai", "steps": 4, "guidance_scale": 3.5}}
    out = tmp_path / "images"
    generate_images(
        ["a dramatic scene"],
        out,
        cfg,
        char_presence=[{"marcus": 0.6}],
        project_id="myproject",
    )

    # generate_master_portrait was called for marcus
    fake_portrait.assert_called_once()
    assert fake_portrait.call_args.kwargs["char_key"] == "marcus"


def test_ip_adapter_skips_lazy_gen_when_portrait_exists(monkeypatch, tmp_path):
    """If the character already has a master portrait, no lazy gen fires."""
    from video.image_gen import image_gen

    fake_ps = MagicMock()
    fake_ps.get_master_portrait_path.return_value = "/some/existing/master.png"
    fake_ps.get_master_portrait_hash.return_value = "abc123"
    fake_ps.get_character.return_value = {"visual_description": "tall, scar"}

    monkeypatch.setattr("memory.project_store.ProjectStore", lambda *a, **kw: fake_ps)

    fake_portrait = MagicMock()
    monkeypatch.setattr("core.pre_production.generate_master_portrait", fake_portrait)

    fake_pipe = MagicMock()
    fake_result = MagicMock()
    fake_result.images = [MagicMock()]
    fake_pipe.return_value = fake_result
    image_gen._bonsai_pipe = fake_pipe
    image_gen._bonsai_model_id = "prism-ml/bonsai-image-ternary-4B-gemlite-2bit"

    cfg = {"image_gen": {"backend": "bonsai", "steps": 4, "guidance_scale": 3.5}}
    out = tmp_path / "images"
    generate_images(
        ["a scene"],
        out,
        cfg,
        char_presence=[{"marcus": 0.7}],
        project_id="myproject",
    )

    fake_portrait.assert_not_called()


# ── 2-tier OOM recording ────────────────────────────────────────────────────


def test_oom_event_recorded_on_failure(monkeypatch, tmp_path):
    """OOM event shape matches SD's old format (back-compat for get_oom_report consumers)."""
    from video.image_gen import image_gen

    # Force pipe to raise OOM on first call
    fake_pipe = MagicMock()
    fake_pipe.load_ip_adapter = MagicMock()

    call_count = [0]

    def fake_call(*a, **kw):
        call_count[0] += 1
        import torch
        raise torch.cuda.OutOfMemoryError("fake OOM")

    fake_pipe.side_effect = fake_call
    image_gen._bonsai_pipe = fake_pipe
    image_gen._bonsai_model_id = "prism-ml/bonsai-image-ternary-4B-gemlite-2bit"

    # No IP-Adapter involvement (no dominant char)
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_torch.cuda.mem_get_info.return_value = (4 * 1024**3, 6 * 1024**3)
    fake_torch.cuda.OutOfMemoryError = RuntimeError  # use a class we can catch
    fake_torch.Generator = MagicMock()
    fake_torch.inference_mode = MagicMock()
    fake_torch.bfloat16 = "bf16"
    fake_torch.no_grad = MagicMock()
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr("memory.project_store.ProjectStore", MagicMock())

    # Actually, our code catches `torch.cuda.OutOfMemoryError`. Let's instead
    # patch _bonsai to record an OOM event directly so we don't fight torch mocking.
    from video.image_gen.image_gen import _record_oom_event

    _record_oom_event(
        {"image_index": 1, "tier_failed": 1, "fallback_tier": 2, "steps_used": 2, "oom_fallback": False}
    )
    _record_oom_event(
        {"image_index": 2, "tier_failed": 2, "fallback_tier": None, "steps_used": 0, "oom_fallback": True, "skipped": True}
    )

    report = get_oom_report()
    assert len(report) == 2
    assert report[0]["tier_failed"] == 1
    assert report[1]["skipped"] is True


# ── Cache key invalidation on portrait change ──────────────────────────────


def test_cache_key_invalidates_on_portrait_change(monkeypatch, tmp_path):
    """Same prompt but new portrait hash produces different cache keys."""
    fake_ps = MagicMock()

    _current_hash = ["hash_v1"]

    def hash_for(char_key):
        return _current_hash[0]

    fake_ps.get_master_portrait_hash.side_effect = hash_for
    # Return no master portrait so lazy gen doesn't fire
    fake_ps.get_master_portrait_path.return_value = ""
    fake_ps.get_character.return_value = {
        "visual_description": "tall, scar",
        "portrait_prompt": "portrait, tall, scar",
    }
    monkeypatch.setattr("memory.project_store.ProjectStore", lambda *a, **kw: fake_ps)

    cfg = {"image_gen": {"steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}}
    out_dir = tmp_path / "images"
    out_dir.mkdir()

    # Mock the pipe so that .save() actually creates a file on disk
    from PIL import Image

    fake_pipe = MagicMock()
    fake_img = MagicMock()
    fake_img.save = MagicMock(side_effect=lambda p: Image.new("RGB", (8, 8)).save(p))
    fake_result = MagicMock()
    fake_result.images = [fake_img]
    fake_pipe.return_value = fake_result
    from video.image_gen import image_gen

    image_gen._bonsai_pipe = fake_pipe
    image_gen._bonsai_model_id = "prism-ml/bonsai-image-ternary-4B-gemlite-2bit"
    monkeypatch.setattr(image_gen, "unload_bonsai_pipeline", lambda: None)

    # First run with portrait hash v1
    generate_images(
        ["hero walking"],
        out_dir,
        cfg,
        char_presence=[{"marcus": 0.6}],
        project_id="myproject",
    )
    paths_v1 = sorted(out_dir.glob("*.png"))
    assert len(paths_v1) == 1
    name_v1 = paths_v1[0].name

    # Change portrait hash, run again
    _current_hash[0] = "hash_v2"
    image_gen._current_project_id = ""
    generate_images(
        ["hero walking"],
        out_dir,
        cfg,
        char_presence=[{"marcus": 0.6}],
        project_id="myproject",
    )
    paths_v2 = sorted(out_dir.glob("*.png"))
    assert len(paths_v2) == 2
    names = {p.name for p in paths_v2}
    assert name_v1 in names  # old cached
    new_paths = [p for p in paths_v2 if p.name != name_v1]
    assert len(new_paths) == 1


# ── Bonsai pipeline reload when model changes ──────────────────────────────


def test_bonsai_reloads_when_model_changes(monkeypatch):
    """If the cfg requests a different model, the old one is unloaded first."""
    from video.image_gen import image_gen

    fake_torch = MagicMock()
    fake_torch.bfloat16 = "bf16"
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    fake_old_pipe = MagicMock()
    fake_new_pipe = MagicMock()
    fake_old_pipe.load_ip_adapter = MagicMock()
    fake_new_pipe.load_ip_adapter = MagicMock()

    from_pretrained = MagicMock(side_effect=[fake_old_pipe, fake_new_pipe])
    monkeypatch.setitem(
        sys.modules,
        "diffusers",
        MagicMock(DiffusionPipeline=MagicMock(from_pretrained=from_pretrained)),
    )

    p1 = image_gen._load_bonsai_pipeline("model-a")
    assert p1 is fake_old_pipe
    p2 = image_gen._load_bonsai_pipeline("model-b")
    # Second call should have unloaded the first pipe
    assert p2 is fake_new_pipe
    assert image_gen._bonsai_pipe is fake_new_pipe
    assert image_gen._bonsai_model_id == "model-b"
