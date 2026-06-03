"""test_llm_client.py - Comprehensive unit tests for agents/llm_client.py"""

import sys
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure parent directory is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.llm_client import DirectorLlmClient
from agents.ui_state import UIState


def test_resolve_model_variations():
    """Test DirectorLlmClient._resolve_model with various config schemas."""
    # Dict with models section
    client1 = DirectorLlmClient(
        {"models": {"director": "my-director-model", "writer": "my-writer-model"}}
    )
    assert client1._resolve_model("director") == "my-director-model"
    assert client1._resolve_model("writer") == "my-writer-model"
    assert client1._resolve_model("unknown") == "llama3"  # default fallback

    # Dict without nested models section (flat schema)
    client2 = DirectorLlmClient(
        {"director": "flat-director-model", "default": "flat-default-model"}
    )
    assert client2._resolve_model("director") == "flat-director-model"
    assert client2._resolve_model("unknown") == "flat-default-model"

    # Non-dict config fallback
    client3 = DirectorLlmClient(None)
    assert client3._resolve_model("director") == "llama3"


def test_ollama_opts():
    """Test DirectorLlmClient._ollama_opts parsing options."""
    # Custom config
    client1 = DirectorLlmClient(
        {
            "ollama": {
                "host": "http://ollama-test:11434",
                "request_timeout": "180",
                "keep_alive": "5m",
            }
        }
    )
    host, timeout, keep_alive = client1._ollama_opts()
    assert host == "http://ollama-test:11434"
    assert timeout == 180
    assert keep_alive == "5m"

    # Defaults config
    client2 = DirectorLlmClient({})
    host, timeout, keep_alive = client2._ollama_opts()
    assert host == "http://localhost:11434"
    assert timeout == 240
    assert keep_alive == "3m"


def test_call_ollama_success():
    """Test _call_ollama correctly delegates to get_ollama_client and generates."""
    client = DirectorLlmClient({"models": {"director": "test-director"}})

    mock_client = MagicMock()
    mock_client.generate.return_value = "Ollama response"

    with patch(
        "utils.ollama_client.get_ollama_client", return_value=mock_client
    ) as mock_get_client:
        res = client._call_ollama("test prompt", model_type="director", format_json=True, seed=42)

        assert res == "Ollama response"
        mock_get_client.assert_called_once_with(client.llm_config)
        mock_client.generate.assert_called_once_with(
            "test prompt", model="test-director", format_json=True, seed=42
        )


def test_call_ollama_exception_handling():
    """Test _call_ollama returns an empty string on exception instead of raising."""
    client = DirectorLlmClient({})

    with patch(
        "utils.ollama_client.get_ollama_client", side_effect=RuntimeError("Connection refused")
    ):
        res = client._call_ollama("test prompt")
        assert res == ""  # Never return None, returns empty string


def test_call_ollama_chat_success():
    """Test _call_ollama_chat correctly delegates to get_ollama_client and calls chat."""
    client = DirectorLlmClient({"models": {"translator": "test-translator"}})

    mock_client = MagicMock()
    mock_client.chat.return_value = "Chat response"

    with patch(
        "utils.ollama_client.get_ollama_client", return_value=mock_client
    ) as mock_get_client:
        res = client._call_ollama_chat(
            "translate this", model_type="translator", system_msg="custom system"
        )

        assert res == "Chat response"
        mock_get_client.assert_called_once_with(client.llm_config)
        mock_client.chat.assert_called_once_with(
            [{"role": "user", "content": "translate this"}],
            model="test-translator",
            system_msg="custom system",
        )


def test_call_ollama_chat_exception_handling():
    """Test _call_ollama_chat returns empty string on exception."""
    client = DirectorLlmClient({})
    with patch("utils.ollama_client.get_ollama_client", side_effect=RuntimeError("Chat failed")):
        res = client._call_ollama_chat("test prompt")
        assert res == ""


def test_call_ollama_streaming_success():
    """Test _call_ollama_streaming streams response and parses JSON chunks."""
    client = DirectorLlmClient(
        {"ollama": {"host": "http://localhost:11434"}, "models": {"director": "llama3"}}
    )

    # Prepare mock chunks as they would be returned from Ollama stream
    chunks = [
        b'{"response": "{\\"hero\\":", "done": false}',
        b'{"response": " \\"Rama\\"}", "done": true, "total_duration": 5000000000}',
    ]

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = chunks

    # Track UIState logs
    uistate_logs = []

    def mock_ui_log(msg):
        uistate_logs.append(msg)

    with (
        patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen,
        patch.object(UIState, "_uistate_log", side_effect=mock_ui_log),
    ):
        result = client._call_ollama_streaming("Give me Rama in JSON", label="TestStream")

        assert result == '{"hero": "Rama"}'
        assert len(uistate_logs) >= 2
        assert any("Streaming..." in msg for msg in uistate_logs)
        assert any("Done: 2 tokens in 5.0s" in msg for msg in uistate_logs)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:11434/api/generate"
        assert req.get_header("Content-type") == "application/json"


def test_call_ollama_streaming_retries():
    """Test _call_ollama_streaming retries on urllib exceptions and fails after 3 attempts."""
    client = DirectorLlmClient({})

    # Mock urllib.request.urlopen to always raise an exception
    with (
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection reset")),
        patch("time.sleep") as mock_sleep,
    ):
        with pytest.raises(RuntimeError) as exc_info:
            client._call_ollama_streaming("prompt")

        assert "Streaming failed after 3 attempts" in str(exc_info.value)
        # Should sleep 2 times: 2^1 = 2s, 2^2 = 4s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(2.0)
        mock_sleep.assert_any_call(4.0)


def test_prewarm_ollama():
    """Test _prewarm_ollama launches threads calling _call_ollama for director and writer models."""
    client = DirectorLlmClient({})

    # We patch Thread to immediately execute the target function synchronously
    # so we can easily check assertions.
    threads_started = []

    class SyncThread:
        def __init__(self, target, args=(), kwargs=None, daemon=True):
            self.target = target
            self.args = args

        def start(self):
            threads_started.append(self.args[0])
            self.target(*self.args)

    with (
        patch("threading.Thread", side_effect=SyncThread),
        patch.object(client, "_call_ollama") as mock_call,
    ):
        client._prewarm_ollama()

        # Verify that both director and writer threads started
        assert "director" in threads_started
        assert "writer" in threads_started

        # Verify call was made for both models
        assert mock_call.call_count == 2
        mock_call.assert_any_call("Hello", model_type="director")
        mock_call.assert_any_call("Hello", model_type="writer")
