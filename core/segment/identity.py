"""Perceptual-hash (dhash) + identity-critical image trigger detection.

Moved verbatim from make_process_segment closure in segment_runner.py.
Each function is now a module-level helper — no closure dependencies.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

_OUTFIT_KEYWORDS = {
    "outfit",
    "gown",
    "robe",
    "armor",
    "cloak",
    "uniform",
    "dress",
    "suit",
    "costume",
    "garment",
}
_JEWELRY_KEYWORDS = {
    "necklace",
    "ring",
    "earring",
    "bracelet",
    "crown",
    "tiara",
    "amulet",
    "pendant",
    "brooch",
    "gem",
    "jewel",
}
_WEAPON_KEYWORDS = {
    "sword",
    "dagger",
    "bow",
    "arrow",
    "spear",
    "axe",
    "shield",
    "staff",
    "wand",
    "blade",
    "mace",
    "scythe",
}
_CLOSEUP_TOKENS = {"close-up", "closeup", "portrait", "medium close-up"}
_INTRO_TOKENS = {"wearing", "introducing", "reveals", "new", "emerges", "appears"}


def _perceptual_hash(image_path: str | Path, hash_size: int = 8) -> str:
    """Compute a perceptual difference hash (dhash) for an image.

    Returns a hex string of length ``hash_size * hash_size // 4``.
    Similar images produce similar hashes; a Hamming-distance
    threshold of 10—14 is typical for ``hash_size=8``.
    """
    try:
        from PIL import Image

        with Image.open(str(image_path)) as img:
            grey = img.convert("L")
            resized = grey.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
        bits = []
        for row in range(hash_size):
            for col in range(hash_size):
                left = int(cast(Any, resized.getpixel((col, row))))
                right = int(cast(Any, resized.getpixel((col + 1, row))))
                bits.append(1 if left < right else 0)
        # Pack into hex
        hex_hash = ""
        for i in range(0, len(bits), 4):
            nibble = 0
            for j in range(4):
                if i + j < len(bits):
                    nibble |= bits[i + j] << (3 - j)
            hex_hash += format(nibble, "x")
        return hex_hash
    except Exception:
        return ""


def _detect_important_trigger(
    idx: int,
    frame_cp: dict,
    prompt: str,
    script: str,
) -> tuple[bool, str]:
    """Return (is_important, trigger_reason) for the given frame."""
    weights = list(frame_cp.values())
    max_w = max(weights) if weights else 0.0
    prompt_lower = prompt.lower()

    # Frame 0 is always a character sheet / establishing identity
    if idx == 0:
        return True, "character_sheet"

    # Multi-character key frame (two+ characters with significant presence)
    significant = sum(1 for w in weights if w >= 0.3)
    if significant >= 2:
        return True, "multi_char_key_frame"

    # Major close-up / face reference
    if max_w >= 0.8:
        return True, "face_reference"

    # Full-body reference (medium shot with identity description)
    full_body_hint = (
        any(tok in prompt_lower for tok in _CLOSEUP_TOKENS) and "full body" in prompt_lower
    )
    if full_body_hint and max_w >= 0.5:
        return True, "full_body_reference"

    # New outfit / garment detected in prompt
    outfit_hit = any(tok in prompt_lower for tok in _OUTFIT_KEYWORDS)
    intro_hit = any(tok in prompt_lower for tok in _INTRO_TOKENS)
    if outfit_hit and intro_hit and max_w >= 0.5:
        return True, "new_outfit"

    # Jewelry detected in prompt
    if any(tok in prompt_lower for tok in _JEWELRY_KEYWORDS) and max_w >= 0.3:
        return True, "jewelry"

    # Weapon detected in prompt
    if any(tok in prompt_lower for tok in _WEAPON_KEYWORDS) and max_w >= 0.3:
        return True, "weapon"

    # Fallback: weight >= 0.5 (legacy heuristic)
    if max_w >= 0.5:
        return True, "high_importance_frame"

    return False, ""
