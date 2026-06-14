"""test_llm_factory.py - W1: verify per-role max_tokens wiring in create_writer/create_director.

Strategy: monkeypatch _create_ollama_llm (not LLM directly) so we capture the
kwargs passed to it without triggering CrewAI's Pydantic Agent validation.
"""

import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_cfg(**overrides):
    cfg = {
        "models": {
            "director": "hermes-director",
            "writer": "zephyr-writer",
        },
        "ollama": {"host": "http://localhost:11434", "request_timeout": 240},
        "script": {},
        "narrator_persona": "",
    }
    for k, v in overrides.items():
        cfg[k] = v
    return cfg


# ---------------------------------------------------------------------------
# Tests — patch _create_ollama_llm so we capture kwargs without Agent validation
# ---------------------------------------------------------------------------


def test_create_writer_default_max_tokens(monkeypatch):
    """create_writer should call _create_ollama_llm with writer_max_tokens=1024 (default)."""
    import core.main as cm

    calls = []

    def _fake_create_llm(model_name, host="http://localhost:11434", timeout=240, max_tokens=2048):
        calls.append({"model": model_name, "max_tokens": max_tokens, "timeout": timeout})
        # Return a real LLM string so Agent() is happy
        return f"ollama/{model_name}"

    monkeypatch.setattr(cm, "_create_ollama_llm", _fake_create_llm)
    monkeypatch.setattr(cm, "_ollama_model_available", lambda *a, **kw: True)

    cfg = _make_cfg()
    with contextlib.suppress(Exception):
        cm.create_writer(cfg)
    # Agent() may still fail; we only care about the kwargs captured
    assert calls, "Expected _create_ollama_llm to be called"
    assert calls[0]["max_tokens"] == 1024


def test_create_writer_config_max_tokens(monkeypatch):
    """create_writer should honour script.writer_max_tokens from config."""
    import core.main as cm

    calls = []

    def _fake_create_llm(model_name, host="http://localhost:11434", timeout=240, max_tokens=2048):
        calls.append({"max_tokens": max_tokens})
        return f"ollama/{model_name}"

    monkeypatch.setattr(cm, "_create_ollama_llm", _fake_create_llm)
    monkeypatch.setattr(cm, "_ollama_model_available", lambda *a, **kw: True)

    cfg = _make_cfg(script={"writer_max_tokens": 512})
    with contextlib.suppress(Exception):
        cm.create_writer(cfg)
    assert calls and calls[0]["max_tokens"] == 512


def test_create_director_default_max_tokens(monkeypatch):
    """create_director should call _create_ollama_llm with director_max_tokens=2048 (default)."""
    import core.main as cm

    calls = []

    def _fake_create_llm(model_name, host="http://localhost:11434", timeout=240, max_tokens=2048):
        calls.append({"max_tokens": max_tokens})
        return f"ollama/{model_name}"

    monkeypatch.setattr(cm, "_create_ollama_llm", _fake_create_llm)

    cfg = _make_cfg()
    with contextlib.suppress(Exception):
        cm.create_director(cfg)
    assert calls and calls[0]["max_tokens"] == 2048


def test_create_director_config_max_tokens(monkeypatch):
    """create_director should honour models.director_max_tokens from config."""
    import core.main as cm

    calls = []

    def _fake_create_llm(model_name, host="http://localhost:11434", timeout=240, max_tokens=2048):
        calls.append({"max_tokens": max_tokens})
        return f"ollama/{model_name}"

    monkeypatch.setattr(cm, "_create_ollama_llm", _fake_create_llm)

    cfg = _make_cfg(
        models={
            "director": "hermes-director",
            "writer": "zephyr-writer",
            "director_max_tokens": 4096,
        }
    )
    with contextlib.suppress(Exception):
        cm.create_director(cfg)
    assert calls and calls[0]["max_tokens"] == 4096


def test_num_retries_not_passed_to_llm():
    """_create_ollama_llm must NOT pass num_retries to LLM().

    Regression guard: this CrewAI version's OpenAI provider forwards unknown
    kwargs straight to openai's Completions.create(), which rejects
    'num_retries' with a TypeError and crashes every LLM call (observed as
    "No segments generated"). Retry suppression is done via the
    OPENAI_MAX_RETRIES env var in bootstrap, never as an LLM() kwarg.
    """
    import inspect

    import core.main as cm

    src = inspect.getsource(cm._create_ollama_llm)
    # The literal kwarg must not be present in the LLM() construction.
    assert "num_retries=0" not in src, (
        "num_retries=0 must NOT be passed to LLM() — it crashes CrewAI's "
        "OpenAI provider with a TypeError. Use OPENAI_MAX_RETRIES env instead."
    )


