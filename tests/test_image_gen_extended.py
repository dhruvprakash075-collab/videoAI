"""test_image_gen_extended.py - Extended unit tests for video/image_gen/image_gen.py"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import diffusers
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video.image_gen.image_gen import (
    clear_oom_events,
    generate_images,
)


@pytest.fixture(autouse=True)
def cleanup_pipeline():
    import video.image_gen.image_gen as mod

    mod._sd_pipe = None
    mod._active_lora_path = None
    clear_oom_events()
    yield
    mod._sd_pipe = None
    mod._active_lora_path = None
    clear_oom_events()


def test_tf32_exception(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    class MockMatmul:
        @property
        def allow_tf32(self):
            return False

        @allow_tf32.setter
        def allow_tf32(self, val):
            raise Exception("no tf32 matmul")

    monkeypatch.setattr(torch.backends.cuda, "matmul", MockMatmul())

    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1


def test_dpm_scheduler_exception(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    with patch(
        "diffusers.DPMSolverMultistepScheduler.from_config", side_effect=Exception("dpm fail")
    ):
        cfg = {"image_gen": {"backend": "stable_diffusion"}}
        res = generate_images(["test"], tmp_path, cfg)
        assert len(res) == 1


def test_acceleration_loading_fusing_fail(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe
    pipe.fuse_lora.side_effect = Exception("fuse fail")

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    cfg = {
        "image_gen": {
            "backend": "stable_diffusion",
            "acceleration": {"type": "lcm", "lora_path": "accel.safetensors"},
        }
    }

    with patch("pathlib.Path.exists", return_value=True):
        res = generate_images(["test"], tmp_path, cfg)
        assert len(res) == 1
        pipe.load_lora_weights.assert_called_with("accel.safetensors", adapter_name="_accel")
        pipe.fuse_lora.assert_called()


def test_acceleration_loading_path_not_found(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    cfg = {
        "image_gen": {
            "backend": "stable_diffusion",
            "acceleration": {"type": "lcm", "lora_path": "accel.safetensors"},
        }
    }

    with patch("pathlib.Path.exists", return_value=False):
        res = generate_images(["test"], tmp_path, cfg)
        assert len(res) == 1
        pipe.load_lora_weights.assert_not_called()


def test_acceleration_lcm_scheduler_fail(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    cfg = {"image_gen": {"backend": "stable_diffusion", "acceleration": {"type": "lcm"}}}

    with patch("diffusers.LCMScheduler.from_config", side_effect=Exception("lcm config fail")):
        res = generate_images(["test"], tmp_path, cfg)
        assert len(res) == 1


def test_acceleration_general_exception(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    class BadDict(dict):
        def get(self, key, default=None):
            if key == "lora_path":
                raise Exception("bad get")
            return super().get(key, default)

    cfg = {"image_gen": {"backend": "stable_diffusion", "acceleration": BadDict({"type": "lcm"})}}

    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1


def test_channels_last_exception(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.unet.to.side_effect = Exception("channels_last fail")
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    cfg = {"image_gen": {"backend": "stable_diffusion", "channels_last": True}}

    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1


def test_model_cpu_offload_exception(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.enable_model_cpu_offload.side_effect = Exception("offload fail")
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    cfg = {"image_gen": {"backend": "stable_diffusion", "model_cpu_offload": True}}

    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1
    pipe.to.assert_called_with("cuda")


def test_group_offload_exception(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.enable_group_offload.side_effect = Exception("group offload fail")
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    cfg = {"image_gen": {"backend": "stable_diffusion", "group_offload": True}}

    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1
    pipe.to.assert_called_with("cuda")


def test_vae_slicing_exception(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.enable_vae_slicing.side_effect = Exception("vae slicing fail")
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    cfg = {"image_gen": {"backend": "stable_diffusion"}}

    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1


def test_vae_tiling_exception(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.enable_vae_tiling.side_effect = Exception("vae tiling fail")
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    cfg = {"image_gen": {"backend": "stable_diffusion", "vae_tiling": True}}

    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1


def test_lora_face_lock_exceptions(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    def load_lora_mock(path, adapter_name):
        if adapter_name == "char2":
            raise Exception("lora load error")

    pipe.load_lora_weights.side_effect = load_lora_mock

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    cfg = {"image_gen": {"backend": "stable_diffusion"}}

    path1 = MagicMock()
    path1.exists.return_value = True
    path2 = MagicMock()
    path2.exists.return_value = True
    path3 = MagicMock()
    path3.exists.return_value = False

    lora_paths = {"char1": path1, "char2": path2, "char3": path3}

    res = generate_images(["test"], tmp_path, cfg, lora_paths=lora_paths)
    assert len(res) == 1


def test_projects_seed_map_parsing(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    proj1 = projects_root / "proj1"
    proj1.mkdir()
    (proj1 / "project.json").write_text(
        json.dumps({"visual_locks": {"char_a": {"seed": 12345}}}), encoding="utf-8"
    )

    proj2 = projects_root / "proj2"
    proj2.mkdir()
    (proj2 / "project.json").write_text("invalid json", encoding="utf-8")

    proj3 = projects_root / "proj3"
    proj3.mkdir()

    cfg = {"image_gen": {"backend": "stable_diffusion"}}

    with patch("memory.project_store.PROJECTS_ROOT", projects_root):
        res = generate_images(["test"], tmp_path, cfg)
        assert len(res) == 1


def test_generate_images_cpu_device_fallbacks(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    cfg = {"image_gen": {"backend": "stable_diffusion"}}

    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1
    pipe.to.assert_called_with("cpu")


def test_replicate_success(tmp_path, monkeypatch):
    import sys
    from unittest.mock import MagicMock

    mock_replicate = MagicMock()
    mock_replicate.run.return_value = ["http://fakeurl.com/img.png"]
    sys.modules["replicate"] = mock_replicate

    # Mock urllib.request.urlopen to return mock response with bytes
    mock_res = MagicMock()
    mock_res.__enter__.return_value = mock_res
    mock_res.read.return_value = b"fake image bytes"

    from video.image_gen.image_gen import _replicate

    with patch("urllib.request.urlopen", return_value=mock_res):
        res = _replicate(["test prompt"], tmp_path, {"replicate_model": "test-model"})
        assert len(res) == 1
        assert res[0].name == "scene_01.png"


def test_upscale_realesrgan(tmp_path):
    import sys
    from unittest.mock import MagicMock

    mock_realesrgan = MagicMock()
    mock_realesrgan_er = MagicMock()
    mock_realesrgan.RealESRGANer = mock_realesrgan_er

    mock_enhancer = MagicMock()
    # returns output image numpy array
    import numpy as np

    mock_enhancer.enhance.return_value = (np.zeros((576, 1024, 3), dtype=np.uint8), None)
    mock_realesrgan_er.return_value = mock_enhancer

    sys.modules["realesrgan"] = mock_realesrgan

    # Mock RRDBNet in basicsr.archs.rrdbnet_arch
    sys.modules["basicsr"] = MagicMock()
    sys.modules["basicsr.archs"] = MagicMock()
    sys.modules["basicsr.archs.rrdbnet_arch"] = MagicMock()

    from PIL import Image

    from video.image_gen.image_gen import _maybe_upscale

    img = Image.new("RGB", (256, 256))

    # Call _maybe_upscale with upscaler_cfg in config
    cfg = {
        "upscaler": {
            "model": "realesrgan",
            "model_path": "fake_path.pth",
            "scale": 4,
            "target_width": 1024,
            "target_height": 576,
        }
    }
    with patch("video.image_gen.image_gen.RRDBNet", create=True):
        res = _maybe_upscale(img, cfg)
        assert res.size == (1024, 576)


def test_image_gen_cuda_seed_resolution_and_lora_perturb(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    # Mock PROJECTS_ROOT to exist and contain visual lock seed
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj = projects_root / "proj"
    proj.mkdir()
    (proj / "project.json").write_text(
        json.dumps({"visual_locks": {"Sanjay": {"seed": 98765}}}), encoding="utf-8"
    )

    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    lora_file = tmp_path / "sanjay.safetensors"
    lora_file.touch()
    lora_paths = {"Sanjay": lora_file}

    with patch("memory.project_store.PROJECTS_ROOT", projects_root):
        # Call generate_images with char_presence where Sanjay is dominant (>= 0.3)
        char_presence = [{"Sanjay": 0.8}]
        res = generate_images(
            ["test"], tmp_path, cfg, lora_paths=lora_paths, char_presence=char_presence
        )
        assert len(res) == 1


def test_image_gen_vram_guard_and_clip_warning(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    # Mock VRAM guard check: free VRAM < 1.2 GB (e.g. 1.0 GB)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (1024**3, 6 * (1024**3)))

    cfg = {"image_gen": {"backend": "stable_diffusion", "steps": 20}}

    # Long prompt to trigger CLIP warning (>77 tokens)
    long_prompt = "a " * 80

    res = generate_images([long_prompt], tmp_path, cfg)
    assert len(res) == 1


def test_image_gen_vram_guard_exception(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    # Force VRAM guard check to raise exception
    def mock_mem_get_info():
        raise RuntimeError("GPU query fail")

    monkeypatch.setattr(torch.cuda, "mem_get_info", mock_mem_get_info)

    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1


def test_image_gen_set_adapters_exception(tmp_path, monkeypatch):
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe
    pipe.set_adapters.side_effect = Exception("set adapters fail")

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    lora_file = tmp_path / "sanjay.safetensors"
    lora_file.touch()
    lora_paths = {"Sanjay": lora_file}

    char_presence = [{"Sanjay": 0.8}]
    res = generate_images(
        ["test"], tmp_path, cfg, lora_paths=lora_paths, char_presence=char_presence
    )
    assert len(res) == 1
    pipe.set_adapters.assert_called()


def test_image_gen_upscaler_missing_path(tmp_path):
    from PIL import Image

    from video.image_gen.image_gen import _maybe_upscale

    img = Image.new("RGB", (100, 100))
    cfg = {
        "upscaler": {
            "model": "realesrgan",
            "model_path": "",  # Empty path
        }
    }
    # Should fall back to Lanczos resize
    res = _maybe_upscale(img, cfg)
    assert res.size == (1920, 1080)


def test_image_gen_upscaler_enhance_failure(tmp_path):
    import sys

    from PIL import Image

    from video.image_gen.image_gen import _maybe_upscale

    # Force RealESRGANer constructor to fail
    mock_realesrgan = MagicMock()
    mock_realesrgan.RealESRGANer.side_effect = RuntimeError("init fail")
    sys.modules["realesrgan"] = mock_realesrgan

    img = Image.new("RGB", (100, 100))
    cfg = {
        "upscaler": {
            "model": "realesrgan",
            "model_path": "fake.pth",
        }
    }
    # Should log warning and fall back to Lanczos resize
    with patch("video.image_gen.image_gen.RRDBNet", create=True):
        res = _maybe_upscale(img, cfg)
        assert res.size == (1920, 1080)


def test_unload_sd_pipeline_exceptions(monkeypatch):
    """Test exceptions and checks in unload_sd_pipeline."""
    import video.image_gen.image_gen as mod

    # 1. Pipeline has no unload_lora_weights attribute
    pipe_no_unload = MagicMock(spec=[])
    mod._sd_pipe = pipe_no_unload
    mod.unload_sd_pipeline()
    assert mod._sd_pipe is None

    # 2. unload_lora_weights raises exception
    pipe_fail_unload = MagicMock()
    pipe_fail_unload.unload_lora_weights.side_effect = Exception("Unload weights fail")
    mod._sd_pipe = pipe_fail_unload
    mod.unload_sd_pipeline()
    assert mod._sd_pipe is None

    # 3. general exception during pipeline deletion/teardown
    mod._sd_pipe = MagicMock()
    with patch("gc.collect", side_effect=Exception("GC fail")):
        mod.unload_sd_pipeline()
        # Should catch exception and not crash


def test_generate_images_import_error(tmp_path, monkeypatch):
    """Test ImportError handling inside generate_images on different platforms."""
    # Hide diffusers/torch from imports to raise ImportError
    with patch.dict("sys.modules", {"diffusers": None}):
        # Win32 platform
        monkeypatch.setattr(sys, "platform", "win32")
        with pytest.raises(ImportError, match="pip install diffusers torch"):
            generate_images(["test"], tmp_path, {})

        # Linux platform
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(ImportError, match="pip install diffusers torch xformers"):
            generate_images(["test"], tmp_path, {})


def test_generate_images_enable_xformers_win32(tmp_path, monkeypatch):
    """Test xformers check on Windows platform disables xformers."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(sys, "platform", "win32")

    cfg = {"image_gen": {"backend": "stable_diffusion", "enable_xformers": True}}
    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1
    assert cfg["image_gen"]["enable_xformers"] is False


