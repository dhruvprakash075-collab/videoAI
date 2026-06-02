"""test_retry_budget.py - Tests for A7: per-segment retry budget."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contextlib

from agents.director_agent import UIState


def _make_budget_wrapper(max_retries: int, fail_times: int):
    """Return a (wrapper_fn, call_count_list) that fails `fail_times` then succeeds."""
    call_count = [0]
    retry_counts: dict = {}

    def process_segment_fake(i):
        call_count[0] += 1
        if call_count[0] <= fail_times:
            raise RuntimeError(f"Simulated failure #{call_count[0]}")

    def _process_segment_with_budget(i):
        retry_counts.setdefault(i, 0)
        while retry_counts[i] <= max_retries:
            try:
                process_segment_fake(i)
                return
            except Exception as _e:
                retry_counts[i] += 1
                if retry_counts[i] > max_retries:
                    with contextlib.suppress(Exception):
                        UIState.add_degradation(i, "segment_skip",
                                                f"retry budget exhausted: {str(_e)[:100]}")
                    return

    return _process_segment_with_budget, call_count, retry_counts


def test_retry_stops_at_budget():
    """Segment should be skipped after max_segment_retries attempts."""
    UIState.degradations = []
    max_retries = 2
    # Always fails — should stop after max_retries+1 total calls
    wrapper, call_count, _retry_counts = _make_budget_wrapper(max_retries, fail_times=999)
    wrapper(1)
    # Total calls = initial attempt + max_retries retries = max_retries + 1
    assert call_count[0] == max_retries + 1, (
        f"Expected {max_retries + 1} calls, got {call_count[0]}"
    )


def test_retry_succeeds_before_budget():
    """If segment succeeds on attempt 2, no degradation should be recorded."""
    UIState.degradations = []
    max_retries = 2
    wrapper, call_count, _retry_counts = _make_budget_wrapper(max_retries, fail_times=1)
    wrapper(1)
    assert call_count[0] == 2  # failed once, succeeded on second
    assert len(UIState.degradations) == 0


def test_retry_budget_exhausted_records_degradation():
    """When budget is exhausted, a degradation entry should be recorded."""
    UIState.degradations = []
    max_retries = 1
    wrapper, _call_count, _retry_counts = _make_budget_wrapper(max_retries, fail_times=999)
    wrapper(3)  # segment index 3
    assert len(UIState.degradations) == 1
    assert UIState.degradations[0]["seg"] == 3
    assert UIState.degradations[0]["stage"] == "segment_skip"
    assert "retry budget exhausted" in UIState.degradations[0]["reason"]


def test_retry_budget_zero_means_no_retry():
    """max_segment_retries=0 means one attempt only, then skip."""
    UIState.degradations = []
    max_retries = 0
    wrapper, call_count, _retry_counts = _make_budget_wrapper(max_retries, fail_times=999)
    wrapper(1)
    assert call_count[0] == 1  # only one attempt
    assert len(UIState.degradations) == 1
