"""pre_production.py - Pre-production phase: Director research, analysis, consultation.

Extracted from pipeline_long.py (Task 1: split god module). Owns everything that
runs ONCE before the per-segment loop:

  • Pre-flight health checks (Ollama, FFmpeg, TTS, disk)
  • Director research + analysis + user consultation
  • Story outline (planning)
  • LoRA Studio Session (upfront character face-lock training)
  • Director memory seeding (StoryMemory, WorldState, PermanentMemoryLog)
  • DecisionRecord build + persist to blackboard
  • Config overlay save for debugging/series reuse

This module NEVER touches Stable Diffusion, TTS, FFmpeg, or the per-segment loop.
It is pure LLM/config/state work, so it can be unit-tested in isolation.
"""

from __future__ import annotations

import json as _json
import logging
import os
from pathlib import Path
from typing import Any, cast

from utils import _safe_filename

# ── moved verbatim to utils/narration_sanitize.py / utils/time_format.py ──
from utils.narration_sanitize import (  # noqa: F401
    _normalize_hindi_for_tts,
    _reject_unsafe_narration,
    _sanitize_narration,
)
from utils.time_format import (  # noqa: F401
    format_chapters_time,
    format_time_hms,
    get_video_duration,
)

log = logging.getLogger(__name__)


# ── moved verbatim to utils/deep_merge.py / core/preflight.py ──
# ── moved verbatim to core/director_memory.py ──
from core.director_memory import _seed_director_memory  # noqa: F401
from core.preflight import run_preflight_checks  # noqa: F401 — re-export
from utils.deep_merge import _deep_merge

# ── Main pre-production entry point ───────────────────────────────────────


