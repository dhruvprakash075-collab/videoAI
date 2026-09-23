#!/usr/bin/env python3
"""Standalone script for audio-only generation (story → translation → TTS).

Runs just the story script generation, translation, and TTS generation without
performing time-consuming image generation or video rendering.

Sets num_images=0 in config to effectively skip image generation.

Usage:
    python scratch/generate_audio_only.py --topic "A mysterious clock that counts down to the future" --yes
    python scratch/generate_audio_only.py --topic "मेरी कहानी" --lang hi --engine supertonic --yes
"""

import argparse
import os
import sys
from pathlib import Path

_repo_root = str(Path(__file__).parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from bootstrap_pipeline import bootstrap
bootstrap()

from config import load_config
from utils import _safe_filename


def main():
    parser = argparse.ArgumentParser(
        description="Generate audio-only (story → translation → TTS) without images/video"
    )
    parser.add_argument(
        "--topic",
        required=True,
        help="Video topic/title (required)",
    )
    parser.add_argument(
        "--lang",
        default="hi",
        choices=["hi", "en", "hinglish"],
        help="Language for TTS output (default: hi for Devanagari)",
    )
    parser.add_argument(
        "--engine",
        default="omnivoice",
        choices=["omnivoice", "supertonic", "f5", "xtts"],
        help="TTS engine to use (default: omnivoice)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-accept all Director consultations without prompting",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=5,
        help="Estimated video duration in minutes (affects segment count, default: 5)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh (ignore checkpoints)",
    )

    args = parser.parse_args()

    # Enable voice-only mode via environment variable
    os.environ["DIRECTOR_MODE"] = "voice-only"

    # Auto-accept flag
    if args.yes:
        try:
            from agents.director_agent import UIState

            UIState.auto_accept = True
            print("[--yes] Auto-accept mode enabled — all consultations will use defaults")
        except Exception:
            pass

    # Load config and set overrides
    config = load_config()
    
    # Voice-only mode: skip images by setting num_images to 0
    config.setdefault("visual", {})["num_scenes"] = 0
    # Override for voice-only: set default images per segment to 0
    config.setdefault("script", {}).setdefault("default_images_per_segment", 0)
    config.setdefault("script", {})["max_images_per_segment"] = 0
    
    # Set TTS engine
    config.setdefault("tts", {})["engine"] = args.engine

    # Set language for TTS
    if args.lang != "hi":
        config["tts"]["lang"] = args.lang

    topic = args.topic
    topic_slug = _safe_filename(topic)

    print("=" * 60)
    print("  AUDIO-ONLY GENERATION")
    print("=" * 60)
    print(f"Topic: {topic}")
    print(f"Language: {args.lang}")
    print(f"TTS Engine: {args.engine}")
    print(f"Mode: voice-only (no images/video)")
    print("=" * 60)

    from core.pipeline_long import run_long_pipeline

    try:
        result = run_long_pipeline(
            topic=topic,
            duration_min=args.duration,
            resume=not args.no_resume,
            dry_run=False,
            skip_rvc=False,
            preloaded_config=config,
        )

        print("\n" + "=" * 60)
        print("AUDIO GENERATION COMPLETE")
        print("=" * 60)
        print(f"Status: {result.get('status', 'unknown').upper()}")

        if result.get("status") in ("success", "dry_run"):
            # Find and print all generated WAV files
            out_dir = Path(config.get("output", {}).get("dir", "studio_outputs")) / topic_slug / "segments"
            if out_dir.exists():
                wav_files = sorted(out_dir.glob("**/*.wav"))
                if wav_files:
                    print(f"\nGenerated {len(wav_files)} audio file(s):")
                    for wav_path in wav_files:
                        print(f"  {wav_path}")
                else:
                    print("\nNo WAV files found. Check logs for details.")
            print(f"\nRun directory: {out_dir.parent}")
        else:
            print(f"Error: {result.get('reason', 'Unknown error')}")

        return 0 if result.get("status") == "success" else 1

    except KeyboardInterrupt:
        print("\n[FAILED] Interrupted by user")
        return 1
    except Exception as e:
        print(f"\n[FAILED] Error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())