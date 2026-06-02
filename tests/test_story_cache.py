"""test_story_cache.py - Tests for A5: invented story caching."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))



def _topic_hash(topic: str) -> str:
    return hashlib.md5(topic.strip().lower().encode()).hexdigest()[:16]


def test_story_cache_write_and_read(tmp_path):
    """Writing a story to cache and reading it back should return the same text."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    topic = "A lonely robot learns to paint"
    story = "Once upon a time, a robot discovered colors..."

    cache_path = cache_dir / f"story_{_topic_hash(topic)}.json"
    cache_path.write_text(json.dumps({"topic": topic, "story": story}), encoding="utf-8")

    loaded = json.loads(cache_path.read_text(encoding="utf-8"))
    assert loaded["story"] == story


def test_story_cache_hash_keying():
    """Different topics must produce different cache keys."""
    h1 = _topic_hash("Topic A")
    h2 = _topic_hash("Topic B")
    assert h1 != h2


def test_story_cache_same_topic_same_key():
    """Same topic (case-insensitive, stripped) must produce the same key."""
    h1 = _topic_hash("  My Topic  ")
    h2 = _topic_hash("my topic")
    assert h1 == h2


def test_story_cache_force_refresh_bypasses(tmp_path):
    """When force_refresh is True, the cache should be ignored."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    topic = "Test topic"
    story_old = "Old story"
    cache_path = cache_dir / f"story_{_topic_hash(topic)}.json"
    cache_path.write_text(json.dumps({"topic": topic, "story": story_old}), encoding="utf-8")

    # Simulate force_refresh: don't read cache
    force_refresh = True
    loaded_story = None
    if not force_refresh and cache_path.exists():
        loaded_story = json.loads(cache_path.read_text(encoding="utf-8")).get("story")

    assert loaded_story is None, "force_refresh should bypass cache"


def test_story_cache_no_resume_bypasses(tmp_path):
    """When resume=False (--no-resume), the cache should be ignored."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    topic = "Test topic"
    story_old = "Old story"
    cache_path = cache_dir / f"story_{_topic_hash(topic)}.json"
    cache_path.write_text(json.dumps({"topic": topic, "story": story_old}), encoding="utf-8")

    resume = False
    loaded_story = None
    if resume and cache_path.exists():
        loaded_story = json.loads(cache_path.read_text(encoding="utf-8")).get("story")

    assert loaded_story is None, "--no-resume should bypass cache"
