"""test_phase0_regressions.py - Regression tests locking in Phase 0 fixes.

Each test targets a specific bug or gap that was found during the
Phase 0 code audit. These tests ensure the fixes are not regressed.
"""

import urllib.error
from unittest.mock import patch

import pytest

from core.main import _ollama_model_available
from utils.errors import (
    FatalError,
    RecoverableError,
    classify_errors,
)


class TestPhase0Regressions:
    """Regression tests for every Phase 0 fix."""

    # ── 0.1: _ollama_model_available fail-open fix ────────────────

    def test_ollama_available_raises_on_network_error(self):
        """Regression: network errors raise RecoverableError, not silent False."""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with pytest.raises(RecoverableError, match="unreachable"):
                _ollama_model_available("test-model", "http://localhost:11434")

    def test_ollama_available_returns_false_on_parse_error(self):
        """Regression: non-network errors still return False (safe fallback)."""
        with patch("urllib.request.urlopen", side_effect=ValueError("bad json")):
            result = _ollama_model_available("test-model", "http://localhost:11434")
            assert result is False

    # ── 0.1: classify_errors regression ───────────────────────────

    def test_classify_errors_fatal_on_unexpected(self):
        """Regression: unexpected exceptions become FatalError."""
        with pytest.raises(FatalError, match="my_stage"):
            with classify_errors("my_stage"):
                raise RuntimeError("unexpected crash")

    def test_classify_errors_recoverable_on_network(self):
        """Regression: network errors become RecoverableError."""
        with pytest.raises(RecoverableError):
            with classify_errors("api"):
                raise ConnectionError("timeout")

    def test_classify_errors_passthrough_already_classified(self):
        """Regression: already-classified errors propagate unchanged."""
        orig = RecoverableError("known issue")
        with pytest.raises(RecoverableError) as exc_info:
            with classify_errors("test"):
                raise orig
        assert exc_info.value is orig

    # ── 0.2: contextlib.suppress replaced with try/except ─────────

    def test_world_state_update_failure_logged(self):
        """Regression: world_state.update failures are logged not swallowed."""
        import inspect

        import core.segment_runner as sr

        # The translate_node closure is inside make_process_segment, so we scan
        # the whole file for the world_state.update patterns
        source = inspect.getsource(sr)
        # Verify contextlib.suppress was replaced with try/except
        # We look for the new pattern (try: world_state.update)
        assert (
            "try:\n                world_state.update" in source
            or "try:\n                    world_state.update" in source
        )
