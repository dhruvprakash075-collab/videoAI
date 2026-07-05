"""Execution tests for all 7 Video.AI ComfyUI V3 nodes.

Sets up minimal comfy_api.v0_0_2 stubs so nodes.py can be imported.
Heavy deps (torch, comfy, PIL, folder_paths) are mocked per test.
"""
from __future__ import annotations

import json
import math
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── comfy_api.v0_0_2 stubs (injected before any node import) ──────────

class _ComfyExtension:
    async def get_node_list(self):
        return []

_SCHEMA_REQUIRED = frozenset({"node_id", "display_name", "category", "inputs", "outputs"})

class _Schema:
    def __init__(self, **kwargs):
        missing = _SCHEMA_REQUIRED - kwargs.keys()
        if missing:
            raise TypeError(f"Schema missing required fields: {missing}")
        for k, v in kwargs.items():
            setattr(self, k, v)

class _NodeOutput:
    def __init__(self, *args):
        self._values = args
        self.args = args
    def __iter__(self):
        return iter(self._values)
    def __getitem__(self, idx):
        return self._values[idx]
    def __len__(self):
        return len(self._values)

class _IOFactory:
    @classmethod
    def Input(cls, name=None, **kwargs):  # noqa: N802
        return (name, kwargs) if name else kwargs
    @classmethod
    def Output(cls, **kwargs):  # noqa: N802
        return kwargs

class _Custom:
    def __init__(self, type_name):
        self._type = type_name
    def Input(self, name=None, **kwargs):  # noqa: N802
        return {"name": name, "type": self._type, **kwargs} if name else {"type": self._type, **kwargs}
    def Output(self, **kwargs):  # noqa: N802
        return {"type": self._type, **kwargs}

class _Hidden:
    prompt = "STUB_PROMPT"
    extra_pnginfo = "STUB_EXTRA"

class _io:  # noqa: N801
    ComfyNode = object
    Schema = _Schema
    NodeOutput = _NodeOutput
    String = _IOFactory
    Int = _IOFactory
    Float = _IOFactory
    Boolean = _IOFactory
    Combo = _IOFactory
    Custom = _Custom
    Hidden = _Hidden()
    Model = _IOFactory
    Clip = _IOFactory
    Vae = _IOFactory
    Conditioning = _IOFactory
    Latent = _IOFactory
    Image = _IOFactory
    Mask = _IOFactory

_comfy_api_mod = types.ModuleType("comfy_api")
_comfy_api_v0 = types.ModuleType("comfy_api.v0_0_2")
_comfy_api_v0.ComfyExtension = _ComfyExtension
_comfy_api_v0.io = _io
_comfy_api_mod.v0_0_2 = _comfy_api_v0
sys.modules["comfy_api"] = _comfy_api_mod
sys.modules["comfy_api.v0_0_2"] = _comfy_api_v0

# Now safe to import node classes

def _with_mock_comfy_modules(**mocks):
    """Context manager that patches sys.modules with mock comfy modules."""
    class _Ctx:
        def __enter__(self):
            self._patches = {}
            for mod_name, mock_val in mocks.items():
                if mod_name not in sys.modules:
                    if mod_name.startswith("comfy."):
                        parts = mod_name.split(".")
                        parent_name = parts[0]
                        if parent_name not in sys.modules:
                            sys.modules[parent_name] = types.ModuleType(parent_name)
                            self._patches[parent_name] = None
                        setattr(sys.modules[parent_name], parts[-1], mock_val)
                        sys.modules[mod_name] = mock_val
                    else:
                        sys.modules[mod_name] = mock_val
                    self._patches[mod_name] = None
                else:
                    self._patches[mod_name] = sys.modules[mod_name]
                    sys.modules[mod_name] = mock_val
            return self
        def __exit__(self, *exc):
            for mod_name, orig in self._patches.items():
                if orig is None:
                    sys.modules.pop(mod_name, None)
                else:
                    sys.modules[mod_name] = orig
            return False
    return _Ctx()

