"""Tests for the DecisionEngine authority chain (no real LLM calls).

Covers Requirements 2, 3, 11, 14 and Correctness Properties 1, 2.
A fake director is used so no Ollama/model calls happen.
"""

import pytest

from agents.decision_engine import build_decision_record, should_prompt_user
from config.config_schemas import DecisionConflict


class FakeDirector:
    """Stand-in for DirectorAgent — the engine only needs it as a handle."""

    pass


BASE_CONFIG = {
    "video": {"total_duration_min": 10, "segment_duration_min": 2},
    "script": {"words_per_segment": 130, "default_images_per_segment": 6},
}


def test_director_proposals_applied():
    vision = {
        "recommended_duration_min": 20,
        "segment_count": 8,
        "words_per_segment": 150,
        "image_count_per_segment": 7,
    }
    rec = build_decision_record(FakeDirector(), vision, {}, {}, {}, BASE_CONFIG)
    assert rec.words_per_segment.value == 150
    assert rec.words_per_segment.provenance == "director"
    assert rec.images_per_segment.value == 7


def test_writer_adjusts_director():
    vision = {"segment_count": 8, "words_per_segment": 150}
    writer = {
        "segment_count": 10,
        "words_per_segment": 200,
        "pacing_notes": "slower",
        "opening_hook_style": "cold open",
    }
    rec = build_decision_record(FakeDirector(), vision, writer, {}, {}, BASE_CONFIG)
    assert rec.words_per_segment.value == 200
    assert rec.words_per_segment.provenance == "writer"
    assert "slower" in rec.words_per_segment.rationale


def test_user_lock_beats_writer_and_director():
    vision = {"segment_count": 8, "words_per_segment": 150}
    writer = {"segment_count": 10, "words_per_segment": 200}
    user = {"words_per_segment": 90}
    rec = build_decision_record(FakeDirector(), vision, writer, user, {}, BASE_CONFIG)
    assert rec.words_per_segment.value == 90
    assert rec.words_per_segment.provenance == "user"
    assert rec.words_per_segment.locked is True


def test_cli_flag_locks_duration():
    vision = {"recommended_duration_min": 30}
    rec = build_decision_record(
        FakeDirector(), vision, {}, {}, {"total_duration_min": 6}, BASE_CONFIG
    )
    assert rec.total_duration_min.value == 6
    assert rec.total_duration_min.provenance == "cli_flag"
    assert rec.total_duration_min.locked is True


def test_cli_flag_beats_user_lock():
    """CLI flag must win when both user and CLI specify the same field."""
    user = {"total_duration_min": 30}
    cli = {"total_duration_min": 1}
    rec = build_decision_record(FakeDirector(), {}, {}, user, cli, BASE_CONFIG)
    assert rec.total_duration_min.value == 1
    assert rec.total_duration_min.provenance == "cli_flag"
    assert rec.total_duration_min.locked is True


def test_run_mode_set():
    rec = build_decision_record(
        FakeDirector(), {}, {}, {"run_mode": "project", "project_name": "myproj"}, {}, BASE_CONFIG
    )
    assert rec.run_mode.value == "project"
    assert rec.project_name == "myproj"


def test_conflict_surfaces():
    # Two locked, inconsistent values must raise (not silently resolve)
    user = {"segment_count": 9, "total_duration_min": 30, "segment_duration_min": 2}
    with pytest.raises(DecisionConflict):
        build_decision_record(FakeDirector(), {}, {}, user, {}, BASE_CONFIG)


def test_resolve_conflicts_runs_for_unlocked():
    vision = {"segment_count": 7}
    rec = build_decision_record(FakeDirector(), vision, {}, {}, {}, BASE_CONFIG)
    # total recomputed from segment_count * segment_duration (7 * 2 = 14)
    assert rec.total_duration_min.value == 14


# ── Risk-tiered intervention (Req 11) ───────────────────────────────────────


@pytest.mark.parametrize(
    "field",
    ["segment_count", "total_duration_min", "words_per_segment", "images_per_segment", "end_mode"],
)
def test_high_impact_prompts(field):
    assert should_prompt_user(field) is True


@pytest.mark.parametrize(
    "field", ["transition_style", "music_style", "segment_duration_min", "color_palette"]
)
def test_low_impact_no_prompt(field):
    assert should_prompt_user(field) is False
