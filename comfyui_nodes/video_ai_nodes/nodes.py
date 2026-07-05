"""Video.AI ComfyUI V3 custom nodes."""

from __future__ import annotations

import gc
import json
import logging
import random
from pathlib import Path

from comfy_api.v0_0_2 import ComfyExtension, io

from .helpers import (
    bootstrap_repo_import,
    char_key_from,
    image_to_tensor,
    load_yaml,
    read_image_gen_values,
    resolve_config_path,
    resolve_repo_root,
    sha256_file,
    tensor_to_images,
)

log = logging.getLogger("video_ai_nodes")
CATEGORY = "Video.AI"
BARRIER = io.Custom("VIDEOAI_BARRIER")
_KSAMPLER_SEED_STATE: dict[str, int] = {}


class VideoAI_ProjectConfigLoader(io.ComfyNode):  # noqa: N801
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="VideoAI_ProjectConfigLoader",
            display_name="Video.AI Project Config Loader",
            category=CATEGORY,
            inputs=[
                io.String.Input("config_path", default="config/config.yaml"),
                io.String.Input("repo_root", default="", optional=True),
                BARRIER.Input("barrier", optional=True),
            ],
            outputs=[
                io.Int.Output(display_name="WIDTH"),
                io.Int.Output(display_name="HEIGHT"),
                io.Int.Output(display_name="STEPS"),
                io.Float.Output(display_name="CFG"),
                io.String.Output(display_name="SAMPLER_NAME"),
                io.String.Output(display_name="SCHEDULER"),
                io.String.Output(display_name="CHECKPOINT"),
                io.String.Output(display_name="NEGATIVE_PROMPT"),
                io.Boolean.Output(display_name="UNLOAD_AFTER_BATCH"),
            ],
        )

    @classmethod
    def execute(cls, config_path: str, repo_root: str = "", barrier=None) -> io.NodeOutput:  # noqa: ARG003
        root = resolve_repo_root(repo_root)
        cfg = load_yaml(resolve_config_path(config_path, root))
        return io.NodeOutput(*read_image_gen_values(cfg))


class VideoAI_ConfigCheckpointLoader(io.ComfyNode):  # noqa: N801
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="VideoAI_ConfigCheckpointLoader",
            display_name="Video.AI Config Checkpoint Loader",
            category=CATEGORY,
            inputs=[
                io.String.Input("config_path", default="config/config.yaml"),
                io.String.Input("repo_root", default="", optional=True),
                io.String.Input("checkpoint_override", default="", optional=True),
                BARRIER.Input("barrier", optional=True),
            ],
            outputs=[
                io.Model.Output(display_name="MODEL"),
                io.Clip.Output(display_name="CLIP"),
                io.Vae.Output(display_name="VAE"),
                io.String.Output(display_name="CHECKPOINT"),
            ],
        )

    @classmethod
    def _checkpoint_name(cls, config_path, repo_root, checkpoint_override):
        if checkpoint_override:
            return checkpoint_override
        root = resolve_repo_root(repo_root)
        cfg = load_yaml(resolve_config_path(config_path, root))
        image_gen = cfg.get("image_gen", {}) or {}
        comfy = image_gen.get("comfyui", {}) or {}
        return comfy.get("checkpoint") or image_gen.get("checkpoint") or ""

    @classmethod
    def execute(cls, config_path="config/config.yaml", repo_root="", checkpoint_override="", barrier=None) -> io.NodeOutput:  # noqa: ARG003
        ckpt_name = cls._checkpoint_name(config_path, repo_root, checkpoint_override)
        if not ckpt_name:
            raise ValueError("No checkpoint configured at image_gen.comfyui.checkpoint")
        import comfy.sd
        import folder_paths
        ckpt_path = folder_paths.get_full_path("checkpoints", ckpt_name)
        if not ckpt_path:
            raise FileNotFoundError(f"Checkpoint '{ckpt_name}' not found")
        model, clip, vae = comfy.sd.load_checkpoint_guess_config(
            ckpt_path,
            output_vae=True,
            output_clip=True,
            embedding_directory=folder_paths.get_folder_paths("embeddings"),
        )[:3]
        return io.NodeOutput(model, clip, vae, ckpt_name)

    @classmethod
    def fingerprint_inputs(cls, config_path="config/config.yaml", repo_root="", checkpoint_override="", barrier=None):  # noqa: ARG003
        ckpt = cls._checkpoint_name(config_path, repo_root, checkpoint_override)
        if not ckpt:
            return "unknown"
        try:
            import folder_paths
            resolved = folder_paths.get_full_path("checkpoints", ckpt)
            if resolved:
                st = Path(resolved).stat()
                return f"{ckpt}:{st.st_mtime_ns}:{st.st_size}"
        except Exception:
            pass
        return ckpt


