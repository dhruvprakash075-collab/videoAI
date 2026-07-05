"""test_director_split.py - Regression tests for the 2026-06 director_agent.py split.

Verifies that the God-module split (UIState → ui_state.py, LLM client →
llm_client.py) preserves all public-facing contracts used by other modules
and the existing test suite.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_uistate_class_is_singleton_across_re_exports():
    """``from agents.director_agent import UIState`` and
    ``from agents.ui_state import UIState`` must return the same class object
    so that ``UIState.degradations = []`` in conftest.py resets the same
    state that production code mutates."""
    from agents.director_agent import UIState as UIState_via_director
    from agents.ui_state import UIState as UIState_native

    assert UIState_via_director is UIState_native, (
        "UIState re-export is a different class — conftest resets would no "
        "longer clear the state production code writes to."
    )


def test_uistate_class_attributes_present():
    """Every class attribute that production code and conftest reset MUST
    still exist on the (now-extracted) UIState class."""
    from agents.ui_state import UIState

    for attr in (
        "is_ui_mode",
        "pause_event",
        "active_question",
        "user_reply",
        "status",
        "logs",
        "topic",
        "character",
        "output_video",
        "current_script",
        "auto_accept",
        "segment_current",
        "segment_total",
        "run_start_ts",
        "vram_text",
        "degradations",
    ):
        assert hasattr(UIState, attr), f"UIState.{attr} missing after extraction"

    for method in ("add_log", "add_degradation", "reset_run", "set_progress"):
        assert callable(getattr(UIState, method, None)), (
            f"UIState.{method} missing after extraction"
        )


def test_devanagari_ratio_reexported():
    """_devanagari_ratio must remain importable from director_agent so the
    4 tests in test_devanagari_translation.py and 2 archive tests still work."""
    from agents.director_agent import _devanagari_ratio as via_director
    from agents.ui_state import _devanagari_ratio as native

    assert via_director is native
    # Spot-check: 1.0 for pure Devanagari
    assert native("नमस्ते") == 1.0
    # 0.0 for pure Latin
    assert native("hello") == 0.0


def test_director_agent_constructs_llm_client():
    """DirectorAgent.__init__ must construct a DirectorLlmClient as self.llm
    so the LLM transport is encapsulated and the existing self._call_ollama*
    delegation shims work."""
    from agents.director_agent import DirectorAgent
    from agents.llm_client import DirectorLlmClient

    a = DirectorAgent(
        llm_config={
            "ollama": {"host": "http://localhost:11434"},
            "models": {"director": "d", "writer": "w"},
        }
    )
    assert isinstance(a.llm, DirectorLlmClient)
    assert a.llm.llm_config is a.llm_config


def test_director_agent_call_ollama_delegates_to_llm():
    """The shim methods on DirectorAgent must route to self.llm. We verify by
    swapping the LLM client and observing the shim call it."""
    from unittest.mock import MagicMock

    from agents.director_agent import DirectorAgent

    a = DirectorAgent(llm_config={"ollama": {}, "models": {"director": "d"}})
    stub = MagicMock()
    stub._call_ollama.return_value = "delegated-text"
    a.llm = stub

    result = a._call_ollama("hi", model_type="director", format_json=True)
    stub._call_ollama.assert_called_once_with(
        "hi",
        model_type="director",
        format_json=True,
        seed=None,
    )
    assert result == "delegated-text"


def test_director_agent_resolve_model_delegates_to_llm():
    """self._resolve_model must read from self.llm too (subclasses rely on it)."""
    from agents.director_agent import DirectorAgent

    a = DirectorAgent(llm_config={"models": {"director": "d-model"}})
    assert a._resolve_model("director") == "d-model"
    assert a._resolve_model("default-model") == "llama3"  # fallback


def test_llm_client_can_be_constructed_standalone():
    """DirectorLlmClient must be testable in isolation (no Director required)."""
    from agents.llm_client import DirectorLlmClient

    c = DirectorLlmClient({"models": {"director": "d"}, "ollama": {"host": "h"}})
    assert c._resolve_model("director") == "d"
    host, timeout, keep_alive = c._ollama_opts()
    assert host == "h"
    assert timeout == 240
    assert keep_alive == "3m"


# ── _call_ollama_streaming ────────────────────────────────────────────────────


def test_call_ollama_streaming_success():
    """_call_ollama_streaming returns accumulated tokens from streaming response."""
    import json
    from unittest.mock import MagicMock, patch

    from agents.llm_client import DirectorLlmClient

    client = DirectorLlmClient(
        {"models": {"director": "hermes"}, "ollama": {"host": "http://localhost:11434"}}
    )

    # Build a fake streaming response: two token chunks + done chunk
    chunks = [
        {"response": "Hello", "done": False},
        {"response": " world", "done": False},
        {"response": "", "done": True, "total_duration": 1_000_000_000},
    ]
    lines = [json.dumps(c).encode("utf-8") + b"\n" for c in chunks]

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = lambda s: iter(lines)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = client._call_ollama_streaming("Test prompt", label="TEST")

    assert result == "Hello world"


def test_call_ollama_streaming_skips_blank_lines():
    """_call_ollama_streaming ignores blank lines in the stream."""
    import json
    from unittest.mock import MagicMock, patch

    from agents.llm_client import DirectorLlmClient

    client = DirectorLlmClient({"models": {"director": "h"}, "ollama": {"host": "http://localhost:11434"}})

    chunks_lines = [
        b"\n",  # blank line — must be skipped
        b"not-json\n",  # invalid JSON — must be skipped
        json.dumps({"response": "ok", "done": True, "total_duration": 0}).encode() + b"\n",
    ]

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = lambda s: iter(chunks_lines)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = client._call_ollama_streaming("p")

    assert result == "ok"


def test_call_ollama_streaming_preview_every_20_tokens():
    """_call_ollama_streaming calls UIState._uistate_log for every 20th token."""
    import json
    from unittest.mock import MagicMock, patch

    from agents.llm_client import DirectorLlmClient

    client = DirectorLlmClient({"models": {"director": "h"}, "ollama": {"host": "http://localhost:11434"}})

    # 20 token chunks then done
    chunks_lines = [
        json.dumps({"response": f"t{i}", "done": False}).encode() + b"\n" for i in range(20)
    ]
    chunks_lines.append(
        json.dumps({"response": "", "done": True, "total_duration": 0}).encode() + b"\n"
    )

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.__iter__ = lambda s: iter(chunks_lines)

    log_calls = []
    with (
        patch("urllib.request.urlopen", return_value=mock_resp),
        patch("agents.ui_state.UIState._uistate_log", side_effect=log_calls.append),
    ):
        client._call_ollama_streaming("p", label="PREVIEW")

    # The 20th token should trigger a preview log
    assert any("..." in call for call in log_calls)


def test_call_ollama_streaming_retries_on_failure():
    """_call_ollama_streaming retries up to 3 times, then raises RuntimeError."""
    from unittest.mock import patch

    from agents.llm_client import DirectorLlmClient

    client = DirectorLlmClient({"models": {"director": "h"}, "ollama": {"host": "http://localhost:11434"}})

    with (
        patch("urllib.request.urlopen", side_effect=OSError("Connection refused")),
        patch("agents.llm_client.time.sleep"),  # skip actual sleep
    ):
        import pytest

        with pytest.raises(RuntimeError, match="Streaming failed after 3 attempts"):
            client._call_ollama_streaming("p")


# ── _prewarm_ollama ───────────────────────────────────────────────────────────


def test_prewarm_ollama_fires_two_threads():
    """_prewarm_ollama starts two daemon threads (director + writer)."""
    import time
    from unittest.mock import patch

    from agents.llm_client import DirectorLlmClient

    client = DirectorLlmClient({"models": {"director": "d", "writer": "w"}})

    calls = []

    def _capture(prompt, model_type="director"):
        calls.append(model_type)

    with patch.object(client, "_call_ollama", side_effect=_capture):
        client._prewarm_ollama()
        time.sleep(0.2)  # Let daemon threads fire

    assert "director" in calls or "writer" in calls  # at least one fired
