#!/usr/bin/env python3
"""
Bootstrap script for Video.AI pipeline.

Applies compatibility patches and environment setup before importing pipeline modules.
"""

import os
import sys
from pathlib import Path


def bootstrap():
    """Apply patches and environment setup before any imports."""

    # Guard: ensure we run inside the project venv
    if not hasattr(sys, "real_prefix") and not (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix):
        print("ERROR: This pipeline must run inside the project virtual environment.")
        print("Use: .\\venv\\Scripts\\python.exe bootstrap_pipeline.py [args]")
        sys.exit(1)

    # Add current directory to Python path
    current_dir = Path(__file__).parent
    sys.path.insert(0, str(current_dir))

    # Register signal handlers for graceful shutdown. Safe to call before any
    # pipeline import; the registered hooks (set later in run_pipeline_with_args)
    # will run when SIGINT / SIGTERM / SIGBREAK is received.
    try:
        from utils.shutdown import register_shutdown_handlers

        register_shutdown_handlers()
    except Exception as e:
        print(f"Warning: Could not register shutdown handlers: {e}")

    # Apply compatibility patches (encoding fixes, dependency checks)
    try:
        from utils.compatibility import apply_all_patches

        apply_all_patches()
    except ImportError as e:
        print(f"Warning: Could not apply compatibility patches: {e}")
    except Exception as e:
        print(f"Warning: Error applying compatibility patches: {e}")

    # Disable CrewAI telemetry
    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
    os.environ["CREWAI_TELEMETRY_OPTOUT"] = "true"

    # Force UTF-8 encoding for rich/tqdm on Windows to prevent [Errno 22]
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # Disable rich legacy Windows console rendering (uses Win32 API that fails with Unicode)
    os.environ.setdefault("TERM", "xterm-256color")

    # Patch rich to catch Win32 console write errors gracefully.
    # rich's legacy Windows renderer is LegacyWindowsTerm (in rich._win32_console);
    # its write_text/write_styled call into the Win32 API and can raise OSError
    # ([Errno 22]) on Unicode output. Wrap both so a failure falls back to a plain
    # write instead of crashing the run.
    if sys.platform == "win32":
        try:
            from rich._win32_console import LegacyWindowsTerm as _LWT

            def _make_safe(orig):
                def _safe(self, text, *args, **kwargs):
                    try:
                        orig(self, text, *args, **kwargs)
                    except OSError:
                        # Fallback: write plain text without Win32 styling
                        try:
                            self._file.write(text)
                            self._file.flush()
                        except Exception:
                            pass

                return _safe

            _LWT.write_text = _make_safe(_LWT.write_text)
            _LWT.write_styled = _make_safe(_LWT.write_styled)
        except (ImportError, AttributeError):
            pass

    # Fix Windows console encoding
    if sys.platform == "win32":
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    # Ensure the bundled FFmpeg is discoverable on PATH.
    # Preflight checks (and several ffmpeg/ffprobe subprocess calls) require
    # ffmpeg on PATH; the repo ships its own build that operators may not have
    # added manually. Prepend it so a fresh setup works out of the box.
    try:
        for _ff in current_dir.glob("ffmpeg-*/**/bin/ffmpeg.exe"):
            _ff_bin = str(_ff.parent)
            if _ff_bin not in os.environ.get("PATH", ""):
                os.environ["PATH"] = _ff_bin + os.pathsep + os.environ.get("PATH", "")
                print(f"Added bundled FFmpeg to PATH: {_ff_bin}")
            break
    except Exception as e:
        print(f"Warning: Could not wire bundled FFmpeg into PATH: {e}")


