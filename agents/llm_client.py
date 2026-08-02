"""llm_client.py - Director's LLM client methods, extracted from director_agent.py.

Split out of ``director_agent.py`` (2026-06-02 refactor — God module split).
Encapsulates the raw Ollama plumbing so ``DirectorAgent`` focuses on creative
logic (vision, config, narration) and not on HTTP / retry / streaming.

What lives here
---------------
* ``_resolve_model`` — config → model name
* ``_ollama_opts``    — config → (host, timeout, keep_alive)
* ``_call_ollama``    — non-streaming /api/generate (B1 client + breaker)
* ``_call_ollama_chat`` — /api/chat for chat-template models (Sarvam etc.)

Backward compatibility
----------------------
``DirectorAgent`` constructs a ``DirectorLlmClient`` in ``__init__`` as
``self.llm`` and keeps thin delegation shims for each public method
(``self._call_ollama(...)`` still works). All 14 internal call sites and the
``test_director_call_ollama_*`` tests continue to use the ``self._*`` form.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class DirectorLlmClient:
    """All LLM transport for the Director. Owns the raw Ollama plumbing.

    Constructed once per ``DirectorAgent`` with the same ``llm_config`` dict
    the Director already received. The class is stateless beyond
    ``self.llm_config``; concurrency safety lives in the underlying
    ``utils.ollama_client.OllamaClient`` (per-model circuit breaker + retry).
    """

    def __init__(self, llm_config: dict | Any):
        self.llm_config = llm_config

    # ── config helpers ──────────────────────────────────────────────────────

    def _resolve_model(self, model_type: str = "director") -> str:
        """Resolve model name from config or defaults."""
        cfg = self.llm_config if isinstance(self.llm_config, dict) else {}
        models = cfg.get("models", cfg)
        return str(models.get(model_type, models.get("default", "llama3")))

    def _ollama_opts(self) -> tuple:
        """Return (host, request_timeout, keep_alive) from config with safe defaults.

        request_timeout caps a single Ollama request so a hung grammar-constrained
        generation aborts and retries instead of freezing the whole pipeline.
        keep_alive is forwarded so the model is evicted promptly (6GB single-model rule).
        """
        cfg = self.llm_config.get("ollama", {}) if isinstance(self.llm_config, dict) else {}
        host = cfg.get("host", "http://localhost:11434")
        timeout = int(cfg.get("request_timeout", 240))
        keep_alive = cfg.get("keep_alive", "3m")
        return host, timeout, keep_alive

    # ── transport ───────────────────────────────────────────────────────────

    def _call_ollama(
        self,
        prompt: str,
        model_type: str = "director",
        format_json: bool = False,
        seed: int | None = None,
    ) -> str:
        """Call Ollama with retry and validation.

        B1: delegates to the centralized OllamaClient (one retry policy + per-model
        circuit breaker). Returns the cleaned text, or ``""`` on failure /
        breaker-open (never ``None``).
        """
        m = self._resolve_model(model_type)
        try:
            from utils.ollama_client import get_ollama_client

            client = get_ollama_client(self.llm_config if isinstance(self.llm_config, dict) else {})
            return client.generate(prompt, model=m, format_json=format_json, seed=seed)
        except Exception as e:
            log.exception(f"[OLLAMA] {model_type} client.generate failed: {e}")
            return ""  # BUG-396 FIX: Never return None

    def _call_ollama_chat(
        self,
        prompt: str,
        model_type: str = "translator",
        system_msg: str = "You are a professional translator. "
        "Translate the given text to Hindi (Devanagari script). "
        "Output only the translation.",
    ) -> str:
        """Call Ollama using /api/chat for models that require chat templates.

        B1: delegates to the centralized OllamaClient. Returns cleaned text,
        or ``""`` on failure.
        """
        m = self._resolve_model(model_type)
        try:
            from utils.ollama_client import get_ollama_client

            client = get_ollama_client(self.llm_config if isinstance(self.llm_config, dict) else {})
            chat_kwargs = {}
            if model_type == "translator":
                cfg = self.llm_config if isinstance(self.llm_config, dict) else {}
                deva_cfg = cfg.get("tts", {}).get("devanagari", {})
                chat_kwargs = {
                    "temperature": 0.0,
                    "num_predict": int(deva_cfg.get("max_predict_tokens", 768)),
                }
            return client.chat(
                [{"role": "user", "content": prompt}],
                model=m,
                system_msg=system_msg,
                **chat_kwargs,
            )
        except Exception as e:
            log.exception(f"[OLLAMA] {model_type} client.chat failed: {e}")
            return ""
