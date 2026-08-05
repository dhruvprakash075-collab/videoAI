"""test_story_cache.py - Tests for A5: invented story caching (real StoryMixin path)."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.director.story import StoryMixin


class _FakeDirector(StoryMixin):
    """Minimal StoryMixin host: real invent_story cache logic, stubbed LLM calls."""

    def __init__(self, llm_config, story_text):
        self.llm_config = llm_config
        self.calls = []
        self.story_text = story_text

    def _call_ollama(self, prompt):
        self.calls.append(prompt)
        return self.story_text

    def _prompt(self, name, **kwargs):
        return None

    def _parse_json(self, text):
        return json.loads(text)


def _cache_path(cache_dir, topic):
    topic_hash = hashlib.sha256(topic.strip().lower().encode()).hexdigest()[:12]
    return cache_dir / f"story_{topic_hash}.json"


def test_story_cache_write_and_hit(tmp_path):
    """First call writes the cache; second call hits it without calling the LLM."""
    llm_config = {"cache_dir": str(tmp_path), "cache": {"cache_invented_story": True}}
    d = _FakeDirector(llm_config, "Once upon a time, a robot discovered colors...")
    topic = "A lonely robot learns to paint"

    story1 = d.invent_story(topic, "")
    story2 = d.invent_story(topic, "")

    assert story1 == story2 == d.story_text
    assert len(d.calls) == 1, "second call must hit the cache, not the LLM"
    cached = json.loads(_cache_path(tmp_path, topic).read_text(encoding="utf-8"))
    assert cached["story"] == d.story_text
    assert cached["topic"] == topic


def test_story_cache_force_refresh_bypasses(tmp_path):
    """force_refresh=True must ignore an existing cache entry."""
    llm_config = {"cache_dir": str(tmp_path), "cache": {"cache_invented_story": True}}
    d = _FakeDirector(llm_config, "cached story")
    topic = "Cached topic"
    _cache_path(tmp_path, topic).write_text(
        json.dumps({"topic": topic, "story": "cached story"}), encoding="utf-8"
    )

    d.invent_story(topic, "", force_refresh=True)

    assert len(d.calls) == 1, "force_refresh must bypass the cache and call the LLM"


def test_story_cache_disabled_by_config(tmp_path):
    """cache_invented_story=False must disable both read and write."""
    llm_config = {"cache_dir": str(tmp_path), "cache": {"cache_invented_story": False}}
    d = _FakeDirector(llm_config, "story a")
    topic = "Uncached topic"

    d.invent_story(topic, "")
    d.invent_story(topic, "")

    assert len(d.calls) == 2, "disabled cache must call the LLM every time"
    assert not _cache_path(tmp_path, topic).exists()


def test_story_cache_key_is_case_insensitive(tmp_path):
    """Same topic (case-insensitive, stripped) must share one cache file."""
    llm_config = {"cache_dir": str(tmp_path), "cache": {"cache_invented_story": True}}
    d = _FakeDirector(llm_config, "one story")
    topic = "  My Topic  "

    d.invent_story(topic, "")
    d.invent_story("my topic", "")

    assert len(d.calls) == 1, "normalized topic must hit the same cache key"
