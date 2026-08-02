"""test_llm_client.py - Comprehensive unit tests for agents/llm_client.py"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure parent directory is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.llm_client import DirectorLlmClient


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
            temperature=0.0,
            num_predict=768,
        )


def test_call_ollama_chat_exception_handling():
    """Test _call_ollama_chat returns empty string on exception."""
    client = DirectorLlmClient({})
    with patch("utils.ollama_client.get_ollama_client", side_effect=RuntimeError("Chat failed")):
        res = client._call_ollama_chat("test prompt")
        assert res == ""
