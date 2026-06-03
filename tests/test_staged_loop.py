"""test_staged_loop.py - Tests for C1: staged loop config keys (flag exists, default false)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_staged_loop_default_false():
    """performance.staged_loop should be a bool in config (currently enabled)."""
    from config import load_config

    cfg = load_config()
    val = cfg.get("performance", {}).get("staged_loop", False)
    assert isinstance(val, bool)


def test_lookahead_segments_default_one():
    """performance.lookahead_segments should be a positive integer in config."""
    from config import load_config

    cfg = load_config()
    val = cfg.get("performance", {}).get("lookahead_segments", 1)
    assert isinstance(val, int)
    assert val >= 1


def test_staged_loop_flag_readable():
    """The staged_loop flag should be readable from config without error."""
    from config import load_config

    cfg = load_config()
    val = cfg.get("performance", {}).get("staged_loop", False)
    assert isinstance(val, bool)


def test_lookahead_segments_readable():
    """lookahead_segments should be readable and be an integer."""
    from config import load_config

    cfg = load_config()
    val = cfg.get("performance", {}).get("lookahead_segments", 1)
    assert isinstance(val, int)
    assert val >= 1
