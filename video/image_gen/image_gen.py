"""image_gen.py - Image generation.

ComfyUI is the image backend (config image_gen.backend: comfyui).

Public surface:
- generate_images(prompts, output_dir, config, char_presence=None)
- get_oom_report(), clear_oom_events(), _record_oom_event()
- _prompt_cache_key()
- _maybe_upscale()
"""

import hashlib
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, cast

from tqdm import tqdm

log = logging.getLogger(__name__)

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





def _qwen_preflight_issues(cfg: dict) -> list[str]:
    """Return Qwen preflight issues for an image_gen config dict."""
    try:
        from video.image_gen.qwen_repose import preflight_qwen_edit

        return preflight_qwen_edit({"image_gen": cfg})
    except Exception as e:
        return [f"qwen_edit preflight raised: {e}"]


def _available_ram_gib() -> float | None:
    """Return currently available physical RAM using only the standard library."""
    try:
        if os.name == "nt":
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.ullAvailPhys / (1024**3)

        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return pages * page_size / (1024**3)
    except (AttributeError, OSError, ValueError):
        return None


def _free_vram_mib() -> int | None:
    """Return free VRAM for the first NVIDIA GPU, or None when unavailable."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return None
        first = result.stdout.strip().splitlines()[0]
        return int(first.strip())
    except (IndexError, OSError, subprocess.SubprocessError, ValueError):
        return None



def _qwen_resource_issues(cfg: dict) -> list[str]:
    """Reject Qwen before allocation when this machine lacks safe headroom."""
    qwen_cfg = cfg.get("qwen_edit", {}) or {}
    min_ram = float(qwen_cfg.get("min_available_ram_gib", 8.0))
    min_vram = int(qwen_cfg.get("min_free_vram_mib", 5000))
    issues: list[str] = []

    available_ram = _available_ram_gib()
    if available_ram is None:
        issues.append("available RAM could not be measured")
    elif available_ram < min_ram:
        issues.append(f"available RAM {available_ram:.2f} GiB is below {min_ram:.2f} GiB")

    free_vram = _free_vram_mib()
    if free_vram is None:
        issues.append("free NVIDIA VRAM could not be measured")
    elif free_vram < min_vram:
        issues.append(f"free VRAM {free_vram} MiB is below {min_vram} MiB")

    return issues


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
                 (prompts_str, neg_prompt_override).
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

    backend = cfg.get("backend", "comfyui")
    composition_mode = cfg.get("composition_mode", "one_pass")

    if backend == "comfyui" and composition_mode == "qwen_edit":
        qwen_cfg = cfg.get("qwen_edit", {}) or {}
        qwen_trigger = qwen_cfg.get("trigger", "any_character")
        if qwen_cfg.get("enabled", False) and qwen_trigger != "disabled":
            qwen_issues = _qwen_preflight_issues(cfg) + _qwen_resource_issues(cfg)
            if qwen_issues:
                log.warning(
                    "[image_gen] qwen_edit preflight failed; using one_pass ComfyUI. Issues: %s",
                    "; ".join(qwen_issues),
                )
            else:
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
                    raise
        else:
            log.info(
                "[image_gen] qwen_edit mode configured but inactive "
                "(enabled=%s, trigger=%s); using one_pass",
                qwen_cfg.get("enabled", False),
                qwen_trigger,
            )

    if backend == "comfyui":
        try:
            return _comfyui(
                prompt_list,
                output_dir,
                cfg,
            )
        except Exception as e:
            log.warning(f"[image_gen] ComfyUI failed: {e}")
            raise
    raise ValueError(f"Unsupported image backend: {backend}")


# ── CACHE HELPERS ────────────────────────────


def _master_portrait_hash_for_frame(char_key: str | None, ps=None) -> str:
    """Look up the master portrait content hash for a character.

    Returns the hash if the project store has one, else ''. Used in the
    per-frame cache key so portrait regeneration invalidates stale PNGs.
    """
    if not char_key:
        return ""
    try:
        if ps is None:
            from memory.project_store import ProjectStore
            ps = ProjectStore(_current_project_id or "_default")
        return ps.get_master_portrait_hash(char_key)
    except Exception:
        return ""


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
    """Return an 8-char hex MD5 hash of prompt + generation parameters."""
    if isinstance(prompt, list):
        prompt = ";".join([str(p) for p in prompt])
    elif not isinstance(prompt, str):
        prompt = str(prompt)
    steps = cfg.get("steps", 4)
    width = cfg.get("width", 1024)
    height = cfg.get("height", 1024)
    guidance_scale = cfg.get("guidance_scale", 3.5)
    model_id = cfg.get("sd_model_path") or "comfyui"
    effective_steps = throttled_steps if throttled_steps is not None else steps
    raw = (
        f"{prompt}|steps={effective_steps}|w={width}|h={height}"
        f"|gs={guidance_scale}|neg={neg_prompt}|lora={lora_state}|model={model_id}"
        f"|seed={seed}|lora_fp={lora_fingerprint}|mp_hash={master_portrait_hash}"
    )
    return hashlib.md5(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]






# ── UPSCALER ──────────────────────────


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
            resampling = cast(Any, getattr(_PILImage, "Resampling", _PILImage))
            if upscaled.size != (target_w, target_h):
                upscaled = upscaled.resize((target_w, target_h), resampling.LANCZOS)
            log.debug(f"[Upscale] {model_name}: {img.size} → {upscaled.size}")
            return upscaled
        except Exception as e:
            log.warning(f"[Upscale] {model_name} failed ({e}) — falling back to Lanczos")

    # Lanczos fallback
    try:
        from PIL import Image as _PILImage

        resampling = cast(Any, getattr(_PILImage, "Resampling", _PILImage))
        resized = img.resize((target_w, target_h), resampling.LANCZOS)
        log.debug(f"[Upscale] Lanczos: {img.size} → {resized.size}")
        return resized
    except Exception as e:
        log.warning(f"[Upscale] Lanczos failed ({e}) — returning original")
        return img


# ── DOMINANT CHARACTER RESOLUTION ────────────────────────────


def _resolve_dominant_char_at_threshold(
    char_presence: dict | None,
    threshold: float,
) -> tuple[str | None, float]:
    """Return dominant character using a caller-provided presence threshold."""
    if not char_presence:
        return None, 0.0
    if not isinstance(char_presence, dict) or not char_presence:
        return None, 0.0
    best_key = max(char_presence, key=lambda k: cast(float, char_presence.get(k, 0.0)))
    best_weight = float(char_presence[best_key])
    if best_weight < threshold:
        return None, 0.0
    return best_key, best_weight


# ── COMFYUI ───────────────────────────


def _comfyui_seed(cfg: dict, prompt: str, frame_index: int) -> int | None:
    """Resolve a deterministic ComfyUI seed for one frame.

    Priority:
    1. An explicit non-negative ``image_gen.seed`` is used as a reproducible
       base, offset per frame so frames differ while the whole run repeats.
    2. Otherwise, when ``lock_seed`` is true, derive a stable seed from the
       prompt and frame index via md5 — never Python's salted ``hash()``,
       which changes between processes.
    3. Otherwise return ``None`` so the workflow layer picks a fresh random
       seed (legacy, non-reproducible behavior).
    """
    explicit = cfg.get("seed", -1)
    try:
        explicit = int(explicit)
    except (TypeError, ValueError):
        explicit = -1
    if explicit >= 0:
        return (explicit + frame_index * 7919) % (2**32)
    if cfg.get("lock_seed", True):
        raw = f"comfyui|{prompt[:120]}|frame={frame_index}"
        return int(hashlib.md5(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:8], 16) % (2**32)
    return None


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
            seed = _comfyui_seed(cfg, prompt, i)
            if patcher:
                patcher.patch_all(
                    prompt=prompt,
                    negative_prompt=neg_prompt,
                    seed=seed,
                    width=width,
                    height=height,
                    steps=steps,
                    cfg=cfg_scale,
                    sampler_name=sampler,
                    scheduler=scheduler,
                    checkpoint=checkpoint,
                    filename_prefix=filename_prefix,
                )
                loras = comfy_cfg.get("loras")
                if loras:
                    patcher.patch_lora(loras)
                vae_name = comfy_cfg.get("vae_name")
                if vae_name:
                    patcher.patch_vae(vae_name)
                reference_image = comfy_cfg.get("reference_image")
                if reference_image:
                    uploaded = client.upload_image(Path(reference_image))
                    patcher.patch_reference_image(uploaded["name"])
                workflow = patcher.get_workflow()
            else:
                workflow = create_default_workflow(
                    prompt=prompt,
                    negative_prompt=neg_prompt,
                    seed=seed,
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
    images = _refine_upscale(images, cfg)

    if comfy_cfg.get("unload_after_batch", False):
        log.info("[ComfyUI] Unloading after batch (VRAM release)")
        try:
            client.free_memory()
        except Exception as e:
            # cleanup is best-effort; a failed /free request must not
            # discard images that ComfyUI already generated successfully.
            log.warning(f"[ComfyUI] Could not unload after batch: {e}")

    return images


def _refine_upscale(frames: list[Path], cfg: dict) -> list[Path]:
    """Standalone FaceDetailer + tiled-upscale pass, one frame at a time."""
    comfy_cfg = cfg.get("comfyui", {}) or {}
    if not comfy_cfg.get("refine_upscale", False):
        return frames

    refine_path = comfy_cfg.get(
        "refine_workflow_path",
        "config/comfyui/workflows/manga_refine_upscale_api.json",
    )
    if not Path(refine_path).is_file():
        log.warning("[refine] workflow missing: %s; skipping refine pass", refine_path)
        return frames

    from video.image_gen.comfyui_client import ComfyUIClient
    from video.image_gen.comfyui_runtime import get_comfyui_runtime
    from video.image_gen.comfyui_workflow import WorkflowPatcher

    runtime = get_comfyui_runtime({"comfyui": comfy_cfg})
    if not runtime.ensure_running(timeout=comfy_cfg.get("auto_start_timeout", 60)):
        log.warning("[refine] ComfyUI not running; skipping refine pass")
        return frames
    client = ComfyUIClient(base_url=runtime.base_url, timeout=comfy_cfg.get("timeout_seconds", 300))

    try:
        client.free_memory()
    except Exception as e:
        log.debug("[refine] free_memory failed (non-fatal): %s", e)

    final_frames: list[Path] = []
    with tqdm(total=len(frames), desc=" Refine+Upscale", leave=False) as pbar:
        for i, frame in enumerate(frames):
            frame = Path(frame)
            try:
                uploaded = client.upload_image(frame, overwrite=True)
                patcher = WorkflowPatcher(Path(refine_path))
                wf = patcher.get_workflow()
                wf["1"]["inputs"]["image"] = uploaded["name"]
                wf["11"]["inputs"]["filename_prefix"] = f"{frame.stem}_final"
                out = client.generate_image(
                    wf,
                    frame.parent,
                    filename_prefix=f"{frame.stem}_final",
                    poll_interval=comfy_cfg.get("poll_seconds", 1.0),
                    timeout=comfy_cfg.get("timeout_seconds", 300),
                )
                final_frames.append(out[0] if out else frame)
            except Exception as e:
                log.warning("[refine] frame %d (%s) failed: %s; keeping original", i, frame, e)
                final_frames.append(frame)
            pbar.update(1)

    log.info("[refine] Completed FaceDetailer + upscale on %d frames", len(final_frames))
    return final_frames


def _qwen_seed(char_key: str, frame_index: int, prompt: str) -> int:
    raw = f"qwen_edit|{char_key}|{frame_index}|{prompt[:80]}"
    return int(hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()[:8], 16) % (2**32)


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


def _log_qwen_degradation(frame_index: int, reason: str) -> None:
    """Record a per-frame Qwen edit fallback so the run reports the truth.

    A degradation means the frame kept its character-blind background because
    the saved character could not be composited. Recording it must never abort
    the batch, so any failure to record is swallowed.
    """
    try:
        from agents.ui_state import UIState

        UIState.add_degradation(
            frame_index,
            "qwen_edit_fallback",
            reason or "qwen edit did not composite the character",
        )
    except Exception as e:
        log.debug("[qwen_edit] Could not record degradation for frame %d: %s", frame_index, e)


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

    Outcomes are reported truthfully: a frame is counted as composited only when
    a real image was written. Per-frame failures keep that frame's background
    and record a qwen_edit_fallback degradation; they never reroute the whole
    batch to Bonsai.
    """
    qwen_cfg = cfg.get("qwen_edit", {}) or {}
    threshold = float(qwen_cfg.get("character_threshold", 0.05))

    log.info("[qwen_edit] Pass 1/2: generating full backgrounds")
    images = _comfyui(prompts, out, cfg)
    if not images:
        return images

    _free_comfyui_memory(cfg)

    resource_issues = _qwen_resource_issues(cfg)
    if resource_issues:
        reason = "resource gate after background pass: " + "; ".join(resource_issues)
        log.warning("[qwen_edit] %s; keeping all background frames", reason)
        for i, cp in enumerate(char_presence or []):
            if isinstance(cp, dict) and _resolve_dominant_char_at_threshold(cp, threshold)[0]:
                _log_qwen_degradation(i, reason)
        return [Path(image) for image in images]

    from video.image_gen.qwen_repose import repose_character_detailed

    edited_paths: list[Path] = []
    attempted = composited = skipped = failed = 0

    with tqdm(total=len(images), desc="  Qwen edit", leave=False) as pbar:
        for i, image_path in enumerate(images):
            cp = {}
            if isinstance(char_presence, list) and i < len(char_presence):
                val = char_presence[i]
                if isinstance(val, dict):
                    cp = val
            dom_char, _dom_weight = _resolve_dominant_char_at_threshold(cp, threshold)
            if not dom_char:
                # No character expected in this frame; keep the background as-is.
                edited_paths.append(Path(image_path))
                pbar.update(1)
                continue

            attempted += 1
            prompt = prompts[i] if i < len(prompts) else ""
            seed = _qwen_seed(dom_char, i, prompt)
            result = repose_character_detailed(
                str(image_path),
                dom_char,
                prompt,
                str(image_path),
                config={"image_gen": cfg},
                project_id=project_id,
                seed=seed,
            )
            if result.composited:
                composited += 1
            elif result.status == "failed":
                failed += 1
                _log_qwen_degradation(i, result.reason)
            else:  # skipped: a character was expected but could not be staged
                skipped += 1
                _log_qwen_degradation(i, result.reason)
            edited_paths.append(Path(result.output_path))
            pbar.update(1)

    log.info(
        "[qwen_edit] Pass 2/2 complete: %d attempted, %d composited, %d skipped, %d failed",
        attempted,
        composited,
        skipped,
        failed,
    )
    print(
        f"[image_gen] Qwen edit summary: {len(images)} frames | "
        f"{attempted} with character | {composited} composited | "
        f"{skipped} skipped | {failed} failed"
    )
    return edited_paths
