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
* ``_call_ollama_streaming`` — token-by-token stream for live UI feedback
* ``_prewarm_ollama`` — background warm-up of director + writer models

Backward compatibility
----------------------
``DirectorAgent`` constructs a ``DirectorLlmClient`` in ``__init__`` as
``self.llm`` and keeps thin delegation shims for each public method
(``self._call_ollama(...)`` still works). All 14 internal call sites and the
``test_director_call_ollama_*`` tests continue to use the ``self._*`` form.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
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
            return client.chat(
                [{"role": "user", "content": prompt}],
                model=m,
                system_msg=system_msg,
            )
        except Exception as e:
            log.exception(f"[OLLAMA] {model_type} client.chat failed: {e}")
            return ""

    def _call_ollama_streaming(self, prompt: str, label: str = "") -> str:
        """Stream tokens for live UI feedback, accumulate full response for JSON parsing.

        Imports ``UIState`` lazily to avoid a circular import at module load
        time (ui_state.py is imported by director_agent.py, and we want to
        keep the Director's LLM client testable in isolation).
        """
        from agents.ui_state import UIState  # lazy: avoid circular import

        host = (
            self.llm_config.get("ollama", {}).get("host", "http://localhost:11434")
            if isinstance(self.llm_config, dict)
            else "http://localhost:11434"
        )
        model = self._resolve_model()

        full: list[str] = []
        tokens = 0
        for attempt in range(1, 4):
            full.clear()
            tokens = 0
            payload = json.dumps(
                {
                    "model": model,
                    "prompt": prompt,
                    "format": "json",
                    "stream": True,
                    "options": {"temperature": 0.0},
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{host.rstrip('/')}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=300) as resp:
                    UIState._uistate_log(f"[{label}] Streaming...")
                    for raw_line in resp:
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        token = chunk.get("response", "")
                        full.append(token)
                        tokens += 1
                        if tokens % 20 == 0:
                            preview = "".join(full)[-60:]
                            UIState._uistate_log(f"  ...{preview}")
                        if chunk.get("done"):
                            dur = chunk.get("total_duration", 0) / 1e9
                            UIState._uistate_log(f"[{label}] Done: {tokens} tokens in {dur:.1f}s")
                            return "".join(full).strip()
            except Exception as e:
                if attempt < 3:
                    time.sleep(2.0**attempt)
                else:
                    raise RuntimeError(f"Streaming failed after 3 attempts: {e}") from e
        return "".join(full).strip()

    def _prewarm_ollama(self) -> None:
        """Pre-warm the Director and Writer models in background (parallel threads)."""

        def _warm(model_type: str) -> None:
            try:
                self._call_ollama("Hello", model_type=model_type)
                log.info(f"[DIRECTOR] Ollama pre-warmed: {model_type}")
            except Exception:
                pass

        threading.Thread(target=_warm, args=("director",), daemon=True).start()
        threading.Thread(target=_warm, args=("writer",), daemon=True).start()