def _load_and_split_source(source_arg: str, args, pf_config: dict) -> tuple:
    """Load a source document and split it into per-segment chunks.

    Args:
        source_arg: The raw --source value (file path or URL).
        args: Parsed argparse args (uses --segment-count, --words-per-segment).
        pf_config: Preflight config (used to read script.words_per_segment).

    Returns:
        (source_chunks, topic_text, content_text)
          - source_chunks: List[SegmentChunk] for the per-segment writer.
          - topic_text: Document title (used as the YouTube video topic).
          - content_text: Full document text (passed to director for context).

    Raises:
        SystemExit(1) on any load/split failure.
    """
    from utils.source_loader import SourceLoaderError, load_source
    from utils.source_splitter import SourceSplitterError, split_source

    print(f"\n[SOURCE] Loading: {source_arg}")
    try:
        source_doc = load_source(source_arg, pf_config or {})
    except SourceLoaderError as e:
        print(f"[SOURCE] Load failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[SOURCE] Unexpected load error: {e}")
        sys.exit(1)

    print(
        f"[SOURCE] Loaded: {source_doc.source_type}, "
        f"~{source_doc.word_count} words, language={source_doc.language}"
    )

    topic_text = (
        source_doc.metadata.get("title")
        or source_doc.metadata.get("front_matter", {}).get("title")
        or (
            Path(source_arg).stem.replace("_", " ").replace("-", " ")
            if not source_arg.startswith(("http://", "https://"))
            else source_doc.metadata.get("url", "Source Document")
        )
    )
    if not topic_text.strip():
        topic_text = "Source Document"
    print(f"[SOURCE] Topic: {topic_text}")

    target_words = int((pf_config or {}).get("script", {}).get("words_per_segment", 100))
    if getattr(args, "words_per_segment", None):
        target_words = int(args.words_per_segment)

    n_segments = getattr(args, "segment_count", None)
    if n_segments is None:
        n_segments = max(1, (source_doc.word_count + target_words - 1) // target_words)
    print(f"[SOURCE] Target: {n_segments} segments @ ~{target_words} words/segment")

    try:
        chunks = split_source(source_doc, n_segments, pf_config or {})
    except SourceSplitterError as e:
        print(f"[SOURCE] Split failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[SOURCE] Unexpected split error: {e}")
        sys.exit(1)

    strategy = (pf_config or {}).get("source", {}).get("split_strategy", "by_word_count")
    print(f"[SOURCE] Split done: {len(chunks)} chunks via '{strategy}' strategy")
    if getattr(args, "segment_count", None) is None:
        args.segment_count = len(chunks)
        print(f"[SOURCE] Forcing --segment-count={len(chunks)} to match source chunk count")
    elif len(chunks) != args.segment_count:
        print(
            f"[SOURCE] WARNING: --segment-count={args.segment_count} differs from "
            f"source chunk count ({len(chunks)}). Per-segment source chunks will cycle; "
            f"the Director's plan drives n_segs."
        )
    return chunks, topic_text, source_doc.text


def run_pipeline_with_args():
    """Run the pipeline with command line arguments."""
    import argparse

    parser = argparse.ArgumentParser(description="Video.AI Pipeline")
    parser.add_argument("--topic", help="Video topic/title", default="")
    parser.add_argument(
        "--file", help="Path to text or markdown file containing the story", default=""
    )
    parser.add_argument(
        "--duration",
        type=float,
        dest="duration",
        help="Override total duration (minutes); accepts fractional values (e.g. 2.5)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without generating video")
    parser.add_argument("--no-resume", action="store_true", help="Start fresh (ignore checkpoints)")
    parser.add_argument("--skip-rvc", action="store_true", help="Skip RVC voice conversion")
    parser.add_argument(
        "--project", default=None, help="Project series name from projects/ directory"
    )
    parser.add_argument(
        "--series", action="store_true", help="Resume series without re-consultation"
    )
    parser.add_argument(
        "--director-mode", action="store_true", help="Pause after each script for human review"
    )
    parser.add_argument(
        "--run-mode",
        choices=["project", "one_time"],
        default="one_time",
        help="project: persist continuity under --project; one_time: isolated run (default)",
    )
    parser.add_argument(
        "--eval-models",
        action="store_true",
        help="Run the model eval harness (sample images + TTS clip) without a full video",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Pause after the first segment for approval before producing the full video",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip the preflight readiness checks (Ollama/VRAM/disk/ffmpeg)",
    )
    parser.add_argument(
        "--preflight-only", action="store_true", help="Run preflight and exit (no video generation)"
    )
    parser.add_argument(
        "--words-per-segment",
        type=int,
        dest="words_per_segment",
        default=None,
        help="Lock words per segment (≈130 words ≈ 1 min narration). Overrides Director/Writer.",
    )
    parser.add_argument(
        "--images-per-segment",
        type=int,
        dest="images_per_segment",
        default=None,
        help="Lock the exact number of images per segment. Overrides Director/Writer.",
    )
    parser.add_argument(
        "--segment-count",
        type=int,
        dest="segment_count",
        default=None,
        help="Lock the total number of segments. Overrides Director/Writer.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="A6: auto-accept all Director consultations without prompting (for unattended runs)",
    )
    parser.add_argument(
        "--topics-file",
        dest="topics_file",
        default=None,
        help="D4: path to a text file with one topic per line; runs each sequentially",
    )
    parser.add_argument(
        "--source",
        dest="source",
        default=None,
        help="Phase 4: path or URL to source material (.txt/.md/.pdf/.docx). "
        "The pipeline loads it, splits it into per-segment chunks, and uses "
        "each chunk as the segment script (no LLM call for the body). The "
        "document title becomes the video topic. Works with --words-per-segment "
        "and --segment-count overrides.",
    )

    args = parser.parse_args()

    _pf_config = None

    # ── Preflight ────────────────────────────────────────────────────────
    # Run readiness checks (Ollama, VRAM, disk, ffmpeg) before doing anything
    # expensive. Skippable via --skip-preflight for hot-iteration debugging.
    if not args.skip_preflight:
        try:
            from config import load_config

            _pf_config = load_config()
        except Exception as e:
            print(f"Warning: Could not load config for preflight: {e}")
            _pf_config = {}
        try:
            from utils.preflight import run_preflight

            _pf_result = run_preflight(_pf_config, fail_fast=False)
        except Exception as e:
            print(f"Warning: Preflight crashed ({e}) — continuing")
            _pf_result = None
        if _pf_result is not None and args.preflight_only:
            sys.exit(0 if _pf_result.all_ok else 1)
        if _pf_result is not None and not _pf_result.all_ok and not args.dry_run:
            print("\nPreflight FAILED. Re-run with --skip-preflight to bypass (not recommended).")
            sys.exit(1)

    # ── Register Ollama-eviction shutdown hook ──────────────────────────
    # Ensures any loaded LLM is force-evicted (keep_alive=0) when the user
    # Ctrl-C's a long generation. Without this, an Ollama model can stay
    # resident and starve Stable Diffusion of VRAM on the next run.
    if not args.skip_preflight and not args.dry_run:
        try:
            from utils.shutdown import register_cleanup_hook

            def _shutdown_evict():
                try:
                    from core.segment_runner import evict_ollama_models

                    evict_ollama_models(_pf_config, reason="graceful shutdown")
                except Exception as e:
                    print(f"Warning: Ollama eviction on shutdown failed: {e}")

            _shutdown_evict.__name__ = "evict_ollama_on_shutdown"
            register_cleanup_hook(_shutdown_evict)
        except Exception as e:
            print(f"Warning: Could not register shutdown eviction hook: {e}")

    try:
        from core.pipeline_long import run_long_pipeline

        # Handle file input
        if args.file:
            file_path = Path(args.file)
            content_text = file_path.read_text(encoding="utf-8").strip()
            topic_text = file_path.stem.replace("_", " ").replace("-", " ")
            print(
                f"[FILE] Loaded: {file_path.name} ({len(content_text)} chars, ~{len(content_text.split())} words)"
            )
        else:
            topic_text = args.topic
            if topic_text and topic_text.lower() == "auto":
                try:
                    from utils.topic_researcher import brainstorm_topic

                    topic_text = brainstorm_topic(_pf_config if not args.skip_preflight else None)
                except Exception as e:
                    print(f"Warning: Auto-topic researcher failed: {e}")
                    topic_text = "The Mysteries of the Deep Ocean"
            content_text = None

        # Handle --source (Phase 4: source-path ingestion)
        source_chunks = None
        if getattr(args, "source", None):
            source_chunks, topic_text, content_text = _load_and_split_source(
                args.source, args, _pf_config
            )

        if not topic_text and not args.eval_models and not getattr(args, "topics_file", None):
            parser.error(
                "You must provide either --topic, --file, or --source (or --eval-models, or --topics-file)"
            )

        # A6: wire --yes flag to UIState.auto_accept before pipeline starts
        if getattr(args, "yes", False):
            try:
                from agents.director_agent import UIState

                UIState.auto_accept = True
                print(
                    "[--yes] Auto-accept mode enabled — all Director consultations will use defaults"
                )
            except Exception:
                pass

        print("\n" + "=" * 60)

        # Handle --eval-models (no full video, just sample generation)
        if args.eval_models:
            from utils.model_eval import run_eval

            run_eval()
            sys.exit(0)

        # D4: Batch mode — run multiple topics from a file sequentially
        if getattr(args, "topics_file", None):
            _topics_path = Path(args.topics_file)
            if not _topics_path.exists():
                print(f"[BATCH] Topics file not found: {_topics_path}")
                sys.exit(1)
            _raw_lines = _topics_path.read_text(encoding="utf-8").splitlines()
            _topics = [l.strip() for l in _raw_lines if l.strip() and not l.strip().startswith("#")]
            if not _topics:
                print("[BATCH] No topics found in file (blank lines and # comments are ignored)")
                sys.exit(1)
            print(f"[BATCH] Running {len(_topics)} topics from {_topics_path.name}")
            import json as _bjson
            import time as _btime

            _batch_report = []
            _batch_out = Path("studio_outputs") / "batch_report.json"
            for _bi, _btopic in enumerate(_topics, 1):
                print(f"\n[BATCH {_bi}/{len(_topics)}] Topic: {_btopic}")
                _bt_start = _btime.time()
                try:
                    _bres = run_long_pipeline(
                        topic=_btopic,
                        project_name=args.project,
                        resume=not args.no_resume,
                        skip_rvc=args.skip_rvc,
                        dry_run=args.dry_run,
                        duration_min=args.duration,
                        director_mode=args.director_mode,
                        series_mode=args.series,
                        preview_mode=args.preview,
                        words_per_segment=args.words_per_segment,
                        images_per_segment=args.images_per_segment,
                        segment_count=args.segment_count,
                        source_chunks=source_chunks,
                    )
                    _bwall = round(_btime.time() - _bt_start, 1)
                    try:
                        from agents.director_agent import UIState as _BUIS

                        _bdeg = len(_BUIS.degradations)
                    except Exception:
                        _bdeg = 0
                    _batch_report.append(
                        {
                            "topic": _btopic,
                            "status": _bres.get("status", "unknown"),
                            "output": _bres.get("output"),
                            "degradations": _bdeg,
                            "wall_time_s": _bwall,
                        }
                    )
                    print(f"[BATCH {_bi}] Done: {_bres.get('status')} in {_bwall:.0f}s")
                except Exception as _be:
                    _bwall = round(_btime.time() - _bt_start, 1)
                    _batch_report.append(
                        {
                            "topic": _btopic,
                            "status": "error",
                            "error": str(_be)[:200],
                            "wall_time_s": _bwall,
                        }
                    )
                    print(f"[BATCH {_bi}] FAILED: {_be}")
            _batch_out.parent.mkdir(parents=True, exist_ok=True)
            _batch_out.write_text(
                _bjson.dumps(_batch_report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"\n[BATCH] Complete. Report: {_batch_out}")
            _ok = sum(1 for r in _batch_report if r.get("status") in ("success", "dry_run"))
            print(f"[BATCH] {_ok}/{len(_topics)} succeeded")
            sys.exit(0 if _ok == len(_topics) else 1)

        try:
            result = run_long_pipeline(
                topic=topic_text,
                project_name=args.project,
                resume=not args.no_resume,
                skip_rvc=args.skip_rvc,
                dry_run=args.dry_run,
                duration_min=args.duration,
                director_mode=args.director_mode,
                series_mode=args.series,
                content_text=content_text,
                preview_mode=args.preview,
                words_per_segment=args.words_per_segment,
                images_per_segment=args.images_per_segment,
                segment_count=args.segment_count,
                source_chunks=source_chunks,
            )

            print("PIPELINE COMPLETE")
            print("=" * 60)
            print(f"Status: {result.get('status', 'unknown').upper()}")

            if result.get("status") == "success":
                print(f"Output: {result.get('output')}")
                print(f"Segments: {result.get('segments')}")
                print(f"Duration: {result.get('duration_s'):.1f}s")
            elif result.get("status") == "dry_run":
                print(f"Would generate: {result.get('segments')} segments")
                print(f"Output would be: {result.get('output')}")
            else:
                print(f"Error: {result.get('reason')}")

            print("=" * 60 + "\n")
            sys.exit(0 if result.get("status") in ["success", "dry_run"] else 1)

        except KeyboardInterrupt:
            print("\n[FAILED] Pipeline interrupted by user")
            sys.exit(1)
        except Exception as e:
            print(f"\n[FAILED] Fatal error: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)

    except ImportError as e:
        print(f"\n[FAILED] Could not import pipeline modules: {e}")
        print("Check that all dependencies are installed: pip install -r requirements.txt")
        sys.exit(1)


if __name__ == "__main__":
    print("Video.AI Pipeline Bootstrap")
    print("=" * 40)

    bootstrap()

    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {sys.platform}")
    print("=" * 40)

    run_pipeline_with_args()
