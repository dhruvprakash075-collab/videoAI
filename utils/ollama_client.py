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
from utils.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry


class _BreakerState(CircuitBreaker):
    """Subclass of CircuitBreaker for backward compatibility in legacy tests."""
    def __init__(self, fails_threshold: int, cooldown_s: float):
        super().__init__("legacy", fails_threshold, cooldown_s)

class OllamaClient:
    """Centralized Ollama HTTP client.
    Thread-safe. One instance per pipeline run is sufficient.
    """
    def __init__(self, config: dict):
        import os as _os

        from utils.url_security import build_validated_url, validate_local_service_base_url

        self._config = config
        _ollama = config.get("ollama", {})
        # Check standard environment variables first to allow external server settings
        _env_host = _os.environ.get("OLLAMA_HOST") or _os.environ.get("OLLAMA_BASE_URL")
        if _env_host:
            self._host = validate_local_service_base_url(_env_host.rstrip("/"))
        else:
            host = _ollama.get("host", "http://localhost:11434").rstrip("/")
            self._host = validate_local_service_base_url(host)
        self._timeout = int(_ollama.get("request_timeout", 240))
        self._keep_alive = _ollama.get("keep_alive", "3m")
        _fails = int(_ollama.get("breaker_fails", 3))
        _cooldown = float(_ollama.get("breaker_cooldown_s", 30))
        self._breakers: dict[str, CircuitBreaker] = {}
        self._breaker_defaults = (_fails, _cooldown)
        self._lock = threading.Lock()
        self._build_url = build_validated_url
    def _breaker(self, model: str) -> CircuitBreaker:
        fails, cooldown = self._breaker_defaults
        return CircuitBreakerRegistry.get(f"ollama:{model}", fails=fails, cooldown=cooldown)
    def _is_local_host(self, host: str) -> bool:
        """Check if the host is a local address (localhost/127.0.0.1/::1).
        Valid formats:
        - localhost
        - 127.0.0.1
        - ::1
        - http://localhost, http://localhost:11434
        - http://127.0.0.1, http://127.0.0.1:11434
        - http://[::1], http://[::1]:11434
        Rejects:
        - localhost.evil.com
        - 127.0.0.1.evil.com
        - http://attacker.com?localhost
        """
        import ipaddress
        import urllib.parse

        # Handle raw IPv6 addresses
        if host.lower() in {"::1", "[::1]", "http://[::1]", "http://[::1]:11434"}:
            return True

        # Normalize host: remove scheme and port
        parsed = urllib.parse.urlparse(host)
        netloc = parsed.netloc or host

        # Handle IPv6 brackets (e.g., [::1]:11434)
        if netloc.startswith('[') and ']' in netloc:
            ipv6_end = netloc.find(']')
            if ipv6_end != -1:
                netloc = netloc[1:ipv6_end]

        # Strip port if present
        if ':' in netloc:
            netloc = netloc.split(':')[0]

        # Define valid localhost patterns
        valid_hosts = {
            "localhost",
            "127.0.0.1",
            "::1"
        }

        # Check hostname
        if netloc in valid_hosts:
            return True

        # Check IP
        try:
            ip = ipaddress.ip_address(netloc)
            return ip.is_loopback
        except ValueError:
            return False
    def _post(self, url: str, payload: dict, timeout: int) -> dict:
        """Raw POST with retry on transient errors. Classification: local service URL (Ollama)."""
        # Validate URL before making the request to prevent SSRF
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        if not self._is_local_host(parsed.netloc):
            raise ValueError(f"Ollama requests are only allowed to local hosts, got: {parsed.netloc}")
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
            res = self._post(self._build_url(self._host, "/api/generate"), payload, self._timeout)
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
            res = self._post(self._build_url(self._host, "/api/chat"), payload, self._timeout)
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
                self._build_url(self._host, "/api/generate"),
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
        with contextlib.suppress(urllib.error.URLError, TimeoutError, OSError):
            self._post(
                self._build_url(self._host, "/api/generate"),
                {"model": model, "keep_alive": 0},
                timeout=3,
            )
        # eviction is best-effort
    def get_resident_models(self) -> list[str]:
        """Return list of currently loaded model names via /api/ps."""
        try:
            req = urllib.request.Request(self._build_url(self._host, "/api/ps"))
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
    CircuitBreakerRegistry.reset_all()