def run_pre_production(
    topic: str,
    config: dict,
    skip_consultation: bool = False,
    content_text: str | None = None,
    force_refresh: bool = False,
    project_name: str | None = None,
    cli_flags: dict | None = None,
    run_mode: str = "one_time",
) -> dict:
    """Run Director pre-production before any segment generation.

    Phases:
      1. Web research (Wikipedia + DuckDuckGo, no spoilers)
      2. Director analyzes story + research -> Vision Document
      3. User consultation (CLI multiple-choice prompts)
      4. Writer collaboration (scene breakdown suggestions)
      5. Build runtime config overlay
      6. Build & persist DecisionRecord to blackboard
      7. Seed StoryMemory + WorldState + PermanentMemoryLog

    Returns config_overlay dict that overrides config.yaml defaults.
    """
    from agents.director_agent import DirectorAgent

    log.info("=" * 60)
    log.info("  DIRECTOR PRE-PRODUCTION")
    log.info("=" * 60)

    director = DirectorAgent(config)
    director._force_refresh = force_refresh
    config_overlay: dict[str, Any] = {"video": {}}

    output_mode = os.environ.get("DIRECTOR_MODE", "full").lower()
    if output_mode not in ("full", "video-only", "voice-only"):
        output_mode = "full"
    log.info(f"[DIRECTOR] Output mode: {output_mode}")

    log.info("[DIRECTOR] Phase 0: Pre-flight decisions...")
    try:
        do_search = director.ask_search_online()
    except Exception as e:
        log.warning(f"[DIRECTOR] Consultation failed ({e}) — proceeding with defaults")
        do_search = False

    try:
        director.ask_cache_ttl()
    except Exception as e:
        log.warning(f"[DIRECTOR] Cache TTL consultation failed ({e}) — using defaults")

    log.info(f"[DIRECTOR] Web search: {'ON' if do_search else 'OFF'}")

    if content_text and content_text.strip():
        create_scratch, scratch_notes = False, ""
        log.info("[DIRECTOR] Story file provided — using it as-is (skipping create-from-scratch)")
    else:
        try:
            create_scratch, scratch_notes = director.ask_create_from_scratch(topic)
        except Exception as e:
            log.warning(
                f"[DIRECTOR] Create-from-scratch consultation failed ({e}) — proceeding with defaults"
            )
            create_scratch, scratch_notes = False, ""
        log.info(f"[DIRECTOR] Create from scratch: {'YES' if create_scratch else 'NO'}")

    _models = config.setdefault("models", {})
    if create_scratch:
        _chosen_writer = _models.get("writer_scratch", _models.get("writer", "cra-guided-7b"))
        log.info(
            f"[DIRECTOR] Writer mode: CREATE-FROM-SCRATCH → creative writer '{_chosen_writer}'"
        )
    else:
        _chosen_writer = _models.get("writer_adapt", _models.get("writer", "zephyr-writer"))
        log.info(f"[DIRECTOR] Writer mode: ADAPTATION → faithful writer '{_chosen_writer}'")
    _models["writer"] = _chosen_writer

    if create_scratch:
        story_text = director.invent_story(topic, scratch_notes)
        vision_doc = director.analyze_with_research(
            topic,
            {"combined_summary": story_text, "result_count": 0},
            content_text=content_text,
        )
        user_responses, writer_input = director.consult_on_config(vision_doc)
        config_overlay = director.produce_runtime_config(
            vision_doc, user_responses, writer_input, mode=output_mode
        )
        config_overlay["_invented_story"] = story_text
        _ov_chars = config_overlay.get("characters", {})
        _usable = {
            k: v
            for k, v in _ov_chars.items()
            if isinstance(v, dict) and len(str(v.get("description", "")).strip()) >= 30
        }
        if not _usable:
            log.warning(
                "[PRE-PROD] Scratch mode produced no characters with visual detail — "
                "keeping config.yaml characters for visual consistency"
            )
            config_overlay.pop("characters", None)
        else:
            config_overlay["characters"] = _usable
        log.info("[PRE-PROD] Original story created! Skipping web research.")

        from core.decision_record import build_and_persist_decision_record

        try:
            _scratch_extra = {}
            _scratch_dur = config_overlay.get("video", {}).get("total_duration_min")
            if _scratch_dur:
                _scratch_extra["total_duration_min"] = _scratch_dur
            config_overlay, _scratch_rec = build_and_persist_decision_record(
                director=director, topic=topic, config=config, config_overlay=config_overlay,
                vision_doc=vision_doc, writer_input=writer_input, run_mode=run_mode,
                project_name=project_name, cli_flags=cli_flags,
                extra_user_locks=_scratch_extra or None,
            )
            log.info(
                f"[PRE-PROD] Scratch DecisionRecord built and persisted — "
                f"segments={_scratch_rec.segment_count.value}, "
                f"duration={_scratch_rec.total_duration_min.value}min, "
                f"words/seg={_scratch_rec.words_per_segment.value}"
            )
        except Exception as _e:
            log.warning(f"[PRE-PROD] Scratch DecisionEngine failed ({_e}) — overlay used as-is")

        return config_overlay

    # Phase 1: Web Research
    if do_search:
        research = director.research_story(topic)
    else:
        log.info("[DIRECTOR] Phase 1/5: Web research SKIPPED by user")
        research = {"topic": topic, "combined_summary": "", "result_count": 0}

    # Series continuity
    overlay_dir = Path("studio_checkpoints")
    prev_overlay_path = overlay_dir / f"config_overlay_{_safe_filename(topic)}.json"
    prev_overlay = None
    if prev_overlay_path.exists():
        try:
            prev_overlay = _json.loads(prev_overlay_path.read_text(encoding="utf-8"))
            log.info(f"[PRE-PROD] Loaded previous config overlay: {prev_overlay_path.name}")
        except Exception as exc:
            log.warning(f"[PRE-PROD] Ignoring unreadable previous overlay {prev_overlay_path.name}: {exc}")

    if skip_consultation and prev_overlay:
        log.info("[PRE-PROD] Series resume detected — skipping phases 1-3, reusing previous config")
        vision_doc = prev_overlay.get("_director_vision", {})
        if not vision_doc:
            vision_doc = {}
        chars = prev_overlay.get("characters", {})
        if isinstance(chars, dict):
            chars_list = []
            for name, details in chars.items():
                if isinstance(details, dict):
                    c = details.copy()
                    c.setdefault("name", name)
                else:
                    c = {"name": name, "description": str(details)}
                chars_list.append(c)
            chars = chars_list
        vision_doc["characters"] = chars
        user_responses = {
            "visual_style": prev_overlay.get("visual", {}).get("style", ""),
            "subtitle_style": prev_overlay.get("subtitles", {}).get("format", "classic"),
            # ponytail: empty default → base config's tts.engine wins (consistent
            # with the director's engine priority: user > base config > vision).
            "tts_engine": prev_overlay.get("tts", {}).get("engine", ""),
            "custom_instructions": prev_overlay.get("production_notes", {}).get(
                "custom_instructions", ""
            ),
        }
        writer_input = director.consult_with_writer(vision_doc, user_responses)
        config_overlay = director.produce_runtime_config(
            vision_doc, user_responses, writer_input, mode=output_mode
        )
        config_overlay = _deep_merge(prev_overlay, config_overlay)
        log.info(
            f"[PRE-PROD] Series resume: overlay merged with previous ({len(config_overlay.get('characters', {}))} total chars)"
        )

        from core.decision_record import build_and_persist_decision_record

        try:
            _series_extra = {}
            _series_dur = config_overlay.get("video", {}).get("total_duration_min")
            if _series_dur:
                _series_extra["total_duration_min"] = _series_dur
            config_overlay, _series_rec = build_and_persist_decision_record(
                director=director, topic=topic, config=config, config_overlay=config_overlay,
                vision_doc=vision_doc, writer_input=writer_input, run_mode=run_mode,
                project_name=project_name, cli_flags=cli_flags,
                extra_user_locks=_series_extra or None,
            )
            log.info(
                f"[PRE-PROD] Series DecisionRecord built and persisted — "
                f"segments={_series_rec.segment_count.value}, "
                f"duration={_series_rec.total_duration_min.value}min, "
                f"words/seg={_series_rec.words_per_segment.value}"
            )
        except Exception as _e:
            log.warning(f"[PRE-PROD] Series DecisionEngine failed ({_e}) — overlay used as-is")

        return config_overlay

    # Phase 2: Director analysis
    vision_doc = director.analyze_with_research(
        topic,
        research,
        config.get("video", {}).get("total_duration_min", 10),
        content_text=content_text,
    )

    # Phase 2.5: Duration
    director_recommended = vision_doc.get("recommended_duration_min", 0)
    config_default = config.get("video", {}).get("total_duration_min", 10)
    if director_recommended and director_recommended > 0:
        est_minutes = int(director_recommended)
        log.info(f"[DURATION] Director recommends {est_minutes} min based on content analysis")
        config_overlay["video"]["total_duration_min"] = est_minutes
        config_overlay["video"]["_director_recommended"] = True
    else:
        est_minutes = config_default
    if content_text and len(content_text) > 500:
        duration_choice = director.consult_on_duration(est_minutes)
        if duration_choice["action"] == "cliffhanger":
            cliffhangers = director.suggest_cliffhangers(content_text, est_minutes)
            cliff_options = [
                "{} — {} (approx {} min)".format(
                    f"Option {i + 1}",
                    c["outcome"],
                    max(5, int(est_minutes * c["point"] / 100)),
                )
                for i, c in enumerate(cliffhangers)
            ]
            chosen = director.consult_user(
                "Which cliffhanger point would you like the video to end at?",
                options=cliff_options,
                allow_custom=True,
            )
            chosen_idx = 0
            for i, opt in enumerate(cliff_options):
                if opt in chosen:
                    chosen_idx = i
                    break
            chosen_point = cliffhangers[chosen_idx]["point"]
            est_minutes = max(5, int(est_minutes * chosen_point / 100))
            config_overlay["video"]["total_duration_min"] = est_minutes
            config_overlay["video"]["_cliffhanger_point"] = chosen_point
            config_overlay["video"]["_cliffhanger_reason"] = cliffhangers[chosen_idx]["reason"]
            log.info(f"[DURATION] Cliffhanger at {chosen_point}% -> {est_minutes} min video")

        elif duration_choice["action"] == "compact":
            target = duration_choice["target_minutes"]
            compacted = director.compact_story(content_text, target, est_minutes)
            content_text = compacted
            log.info("[DURATION] Re-analyzing with compacted content...")
            vision_doc = director.analyze_with_research(
                topic, research, target, content_text=compacted
            )
            config_overlay["video"]["total_duration_min"] = target
            config_overlay["video"]["_content_compacted"] = True
            est_minutes = target

        elif duration_choice["action"] == "custom":
            config_overlay["video"]["total_duration_min"] = duration_choice["target_minutes"]
            config_overlay["video"]["_user_adjusted"] = True
            est_minutes = duration_choice["target_minutes"]

        elif duration_choice["action"] == "adjusted":
            target = duration_choice.get("target_minutes", est_minutes)
            config_overlay["video"]["total_duration_min"] = target
            config_overlay["video"]["_user_adjusted"] = True
            est_minutes = target
            log.info(f"[DURATION] User adjusted duration to {est_minutes} min")

    # Phase 3: User consultation
    user_responses, writer_input = director.consult_on_config(vision_doc)

    # Phase 4: Writer collaboration
    if not skip_consultation:
        try:
            writer_structural = director.consult_with_writer(vision_doc, user_responses)
            for k in (
                "segment_count",
                "words_per_segment",
                "image_count_per_segment",
                "opening_hook_style",
                "pacing_notes",
            ):
                if writer_structural.get(k):
                    writer_input.setdefault(k, writer_structural[k])
        except Exception as e:
            log.warning(f"[PRE-PROD] Writer consultation failed ({e}) — using Director proposals")

    # Phase 5: Build overlay
    config_overlay = director.produce_runtime_config(
        vision_doc, user_responses, writer_input, mode=output_mode
    )

    # Build DecisionRecord (single source of truth)
    _cli_flags = dict(cli_flags or {})
    user_chosen_duration = config_overlay.get("video", {}).get("total_duration_min")
    _video_ov = config_overlay.get("video", {})
    _user_picked_duration = (
        _video_ov.get("_cliffhanger_point") is not None
        or _video_ov.get("_content_compacted")
        or _video_ov.get("_user_adjusted")
    )
    _normal_extra = {}
    if user_chosen_duration and _user_picked_duration:
        _normal_extra["total_duration_min"] = user_chosen_duration

    from core.decision_record import build_and_persist_decision_record

    try:
        config_overlay, rec = build_and_persist_decision_record(
            director=director, topic=topic, config=config, config_overlay=config_overlay,
            vision_doc=vision_doc, writer_input=writer_input, run_mode=run_mode,
            project_name=project_name, cli_flags=_cli_flags,
            extra_user_locks=_normal_extra or None,
        )
        log.info(
            f"[PRE-PROD] DecisionRecord built and persisted — "
            f"segments={rec.segment_count.value}, "
            f"duration={rec.total_duration_min.value}min, "
            f"words/seg={rec.words_per_segment.value}"
        )
    except Exception as e:
        log.warning(f"[PRE-PROD] DecisionEngine failed ({e}) — overlay used as-is")

    seg_count = config_overlay.get("video", {}).get("total_duration_min", "?")
    log.info(
        f"Pre-production complete! Config overlay: "
        f"{len(config_overlay.get('characters', {}))} characters, "
        f"~{seg_count} min"
    )

    # Normalize TTS engine in overlay
    try:
        from audio.audio_proxy import normalize_tts_engine as _norm_engine

        _raw_ov_engine = config_overlay.get("tts", {}).get("engine", "")
        if _raw_ov_engine:
            _norm_ov_engine = _norm_engine(_raw_ov_engine)
            if _norm_ov_engine != _raw_ov_engine:
                log.warning(
                    f"[PRE-PROD] TTS engine {_raw_ov_engine!r} in overlay "
                    f"normalized to {_norm_ov_engine!r}"
                )
            config_overlay.setdefault("tts", {})["engine"] = _norm_ov_engine
    except Exception as _ne:
        log.debug(f"[PRE-PROD] TTS engine normalization skipped: {_ne}")

    # Enforce operator-locked manga style before persisting the overlay used
    # for resume/debug. Runtime also enforces this after merging, but saving the
    # pre-lock overlay can reintroduce Director style drift on later resumes.
    try:
        from utils import scene_director

        style_lock = cast(Any, scene_director)._enforce_visual_style_lock
        config_overlay = style_lock(config_overlay, config)
    except Exception as _style_lock_err:
        log.debug(f"[PRE-PROD] Visual style lock skipped before overlay save: {_style_lock_err}")

    # Save overlay for series reuse
    overlay_dir = Path("studio_checkpoints")
    overlay_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = overlay_dir / f"config_overlay_{_safe_filename(topic)}.json"
    with open(overlay_path, "w", encoding="utf-8") as f:
        _json.dump(config_overlay, f, indent=2, ensure_ascii=False)
    log.info(f"Config overlay saved: {overlay_path}")

    return config_overlay


