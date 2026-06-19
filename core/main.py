"""main.py - CrewAI agent factory: create_director(), create_writer()."""

import os
import sys
from pathlib import Path

# Add parent directory to path for compatibility module
sys.path.append(str(Path(__file__).parent.parent))

# Apply compatibility fixes (encoding, dependency checks)
try:
    from utils.compatibility import apply_all_patches

    apply_all_patches()
except ImportError:
    pass  # Compatibility module not available

# Disable all CrewAI telemetry/OpenTelemetry to prevent network timeouts/deadlocks
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
os.environ["CREWAI_TELEMETRY_OPTOUT"] = "true"
# CrewAI always initializes a SQLite task-output store, even when its response
# cache is disabled. Keep it in the workspace unless the operator overrides it.
os.environ.setdefault(
    "CREWAI_STORAGE_DIR",
    str(Path(__file__).resolve().parent.parent / "studio_cache" / "crewai"),
)

# Suppress automatic LLM retries the SAFE way. We can NOT pass num_retries=0 to
# CrewAI's LLM() — its OpenAI provider forwards that kwarg to openai's
# Completions.create(), which raises TypeError and crashes every LLM call.
# The openai SDK reads OPENAI_MAX_RETRIES from the env instead, so a hung/slow
# local generation fails fast and the caller can fall back, rather than silently
# retrying for minutes. setdefault so an operator override still wins.
os.environ.setdefault("OPENAI_MAX_RETRIES", "0")

import logging

from crewai import LLM, Agent

log = logging.getLogger(__name__)


def _create_ollama_llm(
    model_name: str,
    host: str = "http://localhost:11434",
    timeout: int = 240,
    max_tokens: int = 2048,
) -> LLM:
    """Create a CrewAI LLM instance for Ollama model.

    Args:
        model_name: Name of the Ollama model (e.g., "qwen:7b", "llama2:7b")
        host: Ollama server host URL
        timeout: Hard per-request timeout (seconds). Prevents a hung
                 grammar-constrained generation from freezing the pipeline;
                 LiteLLM raises on timeout and CrewAI surfaces the error so
                 the caller can fall back instead of blocking forever.
        max_tokens: Maximum tokens to generate. W1 fix: was hardcoded 8192 for
                    all agents, causing 4-min runaway generations that hit the
                    240s timeout and triggered silent litellm retries. Now
                    per-role: writer=1024, director=2048.

    Returns:
        Configured LLM instance for CrewAI
    """
    # NOTE: do NOT pass num_retries here — this CrewAI version's OpenAI provider
    # forwards unknown kwargs straight to openai Completions.create(), which
    # rejects 'num_retries' with a TypeError (crashes every LLM call). Retry
    # suppression is handled via OPENAI_MAX_RETRIES env in bootstrap instead.
    return LLM(
        model=model_name,
        base_url=f"{host}/v1",
        api_key="ollama",
        max_tokens=max_tokens,
        timeout=timeout,
    )


def create_director(config: dict) -> Agent:
    """Create a CrewAI Director agent using Ollama (local LLM)."""
    model_cfg = config.get("models", {})
    model_name = model_cfg.get("director", "hermes-director")
    ollama_host = config.get("ollama", {}).get("host", "http://localhost:11434")

    log.info(f"Creating Director agent with Ollama model: {model_name}")
    # W1: per-role max_tokens cap — director needs more headroom for planning JSON
    director_max_tokens = int(config.get("models", {}).get("director_max_tokens", 2048))
    llm = _create_ollama_llm(
        model_name,
        host=ollama_host,
        timeout=int(config.get("ollama", {}).get("request_timeout", 240)),
        max_tokens=director_max_tokens,
    )

    return Agent(
        role="Creative Visionary & Director",
        goal="Analyze the story, determine pacing/length, visual style, and capture complex core emotions. Pause and consult user if there is ambiguity.",
        backstory=(
            "You are the Director, the creative visionary of a Dynamic Narrative Video-Generation Engine. "
            "Before production begins, you must thoroughly read the entire story, understand characters, overarching themes, core emotions, and subtextual pacing. "
            "You accurately capture complex or negative human emotions (e.g., hatred, irritation, jealousy). "
            "You alone determine the visual style, dynamic imagery requirements, audio dynamics, pacing, and length based strictly on what is required to beautifully explain the narrative."
        ),
        allow_delegation=False,
        verbose=False,
        max_iter=5,  # Cap iterations to reduce redundant LLM calls
        llm=llm,
    )