from video_ai_nodes import nodes as video_nodes
from video_ai_nodes.helpers import (
    bootstrap_repo_import,
    char_key_from,
    load_yaml,
    pick_image_gen,
    read_image_gen_values,
    resolve_config_path,
    resolve_repo_root,
    sha256_file,
)
from video_ai_nodes.nodes import (
    _KSAMPLER_SEED_STATE,
    CATEGORY,
    VideoAI_CharacterPortraitLoader,
    VideoAI_ConfigCheckpointLoader,
    VideoAI_ConfigKSampler,
    VideoAI_FreeMemoryBarrier,
    VideoAI_ProjectConfigLoader,
    VideoAI_SmartFaceIDLoraRouter,
    VideoAI_VideoFrameSaver,
    VideoAIExtension,
    comfy_entrypoint,
)

# ═══════════════════════════════════════════════════════════════════════
# 1. Seed control
# ═══════════════════════════════════════════════════════════════════════

class TestSeedControl:
    def test_fixed_returns_seed_unchanged(self):
        assert VideoAI_ConfigKSampler._apply_seed_control(42, "fixed") == 42
        assert VideoAI_ConfigKSampler._apply_seed_control(0, "fixed") == 0
        assert VideoAI_ConfigKSampler._apply_seed_control(0xffffffffffffffff, "fixed") == 0xffffffffffffffff

    def test_randomize_returns_in_range(self):
        _KSAMPLER_SEED_STATE.clear()
        for _ in range(50):
            val = VideoAI_ConfigKSampler._apply_seed_control(0, "randomize")
            assert 0 <= val <= 0xffffffffffffffff

    def test_increment_increases_sequentially(self):
        _KSAMPLER_SEED_STATE.clear()
        assert VideoAI_ConfigKSampler._apply_seed_control(100, "increment") == 101
        assert VideoAI_ConfigKSampler._apply_seed_control(100, "increment") == 102
        assert VideoAI_ConfigKSampler._apply_seed_control(100, "increment") == 103

    def test_decrement_decreases_sequentially(self):
        _KSAMPLER_SEED_STATE.clear()
        assert VideoAI_ConfigKSampler._apply_seed_control(50, "decrement") == 49
        assert VideoAI_ConfigKSampler._apply_seed_control(50, "decrement") == 48

    def test_increment_decrement_independent_offset(self):
        _KSAMPLER_SEED_STATE.clear()
        VideoAI_ConfigKSampler._apply_seed_control(0, "increment")
        VideoAI_ConfigKSampler._apply_seed_control(0, "increment")
        # offset=2 after two increments, then decrement → offset=1, seed=100+1=101
        assert VideoAI_ConfigKSampler._apply_seed_control(100, "decrement") == 101

    def test_seed_wraps_at_max(self):
        _KSAMPLER_SEED_STATE.clear()
        val = VideoAI_ConfigKSampler._apply_seed_control(0xffffffffffffffff, "increment")
        assert val == 0

    def test_reset_state_between_tests(self):
        _KSAMPLER_SEED_STATE.clear()
        assert _KSAMPLER_SEED_STATE == {}


# ═══════════════════════════════════════════════════════════════════════
# 2. ConfigCheckpointLoader
# ═══════════════════════════════════════════════════════════════════════

