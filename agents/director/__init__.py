"""agents.director — role mixins backing the DirectorAgent facade (WS-4 split).

Each module holds one mixin of ``DirectorAgent`` (agents/director_agent.py).
Mixins deliberately do not define ``__init__``; the facade owns construction.
``LlmShimsMixin`` and ``PromptsMixin`` sit rightmost in the MRO so the most
called helpers (``_resolve_model``, ``_prompt``, ``_parse_json``) never
shadow a role mixin's methods.

Nothing here imports ``agents.director_agent`` (circular-import free).
"""

from .config_production import ConfigProductionMixin
from .consultation import ConsultationMixin
from .llm_shims import LlmShimsMixin
from .memory_sync import MemorySyncMixin
from .prompts import PromptsMixin
from .story import StoryMixin
from .translation import TranslationMixin
from .vision import VisionMixin

__all__ = [
    "ConfigProductionMixin",
    "ConsultationMixin",
    "LlmShimsMixin",
    "MemorySyncMixin",
    "PromptsMixin",
    "StoryMixin",
    "TranslationMixin",
    "VisionMixin",
]
