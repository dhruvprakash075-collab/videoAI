"""image_gen.py - Image generation.

ComfyUI is the image backend (config image_gen.backend: comfyui).

Public surface:
- generate_images(prompts, output_dir, config, char_presence=None)
- get_oom_report(), clear_oom_events(), _record_oom_event()
- _prompt_cache_key()
- _maybe_upscale()
"""

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, cast

from tqdm import tqdm

log = logging.getLogger(__name__)

# Module-level OOM event list — shared between portrait gen + frame gen.
_oom_events: list = []
_oom_events_lock = threading.Lock()
_REFERENCE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


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


def _reference_image_for(comfy_cfg: dict, prompt: str, index: int) -> Path | None:
    if comfy_cfg.get("reference_usage", "style_inspiration") != "direct":
        return None
    images = [Path(p) for p in comfy_cfg.get("reference_images", []) if str(p).strip()]
    ref_dir = comfy_cfg.get("reference_image_dir")
    if ref_dir:
        root = Path(ref_dir)
        if root.is_dir():
            images.extend(
                p for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in _REFERENCE_IMAGE_EXTS
            )
    if not images and comfy_cfg.get("reference_image"):
        images = [Path(comfy_cfg["reference_image"])]
    images = [p for p in images if p.is_file()]
    if not images:
        return None
    if comfy_cfg.get("reference_seed_mode", "prompt_hash") == "round_robin":
        return images[index % len(images)]
    key = f"{prompt}|{index}".encode("utf-8", errors="ignore")
    return images[int(hashlib.sha256(key).hexdigest(), 16) % len(images)]


def _reference_pool(comfy_cfg: dict) -> list[Path]:
    images = [Path(p) for p in comfy_cfg.get("reference_images", []) if str(p).strip()]
    ref_dir = comfy_cfg.get("reference_image_dir")
    if ref_dir:
        root = Path(ref_dir)
        if root.is_dir():
            images.extend(
                p for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in _REFERENCE_IMAGE_EXTS
            )
    return [p for p in images if p.is_file()]


def _face_inspiration_prompt(cfg: dict, prompt: str, index: int) -> str:
    face_cfg = cfg.get("face_inspiration", {}) or {}
    if not face_cfg.get("enabled", False):
        return ""
    path = Path(face_cfg.get("prompt_bank", "config/anime_face_inspiration.json"))
    if not path.is_file():
        return ""
    try:
        bank = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    phrases = [str(item).strip() for item in bank if str(item).strip()]
    if not phrases:
        return ""
    count = max(1, min(int(face_cfg.get("phrases_per_prompt", 3)), len(phrases)))
    seed = int(hashlib.sha256(f"{prompt}|{index}".encode()).hexdigest(), 16)
    selected = [phrases[(seed + offset) % len(phrases)] for offset in range(count)]
    return ", ".join(selected)


def _stable_character_reference(comfy_cfg: dict, char_key: str, project_id: str | None) -> Path | None:
    if comfy_cfg.get("reference_usage", "style_inspiration") != "direct":
        return None
    if not char_key or not project_id:
        return None
    try:
        from memory.project_store import ProjectStore

        store = ProjectStore(project_id)
        existing = store.get_master_portrait_path(char_key)
        if existing and Path(existing).is_file():
            return Path(existing)
        pool = _reference_pool(comfy_cfg)
        if not pool:
            return None
        idx = int(hashlib.sha256(char_key.encode("utf-8")).hexdigest(), 16) % len(pool)
        ref = pool[idx]
        digest = hashlib.sha256(ref.read_bytes()).hexdigest()
        store.log_character(char_key, f"Anime face reference seeded from {ref.name}")
        store.set_master_portrait(char_key, str(ref), digest)
        return ref
    except Exception as exc:
        log.debug("[image_gen] character reference memory skipped: %s", exc)
        return None


def _dominant_char_for_frame(char_presence: list[dict[str, float]] | None, index: int) -> str | None:
    if not char_presence or index >= len(char_presence):
        return None
    char_key, _weight = _resolve_dominant_char_at_threshold(char_presence[index], 0.3)
    return char_key


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
    if backend == "comfyui":
        try:
            return _comfyui(
                prompt_list,
                output_dir,
                cfg,
                char_presence=char_presence,
                project_id=project_id,
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


def _comfyui(
    prompts: list[str],
    out: Path,
    cfg: dict,
    *,
    char_presence: list[dict[str, float]] | None = None,
    project_id: str | None = None,
) -> list[Path]:
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
            inspiration = _face_inspiration_prompt(cfg, prompt, i)
            if inspiration:
                prompt = f"{prompt}, {inspiration}"
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
                char_key = _dominant_char_for_frame(char_presence, i)
                reference_image = _stable_character_reference(comfy_cfg, char_key or "", project_id)
                if reference_image is None:
                    reference_image = _reference_image_for(comfy_cfg, prompt, i)
                if reference_image:
                    uploaded = client.upload_image(reference_image)
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
    panel_cfg = cfg.get("panel_composite", {}) or {}
    if panel_cfg.get("enabled"):
        from video.image_gen.panel_compositor import compose_panel_pages

        images = compose_panel_pages(
            images,
            out,
            width=int(panel_cfg.get("width", 1920)),
            height=int(panel_cfg.get("height", 1080)),
            margin=int(panel_cfg.get("margin", 48)),
            gutter=int(panel_cfg.get("gutter", 24)),
            border=int(panel_cfg.get("border", 6)),
            layout_file=Path(panel_cfg.get("layout_file", "config/panel_layouts.json")),
            fallback_layout_file=Path(panel_cfg.get("fallback_layout_file", "config/panel_layouts.json")),
        )

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
