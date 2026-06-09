"""post_production.py - Final concat, thumbnail, chapters, manifest, QC.

Extracted from pipeline_long.py (Task 1: split god module). Owns the work that
runs ONCE after all segments complete:

  • Final video concatenation (FFmpeg, with optional background music + crossfade)
  • Thumbnail generation (D3 — hero frame → thumbnail.png)
  • Quality check
  • YouTube chapter markers
  • Run manifest write
  • Dry-run chapters

This module NEVER touches the per-segment loop, TTS, SD, or the Director. It
is a pure post-processor that takes (mp4s, outline, config) → final video.
"""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime as _dt
from pathlib import Path
from typing import Any

from core.pre_production import format_chapters_time, get_video_duration
from utils import _safe_filename

log = logging.getLogger(__name__)


def write_manifest(topic: str, result: dict, config: dict, n_segs: int, wall_time_s: float) -> None:
    """Write a structured JSON run manifest for this pipeline run."""
    manifest_dir = Path("studio_outputs") / _safe_filename(topic)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "run_manifest.json"

    manifest = {
        "topic": topic,
        "run_date": _dt.now().isoformat(),
        "wall_time_seconds": round(wall_time_s, 1),
        "status": result.get("status", "unknown"),
        "models": {
            "director": config.get("models", {}).get("director", "unknown"),
            "writer": config.get("models", {}).get("writer", "unknown"),
            "image_gen": config.get("image_gen", {}).get("sd_model_path", "unknown"),
            "tts": config.get("tts", {}).get("model", "unknown"),
        },
        "settings": {
            "resolution": config.get("video", {}).get("resolution"),
            "fps": config.get("video", {}).get("fps"),
            "sd_steps": config.get("image_gen", {}).get("steps"),
            "sd_width": config.get("image_gen", {}).get("width"),
            "sd_height": config.get("image_gen", {}).get("height"),
            "tts_lang": config.get("tts", {}).get("lang"),
        },
        "segments_completed": result.get("segments", n_segs),
        "final_video": result.get("output"),
        "duration_s": result.get("duration_s", 0),
        "quality_check": result.get("quality", {}),
        "youtube_upload": result.get("youtube_upload", "not_attempted"),
    }

    try:
        from agents.director_agent import UIState as _UIS

        manifest["degradations"] = list(_UIS.degradations)
    except Exception:
        manifest["degradations"] = []

    try:
        _thumb = manifest_dir / "thumbnail.png"
        if _thumb.exists():
            manifest["thumbnail"] = str(_thumb)
    except Exception:
        pass

    try:
        from memory.blackboard import get_blackboard

        _bb = get_blackboard(config, topic_slug=_safe_filename(topic))
        _rec = _bb.read_decision()
        if _rec is not None:
            manifest["decisions"] = _rec.provenance_report()
    except Exception as _e:
        log.debug(f"[MANIFEST] Could not include decision provenance: {_e}")

    manifest_path.write_text(
        _json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info(f"[MANIFEST] Run manifest written: {manifest_path}")


def _write_chapters(outline: list, mp4s: list, final_out: Path, topic: str) -> None:
    """Write YouTube chapter markers based on actual segment durations."""
    try:
        chapters_lines = []
        curr_time = 0.0
        for idx, plan in enumerate(outline):
            t_str = format_chapters_time(curr_time)
            title = plan.get("title") or f"Part {idx + 1} - {plan.get('key_event', 'Key Event')}"
            chapters_lines.append(f"{t_str} {title}")
            _mp4_idx = mp4s[idx] if idx < len(mp4s) else None
            if _mp4_idx is not None:
                curr_time += get_video_duration(_mp4_idx)

        chapters_content = "\n".join(chapters_lines)
        chapters_dir = Path("studio_outputs") / _safe_filename(topic)
        chapters_dir.mkdir(parents=True, exist_ok=True)
        chapters_path = chapters_dir / "chapters.txt"
        chapters_path.write_text(chapters_content, encoding="utf-8")
        log.info(f"[CHAPTERS] YouTube chapters written: {chapters_path}")

        final_chapters_path = final_out.parent / f"{final_out.stem}_chapters.txt"
        final_chapters_path.write_text(chapters_content, encoding="utf-8")
        log.info(f"[CHAPTERS] Chapters also written: {final_chapters_path}")
        return chapters_lines
    except Exception as e:
        log.warning(f"Could not generate YouTube chapters: {e}")
        return []


def _write_dry_run_chapters(outline: list, final_out: Path, topic: str) -> list:
    """Dry-run: generate mock chapter markers (30s/seg assumption)."""
    try:
        chapters_lines = []
        curr_time = 0.0
        for idx, plan in enumerate(outline):
            t_str = format_chapters_time(curr_time)
            title = plan.get("title") or f"Part {idx + 1} - {plan.get('key_event', 'Key Event')}"
            chapters_lines.append(f"{t_str} {title}")
            curr_time += 30.0

        chapters_content = "\n".join(chapters_lines)
        chapters_dir = Path("studio_outputs") / _safe_filename(topic)
        chapters_dir.mkdir(parents=True, exist_ok=True)
        chapters_path = chapters_dir / "chapters.txt"
        chapters_path.write_text(chapters_content, encoding="utf-8")

        final_chapters_path = final_out.parent / f"{final_out.stem}_chapters.txt"
        final_chapters_path.write_text(chapters_content, encoding="utf-8")
        return chapters_lines
    except Exception as e:
        log.warning(f"Could not generate dry-run chapters: {e}")
        return []


def _generate_thumbnail(final_video: Path, topic: str) -> str:
    """Generate a 1280x720 thumbnail from the hero frame. Returns path or None."""
    if not Path(final_video).exists():
        return None
    try:
        import subprocess as _sp

        _thumb_out = Path("studio_outputs") / _safe_filename(topic) / "thumbnail.png"
        _thumb_out.parent.mkdir(parents=True, exist_ok=True)
        _sp.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(final_video),
                "-ss",
                "0",
                "-vframes",
                "1",
                "-vf",
                "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2",
                str(_thumb_out),
            ],
            capture_output=True,
            timeout=60,
        )
        if _thumb_out.exists():
            log.info(f"[D3] Thumbnail saved: {_thumb_out}")
            return str(_thumb_out)
        log.warning("[D3] Thumbnail generation produced no output")
        return None
    except Exception as _te:
        log.warning(f"[D3] Thumbnail generation failed: {_te}")
        return None


