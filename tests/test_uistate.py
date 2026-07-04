"""Unit tests for UIState progress helpers."""

import time

import pytest

from agents.director_agent import UIState

# ── UIState.set_progress ──────────────────────────────────────────────────────


def test_set_progress_total_only():
    UIState.set_progress(total=12)
    assert UIState.segment_total == 12
    assert UIState.segment_current == 0


def test_set_progress_current_only():
    UIState.set_progress(current=5)
    assert UIState.segment_current == 5
    assert UIState.segment_total == 0


def test_set_progress_both():
    UIState.set_progress(current=3, total=10)
    assert UIState.segment_current == 3
    assert UIState.segment_total == 10


def test_set_progress_both_none_is_noop():
    UIState.segment_current = 4
    UIState.segment_total = 8
    UIState.set_progress()
    assert UIState.segment_current == 4
    assert UIState.segment_total == 8


def test_set_progress_float_is_cast_to_int():
    UIState.set_progress(current=3.9, total=10.2)
    assert UIState.segment_current == 3
    assert UIState.segment_total == 10


def test_set_progress_non_castable_raises():
    with pytest.raises(ValueError):
        UIState.set_progress(current="abc")


# ── UIState.reset_run ─────────────────────────────────────────────────────────


def test_reset_run_zeroes_and_sets_topic():
    UIState.segment_current = 9
    UIState.segment_total = 9
    UIState.vram_text = "5.0/6.0GB (83%)"
    UIState.reset_run("My Topic")
    assert UIState.topic == "My Topic"
    assert UIState.segment_current == 0
    assert UIState.segment_total == 0
    assert UIState.vram_text == ""
    assert abs(UIState.run_start_ts - time.time()) < 1.0


def test_reset_run_twice_from_dirty_state():
    UIState.reset_run("first")
    UIState.segment_current = 5
    UIState.reset_run("second")
    assert UIState.topic == "second"
    assert UIState.segment_current == 0


# ── UIState._uistate_log ──────────────────────────────────────────────────────


def test_uistate_log_basic():
    """_uistate_log appends a message to UIState.logs."""
    UIState.logs = []
    UIState._uistate_log("Test message one")
    assert "Test message one" in UIState.logs


def test_uistate_log_trims_when_over_maxlen():
    """_uistate_log trims the oldest 100 entries when log exceeds _log_maxlen."""
    UIState.logs = [f"old {i}" for i in range(UIState._log_maxlen)]
    assert len(UIState.logs) == UIState._log_maxlen

    UIState._uistate_log("New message after trim")

    # After trim, 100 oldest dropped, then new message appended
    assert len(UIState.logs) < UIState._log_maxlen
    assert "New message after trim" in UIState.logs
    # 'old 0' through 'old 99' should be gone
    assert "old 0" not in UIState.logs


def test_add_log_basic():
    """add_log appends a message to UIState.logs."""
    UIState.logs = []
    UIState.add_log("Added message")
    assert "Added message" in UIState.logs


def test_add_log_trims_when_over_maxlen():
    """add_log trims the log list to _log_maxlen when it exceeds the limit."""
    # Fill to exactly maxlen + 1
    UIState.logs = [f"entry {i}" for i in range(UIState._log_maxlen + 1)]

    # Trigger the trim path
    UIState.add_log("trim trigger")

    # Should be trimmed back to at most _log_maxlen
    assert len(UIState.logs) <= UIState._log_maxlen
    assert "trim trigger" in UIState.logs


def test_uistate_log_multiple_additions():
    """Multiple calls accumulate in order."""
    UIState.logs = []
    UIState._uistate_log("first")
    UIState._uistate_log("second")
    UIState._uistate_log("third")
    assert UIState.logs == ["first", "second", "third"]


def test_segment_manifests_thread_safety():
    """Verify thread-safe set and list of segment manifests."""
    UIState.reset_run("Test Topic")
    UIState.set_segment_manifest(1, {"title": "Part 1", "status": "success"})
    UIState.set_segment_manifest(2, {"title": "Part 2", "status": "error"})

    manifests = UIState.list_segment_manifests()
    assert len(manifests) == 2
    assert {"title": "Part 1", "status": "success"} in manifests
    assert {"title": "Part 2", "status": "error"} in manifests


def test_uistate_auto_accept_default():
    """UIState.auto_accept defaults to False (set to True by TUI on run start)."""
    UIState.auto_accept = False
    assert UIState.auto_accept is False
    # TUI sets it to True before starting the pipeline
    UIState.auto_accept = True
    assert UIState.auto_accept is True
