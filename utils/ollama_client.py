"""ollama_client.py - Centralized Ollama HTTP client with 3-state circuit breaker.

B1: Replaces the duplicated urllib+retry loops scattered across director_agent,
audio_proxy, and pipeline_long with a single client that has:
  - One retry policy (exponential backoff, transient errors only)
  - One timeout source (ollama.request_timeout from config)
  - Per-model circuit breaker: Closed → Open → Half-Open → Closed
    * Closed:    requests pass through normally
    * Open:      fail fast for breaker_cooldown_s after breaker_fails consecutive failures
    * Half-Open: allow ONE probe after cooldown; success → Closed, failure → Open

Usage:
    from utils.ollama_client import OllamaClient
    client = OllamaClient(config)
    text = client.generate("Hello", model="hermes-director")
    text = client.chat([{"role": "user", "content": "Hi"}], model="sarvam-translate")
"""

import contextlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class _BreakerState:
    """Per-model circuit breaker state (thread-safe)."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, fails_threshold: int, cooldown_s: float):
        self._lock = threading.Lock()
        self._state = self.CLOSED
        self._fail_count = 0
        self._open_until = 0.0
        self._fails_thresh = fails_threshold
        self._cooldown_s = cooldown_s

    def allow_request(self) -> bool:
        """Return True if the request should be attempted."""
        with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if time.time() >= self._open_until:
                    self._state = self.HALF_OPEN
                    log.info("[Breaker] → Half-Open (probe allowed)")
                    return True
                return False
            # HALF_OPEN: allow exactly one probe (caller must call record_success/failure)
            return True

    def record_success(self) -> None:
        with self._lock:
            self._fail_count = 0
            if self._state != self.CLOSED:
                log.info("[Breaker] → Closed (probe succeeded)")
            self._state = self.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._fail_count += 1
            if self._state == self.HALF_OPEN:
                # Probe failed — reopen
                self._state = self.OPEN
                self._open_until = time.time() + self._cooldown_s
                log.warning(f"[Breaker] → Open (probe failed, cooldown {self._cooldown_s:.0f}s)")
            elif self._fail_count >= self._fails_thresh:
                self._state = self.OPEN
                self._open_until = time.time() + self._cooldown_s
                log.warning(
                    f"[Breaker] → Open after {self._fail_count} failures "
                    f"(cooldown {self._cooldown_s:.0f}s)"
                )

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def cooldown_remaining_s(self) -> float:
        """Return seconds until the breaker auto-transitions OPEN → HALF_OPEN.

        Returns 0.0 if the breaker is not OPEN (closed or already half-open).
        Used by callers (e.g. `utils.crewai_breaker`) to report a useful
        `BreakerOpen.cooldown_s` to the user instead of a hardcoded 0.
        """
        with self._lock:
            if self._state != self.OPEN:
                return 0.0
            return max(0.0, self._open_until - time.time())


class OllamaClient:
    """Centralized Ollama HTTP client.

    Thread-safe. One instance per pipeline run is sufficient.
    """

    def __init__(self, config: dict):
        import os as _os

        self._config = config
        _ollama = config.get("ollama", {})

        # Check standard environment variables first to allow external server settings
        _env_host = _os.environ.get("OLLAMA_HOST") or _os.environ.get("OLLAMA_BASE_URL")
        if _env_host:
            self._host = _env_host.rstrip("/")
        else:
            self._host = _ollama.get("host", "http://localhost:11434").rstrip("/")
        self._timeout = int(_ollama.get("request_timeout", 240))
        self._keep_alive = _ollama.get("keep_alive", "3m")
        _fails = int(_ollama.get("breaker_fails", 3))
        _cooldown = float(_ollama.get("breaker_cooldown_s", 30))
        self._breakers: dict[str, _BreakerState] = {}
        self._breaker_defaults = (_fails, _cooldown)
        self._lock = threading.Lock()

    def _breaker(self, model: str) -> _BreakerState:
        with self._lock:
            if model not in self._breakers:
                self._breakers[model] = _BreakerState(*self._breaker_defaults)
            return self._breakers[model]

    def _post(self, url: str, payload: dict, timeout: int) -> dict:
        """Raw POST with retry on transient errors. Raises on permanent failure."""
        data = json.dumps(payload).encode("utf-8")
        last_err = None
        for attempt in range(1, 4):
            try:
                req = urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last_err = e
                # Check for timeout specifically to prevent infinite nested retry hangs (e.g., 3 * 240s)
                is_timeout = isinstance(e, TimeoutError) or "timed out" in str(e).lower()
                if is_timeout:
                    log.warning(
                        f"[OllamaClient] Request timed out after {timeout}s. Skipping retries to prevent blocking the pipeline."
                    )
                    break

                # Transient error — retry with backoff
                if attempt < 3:
                    delay = 2.0**attempt
                    log.info(
                        f"[OllamaClient] attempt {attempt}/3 failed ({e}), retry in {delay:.0f}s"
                    )
                    time.sleep(delay)
            except Exception:
                # Non-transient (e.g. JSON decode) — don't retry
                raise
        raise RuntimeError(f"Ollama request failed after attempt {attempt}: {last_err}")

    def _clean_response(self, text: str) -> str:
        """Strip <think>…</think> reasoning blocks from the response."""
        import re

        if "<think>" in text:
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            if "<think>" in text:
                text = text[: text.index("<think>")].strip()
        return text.strip()

    def generate(
        self,
        prompt: str,
        model: str,
        format_json: bool = False,
        seed: int | None = None,
        temperature: float = 0.3,
        num_predict: int = 4096,
    ) -> str:
        """Call /api/generate. Returns the response text or "" on breaker-open."""
        breaker = self._breaker(model)
        if not breaker.allow_request():
            log.warning(f"[OllamaClient] Breaker OPEN for '{model}' — failing fast")
            return ""

        payload: dict = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        if format_json:
            payload["format"] = "json"
        if seed is not None:
            payload["options"]["seed"] = int(seed) % (2**32)
            payload["options"]["temperature"] = 0.0

        try:
            res = self._post(f"{self._host}/api/generate", payload, self._timeout)
            text = self._clean_response((res.get("response") or "").strip())
            if not text:
                raise ValueError("empty response")
            if format_json and text[0] not in "{[":
                raise ValueError(f"non-JSON response: {text[:80]}")
            breaker.record_success()
            log.debug(f"[OllamaClient] generate ok ({model}, {len(text)} chars)")
            return text
        except Exception as e:
            breaker.record_failure()
            log.exception(f"[OllamaClient] generate failed ({model}): {e}")
            return ""

    def chat(
        self,
        messages: list[dict],
        model: str,
        system_msg: str = "",
        temperature: float = 0.3,
        num_predict: int = 4096,
    ) -> str:
        """Call /api/chat. Returns the assistant message text or "" on failure."""
        breaker = self._breaker(model)
        if not breaker.allow_request():
            log.warning(f"[OllamaClient] Breaker OPEN for '{model}' — failing fast")
            return ""

        _msgs = []
        if system_msg:
            _msgs.append({"role": "system", "content": system_msg})
        _msgs.extend(messages)

        payload = {
            "model": model,
            "messages": _msgs,
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }

        try:
            res = self._post(f"{self._host}/api/chat", payload, self._timeout)
            text = self._clean_response((res.get("message", {}).get("content") or "").strip())
            breaker.record_success()
            log.debug(f"[OllamaClient] chat ok ({model}, {len(text)} chars)")
            return text
        except Exception as e:
            breaker.record_failure()
            log.exception(f"[OllamaClient] chat failed ({model}): {e}")
            return ""

    def stream(self, prompt: str, model: str) -> str:
        """Stream /api/generate tokens, accumulate and return full response."""
        breaker = self._breaker(model)
        if not breaker.allow_request():
            log.warning(f"[OllamaClient] Breaker OPEN for '{model}' — failing fast")
            return ""

        payload = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "format": "json",
                "stream": True,
                "options": {"temperature": 0.0},
            }
        ).encode("utf-8")

        full = []
        try:
            req = urllib.request.Request(
                f"{self._host}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    full.append(chunk.get("response", ""))
                    if chunk.get("done"):
                        break
            text = "".join(full).strip()
            breaker.record_success()
            return text
        except Exception as e:
            breaker.record_failure()
            log.exception(f"[OllamaClient] stream failed ({model}): {e}")
            return "".join(full).strip()

    def evict(self, model: str) -> None:
        """Send keep_alive=0 to evict a model from VRAM (best-effort, non-fatal)."""
        with contextlib.suppress(Exception):
            self._post(
                f"{self._host}/api/generate",
                {"model": model, "keep_alive": 0},
                timeout=3,
            )
        # eviction is best-effort

    def get_resident_models(self) -> list[str]:
        """Return list of currently loaded model names via /api/ps."""
        try:
            req = urllib.request.Request(f"{self._host}/api/ps")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except Exception:
            return []


# ── Module-level singleton (lazy-initialized) ─────────────────────────────
_client_instance: OllamaClient | None = None
_client_lock = threading.Lock()


def get_ollama_client(config: dict) -> OllamaClient:
    """Return the module-level OllamaClient singleton, creating it if needed."""
    global _client_instance
    with _client_lock:
        if _client_instance is None:
            _client_instance = OllamaClient(config)
            log.info("[OllamaClient] Singleton created")
        return _client_instance


def reset_ollama_client() -> None:
    """Reset the singleton (call between pipeline runs in tests)."""
    global _client_instance
    with _client_lock:
        _client_instance = None
