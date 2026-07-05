"""Pure helpers for Video.AI ComfyUI nodes."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import yaml

DEFAULT_REPO_ROOT = r"C:\Video.AI"


def resolve_repo_root(explicit: str = "") -> Path:
    return Path(explicit or os.environ.get("VIDEO_AI_ROOT") or DEFAULT_REPO_ROOT).expanduser()


def bootstrap_repo_import(explicit: str = "") -> Path:
    root = resolve_repo_root(explicit)
    root_str = str(root)
    if root.exists() and root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def resolve_config_path(config_path: str, repo_root: Path) -> Path:
    path = Path(config_path)
    return path if path.is_absolute() else repo_root / path


def pick_image_gen(cfg: dict, key: str, default):
    image_gen = cfg.get("image_gen", {}) or {}
    comfy = image_gen.get("comfyui", {}) or {}
    if key in comfy:
        return comfy[key]
    return image_gen.get(key, default)


def read_image_gen_values(cfg: dict) -> tuple:
    image_gen = cfg.get("image_gen", {}) or {}
    return (
        int(pick_image_gen(cfg, "width", 1024)),
        int(pick_image_gen(cfg, "height", 1024)),
        int(pick_image_gen(cfg, "steps", 20)),
        float(pick_image_gen(cfg, "cfg", image_gen.get("guidance_scale", 7.0))),
        str(pick_image_gen(cfg, "sampler_name", "euler")),
        str(pick_image_gen(cfg, "scheduler", "normal")),
        str(pick_image_gen(cfg, "checkpoint", "")),
        str(pick_image_gen(cfg, "negative_prompt", "")),
        bool(pick_image_gen(cfg, "unload_after_batch", False)),
    )


def image_to_tensor(img):
    import numpy as np
    import torch
    from PIL import ImageOps

    arr = np.asarray(ImageOps.exif_transpose(img).convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(arr)[None, ...]


def tensor_to_images(image):
    import numpy as np
    from PIL import Image

    return [Image.fromarray(np.clip(255.0 * frame.cpu().numpy(), 0, 255).astype(np.uint8)) for frame in image]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def char_key_from(name: str) -> str:
    return name.strip().lower().replace(" ", "_")