def _ollama_model_available(model_name: str, host: str) -> bool:
    """Return True if `model_name` is pulled in Ollama (prefix match on tags)."""
    import json as _json
    import urllib.error
    import urllib.request

    from utils.errors import RecoverableError
    from utils.url_security import build_validated_url, validate_service_base_url

    try:
        tags_url = build_validated_url(validate_service_base_url(host), "/api/tags")
        with urllib.request.urlopen(tags_url, timeout=4) as r:
            tags = [t.get("name", "") for t in _json.loads(r.read()).get("models", [])]
        return any(model_name == t or t.startswith(model_name) or model_name in t for t in tags)
    except (urllib.error.URLError, OSError) as e:
        # Unreachable Ollama: raise RecoverableError loudly
        raise RecoverableError(f"Ollama server is unreachable at {host}: {e}") from e
    except Exception:
        # Reachable but other error (e.g. invalid response format), return False to trigger fallback
        return False


def create_writer(config: dict) -> Agent:
    """Create a CrewAI Writer agent using Ollama (local LLM)."""
    model_cfg = config.get("models", {})
    model_name = model_cfg.get("writer", "zephyr-writer")
    ollama_host = config.get("ollama", {}).get("host", "http://localhost:11434")

    # 6GB single-model rule: Writer and Director never co-reside, so falling back
    # to the director model is safe. If the configured Writer model isn't pulled
    # yet (e.g. still downloading), fall back so the run still works.
    if not _ollama_model_available(model_name, ollama_host):
        fallback = model_cfg.get("director", "hermes-director")
        log.warning(
            f"Writer model '{model_name}' not pulled — falling back to '{fallback}'. "
            f"Run: ollama pull {model_name}"
        )
        from agents.ui_state import UIState
        UIState.add_degradation(
            0,
            "create_writer",
            f"Writer model '{model_name}' not pulled — falling back to '{fallback}'"
        )
        model_name = fallback

    log.info(f"Creating Writer agent with Ollama model: {model_name}")
    # W1: writer gets a tight cap — 150-400 word script ≈ 600 tokens; 1024 is
    # generous headroom without letting the model ramble for 4+ minutes.
    writer_max_tokens = int(config.get("script", {}).get("writer_max_tokens", 1024))
    llm = _create_ollama_llm(
        model_name,
        host=ollama_host,
        timeout=int(config.get("ollama", {}).get("request_timeout", 240)),
        max_tokens=writer_max_tokens,
    )

    persona = config.get("narrator_persona", "")
    persona_prompt = f"Adopt the following persona/narrator voice: {persona}. " if persona else ""

    return Agent(
        role="Creative Screenwriter",
        goal="Write an engaging, highly detailed, and compelling script segment for a narrative video based on the Director's outline.",
        backstory=(
            "You are an expert, award-winning Screenwriter for a Dynamic Narrative Video Engine. "
            f"{persona_prompt}"
            "You take the Director's high-level segment outlines and weave them into immersive, captivating narration. "
            "Your words bring the characters and worlds to life, perfectly matching the required pacing and tone."
        ),
        allow_delegation=False,
        verbose=False,
        max_iter=5,  # Cap iterations to reduce redundant LLM calls
        llm=llm,
    )


def create_agents_crew(config: dict) -> tuple:
    """Create both Director and Writer agents for the CrewAI crew."""
    return create_director(config), create_writer(config)
