"""Qwen-Image-Edit two-pass character compositing helpers.

This module is deliberately Python orchestration glue: it resolves the saved
character reference, builds the edit instruction, uploads the inputs into
ComfyUI's input store, patches a ComfyUI workflow, and copies the result back
to the existing frame path. The expensive work runs inside ComfyUI/CUDA.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_WORKFLOW = "config/comfyui/workflows/qwen_image_edit_api.json"
_REQUIRED_WORKFLOW_PLACEHOLDERS = {
    "__BASE_IMAGE__",
    "__CHARACTER_IMAGE__",
    "__EDIT_PROMPT__",
    "__FILENAME_PREFIX__",
    "__MODEL_PATH__",
    "__SEED__",
    "__STEPS__",
    "__CFG__",
    "__DENOISE__",
}
_OPTIONAL_WORKFLOW_PLACEHOLDERS = {"__LIGHTNING_LORA__"}


@dataclass
class QwenEditResult:
    """Outcome of a single Qwen-Image-Edit compositing attempt.

    status is one of:
      - "edited":  ComfyUI produced an image and it was copied to output.
      - "cached":  a previously composited frame was reused (output copied).
      - "skipped": the edit could not be attempted (missing base/reference or
                   failed preflight); the original background is kept.
      - "failed":  the edit was attempted but errored; the background is kept.

    Only "edited"/"cached" mean the character was actually composited. The
    output_path is the path callers should use for the frame.
    """

    status: str
    output_path: str
    reason: str = ""

    @property
    def composited(self) -> bool:
        return self.status in ("edited", "cached")


def _image_gen_cfg(config: dict) -> dict:
    if "image_gen" in config and isinstance(config.get("image_gen"), dict):
        return config.get("image_gen", {}) or {}
    return config or {}


def _qwen_cfg(config: dict) -> dict:
    img = _image_gen_cfg(config)
    return img.get("qwen_edit", {}) or {}


def _comfyui_image_ref(upload_result: dict) -> str:
    """Convert an upload_image response into a ComfyUI LoadImage ref.

    ComfyUI's LoadImage references an uploaded input file by its filename, or
    by ``subfolder/filename`` when the upload landed in a subfolder.
    """
    name = upload_result.get("name", "")
    if not name:
        raise ValueError("upload_image response missing 'name'")
    subfolder = upload_result.get("subfolder", "") or ""
    return f"{subfolder}/{name}" if subfolder else name


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


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dst.resolve():
        return
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    tmp.replace(dst)


def _workflow_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def validate_qwen_workflow_template(
    workflow_path: str | Path,
    *,
    require_lightning_lora: bool = False,
) -> list[str]:
    """Validate that the Qwen workflow template can be patched safely.

    This does not contact ComfyUI. It only checks that the workflow is JSON,
    object-shaped, and still exposes the placeholders patched by Python.
    """
    path = Path(workflow_path)
    issues: list[str] = []
    try:
        from utils.path_utils import is_safe_path

        if not path.is_absolute() and not is_safe_path(Path.cwd(), str(workflow_path)):
            return [f"qwen_edit workflow path escapes project directory: {path}"]
        with path.open(encoding="utf-8") as f:
            workflow = json.load(f)
    except json.JSONDecodeError as e:
        return [f"qwen_edit workflow JSON is invalid: {path} ({e})"]
    except OSError as e:
        return [f"qwen_edit workflow file could not be read: {path} ({e})"]

    if not isinstance(workflow, dict):
        return [f"qwen_edit workflow must be a JSON object: {path}"]

    workflow_blob = _workflow_text(workflow)
    missing_required = sorted(
        placeholder for placeholder in _REQUIRED_WORKFLOW_PLACEHOLDERS if placeholder not in workflow_blob
    )
    if missing_required:
        issues.append(
            "qwen_edit workflow missing required placeholder(s): "
            + ", ".join(missing_required)
        )

    if require_lightning_lora and "__LIGHTNING_LORA__" not in workflow_blob:
        issues.append("qwen_edit workflow missing optional LoRA placeholder: __LIGHTNING_LORA__")

    return issues


def preflight_qwen_edit(config: dict) -> list[str]:
    """Return missing Qwen-Image-Edit prerequisites without contacting ComfyUI."""
    img = _image_gen_cfg(config)
    qwen = _qwen_cfg(config)
    comfy_cfg = img.get("comfyui", {}) or {}
    missing: list[str] = []

    workflow_path = qwen.get("workflow_path") or _DEFAULT_WORKFLOW
    if not Path(workflow_path).exists():
        missing.append(f"qwen_edit workflow file not found: {workflow_path}")
    else:
        missing.extend(
            validate_qwen_workflow_template(
                workflow_path,
                require_lightning_lora=bool(qwen.get("lightning_lora", "")),
            )
        )

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
    base_image_ref: str,
    character_image_ref: str,
    edit_prompt: str,
    output_path: Path,
    seed: int,
    config: dict,
) -> dict:
    """Patch the Qwen workflow template with uploaded ComfyUI input refs.

    base_image_ref and character_image_ref are ComfyUI LoadImage refs (a
    filename or ``subfolder/filename`` that already exists in ComfyUI's input
    store), NOT host filesystem paths. They are produced by uploading the
    images first via ComfyUIClient.upload_image.
    """
    qwen = _qwen_cfg(config)
    with workflow_path.open(encoding="utf-8") as f:
        workflow = json.load(f)

    replacements = {
        "__BASE_IMAGE__": base_image_ref,
        "__CHARACTER_IMAGE__": character_image_ref,
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
        is_negative = "negative" in title

        # Stage uploaded ComfyUI input refs into LoadImage nodes only, and only
        # when the value is a string filename. Never clobber a node link (a
        # list like ["3", 0]) that wires one node's output into another.
        image_value = inputs.get("image")
        if class_type == "LoadImage" and isinstance(image_value, str):
            if "character" in title or "reference" in title:
                inputs["image"] = character_image_ref
            elif "background" in title or "base" in title:
                inputs["image"] = base_image_ref

        if "seed" in inputs:
            inputs["seed"] = seed
        if "steps" in inputs:
            inputs["steps"] = int(qwen.get("steps", inputs["steps"]))
        if "cfg" in inputs:
            inputs["cfg"] = float(qwen.get("cfg", inputs["cfg"]))
        if "denoise" in inputs:
            inputs["denoise"] = float(qwen.get("denoise", inputs["denoise"]))
        if "backend" in inputs:
            inputs["backend"] = qwen.get("backend", inputs["backend"])
        if "vram_offload" in inputs:
            inputs["vram_offload"] = bool(qwen.get("vram_offload", inputs["vram_offload"]))
        if "model_path" in inputs:
            inputs["model_path"] = qwen.get("model_path", inputs["model_path"])
        if "model_name" in inputs and qwen.get("model_path", ""):
            inputs["model_name"] = Path(qwen.get("model_path", "")).name
        for lora_key in ("lightning_lora", "lora_name", "lora_path"):
            if lora_key in inputs and qwen.get("lightning_lora", ""):
                inputs[lora_key] = qwen.get("lightning_lora", "")
        if "filename_prefix" in inputs:
            inputs["filename_prefix"] = output_path.stem
        # Only the positive prompt encoder receives the edit prompt. The
        # negative encoder (title contains "negative") is left untouched so the
        # instruction never leaks into the negative conditioning.
        if not is_negative and "text" in inputs and ("prompt" in title or class_type.endswith("TextEncode")) and isinstance(inputs.get("text"), str):
            inputs["text"] = edit_prompt
        if not is_negative and "prompt" in inputs and ("prompt" in title or "textencode" in class_type.lower()) and isinstance(inputs.get("prompt"), str):
            inputs["prompt"] = edit_prompt

    return patched


def repose_character_detailed(
    base_image_path: str,
    char_key: str,
    edit_prompt: str,
    output_path: str,
    config: dict,
    project_id: str,
    *,
    seed: int = 0,
) -> QwenEditResult:
    """Composite the saved character into the background, reporting the outcome.

    The result is truthful: status is only "edited"/"cached" when a composited
    image was actually written to output_path. On any skip or failure the
    original background is kept and output_path points at the base image.
    """
    base_path = Path(base_image_path)
    output = Path(output_path)
    if not base_path.exists():
        log.warning("[qwen_edit] Base image missing, skipping: %s", base_path)
        return QwenEditResult("skipped", str(base_path), "base image missing")

    missing = preflight_qwen_edit(config)
    if missing:
        reason = "preflight failed: " + "; ".join(missing)
        log.warning("[qwen_edit] %s; keeping base image", reason)
        return QwenEditResult("skipped", str(base_path), reason)

    reference_path, identity_hash, character = _ensure_reference_image(project_id, char_key, config)
    if not reference_path:
        reason = f"no character reference for {char_key}"
        log.warning("[qwen_edit] %s; keeping base image", reason)
        return QwenEditResult("skipped", str(base_path), reason)

    full_prompt = build_qwen_edit_prompt(character, edit_prompt)
    cached = _cache_path(base_path, identity_hash or char_key, full_prompt, seed, config)
    if cached.exists():
        _copy_file(cached, output)
        log.info("[qwen_edit] Cache hit for %s -> %s", char_key, output)
        return QwenEditResult("cached", str(output), "cache hit")

    try:
        from video.image_gen.comfyui_client import ComfyUIClient
        from video.image_gen.comfyui_runtime import get_comfyui_runtime

        img_cfg = _image_gen_cfg(config)
        qwen = _qwen_cfg(config)
        comfy_cfg = img_cfg.get("comfyui", {}) or {}
        runtime = get_comfyui_runtime({"comfyui": comfy_cfg})
        if not runtime.ensure_running(timeout=comfy_cfg.get("auto_start_timeout", 60)):
            raise RuntimeError(f"ComfyUI not running at {runtime.base_url}")

        client = ComfyUIClient(
            base_url=runtime.base_url,
            timeout=qwen.get("timeout_seconds", comfy_cfg.get("timeout_seconds", 600)),
        )

        # Stage both inputs inside ComfyUI's input store so the LoadImage nodes
        # can read them. LoadImage cannot read arbitrary host paths.
        base_ref = _comfyui_image_ref(client.upload_image(base_path))
        character_ref = _comfyui_image_ref(client.upload_image(reference_path))

        workflow_path = Path(qwen.get("workflow_path") or _DEFAULT_WORKFLOW)
        workflow = _patch_qwen_workflow(
            workflow_path,
            base_image_ref=base_ref,
            character_image_ref=character_ref,
            edit_prompt=full_prompt,
            output_path=output,
            seed=seed,
            config=config,
        )
        generated = client.generate_image(
            workflow,
            output.parent,
            filename_prefix=output.stem,
            poll_interval=qwen.get("poll_seconds", comfy_cfg.get("poll_seconds", 1.0)),
            timeout=qwen.get("timeout_seconds", comfy_cfg.get("timeout_seconds", 600)),
        )
        if not generated:
            reason = "ComfyUI returned no image"
            log.warning("[qwen_edit] %s; keeping base image", reason)
            return QwenEditResult("failed", str(base_path), reason)

        generated_path = Path(generated[0])
        _copy_file(generated_path, output)
        cached.parent.mkdir(parents=True, exist_ok=True)
        _copy_file(output, cached)
        return QwenEditResult("edited", str(output), "")
    except Exception as e:
        reason = str(e)
        log.warning("[qwen_edit] Generation failed for %s: %s — keeping base image", char_key, e)
        return QwenEditResult("failed", str(base_path), reason)



