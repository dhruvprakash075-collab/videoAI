"""test_segment_runner_helpers_extended.py - Extended unit tests for core/segment_runner.py helpers"""

import sys
from unittest.mock import MagicMock, patch

from core.segment_runner import (
    aggressive_vram_cleanup,
    evict_ollama_models,
    log_vram_usage,
)


def test_evict_ollama_models_config_error():
    # Evict should handle non-dict config gracefully by logging and not raising
    evict_ollama_models(123)


def test_evict_ollama_models_vram_poll_failure(monkeypatch):
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True
    mock_torch.cuda.mem_get_info.side_effect = Exception("mem check fail")
    monkeypatch.setitem(sys.modules, "torch", mock_torch)

    cfg = {"performance": {"vram_evict_wait_s": 1}}
    evict_ollama_models(cfg)


def test_evict_ollama_models_vram_timeout_harder_evict(monkeypatch):
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True
    mock_torch.cuda.mem_get_info.return_value = (1 * 1024**3, 8 * 1024**3)
    monkeypatch.setitem(sys.modules, "torch", mock_torch)

    class FakePsResp:
        def read(self):
            return b'{"models": [{"name": "running-model"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    url_mock = MagicMock(side_effect=[FakePsResp(), Exception("evict failed")])

    cfg = {
        "performance": {"vram_evict_wait_s": 0.1, "vram_sd_threshold_gb": 4.5},
        "ollama": {"host": "http://localhost:11434"},
    }

    with patch("urllib.request.urlopen", url_mock):
        evict_ollama_models(cfg)

    assert url_mock.call_count == 2


def test_log_vram_usage_uistate_exception(monkeypatch):
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True
    mock_torch.cuda.mem_get_info.return_value = (4 * 1024**3, 8 * 1024**3)
    monkeypatch.setitem(sys.modules, "torch", mock_torch)

    class BadUIState:
        def __setattr__(self, key, value):
            raise Exception("UIState set error")

    with patch("agents.director_agent.UIState", BadUIState()):
        log_vram_usage("test")


def test_aggressive_vram_cleanup_exception(monkeypatch):
    # Triggers the exception handler block inside aggressive_vram_cleanup
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True
    mock_torch.cuda.synchronize.side_effect = Exception("cuda sync fail")
    monkeypatch.setitem(sys.modules, "torch", mock_torch)

    mock_sched = MagicMock()
    mock_sched.active_heavy_count = 0

    aggressive_vram_cleanup(mock_sched)
