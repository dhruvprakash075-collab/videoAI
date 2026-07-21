"""Perceptual-hash (dhash) + identity-critical image trigger detection.

Moved verbatim from make_process_segment closure in segment_runner.py.
Each function is now a module-level helper — no closure dependencies.

Used by:
- segment_runner.image_node: computes dhash for each generated frame
- segment_runner.do_important_image_review: decides if frame needs Director review
- memory.permanent_memory.PermanentMemoryLog: stores identity_hash for LoRA/IP-Adapter candidates

Keyword sets (_OUTFIT_KEYWORDS, _JEWELRY_KEYWORDS, _WEAPON_KEYWORDS, _CLOSEUP_TOKENS,
_INTRO_TOKENS) define what the Director considers "identity-critical" — i.e. frames
where a character's visual identity is being established or changed, and therefore
must be consistent across the entire video series.

Config knobs (not yet exposed, thresholds hardcoded here):
- hash_size: 8 (64-bit dhash) — increase to 16 for finer discrimination
- weight thresholds: 0.8 face ref, 0.5 full-body/outfit/weapon, 0.3 jewelry/legacy
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

# Identity-critical keyword sets — extend if new categories need tracking
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
    threshold of 10–14 is typical for ``hash_size=8``.

    Used to detect visual drift when the same character appears across segments.
    If the current frame's dhash differs from the stored identity_hash by more
    than the threshold, the frame is flagged for Director review.

    Args:
        image_path: Path to image file
        hash_size: Dhash grid size (default 8 = 64-bit hash)

    Returns:
        Hex-encoded dhash string, or empty string on failure
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
    """Return (is_important, trigger_reason) for the given frame.

    Determines whether a generated frame contains identity-critical content
    that must be reviewed by the Director and potentially stored as a
    LoRA/IP-Adapter reference. Triggers are prioritized:

    1. idx == 0 → "character_sheet" (first frame always establishes identity)
    2. Multi-character frame (≥2 chars with weight ≥0.3) → "multi_char_key_frame"
    3. Dominant face close-up (max weight ≥0.8) → "face_reference"
    4. Full-body reference (closeup token + "full body" + weight ≥0.5) → "full_body_reference"
    5. New outfit (outfit keyword + intro token + weight ≥0.5) → "new_outfit"
    6. Jewelry (jewelry keyword + weight ≥0.3) → "jewelry"
    7. Weapon (weapon keyword + weight ≥0.3) → "weapon"
    8. Legacy fallback (max weight ≥0.5) → "high_importance_frame"

    Args:
        idx: Frame index within segment (0 = character sheet)
        frame_cp: char_presence dict for this frame {char_name: weight}
        prompt: Image generation prompt used for this frame
        script: Segment narration script (unused, reserved for future context)

    Returns:
        (is_important: bool, trigger_reason: str)
    """
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