class VideoAI_ConfigKSampler(io.ComfyNode):  # noqa: N801
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="VideoAI_ConfigKSampler",
            display_name="Video.AI Config KSampler",
            category=CATEGORY,
            inputs=[
                io.Model.Input("model"),
                io.Conditioning.Input("positive"),
                io.Conditioning.Input("negative"),
                io.Latent.Input("latent"),
                io.String.Input("config_path", default="config/config.yaml"),
                io.String.Input("repo_root", default="", optional=True),
                io.Int.Input("seed", default=0, min=0, max=0xffffffffffffffff),
                io.Combo.Input("seed_control", options=["fixed", "increment", "decrement", "randomize"], default="fixed"),
                io.Float.Input("denoise", default=1.0, min=0.0, max=1.0, step=0.01, optional=True),
                io.String.Input("sampler_override", default="", optional=True),
                io.String.Input("scheduler_override", default="", optional=True),
                io.Int.Input("steps_override", default=0, min=0, max=10000, optional=True),
                io.Float.Input("cfg_override", default=0.0, min=0.0, max=100.0, step=0.1, optional=True),
                BARRIER.Input("barrier", optional=True),
            ],
            outputs=[
                io.Latent.Output(display_name="LATENT"),
                io.String.Output(display_name="SAMPLER_NAME"),
                io.String.Output(display_name="SCHEDULER"),
                io.Int.Output(display_name="STEPS"),
                io.Float.Output(display_name="CFG"),
                io.Int.Output(display_name="SEED"),
            ],
        )

    @staticmethod
    def _apply_seed_control(seed: int, mode: str) -> int:
        max_seed = 0xffffffffffffffff
        if mode == "randomize":
            return random.randint(0, max_seed)
        if mode in {"increment", "decrement"}:
            offset = _KSAMPLER_SEED_STATE.get("offset", 0) + (1 if mode == "increment" else -1)
            _KSAMPLER_SEED_STATE["offset"] = offset
            return (int(seed) + offset) & max_seed
        return int(seed)

    @classmethod
    def execute(cls, model, positive, negative, latent, config_path="config/config.yaml", repo_root="", seed=0, seed_control="fixed", denoise=1.0, sampler_override="", scheduler_override="", steps_override=0, cfg_override=0.0, barrier=None) -> io.NodeOutput:  # noqa: ARG003
        import comfy.samplers
        from nodes import common_ksampler

        root = resolve_repo_root(repo_root)
        width, height, steps, cfg_scale, sampler_name, scheduler, _ckpt, _neg, _unload = read_image_gen_values(
            load_yaml(resolve_config_path(config_path, root))
        )
        del width, height
        steps = int(steps_override) or steps
        cfg_scale = float(cfg_override) or cfg_scale
        sampler_name = sampler_override or sampler_name
        scheduler = scheduler_override or scheduler
        effective_seed = cls._apply_seed_control(seed, seed_control)
        if sampler_name not in comfy.samplers.KSampler.SAMPLERS:
            raise ValueError(f"sampler_name '{sampler_name}' is not supported")
        if scheduler not in comfy.samplers.KSampler.SCHEDULERS:
            raise ValueError(f"scheduler '{scheduler}' is not supported")
        (out_latent,) = common_ksampler(model, effective_seed, steps, cfg_scale, sampler_name, scheduler, positive, negative, latent, denoise=denoise)
        return io.NodeOutput(out_latent, sampler_name, scheduler, steps, cfg_scale, effective_seed)

    @classmethod
    def fingerprint_inputs(cls, model=None, positive=None, negative=None, latent=None, config_path="config/config.yaml", repo_root="", seed=0, seed_control="fixed", denoise=1.0, sampler_override="", scheduler_override="", steps_override=0, cfg_override=0.0, barrier=None):  # noqa: ARG003
        if seed_control != "fixed":
            return float("nan")
        return f"{seed}:{config_path}:{repo_root}:{denoise}:{sampler_override}:{scheduler_override}:{steps_override}:{cfg_override}"


