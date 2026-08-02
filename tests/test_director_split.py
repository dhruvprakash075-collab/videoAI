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


# ── WS-4 mixin split ─────────────────────────────────────────────────────────


def _all_mixins():
    """The 8 role mixins backing the DirectorAgent facade."""
    from agents.director.config_production import ConfigProductionMixin
    from agents.director.consultation import ConsultationMixin
    from agents.director.llm_shims import LlmShimsMixin
    from agents.director.memory_sync import MemorySyncMixin
    from agents.director.prompts import PromptsMixin
    from agents.director.story import StoryMixin
    from agents.director.translation import TranslationMixin
    from agents.director.vision import VisionMixin

    return (
        ConfigProductionMixin,
        ConsultationMixin,
        LlmShimsMixin,
        MemorySyncMixin,
        PromptsMixin,
        StoryMixin,
        TranslationMixin,
        VisionMixin,
    )


def test_mixin_modules_import_standalone():
    """Each mixin module imports on its own (no facade dependency, no cycle)."""
    import importlib

    for module_name in (
        "agents.director.config_production",
        "agents.director.consultation",
        "agents.director.llm_shims",
        "agents.director.memory_sync",
        "agents.director.prompts",
        "agents.director.story",
        "agents.director.translation",
        "agents.director.vision",
    ):
        module = importlib.import_module(module_name)
        assert module.__name__ == module_name

    for mixin in _all_mixins():
        assert isinstance(mixin, type)


def test_mixins_do_not_define_init():
    """Only the facade may own __init__ (mixin-inheritance contract)."""
    for mixin in _all_mixins():
        assert "__init__" not in mixin.__dict__, f"{mixin.__name__} defines __init__"


def test_director_agent_mro_contains_all_mixins():
    """DirectorAgent.__mro__ must contain all 8 mixins, with the helper
    mixins (LlmShimsMixin, PromptsMixin) rightmost so nothing shadows them."""
    from agents.director.llm_shims import LlmShimsMixin
    from agents.director.prompts import PromptsMixin
    from agents.director_agent import DirectorAgent

    mro = list(DirectorAgent.__mro__)
    for mixin in _all_mixins():
        assert mixin in mro, f"{mixin.__name__} missing from DirectorAgent.__mro__"

    role_mixins = [
        m
        for m in mro
        if m is not DirectorAgent
        and m not in (LlmShimsMixin, PromptsMixin)
        and m is not object
    ]
    assert mro.index(LlmShimsMixin) > max(mro.index(m) for m in role_mixins)
    assert mro.index(PromptsMixin) > max(mro.index(m) for m in role_mixins)


def test_facade_class_instantiates_with_mixins():
    """The facade class is still agents.director_agent.DirectorAgent and
    instantiates; helpers resolve through the mixin MRO."""
    from agents.director_agent import DirectorAgent

    a = DirectorAgent(
        llm_config={
            "ollama": {"host": "http://localhost:11434"},
            "models": {"director": "d", "translator": "t"},
        }
    )
    assert isinstance(a, DirectorAgent)
    assert a._resolve_model("director") == "d"  # LlmShimsMixin
    assert a._parse_json("") == {}  # PromptsMixin
    assert a._prompt("nonexistent_template_key") == ""  # PromptsMixin
    assert a._topic_key("Real Hero") == "real_hero"  # VisionMixin
    assert a._last_estimated_minutes == 10  # facade __init__ state


def test_facade_reexports_public_names():
    """Every name consumers import from agents.director_agent must still exist."""
    from agents.director_agent import (
        DirectorAgent,
        DirectorLlmClient,
        UIState,
        _devanagari_ratio,
        extract_json,
        hinglish_ratio,
        protect_hinglish,
        restore_hinglish,
        transliterate_latin_runs,
    )

    assert callable(DirectorAgent)
    assert isinstance(DirectorLlmClient, type)
    assert isinstance(UIState, type)
    assert callable(_devanagari_ratio)
    assert callable(extract_json)
    assert callable(hinglish_ratio)
    assert callable(protect_hinglish)
    assert callable(restore_hinglish)
    assert callable(transliterate_latin_runs)
