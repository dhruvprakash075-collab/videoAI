"""test_image_gen.py - test the testable surface of video/image_gen/image_gen.py.

The _stable_diffusion function is GPU-bound and not directly testable. We focus
on the orchestrators and helpers that ARE testable in pure Python.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video.image_gen.image_gen import (
    _maybe_upscale,
    _prompt_cache_key,
    _record_oom_event,
    clear_oom_events,
    generate_images,
    get_oom_report,
    unload_sd_pipeline,
)


@pytest.fixture(autouse=True)
def _reset_oom():
    clear_oom_events()
    yield
    clear_oom_events()


# ── OOM ledger ───────────────────────────────────────────────────────────────


def test_record_oom_event_appends():
    _record_oom_event(
        {"segment_prompt_index": 0, "tier_failed": 1, "fallback_used": True, "steps_used": 4}
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


# ── unload_sd_pipeline ───────────────────────────────────────────────────────


def test_unload_when_no_pipeline(monkeypatch):
    """If pipeline is None, no error, just a debug log."""
    import video.image_gen.image_gen as mod

    monkeypatch.setattr(mod, "_sd_pipe", None)
    unload_sd_pipeline()  # Should not raise


def test_unload_releases_pipeline(monkeypatch):
    """When a pipeline is loaded, it gets unloaded and VRAM cache cleared."""
    import video.image_gen.image_gen as mod

    fake_pipe = MagicMock()
    monkeypatch.setattr(mod, "_sd_pipe", fake_pipe)
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    unload_sd_pipeline()
    assert mod._sd_pipe is None
    assert mod._active_lora_path is None
    fake_torch.cuda.empty_cache.assert_called_once()


def test_unload_handles_lora_failure(monkeypatch):
    fake_pipe = MagicMock()
    fake_pipe.unload_lora_weights.side_effect = RuntimeError("lora fail")
    import video.image_gen.image_gen as mod

    monkeypatch.setattr(mod, "_sd_pipe", fake_pipe)
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    unload_sd_pipeline()
    # Pipeline is still unloaded despite the lora failure
    assert mod._sd_pipe is None


# ── _prompt_cache_key ────────────────────────────────────────────────────────


def test_prompt_cache_key_returns_8_chars():
    cfg = {"steps": 12, "width": 768, "height": 432, "guidance_scale": 6.0}
    key = _prompt_cache_key("a hero standing", cfg, neg_prompt="ugly")
    assert len(key) == 8
    assert all(c in "0123456789abcdef" for c in key)


def test_prompt_cache_key_changes_with_steps():
    cfg1 = {"steps": 12}
    cfg2 = {"steps": 20}
    assert _prompt_cache_key("hero", cfg1) != _prompt_cache_key("hero", cfg2)


def test_prompt_cache_key_changes_with_seed():
    cfg = {"steps": 12}
    assert _prompt_cache_key("hero", cfg, seed=1) != _prompt_cache_key("hero", cfg, seed=2)


def test_prompt_cache_key_changes_with_lora_state():
    cfg = {"steps": 12}
    assert _prompt_cache_key("hero", cfg, lora_state="a") != _prompt_cache_key(
        "hero", cfg, lora_state="b"
    )


def test_prompt_cache_key_changes_with_lora_fingerprint():
    cfg = {"steps": 12}
    assert _prompt_cache_key("hero", cfg, lora_fingerprint="v1") != _prompt_cache_key(
        "hero", cfg, lora_fingerprint="v2"
    )


def test_prompt_cache_key_changes_with_throttled_steps():
    cfg = {"steps": 12}
    base = _prompt_cache_key("hero", cfg)
    throttled = _prompt_cache_key("hero", cfg, throttled_steps=4)
    assert base != throttled


def test_prompt_cache_key_changes_with_acceleration():
    cfg1 = {"steps": 12, "acceleration": {"type": "none"}}
    cfg2 = {"steps": 12, "acceleration": {"type": "lcm", "steps": 4, "guidance_scale": 1.5}}
    assert _prompt_cache_key("hero", cfg1) != _prompt_cache_key("hero", cfg2)


def test_prompt_cache_key_handles_list_prompt():
    cfg = {"steps": 12}
    key = _prompt_cache_key(["hero", "villain"], cfg)
    assert len(key) == 8


def test_prompt_cache_key_handles_non_string_prompt():
    cfg = {"steps": 12}
    key = _prompt_cache_key(42, cfg)
    assert len(key) == 8


def test_prompt_cache_key_uses_config_defaults():
    """If config omits values, defaults are used (steps=12, w=768, h=432, gs=6.0)."""
    k1 = _prompt_cache_key("hero", {})
    k2 = _prompt_cache_key(
        "hero", {"steps": 12, "width": 768, "height": 432, "guidance_scale": 6.0}
    )
    # Both should produce the same key
    assert k1 == k2


def test_prompt_cache_key_uses_sd_model():
    """model_id is derived from sd_model_path or sd_model or 'anyLoRA'."""
    k1 = _prompt_cache_key("hero", {"sd_model_path": "x"})
    k2 = _prompt_cache_key("hero", {"sd_model": "x"})
    k3 = _prompt_cache_key("hero", {"sd_model_path": "x", "sd_model": "y"})
    k4 = _prompt_cache_key("hero", {"sd_model": "y"})  # different model
    # sd_model_path wins when both present
    assert k1 == k3
    # sd_model alone with same value as sd_model_path gives same key
    assert k1 == k2
    # Different sd_model produces different key
    assert k1 != k4


# ── generate_images dispatcher ──────────────────────────────────────────────


def test_generate_images_replicate_dispatches(tmp_path: Path):
    cfg = {"image_gen": {"backend": "replicate"}}
    with patch("video.image_gen.image_gen._replicate", return_value=[tmp_path / "x.png"]) as rep:
        out = generate_images(["prompt1"], tmp_path, cfg)
    assert out == [tmp_path / "x.png"]
    rep.assert_called_once()


def test_generate_images_pexels_dispatches(tmp_path: Path):
    cfg = {"image_gen": {"backend": "pexels"}}
    with patch("video.image_gen.image_gen._pexels", return_value=[tmp_path / "y.png"]) as pex:
        out = generate_images(["prompt1"], tmp_path, cfg)
    assert out == [tmp_path / "y.png"]
    pex.assert_called_once()


def test_generate_images_string_prompts(tmp_path: Path):
    """Semicolon-separated string is split into list."""
    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    with patch("video.image_gen.image_gen._stable_diffusion", return_value=[]) as sd:
        generate_images("a; b; c", tmp_path, cfg)
    # Verify the prompts list has 3 items
    prompts_arg = sd.call_args.args[0]
    assert len(prompts_arg) == 3


def test_generate_images_tuple_input(tmp_path: Path):
    """When called as (prompts_str, neg_prompt_override), tuple is unpacked."""
    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    with patch("video.image_gen.image_gen._stable_diffusion", return_value=[]) as sd:
        generate_images(("a; b", "ugly"), tmp_path, cfg)
    # Verify negative_prompt_override was passed through
    assert sd.call_args.kwargs.get("neg_prompt_override") == "ugly"


def test_generate_images_empty_list(tmp_path: Path):
    """Empty prompts list still gets dispatched."""
    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    with patch("video.image_gen.image_gen._stable_diffusion", return_value=[]) as sd:
        generate_images([], tmp_path, cfg)
    assert sd.call_args.args[0] == []


# ── _maybe_upscale ──────────────────────────────────────────────────────────


def test_maybe_upscale_noop_when_model_is_none():
    """When upscaler.model is 'none', return the image unchanged."""
    from PIL import Image

    img = Image.new("RGB", (256, 144))
    out = _maybe_upscale(img, {"upscaler": {"model": "none"}})
    assert out is img


def test_maybe_upscale_no_upscaler_config():
    """No upscaler config at all → noop."""
    from PIL import Image

    img = Image.new("RGB", (256, 144))
    out = _maybe_upscale(img, {})
    assert out is img


def test_maybe_upscale_lanczos_fallback():
    """When model_path is missing, fall back to Lanczos."""
    from PIL import Image

    img = Image.new("RGB", (256, 144))
    out = _maybe_upscale(
        img,
        {
            "upscaler": {
                "model": "4x-ultrasharp",
                "target_width": 1920,
                "target_height": 1080,
                "model_path": "",  # empty triggers fallback
            }
        },
    )
    assert out.size == (1920, 1080)


def test_maybe_upscale_lanczos_realesrgan_name():
    """realesrgan model name with no model_path also falls back."""
    from PIL import Image

    img = Image.new("RGB", (320, 180))
    out = _maybe_upscale(img, {"upscaler": {"model": "realesrgan", "model_path": ""}})
    assert out.size == (1920, 1080)


def test_maybe_upscale_lanczos_fails_returns_original():
    """If Lanczos itself fails, return the original image."""
    from PIL import Image

    img = Image.new("RGB", (256, 144))
    # Patch PIL Image.LANCZOS to something invalid to make resize fail
    with patch.object(Image.Image, "resize", side_effect=RuntimeError("resize fail")):
        out = _maybe_upscale(img, {"upscaler": {"model": "4x-ultrasharp", "model_path": ""}})
    assert out is img


def test_maybe_upscale_real_esrgan_import_fails_falls_back():
    """If basicsr/realesrgan not importable, fall back to Lanczos."""
    from PIL import Image

    img = Image.new("RGB", (256, 144))
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("basicsr.archs.rrdbnet_arch", "realesrgan"):
            raise ImportError(f"no {name}")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        out = _maybe_upscale(
            img, {"upscaler": {"model": "4x-ultrasharp", "model_path": "fake.ckpt"}}
        )
    # Should fall back to Lanczos
    assert out.size == (1920, 1080)


# ── _replicate (mocked) ──────────────────────────────────────────────────────


def test_replicate_missing_module():
    """If replicate module isn't installed, ImportError is raised."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "replicate":
            raise ImportError("no replicate")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        import pytest

        with pytest.raises(ImportError, match="pip install replicate"):
            from video.image_gen.image_gen import _replicate

            _replicate(["prompt"], Path("/tmp"), {})