class TestConfigCheckpointLoader:
    def test_checkpoint_name_override(self, tmp_path):
        name = VideoAI_ConfigCheckpointLoader._checkpoint_name(
            str(tmp_path / "nonexistent.yaml"), "", "override_ckpt.safetensors"
        )
        assert name == "override_ckpt.safetensors"

    def test_checkpoint_name_from_config(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  comfyui:\n    checkpoint: my_model.safetensors\n", encoding="utf-8")
        name = VideoAI_ConfigCheckpointLoader._checkpoint_name(str(cfg), str(tmp_path), "")
        assert name == "my_model.safetensors"

    def test_checkpoint_name_fallback_to_image_gen(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  checkpoint: fallback_ckpt.safetensors\n", encoding="utf-8")
        name = VideoAI_ConfigCheckpointLoader._checkpoint_name(str(cfg), str(tmp_path), "")
        assert name == "fallback_ckpt.safetensors"

    def test_checkpoint_name_returns_empty_when_missing(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  comfyui:\n    width: 512\n", encoding="utf-8")
        name = VideoAI_ConfigCheckpointLoader._checkpoint_name(str(cfg), str(tmp_path), "")
        assert name == ""

    def test_execute_raises_on_missing_checkpoint(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  comfyui:\n    steps: 20\n", encoding="utf-8")
        with pytest.raises(ValueError, match="No checkpoint configured"):
            VideoAI_ConfigCheckpointLoader.execute(str(cfg), str(tmp_path))

    def test_execute_raises_on_file_not_found(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  comfyui:\n    checkpoint: ghost.safetensors\n", encoding="utf-8")
        mock_fp = MagicMock()
        mock_fp.get_full_path.return_value = None
        mock_sd = MagicMock()
        with _with_mock_comfy_modules(folder_paths=mock_fp, **{"comfy.sd": mock_sd}):
            with pytest.raises(FileNotFoundError, match=r"ghost\.safetensors"):
                VideoAI_ConfigCheckpointLoader.execute(str(cfg), str(tmp_path))

    def test_fingerprint_inputs_unknown_when_no_checkpoint(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  steps: 20\n", encoding="utf-8")
        fp = VideoAI_ConfigCheckpointLoader.fingerprint_inputs(str(cfg), str(tmp_path))
        assert fp == "unknown"

    def test_fingerprint_inputs_ckpt_only_when_folder_paths_unavailable(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  comfyui:\n    checkpoint: test.safetensors\n", encoding="utf-8")
        fp = VideoAI_ConfigCheckpointLoader.fingerprint_inputs(str(cfg), str(tmp_path))
        assert fp == "test.safetensors"

    def test_fingerprint_inputs_includes_stat(self, tmp_path):
        ckpt = tmp_path / "model.safetensors"
        ckpt.write_text("dummy model data", encoding="utf-8")
        cfg = tmp_path / "config.yaml"
        cfg.write_text(f"image_gen:\n  comfyui:\n    checkpoint: {ckpt.name}\n", encoding="utf-8")
        mock_fp = MagicMock()
        mock_fp.get_full_path.return_value = str(ckpt)
        with _with_mock_comfy_modules(folder_paths=mock_fp):
            fp = VideoAI_ConfigCheckpointLoader.fingerprint_inputs(str(cfg), str(tmp_path))
            st = ckpt.stat()
            expected = f"{ckpt.name}:{st.st_mtime_ns}:{st.st_size}"
            assert fp == expected

    def test_execute_loads_checkpoint(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "image_gen:\n  comfyui:\n    checkpoint: dreamshaper.safetensors\n", encoding="utf-8"
        )
        fake_model = MagicMock()
        fake_clip = MagicMock()
        fake_vae = MagicMock()
        mock_sd = MagicMock()
        mock_sd.load_checkpoint_guess_config.return_value = (fake_model, fake_clip, fake_vae)
        mock_fp = MagicMock()
        mock_fp.get_full_path.return_value = "/models/dreamshaper.safetensors"
        mock_fp.get_folder_paths.return_value = ["/models/embeddings"]
        with _with_mock_comfy_modules(folder_paths=mock_fp, **{"comfy.sd": mock_sd}):
            out = VideoAI_ConfigCheckpointLoader.execute(str(cfg), str(tmp_path))
            assert out[0] is fake_model
            assert out[1] is fake_clip
            assert out[2] is fake_vae
            assert out[3] == "dreamshaper.safetensors"


# ═══════════════════════════════════════════════════════════════════════
# 3. ProjectConfigLoader
# ═══════════════════════════════════════════════════════════════════════

class TestProjectConfigLoader:
    def test_execute_returns_config_values(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "image_gen:\n  comfyui:\n    width: 800\n    height: 600\n    steps: 25\n"
            "    cfg: 5.0\n    sampler_name: dpmpp_2m\n    scheduler: karras\n"
            "    checkpoint: ckpt.safetensors\n    negative_prompt: bad quality\n"
            "    unload_after_batch: false\n",
            encoding="utf-8",
        )
        out = VideoAI_ProjectConfigLoader.execute(str(cfg), str(tmp_path))
        assert out[0] == 800
        assert out[1] == 600
        assert out[2] == 25
        assert out[3] == 5.0
        assert out[4] == "dpmpp_2m"
        assert out[5] == "karras"
        assert out[6] == "ckpt.safetensors"
        assert out[7] == "bad quality"
        assert out[8] is False

    def test_execute_falls_back_to_image_gen_top_level(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "image_gen:\n  guidance_scale: 4.0\n  comfyui:\n    width: 1024\n    height: 768\n",
            encoding="utf-8",
        )
        out = VideoAI_ProjectConfigLoader.execute(str(cfg), str(tmp_path))
        assert out[0] == 1024
        assert out[1] == 768
        assert out[2] == 20  # default
        assert out[3] == 4.0  # falls back to image_gen.guidance_scale

    def test_execute_empty_config_uses_defaults(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("other_key: true\n", encoding="utf-8")
        out = VideoAI_ProjectConfigLoader.execute(str(cfg), str(tmp_path))
        assert out[0] == 1024
        assert out[1] == 1024
        assert out[2] == 20
        assert out[3] == 7.0
        assert out[4] == "euler"
        assert out[5] == "normal"

    def test_execute_missing_config_file_uses_defaults(self, tmp_path):
        out = VideoAI_ProjectConfigLoader.execute(str(tmp_path / "missing.yaml"), str(tmp_path))
        assert isinstance(out[0], int)

    def test_execute_passes_barrier_ignored(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  comfyui:\n    steps: 15\n", encoding="utf-8")
        out = VideoAI_ProjectConfigLoader.execute(str(cfg), str(tmp_path), barrier="anything")
        assert out[2] == 15


# ═══════════════════════════════════════════════════════════════════════
# 4. ConfigKSampler
# ═══════════════════════════════════════════════════════════════════════

class TestConfigKSampler:
    def _ksampler_mocks(self, samplers=None, schedulers=None):
        mock_samplers = MagicMock()
        mock_samplers.KSampler.SAMPLERS = samplers or ["euler"]
        mock_samplers.KSampler.SCHEDULERS = schedulers or ["normal"]
        mock_ksampler = MagicMock(return_value=(MagicMock(),))
        return {"comfy.samplers": mock_samplers, "nodes": MagicMock(common_ksampler=mock_ksampler)}

    def test_execute_uses_overrides(self, tmp_path):
        _KSAMPLER_SEED_STATE.clear()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "image_gen:\n  comfyui:\n    steps: 30\n    cfg: 7.0\n    sampler_name: euler\n"
            "    scheduler: normal\n",
            encoding="utf-8",
        )
        with _with_mock_comfy_modules(**self._ksampler_mocks()):
            out = VideoAI_ConfigKSampler.execute(
                MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                str(cfg), str(tmp_path), seed=42, seed_control="fixed",
                steps_override=15, cfg_override=3.5,
            )
            # out: LATENT, SAMPLER_NAME, SCHEDULER, STEPS, CFG, SEED
            assert out[3] == 15  # STEPS
            assert out[4] == 3.5  # CFG
            assert out[5] == 42  # SEED

    def test_execute_raises_on_bad_sampler(self, tmp_path):
        _KSAMPLER_SEED_STATE.clear()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  comfyui:\n    sampler_name: imaginary\n    scheduler: normal\n", encoding="utf-8")
        with _with_mock_comfy_modules(**self._ksampler_mocks(samplers=["euler"])):
            with pytest.raises(ValueError, match=r"sampler_name.*imaginary"):
                VideoAI_ConfigKSampler.execute(
                    MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                    str(cfg), str(tmp_path), seed=0,
                )

    def test_execute_raises_on_bad_scheduler(self, tmp_path):
        _KSAMPLER_SEED_STATE.clear()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  comfyui:\n    sampler_name: euler\n    scheduler: magic\n", encoding="utf-8")
        with _with_mock_comfy_modules(**self._ksampler_mocks(schedulers=["normal"])):
            with pytest.raises(ValueError, match=r"scheduler.*magic"):
                VideoAI_ConfigKSampler.execute(
                    MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                    str(cfg), str(tmp_path), seed=0,
                )

    def test_execute_seed_control_increment(self, tmp_path):
        _KSAMPLER_SEED_STATE.clear()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("image_gen:\n  comfyui:\n    sampler_name: euler\n    scheduler: normal\n", encoding="utf-8")
        with _with_mock_comfy_modules(**self._ksampler_mocks()):
            out1 = VideoAI_ConfigKSampler.execute(
                MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                str(cfg), str(tmp_path), seed=100, seed_control="increment",
            )
            out2 = VideoAI_ConfigKSampler.execute(
                MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                str(cfg), str(tmp_path), seed=100, seed_control="increment",
            )
            # out: LATENT, SAMPLER_NAME, SCHEDULER, STEPS, CFG, SEED
            assert out1[5] == 101
            assert out2[5] == 102

    def test_fingerprint_inputs_non_fixed_returns_nan(self):
        fp = VideoAI_ConfigKSampler.fingerprint_inputs(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            seed=0, seed_control="increment",
        )
        assert isinstance(fp, float) and math.isnan(fp)

    def test_fingerprint_inputs_fixed_returns_string(self):
        fp = VideoAI_ConfigKSampler.fingerprint_inputs(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            seed=0, seed_control="fixed",
        )
        assert isinstance(fp, str)
        assert "0" in fp


# ═══════════════════════════════════════════════════════════════════════
# 5. CharacterPortraitLoader
# ═══════════════════════════════════════════════════════════════════════

class TestCharacterPortraitLoader:
    def test_resolve_master_portrait(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "studio_projects" / "testproj"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            '{"characters": {"hero": {"name": "Hero", "master_portrait_path": "", "master_portrait_hash": ""}}}',
            encoding="utf-8",
        )
        portrait = tmp_path / "hero_portrait.png"
        portrait.write_text("fake image data", encoding="utf-8")

        monkeypatch.setattr("sys.path", [str(tmp_path), *sys.path])
        from memory import project_store

        monkeypatch.setattr(project_store, "PROJECTS_ROOT", tmp_path / "studio_projects")
        store = project_store.ProjectStore("testproj", root=tmp_path / "studio_projects")
        store.set_master_portrait("hero", str(portrait), "abc123")

        _store2, key, _char, path = VideoAI_CharacterPortraitLoader._resolve(
            "testproj", "Hero", str(tmp_path)
        )
        assert key == "hero"
        assert path is not None
        assert path.exists()

    def test_resolve_face_reference_fallback(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "studio_projects" / "testproj"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            '{"characters": {"hero": {"name": "Hero"}}}', encoding="utf-8"
        )
        monkeypatch.setattr("sys.path", [str(tmp_path), *sys.path])
        from memory import project_store

        monkeypatch.setattr(project_store, "PROJECTS_ROOT", tmp_path / "studio_projects")
        store = project_store.ProjectStore("testproj", root=tmp_path / "studio_projects")
        face = tmp_path / "face_ref.png"
        face.write_text("face", encoding="utf-8")
        store.set_character_assets("hero", face_reference_path=str(face))

        _store2, _key, _char, path = VideoAI_CharacterPortraitLoader._resolve(
            "testproj", "Hero", str(tmp_path)
        )
        assert path is not None and path.exists()

    def test_resolve_no_portrait_returns_none(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "studio_projects" / "testproj"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            '{"characters": {"hero": {"name": "Hero"}}}', encoding="utf-8"
        )
        monkeypatch.setattr("sys.path", [str(tmp_path), *sys.path])
        from memory import project_store

        monkeypatch.setattr(project_store, "PROJECTS_ROOT", tmp_path / "studio_projects")
        _store2, _key, _char, path = VideoAI_CharacterPortraitLoader._resolve(
            "testproj", "Hero", str(tmp_path)
        )
        assert path is None

    def test_execute_raises_when_no_portrait(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "studio_projects" / "testproj"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            '{"characters": {"hero": {"name": "Hero"}}}', encoding="utf-8"
        )
        monkeypatch.setattr("sys.path", [str(tmp_path), *sys.path])
        with pytest.raises(FileNotFoundError, match="No portrait"):
            VideoAI_CharacterPortraitLoader.execute("testproj", "Hero", str(tmp_path))

    def test_fingerprint_inputs_returns_missing(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "studio_projects" / "testproj"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            '{"characters": {"hero": {"name": "Hero"}}}', encoding="utf-8"
        )
        monkeypatch.setattr("sys.path", [str(tmp_path), *sys.path])
        fp = VideoAI_CharacterPortraitLoader.fingerprint_inputs("testproj", "Hero", str(tmp_path))
        assert fp.startswith("missing:")


# ═══════════════════════════════════════════════════════════════════════
# 6. SmartFaceIDLoraRouter
# ═══════════════════════════════════════════════════════════════════════

class TestSmartFaceIDLoraRouter:
    @pytest.mark.parametrize("name,expected", [
        ("DreamShaper_8.safetensors", "sd15"),
        ("sd15_v1.safetensors", "sd15"),
        ("realisticVision.safetensors", "sd15"),
        ("sdxl_base.safetensors", "sdxl"),
        ("ponyDiffusion.safetensors", "sdxl"),
        ("illustrious.safetensors", "sdxl"),
        ("flux-dev.safetensors", "flux"),
        ("qwen_image_edit.safetensors", "qwen"),
        ("random_model.safetensors", "unknown"),
        ("", "unknown"),
    ])
    def test_detect_family(self, name, expected):
        assert VideoAI_SmartFaceIDLoraRouter._detect_family(name) == expected

    def test_execute_non_sd15_skips(self):
        out = VideoAI_SmartFaceIDLoraRouter.execute(
            "model", "clip", checkpoint_name="flux-dev.safetensors",
            model_family="auto",
        )
        assert out[0] == "model"
        assert out[1] == "clip"
        assert out[2] is False

    def test_execute_sd15_no_lora_skips(self):
        out = VideoAI_SmartFaceIDLoraRouter.execute(
            "model", "clip", checkpoint_name="DreamShaper_8.safetensors",
            lora_name="None", model_family="auto",
        )
        assert out[2] is False

    def test_execute_sd15_lora_not_found_skips(self, tmp_path):
        mock_fp = MagicMock()
        mock_fp.get_full_path.return_value = None
        with _with_mock_comfy_modules(folder_paths=mock_fp, **{"comfy.sd": MagicMock(), "comfy.utils": MagicMock()}):
            out = VideoAI_SmartFaceIDLoraRouter.execute(
                "model", "clip", checkpoint_name="DreamShaper_8.safetensors",
                lora_name="faceid.safetensors", model_family="auto",
            )
            assert out[2] is False

    def test_execute_sd15_lora_applies(self, tmp_path):
        fake_model_out = MagicMock()
        fake_clip_out = MagicMock()
        mock_fp = MagicMock()
        mock_fp.get_full_path.return_value = str(tmp_path / "faceid.safetensors")
        mock_sd = MagicMock()
        mock_sd.load_lora_for_models.return_value = (fake_model_out, fake_clip_out)
        mock_utils = MagicMock()
        mock_utils.load_torch_file.return_value = {"lora_weights": "data"}
        with _with_mock_comfy_modules(
            folder_paths=mock_fp, **{"comfy.sd": mock_sd, "comfy.utils": mock_utils}
        ):
            out = VideoAI_SmartFaceIDLoraRouter.execute(
                "model", "clip", checkpoint_name="DreamShaper_8.safetensors",
                lora_name="faceid.safetensors", strength_model=1.0, strength_clip=0.5,
                model_family="auto",
            )
            assert out[0] is fake_model_out
            assert out[1] is fake_clip_out
            assert out[2] is True
            mock_sd.load_lora_for_models.assert_called_once_with(
                "model", "clip", {"lora_weights": "data"}, 1.0, 0.5
            )

    def test_execute_explicit_family_override(self):
        out = VideoAI_SmartFaceIDLoraRouter.execute(
            "model", "clip", model_family="flux",
        )
        assert out[2] is False


# ═══════════════════════════════════════════════════════════════════════
# 7. FreeMemoryBarrier
# ═══════════════════════════════════════════════════════════════════════

class TestFreeMemoryBarrier:
    def test_disabled_returns_label(self):
        out = VideoAI_FreeMemoryBarrier.execute(enabled=False, label="test_barrier")
        assert out[0] == "test_barrier"

    def test_enabled_without_torch(self):
        """exercises the gc.collect() path when torch import fails"""
        out = VideoAI_FreeMemoryBarrier.execute(enabled=True, label="cleanup_ok")
        assert out[0] == "cleanup_ok"

    def test_enabled_with_torch_mock(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            _out = VideoAI_FreeMemoryBarrier.execute(enabled=True, free_cuda_cache=True)
            assert mock_torch.cuda.empty_cache.called
            assert mock_torch.cuda.ipc_collect.called

    def test_enabled_comfy_unavailable_falls_back(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": mock_torch}):
            out = VideoAI_FreeMemoryBarrier.execute(enabled=True)
            assert out[0] is not None


# ═══════════════════════════════════════════════════════════════════════
# 8. VideoFrameSaver
# ═══════════════════════════════════════════════════════════════════════

class TestVideoFrameSaver:
    @pytest.fixture(autouse=True)
    def _mock_pil_images(self, monkeypatch):
        """Replace tensor_to_images with a fake that returns PIL Images."""
        import numpy as np
        from PIL import Image
        def _fake_tensor_to_images(image):
            batch = image.shape[0] if hasattr(image, 'shape') else 1
            return [Image.fromarray(np.zeros((64, 64, 3), dtype=np.uint8)) for _ in range(batch)]
        monkeypatch.setattr(video_nodes, "tensor_to_images", _fake_tensor_to_images)

    def test_save_single_frame(self, tmp_path):
        import numpy as np
        frames = np.zeros((1, 64, 64, 3), dtype=np.float32)
        VideoAI_VideoFrameSaver.hidden = MagicMock(prompt=None, extra_pginfo=None)
        out = VideoAI_VideoFrameSaver.execute(
            frames, str(tmp_path), scene_index=1, filename_prefix="scene",
        )
        saved = json.loads(out[0])
        assert len(saved) == 1
        assert out[1] == 1
        assert Path(saved[0]).exists()
        assert "scene_01.png" in saved[0]

    def test_save_multi_frame(self, tmp_path):
        import numpy as np
        frames = np.zeros((3, 64, 64, 3), dtype=np.float32)
        VideoAI_VideoFrameSaver.hidden = MagicMock(prompt=None, extra_pginfo=None)
        out = VideoAI_VideoFrameSaver.execute(
            frames, str(tmp_path), scene_index=2, filename_prefix="shot",
        )
        saved = json.loads(out[0])
        assert len(saved) == 3
        assert out[1] == 3
        assert "shot_02_0001.png" in saved[0]
        assert "shot_02_0002.png" in saved[1]
        assert "shot_02_0003.png" in saved[2]

    def test_overwrite_false_creates_dup(self, tmp_path):
        import numpy as np
        existing = tmp_path / "scene_01.png"
        existing.write_text("existing", encoding="utf-8")
        frames = np.zeros((1, 64, 64, 3), dtype=np.float32)
        VideoAI_VideoFrameSaver.hidden = MagicMock(prompt=None, extra_pginfo=None)
        out = VideoAI_VideoFrameSaver.execute(
            frames, str(tmp_path), scene_index=1, filename_prefix="scene",
            overwrite=False,
        )
        saved = json.loads(out[0])
        assert len(saved) == 1
        assert "dup" in saved[0]

    def test_metadata_embedded(self, tmp_path):
        import numpy as np
        frames = np.zeros((1, 64, 64, 3), dtype=np.float32)
        VideoAI_VideoFrameSaver.hidden = MagicMock(prompt="test_prompt", extra_pginfo={"key": "val"})
        out = VideoAI_VideoFrameSaver.execute(
            frames, str(tmp_path), scene_index=1, filename_prefix="meta",
            metadata_json='{"custom": "data"}',
        )
        saved = json.loads(out[0])
        assert Path(saved[0]).exists()
        assert Path(saved[0]).stat().st_size > 0


# ═══════════════════════════════════════════════════════════════════════
# 9. Extension registration
# ═══════════════════════════════════════════════════════════════════════

class TestExtensionRegistration:
    EXPECTED = {
        "VideoAI_ProjectConfigLoader",
        "VideoAI_ConfigCheckpointLoader",
        "VideoAI_ConfigKSampler",
        "VideoAI_CharacterPortraitLoader",
        "VideoAI_FreeMemoryBarrier",
        "VideoAI_SmartFaceIDLoraRouter",
        "VideoAI_VideoFrameSaver",
    }

    def test_entrypoint_returns_extension(self):
        import asyncio
        ext = asyncio.run(comfy_entrypoint())
        assert isinstance(ext, VideoAIExtension)

    def test_get_node_list_returns_all_seven(self):
        import asyncio
        ext = VideoAIExtension()
        nodes = asyncio.run(ext.get_node_list())
        assert {n.define_schema().node_id for n in nodes} == self.EXPECTED

    def test_all_node_schemas_are_valid(self):
        """Validate every node's define_schema() returns a well-formed schema.
        Catches API drift (required field renames, type changes) in comfy_api stubs."""
        import asyncio
        nodes = asyncio.run(VideoAIExtension().get_node_list())
        for node_cls in nodes:
            schema = node_cls.define_schema()
            assert schema.node_id  # non-empty
            assert schema.display_name
            assert schema.category == CATEGORY
            assert isinstance(schema.inputs, list)
            assert isinstance(schema.outputs, list)
            for inp in schema.inputs:
                # standard inputs (name, kwargs) tuple or Custom/barrier dict
                assert isinstance(inp, (tuple, dict))
                if isinstance(inp, tuple):
                    assert len(inp) == 2 and isinstance(inp[0], str)  # (name, kwargs)
            for out in schema.outputs:
                assert isinstance(out, dict)  # kwargs
            assert hasattr(node_cls, "execute")
            # ponytail: fingerprint_inputs is optional — only checkpoint/ksampler/portrait implement it


# ═══════════════════════════════════════════════════════════════════════
# 10. Helper function edge cases
# ═══════════════════════════════════════════════════════════════════════

class TestHelpers:
    def test_char_key_from(self):
        assert char_key_from("John Doe") == "john_doe"
        assert char_key_from("  Alice  ") == "alice"
        assert char_key_from("") == ""

    def test_resolve_repo_root_explicit(self):
        root = resolve_repo_root("C:/custom/path")
        assert str(root) == "C:\\custom\\path"

    def test_resolve_repo_root_env(self, monkeypatch):
        monkeypatch.setenv("VIDEO_AI_ROOT", "C:/from_env")
        root = resolve_repo_root()
        assert str(root) == "C:\\from_env"

    def test_resolve_config_path_absolute(self):
        result = resolve_config_path("C:/absolute/path.yaml", Path("/repo"))
        assert result == Path("C:/absolute/path.yaml")

    def test_resolve_config_path_relative(self):
        result = resolve_config_path("relative/path.yaml", Path("/repo"))
        assert result == Path("/repo/relative/path.yaml")

    def test_load_yaml_missing_file(self, tmp_path):
        assert load_yaml(tmp_path / "missing.yaml") == {}

    def test_pick_image_gen_prefers_comfyui(self):
        cfg = {
            "image_gen": {
                "guidance_scale": 3.5,
                "comfyui": {"cfg": 7.0},
            }
        }
        assert pick_image_gen(cfg, "cfg", 1.0) == 7.0
        assert pick_image_gen(cfg, "guidance_scale", 1.0) == 3.5
        assert pick_image_gen(cfg, "missing", "default") == "default"

    def test_read_image_gen_values_comfyui_first(self):
        cfg = {
            "image_gen": {
                "guidance_scale": 3.5,
                "comfyui": {
                    "width": 1344, "height": 768, "steps": 30, "cfg": 7.0,
                    "sampler_name": "euler", "scheduler": "normal",
                    "checkpoint": "ds.safetensors", "unload_after_batch": True,
                },
            }
        }
        vals = read_image_gen_values(cfg)
        assert vals[0] == 1344
        assert vals[3] == 7.0  # cfg from comfyui, NOT guidance_scale

    def test_read_image_gen_values_falls_back_to_guidance_scale(self):
        cfg = {"image_gen": {"guidance_scale": 4.5}}
        vals = read_image_gen_values(cfg)
        assert vals[3] == 4.5

    def test_bootstrap_repo_import_adds_to_sys_path(self, tmp_path, monkeypatch):
        root = tmp_path / "myrepo"
        root.mkdir()
        before = list(sys.path)
        try:
            out = bootstrap_repo_import(str(root))
            assert out == root
            assert str(root) in sys.path
        finally:
            sys.path[:] = before

    def test_bootstrap_repo_import_skips_if_missing(self):
        out = bootstrap_repo_import("C:/nonexistent_path_12345")
        assert out == Path("C:/nonexistent_path_12345")
        assert "C:/nonexistent_path_12345" not in sys.path

    def test_sha256_file(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"hello world")
        h = sha256_file(f)
        assert len(h) == 64
        assert isinstance(h, str)
