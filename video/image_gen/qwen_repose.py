"""Qwen-Image-Edit two-pass character compositing helpers.

This module is deliberately Python orchestration glue: it resolves the saved
character reference, builds the edit instruction, patches a ComfyUI workflow,
and copies the result back to the existing frame path. The expensive work runs
inside ComfyUI/CUDA.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_WORKFLOW = "config/comfyui/workflows/qwen_image_edit_api.json"


def _image_gen_cfg(config: dict) -> dict:
    if "image_gen" in config and isinstance(config.get("image_gen"), dict):
        return config.get("image_gen", {}) or {}
    return config or {}


def _qwen_cfg(config: dict) -> dict:
    img = _image_gen_cfg(config)
    return img.get("qwen_edit", {}) or {}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_existing_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if path.exists():
        return path
    candidate = Path.cwd() / path
    if candidate.exists():
        return candidate
    return None


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return str(a) == str(b)


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if _same_file(src, dst):
        return
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    tmp.replace(dst)


def preflight_qwen_edit(config: dict) -> list[str]:
    """Return missing Qwen-Image-Edit prerequisites without contacting ComfyUI."""
    img = _image_gen_cfg(config)
    qwen = _qwen_cfg(config)
    comfy_cfg = img.get("comfyui", {}) or {}
    missing: list[str] = []

    workflow_path = qwen.get("workflow_path") or _DEFAULT_WORKFLOW
    if not Path(workflow_path).exists():
        missing.append(f"qwen_edit workflow file not found: {workflow_path}")

    model_path = qwen.get("model_path", "")
    if not model_path:
        missing.append("qwen_edit.model_path is empty")
    elif not Path(model_path).exists():
        missing.append(f"qwen_edit model_path not found: {model_path}")

    lightning_lora = qwen.get("lightning_lora", "")
    if lightning_lora and not Path(lightning_lora).exists():
        missing.append(f"qwen_edit lightning_lora not found: {lightning_lora}")

    comfy_root = Path(comfy_cfg.get("root", "external/ComfyUI"))
    for node_name in qwen.get("required_custom_nodes", []) or []:
        node_path = comfy_root / "custom_nodes" / str(node_name)
        if not node_path.exists():
            missing.append(f"missing ComfyUI custom node: {node_name} ({node_path})")

    return missing


def _load_project_store(project_id: str):
    from memory.project_store import ProjectStore

    return ProjectStore(project_id or "_default")


def _character_data(project_id: str, char_key: str) -> tuple[dict, dict]:
    ps = _load_project_store(project_id)
    try:
        character = ps.get_character(char_key) or {}
    except Exception as e:
        log.debug("[qwen_edit] Could not load character %s: %s", char_key, e)
        character = {}
    try:
        assets = ps.get_character_assets(char_key) or {}
    except Exception as e:
        log.debug("[qwen_edit] Could not load assets for %s: %s", char_key, e)
        assets = {}
    return character, assets


def _ensure_reference_image(project_id: str, char_key: str, config: dict) -> tuple[Path | None, str, dict]:
    """Resolve the best existing character reference, generating one if needed."""
    ps = _load_project_store(project_id)
    character, assets = _character_data(project_id, char_key)

    candidates = [
        getattr(ps, "get_master_portrait_path", lambda _k: "")(char_key),
        assets.get("face_reference_path"),
        assets.get("full_body_reference_path"),
        assets.get("character_sheet_path"),
    ]
    for candidate in candidates:
        path = _normalize_existing_path(candidate)
        if path:
            identity_hash = assets.get("identity_hash") or character.get("master_portrait_hash") or _sha256_file(path)
            return path, identity_hash, character

    try:
        from core.pre_production import generate_master_portrait

        log.info("[qwen_edit] No reference for %s — generating master portrait", char_key)
        generate_master_portrait(
            char_key=char_key,
            project_id=project_id or "_default",
            char_data=character or {"name": char_key},
            config={"image_gen": _image_gen_cfg(config)},
        )
    except Exception as e:
        log.warning("[qwen_edit] Could not generate master portrait for %s: %s", char_key, e)

    character, assets = _character_data(project_id, char_key)
    candidates = [
        getattr(ps, "get_master_portrait_path", lambda _k: "")(char_key),
        assets.get("face_reference_path"),
        assets.get("full_body_reference_path"),
        assets.get("character_sheet_path"),
    ]
    for candidate in candidates:
        path = _normalize_existing_path(candidate)
        if path:
            identity_hash = assets.get("identity_hash") or character.get("master_portrait_hash") or _sha256_file(path)
            return path, identity_hash, character

    return None, assets.get("identity_hash", ""), character


def build_qwen_edit_prompt(character: dict, scene_instruction: str) -> str:
    """Build the instruction that tells Qwen to paste the character into a full background."""
    desc = character.get("visual_description") or character.get("description") or character.get("name") or "the saved character"
    return (
        "Use the background image as a complete finished scene; do not expect an empty space. "
        "Insert the same saved character from the reference image into the background. "
        "Preserve identity, face, outfit, age, body shape, and overall style. "
        "Overpaint the background only where the character naturally covers it. "
        "Respect placement and depth from the director instruction: if the character is in front of an object, "
        "place the character in front and cover that part of the object; if behind an object, keep the object in front. "
        "Generate any object or prop the character is holding together with the character, not as part of the background. "
        f"Character description: {desc}. Director instruction: {scene_instruction}"
    )


def _cache_path(base_image_path: Path, identity_hash: str, edit_prompt: str, seed: int, config: dict) -> Path:
    qwen = _qwen_cfg(config)
    cache_dir = qwen.get("cache_dir") or ".qwen_edit_cache"
    cache_root = base_image_path.parent / cache_dir
    raw = "|".join(
        [
            identity_hash,
            _sha256_file(base_image_path) if base_image_path.exists() else str(base_image_path),
            edit_prompt,
            str(seed),
            str(qwen.get("backend", "nunchaku")),
            str(qwen.get("steps", 8)),
            str(qwen.get("denoise", 0.6)),
            str(qwen.get("model_path", "")),
            str(qwen.get("lightning_lora", "")),
        ]
    )
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return cache_root / f"{base_image_path.stem}_{key}.png"


def _replace_placeholders(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, str):
        result = value
        for key, replacement in replacements.items():
            result = result.replace(key, str(replacement))
        return result
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if isinstance(value, dict):
        return {k: _replace_placeholders(v, replacements) for k, v in value.items()}
    return value


def _patch_qwen_workflow(
    workflow_path: Path,
    *,
    base_image_path: Path,
    character_image_path: Path,
    edit_prompt: str,
    output_path: Path,
    seed: int,
    config: dict,
) -> dict:
    qwen = _qwen_cfg(config)
    with workflow_path.open(encoding="utf-8") as f:
        workflow = json.load(f)

    replacements = {
        "__BASE_IMAGE__": str(base_image_path),
        "__CHARACTER_IMAGE__": str(character_image_path),
        "__EDIT_PROMPT__": edit_prompt,
        "__FILENAME_PREFIX__": output_path.stem,
        "__MODEL_PATH__": qwen.get("model_path", ""),
        "__LIGHTNING_LORA__": qwen.get("lightning_lora", ""),
        "__SEED__": seed,
        "__STEPS__": int(qwen.get("steps", 8)),
        "__CFG__": float(qwen.get("cfg", 1.0)),
        "__DENOISE__": float(qwen.get("denoise", 0.6)),
    }
    patched = _replace_placeholders(copy.deepcopy(workflow), replacements)

    for node in patched.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {}) or {}
        title = (node.get("_meta", {}) or {}).get("title", "").lower()
        class_type = node.get("class_type", "")
        if "seed" in inputs:
            inputs["seed"] = seed
        if "steps" in inputs:
            inputs["steps"] = int(qwen.get("steps", inputs["steps"]))
        if "denoise" in inputs:
            inputs["denoise"] = float(qwen.get("denoise", inputs["denoise"]))
        if "filename_prefix" in inputs:
            inputs["filename_prefix"] = output_path.stem
        if "text" in inputs and ("prompt" in title or class_type.endswith("TextEncode")):
            inputs["text"] = edit_prompt
        if "image" in inputs and "background" in title:
            inputs["image"] = str(base_image_path)
        if "image" in inputs and ("character" in title or "reference" in title):
            inputs["image"] = str(character_image_path)

    return patched


def repose_character(
    base_image_path: str,
    char_key: str,
    edit_prompt: str,
    output_path: str,
    config: dict,
    project_id: str,
    *,
    seed: int = 0,
) -> str:
    """Return path to reposed image; on any failure return base_image_path unchanged."""
    base_path = Path(base_image_path)
    output = Path(output_path)
    if not base_path.exists():
        log.warning("[qwen_edit] Base image missing, skipping: %s", base_path)
        return str(base_path)

    missing = preflight_qwen_edit(config)
    if missing:
        log.warning("[qwen_edit] Preflight failed; keeping base image. Missing: %s", "; ".join(missing))
        return str(base_path)

    reference_path, identity_hash, character = _ensure_reference_image(project_id, char_key, config)
    if not reference_path:
        log.warning("[qwen_edit] No character reference for %s; keeping base image", char_key)
        return str(base_path)

    full_prompt = build_qwen_edit_prompt(character, edit_prompt)
    cached = _cache_path(base_path, identity_hash or char_key, full_prompt, seed, config)
    if cached.exists():
        _copy_file(cached, output)
        log.info("[qwen_edit] Cache hit for %s -> %s", char_key, output)
        return str(output)

    try:
        from video.image_gen.comfyui_client import ComfyUIClient
        from video.image_gen.comfyui_runtime import get_comfyui_runtime

        img_cfg = _image_gen_cfg(config)
        qwen = _qwen_cfg(config)
        comfy_cfg = img_cfg.get("comfyui", {}) or {}
        runtime = get_comfyui_runtime({"comfyui": comfy_cfg})
        if not runtime.ensure_running(timeout=comfy_cfg.get("auto_start_timeout", 60)):
            raise RuntimeError(f"ComfyUI not running at {runtime.base_url}")

        workflow_path = Path(qwen.get("workflow_path") or _DEFAULT_WORKFLOW)
        workflow = _patch_qwen_workflow(
            workflow_path,
            base_image_path=base_path,
            character_image_path=reference_path,
            edit_prompt=full_prompt,
            output_path=output,
            seed=seed,
            config=config,
        )
        client = ComfyUIClient(base_url=runtime.base_url, timeout=qwen.get("timeout_seconds", comfy_cfg.get("timeout_seconds", 600)))
        generated = client.generate_image(
            workflow,
            output.parent,
            filename_prefix=output.stem,
            poll_interval=qwen.get("poll_seconds", comfy_cfg.get("poll_seconds", 1.0)),
            timeout=qwen.get("timeout_seconds", comfy_cfg.get("timeout_seconds", 600)),
        )
        if not generated:
            log.warning("[qwen_edit] ComfyUI returned no image; keeping base image")
            return str(base_path)

        generated_path = Path(generated[0])
        _copy_file(generated_path, output)
        cached.parent.mkdir(parents=True, exist_ok=True)
        _copy_file(output, cached)
        return str(output)
    except Exception as e:
        log.warning("[qwen_edit] Generation failed for %s: %s — keeping base image", char_key, e)
        return str(base_path)