def test_max_tokens_in_create_ollama_llm_signature():
    """_create_ollama_llm must accept a max_tokens parameter (W1 contract)."""
    import inspect

    import core.main as cm

    sig = inspect.signature(cm._create_ollama_llm)
    assert "max_tokens" in sig.parameters, "W1: _create_ollama_llm must have max_tokens param"


# ── _ollama_model_available ───────────────────────────────────────────────────


def test_ollama_model_available_model_found(monkeypatch):
    """_ollama_model_available returns True when model is in tags list."""
    import json
    from unittest.mock import MagicMock, patch

    import core.main as cm

    response_data = json.dumps(
        {"models": [{"name": "zephyr-writer:latest"}, {"name": "hermes-director:latest"}]}
    ).encode()

    mock_response = MagicMock()
    mock_response.read.return_value = response_data
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = cm._ollama_model_available("zephyr-writer", "http://localhost:11434")

    assert result is True


def test_ollama_model_available_model_not_found(monkeypatch):
    """_ollama_model_available returns False when model is NOT in tags list."""
    import json
    from unittest.mock import MagicMock, patch

    import core.main as cm

    response_data = json.dumps({"models": [{"name": "other-model:latest"}]}).encode()

    mock_response = MagicMock()
    mock_response.read.return_value = response_data
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = cm._ollama_model_available("zephyr-writer", "http://localhost:11434")

    assert result is False


def test_ollama_model_available_network_error_raises_recoverable_error():
    """_ollama_model_available raises RecoverableError on network error."""
    import pytest
    from unittest.mock import patch
    from utils.errors import RecoverableError

    import core.main as cm

    with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
        with pytest.raises(RecoverableError) as exc_info:
            cm._ollama_model_available("any-model", "http://localhost:11434")
    assert "Ollama server is unreachable" in str(exc_info.value)


def test_create_writer_fallback_when_model_unavailable(monkeypatch):
    """When configured writer model is unavailable, fall back to director model."""
    import core.main as cm

    calls = []

    def _fake_create_llm(model_name, host="http://localhost:11434", timeout=240, max_tokens=2048):
        calls.append(model_name)
        return f"ollama/{model_name}"

    monkeypatch.setattr(cm, "_create_ollama_llm", _fake_create_llm)
    monkeypatch.setattr(cm, "_ollama_model_available", lambda *a, **kw: False)

    cfg = _make_cfg()
    import contextlib

    with contextlib.suppress(Exception):
        cm.create_writer(cfg)

    # Should have fallen back to the director model
    assert calls, "Expected _create_ollama_llm to be called"
    assert calls[0] == "hermes-director"


def test_create_agents_crew_returns_tuple(monkeypatch):
    """create_agents_crew should return a (director, writer) tuple."""
    import contextlib

    import core.main as cm

    calls = []

    def _fake_create_llm(model_name, host="http://localhost:11434", timeout=240, max_tokens=2048):
        calls.append(model_name)
        return f"ollama/{model_name}"

    monkeypatch.setattr(cm, "_create_ollama_llm", _fake_create_llm)
    monkeypatch.setattr(cm, "_ollama_model_available", lambda *a, **kw: True)

    cfg = _make_cfg()
    with contextlib.suppress(Exception):
        result = cm.create_agents_crew(cfg)
        if result is not None:
            assert isinstance(result, tuple)
            assert len(result) == 2


def test_create_ollama_llm_direct(monkeypatch):
    """_create_ollama_llm should return an LLM instance with correct parameters."""
    from unittest.mock import MagicMock, patch

    import core.main as cm

    mock_llm_instance = MagicMock()

    with patch("core.main.LLM", return_value=mock_llm_instance) as MockLLM:
        result = cm._create_ollama_llm("hermes-director", max_tokens=4096)

    assert result is mock_llm_instance
    MockLLM.assert_called_once()
    call_kwargs = MockLLM.call_args.kwargs
    assert call_kwargs.get("max_tokens") == 4096
    assert "hermes-director" in call_kwargs.get("model", "")
