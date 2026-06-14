"""model_eval.py - Opt-in evaluation harness for image model and TTS engine (R12.6).

Generates a small fixed-prompt sample set from the current SD model and a short
TTS clip from the current voice, writing them to model_eval/ for A/B comparison.
Does NOT run a full video pipeline.

Usage:
  venv\\Scripts\\python.exe utils\\model_eval.py
  venv\\Scripts\\python.exe utils\\model_eval.py --tts-only
  venv\\Scripts\\python.exe utils\\model_eval.py --image-only
  venv\\Scripts\\python.exe utils\\model_eval.py --out-dir my_eval
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Fixed sample prompts for image A/B comparison
_SAMPLE_PROMPTS = [
    "young adult protagonist, determined expression, dark practical clothing, "
    "standing at the edge of a cliff, stormy sky, cinematic lighting, anime style",
    "ancient stone lighthouse on a rocky coast, volumetric fog, moonlight, "
    "atmospheric, wide establishing shot, dark fantasy anime style",
    "older wise mentor figure, weathered face, calm knowing eyes, long coat, "
    "standing in a library, warm candlelight, medium shot, anime style",
]

# Fixed TTS sample text (short, covers emotion + Devanagari loanwords)
_SAMPLE_TTS_TEXT = (
    "यह एक परीक्षण है। प्रकाशस्तंभ की रोशनी... अंधेरे में चमकती है! "
    "क्या यह सच है? फोन, कैमरा, स्कूल — सब कुछ बदल गया।"
)
_SAMPLE_TTS_TEXT_EN = (
    "This is a test. The lighthouse beam... shines through the darkness! "
    "Is this real? Everything has changed."
)


def run_image_eval(out_dir: Path, config: dict) -> list:
    """Generate sample images from the current SD model. Returns list of saved paths."""
    try:
        from video.image_gen.image_gen import generate_images
    except ImportError:
        log.warning("[eval] image_gen not available — skipping image eval")
        return []

    img_out = out_dir / "images"
    img_out.mkdir(parents=True, exist_ok=True)

    log.info(f"[eval] Generating {len(_SAMPLE_PROMPTS)} sample images...")
    t0 = time.time()
    try:
        paths = generate_images(
            "; ".join(_SAMPLE_PROMPTS),
            img_out,
            config,
        )
        elapsed = time.time() - t0
        log.info(f"[eval] {len(paths)} images generated in {elapsed:.1f}s")
        return [str(p) for p in paths]
    except Exception as e:
        log.exception(f"[eval] Image generation failed: {e}")
        return []


def run_tts_eval(out_dir: Path, config: dict) -> str:
    """Generate a short TTS clip from the current voice. Returns path or empty string."""
    try:
        from audio.audio_proxy import tts_generate
    except ImportError:
        log.warning("[eval] audio_proxy not available — skipping TTS eval")
        return ""

    tts_out = out_dir / "tts"
    tts_out.mkdir(parents=True, exist_ok=True)

    from config.config import get_language

    lang = get_language(config)
    text = _SAMPLE_TTS_TEXT if lang == "hi" else _SAMPLE_TTS_TEXT_EN

    log.info(f"[eval] Generating TTS sample (lang={lang})...")
    t0 = time.time()
    try:
        result = tts_generate(text, lang=lang, output_dir=tts_out)
        elapsed = time.time() - t0
        wav = str(result.get("wav_path", "")) if isinstance(result, dict) else str(result)
        log.info(f"[eval] TTS sample generated in {elapsed:.1f}s: {wav}")
        return wav
    except Exception as e:
        log.exception(f"[eval] TTS generation failed: {e}")
        return ""


def run_eval(out_dir: Path | None = None, image: bool = True, tts: bool = True) -> dict:
    """Run the evaluation harness. Returns a summary dict."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from config import load_config

    config = load_config()

    if out_dir is None:
        out_dir = Path("model_eval") / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "image_model": config.get("image_gen", {}).get("sd_model_path")
        or config.get("image_gen", {}).get("sd_model", "unknown"),
        "tts_engine": config.get("tts", {}).get("engine", "unknown"),
        "acceleration": (config.get("image_gen", {}).get("acceleration") or {}).get("type", "none"),
        "upscaler": (config.get("image_gen", {}).get("upscaler") or {}).get("model", "none"),
        "images": [],
        "tts_sample": "",
        "output_dir": str(out_dir),
    }

    if image:
        summary["images"] = run_image_eval(out_dir, config)
    if tts:
        summary["tts_sample"] = run_tts_eval(out_dir, config)

    # Write summary JSON
    summary_path = out_dir / "eval_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print("  MODEL EVAL COMPLETE")
    print("=" * 60)
    print(f"  Output dir : {out_dir}")
    print(f"  Images     : {len(summary['images'])} samples")
    print(f"  TTS sample : {summary['tts_sample'] or 'skipped'}")
    print(f"  Model      : {summary['image_model']}")
    print(f"  TTS engine : {summary['tts_engine']}")
    print(f"  Accel      : {summary['acceleration']}")
    print(f"  Upscaler   : {summary['upscaler']}")
    print("=" * 60)
    print("  Open the output dir to review images and listen to the TTS clip.")
    print("  Compare against a previous eval run to judge quality/speed tradeoffs.")
    print("=" * 60 + "\n")

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Evaluate current image model and TTS engine")
    parser.add_argument("--image-only", action="store_true", help="Only generate sample images")
    parser.add_argument("--tts-only", action="store_true", help="Only generate TTS sample")
    parser.add_argument(
        "--out-dir", default=None, help="Output directory (default: model_eval/TIMESTAMP)"
    )
    args = parser.parse_args()

    out = Path(args.out_dir) if args.out_dir else None
    do_image = not args.tts_only
    do_tts = not args.image_only

    run_eval(out_dir=out, image=do_image, tts=do_tts)
