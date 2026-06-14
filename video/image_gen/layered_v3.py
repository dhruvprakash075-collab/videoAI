"""layered_v3.py - Multi-pass layered image generation.

Generates images in passes: background → character → composite → refine.
Keeps character identity stable across frames while allowing cinematic backgrounds.

Public surface:
- generate_layered_images(prompts, output_dir, config, char_presence, project_id)
- preflight_layered_v3(config) → list of error strings (empty = pass)
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tqdm import tqdm

if TYPE_CHECKING:
    from video.image_gen.comfyui_client import ComfyUIClient

log = logging.getLogger(__name__)


def _resolve_dominant_char(
    char_presence: dict | None, threshold: float = 0.3
) -> tuple[str | None, float]:
    """Return (char_key, weight) of the dominant character, or (None, 0.0)."""
    if not char_presence or not isinstance(char_presence, dict) or not char_presence:
        return None, 0.0
    best_key = max(char_presence, key=char_presence.get)
    best_weight = float(char_presence.get(best_key, 0.0))
    if best_weight < threshold:
        return None, 0.0
    return best_key, best_weight


def preflight_layered_v3(config: dict) -> list[str]:
    """Validate layered_v3 configuration and return list of error messages.

    Returns an empty list if preflight passes. Non-empty list means preflight failed.
    Each string is a human-readable error message suitable for display.
    """
    errors: list[str] = []
    img = config.get("image_gen", {}) or {}
    composition_mode = img.get("composition_mode", "one_pass")
    if composition_mode != "layered_v3":
        return errors

    comfy_cfg = img.get("comfyui", {}) or {}
    lv3 = img.get("layered_v3", {}) or {}
    workflows = lv3.get("workflows", {}) or {}

    # Check ComfyUI reachable
    host = comfy_cfg.get("host", "127.0.0.1")
    port = comfy_cfg.get("port", 8188)
    try:
        import urllib.error
        import urllib.request

        with urllib.request.urlopen(f"http://{host}:{port}/system_stats", timeout=5) as resp:
            if resp.status >= 400:
                errors.append(f"ComfyUI returned HTTP {resp.status}")
    except (urllib.error.URLError, TimeoutError):
        errors.append(f"ComfyUI not reachable at http://{host}:{port}/")
    except Exception as e:
        errors.append(f"ComfyUI probe failed: {e}")

    # Check workflow files exist
    wf_map = {
        "character_sheet": workflows.get("character_sheet", ""),
        "background": workflows.get("background", ""),
        "character_pose": workflows.get("character_pose", ""),
        "composite_refine": workflows.get("composite_refine", ""),
    }
    for name, path in wf_map.items():
        if path and not Path(path).exists():
            errors.append(f"workflow file not found [{name}]: {path}")
        elif not path:
            errors.append(
                f"workflow path not set [{name}] — set in config.yaml: image_gen.layered_v3.workflows.{name}"
            )

    # Check custom nodes
    comfy_root = Path(comfy_cfg.get("root", ""))
    if not comfy_root.exists():
        # Fall back to relative path for portability
        comfy_root = Path("external") / "ComfyUI"
    required_nodes = {
        "IPAdapter Plus": comfy_root / "custom_nodes" / "ComfyUI_IPAdapter_plus",
        "Impact Pack": comfy_root / "custom_nodes" / "ComfyUI-Impact-Pack",
        "ControlNet Aux": comfy_root / "custom_nodes" / "comfyui_controlnet_aux",
    }
    for name, node_path in required_nodes.items():
        if not node_path.exists():
            errors.append(
                f"missing ComfyUI custom node '{name}' at {node_path}. "
                f"See docs/layered_v3_setup.md for installation."
            )

    # Check IPAdapter models
    ipadapter_dir = comfy_root / "models" / "ipadapter"
    for model_name in ["ip-adapter-plus_sd15.bin", "ip-adapter-plus-fullface_sd15.bin"]:
        model_path = ipadapter_dir / model_name
        if not model_path.exists():
            errors.append(
                f"missing IPAdapter model '{model_name}' at {model_path}. "
                f"See docs/layered_v3_setup.md."
            )

    return errors


def _run_workflow(
    client: ComfyUIClient,
    workflow_path: str,
    output_dir: Path,
    filename_prefix: str,
    config: dict,
    extra_prompt_vars: dict[str, Any] | None = None,
) -> list[Path]:
    """Load a workflow JSON and run it via ComfyUIClient. Returns output image paths."""
    import json

    with open(workflow_path, encoding="utf-8") as f:
        workflow = json.load(f)

    # Apply any extra variable substitutions (e.g., input_image, checkpoint, etc.)
    if extra_prompt_vars:
        workflow_str = json.dumps(workflow)
        for key, value in extra_prompt_vars.items():
            placeholder = f"${{{key}}}"
            if placeholder in workflow_str:
                workflow_str = workflow_str.replace(placeholder, str(value))
            elif isinstance(value, str):
                workflow_str = workflow_str.replace(placeholder, value)
        workflow = json.loads(workflow_str)

    return client.generate_image(
        workflow,
        output_dir,
        filename_prefix=filename_prefix,
        poll_interval=config.get("comfyui", {}).get("poll_seconds", 1.0),
        timeout=config.get("comfyui", {}).get("timeout_seconds", 300),
    )


def _resolve_char_assets(project_id: str, char_key: str) -> dict[str, str]:
    """Load character assets from ProjectStore. Returns dict of asset paths."""
    try:
        from memory.project_store import ProjectStore

        ps = ProjectStore(project_id)
        return ps.get_character_assets(char_key)
    except Exception as e:
        log.warning(f"[layered_v3] Could not load character assets for '{char_key}': {e}")
        return {}


def _compute_identity_hash(char_key: str, project_id: str, approved_assets: dict) -> str:
    """Compute identity hash from approved character assets for cache invalidation."""
    files_to_hash = [
        approved_assets.get("character_sheet_path", ""),
        approved_assets.get("face_reference_path", ""),
        approved_assets.get("full_body_reference_path", ""),
    ]
    hasher = hashlib.md5()
    for fpath in files_to_hash:
        if fpath and Path(fpath).exists():
            with contextlib.suppress(OSError):
                hasher.update(Path(fpath).read_bytes())
    return hasher.hexdigest()[:16]


def _resolve_char_prompt(project_id: str, char_key: str, base_prompt: str) -> str:
    """Augment a prompt with character visual description from ProjectStore."""
    try:
        from memory.project_store import ProjectStore

        ps = ProjectStore(project_id)
        entry = ps.get_character(char_key) or {}
        desc = entry.get("visual_description", "")
        if desc:
            return f"{desc}, {base_prompt}"
    except Exception:
        pass
    return base_prompt


def generate_layered_images(
    prompts: list[str],
    output_dir: Path,
    config: dict,
    char_presence: list[dict[str, float]] | None = None,
    project_id: str = "",
) -> list[Path]:
    """Generate images using multi-pass layered composition.

    Args:
        prompts: Either a semicolon-separated string or a list of prompt strings.
        output_dir: Directory to save generated PNG images (scene_01.png, etc.).
        config: Full pipeline config dict.
        char_presence: Optional list of per-frame character weight dicts.
        project_id: Project name for character asset lookup.

    Returns:
        List of output PNG paths, one per prompt.
    """
    from video.image_gen.comfyui_client import ComfyUIClient
    from video.image_gen.comfyui_runtime import get_comfyui_runtime

    output_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(prompts, str):
        prompt_list = [p.strip() for p in prompts.split(";") if p.strip()]
    else:
        prompt_list = [str(p).strip() for p in prompts if str(p).strip()]

    img = config.get("image_gen", {})
    comfy_cfg = img.get("comfyui", {}) or {}
    lv3_cfg = img.get("layered_v3", {}) or {}
    workflows = lv3_cfg.get("workflows", {}) or {}

    background_wf = workflows.get("background", "")
    character_pose_wf = workflows.get("character_pose", "")
    composite_refine_wf = workflows.get("composite_refine", "")

    char_threshold = float(lv3_cfg.get("character_threshold", 0.3))
    fallback_mode = lv3_cfg.get("fallback_mode", "one_pass")

    # Check preflight
    errors = preflight_layered_v3(config)
    if errors:
        if fallback_mode == "one_pass":
            log.warning(
                f"[layered_v3] Preflight failed ({len(errors)} errors); falling back to one_pass. "
                "Set fallback_mode to 'error' to block instead."
            )
            # Import and delegate to one-pass
            from video.image_gen.comfyui_runtime import get_comfyui_runtime

            runtime = get_comfyui_runtime({"comfyui": comfy_cfg})
            client = ComfyUIClient(
                base_url=runtime.base_url, timeout=comfy_cfg.get("timeout_seconds", 300)
            )
            return _one_pass_fallback(prompt_list, output_dir, config, client)
        else:
            error_msgs = "\n".join(f"  - {e}" for e in errors)
            raise RuntimeError(
                f"[layered_v3] Preflight failed ({len(errors)} errors):\n{error_msgs}\n"
                "Fix the issues above or set image_gen.layered_v3.fallback_mode: 'one_pass' in config.yaml."
            )

    runtime = get_comfyui_runtime({"comfyui": comfy_cfg})
    if not runtime.ensure_running(timeout=comfy_cfg.get("auto_start_timeout", 60)):
        if fallback_mode == "one_pass":
            log.warning("[layered_v3] ComfyUI not running; falling back to one_pass")
            client = ComfyUIClient(
                base_url=runtime.base_url, timeout=comfy_cfg.get("timeout_seconds", 300)
            )
            return _one_pass_fallback(prompt_list, output_dir, config, client)
        raise RuntimeError(
            f"[layered_v3] ComfyUI not running at {runtime.base_url} and auto_start is disabled. "
            "Set image_gen.layered_v3.fallback_mode: 'one_pass' to fall back gracefully."
        )

    client = ComfyUIClient(base_url=runtime.base_url, timeout=comfy_cfg.get("timeout_seconds", 300))

    images: list[Path] = []

    with tqdm(total=len(prompt_list), desc="  Layered v3", leave=False) as pbar:
        for i, prompt in enumerate(prompt_list):
            idx = i + 1
            filename_prefix = f"scene_{idx:02d}"

            # Determine frame type based on char_presence
            cp = {}
            if isinstance(char_presence, list) and i < len(char_presence):
                val = char_presence[i]
                if isinstance(val, dict):
                    cp = val

            dom_char, _ = _resolve_dominant_char(cp, threshold=char_threshold)

            if dom_char is None:
                # Background-only frame
                if not background_wf:
                    log.warning(
                        f"[layered_v3] No background workflow configured; skipping frame {idx}"
                    )
                    pbar.update(1)
                    continue

                try:
                    output = _run_workflow(
                        client,
                        background_wf,
                        output_dir,
                        filename_prefix=f"{filename_prefix}_bg",
                        config=config,
                        extra_prompt_vars={"prompt": prompt},
                    )
                    if output:
                        final = output_dir / f"scene_{idx:02d}.png"
                        shutil.copy2(output[0], final)
                        images.append(final)
                except Exception as e:
                    log.warning(f"[layered_v3] Background workflow failed for frame {idx}: {e}")
                    pbar.update(1)
                    continue
            else:
                # Character frame: bg → character → composite
                char_assets = _resolve_char_assets(project_id, dom_char)
                char_prompt = _resolve_char_prompt(project_id, dom_char, prompt)

                # Determine temp directory for intermediate images
                with tempfile.TemporaryDirectory(prefix="layered_") as tmpdir:
                    tmp_path = Path(tmpdir)

                    # Pass 1: Background
                    bg_output: list[Path] = []
                    if background_wf:
                        try:
                            bg_output = _run_workflow(
                                client,
                                background_wf,
                                tmp_path,
                                filename_prefix=f"{filename_prefix}_bg",
                                config=config,
                                extra_prompt_vars={"prompt": prompt},
                            )
                        except Exception as e:
                            log.warning(f"[layered_v3] Background pass failed for frame {idx}: {e}")
                            pbar.update(1)
                            continue
                    else:
                        log.warning(
                            f"[layered_v3] No background workflow configured; skipping frame {idx}"
                        )
                        pbar.update(1)
                        continue

                    if not bg_output:
                        log.warning(f"[layered_v3] No background output for frame {idx}")
                        pbar.update(1)
                        continue

                    bg_image = bg_output[0]

                    # Pass 2: Character in pose (using identity ref)
                    char_output: list[Path] = []
                    if character_pose_wf:
                        try:
                            char_output = _run_workflow(
                                client,
                                character_pose_wf,
                                tmp_path,
                                filename_prefix=f"{filename_prefix}_char",
                                config=config,
                                extra_prompt_vars={
                                    "prompt": char_prompt,
                                    "input_image": str(bg_image),
                                    "face_ref": char_assets.get("face_reference_path", ""),
                                    "body_ref": char_assets.get("full_body_reference_path", ""),
                                    "seed": str(_frame_seed(dom_char, i)),
                                },
                            )
                        except Exception as e:
                            log.warning(f"[layered_v3] Character pass failed for frame {idx}: {e}")
                            pbar.update(1)
                            continue
                    else:
                        log.warning(
                            "[layered_v3] No character_pose workflow configured; using background only"
                        )
                        final = output_dir / f"scene_{idx:02d}.png"
                        shutil.copy2(bg_image, final)
                        images.append(final)
                        pbar.update(1)
                        continue

                    if not char_output:
                        log.warning(
                            f"[layered_v3] No character output for frame {idx}; using background"
                        )
                        final = output_dir / f"scene_{idx:02d}.png"
                        shutil.copy2(bg_image, final)
                        images.append(final)
                        pbar.update(1)
                        continue

                    char_image = char_output[0]

                    # Pass 3: Composite + refine
                    final_output: list[Path] = []
                    if composite_refine_wf:
                        try:
                            final_output = _run_workflow(
                                client,
                                composite_refine_wf,
                                tmp_path,
                                filename_prefix=filename_prefix,
                                config=config,
                                extra_prompt_vars={
                                    "background": str(bg_image),
                                    "character": str(char_image),
                                },
                            )
                        except Exception as e:
                            log.warning(
                                f"[layered_v3] Composite pass failed for frame {idx}; using background+character without composite: {e}"
                            )
                            # Fall through: use char as final if composite fails
                            final_output = []
                    else:
                        log.warning(
                            "[layered_v3] No composite_refine workflow; using background+character blend"
                        )

                    if final_output:
                        final = output_dir / f"scene_{idx:02d}.png"
                        shutil.copy2(final_output[0], final)
                        images.append(final)
                    else:
                        # Use the better of bg+char (prefer char as final)
                        final = output_dir / f"scene_{idx:02d}.png"
                        shutil.copy2(char_image, final)
                        images.append(final)

            pbar.update(1)

    log.info(f"[layered_v3] {len(images)}/{len(prompt_list)} images generated")
    return images


def _frame_seed(char_key: str, frame_idx: int) -> int:
    """Derive a stable per-frame seed for a character."""
    seed_str = f"{char_key}_{frame_idx}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16) % (2**32)
    return seed


def _one_pass_fallback(
    prompts: list[str],
    output_dir: Path,
    config: dict,
    client: ComfyUIClient,
) -> list[Path]:
    """Fallback to existing one-pass ComfyUI generation."""
    from video.image_gen.comfyui_workflow import WorkflowPatcher, create_default_workflow

    img = config.get("image_gen", {})
    comfy_cfg = img.get("comfyui", {})
    workflow_path = comfy_cfg.get("workflow_path")

    width = comfy_cfg.get("width", img.get("width", 1024))
    height = comfy_cfg.get("height", img.get("height", 1024))
    steps = comfy_cfg.get("steps", img.get("steps", 20))
    cfg_scale = comfy_cfg.get("cfg", img.get("guidance_scale", 7.0))
    sampler = comfy_cfg.get("sampler_name", "euler")
    scheduler = comfy_cfg.get("scheduler", "normal")
    checkpoint = comfy_cfg.get("checkpoint", "")
    neg_prompt = comfy_cfg.get("negative_prompt", "")
    poll = comfy_cfg.get("poll_seconds", 1.0)
    timeout = comfy_cfg.get("timeout_seconds", 300)

    patcher = WorkflowPatcher(Path(workflow_path)) if workflow_path else None

    images: list[Path] = []
    with tqdm(total=len(prompts), desc="  ComfyUI (fallback)", leave=False) as pbar:
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
                output_dir,
                filename_prefix=filename_prefix,
                poll_interval=poll,
                timeout=timeout,
            )
            images.extend(output_images)
            pbar.update(1)

    return images
