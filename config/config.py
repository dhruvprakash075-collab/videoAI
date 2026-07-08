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


def _project_config_path(project_name: str) -> Path:
    if ".." in project_name or "/" in project_name or "\\" in project_name:
        raise ValueError(f"Invalid project name: {project_name!r}")

    projects_root = Path("projects").resolve()
    project_file = (projects_root / f"{_safe_filename(project_name)}.yaml").resolve()
    try:
        project_file.relative_to(projects_root)
    except ValueError as exc:
        raise ValueError(f"Invalid project name: {project_name!r}") from exc
    return project_file


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
            config_data = yaml.safe_load(f) or {}
            if not isinstance(config_data, dict):
                raise TypeError(f"Config root must be a mapping: {config_file}")
            base_config = dict_merge(base_config, config_data)
    else:
        log.warning("config.yaml missing — using defaults")

    if project_name:
        project_file = _project_config_path(project_name)
        project_display = Path("projects") / f"{_safe_filename(project_name)}.yaml"
        if project_file.exists():
            with open(project_file, encoding="utf-8") as f:
                project_config = yaml.safe_load(f) or {}
                if not isinstance(project_config, dict):
                    raise TypeError(f"Project config root must be a mapping: {project_display}")
                base_config = dict_merge(base_config, project_config)
                log.info(f"Loaded project configuration: {project_name}")
        else:
            log.warning(f"Project configuration not found: {project_display}")

    # Validate against schema — strict: invalid config fails fast
    validated_config = validate_config(base_config)
    return validated_config


def _default_config() -> dict:
    return {
        "language": "hi",
        "models": {
            "director": "hermes-director",
            "writer": "zephyr-writer",
        },
        "visual": {"num_scenes": 6, "style": "Gothic Horror, Dark Victorian Steampunk"},
        "tts": {"lang": "hi"},
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


def get_language(config: dict) -> str:
    """Return the active language, preferring top-level 'language' over 'tts.lang'.

    This is a first-class config dimension so TTS, translation, subtitles,
    and narrator all use the same value. Falls back to 'hi' (Hindi).
    """
    lang = config.get("language")
    if isinstance(lang, dict):
        return str(lang.get("code") or "hi")
    if not lang:
        lang = config.get("tts", {}).get("lang", "hi")
    return str(lang)


def get_character(config: dict, name: str) -> dict:
    chars = config.get("characters")
    if not chars:
        raise ValueError("config.yaml missing 'characters'")
    char = chars.get(name)
    if not char:
        log.warning(f"Character '{name}' not found — using first. Available: {list(chars)}")
        char = next(iter(chars.values()))
    return char
