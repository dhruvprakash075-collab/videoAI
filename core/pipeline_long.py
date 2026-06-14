"""pipeline_long.py - Thin orchestrator for the Video.AI pipeline.

Task 1: split god module. The old pipeline_long.py was 149 KB / 2830 lines.
This new file is the slim entry point that:

  1. Loads config + Director state
  2. Calls core.pre_production.run_pre_production()
  3. Builds the per-segment loop via core.segment_runner.make_process_segment()
  4. Calls core.post_production.finalize_*() with the result

All heavy lifting is in:
  • core/pre_production.py    — Director research, analysis, consultation, decisions
  • core/segment_runner.py    — per-segment script/TTS/image/render loop
  • core/post_production.py   — final concat, thumbnail, chapters, manifest, QC

Backwards-compat re-exports keep test imports stable:
  from core.pipeline_long import _sanitize_narration, _evict_ollama_models, ...
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import importlib.util
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# ── Bootstrap: PYTHONPATH + telemetry suppression (matches old behavior) ──
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from utils.compatibility import apply_all_patches

    apply_all_patches()
except ImportError:
    pass

os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TELEMETRY_OPTOUT", "true")

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        _reconf = getattr(_stream, "reconfigure", None)
        if _reconf is not None:
            with contextlib.suppress(AttributeError, OSError):
                _reconf(encoding="utf-8")

os.environ.setdefault("TORCHDYNAMO_SUPPRESS_ERRORS", "1")
try:
    import torch as _torch

    _torch._dynamo.config.suppress_errors = True
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Concurrency scheduler (reused everywhere) ────────────────────────────
concurrency_path = os.path.join(os.path.dirname(__file__), "..", "utils", "concurrency.py")
_spec = importlib.util.spec_from_file_location("concurrency", concurrency_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load concurrency module from {concurrency_path}")
concurrency_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(concurrency_module)
global_scheduler = concurrency_module.global_scheduler

try:
    from utils.concurrency import crewai_lock as _crewai_lock
except Exception:
    _crewai_lock = threading.RLock()


# ── Re-exports for backwards compatibility (tests, TUI, etc.) ────────────
from core.pre_production import (
    _deep_merge,
    _sanitize_narration,  # noqa: F401  (used by tests)
    _seed_director_memory,
    format_chapters_time,  # noqa: F401
    format_time_hms,
    get_video_duration,  # noqa: F401
    plan_outline,
    run_pre_production,
    run_preflight_checks,
)
from core.segment_runner import (
    aggressive_vram_cleanup,
    build_retry_wrapper,
    evict_ollama_models,
    get_director_abort,
    log_vram_usage,
    make_process_segment,
    set_director_abort,
)

# Legacy aliases (old private names that tests/scripts still import)
_evict_ollama_models = evict_ollama_models
_log_vram_usage = log_vram_usage
_aggressive_vram_cleanup = aggressive_vram_cleanup
_director_aborted = get_director_abort


# ── Public Director abort control (TUI calls these) ─────────────────────


def _director_set_abort(val: bool = True) -> None:
    """Set the Director Mode abort flag (thread-safe)."""
    set_director_abort(val)


def request_cancel() -> None:
    """Public zero-coupling cancel hook for the TUI.

    Wraps set_director_abort(True) so the TUI never imports private globals.
    Remaining segments will skip; checkpoints are preserved (run stays resumable).
    """
    set_director_abort(True)


# ── Main pipeline entry point ────────────────────────────────────────────


def run_long_pipeline(
    topic: str,
    project_name: str | None = None,
    resume: bool = True,
    skip_rvc: bool = False,
    dry_run: bool = False,
    fast_dry_run: bool = False,
    duration_min: int | None = None,
    director_mode: bool = False,
    series_mode: bool = False,
    content_text: str | None = None,
    preview_mode: bool = False,
    words_per_segment: int | None = None,
    images_per_segment: int | None = None,
    segment_count: int | None = None,
    source_chunks: list | None = None,
) -> dict:
    """Main pipeline: story outline → script → TTS → images → video.

    Thin orchestrator: delegates to pre_production / segment_runner / post_production.

    When ``source_chunks`` is provided, the per-segment writer short-circuits
    to each chunk's text (no LLM call) and the critic auto-approves. The
    pre-production phase still runs to derive a top-level story arc, but
    individual segment scripts come verbatim from the source.
    """
    from agents.ui_state import UIState
    from core.main import create_director, create_writer
    from utils import _safe_filename, load_config, setup_run_logging
    from utils.checkpoint import build_checkpoint_manager
    from utils.retry_manager import patch_retries

    UIState.reset_run(topic)
    setup_run_logging(Path("logs") / _safe_filename(topic))
    _run_start = time.time()

    # Reset abort flag so a run after a cancel/quit starts cleanly
    set_director_abort(False)

    config = load_config(project_name=project_name)

    # ── Assemble CLI structural locks (only include explicitly-set flags) ──
    _cli_flags: dict[str, Any] = {}
    if (
        duration_min is not None
        and isinstance(duration_min, (int, float))
        and not isinstance(duration_min, bool)
    ):
        _cli_flags["total_duration_min"] = duration_min
    if (
        words_per_segment is not None
        and isinstance(words_per_segment, int)
        and not isinstance(words_per_segment, bool)
    ):
        _cli_flags["words_per_segment"] = words_per_segment
    if (
        images_per_segment is not None
        and isinstance(images_per_segment, int)
        and not isinstance(images_per_segment, bool)
    ):
        _cli_flags["images_per_segment"] = images_per_segment
    if (
        segment_count is not None
        and isinstance(segment_count, int)
        and not isinstance(segment_count, bool)
    ):
        _cli_flags["segment_count"] = segment_count

    # ── Pre-Production ──
    config_overlay = run_pre_production(
        topic,
        config,
        skip_consultation=series_mode,
        content_text=content_text,
        project_name=project_name,
        cli_flags=_cli_flags,
        run_mode="project" if project_name else "one_time",
    )
    config = _deep_merge(config, config_overlay if isinstance(config_overlay, dict) else {})

    # Normalize TTS engine
    from audio.audio_proxy import normalize_tts_engine as _normalize_tts_engine

    _raw_tts_engine = config.get("tts", {}).get("engine", "supertonic")
    _normalized_engine = _normalize_tts_engine(_raw_tts_engine)
    if _normalized_engine != _raw_tts_engine:
        log.warning(
            f"[PIPELINE] TTS engine {_raw_tts_engine!r} from vision doc/overlay "
            f"normalized to {_normalized_engine!r}"
        )
    config.setdefault("tts", {})["engine"] = _normalized_engine
    if isinstance(config_overlay, dict):
        config_overlay.setdefault("tts", {})["engine"] = _normalized_engine

    # Preflight + retries + checkpoint + memory seeding
    run_preflight_checks(config, dry_run=dry_run)
    patch_retries()
    cp_mgr = build_checkpoint_manager(config)
    _seed_director_memory(topic, config_overlay, config)

    from agents.director_agent import DirectorAgent

    director_agent_instance = DirectorAgent(config)
    writer_agent = create_writer(config)
    try:
        director_agent_instance._sync_memory_to_worldstate(topic, config)
    except Exception as e:
        log.debug(f"Memory-to-WorldState sync failed: {e}")

    from memory import StoryMemory

    mem = StoryMemory(
        Path(config["memory"].get("memory_file", "studio_checkpoints/story_memory.json"))
    )
    if (
        duration_min is not None
        and isinstance(duration_min, (int, float))
        and not isinstance(duration_min, bool)
    ):
        config["video"]["total_duration_min"] = duration_min

    total = config["video"]["total_duration_min"]
    seg_min = config["video"]["segment_duration_min"]
    if seg_min == 0:
        raise ValueError(f"segment_duration_min must be > 0, got {seg_min}")

    # Read structural decisions from DecisionRecord
    _rec = None
    try:
        from memory.blackboard import get_blackboard

        _bb = get_blackboard(config, topic_slug=_safe_filename(topic))
        _rec = _bb.read_decision()
    except Exception as _e:
        log.warning(f"[PIPELINE] Could not read DecisionRecord from blackboard: {_e}")

    if _rec is not None:
        n_segs = int(_rec.segment_count.value or 1)
        words_per_seg = int(
            _rec.words_per_segment.value or config.get("script", {}).get("words_per_segment", 130)
        )
        _seg_count_locked = bool(_rec.segment_count.locked)
        log.info(
            f"[PIPELINE] Using DecisionRecord — "
            f"segments={n_segs} ({_rec.segment_count.provenance}, locked={_seg_count_locked}), "
            f"words/seg={words_per_seg} ({_rec.words_per_segment.provenance})"
        )
        config["video"]["total_duration_min"] = _rec.total_duration_min.value
    else:
        import math as _math
        n_segs = max(1, _math.ceil(total / seg_min))
        words_per_seg = config.get("script", {}).get("words_per_segment", 130)
        _seg_count_locked = False
        log.info(
            f"[PIPELINE] No DecisionRecord found — "
            f"falling back to arithmetic: segments={n_segs}, words/seg={words_per_seg}"
        )

    out_base = Path("studio_outputs") / _safe_filename(topic) / "segments"
    out_base.mkdir(parents=True, exist_ok=True)
    tts_cfg = config.get("tts", {})
    mp4s: list[Path | None] = [None] * n_segs
    mp4s_lock = threading.Lock()

    # Master portraits are generated lazily on first character appearance
    # (triggered inside image_gen._bonsai()). No upfront session needed.
    completed_segs_counter_holder = [0]
    completed_segs_lock = threading.Lock()

    # WorldState init
    from memory import WorldState

    ck_dir = Path(config.get("checkpoint", {}).get("dir", "studio_checkpoints"))
    world_state = WorldState(topic=topic, checkpoint_dir=ck_dir)
    if not resume:
        try:
            _ws_file = ck_dir / f"world_state_{_safe_filename(topic)}.json"
            if _ws_file.exists():
                _ws_file.unlink()
                log.info("[WorldState] Cleared stale world state (--no-resume)")
            world_state = WorldState(topic=topic, checkpoint_dir=ck_dir)
        except Exception as _ws_clear_err:
            log.warning(f"[WorldState] Could not clear stale state: {_ws_clear_err}")
    log.info("[WorldState] Initialized")

    # ContextWindowManager init
    try:
        from utils.context_manager import ContextWindowManager
    except ImportError:
        ContextWindowManager = None
    ctx_mgr = ContextWindowManager() if ContextWindowManager else None
    if ctx_mgr:
        log.info("[CtxMgr] Context Window Manager active (budget: 6000 tokens)")

    log_vram_usage("Pipeline Start")

    est_dry_s = n_segs * 25 if not fast_dry_run else n_segs * 20
    est_total_s = n_segs * (120 + 60 + 30)
    log.info("┌─────────────────────────────────────────┐")
    log.info("│  Estimated Run Time                     │")
    log.info(f"│  Segments:    {n_segs:<26}│")
    if dry_run:
        label = "Fast-dry-run" if fast_dry_run else "Dry-run"
        log.info(f"│  {label}: ~{format_time_hms(est_dry_s):<25}│")
    else:
        log.info(f"│  TTS/segment: ~2.0 min  → {n_segs * 2:>2} min total  │")
        log.info(f"│  SD/segment:  ~1.0 min  → {n_segs * 1:>2} min total  │")
        log.info(f"│  Assembly:    ~0.5 min  → {round(n_segs * 0.5):>2} min total│")

    # ── Story outline ──
    director_agent = create_director(config)
    outline = plan_outline(topic, n_segs, config, director_agent, cp_mgr, resume)

    if len(outline) != n_segs:
        if _seg_count_locked:
            if len(outline) > n_segs:
                log.warning(
                    f"Outline produced {len(outline)} segments but segment_count is "
                    f"LOCKED to {n_segs} — truncating outline to honor the lock."
                )
                outline = outline[:n_segs]
            else:
                log.warning(
                    f"Outline produced only {len(outline)} segments but segment_count is "
                    f"LOCKED to {n_segs} — using the {len(outline)} planned segment(s) "
                    f"(Director could not expand). Adjusting to {len(outline)}."
                )
                n_segs = len(outline)
                mp4s = [None] * n_segs
        else:
            log.warning(
                f"Outline length ({len(outline)}) differs from requested ({n_segs}). Adjusting pipeline length."
            )
            n_segs = len(outline)
            mp4s = [None] * n_segs
    log.info(f"│  Total:       ~{format_time_hms(est_total_s):<25}│")
    log.info("└─────────────────────────────────────────┘")

    try:
        from agents.director_agent import UIState as _UIState

        _UIState.set_progress(total=n_segs)
    except Exception:
        pass

    # Cap images per segment
    _max_imgs = config.get("script", {}).get("max_images_per_segment", 10)
    for seg_plan in outline:
        _ni = seg_plan.get("num_images", config["script"].get("default_images_per_segment", 6))
        if _ni > _max_imgs:
            log.info(f"  Seg {seg_plan.get('seg', '?')}: capping images {_ni} → {_max_imgs}")
            seg_plan["num_images"] = _max_imgs
            cp_list = seg_plan.get("char_presence")
            if isinstance(cp_list, list) and len(cp_list) > _max_imgs:
                seg_plan["char_presence"] = cp_list[:_max_imgs]

    # P3: enforce minimum environment/world frames
    _env_ratio = config.get("visual", {}).get("environment_frame_ratio", 0.4)
    for seg_plan in outline:
        cp_list = seg_plan.get("char_presence")
        if not isinstance(cp_list, list) or not cp_list:
            continue
        n_frames = len(cp_list)
        n_env_needed = max(1, int(n_frames * _env_ratio))
        env_indices = [
            j
            for j, frame in enumerate(cp_list)
            if isinstance(frame, dict) and (max(frame.values()) if frame else 0) <= 0.2
        ]
        if len(env_indices) < n_env_needed:
            sorted_by_weight = sorted(
                range(n_frames),
                key=lambda j: (
                    max(cp_list[j].values()) if isinstance(cp_list[j], dict) and cp_list[j] else 0
                ),
            )
            for j in sorted_by_weight:
                if len(env_indices) >= n_env_needed:
                    break
                if j not in env_indices:
                    if isinstance(cp_list[j], dict):
                        cp_list[j] = {k: min(0.1, v) for k, v in cp_list[j].items()}
                    else:
                        cp_list[j] = {}
                    env_indices.append(j)
        for _force_idx in [0, n_frames - 1]:
            if isinstance(cp_list[_force_idx], dict) and cp_list[_force_idx]:
                cp_list[_force_idx] = {k: min(0.15, v) for k, v in cp_list[_force_idx].items()}
            else:
                cp_list[_force_idx] = {}

    # Segment Preview (dry-run)
    if not dry_run and n_segs > 1:
        log.info("=" * 60)
        log.info("  DIRECTOR PLAN — Segment Breakdown")
        log.info("=" * 60)
        for idx, seg in enumerate(outline):
            seg_num = seg.get("seg", idx + 1)
            title = seg.get("title", f"Part {seg_num}")
            mood = seg.get("mood", "neutral")
            words = seg.get("target_word_count", words_per_seg)
            images = seg.get("num_images", config["script"].get("default_images_per_segment", 6))
            log.info(
                f"  [{seg_num:2d}] {title[:40]:40s} | {mood:12s} | {words:>4d} words | {images:>2d} images"
            )

        log.info("-" * 60)
        log.info(
            f"  Total segments: {n_segs} | "
            f"Estimated total: {est_total_s:.0f}s (~{est_total_s / 60:.1f} min) | "
            f"Estimated render: {format_time_hms(n_segs * 3.5 * 60) if not dry_run else '0s'}"
        )
        log.info("=" * 60)

    # ── Build process_segment closure (once, inside the executor block) ──
    _cfg_workers = config.get("performance", {}).get("max_workers", 1)
    max_workers = min(n_segs, _cfg_workers)
    log.info(f"Workers: {max_workers} (from config performance.max_workers={_cfg_workers})")

    _max_seg_retries = int(config.get("performance", {}).get("max_segment_retries", 2))
    _seg_retry_counts: dict = {}

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as _shared_prompt_executor:
        # Build the per-segment closure once, with the shared prompt executor
        # captured (it needs the executor for parallel image-prompt and
        # translation tasks). Building twice used to be a footgun.
        process_segment = make_process_segment(
            topic=topic,
            config=config,
            outline=outline,
            n_segs=n_segs,
            out_base=out_base,
            tts_cfg=tts_cfg,
            cp_mgr=cp_mgr,
            world_state=world_state,
            mem=mem,
            ctx_mgr=ctx_mgr,
            director_agent_instance=director_agent_instance,
            writer_agent=writer_agent,
            resume=resume,
            dry_run=dry_run or fast_dry_run,
            fast_dry_run=fast_dry_run,
            director_mode=director_mode,
            preview_mode=preview_mode,
            skip_rvc=skip_rvc,
            words_per_seg=words_per_seg,
            seg_min=seg_min,
            shared_prompt_executor=_shared_prompt_executor,
            global_scheduler=global_scheduler,
            _crewai_lock=_crewai_lock,
            crewai_lock=_crewai_lock,
            completed_segs_counter_holder=completed_segs_counter_holder,
            completed_segs_lock=completed_segs_lock,
            mp4s=mp4s,
            mp4s_lock=mp4s_lock,
            run_start_ts=_run_start,
            source_chunks=source_chunks,
        )
        _process_segment_with_budget = build_retry_wrapper(
            process_segment,
            _max_seg_retries,
            0,
            _seg_retry_counts,
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            _staged = config.get("performance", {}).get("staged_loop", False)
            _lookahead = int(config.get("performance", {}).get("lookahead_segments", 1))

            if _staged:
                log.info(
                    f"[C1] Staged loop enabled (lookahead={_lookahead}). "
                    f"Running segments in batches with one evict per batch."
                )
                _seg_indices = list(range(1, n_segs + 1))
                _batch_size = max(1, _lookahead)
                _batches = [
                    _seg_indices[k : k + _batch_size]
                    for k in range(0, len(_seg_indices), _batch_size)
                ]

                for _batch in _batches:
                    if get_director_abort():
                        break
                    log.debug(f"[C1] Evicting Ollama before batch {_batch}")
                    evict_ollama_models(config, reason="C1 staged batch")

                    _batch_futures = {
                        executor.submit(_process_segment_with_budget, _bi): _bi for _bi in _batch
                    }
                    for _bf in concurrent.futures.as_completed(_batch_futures):
                        _bseg = _batch_futures[_bf]
                        try:
                            _bf.result()
                        except Exception as _be:
                            log.error(f"Segment {_bseg} execution failed: {_be}", exc_info=True)
            else:
                futures = {
                    executor.submit(_process_segment_with_budget, idx): idx
                    for idx in range(1, n_segs + 1)
                }
                for future in concurrent.futures.as_completed(futures):
                    seg_idx = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        log.error(f"Segment {seg_idx} execution failed: {e}", exc_info=True)

    mp4s = [p for p in mp4s if p is not None]

    # ── Final concatenation ──
    if not mp4s:
        log.error("No segments generated")
        return {"status": "error", "reason": "no segments"}

    if len(mp4s) != n_segs:
        log.warning(
            f"ENDURANCE MODE: Only {len(mp4s)}/{n_segs} segments generated successfully. "
            f"Concatenating available segments to salvage the run."
        )

    wall_time_s = time.time() - _run_start
    from core.post_production import finalize_dry_run, finalize_production

    try:
        if dry_run:
            return finalize_dry_run(topic, config, outline, n_segs, mp4s, wall_time_s)
        return finalize_production(topic, config, outline, n_segs, mp4s, wall_time_s)
    finally:
        # B16: stop persistent TTS workers so models are released
        try:
            from audio.audio_proxy import (
                shutdown_omnivoice_worker,
                shutdown_supertonic_worker,
            )
            shutdown_supertonic_worker()
            shutdown_omnivoice_worker()
        except Exception as _sw_err:
            log.debug(f"TTS worker shutdown error: {_sw_err}")


# ── Async variant (kept for compat) ──────────────────────────────────────


def run_long_pipeline_async(topic: str, config: dict, **kwargs):
    """Runs pre-production and returns config overlay."""
    from utils import _safe_filename, setup_run_logging

    setup_run_logging(Path("logs") / _safe_filename(topic))
    config_overlay = run_pre_production(topic, config, **kwargs)
    config = _deep_merge(config, config_overlay)
    return {"status": "ok", "topic": topic, "overlay": config_overlay}


# ── CLI entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
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
    parser.add_argument("--skip-rvc", action="store_true", help="Skip RVC voice conversion")
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
    parser.add_argument(
        "--director-mode",
        action="store_true",
        help="Pause after each script generation for human review",
    )

    args = parser.parse_args()

    if args.file:
        from pathlib import Path

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
            skip_rvc=args.skip_rvc,
            dry_run=args.dry_run or args.fast_dry_run,
            fast_dry_run=args.fast_dry_run,
            duration_min=args.duration,
            director_mode=args.director_mode,
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
            from video.image_gen.image_gen import unload_bonsai_pipeline
            from video.image_gen.ip_adapter import unload_ip_adapter

            unload_bonsai_pipeline()
            unload_ip_adapter()
            log.info("Gracefully released GPU Image Generation models.")
        except Exception as e:
            log.debug(f"Error during graceful shutdown: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAILED] Fatal error: {e}")
        log.exception("Fatal error in pipeline")
        sys.exit(1)
