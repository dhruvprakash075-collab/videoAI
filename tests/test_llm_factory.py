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

    def _fake_create_llm(model_name, host="http://localhost:11434",
                         timeout=240, max_tokens=2048):
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

    def _fake_create_llm(model_name, host="http://localhost:11434",
                         timeout=240, max_tokens=2048):
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

    def _fake_create_llm(model_name, host="http://localhost:11434",
                         timeout=240, max_tokens=2048):
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

    def _fake_create_llm(model_name, host="http://localhost:11434",
                         timeout=240, max_tokens=2048):
        calls.append({"max_tokens": max_tokens})
        return f"ollama/{model_name}"

    monkeypatch.setattr(cm, "_create_ollama_llm", _fake_create_llm)

    cfg = _make_cfg(models={
        "director": "hermes-director",
        "writer": "zephyr-writer",
        "director_max_tokens": 4096,
    })
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
