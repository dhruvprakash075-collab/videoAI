"""framepack_i2v.py - FramePack image-to-video motion (V1, opt-in).

Turns each scene PNG into a short MP4 clip using FramePack (lllyasviel).
Only active when config video.motion_engine = "framepack".
Default is "none" (Ken Burns, unchanged).

Install (operator, one-time — NOT auto-installed):
  venv\\Scripts\\pip.exe install framepack
  # Download weights to hf_cache/ (multi-GB — do separately)
  venv\\Scripts\\huggingface-cli.exe download lllyasviel/FramePack --local-dir hf_cache\\framepack

6GB constraint: FramePack MUST run in the HEAVY scheduler slot after a verified
VRAM evict. Never co-resident with SD, LLM, or F5-TTS.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# FramePack is optional — guard the import
try:
    import framepack as _fp_module

    _FRAMEPACK_AVAILABLE = True
except ImportError:
    _FRAMEPACK_AVAILABLE = False


def is_available() -> bool:
    """Return True if FramePack is installed and importable."""
    return _FRAMEPACK_AVAILABLE


def image_to_video(
    image_path: Path,
    output_path: Path,
    seconds: float = 3.0,
    fps: int = 24,
    device: str = "cuda",
) -> Path | None:
    """Convert a single PNG/JPG to a short MP4 using FramePack.

    Args:
        image_path: Source image (PNG/JPG).
        output_path: Destination MP4 path.
        seconds: Duration of the output clip in seconds.
        fps: Frames per second.
        device: "cuda" or "cpu".

    Returns:
        Path to the generated MP4, or None on failure.
    """
    if not _FRAMEPACK_AVAILABLE:
        log.warning(
            "[FramePack] Not installed — skipping motion generation for %s", image_path.name
        )
        return None

    if not image_path.exists():
        log.error("[FramePack] Source image not found: %s", image_path)
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        log.info(
            "[FramePack] Generating %ss motion clip: %s -> %s",
            seconds,
            image_path.name,
            output_path.name,
        )

        # FramePack API: generate(image, duration_seconds, fps, device) -> video_path
        # The exact API depends on the installed version; we wrap it defensively.
        result = _fp_module.generate(
            image=str(image_path),
            output=str(output_path),
            duration=seconds,
            fps=fps,
            device=device,
        )
        if result and Path(result).exists():
            log.info("[FramePack] Motion clip ready: %s", output_path.name)
            return Path(result)
        if output_path.exists():
            return output_path
        log.error("[FramePack] generate() returned no output for %s", image_path.name)
        return None
    except Exception as e:
        log.exception("[FramePack] Failed to generate motion for %s: %s", image_path.name, e)
        return None


def images_to_videos(
    image_paths: list,
    output_dir: Path,
    seconds: float = 3.0,
    fps: int = 24,
    device: str = "cuda",
) -> list:
    """Convert a list of images to motion clips.

    Returns a list of (original_image_path, mp4_path_or_None) tuples.
    Images that fail fall back to None so the caller can use the static image.
    """
    results = []
    for img_path in image_paths:
        img_path = Path(img_path)
        out_mp4 = output_dir / (img_path.stem + "_motion.mp4")
        mp4 = image_to_video(img_path, out_mp4, seconds=seconds, fps=fps, device=device)
        results.append((img_path, mp4))
    return results