# ── _pexels (mocked) ─────────────────────────────────────────────────────────


def test_pexels_missing_api_key(tmp_path: Path):
    """If no Pexels API key, ValueError is raised."""
    from video.image_gen.image_gen import _pexels

    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("PEXELS_API_KEY", None)
        with pytest.raises(ValueError, match="pexels_api_key"):
            _pexels(["prompt"], tmp_path, {})


def test_pexels_uses_config_key(tmp_path: Path):
    """If config has the key, no env var lookup needed."""
    from video.image_gen.image_gen import _pexels

    cfg = {"pexels_api_key": "test_key"}

    class FakeResp:
        def __init__(self, payload, raw=False):
            self.payload = payload
            self.raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            if self.raw:
                return self.payload
            return json.dumps(self.payload).encode()

    photo_data = {
        "photos": [
            {
                "src": {
                    "large2x": "http://example.com/img.jpg",
                    "original": "http://example.com/orig.jpg",
                }
            }
        ]
    }
    # First urlopen returns the API response, second returns raw image bytes
    responses = [FakeResp(photo_data), FakeResp(b"fake_image_bytes", raw=True)]
    with (
        patch("urllib.request.urlopen", side_effect=responses),
        patch("builtins.open", new_callable=MagicMock),
    ):
        result = _pexels(["a hero"], tmp_path, cfg)
    # Should produce one image
    assert len(result) == 1


