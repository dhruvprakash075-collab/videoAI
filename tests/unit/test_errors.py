"""test_errors.py - Unit tests for the central error taxonomy."""

import urllib.error
from contextlib import nullcontext

import pytest

from utils.errors import (
    ComfyUIError,
    DegradedResult,
    FatalError,
    OllamaError,
    RecoverableError,
    TTSError,
    VideoAIError,
    classify_errors,
)


def test_error_hierarchy():
    assert issubclass(FatalError, VideoAIError)
    assert issubclass(RecoverableError, VideoAIError)
    assert issubclass(DegradedResult, VideoAIError)
    assert issubclass(OllamaError, RecoverableError)
    assert issubclass(ComfyUIError, RecoverableError)
    assert issubclass(TTSError, RecoverableError)


def test_fatal_error_raises():
    with pytest.raises(FatalError):
        with classify_errors("test"):
            raise ValueError("something broke")


def test_recoverable_error_passthrough():
    """Already-classified errors pass through unchanged."""
    with pytest.raises(RecoverableError):
        with classify_errors("test"):
            raise RecoverableError("known issue")


def test_degraded_result_passthrough():
    with pytest.raises(DegradedResult):
        with classify_errors("test"):
            raise DegradedResult("degraded but ok")


def test_network_error_maps_to_recoverable():
    with pytest.raises(RecoverableError):
        with classify_errors("api_call"):
            raise urllib.error.URLError("connection refused")


def test_connection_error_maps_to_recoverable():
    with pytest.raises(RecoverableError):
        with classify_errors("api_call"):
            raise ConnectionError("timed out")


def test_timeout_error_maps_to_recoverable():
    with pytest.raises(RecoverableError):
        with classify_errors("api_call"):
            raise TimeoutError("timed out")


def test_classify_errors_no_error():
    """No exception: context manager is a no-op."""
    with nullcontext():
        with classify_errors("test"):
            pass


def test_classify_errors_stage_name_in_message():
    with pytest.raises(FatalError, match="Fatal failure in stage 'my_stage'"):
        with classify_errors("my_stage"):
            raise RuntimeError("unexpected")
