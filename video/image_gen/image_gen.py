"""image_gen.py - Image generation.

ComfyUI is the image backend (config image_gen.backend: comfyui).

Public surface:
- generate_images(prompts, output_dir, config, char_presence=None)
- get_oom_report(), clear_oom_events(), _record_oom_event()
"""

import hashlib
import json
import logging
import math
import threading
from pathlib import Path
from typing import cast

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
    # Storyboard sheet override: when the approved sheet is wired in by the
    # pipeline hook, it outranks the per-character master portrait so the whole
    # scene set shares the same approved visual reference. Deliberately NOT
    # gated by reference_usage — after the first run the stored sheet IS the
    # reference, per user decision.
    _sheet = comfy_cfg.get("storyboard_sheet")
    if _sheet and Path(_sheet).is_file():
        return Path(_sheet)
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

# SD1.5-safe latent sizes at ~393k px budget, one per panel aspect family.
# ponytail: fixed bucket set — extreme panel aspects (>2:1 or <1:2) snap to the
# nearest bucket; the compositor's blur fill still covers the residual gap.
_PANEL_SIZE_BUCKETS: tuple[tuple[float, int, int], ...] = (
    (1.5, 768, 512),
    (1.857, 832, 448),
    (1.0, 640, 640),
    (0.667, 512, 768),
    (0.538, 448, 832),
)


def _snap_to_bucket(aspect: float) -> tuple[int, int]:
    """Snap a panel aspect ratio to the nearest generation size bucket."""
    return min(_PANEL_SIZE_BUCKETS, key=lambda b: abs(math.log(aspect / b[0])))[1:]


def _panel_sizes(count: int, cfg: dict) -> list[tuple[int, int]] | None:
    """Per-prompt generation sizes matching the panel-page layout plan.

    Returns None when panel compositing is disabled (fixed-size legacy path).
    ponytail: assumes one output image per prompt — if a workflow ever emits
    multiple images per prompt, page/slot mapping drifts and sizes misalign.
    """
    panel_cfg = cfg.get("panel_composite", {}) or {}
    if not panel_cfg.get("enabled"):
        return None
    width = int(panel_cfg.get("width", 1920))
    height = int(panel_cfg.get("height", 1080))
    layout_file = Path(panel_cfg.get("layout_file", "config/panel_layouts.json"))
    fallback_file = Path(panel_cfg.get("fallback_layout_file", "config/panel_layouts.json"))

    from video.image_gen.panel_compositor import page_canvas_size, plan_page_counts, plan_page_rects

    page_w, page_h = page_canvas_size(
        width, height, int(panel_cfg.get("margin", 48)), float(panel_cfg.get("page_aspect", 1.414))
    )
    sizes: list[tuple[int, int]] = []
    for page_i, page_count in enumerate(plan_page_counts(count, layout_file, fallback_file)):
        rects = plan_page_rects(
            page_count,
            page_w,
            page_h,
            page_i,
            layout_file=layout_file,
            fallback_layout_file=fallback_file,
        )
        for slot in range(page_count):
            if slot < len(rects):
                x1, y1, x2, y2 = rects[slot]
                sizes.append(_snap_to_bucket((x2 - x1) / max(1, y2 - y1)))
            else:
                sizes.append((768, 512))
    return sizes


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
    panel_sizes = _panel_sizes(len(prompts), cfg)

    images: list[Path] = []

    with tqdm(total=len(prompts), desc="  ComfyUI", leave=False) as pbar:
        for i, prompt in enumerate(prompts):
            filename_prefix = f"scene_{i + 1:02d}"
            inspiration = _face_inspiration_prompt(cfg, prompt, i)
            if inspiration:
                prompt = f"{prompt}, {inspiration}"
            seed = _comfyui_seed(cfg, prompt, i)
            if panel_sizes:
                width_i, height_i = panel_sizes[i]
            else:
                width_i, height_i = width, height
            if patcher:
                patcher.patch_all(
                    prompt=prompt,
                    negative_prompt=neg_prompt,
                    seed=seed,
                    width=width_i,
                    height=height_i,
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
                    uploaded = client.upload_image(reference_image, overwrite=True)
                    patcher.patch_reference_image(uploaded["name"])
                workflow = patcher.get_workflow()
            else:
                workflow = create_default_workflow(
                    prompt=prompt,
                    negative_prompt=neg_prompt,
                    seed=seed,
                    width=width_i,
                    height=height_i,
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
    images = _refine_passes(images, cfg)
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
            page_aspect=float(panel_cfg.get("page_aspect", 1.414)),
            page_blur=bool(panel_cfg.get("page_blur", True)),
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


def _refine_passes(frames: list[Path], cfg: dict) -> list[Path]:
    """Run enabled refine passes (face detail, then upscale), one frame at a time."""
    comfy_cfg = cfg.get("comfyui", {}) or {}
    # ponytail: schema converts legacy refine_upscale -> both passes; fall back
    # here too so raw dicts (tests, older callers) keep working unvalidated.
    legacy = comfy_cfg.get("refine_upscale", False)
    passes = [
        (comfy_cfg.get("face_detail", legacy), "FaceDetailer",
         comfy_cfg.get("face_detail_workflow_path", "config/comfyui/workflows/manga_face_detail_api.json")),
        (comfy_cfg.get("upscale", legacy), "Upscale",
         comfy_cfg.get("upscale_workflow_path", "config/comfyui/workflows/manga_upscale_api.json")),
    ]
    enabled = [(name, path) for on, name, path in passes if on]
    if not enabled:
        return frames

    from video.image_gen.comfyui_client import ComfyUIClient
    from video.image_gen.comfyui_runtime import get_comfyui_runtime
    from video.image_gen.comfyui_workflow import WorkflowPatcher

    try:
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
        with tqdm(total=len(frames), desc=" Refine passes", leave=False) as pbar:
            for i, frame in enumerate(frames):
                frame = Path(frame)
                current = frame
                for pass_name, pass_path in enabled:
                    try:
                        uploaded = client.upload_image(current, overwrite=True)
                        patcher = WorkflowPatcher(Path(pass_path))
                        wf = patcher.get_workflow()
                        if wf.get("1", {}).get("class_type") != "LoadImage" or wf.get("11", {}).get("class_type") != "SaveImage":
                            raise ValueError(f"{pass_name} workflow drifted: expected LoadImage/SaveImage nodes 1/11, got {wf.get('1', {}).get('class_type')}/{wf.get('11', {}).get('class_type')}")
                        wf["1"]["inputs"]["image"] = uploaded["name"]
                        wf["11"]["inputs"]["filename_prefix"] = f"{current.stem}_final"
                        out = client.generate_image(
                            wf,
                            current.parent,
                            filename_prefix=f"{current.stem}_final",
                            poll_interval=comfy_cfg.get("poll_seconds", 1.0),
                            timeout=comfy_cfg.get("timeout_seconds", 300),
                        )
                        if out:
                            current = out[0]
                    except Exception as e:
                        log.warning("[refine] frame %d (%s) %s pass failed: %s; keeping previous", i, frame, pass_name, e)
                final_frames.append(current)
                pbar.update(1)

        log.info("[refine] Completed refine passes on %d frames", len(final_frames))
        return final_frames
    except Exception as e:
        log.warning("[refine] refine pass failed: %s; returning original frames", e)
        return frames
