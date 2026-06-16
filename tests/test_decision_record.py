"""Tests for DecisionRecord: set/lock, conflict resolution, clamps, overlay, migration.

Covers Requirements 1, 3, 4 and design Correctness Properties 1–4, 7.
"""

import pytest

from config.config_schemas import (
    DECISION_SCHEMA_VERSION,
    DecisionConflict,
    DecisionRecord,
    build_default_decision_record,
    load_decision_record,
    migrate_decision_record,
)

# ── set() and lock immutability (Property 2) ────────────────────────────────


def test_defaults():
    r = DecisionRecord()
    assert r.version == DECISION_SCHEMA_VERSION
    assert r.segment_count.value == 5
    assert r.segment_count.provenance == "default"
    assert r.segment_count.locked is False


def test_director_set():
    r = DecisionRecord()
    assert r.set("segment_count", 9, "director")
    assert r.segment_count.value == 9
    assert r.segment_count.provenance == "director"


def test_writer_overrides_director():
    r = DecisionRecord()
    r.set("words_per_segment", 100, "director")
    assert r.set("words_per_segment", 200, "writer")
    assert r.words_per_segment.value == 200
    assert r.words_per_segment.provenance == "writer"


def test_director_cannot_override_writer():
    r = DecisionRecord()
    r.set("words_per_segment", 200, "writer")
    applied = r.set("words_per_segment", 100, "director")
    assert applied is False
    assert r.words_per_segment.value == 200


def test_user_lock_blocks_lower_authority():
    r = DecisionRecord()
    r.set("segment_count", 9, "director")
    r.set("segment_count", 12, "user", lock=True)
    assert r.segment_count.locked is True
    assert r.set("segment_count", 5, "director") is False
    assert r.set("segment_count", 7, "writer") is False
    assert r.segment_count.value == 12


def test_only_user_or_cli_can_relock():
    r = DecisionRecord()
    # director cannot lock even if lock=True is requested
    r.set("segment_count", 9, "director", lock=True)
    assert r.segment_count.locked is False
    # cli_flag can lock
    r.set("segment_count", 6, "cli_flag", lock=True)
    assert r.segment_count.locked is True


# ── Clamping (Property 4 / Req 1.3) ─────────────────────────────────────────


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("words_per_segment", 9999, 800),
        ("words_per_segment", 1, 50),
        ("images_per_segment", 99, 30),
        ("segment_count", 0, 1),
        ("segment_duration_min", 999, 30),
    ],
)
def test_clamps(field, value, expected):
    r = DecisionRecord()
    r.set(field, value, "director")
    assert getattr(r, field).value == expected
    # clamp recorded in adjustments
    assert any(a["type"] == "clamp" and a["field"] == field for a in r.adjustments)


# ── resolve_conflicts (Property 3 / Req 3.3, 4.2) ───────────────────────────


def test_conflict_prefer_segment_count_when_neither_locked():
    r = DecisionRecord()
    r.set("segment_count", 9, "director")
    r.set("segment_duration_min", 2, "default")
    r.set("total_duration_min", 99, "director")
    r.resolve_conflicts()
    assert r.total_duration_min.value == 18  # 9 * 2


def test_conflict_locked_total_recomputes_segments():
    r = DecisionRecord()
    r.set("segment_count", 9, "director")
    r.set("segment_duration_min", 2, "default")
    r.set("total_duration_min", 30, "user", lock=True)
    r.resolve_conflicts()
    assert r.segment_count.value == 15  # ceil(30/2)


def test_conflict_locked_segments_recomputes_total():
    r = DecisionRecord()
    r.set("segment_count", 9, "user", lock=True)
    r.set("segment_duration_min", 2, "default")
    r.set("total_duration_min", 30, "director")
    r.resolve_conflicts()
    assert r.total_duration_min.value == 18  # 9 * 2


def test_both_locked_conflict_raises():
    r = DecisionRecord()
    r.set("segment_count", 9, "user", lock=True)
    r.set("segment_duration_min", 2, "user", lock=True)
    r.set("total_duration_min", 30, "user", lock=True)
    with pytest.raises(DecisionConflict):
        r.resolve_conflicts()


