"""decision_engine.py - Orchestrates the Director→Writer→User structural-decision flow.

Implements the authority model:
    default < director < writer < user / cli_flag

Produces a single validated DecisionRecord that the pipeline reads from
instead of re-deriving structural values locally.

This is a one-time pre-production step — no per-segment model calls.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.config_schemas import DecisionRecord

log = logging.getLogger(__name__)

# Impact ranking for risk-tiered intervention (Req 11)
# High-impact (≥7): offer user a gate; low-impact (<7): Director decides silently.
_IMPACT = {
    "total_duration_min": 10,
    "segment_count": 10,
    "words_per_segment": 9,
    "images_per_segment": 7,
    "segment_duration_min": 6,
    "end_mode": 8,
}
HIGH_IMPACT_THRESHOLD = 7


def build_decision_record(
    director,  # DirectorAgent instance
    vision_doc: dict,  # output of analyze_with_research
    writer_input: dict,  # output of consult_with_writer
    user_locks: dict,  # explicit typed user overrides {field: value}
    cli_flags: dict,  # e.g. {"total_duration_min": 180} from --duration
    config: dict,  # full loaded config
) -> "DecisionRecord":
    """Build the authoritative DecisionRecord for a run.

    Steps (lowest → highest authority):
      1. Seed defaults from config.
      2. Apply Director proposals from vision_doc.
      3. Apply Writer consent/adjustments from writer_input.
      4. Apply user locks and cli_flags (locked=True).
      5. resolve_conflicts().

    Returns a validated DecisionRecord.
    """
    from config.config_schemas import (
        DecisionConflict,
        build_default_decision_record,
    )

    # ── 1. Seed from config ────────────────────────────────────────────────
    rec = build_default_decision_record(config)
    log.info("[DECISION ENGINE] Seeded defaults from config")

    # ── 2. Director proposals ──────────────────────────────────────────────
    _apply_director_proposals(rec, vision_doc, config)

    # ── 3. Writer consent / adjustments ───────────────────────────────────
    _apply_writer_input(rec, writer_input)

    # ── 4. User locks and CLI flags ────────────────────────────────────────
    _apply_user_locks(rec, user_locks, cli_flags)

    # ── 5. Resolve conflicts ───────────────────────────────────────────────
    try:
        rec.resolve_conflicts()
    except DecisionConflict as e:
        # Surface to user — do not silently pick (Req 3.3)
        log.exception(f"[DECISION ENGINE] Conflict detected: {e}")
        raise

    log.info(
        f"[DECISION ENGINE] Record built — "
        f"segments={rec.segment_count.value} ({rec.segment_count.provenance}), "
        f"duration={rec.total_duration_min.value}min ({rec.total_duration_min.provenance}), "
        f"words/seg={rec.words_per_segment.value} ({rec.words_per_segment.provenance}), "
        f"mode={rec.run_mode.value}"
    )
    return rec


def _apply_director_proposals(rec, vision_doc: dict, config: dict) -> None:
    """Apply Director-derived structural proposals to the record."""
    # Duration from Director's content analysis
    rec_dur = vision_doc.get("recommended_duration_min", 0)
    if rec_dur and rec_dur > 0:
        rec.set(
            "total_duration_min", float(rec_dur), "director", rationale="Director content analysis"
        )

    # Segment count from vision doc (if present)
    seg_count = vision_doc.get("segment_count", 0)
    if seg_count and seg_count > 0:
        rec.set("segment_count", int(seg_count), "director", rationale="Director story analysis")

    # Words per segment from vision doc
    wps = vision_doc.get("words_per_segment", 0)
    if wps and wps > 0:
        rec.set("words_per_segment", int(wps), "director", rationale="Director pacing analysis")

    # Images per segment from vision doc
    ips = vision_doc.get("image_count_per_segment", 0)
    if ips and ips > 0:
        rec.set("images_per_segment", int(ips), "director", rationale="Director visual analysis")

    log.debug("[DECISION ENGINE] Director proposals applied")


def _apply_writer_input(rec, writer_input: dict) -> None:
    """Apply Writer consent/adjustments. Records rationale for any change."""
    if not writer_input:
        log.info("[DECISION ENGINE] No writer input — keeping Director proposals")
        return

    mapping = {
        "segment_count": "segment_count",
        "words_per_segment": "words_per_segment",
        "image_count_per_segment": "images_per_segment",
    }
    pacing_notes = writer_input.get("pacing_notes", "")
    hook_style = writer_input.get("opening_hook_style", "")
    rationale_base = f"Writer: hook={hook_style[:40]}, pacing={pacing_notes[:60]}"

    for w_key, r_field in mapping.items():
        val = writer_input.get(w_key)
        if val and isinstance(val, (int, float)) and val > 0:
            applied = rec.set(r_field, int(val), "writer", rationale=rationale_base)
            if applied:
                log.info(f"[DECISION ENGINE] Writer adjusted '{r_field}' → {int(val)}")
            else:
                log.debug(
                    f"[DECISION ENGINE] Writer '{r_field}={int(val)}' blocked "
                    f"(current authority higher)"
                )


def _apply_user_locks(rec, user_locks: dict, cli_flags: dict) -> None:
    """Apply explicit user overrides and CLI flags as locked values."""
    # CLI flags (e.g. --duration → total_duration_min)
    # P4-33 fix: map internal flag keys to their actual CLI flag names for the
    # rationale string (e.g. "total_duration_min" → "--duration", not "--total_duration_min").
    _cli_flag_names = {
        "duration": "--duration",
        "total_duration_min": "--duration",
        "segment_count": "--segment-count",
        "words_per_segment": "--words-per-segment",
        "images_per_segment": "--images-per-segment",
    }
    cli_map = {
        "duration": "total_duration_min",
        "total_duration_min": "total_duration_min",
        "segment_count": "segment_count",
        "words_per_segment": "words_per_segment",
        "images_per_segment": "images_per_segment",
    }
    for flag, field in cli_map.items():
        val = cli_flags.get(flag)
        if val is not None:
            _flag_label = _cli_flag_names.get(flag, f"--{flag}")
            rec.set(field, val, "cli_flag", lock=True, rationale=f"CLI flag {_flag_label}")
            log.info(f"[DECISION ENGINE] CLI lock: '{field}' = {val}")

    # Explicit user overrides from consultation
    user_map = {
        "total_duration_min": "total_duration_min",
        "segment_count": "segment_count",
        "words_per_segment": "words_per_segment",
        "images_per_segment": "images_per_segment",
        "segment_duration_min": "segment_duration_min",
    }
    for u_key, field in user_map.items():
        val = user_locks.get(u_key)
        if val is not None:
            rec.set(field, val, "user", lock=True, rationale="User explicit override")
            log.info(f"[DECISION ENGINE] User lock: '{field}' = {val}")

    # Run mode
    run_mode = user_locks.get("run_mode") or cli_flags.get("run_mode")
    if run_mode in ("project", "one_time"):
        rec.set("run_mode", run_mode, "user" if "run_mode" in user_locks else "cli_flag", lock=True)

    project_name = user_locks.get("project_name") or cli_flags.get("project_name")
    if project_name:
        object.__setattr__(rec, "project_name", project_name)


def should_prompt_user(field: str) -> bool:
    """Return True if this field is high-impact and should offer a user gate (Req 11)."""
    return _IMPACT.get(field, 0) >= HIGH_IMPACT_THRESHOLD
