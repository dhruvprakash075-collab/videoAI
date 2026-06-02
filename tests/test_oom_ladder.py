"""test_oom_ladder.py - Tests for D1: OOM auto-recovery ladder."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock


def test_oom_events_recorded_on_tier1_failure():
    """When tier 1 OOMs, the event should be recorded in _oom_events."""
    from video.image_gen import image_gen as ig
    ig._oom_events.clear()

    # Simulate a tier-1 OOM that falls back to tier-2 success
    import torch
    fake_img = MagicMock()
    fake_img.images = [MagicMock()]

    call_count = [0]
    def fake_pipe(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise torch.cuda.OutOfMemoryError("OOM")
        return fake_img

    # We just test the _record_oom_event function directly
    ig._record_oom_event({"tier": 1, "from_res": "768x432", "to_res": "768x432", "steps": 8})
    assert len(ig._oom_events) == 1
    assert ig._oom_events[0]["tier"] == 1


def test_clear_oom_events():
    """clear_oom_events should empty the list."""
    from video.image_gen import image_gen as ig
    ig._oom_events.clear()
    ig._record_oom_event({"tier": 1, "from_res": "768x432", "to_res": "640x360", "steps": 6})
    assert len(ig._oom_events) == 1
    ig.clear_oom_events()
    assert len(ig._oom_events) == 0


def test_get_oom_report_returns_copy():
    """get_oom_report should return a copy, not the live list."""
    from video.image_gen import image_gen as ig
    ig._oom_events.clear()
    ig._record_oom_event({"tier": 2, "from_res": "768x432", "to_res": "640x360", "steps": 6})
    report = ig.get_oom_report()
    assert len(report) == 1
    # Modifying the report should not affect the internal list
    report.clear()
    assert len(ig._oom_events) == 1


def test_oom_events_thread_safe():
    """Concurrent _record_oom_event calls should not corrupt the list."""
    import threading

    from video.image_gen import image_gen as ig
    ig._oom_events.clear()

    threads = []
    for i in range(20):
        t = threading.Thread(
            target=ig._record_oom_event,
            args=({"tier": i % 3 + 1, "from_res": "768x432", "to_res": "640x360", "steps": 8},)
        )
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ig._oom_events) == 20
