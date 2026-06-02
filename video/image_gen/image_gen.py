"""image_gen.py - Multi-backend: stable_diffusion | replicate | pexels."""

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
_sd_pipe = None  # cached: loaded once, reused across segments
_sd_pipe_lock = threading.Lock()  # thread safety for concurrent GPU access
_lora_lock = threading.Lock()  # thread safety for LoRA injection/unload
_active_lora_path: Path | None = None  # tracks currently loaded LoRA
# Module-level OOM event list (NOT thread-local!) — the pipeline runs process_segment
# in a ThreadPoolExecutor, and each spawned thread gets its own threading.local(),
# causing OOM events to be lost when the main thread calls get_oom_report().
# A threading.Lock protects concurrent writes.
_oom_events: list = []
_oom_events_lock = threading.Lock()

def _record_oom_event(event: dict) -> None:
    with _oom_events_lock:
        _oom_events.append(event)

def get_oom_report() -> list:
    """Return a list of OOM events that occurred during this session.

    Each entry is a dict: {segment_prompt_index, tier_failed, fallback_used, steps_used}
    Call this at the end of the pipeline to include in run_manifest.json.
    """
    with _oom_events_lock:
        return list(_oom_events)


def clear_oom_events() -> None:
    """Reset OOM event list between pipeline runs."""
    with _oom_events_lock:
        _oom_events.clear()


def unload_sd_pipeline() -> None:
    """Unload the cached SD pipeline from GPU to free VRAM for LoRA training."""
    global _sd_pipe, _active_lora_path
    if _sd_pipe is not None:
        try:
            import torch
            if hasattr(_sd_pipe, "unload_lora_weights"):
                try:
                    _sd_pipe.unload_lora_weights()
                except Exception as _lora_e:
                    log.warning(f"[LoRA] Failed to unload weights during pipeline teardown: {_lora_e}")
            del _sd_pipe
            _sd_pipe = None
            _active_lora_path = None
            import gc
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            log.info("[image_gen] SD pipeline unloaded from GPU — VRAM freed for LoRA training")
        except Exception as e:
            log.warning(f"[image_gen] Could not fully unload SD pipeline: {e}")
    else:
        log.debug("[image_gen] unload_sd_pipeline called but pipeline is already unloaded")


