"""test_crewai_breaker.py - Comprehensive unit tests for utils/crewai_breaker.py"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure parent directory is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import utils.crewai_breaker as breaker
from utils.ollama_client import _BreakerState


@pytest.fixture(autouse=True)
def clean_breakers():
    """Clean the fallback breaker cache between tests."""
    breaker._fallback_breakers.clear()


def test_get_breaker_ollama_client_success():
    """Test _get_breaker retrieves the breaker from OllamaClient."""
    mock_client = MagicMock()
    mock_breaker = MagicMock()
    mock_client._breaker.return_value = mock_breaker

    with patch("utils.ollama_client.get_ollama_client", return_value=mock_client):
        res = breaker._get_breaker("model-x")
        assert res == mock_breaker
        mock_client._breaker.assert_called_once_with("model-x")


def test_get_breaker_fallback_path():
    """Test _get_breaker falls back to local BreakerState if OllamaClient fails or is missing."""
    with patch(
        "utils.ollama_client.get_ollama_client", side_effect=Exception("Failed to get client")
    ):
        b1 = breaker._get_breaker("model-y", fails_threshold=2, cooldown_s=10.0)
        assert b1 is not None
        assert b1._fails_thresh == 2
        assert b1._cooldown_s == 10.0

        # Verify caching of the fallback breaker
        b2 = breaker._get_breaker("model-y")
        assert b1 is b2


def test_record_success_failure_and_is_open():
    """Test record_breaker_success, record_breaker_failure, and is_breaker_open utilities."""
    model_name = "test-model"

    # We will test using fallback breakers by forcing an error on get_ollama_client
    with patch("utils.ollama_client.get_ollama_client", side_effect=Exception):
        # Initial state should be closed
        assert not breaker.is_breaker_open(model_name)

        # Record success
        breaker.record_breaker_success(model_name)
        assert not breaker.is_breaker_open(model_name)

        # Record failures to trip the breaker (threshold is 3 by default)
        breaker.record_breaker_failure(model_name)
        breaker.record_breaker_failure(model_name)
        assert not breaker.is_breaker_open(model_name)  # 2 fails, not open yet

        breaker.record_breaker_failure(model_name)
        assert breaker.is_breaker_open(model_name)  # 3 fails, should be open

        # If breaker is open, records success should close it
        breaker.record_breaker_success(model_name)
        assert not breaker.is_breaker_open(model_name)


def test_guarded_crewai_kickoff_success():
    """Test guarded_crewai_kickoff executes successfully and records success."""
    model_name = "success-model"
    crew_mock = MagicMock()
    crew_mock.kickoff.return_value = "Success output"

    # Mock get_breaker to return a clean local breaker
    local_breaker = _BreakerState(3, 30.0)
    with patch("utils.crewai_breaker._get_breaker", return_value=local_breaker):
        res = breaker.guarded_crewai_kickoff(crew_mock, model_name=model_name)
        assert res == "Success output"
        assert local_breaker.state == _BreakerState.CLOSED
        assert local_breaker._fail_count == 0


def test_guarded_crewai_kickoff_fast_fail_open_breaker():
    """Test guarded_crewai_kickoff fails fast without executing when breaker is open."""
    model_name = "open-model"
    crew_mock = MagicMock()

    # Set up an open breaker
    local_breaker = _BreakerState(3, 30.0)
    local_breaker._state = _BreakerState.OPEN
    local_breaker._open_until = time.time() + 30.0

    with patch("utils.crewai_breaker._get_breaker", return_value=local_breaker):
        with pytest.raises(breaker.BreakerOpen) as exc_info:
            breaker.guarded_crewai_kickoff(crew_mock, model_name=model_name)

        assert exc_info.value.model == model_name
        assert exc_info.value.cooldown_s > 0
        # Check that kickoff was never called
        crew_mock.kickoff.assert_not_called()


def test_guarded_crewai_kickoff_failure_tripping():
    """Test guarded_crewai_kickoff failures propagate and trip the breaker."""
    model_name = "failing-model"
    crew_mock = MagicMock()
    crew_mock.kickoff.side_effect = ValueError("LiteLLM connection error")

    local_breaker = _BreakerState(2, 10.0)  # threshold = 2
    with patch("utils.crewai_breaker._get_breaker", return_value=local_breaker):
        # First failure
        with pytest.raises(ValueError):
            breaker.guarded_crewai_kickoff(crew_mock, model_name=model_name)
        assert local_breaker.state == _BreakerState.CLOSED
        assert local_breaker._fail_count == 1

        # Second failure -> should trip breaker
        with pytest.raises(ValueError):
            breaker.guarded_crewai_kickoff(crew_mock, model_name=model_name)
        assert local_breaker.state == _BreakerState.OPEN


def test_guarded_crewai_kickoff_timeout():
    """Test guarded_crewai_kickoff raises TimeoutError if execution times out."""
    model_name = "slow-model"
    crew_mock = MagicMock()

    # Simulate a slow process that sleeps longer than the timeout
    def slow_kickoff():
        time.sleep(2.0)
        return "too late"

    crew_mock.kickoff.side_effect = slow_kickoff

    local_breaker = _BreakerState(3, 30.0)
    with patch("utils.crewai_breaker._get_breaker", return_value=local_breaker):
        with pytest.raises(TimeoutError) as exc_info:
            breaker.guarded_crewai_kickoff(crew_mock, model_name=model_name, timeout_s=0.2)

        assert "exceeded" in str(exc_info.value)
        # Verify the failure was recorded on the breaker
        assert local_breaker._fail_count == 1


def test_guarded_crewai_kickoff_with_lock():
    """Test guarded_crewai_kickoff acquires and releases the RLock if provided."""
    model_name = "locked-model"
    crew_mock = MagicMock()
    crew_mock.kickoff.return_value = "Result"

    lock = MagicMock()

    res = breaker.guarded_crewai_kickoff(crew_mock, model_name=model_name, lock=lock)
    assert res == "Result"
    lock.__enter__.assert_called_once()
    lock.__exit__.assert_called_once()


def test_guarded_ollama_call_success():
    """Test guarded_ollama_call returns response from OllamaClient."""
    mock_client = MagicMock()
    mock_client.generate.return_value = "Ollama output"

    with patch("utils.ollama_client.get_ollama_client", return_value=mock_client):
        res = breaker.guarded_ollama_call(
            "prompt", "model", format_json=True, temperature=0.5, num_predict=100
        )
        assert res == "Ollama output"
        mock_client.generate.assert_called_once_with(
            "prompt", model="model", format_json=True, temperature=0.5, num_predict=100
        )


def test_guarded_ollama_call_exception():
    """Test guarded_ollama_call returns empty string on exception."""
    # Case 1: get_ollama_client fails
    with patch("utils.ollama_client.get_ollama_client", side_effect=Exception("No client")):
        assert breaker.guarded_ollama_call("p", "m") == ""

    # Case 2: generate raises an exception
    mock_client = MagicMock()
    mock_client.generate.side_effect = Exception("Generate failed")
    with patch("utils.ollama_client.get_ollama_client", return_value=mock_client):
        assert breaker.guarded_ollama_call("p", "m") == ""
