"""retry_manager.py - Retry + backoff wrapper for external API calls.

Two retry tiers (B13 fix):
- TRANSIENT errors (network/timeout): retry up to MAX_RETRIES (endurance mode).
- BOUNDED errors (RuntimeError, OSError): retry at most BOUNDED_RETRIES times.
  These are often deterministic failures that won't resolve with more retries.

generate_images has its own internal 3-tier OOM handling and is NOT wrapped
with the outer retry to avoid compounding retries (B14 fix).
"""

import functools
import logging
import subprocess
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

# Endurance mode: survive 10+ minute API outages for transient errors
MAX_RETRIES = 50
BASE_DELAY = 3.0
BACKOFF_FACTOR = 1.5
MAX_DELAY_S = 60.0

# Bounded retries for errors that are often deterministic
BOUNDED_RETRIES = 3

# Transient: network/timeout — worth retrying many times
TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    subprocess.TimeoutExpired,
)

# Bounded: may be deterministic — retry only a few times
BOUNDED_EXCEPTIONS: tuple[type[Exception], ...] = (
    RuntimeError,
    OSError,
)

_patch_lock = threading.Lock()


def retry_with_backoff(
    max_retries: int = MAX_RETRIES,
    base_delay: float = BASE_DELAY,
    backoff: float = BACKOFF_FACTOR,
    exceptions: tuple[type[Exception], ...] = (),
) -> Callable:
    """Decorator: retry a function with exponential backoff on failure."""
    if not exceptions:
        exceptions = TRANSIENT_EXCEPTIONS + BOUNDED_EXCEPTIONS

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    # Transient exceptions (network/timeout) get the full endurance
                    # treatment — check FIRST so that ConnectionError/TimeoutError
                    # (which are OSError subclasses and would also match
                    # BOUNDED_EXCEPTIONS) are never capped at BOUNDED_RETRIES.
                    if isinstance(e, TRANSIENT_EXCEPTIONS):
                        delay = min(base_delay * (backoff ** (attempt - 1)), MAX_DELAY_S)
                        log.warning(
                            f"{func.__name__} attempt {attempt}/{max_retries} "
                            f"transient failure: {e}. Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                        continue
                    # Bounded exceptions: give up quickly
                    if isinstance(e, BOUNDED_EXCEPTIONS) and attempt >= BOUNDED_RETRIES:
                        log.exception(
                            f"{func.__name__} deterministic failure after "
                            f"{attempt} attempts: {e}"
                        )
                        raise
                    delay = min(base_delay * (backoff ** (attempt - 1)), MAX_DELAY_S)
                    log.warning(
                        f"{func.__name__} attempt {attempt}/{max_retries} "
                        f"failed: {e}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
            log.error(f"{func.__name__} failed after {max_retries} attempts")
            raise last_exc
        return wrapper
    return decorator


def patch_retries() -> None:
    """Apply retry wrappers to external-calling functions.

    Monkey-patches functions in their original modules so pipeline_long
    gets retry+backoff automatically. Called once at pipeline start.

    NOTE: generate_images is intentionally NOT wrapped here — it has its own
    internal 3-tier OOM recovery. Wrapping it externally would compound retries
    and cause multi-minute hangs on persistent OOM (B14 fix).
    """
    with _patch_lock:
        log.info("Patching external calls with retry+backoff")

        # Patch tts_generate — transient-only (2 retries; TTS failures are often transient)
        try:
            from audio import audio_proxy
            if not hasattr(audio_proxy.tts_generate, "_is_retry_patched"):
                audio_proxy.tts_generate = retry_with_backoff(
                    max_retries=2, exceptions=TRANSIENT_EXCEPTIONS
                )(audio_proxy.tts_generate)
                audio_proxy.tts_generate._is_retry_patched = True
                log.info("Patched audio_proxy.tts_generate (transient, 2 retries)")
        except Exception as e:
            log.warning(f"Could not patch tts_generate: {e}")

        # Patch translate_hinglish — transient-only (3 retries)
        try:
            from audio import audio_proxy
            if not hasattr(audio_proxy.translate_hinglish, "_is_retry_patched"):
                audio_proxy.translate_hinglish = retry_with_backoff(
                    max_retries=3, exceptions=TRANSIENT_EXCEPTIONS
                )(audio_proxy.translate_hinglish)
                audio_proxy.translate_hinglish._is_retry_patched = True
                log.info("Patched audio_proxy.translate_hinglish (transient, 3 retries)")
        except Exception as e:
            log.warning(f"Could not patch translate_hinglish: {e}")

        # generate_images: NOT wrapped — has internal 3-tier OOM handling (B14 fix)
        log.info("generate_images: NOT wrapped (has internal OOM recovery)")

        # Sync patched references into pipeline_long namespace
        try:
            import sys
            pl = None
            for _mod_name in ("core.pipeline_long", "__main__", "pipeline_long"):
                if _mod_name in sys.modules:
                    pl = sys.modules[_mod_name]
                    break
            if pl:
                if hasattr(pl, "tts_generate") and not hasattr(pl.tts_generate, "_is_retry_patched"):
                    pl.tts_generate = audio_proxy.tts_generate
                if hasattr(pl, "translate_hinglish") and not hasattr(pl.translate_hinglish, "_is_retry_patched"):
                    pl.translate_hinglish = audio_proxy.translate_hinglish
                log.info("Synced patched references into pipeline_long namespace")
        except Exception as e:
            log.warning(f"Could not sync pipeline_long namespace: {e}")

        log.info("Retry patching complete")
