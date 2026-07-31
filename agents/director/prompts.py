"""prompts.py - PromptsMixin: prompt template loading + JSON parsing helpers.

Extracted verbatim from ``agents/director_agent.py`` (WS-4 mixin split).
"""

import logging
from pathlib import Path
from typing import Any

from utils.utils import extract_json

log = logging.getLogger(__name__)


class PromptsMixin:
    """Prompt-template helpers shared by every Director role mixin.

    Rightmost mixin in ``DirectorAgent``'s MRO: ``_prompt``, ``_load_prompts``
    and ``_parse_json`` are the most-called helpers, so nothing may shadow them.
    """

    _prompts: dict[str, Any] = {}  # class-level cache for loaded YAML prompts

    @classmethod
    def _load_prompts(cls):
        """Load prompt templates from prompts.yaml."""

        if cls._prompts:
            return

        import yaml  # type: ignore[import-untyped]

        try:
            # prompts.yaml lives at the repo root, not in agents/
            prompts_path = Path(__file__).parent.parent.parent / "prompts.yaml"
            if not prompts_path.exists():
                # Fallback: legacy location alongside the agents/ package
                prompts_path = Path(__file__).parent.parent / "prompts.yaml"

            if prompts_path.exists():
                with open(prompts_path, encoding="utf-8") as f:
                    prompts = yaml.safe_load(f) or {}
                cls._prompts = prompts if isinstance(prompts, dict) else {}

                log.info(
                    f"[DIRECTOR] Loaded {len(cls._prompts)} prompt templates from {prompts_path}"
                )

        except Exception as e:
            log.warning(f"[DIRECTOR] Failed to load prompts: {e}")

            cls._prompts = {}

    def _prompt(self, key: str, **kwargs) -> str:
        """Get a formatted prompt template by key."""

        template = self._prompts.get(key, "")

        if not template:
            return ""

        try:
            safe_kwargs = {}

            for k, v in kwargs.items():
                if isinstance(v, str):
                    safe_kwargs[k] = v.replace("{", "{{").replace("}", "}}")
                else:
                    safe_kwargs[k] = v

            return template.format(**safe_kwargs)

        except KeyError:
            return template

    def _parse_json(self, text: str, fallback: dict | None = None) -> dict:
        """Extract JSON from LLM response. Returns fallback on failure."""

        if not text:
            return fallback or {}

        try:
            result = extract_json(text)
            if isinstance(result, dict):
                return result
        except Exception:
            log.debug("JSON parse failed, using fallback")

        return fallback or {}
