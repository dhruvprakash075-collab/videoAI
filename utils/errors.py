"""errors.py - Central error taxonomy and error classification utilities."""

import logging
import urllib.error
from contextlib import contextmanager

log = logging.getLogger(__name__)


class VideoAIError(Exception):
    """Base exception for all Video.AI errors."""
    pass


class FatalError(VideoAIError):
    """Unrecoverable error that should abort the run immediately."""
    pass


class RecoverableError(VideoAIError):
    """Error that can be retried or handled via an explicit fallback path."""
    pass


class DegradedResult(VideoAIError):
    """Indicates that a component completed with degraded quality or fallback."""
    pass


class OllamaError(RecoverableError):
    """Specific error representing Ollama failures (e.g., service down)."""
    pass


class ComfyUIError(RecoverableError):
    """Specific error representing ComfyUI generation or timeout failures."""
    pass


class TTSError(RecoverableError):
    """Specific error representing TTS generation failures."""
    pass


@contextmanager
def classify_errors(stage: str):
    """Context manager to classify raw exceptions into VideoAIError categories."""
    try:
        yield
    except VideoAIError:
        # Already classified, let it propagate
        raise
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        log.warning(f"Recoverable network error in stage '{stage}': {e}")
        raise RecoverableError(f"Network error in stage '{stage}': {e}") from e
    except Exception as e:
        log.error(f"Fatal error in stage '{stage}': {e}", exc_info=True)
        raise FatalError(f"Fatal failure in stage '{stage}': {e}") from e