def test_pexels_no_results_skips(tmp_path: Path):
    """If Pexels returns no photos, that prompt is skipped."""
    from video.image_gen.image_gen import _pexels

    cfg = {"pexels_api_key": "test_key"}

    class FakeResp:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.data).encode()

    # No photos for either prompt
    responses = [FakeResp({"photos": []}), FakeResp({"photos": []})]
    with patch("urllib.request.urlopen", side_effect=responses):
        result = _pexels(["prompt1", "prompt2"], tmp_path, cfg)
    assert result == []


# ── _stable_diffusion tests (mock-heavy) ──────────────────────────────────────


def _make_mock_pipe():
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    # Mock return value of running pipe
    img = MagicMock()
    pipe.return_value.images = [img]
    return pipe


def test_stable_diffusion_success(tmp_path, monkeypatch):
    import diffusers
    import torch

    import video.image_gen.image_gen as mod

    monkeypatch.setattr(mod, "_sd_pipe", None)
    pipe = _make_mock_pipe()
    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (3 * 1024**3, 6 * 1024**3))

    cfg = {
        "image_gen": {
            "backend": "stable_diffusion",
            "steps": 12,
            "width": 768,
            "height": 432,
            "attention_slicing": True,
            "channels_last": True,
            "vae_tiling": True,
            "model_cpu_offload": True,
        }
    }

    with patch("memory.project_store.PROJECTS_ROOT", tmp_path / "nonexistent"):
        res = generate_images(["a scenic view"], tmp_path, cfg)

    assert len(res) == 1
    assert pipe.call_count == 1


