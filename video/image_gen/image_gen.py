"""image_gen.py - Image generation.

ComfyUI is the primary image backend (config image_gen.backend: comfyui).
Bonsai 4B ternary (gemlite 2-bit, via diffusers) is the fallback backend used
when ComfyUI fails. Character face consistency on the Bonsai path is achieved
via IP-Adapter FLUX v2 (XLabs-AI/flux-ip-adapter-v2) referencing a per-character
master portrait stored in the project store.

Public surface:
- generate_images(prompts, output_dir, config, char_presence=None)
- unload_bonsai_pipeline()
- unload_ip_adapter() (re-exported from ip_adapter)
- get_oom_report(), clear_oom_events(), _record_oom_event()
- _prompt_cache_key()
- _maybe_upscale()
"""

import contextlib
import hashlib
import json
import logging
import os
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from tqdm import tqdm

log = logging.getLogger(__name__)

# ── Cached Bonsai pipeline (one process-wide instance) ───────────────────
_bonsai_pipe = None
_bonsai_pipe_lock = threading.Lock()
_bonsai_model_id: str | None = None  # tracks which model is loaded; reload if cfg changes
# Module-level OOM event list — shared between portrait gen + frame gen.
_oom_events: list = []
_oom_events_lock = threading.Lock()


def _record_oom_event(event: dict) -> None:
    with _oom_events_lock:
        _oom_events.append(event)


def get_oom_report() -> list:
    """Return a list of OOM events that occurred during this session."""
    with _oom_events_lock:
        return list(_oom_events)


def clear_oom_events() -> None:
    """Reset OOM event list between pipeline runs."""
    with _oom_events_lock:
        _oom_events.clear()


def unload_bonsai_pipeline() -> None:
    """Unload the cached Bonsai pipeline from GPU to free VRAM."""
    global _bonsai_pipe, _bonsai_model_id
    if _bonsai_pipe is not None:
        try:
            del _bonsai_pipe
            _bonsai_pipe = None
            _bonsai_model_id = None
            import gc

            gc.collect()
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            log.info("[image_gen] Bonsai pipeline unloaded from GPU — VRAM freed")
        except Exception as e:
            log.warning(f"[image_gen] Could not fully unload Bonsai pipeline: {e}")
    else:
        log.debug("[image_gen] unload_bonsai_pipeline called but pipeline is already unloaded")


# ── IP-Adapter re-export for convenience ─────────────────────────────────
# (Most callers shouldn't have to import ip_adapter directly.)
from video.image_gen.ip_adapter import (
    get_ip_adapter,  # noqa: F401
    unload_ip_adapter,  # noqa: F401
)