def test_both_locked_recomputes_segment_duration():
    r = DecisionRecord()
    r.set("segment_count", 9, "user", lock=True)
    r.set("segment_duration_min", 2, "default")
    r.set("total_duration_min", 30, "user", lock=True)
    r.resolve_conflicts()
    assert r.segment_duration_min.value == 3.333  # 30 / 9 rounded to 3 decimal places


def test_consistent_values_no_change():
    r = DecisionRecord()
    r.set("segment_count", 5, "director")
    r.set("segment_duration_min", 2, "default")
    r.set("total_duration_min", 10, "director")
    r.resolve_conflicts()
    assert r.total_duration_min.value == 10
    assert r.segment_count.value == 5


# ── to_overlay / provenance_report (Property 1, 4) ──────────────────────────


def test_to_overlay_shape():
    r = DecisionRecord()
    r.set("segment_count", 8, "writer")
    r.set("words_per_segment", 160, "writer")
    ov = r.to_overlay()
    assert ov["video"]["total_duration_min"] == r.total_duration_min.value
    assert ov["script"]["words_per_segment"] == 160
    assert "_decision_record" in ov


def test_provenance_report_marks_locks():
    r = DecisionRecord()
    r.set("segment_count", 12, "user", lock=True)
    pr = r.provenance_report()
    assert pr["fields"]["segment_count"]["locked"] is True
    assert pr["fields"]["segment_count"]["provenance"] == "user"
    assert pr["resolved"]["segment_count"] == 12


# ── Migration (Property 7 / Req 16) ─────────────────────────────────────────


def test_migrate_v0_bare_numbers():
    raw = {
        "segment_count": 7,
        "total_duration_min": 14,
        "segment_duration_min": 2,
        "words_per_segment": 150,
        "images_per_segment": 6,
    }
    migrated = migrate_decision_record(dict(raw), from_version=0)
    assert migrated["version"] == 1
    assert isinstance(migrated["segment_count"], dict)
    assert migrated["segment_count"]["value"] == 7


def test_load_decision_record_migrates():
    raw = {
        "segment_count": 7,
        "total_duration_min": 14,
        "segment_duration_min": 2,
        "words_per_segment": 150,
        "images_per_segment": 6,
    }
    rec = load_decision_record(raw)
    assert rec.version == 1
    assert rec.segment_count.value == 7


def test_load_decision_record_unmigratable_rebuilds():
    rec = load_decision_record(
        {"segment_count": object()},  # not serializable / invalid
        config={
            "video": {"total_duration_min": 8, "segment_duration_min": 2},
            "script": {"words_per_segment": 120},
        },
    )
    assert rec is not None
    assert rec.version == 1


def test_build_default_from_config():
    cfg = {
        "video": {"total_duration_min": 12, "segment_duration_min": 3},
        "script": {"words_per_segment": 140, "default_images_per_segment": 8},
    }
    rec = build_default_decision_record(cfg)
    assert rec.total_duration_min.value == 12
    assert rec.words_per_segment.value == 140
    assert rec.images_per_segment.value == 8
    # segment_count derived: ceil(12/3) = 4
    assert rec.segment_count.value == 4


def test_clamp_total_duration_preserves_fractional_0_5():
    r = DecisionRecord()
    r.set("total_duration_min", 0.5, "cli_flag", lock=True)
    assert r.total_duration_min.value == 0.5
    assert r.total_duration_min.locked is True


def test_clamp_total_duration_rounds_up_from_below_0_5():
    r = DecisionRecord()
    r.set("total_duration_min", 0.1, "cli_flag", lock=True)
    assert r.total_duration_min.value == 0.5  # clamped to min 0.5


def test_duration_0_5_produces_segment_count_1():
    r = DecisionRecord()
    r.set("total_duration_min", 0.5, "cli_flag", lock=True)
    r.set("segment_duration_min", 2, "default")
    r.resolve_conflicts()
    assert r.segment_count.value == 1  # ceil(0.5/2) = 1
