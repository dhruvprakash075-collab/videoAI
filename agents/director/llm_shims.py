"""llm_shims.py - LlmShimsMixin: thin delegation shims to DirectorLlmClient.

Extracted verbatim from ``agents/director_agent.py`` (WS-4 mixin split).
The actual implementations live in ``DirectorLlmClient`` (agents/llm_client.py),
constructed by the facade in ``self.llm``. These shims preserve the existing
``self._call_ollama(...)`` API for the internal call sites and the
``test_director_call_ollama_*`` tests.
"""


from typing import Any


class LlmShimsMixin:
    """LLM transport delegation shims (route to ``self.llm`` at runtime).

    Rightmost-ish in ``DirectorAgent``'s MRO: ``_resolve_model`` is the
    most-called helper and must not be shadowed by a role mixin.
    """

    llm: Any  # DirectorLlmClient, provided by the composing DirectorAgent

    def _resolve_model(self, model_type: str = "director") -> str:
        return self.llm._resolve_model(model_type)

    def _ollama_opts(self) -> tuple:
        return self.llm._ollama_opts()

    def _call_ollama(
        self,
        prompt: str,
        model_type: str = "director",
        format_json: bool = False,
        seed: int | None = None,
    ) -> str:
        return self.llm._call_ollama(
            prompt, model_type=model_type, format_json=format_json, seed=seed
        )

    def _call_ollama_chat(
        self,
        prompt: str,
        model_type: str = "translator",
        system_msg: str = "You are a professional translator. "
        "Translate the given text to Hindi (Devanagari script). "
        "Output only the translation.",
    ) -> str:
        return self.llm._call_ollama_chat(prompt, model_type=model_type, system_msg=system_msg)