def generate_images(
    prompts,
    output_dir: Path,
    config: dict,
    char_presence: list[dict[str, float]] | None = None,
    project_id: str | None = None,
) -> list[Path]:
    """Generate images from prompts using the configured backend.

    Args:
        prompts: Either a plain semicolon-separated string, or a tuple
                 (prompts_str, neg_prompt_override). Bonsai ignores negative
                 prompts — the override is accepted for compatibility only.
        output_dir: Directory to save generated PNG images.
        config: Full pipeline config dict.
        char_presence: Optional list of per-frame character weight dicts.
        project_id: Project name (used to resolve master portrait paths).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = config.get("image_gen") or {}

    if isinstance(prompts, list):
        prompt_list = [str(p).strip() for p in prompts if str(p).strip()]
    elif isinstance(prompts, str):
        prompt_list = [p.strip() for p in prompts.split(";") if p.strip()]
    else:
        prompt_list = [str(prompts).strip()]

    backend = cfg.get("backend", "bonsai")
    composition_mode = cfg.get("composition_mode", "one_pass")

    if backend == "comfyui" and composition_mode == "qwen_edit":
        qwen_cfg = cfg.get("qwen_edit", {}) or {}
        if qwen_cfg.get("enabled", False):
            try:
                return _comfyui_qwen_edit(
                    prompt_list,
                    output_dir,
                    cfg,
                    char_presence=char_presence,
                    project_id=project_id or "",
                )
            except Exception as e:
                log.warning(f"[image_gen] Qwen edit failed: {e}")
                fallback = cfg.get("fallback_backend", "bonsai")
                if fallback == "bonsai":
                    log.info("[image_gen] Falling back to Bonsai after qwen_edit error")
                    return _bonsai(
                        prompt_list,
                        output_dir,
                        cfg,
                        char_presence=char_presence,
                        project_id=project_id or "",
                    )
                raise
        log.info("[image_gen] qwen_edit mode is configured but disabled; using one_pass")

    if backend == "comfyui" and composition_mode == "layered_v3":
        try:
            from video.image_gen.layered_v3 import generate_layered_images

            return generate_layered_images(
                prompt_list,
                output_dir,
                cfg,
                char_presence=char_presence,
                project_id=project_id or "",
            )
        except Exception as e:
            log.warning(f"[image_gen] Layered v3 failed: {e}")
            fallback = cfg.get("fallback_backend", "bonsai")
            if fallback == "bonsai":
                log.info("[image_gen] Falling back to Bonsai after layered_v3 error")
                return _bonsai(
                    prompt_list,
                    output_dir,
                    cfg,
                    char_presence=char_presence,
                    project_id=project_id or "",
                )
            raise

    if backend == "comfyui":
        try:
            return _comfyui(
                prompt_list,
                output_dir,
                cfg,
            )
        except Exception as e:
            log.warning(f"[image_gen] ComfyUI failed: {e}")
            fallback = cfg.get("fallback_backend", "bonsai")
            if fallback == "bonsai":
                log.info("[image_gen] Falling back to Bonsai")
                return _bonsai(
                    prompt_list,
                    output_dir,
                    cfg,
                    char_presence=char_presence,
                    project_id=project_id or "",
                )
            raise
    else:
        return _bonsai(
            prompt_list,
            output_dir,
            cfg,
            char_presence=char_presence,
            project_id=project_id or "",
        )


# ── CACHE HELPERS ──────────────────────────────────────────────────────────


def _master_portrait_hash_for_frame(char_key: str | None) -> str:
    """Look up the master portrait content hash for a character.

    Returns the hash if the project store has one, else ''. Used in the
    per-frame cache key so portrait regeneration invalidates stale PNGs.
    """
    if not char_key:
        return ""
    try:
        from memory.project_store import ProjectStore

        ps = ProjectStore(_current_project_id or "_default")
        return ps.get_master_portrait_hash(char_key)
    except Exception:
        return ""


# Module-level scratch for project_id (set by _bonsai at the start of each call).
_current_project_id: str = ""


def _prompt_cache_key(
    prompt: str,
    cfg: dict,
    neg_prompt: str = "",
    lora_state: str = "",
    seed: int = 0,
    lora_fingerprint: str = "",
    throttled_steps: int | None = None,
    master_portrait_hash: str = "",
) -> str:
    """Return an 8-char hex MD5 hash of prompt + generation parameters.

    Bonsai-specific:
    - `master_portrait_hash` is included so regenerating the master portrait
      auto-invalidates all cached frames for that character.
    - `model_id` is read from `bonsai_model` (Bonsai config) or `sd_model_path`
      (legacy key, used as a fallback by callers that haven't migrated).
    - `guidance_scale` default is 3.5 (Bonsai's tested sweet spot).
    """
    if isinstance(prompt, list):
        prompt = ";".join([str(p) for p in prompt])
    elif not isinstance(prompt, str):
        prompt = str(prompt)
    steps = cfg.get("steps", 4)
    width = cfg.get("width", 1024)
    height = cfg.get("height", 1024)
    guidance_scale = cfg.get("guidance_scale", 3.5)
    model_id = cfg.get("bonsai_model") or cfg.get("sd_model_path") or "bonsai"
    effective_steps = throttled_steps if throttled_steps is not None else steps
    raw = (
        f"{prompt}|steps={effective_steps}|w={width}|h={height}"
        f"|gs={guidance_scale}|neg={neg_prompt}|lora={lora_state}|model={model_id}"
        f"|seed={seed}|lora_fp={lora_fingerprint}|mp_hash={master_portrait_hash}"
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


# ── BONSAI ────────────────────────────────────────────────────────────────


def _load_bonsai_pipeline(model_id: str):
    """Load (or return cached) Bonsai pipeline. Thread-safe."""
    global _bonsai_pipe, _bonsai_model_id
    with _bonsai_pipe_lock:
        if _bonsai_pipe is not None and _bonsai_model_id == model_id:
            return _bonsai_pipe
        if _bonsai_pipe is not None and _bonsai_model_id != model_id:
            log.info(f"[Bonsai] Model changed ({_bonsai_model_id} -> {model_id}); reloading")
            unload_bonsai_pipeline()
        import torch  # local import; user may not have torch at import time

        try:
            from diffusers import DiffusionPipeline
        except ImportError as e:
            raise ImportError(
                "pip install -U diffusers transformers accelerate gemlite hqq triton-windows"
            ) from e

        log.info(f"[Bonsai] Loading model: {model_id}")
        pipe = DiffusionPipeline.from_pretrained(
            model_id,
            dtype=torch.bfloat16,
            device_map="cuda",
        )
        _bonsai_pipe = pipe
        _bonsai_model_id = model_id

        # Attach IP-Adapter (best-effort; if it fails, frame gen still works
        # for env frames and characters without a master portrait).
        try:
            from video.image_gen.ip_adapter import get_ip_adapter

            get_ip_adapter().attach(pipe)
        except Exception as e:
            log.warning(f"[Bonsai] Could not attach IP-Adapter: {e} — face consistency disabled")

        log.info("[Bonsai] Pipeline loaded")
        return _bonsai_pipe


def _resolve_dominant_char(char_presence: dict | None) -> tuple[str | None, float]:
    """Return (char_key, weight) of the dominant character, or (None, 0.0).

    Threshold: weight >= 0.3 means the frame is "about" that character.
    """
    return _resolve_dominant_char_at_threshold(char_presence, 0.3)


def _resolve_dominant_char_at_threshold(
    char_presence: dict | None,
    threshold: float,
) -> tuple[str | None, float]:
    """Return dominant character using a caller-provided presence threshold."""
    if not char_presence:
        return None, 0.0
    if not isinstance(char_presence, dict) or not char_presence:
        return None, 0.0
    best_key = max(char_presence, key=char_presence.get)
    best_weight = float(char_presence[best_key])
    if best_weight < threshold:
        return None, 0.0
    return best_key, best_weight


def _bonsai(
    prompts: list[str],
    out: Path,
    cfg: dict,
    char_presence: list[dict[str, float]] | None = None,
    project_id: str = "",
) -> list[Path]:
    """Run Bonsai inference. See generate_images() for arg docs."""
    global _current_project_id
    _current_project_id = project_id

    import torch

    model_id = cfg.get("bonsai_model", "prism-ml/bonsai-image-ternary-4B-gemlite-2bit")
    pipe = _load_bonsai_pipeline(model_id)

    # IP-Adapter scale is read from cfg (default 0.8, balanced).
    ip_scale = float(cfg.get("ip_adapter_scale", 0.8))

    images: list[Path] = []
    cache_hits = 0
    fresh_gen = 0

    with tqdm(total=len(prompts), desc="  Bonsai", leave=False) as pbar:
        for i, prompt in enumerate(prompts):
            cp = {}
            if isinstance(char_presence, list) and i < len(char_presence):
                val = char_presence[i]
                if isinstance(val, dict):
                    cp = val

            dom_char, dom_weight = _resolve_dominant_char(cp)

            # ── Build the per-frame prompt ─────────────────────────────
            # Prepend the dominant character's visual description as a second
            # layer of consistency (alongside IP-Adapter). Bonsai gets no
            # negative prompt by design.
            frame_prompt = prompt
            if dom_char:
                try:
                    from memory.project_store import ProjectStore

                    ps = ProjectStore(project_id or "_default")
                    entry = ps.get_character(dom_char) or {}
                    desc = entry.get("visual_description", "")
                    if desc:
                        frame_prompt = f"{desc}, {prompt}"
                except Exception as e:
                    log.debug(f"[Bonsai] Could not load character description for {dom_char}: {e}")

            # ── Seed resolution (copy of SD logic, simplified) ─────────
            _seed = int(hashlib.md5(f"frame_{i}".encode()).hexdigest()[:8], 16) % (2**32)
            try:
                if torch.cuda.is_available() and dom_char:
                    if dom_weight >= 0.3:
                        _seed = int(hashlib.md5(dom_char.encode()).hexdigest()[:8], 16) % (
                            2**32
                        )
                        _seed = (_seed + i * 7919) % (2**32)  # per-frame perturb
                else:
                    _seed = int(
                        hashlib.md5(f"env_{i}_{prompt[:40]}".encode()).hexdigest()[:8], 16
                    ) % (2**32)
            except Exception as _seed_err:
                log.debug(f"[Bonsai] Could not resolve seed: {_seed_err}")

            # ── Throttle steps if VRAM is low (mirrors SD VRAM guard) ─
            throttled_steps = int(cfg.get("steps", 4))
            try:
                if torch.cuda.is_available():
                    free_vram, _total_vram = torch.cuda.mem_get_info()
                    free_vram_gb = free_vram / (1024**3)
                    if free_vram_gb < 1.5:
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                        free_vram, _ = torch.cuda.mem_get_info()
                        free_vram_gb = free_vram / (1024**3)
                    if free_vram_gb < 1.2:
                        throttled_steps = max(2, int(throttled_steps * 0.5))
                        log.warning(
                            f"[Bonsai] VRAM Guard: free={free_vram_gb:.2f}GB — throttling to {throttled_steps} steps"
                        )
            except Exception as e:
                log.debug(f"[Bonsai] VRAM guard failed: {e}")

            # ── Cache key (includes master portrait hash for invalidation) ─
            master_hash = _master_portrait_hash_for_frame(dom_char)
            cache_key = _prompt_cache_key(
                f"{frame_prompt}|frame={i}",
                cfg,
                neg_prompt="",
                lora_state="",
                seed=_seed,
                lora_fingerprint="",
                throttled_steps=throttled_steps,
                master_portrait_hash=master_hash,
            )
            cached_path = out / f"scene_{i + 1:02d}_{cache_key}.png"
            if cached_path.exists():
                log.info(f"[Bonsai] Cache hit: {cached_path.name}")
                images.append(cached_path)
                cache_hits += 1
                pbar.update(1)
                continue

            # ── IP-Adapter: load master portrait if dominant char present
            ip_embeds = None
            ip_image_kwarg = None
            if dom_char:
                try:
                    from memory.project_store import ProjectStore
                    from video.image_gen.ip_adapter import get_ip_adapter

                    ps = ProjectStore(project_id or "_default")
                    master_path = ps.get_master_portrait_path(dom_char)
                    if master_path:
                        mgr = get_ip_adapter()
                        mgr.set_scale(ip_scale)
                        ip_embeds = mgr.pre_encode(dom_char, master_path)
                        if ip_embeds is None:
                            ip_image_kwarg = mgr.get_image(dom_char)
                    else:
                        # ── Lazy portrait generation (Phase 1 trigger) ───
                        # No master portrait yet for this character. Generate
                        # one now via the helper from pre_production, then
                        # continue with this frame using the new portrait.
                        log.info(
                            f"[Bonsai] No master portrait for '{dom_char}' — generating one"
                        )
                        try:
                            from core.pre_production import generate_master_portrait

                            char_data = ps.get_character(dom_char) or {"name": dom_char}
                            new_path = generate_master_portrait(
                                char_key=dom_char,
                                project_id=project_id or "_default",
                                char_data=char_data,
                                config={"image_gen": cfg},
                            )
                            if new_path:
                                # Re-read from store (generate_master_portrait
                                # already wrote the path + hash).
                                master_path = ps.get_master_portrait_path(dom_char)
                                if master_path:
                                    mgr = get_ip_adapter()
                                    mgr.set_scale(ip_scale)
                                    ip_embeds = mgr.pre_encode(dom_char, master_path)
                                    if ip_embeds is None:
                                        ip_image_kwarg = mgr.get_image(dom_char)
                        except Exception as e:
                            log.warning(
                                f"[Bonsai] Lazy portrait gen failed for {dom_char}: {e} — "
                                "frame will be prompt-only"
                            )
                except Exception as e:
                    log.debug(f"[Bonsai] IP-Adapter setup failed for {dom_char}: {e}")

            # ── 2-Tier OOM-Resilient Inference ───────────────────────
            # Tier 1: normal, Tier 2: half-steps, then skip.
            img = None
            _generator = None
            try:
                if torch.cuda.is_available() and _seed is not None:
                    _generator = torch.Generator(device="cuda").manual_seed(_seed)
            except Exception as _seed_err:
                log.debug(f"[Bonsai] Could not set per-frame seed: {_seed_err}")

            call_kwargs: dict = {
                "prompt": frame_prompt,
                "height": cfg.get("height", 1024),
                "width": cfg.get("width", 1024),
                "num_inference_steps": throttled_steps,
                "guidance_scale": float(cfg.get("guidance_scale", 3.5)),
            }
            if _generator is not None:
                call_kwargs["generator"] = _generator
            if ip_embeds is not None:
                call_kwargs["ip_adapter_image_embeds"] = ip_embeds
            elif ip_image_kwarg is not None:
                call_kwargs["ip_adapter_image"] = ip_image_kwarg

            with contextlib.suppress(AttributeError):
                pipe.set_ip_adapter_scale(ip_scale) if dom_char else None

            try:
                with torch.inference_mode():
                    img = pipe(**call_kwargs).images[0]
            except torch.cuda.OutOfMemoryError:
                log.warning(
                    f"[Bonsai][OOM] Tier 1 CUDA OOM on image {i + 1} — halving steps"
                )
                torch.cuda.empty_cache()
                reduced_steps = max(2, int(throttled_steps * 0.5))
                call_kwargs["num_inference_steps"] = reduced_steps
                try:
                    with torch.inference_mode():
                        img = pipe(**call_kwargs).images[0]
                    log.info(f"[Bonsai][OOM] Tier 2 recovered at {reduced_steps} steps")
                    _record_oom_event(
                        {
                            "image_index": i + 1,
                            "tier_failed": 1,
                            "fallback_tier": 2,
                            "steps_used": reduced_steps,
                            "oom_fallback": False,
                        }
                    )
                except torch.cuda.OutOfMemoryError:
                    log.error(
                        f"[Bonsai][OOM] All CUDA tiers failed for image {i + 1} — "
                        "CPU inference not viable for 4B model, skipping frame"
                    )
                    _record_oom_event(
                        {
                            "image_index": i + 1,
                            "tier_failed": 2,
                            "fallback_tier": None,
                            "steps_used": 0,
                            "oom_fallback": True,
                            "skipped": True,
                        }
                    )
                    pbar.update(1)
                    continue
            except Exception as e:
                log.exception(f"[Bonsai] Generation failed for image {i + 1}: {e}")
                pbar.update(1)
                continue

            if img is None:
                pbar.update(1)
                continue

            img = _maybe_upscale(img, cfg)
            img.save(str(cached_path))
            images.append(cached_path)
            fresh_gen += 1
            pbar.update(1)

    total = cache_hits + fresh_gen
    log.info(
        f"[Bonsai] {total} images — {cache_hits} cached, {fresh_gen} generated fresh"
    )
    print(
        f"[image_gen] Bonsai summary: {total} images | "
        f"{cache_hits} cached (skipped) | {fresh_gen} generated fresh"
    )

    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception as e:
        log.debug(f"[Bonsai] CUDA cleanup failed: {e}")

    # Bonsai is the fallback path only; free the 4B model so it does not stay
    # resident in VRAM and starve the next sequential GPU task.
    unload_bonsai_pipeline()

    return images


# ── UPSCALER ──────────────────────────────────────────────────────────────


def _maybe_upscale(img, cfg: dict):
    """Optionally upscale a PIL image using the configured upscaler.

    See config.image_gen.upscaler. Off by default; Lanczos fallback.
    """
    upscaler_cfg = cfg.get("upscaler") or {}
    model_name = (upscaler_cfg.get("model") or "none").lower()
    if model_name == "none":
        return img

    target_w = int(upscaler_cfg.get("target_width", 1920))
    target_h = int(upscaler_cfg.get("target_height", 1080))

    # Try Real-ESRGAN
    if model_name in ("4x-ultrasharp", "realesrgan", "real-esrgan"):
        try:
            import numpy as np
            from basicsr.archs.rrdbnet_arch import RRDBNet
            from realesrgan import RealESRGANer

            scale = int(upscaler_cfg.get("scale", 4))
            model_path = upscaler_cfg.get("model_path", "")

            if not model_path:
                log.warning("[Upscale] model_path not set — falling back to Lanczos")
                raise ImportError("no model_path")

            upsampler = RealESRGANer(
                scale=scale,
                model_path=model_path,
                model=RRDBNet(
                    num_in_ch=3,
                    num_out_ch=3,
                    num_feat=64,
                    num_block=23,
                    num_grow_ch=32,
                    scale=scale,
                ),
                tile=512,
                tile_pad=10,
                pre_pad=0,
                half=True,
            )
            img_np = np.array(img)
            out_np, _ = upsampler.enhance(img_np, outscale=scale)
            from PIL import Image as _PILImage

            upscaled = _PILImage.fromarray(out_np)
            if upscaled.size != (target_w, target_h):
                upscaled = upscaled.resize((target_w, target_h), _PILImage.LANCZOS)
            log.debug(f"[Upscale] {model_name}: {img.size} → {upscaled.size}")
            return upscaled
        except Exception as e:
            log.warning(f"[Upscale] {model_name} failed ({e}) — falling back to Lanczos")

    # Lanczos fallback
    try:
        from PIL import Image as _PILImage

        resized = img.resize((target_w, target_h), _PILImage.LANCZOS)
        log.debug(f"[Upscale] Lanczos: {img.size} → {resized.size}")
        return resized
    except Exception as e:
        log.warning(f"[Upscale] Lanczos failed ({e}) — returning original")
        return img


# ── COMFYUI ──────────────────────────────────────────────────────────────


def _comfyui(prompts: list[str], out: Path, cfg: dict) -> list[Path]:
    """Run ComfyUI inference."""
    from video.image_gen.comfyui_client import ComfyUIClient
    from video.image_gen.comfyui_runtime import get_comfyui_runtime
    from video.image_gen.comfyui_workflow import WorkflowPatcher, create_default_workflow

    comfy_cfg = cfg.get("comfyui", {})
    runtime = get_comfyui_runtime({"comfyui": comfy_cfg})

    if not runtime.ensure_running(timeout=comfy_cfg.get("auto_start_timeout", 60)):
        raise RuntimeError(
            f"ComfyUI not running at {runtime.base_url} and auto_start is disabled"
        )

    client = ComfyUIClient(base_url=runtime.base_url, timeout=comfy_cfg.get("timeout_seconds", 300))

    workflow_path = comfy_cfg.get("workflow_path")
    if workflow_path:
        patcher = WorkflowPatcher(Path(workflow_path))
    else:
        patcher = None

    width = comfy_cfg.get("width", cfg.get("width", 1024))
    height = comfy_cfg.get("height", cfg.get("height", 1024))
    steps = comfy_cfg.get("steps", cfg.get("steps", 20))
    cfg_scale = comfy_cfg.get("cfg", cfg.get("guidance_scale", 7.0))
    sampler = comfy_cfg.get("sampler_name", "euler")
    scheduler = comfy_cfg.get("scheduler", "normal")
    checkpoint = comfy_cfg.get("checkpoint", "")
    neg_prompt = comfy_cfg.get("negative_prompt", "")

    images: list[Path] = []

    with tqdm(total=len(prompts), desc="  ComfyUI", leave=False) as pbar:
        for i, prompt in enumerate(prompts):
            filename_prefix = f"scene_{i + 1:02d}"
            if patcher:
                workflow = patcher.patch_all(
                    prompt=prompt,
                    negative_prompt=neg_prompt,
                    width=width,
                    height=height,
                    steps=steps,
                    cfg=cfg_scale,
                    sampler_name=sampler,
                    scheduler=scheduler,
                    checkpoint=checkpoint,
                    filename_prefix=filename_prefix,
                ).get_workflow()
            else:
                workflow = create_default_workflow(
                    prompt=prompt,
                    negative_prompt=neg_prompt,
                    width=width,
                    height=height,
                    steps=steps,
                    cfg=cfg_scale,
                    sampler_name=sampler,
                    scheduler=scheduler,
                    checkpoint=checkpoint,
                    filename_prefix=filename_prefix,
                )

            output_images = client.generate_image(
                workflow,
                out,
                filename_prefix=f"scene_{i + 1:02d}",
                poll_interval=comfy_cfg.get("poll_seconds", 1.0),
                timeout=comfy_cfg.get("timeout_seconds", 300),
            )

            images.extend(output_images)
            pbar.update(1)

    log.info(f"ComfyUI: {len(images)} images generated")

    if comfy_cfg.get("unload_after_batch", False):
        log.info("[ComfyUI] Unloading after batch (VRAM release)")
        client.free_memory()

    return images


def _qwen_seed(char_key: str, frame_index: int, prompt: str) -> int:
    raw = f"qwen_edit|{char_key}|{frame_index}|{prompt[:80]}"
    return int(hashlib.md5(raw.encode()).hexdigest()[:8], 16) % (2**32)


def _free_comfyui_memory(cfg: dict) -> None:
    try:
        from video.image_gen.comfyui_client import ComfyUIClient
        from video.image_gen.comfyui_runtime import get_comfyui_runtime

        comfy_cfg = cfg.get("comfyui", {}) or {}
        runtime = get_comfyui_runtime({"comfyui": comfy_cfg})
        client = ComfyUIClient(base_url=runtime.base_url, timeout=comfy_cfg.get("timeout_seconds", 300))
        client.free_memory()
        log.info("[ComfyUI] Requested memory free before Qwen edit pass")
    except Exception as e:
        log.debug("[ComfyUI] Could not free memory before Qwen edit pass: %s", e)


def _comfyui_qwen_edit(
    prompts: list[str],
    out: Path,
    cfg: dict,
    char_presence: list[dict[str, float]] | None = None,
    project_id: str = "",
) -> list[Path]:
    """Two-pass ComfyUI pipeline: generate full backgrounds, then paste characters.

    Pass 1 deliberately uses the existing character-blind ComfyUI one-pass path
    to create complete backgrounds. Pass 2 loads Qwen-Image-Edit only for frames
    with a saved character and writes the result back to the same frame path.
    """
    qwen_cfg = cfg.get("qwen_edit", {}) or {}
    threshold = float(qwen_cfg.get("character_threshold", 0.05))

    log.info("[qwen_edit] Pass 1/2: generating full backgrounds")
    images = _comfyui(prompts, out, cfg)
    if not images:
        return images

    _free_comfyui_memory(cfg)

    from video.image_gen.qwen_repose import repose_character

    edited: list[Path] = []
    with tqdm(total=len(images), desc="  Qwen edit", leave=False) as pbar:
        for i, image_path in enumerate(images):
            cp = {}
            if isinstance(char_presence, list) and i < len(char_presence):
                val = char_presence[i]
                if isinstance(val, dict):
                    cp = val
            dom_char, _dom_weight = _resolve_dominant_char_at_threshold(cp, threshold)
            if not dom_char:
                edited.append(Path(image_path))
                pbar.update(1)
                continue

            prompt = prompts[i] if i < len(prompts) else ""
            seed = _qwen_seed(dom_char, i, prompt)
            result = repose_character(
                base_image_path=str(image_path),
                char_key=dom_char,
                edit_prompt=prompt,
                output_path=str(image_path),
                config={"image_gen": cfg},
                project_id=project_id,
                seed=seed,
            )
            edited.append(Path(result))
            pbar.update(1)

    return edited


# ── REPLICATE / PEXELS (kept as code; not in active path) ────────────────


def _replicate(prompts: list[str], out: Path, cfg: dict) -> list[Path]:
    try:
        import replicate
    except ImportError as e:
        raise ImportError("pip install replicate") from e

    model = cfg.get(
        "replicate_model",
        "stability-ai/stable-diffusion:db21e45d3f7023abc2a46ee38a23973f6dce16bb082a930b0c49861f96d1e5bf",
    )
    images = []
    with tqdm(total=len(prompts), desc="  Replicate", leave=False) as pbar:
        for i, prompt in enumerate(prompts):
            output = replicate.run(
                model,
                input={
                    "prompt": prompt,
                    "width": cfg.get("width", 1024),
                    "height": cfg.get("height", 576),
                    "num_inference_steps": cfg.get("steps", 25),
                    "guidance_scale": cfg.get("guidance_scale", 7.5),
                },
            )
            url = output[0] if isinstance(output, list) else output
            p = out / f"scene_{i + 1:02d}.png"
            with (
                urllib.request.urlopen(url, timeout=30) as response,
                open(str(p), "wb") as out_file,
            ):
                out_file.write(response.read())
            images.append(p)
            pbar.update(1)

    log.info(f"Replicate: {len(images)} images")
    return images


def _pexels(prompts: list[str], out: Path, cfg: dict) -> list[Path]:
    api_key = cfg.get("pexels_api_key") or os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        raise ValueError("pexels_api_key missing from config or PEXELS_API_KEY env var")

    images = []
    with tqdm(total=len(prompts), desc="  Pexels", leave=False) as pbar:
        for i, prompt in enumerate(prompts):
            query = urllib.parse.quote_plus(prompt[:100])
            url = f"{{https://api.pexels.com/v1/search?query={query}}}&per_page=1&orientation=landscape"
            req = urllib.request.Request(url, headers={"Authorization": api_key})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            photos = data.get("photos", [])
            if not photos:
                log.warning(f"Pexels: no results for prompt {i + 1}, skipping")
                pbar.update(1)
                continue

            img_url = photos[0]["src"].get("large2x") or photos[0]["src"]["original"]
            p = out / f"scene_{i + 1:02d}.png"
            with (
                urllib.request.urlopen(img_url, timeout=30) as response,
                open(str(p), "wb") as out_file,
            ):
                out_file.write(response.read())
            images.append(p)
            pbar.update(1)

    log.info(f"Pexels: {len(images)} images")
    return images