# ── Story outline (called once before segment loop) ───────────────────────


def plan_outline(
    topic: str, n_segs: int, config: dict, director_agent, cp_mgr, resume: bool
) -> list[dict]:
    """Plan the story outline (once, before segments). Loads from checkpoint if available."""
    from utils.story_planner import _default_outline_used, plan_story

    ck_meta = cp_mgr.get(f"{topic}_meta") if resume else None
    if ck_meta and "outline" in ck_meta and ck_meta["outline"].get("data"):
        outline = ck_meta["outline"]["data"]
        log.info("[OK] Story outline loaded from checkpoint")
    else:
        log.info("Planning story outline...")
        outline = plan_story(topic, n_segs, config, director_agent)
        if not outline:
            raise ValueError("Story planner returned no segments")
        cp_mgr.save(f"{topic}_meta", "outline", {"data": outline})
        log.info(f"[OK] Story outline: {len(outline)} segments")

    # Record degradation if the outline fell back to defaults
    if _default_outline_used:
        try:
            from agents.director_agent import UIState as _UIState

            _UIState.add_degradation(
                seg=0,
                stage="plan_outline",
                reason="Director LLM failed — outline is generic defaults",
            )
        except Exception:
            log.warning("[DEGRADED] Director outline is generic defaults (UIState unavailable)")

    return outline
