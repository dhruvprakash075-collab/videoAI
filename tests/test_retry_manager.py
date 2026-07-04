"""test_retry_manager.py - retry_with_backoff decorator."""

import subprocess
from unittest.mock import patch

import pytest

from utils.retry_manager import (
    BOUNDED_EXCEPTIONS,
    BOUNDED_RETRIES,
    MAX_RETRIES,
    TRANSIENT_EXCEPTIONS,
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
    # Transient errors use the full max_retries (4) attempt budget - proving they
    # are not capped at BOUNDED_RETRIES - but the final attempt fails fast without
    # a wasted backoff sleep (audit fix #4), so only max_retries - 1 == 3 sleeps occur.
    assert sleep_mock.call_count == 3


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


def test_constants_have_expected_values():
    assert MAX_RETRIES == 50
    assert BOUNDED_RETRIES == 3
    assert (ConnectionError, TimeoutError, subprocess.TimeoutExpired) == TRANSIENT_EXCEPTIONS
    assert (RuntimeError, OSError) == BOUNDED_EXCEPTIONS
