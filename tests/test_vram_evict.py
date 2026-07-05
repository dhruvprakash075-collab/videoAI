"""test_vram_evict.py - Tests for A1: VRAM-free verification before SD."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from importlib.util import find_spec
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    not find_spec("torch"), reason="torch not installed"
)


def _make_config(wait_s=2, threshold_gb=4.5):
    return {
        "ollama": {"host": "http://localhost:11434"},
        "models": {"director": "test-model"},
        "performance": {
            "vram_evict_wait_s": wait_s,
            "vram_sd_threshold_gb": threshold_gb,
        },
    }


def test_no_cuda_returns_immediately():
    """When CUDA is unavailable the poll must be skipped entirely."""
    from core.pipeline_long import _evict_ollama_models

    with patch("torch.cuda.is_available", return_value=False):
        # Should not raise and should return quickly
        _evict_ollama_models(_make_config(), reason="test")


def test_vram_low_then_high_waits_then_proceeds():
    """Poll should wait while VRAM is low, then proceed when it frees."""
    from core.pipeline_long import _evict_ollama_models

    call_count = [0]
    int(4.5 * 1024**3)

    def fake_mem_get_info():
        call_count[0] += 1
        if call_count[0] < 3:
            # First two calls: VRAM still low
            return (int(1.0 * 1024**3), int(6.0 * 1024**3))
        # Third call: VRAM freed
        return (int(5.0 * 1024**3), int(6.0 * 1024**3))

    with (
        patch("torch.cuda.is_available", return_value=True),
        patch("torch.cuda.mem_get_info", side_effect=fake_mem_get_info),
        patch("torch.cuda.empty_cache"),
        patch("time.sleep"),
    ):  # don't actually sleep in tests
        _evict_ollama_models(_make_config(wait_s=10), reason="test")

    assert call_count[0] >= 3, "Should have polled at least 3 times"


def test_vram_always_low_proceeds_with_warning(caplog):
    """When VRAM never frees within the window, log a warning and proceed."""
    import logging

    from core.pipeline_long import _evict_ollama_models

    def fake_mem_get_info():
        return (int(1.0 * 1024**3), int(6.0 * 1024**3))  # always low

    with (
        patch("torch.cuda.is_available", return_value=True),
        patch("torch.cuda.mem_get_info", side_effect=fake_mem_get_info),
        patch("torch.cuda.empty_cache"),
        patch("time.sleep"),
        patch("time.time", side_effect=[0.0, 100.0, 100.0, 100.0]),
    ):  # instant timeout
        with caplog.at_level(logging.WARNING):
            _evict_ollama_models(_make_config(wait_s=0.001), reason="test")

    # Should have logged a warning about low VRAM
    assert any("VRAM" in r.message or "low" in r.message.lower() for r in caplog.records), (
        "Expected VRAM warning in logs"
    )
