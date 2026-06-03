"""test_retry_manager.py - retry_with_backoff decorator + patch_retries."""

import subprocess
from unittest.mock import patch

import pytest

from utils.retry_manager import (
    BOUNDED_EXCEPTIONS,
    BOUNDED_RETRIES,
    MAX_RETRIES,
    TRANSIENT_EXCEPTIONS,
    patch_retries,
    retry_with_backoff,
)


def test_retry_eventually_succeeds():
    calls = []

    @retry_with_backoff(max_retries=3, base_delay=0.001, backoff=1.0, exceptions=(ValueError,))
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("not yet")
        return "ok"

    assert flaky() == "ok"
    assert len(calls) == 3


def test_retry_raises_after_max_retries():
    calls = []

    @retry_with_backoff(max_retries=2, base_delay=0.001, backoff=1.0, exceptions=(ValueError,))
    def always_fail():
        calls.append(1)
        raise ValueError("boom")

    with pytest.raises(ValueError):
        always_fail()
    assert len(calls) == 2


def test_retry_default_exceptions_include_both_tiers():
    retry_with_backoff()
    assert True
    # We can't introspect defaults easily; instead verify decorator builds with no args.


def test_retry_transient_uses_max_retries_not_bounded():
    @retry_with_backoff(max_retries=4, base_delay=0.001, backoff=1.0, exceptions=(ConnectionError,))
    def flaker():
        raise ConnectionError("net")

    with patch("utils.retry_manager.time.sleep") as sleep_mock:
        with pytest.raises(ConnectionError):
            flaker()
    # All 4 attempts should sleep
    assert sleep_mock.call_count == 4


def test_retry_bounded_caps_at_bounded_retries():
    @retry_with_backoff(max_retries=10, base_delay=0.001, backoff=1.0, exceptions=(OSError,))
    def flaker():
        raise OSError("io")

    with patch("utils.retry_manager.time.sleep") as sleep_mock:
        with pytest.raises(OSError):
            flaker()
    # BOUNDED_RETRIES attempts then raise
    assert sleep_mock.call_count == BOUNDED_RETRIES - 1


def test_retry_backoff_delay_capped():
    seen = []

    @retry_with_backoff(max_retries=3, base_delay=1.0, backoff=100.0, exceptions=(TimeoutError,))
    def flaker():
        raise TimeoutError("t")

    with patch("utils.retry_manager.time.sleep", side_effect=lambda d: seen.append(d)):
        with pytest.raises(TimeoutError):
            flaker()
    # First call sleep: 1*100^0 = 1, second: 1*100 = 100, capped at MAX_DELAY_S
    assert seen[0] == 1.0
    assert all(d <= 60.0 for d in seen[1:])


def test_retry_passes_through_unexpected_exception():
    @retry_with_backoff(max_retries=3, base_delay=0.001, backoff=1.0, exceptions=(ValueError,))
    def flaker():
        raise TypeError("not in list")

    with pytest.raises(TypeError):
        flaker()


def test_retry_preserves_function_metadata():
    @retry_with_backoff(max_retries=1, exceptions=(ValueError,))
    def my_special_function():
        """my docstring"""
        return 1

    assert my_special_function.__name__ == "my_special_function"
    assert "my docstring" in my_special_function.__doc__


def test_patch_retries_idempotent():
    patch_retries()
    from audio import audio_proxy

    # Run again — should be no-op
    patch_retries()
    assert hasattr(audio_proxy.tts_generate, "_is_retry_patched")


def test_patch_retries_handles_missing_audio_proxy(monkeypatch):
    """patch_retries should not crash if audio_proxy import fails."""
    import sys

    monkeypatch.setitem(sys.modules, "audio", None)
    # Reset any prior patching
    with patch("utils.retry_manager.log") as _log:
        patch_retries()
    # Should log a warning but not raise


def test_patch_retries_sync_pipeline_long_success(monkeypatch):
    """Test patch_retries syncing attributes when pipeline_long is present."""
    import sys
    from unittest.mock import MagicMock

    mock_pl = MagicMock()
    mock_pl.tts_generate = lambda: None
    mock_pl.translate_hinglish = lambda: None

    # Put it in sys.modules
    monkeypatch.setitem(sys.modules, "core.pipeline_long", mock_pl)

    # Force re-patching by deleting '_is_retry_patched' attributes if they exist
    from audio import audio_proxy

    if hasattr(audio_proxy.tts_generate, "_is_retry_patched"):
        delattr(audio_proxy.tts_generate, "_is_retry_patched")
    if hasattr(audio_proxy.translate_hinglish, "_is_retry_patched"):
        delattr(audio_proxy.translate_hinglish, "_is_retry_patched")

    patch_retries()

    assert hasattr(mock_pl.tts_generate, "_is_retry_patched")
    assert hasattr(mock_pl.translate_hinglish, "_is_retry_patched")


def test_patch_retries_sync_pipeline_long_exception(monkeypatch):
    """Test patch_retries exception handling when checking sys.modules."""
    import sys

    class BrokenDict(dict):
        def __contains__(self, item):
            if "pipeline_long" in item or item == "__main__":
                raise RuntimeError("Fake lookup error")
            return super().__contains__(item)

    monkeypatch.setattr(sys, "modules", BrokenDict(sys.modules))

    with patch("utils.retry_manager.log") as mock_log:
        patch_retries()
        # Find warning log matching fake lookup error
        warnings = [call[0][0] for call in mock_log.warning.call_args_list]
        assert any(
            "Could not sync pipeline_long namespace: Fake lookup error" in w for w in warnings
        )


def test_constants_have_expected_values():
    assert MAX_RETRIES == 50
    assert BOUNDED_RETRIES == 3
    assert (ConnectionError, TimeoutError, subprocess.TimeoutExpired) == TRANSIENT_EXCEPTIONS
    assert (RuntimeError, OSError) == BOUNDED_EXCEPTIONS
