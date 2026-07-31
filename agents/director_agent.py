"""director_agent.py - Director Agent

The Director acts as the creative visionary, analyzing stories and consulting
users on production decisions before the video pipeline runs.

Module map (WS-4 refactor — mixin split, 2026-07)
--------------------------------------------------
* ``DirectorAgent`` is a thin facade over 8 role mixins in ``agents/director/``:
  ConsultationMixin, VisionMixin, ConfigProductionMixin, StoryMixin,
  TranslationMixin, MemorySyncMixin, LlmShimsMixin, PromptsMixin.
  Mixins share ``self`` (``self.llm``, ``self._prompts``, private state) and
  only the facade defines ``__init__``.
* ``UIState`` and ``_devanagari_ratio`` live in ``agents/ui_state.py``.
  Re-exported here for backward compat.
* LLM client methods (``_call_ollama*``, ``_prewarm_ollama``, ``_resolve_model``,
  ``_ollama_opts``) live in ``agents/llm_client.py`` as the
  ``DirectorLlmClient`` class. ``DirectorAgent.__init__`` constructs one
  in ``self.llm``; thin delegation shims in ``LlmShimsMixin`` preserve the
  public method names that tests and other modules rely on.
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path for top-level imports (style_resolver, utils.*)
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agents.director import (
    ConfigProductionMixin,
    ConsultationMixin,
    LlmShimsMixin,
    MemorySyncMixin,
    PromptsMixin,
    StoryMixin,
    TranslationMixin,
    VisionMixin,
)

# Re-exports for backward compat (UIState + Devanagari helper live in ui_state.py).
from utils.utils import extract_json

from .hinglish_glossary import (
    hinglish_ratio,
    protect_hinglish,
    restore_hinglish,
    transliterate_latin_runs,
)
from .llm_client import DirectorLlmClient
from .ui_state import UIState, _devanagari_ratio

__all__ = [
    "DirectorAgent",
    "DirectorLlmClient",
    "UIState",
    "_devanagari_ratio",
    "extract_json",
    "hinglish_ratio",
    "protect_hinglish",
    "restore_hinglish",
    "transliterate_latin_runs",
]

# ── DirectorAgent ──


class DirectorAgent(
    ConsultationMixin, VisionMixin, ConfigProductionMixin, StoryMixin,
    TranslationMixin, MemorySyncMixin, LlmShimsMixin, PromptsMixin,
):
    """Creative Director LLM Agent.

    Orchestrates story analysis, user consultation, writer collaboration,
    and runtime config generation for the narrative video engine.

    Thin facade: all methods come from the role mixins in ``agents/director/``.
    """

    def __init__(self, llm_config: dict, memory=None):

        self.llm_config = llm_config

        self.memory = memory

        # LLM transport lives in DirectorLlmClient (agents/llm_client.py).
        # Internal ``self._call_ollama*`` calls route through this object via
        # the thin delegation shims below. The 14 internal call sites and the
        # ``test_director_call_ollama_*`` tests keep working unchanged.
        self.llm = DirectorLlmClient(llm_config)

        self._last_estimated_minutes = 10
        self._last_segment_count = 0
        # P2-12: set True by callers (e.g. run_pre_production) to bypass a stale
        # cached vision doc. Declared here so it's a known instance attribute.
        self._force_refresh = False

        if not DirectorAgent._prompts:
            self._load_prompts()
