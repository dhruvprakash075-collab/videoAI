"""test_ollama_client.py - Tests for B1: OllamaClient with 3-state circuit breaker."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch

import pytest

from utils.ollama_client import OllamaClient, _BreakerState, reset_ollama_client


@pytest.fixture(autouse=True)
def reset_client():
    reset_ollama_client()
    yield
    reset_ollama_client()


def _make_client(fails=3, cooldown=30):
    return OllamaClient(
        {
            "ollama": {
                "host": "http://localhost:11434",
                "request_timeout": 10,
                "keep_alive": "3m",
                "breaker_fails": fails,
                "breaker_cooldown_s": cooldown,
            }
        }
    )


def _fake_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps({"response": text}).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── Breaker state machine ──────────────────────────────────────────────────


def test_breaker_starts_closed():
    b = _BreakerState(fails_threshold=3, cooldown_s=30)
    assert b.state == "closed"
    assert b.allow_request() is True


def test_breaker_opens_after_n_failures():
    b = _BreakerState(fails_threshold=3, cooldown_s=30)
    for _ in range(3):
        b.record_failure()
    assert b.state == "open"
    assert b.allow_request() is False


def test_breaker_half_open_after_cooldown():
    import time

    b = _BreakerState(fails_threshold=2, cooldown_s=0.01)
    b.record_failure()
    b.record_failure()
    assert b.state == "open"
    time.sleep(0.05)
    # After cooldown, allow_request should transition to half-open
    assert b.allow_request() is True
    assert b.state == "half_open"


def test_breaker_closes_on_probe_success():
    import time

    b = _BreakerState(fails_threshold=2, cooldown_s=0.01)
    b.record_failure()
    b.record_failure()
    time.sleep(0.05)
    b.allow_request()  # → half-open
    b.record_success()
    assert b.state == "closed"


def test_breaker_reopens_on_probe_failure():
    import time

    b = _BreakerState(fails_threshold=2, cooldown_s=0.01)
    b.record_failure()
    b.record_failure()
    time.sleep(0.05)
    b.allow_request()  # → half-open
    b.record_failure()
    assert b.state == "open"


# ── OllamaClient.generate ─────────────────────────────────────────────────


def test_generate_returns_text_on_success():
    client = _make_client()
    with patch("urllib.request.urlopen", return_value=_fake_response("Hello world")):
        result = client.generate("Hi", model="test-model")
    assert result == "Hello world"


def test_generate_returns_empty_when_breaker_open():
    client = _make_client(fails=1, cooldown=60)
    # Trip the breaker
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        client.generate("Hi", model="test-model")
    # Breaker should now be open
    result = client.generate("Hi", model="test-model")
    assert result == ""


def test_generate_retries_on_transient_error():
    client = _make_client()
    call_count = [0]

    def fake_urlopen(req, timeout=None):
        call_count[0] += 1
        if call_count[0] < 3:
            raise OSError("transient")
        return _fake_response("Success")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("time.sleep"):
        result = client.generate("Hi", model="test-model")

    assert result == "Success"
    assert call_count[0] == 3


# ── OllamaClient.chat ─────────────────────────────────────────────────────


def test_chat_returns_text_on_success():
    client = _make_client()
    chat_resp = MagicMock()
    chat_resp.read.return_value = json.dumps({"message": {"content": "Chat reply"}}).encode()
    chat_resp.__enter__ = lambda s: s
    chat_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=chat_resp):
        result = client.chat([{"role": "user", "content": "Hi"}], model="test-model")
    assert result == "Chat reply"


# ── Timeout passthrough ───────────────────────────────────────────────────


def test_timeout_from_config_is_used():
    client = OllamaClient(
        {
            "ollama": {
                "host": "http://localhost:11434",
                "request_timeout": 999,
                "breaker_fails": 3,
                "breaker_cooldown_s": 30,
            }
        }
    )
    captured_timeout = []

    def fake_urlopen(req, timeout=None):
        captured_timeout.append(timeout)
        return _fake_response("ok")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.generate("Hi", model="test-model")

    assert captured_timeout[0] == 999


# ── B1 wiring: director_agent + translate_hinglish delegate to the client ──


def test_director_call_ollama_delegates_to_client():
    """DirectorAgent._call_ollama should route through the shared OllamaClient."""
    reset_ollama_client()
    from agents.director_agent import DirectorAgent

    agent = DirectorAgent(
        llm_config={
            "ollama": {
                "host": "http://localhost:11434",
                "request_timeout": 10,
                "breaker_fails": 3,
                "breaker_cooldown_s": 30,
            },
            "models": {"director": "test-director"},
        }
    )
    with patch("urllib.request.urlopen", return_value=_fake_response("Director says hi")):
        result = agent._call_ollama("Hello", model_type="director")
    assert result == "Director says hi"
    reset_ollama_client()


def test_director_call_ollama_returns_empty_on_breaker_open():
    """When the breaker is open for the model, _call_ollama returns '' (never None)."""
    reset_ollama_client()
    from agents.director_agent import DirectorAgent

    agent = DirectorAgent(
        llm_config={
            "ollama": {
                "host": "http://localhost:11434",
                "request_timeout": 10,
                "breaker_fails": 1,
                "breaker_cooldown_s": 60,
            },
            "models": {"director": "test-director"},
        }
    )
    # Trip the breaker (1 failure opens it)
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        agent._call_ollama("Hello", model_type="director")
    # Next call should fail fast and return ""
    result = agent._call_ollama("Hello", model_type="director")
    assert result == ""
    reset_ollama_client()


def test_translate_hinglish_delegates_and_records_seg_on_failure():
    """translate_hinglish should use the client and record the real seg on failure."""
    reset_ollama_client()
    import audio.audio_proxy as ap
    from agents.director_agent import UIState

    UIState.degradations = []
    # Force the client to fail so the fallback degradation path runs
    with patch("urllib.request.urlopen", side_effect=OSError("down")), patch("time.sleep"):
        out = ap.translate_hinglish("Hello world", seg=7)
    # Falls back to original text
    assert out == "Hello world"
    # Degradation recorded with the REAL segment number (not hardcoded 0)
    assert any(d["seg"] == 7 and d["stage"] == "translation_fallback" for d in UIState.degradations)
    reset_ollama_client()


def test_environment_variables_are_honored():
    """OllamaClient should prioritize OLLAMA_HOST or OLLAMA_BASE_URL environment variables."""
    import os

    reset_ollama_client()

    # Test OLLAMA_HOST
    with patch.dict(os.environ, {"OLLAMA_HOST": "http://my-remote-ollama-host:11434"}):
        client = OllamaClient(
            {
                "ollama": {
                    "host": "http://localhost:11434",
                    "request_timeout": 10,
                }
            }
        )
        assert client._host == "http://my-remote-ollama-host:11434"

    reset_ollama_client()

    # Test OLLAMA_BASE_URL
    # Ensure OLLAMA_HOST is not in env
    with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://my-other-remote-host:11434"}):
        if "OLLAMA_HOST" in os.environ:
            del os.environ["OLLAMA_HOST"]
        client = OllamaClient(
            {
                "ollama": {
                    "host": "http://localhost:11434",
                    "request_timeout": 10,
                }
            }
        )
        assert client._host == "http://my-other-remote-host:11434"
    reset_ollama_client()


# ── specialized_models on the B1 breaker ────────────────────────────────────


def test_specialized_models_routes_through_breaker():
    """specialized_models._call_ollama must go through OllamaClient so the
    per-model breaker trips on repeated failure instead of looping raw urllib.
    Regression test for: specialized_models had its own urllib+retry loop that
    bypassed the breaker and could hang a hung Ollama backend indefinitely.
    """
    reset_ollama_client()
    from utils.specialized_models import _call_ollama

    captured: dict = {}

    class _StubClient:
        def generate(self, prompt, model, **kwargs):
            captured["prompt"] = prompt
            captured["model"] = model
            captured["kwargs"] = kwargs
            return "stub-response"

    # Use a stub singleton so we don't touch real Ollama. The point of the
    # test is to prove _call_ollama delegates to client.generate, NOT urllib.
    with patch("utils.ollama_client.get_ollama_client", return_value=_StubClient()):
        result = _call_ollama(
            "hi there", "qwen2.5:0.5b", format_json=True, temperature=0.2, timeout=10
        )

    assert result == "stub-response"
    assert captured["model"] == "qwen2.5:0.5b"
    assert captured["kwargs"]["format_json"] is True
    assert captured["kwargs"]["temperature"] == 0.2
    assert captured["kwargs"]["num_predict"] == 4096
    reset_ollama_client()


def test_specialized_models_returns_none_when_client_fails():
    """When OllamaClient.generate returns '' (breaker open or error), the
    specialized_models helper must return None (its contract) — not ''.
    """
    reset_ollama_client()

    class _AlwaysFailClient:
        def generate(self, prompt, model, **kwargs):
            return ""  # breaker open or transport error

    with patch("utils.ollama_client.get_ollama_client", return_value=_AlwaysFailClient()):
        from utils.specialized_models import _call_ollama

        assert _call_ollama("hi", "image-engineer") is None
    reset_ollama_client()


def test_specialized_models_opens_breaker_after_repeated_failures():
    """Hammering the specialized model with 3+ transport errors should open
    the per-model breaker, causing subsequent calls to fail fast with None
    rather than sleeping through the urllib timeout repeatedly.
    """
    reset_ollama_client()
    from utils.ollama_client import OllamaClient

    cfg = {
        "ollama": {
            "host": "http://localhost:11434",
            "request_timeout": 5,
            "breaker_fails": 2,
            "breaker_cooldown_s": 30,
        }
    }
    client = OllamaClient(cfg)  # local instance, not the singleton

    def _raise(*_a, **_kw):
        raise OSError("connection refused")

    with patch.object(client, "_post", side_effect=_raise):
        # 2 failures — opens the breaker for this model
        assert _call_ollama_with(client, "a") is None
        assert _call_ollama_with(client, "b") is None
        # 3rd call should short-circuit via the breaker (no _post call)
        # Replace _post with a marker that would explode if called
        with patch.object(client, "_post", side_effect=AssertionError("breaker should be open")):
            assert _call_ollama_with(client, "c") is None

    reset_ollama_client()


def _call_ollama_with(client, prompt):
    """Drive _call_ollama with a specific OllamaClient instance (bypass singleton)."""
    from unittest.mock import patch

    from utils import specialized_models

    with patch("utils.ollama_client.get_ollama_client", return_value=client):
        return specialized_models._call_ollama(prompt, "qwen2.5:0.5b")


# ── Additional OllamaClient coverage tests ───────────────────────────────


def test_post_timeout_prevents_retries():
    client = _make_client()
    call_count = 0

    def fake_urlopen(req, timeout=None):
        nonlocal call_count
        call_count += 1
        raise TimeoutError("connection timed out")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(RuntimeError, match="timed out"):
            client._post("http://localhost:11434/api/generate", {}, 10)
    # TimeoutError should break out of loop immediately, so call_count is 1
    assert call_count == 1


def test_post_non_transient_error():
    client = _make_client()

    def fake_urlopen(req, timeout=None):
        # A non-transient error like ValueError will raise directly.
        raise ValueError("non-transient error")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(ValueError, match="non-transient error"):
            client._post("http://localhost:11434/api/generate", {}, 10)


def test_generate_with_seed():
    client = _make_client()
    captured_payload = []

    def fake_post(url, payload, timeout):
        captured_payload.append(payload)
        return {"response": "ok"}

    with patch.object(client, "_post", side_effect=fake_post):
        client.generate("Hi", model="test", seed=123)

    assert captured_payload[0]["options"]["seed"] == 123
    assert captured_payload[0]["options"]["temperature"] == 0.0


def test_generate_empty_response_raises():
    client = _make_client()
    with patch("urllib.request.urlopen", return_value=_fake_response("")):
        # returns "" and records breaker failure
        res = client.generate("Hi", model="test-model")
    assert res == ""
    assert client._breaker("test-model").state == "closed"  # only 1 fail (thresh is 3)


def test_generate_non_json_response_raises():
    client = _make_client()
    # format_json=True expects starting with { or [
    with patch("urllib.request.urlopen", return_value=_fake_response("not json")):
        res = client.generate("Hi", model="test-model", format_json=True)
    assert res == ""


def test_chat_with_system_message():
    client = _make_client()
    captured_payload = []

    def fake_post(url, payload, timeout):
        captured_payload.append(payload)
        return {"message": {"content": "ok"}}

    with patch.object(client, "_post", side_effect=fake_post):
        client.chat(
            [{"role": "user", "content": "Hi"}], model="test-model", system_msg="System prompt"
        )

    msgs = captured_payload[0]["messages"]
    assert msgs[0] == {"role": "system", "content": "System prompt"}
    assert msgs[1] == {"role": "user", "content": "Hi"}


def test_chat_breaker_open():
    client = _make_client(fails=1)
    # Trip the breaker
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        client.chat([{"role": "user", "content": "Hi"}], model="test-model")
    # Next call fails fast
    res = client.chat([{"role": "user", "content": "Hi"}], model="test-model")
    assert res == ""


def test_chat_exception_handling():
    client = _make_client()
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        res = client.chat([{"role": "user", "content": "Hi"}], model="test-model")
    assert res == ""


def test_stream_success():
    client = _make_client()
    fake_stream_lines = [
        b'{"response": "Hello", "done": false}\n',
        b'{"response": " world", "done": true}\n',
    ]
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.__iter__ = lambda s: iter(fake_stream_lines)

    with patch("urllib.request.urlopen", return_value=resp):
        res = client.stream("prompt", "test-model")
    assert res == "Hello world"


def test_stream_breaker_open():
    client = _make_client(fails=1)
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        client.stream("prompt", "test-model")
    res = client.stream("prompt", "test-model")
    assert res == ""


def test_stream_exception_handling():
    client = _make_client()
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        res = client.stream("prompt", "test-model")
    assert res == ""


def test_evict_ignores_exceptions():
    client = _make_client()
    with patch.object(client, "_post", side_effect=OSError("timeout")):
        client.evict("test-model")  # should not raise


def test_get_resident_models():
    client = _make_client()
    resp = MagicMock()
    resp.read.return_value = b'{"models": [{"name": "hermes"}, {"name": "zephyr"}]}'
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=resp):
        res = client.get_resident_models()
    assert res == ["hermes", "zephyr"]


def test_get_resident_models_exception():
    client = _make_client()
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        res = client.get_resident_models()
    assert res == []