class VideoAI_CharacterPortraitLoader(io.ComfyNode):  # noqa: N801
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="VideoAI_CharacterPortraitLoader",
            display_name="Video.AI Character Portrait Loader",
            category=CATEGORY,
            inputs=[
                io.String.Input("project_name", default=""),
                io.String.Input("character_name", default=""),
                io.String.Input("repo_root", default="", optional=True),
            ],
            outputs=[
                io.Image.Output(display_name="IMAGE"),
                io.String.Output(display_name="PORTRAIT_HASH"),
                io.String.Output(display_name="PORTRAIT_PATH"),
                io.String.Output(display_name="CHARACTER_JSON"),
            ],
        )

    @classmethod
    def _resolve(cls, project_name: str, character_name: str, repo_root: str):
        root = bootstrap_repo_import(repo_root)
        from memory.project_store import ProjectStore

        store = ProjectStore(project_name, root=root / "studio_projects")
        key = char_key_from(character_name)
        character = store.get_character(character_name) or store.get_character(key) or {}
        path = store.get_master_portrait_path(key)
        if not path:
            assets = store.get_character_assets(key)
            path = assets.get("face_reference_path") or assets.get("full_body_reference_path") or assets.get("character_sheet_path") or ""
        resolved = Path(path)
        if path and not resolved.is_absolute():
            resolved = root / path
        return store, key, character, resolved if path else None

    @classmethod
    def execute(cls, project_name: str, character_name: str, repo_root: str = "") -> io.NodeOutput:
        from PIL import Image

        store, key, character, path = cls._resolve(project_name, character_name, repo_root)
        if path is None or not path.exists():
            raise FileNotFoundError(f"No portrait found for '{character_name}' in '{project_name}'")
        portrait_hash = store.get_master_portrait_hash(key) or sha256_file(path)
        return io.NodeOutput(image_to_tensor(Image.open(path)), portrait_hash, str(path), json.dumps(character, ensure_ascii=False))

    @classmethod
    def fingerprint_inputs(cls, project_name: str, character_name: str, repo_root: str = ""):
        try:
            _store, _key, _character, path = cls._resolve(project_name, character_name, repo_root)
            if path is not None and path.exists():
                st = path.stat()
                return f"{path}:{st.st_mtime_ns}:{st.st_size}"
        except Exception:
            pass
        return f"missing:{project_name}:{character_name}"


class VideoAI_FreeMemoryBarrier(io.ComfyNode):  # noqa: N801
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="VideoAI_FreeMemoryBarrier",
            display_name="Video.AI Free Memory Barrier",
            category=CATEGORY,
            inputs=[
                io.Boolean.Input("enabled", default=True),
                io.Boolean.Input("unload_models", default=True),
                io.Boolean.Input("free_cuda_cache", default=True),
                io.String.Input("label", default="video_ai_barrier", optional=True),
            ],
            outputs=[BARRIER.Output(display_name="BARRIER")],
            not_idempotent=True,
        )

    @classmethod
    def execute(cls, enabled=True, unload_models=True, free_cuda_cache=True, label="video_ai_barrier") -> io.NodeOutput:
        if enabled:
            import torch

            try:
                import comfy.model_management as mm

                if unload_models:
                    mm.unload_all_models()
                if free_cuda_cache:
                    mm.soft_empty_cache()
            except Exception as exc:
                log.warning("ComfyUI memory cleanup unavailable: %s", exc)
            gc.collect()
            if free_cuda_cache and torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        return io.NodeOutput(label)