def generate_images(prompts, output_dir: Path, config: dict,
                    lora_paths: dict[str, Path] | None = None,
                    char_presence: list[dict[str, float]] | None = None) -> list[Path]:
    """Generate images from prompts.

    Args:
        prompts: Either a plain semicolon-separated string, or a tuple
                 (prompts_str, neg_prompt_override) as returned by enrich_prompts().
        output_dir: Directory to save generated images.
        config: Full pipeline config dict.
        lora_paths: Optional dict mapping character keys to trained LoRA .safetensors files.
        char_presence: Optional list of dictionaries mapping character keys to weights for each frame.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = config.get("image_gen") or {}
    backend = cfg.get("backend", "stable_diffusion")

    # Support tuple (prompts_str, neg_prompt_override) from enrich_prompts()
    neg_prompt_override = None
    if isinstance(prompts, tuple):
        prompts, neg_prompt_override = prompts

    if isinstance(prompts, list):
        prompt_list = [str(p).strip() for p in prompts if str(p).strip()]
    elif isinstance(prompts, str):
        prompt_list = [p.strip() for p in prompts.split(";") if p.strip()]
    else:
        prompt_list = [str(prompts).strip()]
    if backend == "replicate": return _replicate(prompt_list, output_dir, cfg)
    if backend == "pexels":    return _pexels(prompt_list, output_dir, cfg)
    return _stable_diffusion(prompt_list, output_dir, cfg,
                             neg_prompt_override=neg_prompt_override,
                             lora_paths=lora_paths,
                             char_presence=char_presence)


# ── CACHE HELPERS ──────────────────────────────────────────────────────────

def _prompt_cache_key(prompt: str, cfg: dict, neg_prompt: str = "",
                      lora_state: str = "", seed: int = 0,
                      lora_fingerprint: str = "",
                      throttled_steps: int | None = None) -> str:
    """Return an 8-char hex MD5 hash of prompt + generation parameters.

    B20 fix: use actual config values as defaults, not mismatched hardcoded ones.
    Task 10.7: include acceleration state so toggling accel invalidates stale cache.
    P3-2 fix: include resolved seed and LoRA file fingerprint (path+mtime) so that
    re-locking a character with a new seed, or retraining a LoRA at the same path,
    correctly invalidates the cached PNG.
    P4-2 fix: include throttled_steps (the actual steps used after VRAM guard) so
    a throttled image is never served as a full-quality cache hit.
    """
    if isinstance(prompt, list):
        prompt = ";".join([str(p) for p in prompt])
    elif not isinstance(prompt, str):
        prompt = str(prompt)
    # Use real config defaults (matching what _stable_diffusion actually uses)
    steps          = cfg.get("steps", 12)
    width          = cfg.get("width", 768)
    height         = cfg.get("height", 432)
    guidance_scale = cfg.get("guidance_scale", 6.0)
    model_id       = cfg.get("sd_model_path") or cfg.get("sd_model", "anyLoRA")
    # Acceleration state — switching modes must invalidate cached PNGs
    _accel         = cfg.get("acceleration") or {}
    accel_id       = (_accel.get("type") or "none").lower()
    accel_steps    = _accel.get("steps", 6) if accel_id != "none" else ""
    accel_gs       = _accel.get("guidance_scale", 1.5) if accel_id != "none" else ""
    # P4-2 fix: use the actual throttled steps when provided (VRAM guard may reduce
    # steps below the config value; a throttled image must not be served as a
    # full-quality cache hit).
    effective_steps = throttled_steps if throttled_steps is not None else steps
    raw = (f"{prompt}|steps={effective_steps}|w={width}|h={height}"
           f"|gs={guidance_scale}|neg={neg_prompt}|lora={lora_state}|model={model_id}"
           f"|accel={accel_id}|asteps={accel_steps}|ags={accel_gs}"
           f"|seed={seed}|lora_fp={lora_fingerprint}")
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


# ── STABLE DIFFUSION ───────────────────────────────────────────────────────

def _stable_diffusion(prompts: list[str], out: Path, cfg: dict,
                       neg_prompt_override: str | None = None,
                       lora_paths: dict[str, Path] | None = None,
                       char_presence: list[dict[str, float]] | None = None) -> list[Path]:
    """Run Stable Diffusion inference.

    Args:
        prompts: List of positive prompt strings.
        out: Output directory for PNG images.
        cfg: image_gen config section.
        neg_prompt_override: If provided, use this negative prompt instead of
                             cfg['negative_prompt'].
        lora_paths: Optional dict of character keys to their LoRA paths.
        char_presence: Optional list of dicts for per-frame character weights.
    """
    try:
        import torch
        from diffusers import DPMSolverMultistepScheduler, StableDiffusionPipeline
    except ImportError as e:
        import sys as _sys
        if _sys.platform == "win32":
            raise ImportError("pip install diffusers torch") from e
        else:
            raise ImportError("pip install diffusers torch xformers") from e

    global _sd_pipe
    with _sd_pipe_lock:
        if _sd_pipe is None:
            # P4-3 fix: unify SD load default with cache-key default.
            # Cache key uses cfg.get("sd_model_path") or cfg.get("sd_model", "anyLoRA").
            # Load path previously used f"Lykon/{cfg.get('sd_model', 'anyLoRA')}" as fallback,
            # causing a mismatch when sd_model_path is empty.  Both now use the same expression.
            model = cfg.get("sd_model_path") or cfg.get("sd_model", "anyLoRA")
            dtype = torch.float16 if cfg.get("dtype") == "float16" else torch.float32

            # Enable TensorFloat32 (TF32) for Ampere/Ada GPU acceleration
            try:
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
                log.info("TensorFloat-32 (TF32) speedup enabled for CUDA")
            except Exception as e:
                log.warning(f"Could not enable TF32: {e}")

            _sd_pipe = StableDiffusionPipeline.from_pretrained(model, torch_dtype=dtype, safety_checker=None)

            # Inject DPM-Solver++ scheduler for 2x speedup at same quality
            try:
                sched_config = dict(_sd_pipe.scheduler.config)
                # Remove keys that might be incompatible with DPM
                for k in ["algorithm_type", "use_karras_sigmas"]:
                    sched_config.pop(k, None)
                _sd_pipe.scheduler = DPMSolverMultistepScheduler.from_config(sched_config)
                log.info("Injected DPMSolverMultistepScheduler for high-speed convergence")
            except Exception as e:
                log.warning(f"Could not inject DPM scheduler: {e}")

            # ── Acceleration adapter (R12.8): DMD2 / Hyper-SD / LCM ────────────
            # Off by default. When configured, loads a step-distillation LoRA and
            # switches the scheduler so images render in 4-8 steps instead of 12+.
            # DMD2 is recommended (preserves prompt adherence at low steps).
            _accel = cfg.get("acceleration") or {}
            _accel_type = (_accel.get("type") or "none").lower()
            if _accel_type != "none":
                try:
                    _accel_lora = _accel.get("lora_path", "")
                    if _accel_lora and Path(_accel_lora).exists():
                        # Load THEN fuse the acceleration LoRA into the base weights.
                        # Fusing is essential: the per-frame face-lock code calls
                        # unload_lora_weights() which would otherwise drop a plain
                        # acceleration adapter every frame. Fused weights are baked
                        # into the UNet and survive adapter swaps, so character LoRAs
                        # can still load/unload on top each frame without interference.
                        _sd_pipe.load_lora_weights(_accel_lora, adapter_name="_accel")
                        try:
                            _sd_pipe.fuse_lora(lora_scale=float(_accel.get("lora_scale", 1.0)))
                            _sd_pipe.unload_lora_weights()  # clear slot; fused delta persists in weights
                            log.info(f"[ACCEL] Fused {_accel_type} LoRA into base weights: {_accel_lora}")
                        except Exception as _fe:
                            log.warning(f"[ACCEL] fuse_lora failed ({_fe}); character LoRAs may interfere")
                    elif _accel_lora:
                        log.warning(f"[ACCEL] LoRA path not found: {_accel_lora} — using step/guidance overrides only")
                    # LCM needs its own scheduler
                    if _accel_type == "lcm":
                        try:
                            from diffusers import LCMScheduler
                            _sd_pipe.scheduler = LCMScheduler.from_config(_sd_pipe.scheduler.config)
                            log.info("[ACCEL] Switched to LCMScheduler")
                        except Exception as _le:
                            log.warning(f"[ACCEL] Could not set LCM scheduler: {_le}")
                    log.info(f"[ACCEL] Acceleration mode '{_accel_type}' active "
                             f"(steps/guidance overridden per config)")
                except Exception as _ae:
                    log.warning(f"[ACCEL] Could not enable acceleration '{_accel_type}': {_ae}")

            if cfg.get("enable_xformers") and hasattr(_sd_pipe, "enable_xformers_memory_efficient_attention"):
                import sys as _sys
                if _sys.platform == "win32":
                    # xformers requires triton which is not available on Windows
                    log.info("Windows detected — skipping xformers (triton unavailable)")
                    cfg["enable_xformers"] = False
                else:
                    try:
                        import triton  # noqa: F401
                        _sd_pipe.enable_xformers_memory_efficient_attention()
                        log.info("xformers memory-efficient attention enabled")
                    except ImportError:
                        log.info("Triton not available — skipping xformers (using attention slicing instead)")
                        cfg["enable_xformers"] = False
                    except Exception as xe:
                        log.warning(f"xformers enable failed ({xe}), falling back to attention slicing")
                        cfg["enable_xformers"] = False
            if cfg.get("attention_slicing"): _sd_pipe.enable_attention_slicing()

            # NHWC channels_last memory format — ~10% faster convolutions on Ada GPUs
            if cfg.get("channels_last") and torch.cuda.is_available():
                try:
                    _sd_pipe.unet = _sd_pipe.unet.to(memory_format=torch.channels_last)
                    _sd_pipe.vae = _sd_pipe.vae.to(memory_format=torch.channels_last)
                    log.info("Enabled channels_last (NHWC) memory format for UNet+VAE")
                except Exception as e:
                    log.warning(f"Could not enable channels_last: {e}")

            if torch.cuda.is_available():
                # Model CPU offload — moves layers GPU<->CPU as needed; ~40% less peak VRAM
                if cfg.get("model_cpu_offload"):
                    try:
                        _sd_pipe.enable_model_cpu_offload()
                        log.info("Enabled model CPU offload (layers move GPU<->CPU dynamically)")
                    except Exception as e:
                        log.warning(f"Could not enable model CPU offload: {e}")
                        _sd_pipe = _sd_pipe.to("cuda")
                elif cfg.get("group_offload"):
                    # Group offload — more granular than model offload; overlaps transfers with compute
                    try:
                        _sd_pipe.enable_group_offload(
                            onload_device=torch.device("cuda"),
                            offload_device=torch.device("cpu"),
                            offload_type="leaf_level",
                            use_stream=True,  # Overlaps data transfer with computation
                        )
                        log.info("Enabled group offload (leaf-level, stream-overlapped)")
                    except Exception as e:
                        log.warning(f"Could not enable group offload: {e}")
                        _sd_pipe = _sd_pipe.to("cuda")
                else:
                    _sd_pipe = _sd_pipe.to("cuda")

                try:
                    _sd_pipe.enable_vae_slicing()
                    log.info("Enabled VAE slicing for VRAM savings")
                except Exception as e:
                    log.warning(f"Could not enable VAE slicing: {e}")

                # VAE tiling — splits large images into tiles; prevents VAE OOM on 6GB
                if cfg.get("vae_tiling"):
                    try:
                        _sd_pipe.enable_vae_tiling()
                        log.info("Enabled VAE tiling for large image decoding")
                    except Exception as e:
                        log.warning(f"Could not enable VAE tiling: {e}")

                # torch.compile uses triton which is not available on Windows
                import sys as _sys
                if _sys.platform != "win32":
                    try:
                        _sd_pipe.unet = torch.compile(_sd_pipe.unet, mode="reduce-overhead", fullgraph=True)
                        log.info("Enabled torch.compile for UNet speedup")
                    except Exception as e:
                        log.info(f"torch.compile not available ({e}); using eager mode")
                else:
                    log.info("Windows detected — skipping torch.compile (triton unavailable)")
            else:
                _sd_pipe = _sd_pipe.to("cpu")

            log.info("SD pipeline loaded")

    # ── LoRA Face-Lock injection ───────────────────────────────────────────
    # Clear any previous adapters to prevent bleed from past runs
    with _lora_lock:
        try:
            _sd_pipe.unload_lora_weights()
        except Exception as _lora_e:
            log.warning(f"[LoRA] Failed to unload previous adapters — wrong character face may persist: {_lora_e}")

        loaded_adapters = set()
        if lora_paths:
            for c_key, path in lora_paths.items():
                if path.exists():
                    try:
                        _sd_pipe.load_lora_weights(str(path), adapter_name=c_key)
                        loaded_adapters.add(c_key)
                        log.info(f"[LoRA] Face-Lock adapter loaded: {c_key}")
                    except Exception as e:
                        log.warning(f"[LoRA] Failed to load adapter for {c_key}: {e}")
                else:
                    log.warning(f"[LoRA] LoRA file not found for {c_key}: {path}")

    images     = []
    cache_hits = 0
    fresh_gen  = 0
    _pipe_oomed_to_cpu = False
    # Use dynamic negative prompt if provided, else fall back to config value
    neg_prompt = neg_prompt_override if neg_prompt_override is not None else cfg.get("negative_prompt", "")
    if neg_prompt_override is not None:
        log.info(f"SD: using dynamic negative prompt ({len(neg_prompt.split(','))} tokens)")

    # ── A2: Build seed_map ONCE before the frame loop ─────────────────────
    # Previously PROJECTS_ROOT.iterdir() was called inside the per-frame loop,
    # causing N disk scans for N frames. Now we read project JSONs once and
    # cache the results in a dict {char_key -> seed}.
    _seed_map: dict = {}
    try:
        import json as _json

        from memory.project_store import PROJECTS_ROOT
        if PROJECTS_ROOT.exists():
            for _proj_dir in PROJECTS_ROOT.iterdir():
                _proj_file = _proj_dir / "project.json"
                if _proj_file.exists():
                    try:
                        _pdata = _json.loads(_proj_file.read_text(encoding="utf-8"))
                        for _ckey, _lock in _pdata.get("visual_locks", {}).items():
                            if _lock and _lock.get("seed") is not None and _ckey not in _seed_map:
                                _seed_map[_ckey] = int(_lock["seed"])
                    except Exception as _seed_err:
                        log.warning(f"[A2] Could not read seed from {_proj_file.name}: {_seed_err}")
        if _seed_map:
            log.debug(f"[A2] Seed map built once: {list(_seed_map.keys())}")
    except Exception as _sm_err:
        log.debug(f"[A2] Seed map build failed (non-fatal): {_sm_err}")

    # A2/lock_seed note: when a character is NOT in the project seed_map, the
    # per-frame logic below derives a deterministic seed from the character key
    # (md5(dominant_char)). That is already stable across segments and resumes,
    # which is exactly what image_gen.lock_seed promises — no extra topic-hash
    # seed is needed. The flag is honored implicitly by the constant char-keyed seed.

    with tqdm(total=len(prompts), desc="  SD", leave=False) as pbar:
        for i, prompt in enumerate(prompts):
            if torch.cuda.is_available() and _pipe_oomed_to_cpu:
                _pipe_oomed_to_cpu = False
                if not cfg.get("model_cpu_offload") and not cfg.get("group_offload"):
                    log.info("[OOM] Restoring model to CUDA from CPU fallback")
                    _sd_pipe = _sd_pipe.to("cuda")

            cp = {}
            if isinstance(char_presence, list) and i < len(char_presence):
                val = char_presence[i]
                if isinstance(val, dict):
                    cp = val

            # Find dominant weight for generic frame rules
            max_weight = max(cp.values()) if cp else 1.0

            # Dynamic negative prompt injection per-frame
            frame_neg_prompt = neg_prompt
            if max_weight < 0.3:
                # P3-4 fix: env frames (no dominant character) only need anti-portrait
                # tokens.  Removing "foggy", "blurry", "low detail" which contradict
                # the configured "atmospheric fog" visual style.
                frame_neg_prompt += ", (portrait:1.5), close up, face, single character"
            elif max_weight > 0.7:
                frame_neg_prompt += ", extra limbs, bad anatomy, disfigured"

            lora_state = ",".join(f"{k}:{v:.2f}" for k, v in sorted(cp.items())) if cp else ""
            # P3-2 fix: compute LoRA file fingerprint (path + mtime) for the active
            # LoRA paths so that retraining a LoRA at the same path invalidates cache.
            _lora_fp_parts = []
            if lora_paths:
                for _lk, _lp in sorted(lora_paths.items()):
                    if _lp and Path(_lp).exists():
                        try:
                            _lora_fp_parts.append(f"{_lk}:{_lp}:{Path(_lp).stat().st_mtime:.0f}")
                        except Exception:
                            _lora_fp_parts.append(f"{_lk}:{_lp}")
            _lora_fingerprint = "|".join(_lora_fp_parts)

            # ── Resolve seed (A2: look up from pre-built seed_map, no per-frame disk scan) ──
            # P3-2 fix: seed must be resolved before computing the cache key.
            # P3-3 fix: only perturb seed per-frame when a LoRA IS active.
            _seed = int(hashlib.md5(f"frame_{i}".encode()).hexdigest()[:8], 16) % (2**32)
            try:
                if torch.cuda.is_available():
                    dominant_char = max(cp, key=cp.get) if cp else None
                    if dominant_char and cp.get(dominant_char, 0) >= 0.3:
                        # A2: look up from pre-built seed_map (no disk scan here)
                        if dominant_char in _seed_map:
                            _seed = _seed_map[dominant_char]
                        else:
                            _seed = int(hashlib.md5(dominant_char.encode()).hexdigest()[:8], 16) % (2**32)
                        # P3-3 fix: only perturb seed per-frame when a LoRA is active
                        _char_has_lora = (loaded_adapters and dominant_char in loaded_adapters)
                        if _char_has_lora:
                            _seed = (_seed + i * 7919) % (2**32)  # 7919 = prime, good spread
                            log.debug(f"[Seed] Frame {i+1}: char={dominant_char} (LoRA active), seed={_seed}")
                        else:
                            log.debug(f"[Seed] Frame {i+1}: char={dominant_char} (no LoRA), seed={_seed} (constant)")
                    else:
                        _seed = int(hashlib.md5(f"env_{i}_{prompt[:40]}".encode()).hexdigest()[:8], 16) % (2**32)
                        log.debug(f"[Seed] Frame {i+1}: environment, seed={_seed}")
            except Exception as _seed_err:
                log.debug(f"[Seed] Could not resolve seed early: {_seed_err}")

            # ── Resolve throttled_steps early (needed for cache key) ──────
            # P4-2 fix: compute throttled_steps before the cache key so a VRAM-
            # throttled image is never served as a full-quality cache hit.
            # A4: use preview_steps in dry_run/preview_mode for faster iteration.
            _is_preview = cfg.get("_preview_mode", False) or cfg.get("_dry_run", False)
            if _is_preview:
                throttled_steps = int(cfg.get("preview_steps", 8))
                log.debug(f"[A4] steps={throttled_steps} (preview)")
            else:
                throttled_steps = cfg.get("steps", 12)
            _accel_cfg    = cfg.get("acceleration") or {}
            _accel_active = (_accel_cfg.get("type") or "none").lower() != "none"
            if _accel_active:
                throttled_steps = int(_accel_cfg.get("steps", 6))
                _guidance_scale = float(_accel_cfg.get("guidance_scale", 1.5))
            else:
                _guidance_scale = float(cfg.get("guidance_scale", 6.0))
            try:
                if torch.cuda.is_available():
                    free_vram, _total_vram = torch.cuda.mem_get_info()
                    free_vram_gb = free_vram / (1024 ** 3)

                    if free_vram_gb < 1.5:
                        log.info(f"VRAM Guard: Free VRAM low ({free_vram_gb:.2f} GB) — clearing cache")
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                        free_vram, _total_vram = torch.cuda.mem_get_info()
                        free_vram_gb = free_vram / (1024 ** 3)

                    if free_vram_gb < 1.2:
                        throttled_steps = max(8, int(throttled_steps * 0.6))
                        log.warning(f"VRAM Guard WARNING: Free VRAM critical ({free_vram_gb:.2f} GB) — Throttling SD steps to {throttled_steps} to prevent OOM!")
            except Exception as e:
                log.debug(f"Could not run VRAM Guard check: {e}")

            # Include frame index in the cache key so two frames with identical
            # prompt text never collide into the same cached PNG (duplicate-image fix).
            cache_key = _prompt_cache_key(f"{prompt}|frame={i}", cfg, neg_prompt=frame_neg_prompt,
                                          lora_state=lora_state, seed=_seed,
                                          lora_fingerprint=_lora_fingerprint,
                                          throttled_steps=throttled_steps)

            # ── 1. Check cache-keyed filename (new format) ─────────────────
            cached_path = out / f"scene_{i+1:02d}_{cache_key}.png"
            if cached_path.exists():
                log.info(f"Cache hit (new): {cached_path.name} — skipping SD")
                images.append(cached_path)
                cache_hits += 1
                pbar.update(1)
                continue


            # ── 3. Generate fresh image ────────────────────────────────────
            # Dynamically set active adapters for this specific frame
            if loaded_adapters:
                active_names = []
                active_weights = []
                for c_key, cw in cp.items():
                    if cw >= 0.3 and c_key in loaded_adapters:
                        active_names.append(c_key)
                        active_weights.append(cw)

                if active_names:
                    try:
                        _sd_pipe.set_adapters(active_names, adapter_weights=active_weights)
                        log.debug(f"[LoRA] Active adapters for frame {i+1}: {dict(zip(active_names, active_weights, strict=False))}")
                    except Exception as e:
                        log.warning(f"[LoRA] Failed to set adapters for frame {i+1}: {e}")
                else:
                    try:
                        _sd_pipe.set_adapters([])
                        log.debug(f"[LoRA] Adapters disabled for environmental frame {i+1}")
                    except Exception:
                        pass

            # ── 3-Tier OOM-Resilient Inference ─────────────────────────────
            img = None
            oom_fallback = False

            # B5 fix: per-character fixed seed for face consistency.
            # Seed was already resolved above (before cache key) for P3-2/P3-3.
            # Build the torch Generator from the pre-resolved _seed.
            _generator = None
            try:
                if torch.cuda.is_available() and _seed is not None:
                    _generator = torch.Generator(device="cuda").manual_seed(_seed)
                    log.debug(f"[Seed] Frame {i+1}: generator set, seed={_seed}")
            except Exception as _seed_err:
                log.debug(f"[Seed] Could not set per-frame seed: {_seed_err}")
                _generator = None

            # Tier 1: Normal CUDA inference
            # P3-16 fix: warn when the prompt likely exceeds CLIP's 77-token limit.
            # diffusers truncates silently; this makes the overflow visible in logs.
            _estimated_tokens = int(len(prompt.split()) * 1.3)
            if _estimated_tokens > 77:
                log.warning(
                    f"[CLIP] Frame {i+1}: prompt estimated at ~{_estimated_tokens} tokens "
                    f"(>{77}) — diffusers will truncate at 77 tokens; "
                    f"scene/style tail may be dropped. Consider shortening the prompt."
                )
            # P1-1 fix: torch.cuda.OutOfMemoryError is a subclass of RuntimeError.
            # It MUST be listed before except RuntimeError, otherwise the OOM is caught
            # by the RuntimeError handler, fails the "triton" check, and is re-raised —
            # making the entire 3-tier OOM recovery dead code on a 6GB GPU.
            try:
                with torch.inference_mode():
                    img = _sd_pipe(
                        prompt,
                        negative_prompt=frame_neg_prompt,
                        height=cfg.get("height", 432),
                        width=cfg.get("width", 768),
                        num_inference_steps=throttled_steps,
                        guidance_scale=_guidance_scale,
                        generator=_generator,
                    ).images[0]
            except torch.cuda.OutOfMemoryError:
                log.warning(f"[OOM] Tier 1 CUDA OOM on image {i+1} — clearing cache and retrying at 60% steps")
                torch.cuda.empty_cache()

                # Tier 2: Reduced steps on CUDA
                reduced_steps = max(8, int(throttled_steps * 0.6))
                try:
                    img = _sd_pipe(
                        prompt,
                        negative_prompt=frame_neg_prompt,
                        height=cfg.get("height", 432),
                        width=cfg.get("width", 768),
                        num_inference_steps=reduced_steps,
                        guidance_scale=_guidance_scale,
                        generator=_generator,
                    ).images[0]
                    log.info(f"[OOM] Tier 2 recovered at {reduced_steps} steps")
                    _record_oom_event({"image_index": i + 1, "tier_failed": 1,
                                          "fallback_tier": 2, "steps_used": reduced_steps,
                                          "oom_fallback": False})
                except torch.cuda.OutOfMemoryError:
                    log.warning("[OOM] Tier 2 CUDA OOM — falling back to CPU inference (4 steps)")
                    torch.cuda.empty_cache()

                    # Tier 3: CPU fallback at 4 steps
                    # Use a CPU generator seeded from the same _seed for reproducibility.
                    try:
                        if not cfg.get("model_cpu_offload") and not cfg.get("group_offload"):
                            _sd_pipe.to("cpu")
                        _pipe_oomed_to_cpu = True
                        _cpu_generator = torch.Generator(device="cpu").manual_seed(_seed)
                        img = _sd_pipe(
                            prompt,
                            negative_prompt=frame_neg_prompt,
                            height=cfg.get("height", 432),
                            width=cfg.get("width", 768),
                            num_inference_steps=4,
                            guidance_scale=_guidance_scale,
                            generator=_cpu_generator,
                        ).images[0]
                        log.warning("[OOM] Tier 3 CPU fallback succeeded (4 steps, lower quality)")
                        oom_fallback = True
                        _record_oom_event({"image_index": i + 1, "tier_failed": 2,
                                              "fallback_tier": 3, "steps_used": 4,
                                              "oom_fallback": True})
                    except Exception as cpu_err:
                        log.exception(f"[OOM] All 3 tiers failed for image {i+1}: {cpu_err} — skipping")
                        _record_oom_event({"image_index": i + 1, "tier_failed": 3,
                                               "fallback_tier": None, "steps_used": 0,
                                               "oom_fallback": True, "skipped": True})
                        pbar.update(1)
                        continue
            except RuntimeError as rte:
                if "triton" in str(rte).lower():
                    log.warning("[xformers] Triton not available — disabling xformers and retrying")
                    with contextlib.suppress(Exception):
                        _sd_pipe.disable_xformers_memory_efficient_attention()
                    with torch.inference_mode():
                        img = _sd_pipe(
                            prompt,
                            negative_prompt=frame_neg_prompt,
                            height=cfg.get("height", 432),
                            width=cfg.get("width", 768),
                            num_inference_steps=throttled_steps,
                            guidance_scale=_guidance_scale,
                            generator=_generator,
                        ).images[0]
                else:
                    raise

            if img is None:
                pbar.update(1)
                continue

            # R12.9 / B3: optional config-selectable upscale to sharpen for 1080p output.
            # Off by default; when enabled, upscales the generated image before saving.
            img = _maybe_upscale(img, cfg)

            img.save(str(cached_path))
            images.append(cached_path)
            fresh_gen += 1
            if oom_fallback:
                log.info(f"[OOM] Image {i+1} saved (CPU fallback): {cached_path.name}")
            pbar.update(1)

    # ── 5. Cache-vs-generated summary ─────────────────────────────────────
    total = cache_hits + fresh_gen
    log.info(
        f"SD: {total} images total — {cache_hits} from cache, {fresh_gen} generated fresh"
    )
    print(
        f"[image_gen] SD summary: {total} images | "
        f"{cache_hits} cached (skipped) | {fresh_gen} generated fresh"
    )

    try:
        import torch as _t
        if _t.cuda.is_available():
            _t.cuda.empty_cache()
    except Exception as e:
        log.debug(f"CUDA cleanup failed: {e}")

    return images


# ── UPSCALER (R12.9 / B3) ─────────────────────────────────────────────────

def _maybe_upscale(img, cfg: dict):
    """Optionally upscale a PIL image using the configured upscaler.

    Config: image_gen.upscaler = {model: "4x-UltraSharp"|"realesrgan"|"none",
                                   scale: 2, target_width: 1920, target_height: 1080}
    Off by default (model: "none"). When enabled, generates at a smaller base
    resolution and upscales to 1080p — sharper than stretching a small image.

    Falls back to Lanczos resize if the upscaler model is unavailable.
    """
    upscaler_cfg = cfg.get("upscaler") or {}
    model_name = (upscaler_cfg.get("model") or "none").lower()
    if model_name == "none":
        return img

    target_w = int(upscaler_cfg.get("target_width", 1920))
    target_h = int(upscaler_cfg.get("target_height", 1080))

    # Try Real-ESRGAN (covers both 4x-UltraSharp and realesrgan model names)
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
                model=RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                              num_block=23, num_grow_ch=32, scale=scale),
                tile=512,  # tiled for 6GB VRAM
                tile_pad=10,
                pre_pad=0,
                half=True,
            )
            img_np = np.array(img)
            out_np, _ = upsampler.enhance(img_np, outscale=scale)
            from PIL import Image as _PILImage
            upscaled = _PILImage.fromarray(out_np)
            # Crop/resize to exact target if needed
            if upscaled.size != (target_w, target_h):
                upscaled = upscaled.resize((target_w, target_h), _PILImage.LANCZOS)
            log.debug(f"[Upscale] {model_name}: {img.size} → {upscaled.size}")
            return upscaled
        except Exception as e:
            log.warning(f"[Upscale] {model_name} failed ({e}) — falling back to Lanczos")

    # Lanczos fallback — always available, decent quality
    try:
        from PIL import Image as _PILImage
        resized = img.resize((target_w, target_h), _PILImage.LANCZOS)
        log.debug(f"[Upscale] Lanczos: {img.size} → {resized.size}")
        return resized
    except Exception as e:
        log.warning(f"[Upscale] Lanczos failed ({e}) — returning original")
        return img


# ── REPLICATE ──────────────────────────────────────────────────────────────

def _replicate(prompts: list[str], out: Path, cfg: dict) -> list[Path]:
    try:
        import replicate
    except ImportError as e:
        raise ImportError("pip install replicate") from e

    model   = cfg.get("replicate_model", "stability-ai/stable-diffusion:db21e45d3f7023abc2a46ee38a23973f6dce16bb082a930b0c49861f96d1e5bf")
    images  = []
    with tqdm(total=len(prompts), desc="  Replicate", leave=False) as pbar:
        for i, prompt in enumerate(prompts):
            output = replicate.run(model, input={
                "prompt": prompt,
                "width":  cfg.get("width",  1024),
                "height": cfg.get("height", 576),
                "num_inference_steps": cfg.get("steps", 25),
                "guidance_scale":      cfg.get("guidance_scale", 7.5),
            })
            url = output[0] if isinstance(output, list) else output
            p   = out / f"scene_{i+1:02d}.png"
            with urllib.request.urlopen(url, timeout=30) as response, open(str(p), 'wb') as out_file:
                out_file.write(response.read())
            images.append(p)
            pbar.update(1)

    log.info(f"Replicate: {len(images)} images")
    return images


# ── PEXELS ─────────────────────────────────────────────────────────────────

def _pexels(prompts: list[str], out: Path, cfg: dict) -> list[Path]:
    api_key = cfg.get("pexels_api_key") or os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        raise ValueError("pexels_api_key missing from config or PEXELS_API_KEY env var")

    images = []
    with tqdm(total=len(prompts), desc="  Pexels", leave=False) as pbar:
        for i, prompt in enumerate(prompts):
            query   = urllib.parse.quote_plus(prompt[:100])
            url     = f"https://api.pexels.com/v1/search?query={query}&per_page=1&orientation=landscape"
            req     = urllib.request.Request(url, headers={"Authorization": api_key})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            photos = data.get("photos", [])
            if not photos:
                log.warning(f"Pexels: no results for prompt {i+1}, skipping")
                pbar.update(1)
                continue

            img_url = photos[0]["src"].get("large2x") or photos[0]["src"]["original"]
            p       = out / f"scene_{i+1:02d}.png"
            with urllib.request.urlopen(img_url, timeout=30) as response, open(str(p), 'wb') as out_file:
                out_file.write(response.read())
            images.append(p)
            pbar.update(1)

    log.info(f"Pexels: {len(images)} images")
    return images
