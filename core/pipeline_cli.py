"""CLI entrypoint + async wrapper moved out of pipeline_long."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

__all__ = ["main", "run_long_pipeline_async"]

log = logging.getLogger(__name__)


def run_long_pipeline_async(topic: str, config: dict, **kwargs):
    """Runs pre-production and returns config overlay."""
    from core.pipeline_long import _deep_merge, _ensure_init, run_pre_production
    from utils import _safe_filename, setup_run_logging

    _ensure_init()
    setup_run_logging(Path("logs") / _safe_filename(topic))
    config_overlay = run_pre_production(topic, config, **kwargs)
    config = _deep_merge(config, config_overlay)
    return {"status": "ok", "topic": topic, "overlay": config_overlay}


def main() -> None:
    from core.pipeline_long import run_long_pipeline

    parser = argparse.ArgumentParser(description="Generate multi-segment lore video with AI")
    parser.add_argument("--topic", help="Video topic/title", default="")
    parser.add_argument(
        "--file", help="Path to text or markdown file containing the story topic", default=""
    )
    parser.add_argument(
        "--duration", type=float, dest="duration", help="Override total duration (minutes)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without generating video")
    parser.add_argument(
        "--fast-dry-run",
        action="store_true",
        help="Skip LLM script generation too (stub scripts, no TTS/images/video)",
    )
    parser.add_argument("--no-resume", action="store_true", help="Start fresh (ignore checkpoints)")
    parser.add_argument(
        "--project",
        default=None,
        help="Name of the project series to load from projects/ directory",
    )
    parser.add_argument(
        "--series",
        action="store_true",
        help="Resume series without re-consultation (reuses previous config)",
    )
    args = parser.parse_args()

    if args.file:
        file_path = Path(args.file)
        full_content = file_path.read_text(encoding="utf-8").strip()
        topic_text = file_path.stem.replace("_", " ").replace("-", " ")
        content_text = full_content
        print(
            f"[FILE] Loaded: {file_path.name} ({len(content_text)} chars, ~{len(content_text.split())} words)"
        )
    else:
        topic_text = args.topic
        content_text = None

    if not topic_text:
        parser.error("You must provide either --topic or --file")

    print("\n" + "=" * 60)

    try:
        result = run_long_pipeline(
            topic=topic_text,
            project_name=args.project,
            resume=not args.no_resume,
            dry_run=args.dry_run or args.fast_dry_run,
            fast_dry_run=args.fast_dry_run,
            duration_min=args.duration,
            series_mode=args.series,
            content_text=content_text,
        )

        print("PIPELINE COMPLETE")
        print("=" * 60)
        print(f"Status: {result.get('status', 'unknown').upper()}")

        if result.get("status") in ("success", "error"):
            if result.get("output"):
                print(f"Output: {result.get('output')}")
            print(f"Segments: {result.get('segments')}")
            _dur = result.get("duration_s")
            if isinstance(_dur, (int, float)) and not isinstance(_dur, bool):
                print(f"Duration: {_dur:.1f}s")
            else:
                print(f"Duration: {_dur}")
            if result.get("status") == "error":
                _qc = result.get("quality", {})
                if _qc.get("issues"):
                    for _issue in _qc["issues"]:
                        print(f"  Quality issue: {_issue}")
        elif result.get("status") == "dry_run":
            print(f"Would generate: {result.get('segments')} segments")
            print(f"Output would be: {result.get('output')}")
        else:
            print(f"Error: {result.get('reason')}")

        print("=" * 60 + "\n")

        sys.exit(0 if result.get("status") in ["success", "dry_run"] else 1)

    except KeyboardInterrupt:
        print("\n[FAILED] Pipeline interrupted by user")
        try:
            log.info("Gracefully released GPU Image Generation models.")
        except Exception as e:
            log.debug(f"Error during graceful shutdown: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAILED] Fatal error: {e}")
        log.exception("Fatal error in pipeline")
        sys.exit(1)


if __name__ == "__main__":
    main()
