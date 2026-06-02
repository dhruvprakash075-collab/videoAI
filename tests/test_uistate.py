"""Unit tests for UIState progress helpers and the pure TUI helpers."""

import time

import pytest

from agents.director_agent import UIState
from studio_tui_helpers import (
    format_elapsed,
    format_etc,
    parse_duration,
    safe_filename,
    vram_high,
)

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


# ── format_elapsed ────────────────────────────────────────────────────────────

def test_format_elapsed_not_started():
    assert format_elapsed(0) == "—"


def test_format_elapsed_minutes_seconds():
    assert format_elapsed(time.time() - 90) == "01:30"


def test_format_elapsed_hours():
    assert format_elapsed(time.time() - 3600) == "1h 00m 00s"


# ── format_etc ────────────────────────────────────────────────────────────────

def test_format_etc_total_zero():
    assert format_etc(time.time(), 0, 0) == "—"


def test_format_etc_current_zero():
    assert format_etc(time.time(), 0, 10) == "—"


def test_format_etc_not_started():
    assert format_etc(0, 5, 10) == "—"


def test_format_etc_complete():
    assert format_etc(time.time() - 10, 10, 10) == "~0s"


def test_format_etc_halfway_seconds():
    # 5 of 10 done in ~10s → ~10s remaining
    etc = format_etc(time.time() - 10, 5, 10)
    assert etc.startswith("~") and etc.endswith("s")


# ── parse_duration ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("0", None),
    ("-5", None),
    ("abc", None),
    ("  15  ", 15),
    ("99999", None),     # above upper bound 480
    ("480", 480),        # exactly the cap
    ("1", 1),
    (10, 10),
    (None, None),
])
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


# ── safe_filename ─────────────────────────────────────────────────────────────

def test_safe_filename_strips_unsafe():
    assert safe_filename("The Clockmaker's Secret!") == "The_Clockmaker_s_Secret_"


def test_safe_filename_keeps_safe():
    assert safe_filename("abc_123-XY") == "abc_123-XY"


# ── vram_high ─────────────────────────────────────────────────────────────────

def test_vram_high_above_threshold():
    assert vram_high("4.9/6.0GB (82%)") is True


def test_vram_high_below_threshold():
    assert vram_high("3.0/6.0GB (50%)") is False


def test_vram_high_no_percent():
    assert vram_high("unknown") is False


def test_vram_high_empty():
    assert vram_high("") is False