def test_stable_diffusion_low_vram_throttling(tmp_path, monkeypatch):
    import diffusers
    import torch

    import video.image_gen.image_gen as mod

    monkeypatch.setattr(mod, "_sd_pipe", None)
    pipe = _make_mock_pipe()
    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    # Return 1.0 GB free VRAM (below critical 1.2 GB limit)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (1.0 * 1024**3, 6 * 1024**3))

    cfg = {
        "image_gen": {
            "backend": "stable_diffusion",
            "steps": 15,
            "width": 768,
            "height": 432,
        }
    }

    with patch("memory.project_store.PROJECTS_ROOT", tmp_path / "nonexistent"):
        res = generate_images(["low vram scene"], tmp_path, cfg)

    assert len(res) == 1
    # Throttled steps should be max(8, int(15 * 0.6)) = 9
    call_kwargs = pipe.call_args.kwargs
    assert call_kwargs["num_inference_steps"] == 9


def test_stable_diffusion_oom_recovery(tmp_path, monkeypatch):
    import diffusers
    import torch

    import video.image_gen.image_gen as mod

    monkeypatch.setattr(mod, "_sd_pipe", None)
    pipe = _make_mock_pipe()

    # Tier 1 OOM, Tier 2 succeeds
    call_count = [0]

    def fake_call(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise torch.cuda.OutOfMemoryError("CUDA OOM Tier 1")
        res_mock = MagicMock()
        res_mock.images = [MagicMock()]
        return res_mock

    pipe.side_effect = fake_call

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (3 * 1024**3, 6 * 1024**3))

    cfg = {
        "image_gen": {
            "backend": "stable_diffusion",
            "steps": 10,
        }
    }

    # Clear OOM report
    clear_oom_events()
    with patch("memory.project_store.PROJECTS_ROOT", tmp_path / "nonexistent"):
        res = generate_images(["oom scene"], tmp_path, cfg)

    assert len(res) == 1
    assert call_count[0] == 2  # 1st tier failed, 2nd tier succeeded

    # Check OOM event recorded
    report = get_oom_report()
    assert len(report) == 1
    assert report[0]["fallback_tier"] == 2
    assert report[0]["steps_used"] == 8


def test_stable_diffusion_oom_cpu_fallback(tmp_path, monkeypatch):
    import diffusers
    import torch

    import video.image_gen.image_gen as mod

    monkeypatch.setattr(mod, "_sd_pipe", None)
    pipe = _make_mock_pipe()

    # Tier 1 OOM, Tier 2 OOM, Tier 3 CPU fallback succeeds
    call_count = [0]

    def fake_call(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise torch.cuda.OutOfMemoryError("CUDA OOM")
        res_mock = MagicMock()
        res_mock.images = [MagicMock()]
        return res_mock

    pipe.side_effect = fake_call

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (3 * 1024**3, 6 * 1024**3))

    cfg = {
        "image_gen": {
            "backend": "stable_diffusion",
            "steps": 12,
        }
    }

    clear_oom_events()
    with patch("memory.project_store.PROJECTS_ROOT", tmp_path / "nonexistent"):
        res = generate_images(["cpu fallback scene"], tmp_path, cfg)

    assert len(res) == 1
    assert call_count[0] == 3  # Tier 1, 2 failed, Tier 3 CPU fallback succeeded

    report = get_oom_report()
    assert len(report) == 1
    assert report[0]["fallback_tier"] == 3
    assert report[0]["steps_used"] == 4