def test_generate_images_xformers_exception(tmp_path, monkeypatch):
    """Test exception when enabling xformers attention efficient styling."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe
    pipe.enable_xformers_memory_efficient_attention.side_effect = Exception("xformers error")

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    # Mock platform as linux so it checks triton
    monkeypatch.setattr(sys, "platform", "linux")

    cfg = {"image_gen": {"backend": "stable_diffusion", "enable_xformers": True}}
    with patch.dict("sys.modules", {"triton": MagicMock()}):
        res = generate_images(["test"], tmp_path, cfg)
        assert len(res) == 1
        assert cfg["image_gen"]["enable_xformers"] is False


def test_generate_images_group_offload_success(tmp_path, monkeypatch):
    """Test successful group offload path in generate_images."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    cfg = {"image_gen": {"backend": "stable_diffusion", "group_offload": True}}
    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1
    pipe.enable_group_offload.assert_called_once()


def test_generate_images_torch_compile_linux(tmp_path, monkeypatch):
    """Test torch.compile execution path on Linux."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(sys, "platform", "linux")

    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    with patch("torch.compile", return_value=pipe.unet) as mock_compile:
        res = generate_images(["test"], tmp_path, cfg)
        assert len(res) == 1
        mock_compile.assert_called_once()


def test_generate_images_lora_stat_exception(tmp_path, monkeypatch):
    """Test stat/mtime exception handling during LoRA fingerprinting."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    lora_file = tmp_path / "sanjay.safetensors"
    lora_file.touch()
    lora_paths = {"Sanjay": lora_file}

    # Raise exception during st_mtime access on the LoRA path (safetensors)
    original_stat = Path.stat

    def mock_stat(self, *args, **kwargs):
        res = original_stat(self, *args, **kwargs)
        if ".safetensors" in str(self):

            class MockStatResult:
                def __init__(self, orig):
                    self._orig = orig

                @property
                def st_mtime(self):
                    raise OSError("Access denied to st_mtime")

                def __getattr__(self, name):
                    return getattr(self._orig, name)

            return MockStatResult(res)
        return res

    with patch("pathlib.Path.stat", mock_stat):
        cfg = {"image_gen": {"backend": "stable_diffusion"}}
        res = generate_images(["test"], tmp_path, cfg, lora_paths=lora_paths)
        assert len(res) == 1


