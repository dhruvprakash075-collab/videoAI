import copy
import logging
import re
from pathlib import Path

import yaml

from .config_schemas import validate_config

log = logging.getLogger(__name__)


def _safe_filename(name: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    s = s.lstrip("._")
    if not s:
        s = "_"
    return s[:maxlen]


# ── CONFIG ─────────────────────────────────────────────────────────────────


def dict_merge(dct, merge_dct):
    dct = copy.deepcopy(dct)
    for k, v in merge_dct.items():
        if k in dct and isinstance(dct[k], dict) and isinstance(v, dict):
            dct[k] = dict_merge(dct[k], v)
        else:
            dct[k] = copy.deepcopy(v)
    return dct


def load_config(
    config_file: Path = Path(__file__).parent / "config.yaml", project_name: str | None = None
) -> dict:
    base_config = _default_config()
    if config_file.exists():
        with open(config_file, encoding="utf-8") as f:
            base_config = dict_merge(base_config, yaml.safe_load(f) or {})
    else:
        log.warning("config.yaml missing — using defaults")

    if project_name:
        project_file = Path("projects") / f"{project_name}.yaml"
        if project_file.exists():
            with open(project_file, encoding="utf-8") as f:
                project_config = yaml.safe_load(f) or {}
                base_config = dict_merge(base_config, project_config)
                log.info(f"Loaded project configuration: {project_name}")
        else:
            log.warning(f"Project configuration not found: {project_file}")

    # Validate against schema
    try:
        validated_config = validate_config(base_config)
        return validated_config
    except Exception as e:
        log.warning(f"Configuration validation failed: {e} — falling back to raw configuration")
        return base_config


def _default_config() -> dict:
    return {
        "models": {
            "director": "hermes-director",
            "writer": "zephyr-writer",
            "script_gen": "ollama/coder",
        },
        "visual": {"num_scenes": 6, "style": "Gothic Horror, Dark Victorian Steampunk"},
        "tts": {"lang": "hi", "slow": False},
        "checkpoint": {"enabled": True, "dir": "studio_checkpoints"},
        "memory": {"memory_file": "studio_checkpoints/story_memory.json"},
        "video": {
            "total_duration_min": 10,
            "segment_duration_min": 2,
            "fps": 24,
            "resolution": "1920x1080",
            "output_path": "studio_outputs/final_video.mp4",
        },
        "script": {"words_per_segment": 130, "min_words": 20, "max_words": 400},
        "characters": {
            "protagonist": {
                "name": "The Protagonist",
                "description": "young adult, determined expression, dark practical clothing, athletic build, striking eyes, original character",
                "keywords": [
                    "hero",
                    "fight",
                    "journey",
                    "discover",
                    "challenge",
                    "courage",
                    "struggle",
                ],
            }
        },
        "scene_templates": {
            "monster": "corrupted creature looming, red fog glow, slow dolly-in",
            "fog": "thick fog rolling, bluish-black haze, horizontal tracking",
        },
    }


# ── CHARACTER ──────────────────────────────────────────────────────────────


def get_character(config: dict, name: str) -> dict:
    chars = config.get("characters")
    if not chars:
        raise ValueError("config.yaml missing 'characters'")
    char = chars.get(name)
    if not char:
        log.warning(f"Character '{name}' not found — using first. Available: {list(chars)}")
        char = next(iter(chars.values()))
    return char