class VideoAI_SmartFaceIDLoraRouter(io.ComfyNode):  # noqa: N801
    @staticmethod
    def _lora_options():
        try:
            import folder_paths

            return folder_paths.get_filename_list("loras") or ["None"]
        except Exception:
            return ["None"]
    # ponytail: _lora_options returns a static snapshot at schema-registration time.
    #   If a new LoRA is added while ComfyUI runs the user won't see it in the dropdown
    #   until restart. ComfyUI-wide limitation — all node packs behave this way.

    @staticmethod
    def _detect_family(checkpoint_name: str) -> str:
        name = checkpoint_name.lower()
        if "flux" in name:
            return "flux"
        if "qwen" in name:
            return "qwen"
        if any(token in name for token in ("sdxl", "_xl", "xl_base", "pony", "illustrious")):
            return "sdxl"
        if any(token in name for token in ("sd15", "sd_15", "v1-5", "v1_5", "dreamshaper", "realisticvision")):
            return "sd15"
        return "unknown"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="VideoAI_SmartFaceIDLoraRouter",
            display_name="Video.AI Smart FaceID LoRA Router",
            category=CATEGORY,
            inputs=[
                io.Model.Input("model"),
                io.Clip.Input("clip"),
                io.String.Input("checkpoint_name", default=""),
                io.Combo.Input("lora_name", options=cls._lora_options()),
                io.Float.Input("strength_model", default=0.8, min=-20.0, max=20.0, step=0.01),
                io.Float.Input("strength_clip", default=0.8, min=-20.0, max=20.0, step=0.01),
                io.Combo.Input("model_family", options=["auto", "sd15", "sdxl", "flux", "qwen"], default="auto"),
            ],
            outputs=[
                io.Model.Output(display_name="MODEL"),
                io.Clip.Output(display_name="CLIP"),
                io.Boolean.Output(display_name="APPLIED"),
            ],
        )

    @classmethod
    def execute(cls, model, clip, checkpoint_name="", lora_name="None", strength_model=0.8, strength_clip=0.8, model_family="auto") -> io.NodeOutput:
        family = model_family if model_family != "auto" else cls._detect_family(checkpoint_name)
        if family != "sd15" or not lora_name or lora_name == "None":
            return io.NodeOutput(model, clip, False)
        import comfy.sd
        import comfy.utils
        import folder_paths

        lora_path = folder_paths.get_full_path("loras", lora_name)
        if not lora_path:
            return io.NodeOutput(model, clip, False)
        lora = comfy.utils.load_torch_file(lora_path, safe_load=True)
        model, clip = comfy.sd.load_lora_for_models(model, clip, lora, strength_model, strength_clip)
        return io.NodeOutput(model, clip, True)


class VideoAI_VideoFrameSaver(io.ComfyNode):  # noqa: N801
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="VideoAI_VideoFrameSaver",
            display_name="Video.AI Video Frame Saver",
            category=CATEGORY,
            inputs=[
                io.Image.Input("images"),
                io.String.Input("output_dir", default="studio_outputs/frames"),
                io.Int.Input("scene_index", default=1, min=0, max=100000),
                io.String.Input("filename_prefix", default="scene", optional=True),
                io.String.Input("metadata_json", default="", multiline=True, optional=True),
                io.Boolean.Input("overwrite", default=True, optional=True),
                BARRIER.Input("barrier", optional=True),
            ],
            outputs=[io.String.Output(display_name="SAVED_PATHS"), io.Int.Output(display_name="COUNT")],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, images, output_dir, scene_index, filename_prefix="scene", metadata_json="", overwrite=True, barrier=None) -> io.NodeOutput:  # noqa: ARG003
        from PIL.PngImagePlugin import PngInfo

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        frames = tensor_to_images(images)
        saved = []
        for idx, img in enumerate(frames):
            suffix = f"_{idx + 1:04d}" if len(frames) > 1 else ""
            path = out_dir / f"{filename_prefix}_{scene_index:02d}{suffix}.png"
            if path.exists() and not overwrite:
                n = 1
                while path.exists():
                    path = out_dir / f"{filename_prefix}_{scene_index:02d}{suffix}_dup{n}.png"
                    n += 1
            meta = PngInfo()
            if cls.hidden.prompt is not None:
                meta.add_text("prompt", json.dumps(cls.hidden.prompt))
            if cls.hidden.extra_pnginfo is not None:
                for key, value in cls.hidden.extra_pnginfo.items():
                    meta.add_text(key, json.dumps(value))
            if metadata_json:
                meta.add_text("video_ai", metadata_json)
            img.save(path, pnginfo=meta, compress_level=4)
            saved.append(str(path))
        return io.NodeOutput(json.dumps(saved), len(saved))


class VideoAIExtension(ComfyExtension):
    async def get_node_list(self):
        return [
            VideoAI_ProjectConfigLoader,
            VideoAI_ConfigCheckpointLoader,
            VideoAI_ConfigKSampler,
            VideoAI_CharacterPortraitLoader,
            VideoAI_FreeMemoryBarrier,
            VideoAI_SmartFaceIDLoraRouter,
            VideoAI_VideoFrameSaver,
        ]


async def comfy_entrypoint() -> ComfyExtension:
    return VideoAIExtension()