def test_generate_images_seed_early_exception(tmp_path, monkeypatch):
    """Test exception handling during early seed resolution."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj = projects_root / "proj"
    proj.mkdir()
    (proj / "project.json").write_text(
        json.dumps({"visual_locks": {"Sanjay": {"seed": "bad-seed"}}}), encoding="utf-8"
    )

    char_presence = [{"Sanjay": 0.8}]
    lora_file = tmp_path / "sanjay.safetensors"
    lora_file.touch()
    lora_paths = {"Sanjay": lora_file}

    with patch("memory.project_store.PROJECTS_ROOT", projects_root):
        cfg = {"image_gen": {"backend": "stable_diffusion"}}
        res = generate_images(
            ["test"], tmp_path, cfg, lora_paths=lora_paths, char_presence=char_presence
        )
        assert len(res) == 1


def test_generate_images_set_adapters_disable_exception(tmp_path, monkeypatch):
    """Test exception handling when disabling adapters on environmental frame."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe
    pipe.set_adapters.side_effect = Exception("disable fail")

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    # Force loaded_adapters to be truthy to hit the set_adapters block
    lora_file = tmp_path / "sanjay.safetensors"
    lora_file.touch()
    lora_paths = {"Sanjay": lora_file}

    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    res = generate_images(["test"], tmp_path, cfg, lora_paths=lora_paths)
    assert len(res) == 1


