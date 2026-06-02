"""topic_researcher.py - Brainstorm trending topics autonomously."""

import logging

from config import load_config
from utils.crewai_breaker import guarded_ollama_call

log = logging.getLogger(__name__)

def brainstorm_topic(config: dict | None = None) -> str:
    """Uses the Director LLM to brainstorm an intriguing documentary topic."""
    if config is None:
        config = load_config()

    model = config.get("models", {}).get("director", "hermes-director")

    prompt = """You are an expert YouTube strategist for a documentary channel.
Your task is to brainstorm exactly ONE highly intriguing, viral-worthy documentary topic.
The topic should be a mix of history, mystery, science, or true crime.
Return ONLY the topic title, nothing else. Do not use quotes or prefixes.
Example output: The Lost Civilization of the Amazon"""

    log.info(f"[Topic Researcher] Brainstorming topic using {model}...")
    try:
        topic = guarded_ollama_call(
            prompt, model=model, temperature=0.9, num_predict=50
        )
        if topic:
            topic = topic.strip().strip('"').strip("'")
            log.info(f"[Topic Researcher] Generated Topic: {topic}")
            return topic
    except Exception as e:
        log.warning(f"[Topic Researcher] Brainstorming failed: {e}")

    fallback = "The Mysteries of the Deep Ocean"
    log.info(f"[Topic Researcher] Using fallback topic: {fallback}")
    return fallback