def test_stable_diffusion_oom_all_fail(tmp_path, monkeypatch):
    import diffusers
    import torch

    import video.image_gen.image_gen as mod

    monkeypatch.setattr(mod, "_sd_pipe", None)
    pipe = _make_mock_pipe()

    # All tiers fail
    pipe.side_effect = torch.cuda.OutOfMemoryError("CUDA OOM")

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (3 * 1024**3, 6 * 1024**3))

    cfg = {
        "image_gen": {
            "backend": "stable_diffusion",
        }
    }

    clear_oom_events()
    with patch("memory.project_store.PROJECTS_ROOT", tmp_path / "nonexistent"):
        # Should catch and skip, returning empty list of saved images since all failed
        res = generate_images(["all fail scene"], tmp_path, cfg)

    assert len(res) == 0


def test_stable_diffusion_acceleration_lcm(tmp_path, monkeypatch):
    import diffusers
    import torch

    import video.image_gen.image_gen as mod

    monkeypatch.setattr(mod, "_sd_pipe", None)
    pipe = _make_mock_pipe()
    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (3 * 1024**3, 6 * 1024**3))

    cfg = {
        "image_gen": {
            "backend": "stable_diffusion",
            "acceleration": {
                "type": "lcm",
                "lora_path": "fake_lcm.safetensors",
                "steps": 4,
                "guidance_scale": 1.0,
            },
        }
    }

    # We mock Path.exists to return True for the lora_path so it tries to load and fuse
    original_exists = Path.exists

    def mock_exists(self):
        if "fake_lcm" in str(self):
            return True
        return original_exists(self)

    with (
        patch("pathlib.Path.exists", mock_exists),
        patch("memory.project_store.PROJECTS_ROOT", tmp_path / "nonexistent"),
    ):
        res = generate_images(["lcm scene"], tmp_path, cfg)

    assert len(res) == 1
    # Check that lcm lora was loaded and fused
    pipe.load_lora_weights.assert_called_with("fake_lcm.safetensors", adapter_name="_accel")
    pipe.fuse_lora.assert_called_once()
    assert pipe.call_args.kwargs["num_inference_steps"] == 4
    assert pipe.call_args.kwargs["guidance_scale"] == 1.0


def test_stable_diffusion_xformers_triton_fallback(tmp_path, monkeypatch):
    import diffusers
    import torch

    import video.image_gen.image_gen as mod

    monkeypatch.setattr(mod, "_sd_pipe", None)
    pipe = _make_mock_pipe()

    # Triton import warning flow and triton runtime error retry
    # If platform is linux, it tries to enable xformers
    monkeypatch.setattr("sys.platform", "linux")

    # Make the 1st call fail with "triton" error, and 2nd call succeed
    call_count = [0]

    def fake_call(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("triton error occurs")
        res_mock = MagicMock()
        res_mock.images = [MagicMock()]
        return res_mock

    pipe.side_effect = fake_call

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (3 * 1024**3, 6 * 1024**3))

    cfg = {
        "image_gen": {
            "backend": "stable_diffusion",
            "enable_xformers": True,
        }
    }

    # Mock builtins import to let "import triton" succeed so it executes the xformers branch
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def fake_import(name, *args, **kwargs):
        if name == "triton":
            return MagicMock()
        return real_import(name, *args, **kwargs)

    with (
        patch("builtins.__import__", side_effect=fake_import),
        patch("memory.project_store.PROJECTS_ROOT", tmp_path / "nonexistent"),
    ):
        res = generate_images(["triton fallback scene"], tmp_path, cfg)

    assert len(res) == 1
    assert call_count[0] == 2  # 1st call failed with triton, 2nd call succeeded after disabling
    pipe.disable_xformers_memory_efficient_attention.assert_called_once()
