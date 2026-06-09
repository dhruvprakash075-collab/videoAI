"""utils.py - Utility functions for pipeline_long.py."""

import json
import logging
import subprocess
from pathlib import Path

from config import _safe_filename, get_character, load_config

log = logging.getLogger(__name__)


def setup_run_logging(log_dir: Path) -> None:
    """Setup file + console logging for a run."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "pipeline.log"

    from logging.handlers import RotatingFileHandler

    # We no longer clear root handlers to prevent wiping other concurrent pipeline logs.
    # Instead, we just check if we already have a handler for this specific file.
    file_resolved = log_file.resolve()
    if any(
        isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == file_resolved
        for h in logging.root.handlers
    ):
        return  # Already configured for this run

    # Rotating File handler (10MB max, keep 5 backups)
    fh = RotatingFileHandler(log_file, encoding="utf-8", maxBytes=10 * 1024 * 1024, backupCount=5)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s"))

    # Console handler
    import sys

    class SafeStream:
        def __init__(self, stream):
            self.stream = stream

        def write(self, s):
            try:
                self.stream.write(s)
            except UnicodeEncodeError:
                self.stream.write(s.encode("ascii", "replace").decode("ascii"))

        def flush(self):
            self.stream.flush()

    ch = logging.StreamHandler(SafeStream(sys.stdout))
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logging.root.addHandler(fh)

    # Only add console handler if one doesn't exist
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logging.root.handlers
    ):
        logging.root.addHandler(ch)

    logging.root.setLevel(logging.DEBUG)

    # Suppress noisy library debug logs
    for _noisy in ("httpcore", "httpx", "huggingface", "huggingface_hub", "urllib3", "PIL"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    log.info(f"Logging to {log_file}")


def build_prompts(script: str, plan: dict, config: dict) -> str:
    """Build image generation prompts from script and scene plan.

    Dynamic image count: Uses plan['num_images'] if available (set by the Director
    LLM in plan_story), otherwise falls back to default_images_per_segment from config.

    Args:
        script: Generated script text
        plan: Segment plan dict with title, key_event, mood, num_images
        config: Config dict

    Returns:
        Semicolon-separated image prompts
    """
    # Use char_presence from plan to pick the most-relevant character, if available
    char_presence_map = plan.get("char_presence", []) if plan else []
    max_weight = 0.0
    best_key = None
    for frame in char_presence_map if isinstance(char_presence_map, list) else []:
        if isinstance(frame, dict):
            for ck, cw in frame.items():
                if cw > max_weight:
                    max_weight, best_key = cw, ck
    characters = config.get("characters", {})
    if best_key and best_key in characters:
        character = characters[best_key]
    else:
        character_key = next(iter(characters.keys()), "protagonist")
        character = characters.get(character_key, {})
    desc = character.get("description", "a figure")

    mood = plan.get("mood", "mysterious")
    key_event = plan.get("key_event", "something happens")

    # Image count: when dynamic_image_count is enabled (default) the Director's
    # per-segment plan['num_images'] wins; when disabled, the config default is
    # used as a fixed count (lets the operator force an exact number per segment,
    # e.g. via the --images-per-segment lock).
    script_cfg = config.get("script", {})
    default_count = script_cfg.get("default_images_per_segment", 6)
    if script_cfg.get("dynamic_image_count", True):
        target_count = plan.get("num_images", default_count)
    else:
        target_count = default_count
    # Guard extremes: clamp to [2, max_images_per_segment].
    # P3-12 fix: read max_images_per_segment from config instead of hardcoding 30.
    # Default is 10 (matches config.yaml script.max_images_per_segment) so we
    # never generate more images than the operator configured, which is important
    # on a 6GB GPU where each SD call is expensive.
    _max_imgs = config.get("script", {}).get("max_images_per_segment", 10)
    target_count = max(2, min(_max_imgs, target_count))

    log.info(f"Building {target_count} prompts for segment (dynamic image scaling)")

    # Build dynamic set of prompts based on target count
    prompts = []

    # Always start with these core shots
    core_shots = [
        f"{desc}, {key_event}, {mood} atmosphere, establishing shot",
        f"Gothic Victorian setting, {mood} mood, dramatic lighting, medium shot",
        f"Close-up emotional scene, {desc}, introspective, shallow depth of field",
    ]

    # Variety of shot types to cycle through for additional images
    # Mood-aware shot templates (customised per script demand)
    mood_shots = {
        "mysterious": [
            f"Wide shot, {desc} dwarfed by ancient ruins, volumetric fog, mysterious atmosphere",
            f"{desc} discovering cryptic symbols, shallow depth of field, cinematic lighting",
        ],
        "horror": [
            f"Dutch angle, {desc} in terror, flickering torchlight, claustrophobic cave",
            f"Extreme close-up, {desc} eyes wide with fear, shadow looming behind",
        ],
        "action": [
            f"Dynamic action shot, {desc} mid-strike, motion blur, dramatic lighting",
            f"Low angle, {desc} heroic pose, debris flying, cinematic scale",
        ],
        "dramatic": [
            f"Hero shot, {desc} in spotlight, dramatic shadows, emotional intensity",
            f"Two-shot, {desc} confronting a figure, rim lighting, intense gaze",
        ],
        "calm": [
            f"Wide serene landscape, {desc} small in frame, golden hour light, peaceful",
            f"Soft close-up, {desc} at peace, warm bokeh background, gentle expression",
        ],
        "epic": [
            f"Crane shot, {desc} overlooking vast battlefield, anamorphic lens flare",
            f"Extreme wide, {desc} silhouette against burning sky, massive scale",
        ],
    }
    # Pick mood-specific shots based on plan mood
    mood = plan.get("mood", "mysterious")
    mood_variations = mood_shots.get(mood, mood_shots["mysterious"])

    shot_variations = [
        f"Wide establishing shot, {mood} Victorian landscape, volumetric fog",
        f"Action shot, {desc} in motion, {key_event}, dynamic pose",
        f"Detailed character study, {desc}, cinematic portrait lighting",
        f"Over-the-shoulder shot, {desc} observing {mood} environment",
        f"Low angle hero shot, {desc}, dramatic sky, epic scale",
        f"Detail close-up, {key_event}, macro shot, intense atmosphere",
        f"Silhouette shot, {desc} against {mood} backdrop, rim lighting",
        f"Dutch angle, unsettling {mood} scene, claustrophobic framing",
        f"Bird's eye view, {key_event} unfolding below, cinematic scale",
        f"Two-shot, {desc} interacting with environment, {mood} lighting",
        f"Extreme close-up, emotional moment, {desc} face, tears or determination",
        f"Crane shot pulling back, revealing {mood} scene scope",
    ]

    # Take core shots first, then fill remaining with variations
    prompts.extend(core_shots)
    # Prepend mood-specific shots for variety
    prompts = mood_variations + prompts

    # ── Uniqueness modifiers (prevents duplicate prompts → duplicate cached images) ──
    # Pools of distinct descriptors. Each frame draws a unique combination based on
    # its index so no two frames within a segment produce identical prompt strings.
    _angles = [
        "low angle",
        "high angle",
        "eye-level",
        "Dutch angle",
        "bird's eye view",
        "worm's eye view",
        "over-the-shoulder",
        "profile view",
        "three-quarter view",
        "front-on",
        "wide angle",
        "telephoto compression",
    ]
    _distances = [
        "extreme wide shot",
        "wide shot",
        "full shot",
        "medium-wide shot",
        "medium shot",
        "medium close-up",
        "close-up",
        "extreme close-up",
    ]
    _light = [
        "golden hour glow",
        "blue-hour twilight",
        "harsh midday sun",
        "moonlit",
        "candlelit interior",
        "backlit silhouette",
        "rim-lit",
        "soft diffused light",
        "dramatic chiaroscuro",
        "neon-tinged shadows",
        "overcast grey",
        "dawn mist",
    ]
    _focus = [
        "deep focus",
        "shallow depth of field",
        "tilt-shift",
        "soft bokeh background",
        "rack focus",
        "sharp foreground detail",
    ]

    remaining = target_count - len(prompts)
    if remaining > 0:
        n_var = len(shot_variations)
        for i in range(remaining):
            base = shot_variations[i % n_var]
            # Append a UNIQUE modifier combination per frame so cycled bases differ.
            ang = _angles[i % len(_angles)]
            dist = _distances[(i // 2) % len(_distances)]
            lit = _light[(i + 1) % len(_light)]
            foc = _focus[i % len(_focus)]
            prompts.append(f"{base}, {dist}, {ang}, {lit}, {foc}")

    # Trim to exact target count
    prompts = prompts[:target_count]

    # ── Final uniqueness guard: if any prompt still repeats, append a distinct
    # per-frame tag so the cache key never collides (no duplicate images). ──
    _seen = {}
    _unique_prompts = []
    for idx, p in enumerate(prompts):
        if p in _seen:
            _seen[p] += 1
            # Add a distinct variation cue drawn from the pools
            _tag = (
                f"{_angles[idx % len(_angles)]}, {_light[idx % len(_light)]}, variation {idx + 1}"
            )
            p = f"{p}, {_tag}"
        else:
            _seen[p] = 1
        _unique_prompts.append(p)
    prompts = _unique_prompts

    return "; ".join(prompts)


def validate_script(script: str, config: dict) -> bool:
    """Check if script meets minimum quality requirements.

    Args:
        script: Generated script text
        config: Config dict

    Returns:
        True if valid, False otherwise
    """
    min_words = config.get("script", {}).get("min_words", 20)
    max_words = config.get("script", {}).get("max_words", 400)

    word_count = len(script.split())

    if word_count < min_words:
        log.warning(f"Script too short: {word_count} < {min_words}")
        return False

    if word_count > max_words:
        log.warning(f"Script too long: {word_count} > {max_words}")
        return False

    # Check for minimum coherence (not all same word repeated)
    words = script.split()
    unique_ratio = len(set(words)) / len(words) if words else 0
    if unique_ratio < 0.4:
        log.warning(f"Script has low vocabulary diversity: {unique_ratio:.1%}")
        return False

    return True


def save_outputs(topic: str, outputs: dict, out_base: Path) -> None:
    """Save intermediate outputs for debugging/resume."""
    import json

    def _sanitize(v):
        if isinstance(v, (list, tuple)):
            return [_sanitize(x) for x in v]
        if isinstance(v, dict):
            return {k: _sanitize(val) for k, val in v.items()}
        if hasattr(v, "__fspath__") or isinstance(v, Path):
            return str(v)
        return v

    meta_file = out_base / "outputs_meta.json"
    meta = {"topic": topic, "outputs": {k: _sanitize(v) for k, v in outputs.items()}}

    meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info(f"Outputs saved to {meta_file}")


# ── Shared Audio Utilities ─────────────────────────────────────────────────


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds using ffprobe.

    Uses ffprobe to probe audio duration. Returns a safe positive value
    or 30.0 seconds as a fallback if probing fails.

    Args:
        audio_path: Path to audio file (WAV, MP3, etc.)

    Returns:
        Duration in seconds (always >= 0.1), or 30.0 if an error occurs
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(audio_path),
            ],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 30.0))
        return max(0.1, duration)  # Prevent zero-duration bugs
    except Exception as e:
        log.warning(f"ffprobe duration read failed ({e}) — defaulting to 30s")
        return 30.0


# Re-export config functions for convenience
__all__ = [
    "_safe_filename",
    "build_prompts",
    "get_audio_duration",
    "get_character",
    "load_config",
    "save_outputs",
    "setup_run_logging",
    "validate_script",
]