def test_generate_images_seed_generator_exception(tmp_path, monkeypatch):
    """Test exception handling when generator seed setup fails."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    with patch("torch.Generator", side_effect=Exception("Generator fail")):
        cfg = {"image_gen": {"backend": "stable_diffusion"}}
        res = generate_images(["test"], tmp_path, cfg)
        assert len(res) == 1


def test_generate_images_cpu_fallback_offloads(tmp_path, monkeypatch):
    """Test CPU fallback behavior when offloads are enabled."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    # Force Tier 1 and Tier 2 to raise OOM, then Tier 3 CPU succeeds on the 3rd call
    pipe.side_effect = [
        torch.cuda.OutOfMemoryError("CUDA OOM"),  # Tier 1
        torch.cuda.OutOfMemoryError("CUDA OOM"),  # Tier 2
        pipe,  # Tier 3 (returns pipeline mock)
    ]

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    # Call with model_cpu_offload=True (skips explicit .to("cpu"))
    cfg = {"image_gen": {"backend": "stable_diffusion", "model_cpu_offload": True}}
    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1
    # Check that .to("cpu") was NOT explicitly called
    pipe.to.assert_not_called()


def test_generate_images_generic_runtime_error(tmp_path, monkeypatch):
    """Test generic RuntimeError propagation inside generate_images."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe
    pipe.side_effect = RuntimeError("Other GPU error")

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )

    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    with pytest.raises(RuntimeError, match="Other GPU error"):
        generate_images(["test"], tmp_path, cfg)


def test_generate_images_cuda_cleanup_exception(tmp_path, monkeypatch):
    """Test CUDA cache cleanup exception handling."""
    pipe = MagicMock()
    pipe.scheduler = MagicMock()
    pipe.scheduler.config = {}
    pipe.unet = MagicMock()
    pipe.vae = MagicMock()
    pipe.to.return_value = pipe

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline, "from_pretrained", MagicMock(return_value=pipe)
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        torch.cuda, "empty_cache", lambda: exec('raise Exception("CUDA empty cache failed")')
    )

    cfg = {"image_gen": {"backend": "stable_diffusion"}}
    res = generate_images(["test"], tmp_path, cfg)
    assert len(res) == 1