def finalize_dry_run(
    topic: str, config: dict, outline: list, n_segs: int, mp4s: list, wall_time_s: float
) -> dict:
    """Dry-run finalization: chapters + manifest, no real concat."""
    default_out = f"studio_outputs/{_safe_filename(topic)}_final_video.mp4"
    final_out = Path(config["video"].get("output_path", ""))
    if not final_out.name or final_out.name == "final_video.mp4":
        final_out = Path(default_out)
    final_out.parent.mkdir(parents=True, exist_ok=True)

    log.info(f"[DRY-RUN] Would concatenate {len(mp4s)} segments to {final_out}")
    _dry_result: dict[str, Any] = {
        "status": "dry_run",
        "segments": len(mp4s),
        "output": str(final_out),
    }
    write_manifest(topic, _dry_result, config, n_segs, wall_time_s)
    _dry_result["chapters"] = _write_dry_run_chapters(outline, final_out, topic)
    return _dry_result


def finalize_production(
    topic: str, config: dict, outline: list, n_segs: int, mp4s: list, wall_time_s: float
) -> dict:
    """Production finalization: concat, thumbnail, QC, manifest, chapters."""
    from core.segment_runner import log_vram_usage
    from utils.quality_check import check_video
    from video.renderer.assembler import concatenate_segments

    log_vram_usage("Pipeline End (pre-concat)")

    default_out = f"studio_outputs/{_safe_filename(topic)}_final_video.mp4"
    final_out = Path(config["video"].get("output_path", ""))
    if not final_out.name or final_out.name == "final_video.mp4":
        final_out = Path(default_out)
    final_out.parent.mkdir(parents=True, exist_ok=True)

    log.info(f"Concatenating {len(mp4s)} segments...")

    # Background music (mood-matched)
    music_path = None
    if config.get("music", {}).get("enabled", False):
        moods = [seg.get("mood", "mysterious") for seg in outline]
        dominant_mood = max(set(moods), key=moods.count) if moods else "mysterious"
        log.info(f"[MUSIC] Dominant story mood detected: {dominant_mood}")
        mood_tracks = config.get("music", {}).get("mood_tracks", {})
        track_name = mood_tracks.get(dominant_mood)
        if not track_name:
            track_name = config.get("music", {}).get("track_path", "")
        if track_name:
            music_path = Path(track_name)
            if not music_path.exists():
                log.warning(f"[MUSIC] Track not found, skipping: {music_path}")
                music_path = None

    try:
        final_video = concatenate_segments(
            [p for p in mp4s if p is not None],
            final_out,
            music=music_path,
            config=config,
        )
        log.info(f"[OK] Final video: {final_video}")
    except Exception as e:
        log.error(f"Final assembly failed: {e}", exc_info=True)
        return {"status": "error", "reason": str(e)}

    # Thumbnail
    _thumbnail_path = None
    if config.get("video", {}).get("generate_thumbnail", False):
        _thumbnail_path = _generate_thumbnail(final_video, topic)

    # Quality check — read DecisionRecord for user-requested duration target
    _requested_duration_s = None
    try:
        from memory.blackboard import get_blackboard
        _bb = get_blackboard(config, topic_slug=_safe_filename(topic))
        _rec = _bb.read_decision()
        if _rec is not None:
            _dur = _rec.total_duration_min
            if _dur.locked and _dur.provenance in ("user", "cli_flag"):
                _requested_duration_s = _dur.value * 60
                log.info(
                    f"[QC] User locked duration = {_dur.value}min "
                    f"({_requested_duration_s:.0f}s) — will validate against this target"
                )
    except Exception as _e:
        log.debug(f"[QC] Could not read DecisionRecord for duration target: {_e}")

    log.info("Running quality checks...")
    _actual_duration_s = sum(get_video_duration(p) for p in mp4s if p is not None)
    qc = check_video(
        final_video,
        config,
        expected_duration_s=_actual_duration_s if _actual_duration_s > 0 else None,
        requested_duration_s=_requested_duration_s,
    )
    log.info(f"  Quality: {'PASS' if qc['passed'] else 'FAIL'}")
    if qc["issues"]:
        for issue in qc["issues"]:
            log.warning(f"    - {issue}")

    _quality_passed = qc["passed"]
    _success_result: dict[str, Any] = {
        "status": "success" if _quality_passed else "error",
        "output": str(final_video),
        "segments": len(mp4s),
        "duration_s": qc["details"].get("duration_s", 0),
        "quality": qc,
        "thumbnail": _thumbnail_path,
    }

    # Chapters
    chapters = _write_chapters(outline, mp4s, final_out, topic)
    if chapters:
        _success_result["chapters"] = chapters

    # Auto-Upload (run BEFORE manifest so manifest includes upload status)
    upload_cfg = config.get("upload", {})
    if upload_cfg.get("enabled", False) and upload_cfg.get("platform") == "youtube":
        log.info("[YouTube] Auto-upload enabled. Initiating Playwright upload...")

        from utils.seo_generator import generate_seo_metadata

        seo_meta = generate_seo_metadata(topic, outline, config)
        title = seo_meta["title"]
        tags = seo_meta["tags"]

        from utils.youtube_uploader import upload_to_youtube

        desc_lines = [f"Auto-generated video about: {topic}\n\nChapters:"] + (chapters or [])
        description = "\n".join(desc_lines)

        uploaded = upload_to_youtube(
            video_path=final_video,
            title=title,
            description=description,
            tags=tags,
            visibility=upload_cfg.get("visibility", "private"),
            profile_dir=upload_cfg.get("profile_dir", "chrome_profile"),
            headless=True,
        )
        _success_result["youtube_upload"] = "success" if uploaded else "failed"

    # Manifest (written after upload so youtube_upload status is included)
    write_manifest(topic, _success_result, config, n_segs, wall_time_s)

    return _success_result
