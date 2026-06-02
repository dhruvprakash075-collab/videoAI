"""test_degradation_ledger.py - Tests for B2: degradation ledger."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.director_agent import UIState


def test_add_degradation_appends():
    """add_degradation should append a dict to UIState.degradations."""
    UIState.degradations = []
    UIState.add_degradation(1, "sfx_skip", "no SFX files found")
    assert len(UIState.degradations) == 1
    assert UIState.degradations[0] == {"seg": 1, "stage": "sfx_skip", "reason": "no SFX files found"}


def test_add_degradation_multiple():
    """Multiple degradations should all be recorded."""
    UIState.degradations = []
    UIState.add_degradation(1, "sfx_skip", "reason A")
    UIState.add_degradation(2, "mastering_fallback", "reason B")
    UIState.add_degradation(3, "image_black_frame", "reason C")
    assert len(UIState.degradations) == 3
    stages = [d["stage"] for d in UIState.degradations]
    assert "sfx_skip" in stages
    assert "mastering_fallback" in stages
    assert "image_black_frame" in stages


def test_reset_run_clears_degradations():
    """reset_run should clear the degradation ledger for a new run."""
    UIState.degradations = [{"seg": 1, "stage": "test", "reason": "old"}]
    UIState.reset_run("new topic")
    assert UIState.degradations == []


def test_conftest_resets_degradations():
    """The conftest autouse fixture should have reset degradations before this test."""
    # If conftest is working, degradations should be empty at test start
    assert UIState.degradations == []


def test_add_degradation_thread_safe():
    """Concurrent add_degradation calls should not corrupt the list."""
    import threading
    UIState.degradations = []
    threads = []
    for i in range(20):
        t = threading.Thread(target=UIState.add_degradation, args=(i, "test", f"reason {i}"))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(UIState.degradations) == 20
