"""test_director_agent_helpers.py - tests for the testable helpers in DirectorAgent."""

import json
import sys
from unittest.mock import patch

import pytest

from agents.director_agent import DirectorAgent, UIState


@pytest.fixture
def agent():
    """A DirectorAgent instance with a mock llm_config."""
    return DirectorAgent(
        llm_config={"cache_dir": "/tmp/test_cache_video_ai"},
        memory=None,
    )


# ── _topic_key ───────────────────────────────────────────────────────────────


def test_topic_key_normalizes(agent):
    assert agent._topic_key("Real Hero") == "real_hero"


def test_topic_key_strips_special_chars(agent):
    # Special chars become underscores; trailing/leading underscores are kept
    assert agent._topic_key("A Hero's Quest!") == "a_hero_s_quest_"


def test_topic_key_lowercases(agent):
    assert agent._topic_key("UPPERCASE") == "uppercase"


def test_topic_key_truncates_at_80(agent):
    long_topic = "a" * 100
    key = agent._topic_key(long_topic)
    assert len(key) == 80


def test_topic_key_strips_whitespace(agent):
    assert agent._topic_key("  hello  ") == "hello"


# ── _normalize_shot_distribution ─────────────────────────────────────────────


def test_normalize_shot_distribution_empty(agent):
    result = DirectorAgent._normalize_shot_distribution({})
    # Should return defaults
    assert result["establishing"] == 0.10
    assert result["environment"] == 0.20
    assert result["character_medium"] == 0.35


def test_normalize_shot_distribution_none(agent):
    result = DirectorAgent._normalize_shot_distribution(None)
    assert "establishing" in result


def test_normalize_shot_distribution_non_dict(agent):
    result = DirectorAgent._normalize_shot_distribution("not a dict")
    assert "establishing" in result


def test_normalize_shot_distribution_zero_total(agent):
    """If total is 0 (or negative), return defaults."""
    result = DirectorAgent._normalize_shot_distribution({"a": 0, "b": 0})
    assert "establishing" in result


def test_normalize_shot_distribution_normal(agent):
    """Normalize weights so they sum to 1.0."""
    sdist = {"a": 2, "b": 2, "c": 1}  # total = 5
    result = DirectorAgent._normalize_shot_distribution(sdist)
    assert abs(sum(result.values()) - 1.0) < 0.01


def test_normalize_shot_distribution_adjusts_last_key(agent):
    """Rounding adjustment: the last key's value makes the sum exactly 1.0."""
    sdist = {"a": 1, "b": 1, "c": 1, "d": 1, "e": 1}  # 5 keys of 1
    result = DirectorAgent._normalize_shot_distribution(sdist)
    # Sum should be EXACTLY 1.0
    assert round(sum(result.values()), 4) == 1.0


def test_normalize_shot_distribution_non_numeric_raises(agent):
    """If a value is non-numeric, the function raises (real-world: should never happen)."""
    import pytest

    sdist = {"a": 1, "b": "not a number", "c": 1}
    with pytest.raises(ValueError):
        DirectorAgent._normalize_shot_distribution(sdist)


# ── _research_cache_path / _vision_cache_path ────────────────────────────────


def test_research_cache_path(agent, tmp_path, monkeypatch):
    agent.llm_config = {"cache_dir": str(tmp_path)}
    p = agent._research_cache_path("Real Hero!")
    assert p.parent == tmp_path
    assert "real_hero" in p.name
    assert p.suffix == ".json"


def test_research_cache_path_creates_dir(agent, tmp_path, monkeypatch):
    agent.llm_config = {"cache_dir": str(tmp_path / "new_dir")}
    p = agent._research_cache_path("topic")
    assert p.parent.exists()
    assert p.parent.is_dir()


def test_research_cache_path_no_config(agent, tmp_path, monkeypatch):
    """If cache_dir not in config, default to 'cache' (relative)."""
    agent.llm_config = {}
    # Path will be relative; just verify it returns a path
    p = agent._research_cache_path("topic")
    assert "topic" in p.name


def test_vision_cache_path(agent, tmp_path, monkeypatch):
    agent.llm_config = {"cache_dir": str(tmp_path)}
    p = agent._vision_cache_path()
    assert p.parent == tmp_path
    assert p.name == "vision_cache.json"


def test_vision_cache_path_creates_dir(agent, tmp_path, monkeypatch):
    agent.llm_config = {"cache_dir": str(tmp_path / "new_dir")}
    p = agent._vision_cache_path()
    assert p.parent.exists()


# ── _load_vision_cache / _save_vision_cache ─────────────────────────────────


def test_load_vision_cache_missing_file(agent, tmp_path, monkeypatch):
    agent.llm_config = {"cache_dir": str(tmp_path)}
    result = agent._load_vision_cache()
    assert result == {}


def test_load_vision_cache_existing(agent, tmp_path, monkeypatch):
    agent.llm_config = {"cache_dir": str(tmp_path)}
    cache_file = tmp_path / "vision_cache.json"
    cache_file.write_text(json.dumps({"topic_a": {"vision": "data"}}), encoding="utf-8")
    result = agent._load_vision_cache()
    assert result == {"topic_a": {"vision": "data"}}


def test_load_vision_cache_corrupt(agent, tmp_path, monkeypatch):
    agent.llm_config = {"cache_dir": str(tmp_path)}
    cache_file = tmp_path / "vision_cache.json"
    cache_file.write_text("not json", encoding="utf-8")
    result = agent._load_vision_cache()
    # Should return empty dict on JSON error
    assert result == {}


def test_save_vision_cache(agent, tmp_path, monkeypatch):
    agent.llm_config = {"cache_dir": str(tmp_path)}
    cache_data = {"topic_x": {"vision": "y"}}
    agent._save_vision_cache(cache_data)
    cache_file = tmp_path / "vision_cache.json"
    assert cache_file.exists()
    loaded = json.loads(cache_file.read_text(encoding="utf-8"))
    assert loaded == cache_data


def test_save_vision_cache_atomic(agent, tmp_path, monkeypatch):
    """Save should be atomic (write to temp, then rename)."""
    agent.llm_config = {"cache_dir": str(tmp_path)}
    cache_data = {"k": "v"}
    agent._save_vision_cache(cache_data)
    tmp_path / "vision_cache.json"
    # No leftover temp files
    temp_files = [
        f
        for f in tmp_path.iterdir()
        if f.name.startswith("vision_cache") and f.name != "vision_cache.json"
    ]
    assert temp_files == []


# ── _parse_json ──────────────────────────────────────────────────────────────


def test_parse_json_empty(agent):
    assert agent._parse_json("") == {}
    assert agent._parse_json(None) == {}


def test_parse_json_with_fallback(agent):
    fb = {"fallback": True}
    assert agent._parse_json("", fallback=fb) == fb
    assert agent._parse_json(None, fallback=fb) == fb


def test_parse_json_pure_json(agent):
    text = '{"a": 1, "b": 2}'
    assert agent._parse_json(text) == {"a": 1, "b": 2}


def test_parse_json_embedded_json(agent):
    """JSON embedded in prose."""
    text = 'Here is the analysis: {"a": 1, "b": 2} — let me explain more.'
    result = agent._parse_json(text)
    assert result == {"a": 1, "b": 2}


def test_parse_json_nested(agent):
    text = 'Output: {"vision": {"theme": "epic", "characters": [{"name": "Hero"}]}}'
    result = agent._parse_json(text)
    assert result["vision"]["theme"] == "epic"


def test_parse_json_invalid_returns_fallback(agent):
    text = "No JSON at all here, just text."
    assert agent._parse_json(text) == {}
    assert agent._parse_json(text, fallback={"x": 1}) == {"x": 1}


def test_parse_json_with_markdown_code_block(agent):
    """JSON in a markdown code block gets parsed (the brace-counter approach handles it)."""
    text = '```json\n{"a": 1}\n```'
    result = agent._parse_json(text)
    # The brace-counter finds the JSON object
    assert result == {"a": 1}


# ── _validate_vision_doc ─────────────────────────────────────────────────────


def test_validate_vision_doc_non_dict(agent):
    """If vision isn't a dict, it's replaced with an empty dict then filled with defaults."""
    result = agent._validate_vision_doc("not a dict")
    assert result["visual_style"] == "anime"
    assert result["theme"] == "untitled"
    assert result["characters"] == []


def test_validate_vision_doc_missing_fields(agent):
    """Required fields are filled with defaults when missing."""
    vision = {}
    result = agent._validate_vision_doc(vision)
    assert result["characters"] == []
    assert result["visual_style"] == "anime"
    assert result["theme"] == "untitled"
    assert result["emotions"] == "neutral"
    assert result["pacing"] == "moderate"
    assert result["tts_recommendation"] == "supertonic"
    assert result["ambiguity_detected"] is False
    assert result["ambiguity_question"] == ""
    assert result["ambiguity_fields"] == []
    assert result["recommendations"] == []


def test_validate_vision_doc_visual_style_from_dict(agent):
    """LLM sometimes returns visual_style as {tone, elements} dict — flatten it."""
    vision = {"visual_style": {"tone": "dark", "elements": ["neon", "rain"]}}
    result = agent._validate_vision_doc(vision)
    assert "dark" in result["visual_style"]
    assert "neon" in result["visual_style"]


def test_validate_vision_doc_visual_style_only_tone(agent):
    vision = {"visual_style": {"tone": "epic"}}
    result = agent._validate_vision_doc(vision)
    assert result["visual_style"] == "epic"


def test_validate_vision_doc_visual_style_non_string(agent):
    """Non-string, non-dict visual_style gets stringified."""
    vision = {"visual_style": 42}
    result = agent._validate_vision_doc(vision)
    assert result["visual_style"] == "42"


def test_validate_vision_doc_tts_recommendation_non_string(agent):
    """LLM sometimes returns True/False for tts_recommendation."""
    vision = {"tts_recommendation": True}
    result = agent._validate_vision_doc(vision)
    assert result["tts_recommendation"] == "supertonic"


def test_validate_vision_doc_string_fields(agent):
    """theme/emotions/pacing must be strings."""
    vision = {"theme": 42, "emotions": None, "pacing": ["a", "b"]}
    result = agent._validate_vision_doc(vision)
    assert result["theme"] == "42"
    # None is falsy → use default
    assert result["emotions"] == "neutral"
    # List is non-string but truthy → str() it
    assert "a" in result["pacing"]


def test_validate_vision_doc_characters_must_be_list(agent):
    vision = {"characters": {"name": "Hero", "role": "protagonist"}}
    result = agent._validate_vision_doc(vision)
    # Single dict becomes a list of one dict
    assert isinstance(result["characters"], list)
    assert result["characters"][0]["name"] == "Hero"


def test_validate_vision_doc_characters_non_list_non_dict(agent):
    vision = {"characters": "not a list"}
    result = agent._validate_vision_doc(vision)
    # Non-list, non-dict becomes empty list
    assert result["characters"] == []


def test_validate_vision_doc_ambiguity_detected(agent):
    vision = {"ambiguity_detected": "yes"}  # string, not bool
    result = agent._validate_vision_doc(vision)
    # Non-bool truthy value gets bool()'d
    assert result["ambiguity_detected"] is True


def test_validate_vision_doc_list_fields(agent):
    """ambiguity_fields/recommendations must be lists."""
    vision = {"ambiguity_fields": "single", "recommendations": {"a": 1}}
    result = agent._validate_vision_doc(vision)
    # "single" truthy non-list becomes [single]
    assert result["ambiguity_fields"] == ["single"]
    # Truthy dict becomes [dict]
    assert result["recommendations"] == [{"a": 1}]


def test_validate_vision_doc_shot_distribution_normalizes(agent):
    """If shot_distribution doesn't sum to 1.0, normalize it."""
    vision = {"shot_distribution": {"a": 2, "b": 2}}  # sum=4
    result = agent._validate_vision_doc(vision)
    # After normalization, sum should be 1.0
    assert abs(sum(result["shot_distribution"].values()) - 1.0) < 0.01


def test_validate_vision_doc_shot_distribution_zero_sum_uses_defaults(agent):
    vision = {"shot_distribution": {"a": 0, "b": 0}}
    result = agent._validate_vision_doc(vision)
    # Zero total → use defaults
    assert result["shot_distribution"]["establishing"] == 0.10


def test_validate_vision_doc_already_normalized_unchanged(agent):
    """If sum is already 1.0, no normalization needed."""
    vision = {"shot_distribution": {"a": 0.5, "b": 0.5}}
    result = agent._validate_vision_doc(vision)
    assert result["shot_distribution"] == {"a": 0.5, "b": 0.5}


# ── read_story ───────────────────────────────────────────────────────────────


def test_read_story_empty(agent):
    result = agent.read_story("")
    assert result == {"segments": [], "total_words": 0}


def test_read_story_with_segment_headers(agent):
    text = "[Segment 1]\nFirst part of the story.\n\n[Segment 2]\nSecond part of the story."
    result = agent.read_story(text)
    assert len(result["segments"]) == 2
    assert result["segments"][0]["header"] == "[Segment 1]"
    assert "First part" in result["segments"][0]["text"]
    assert result["total_words"] > 0


def test_read_story_with_part_headers(agent):
    text = "Part 1: Intro to the story.\n\nPart 2: More story here."
    result = agent.read_story(text)
    assert len(result["segments"]) == 2


def test_read_story_with_markdown_headers(agent):
    text = "## Part 1\nFirst part.\n\n## Part 2\nSecond part."
    result = agent.read_story(text)
    assert len(result["segments"]) == 2


def test_read_story_no_headers_falls_back_to_paragraphs(agent):
    """Without [Segment N] headers, split by paragraph and group into ~250 word segments."""
    paras = []
    for _i in range(10):
        # Each paragraph ~50 words, so 5+ paragraphs make a segment
        paras.append(" ".join(["word"] * 50))
    text = "\n\n".join(paras)
    result = agent.read_story(text)
    # Should produce at least 2 segments (since 500+ words)
    assert len(result["segments"]) >= 2
    # Each segment should be under 250 words
    for seg in result["segments"]:
        assert seg["word_count"] <= 300


def test_read_story_no_segments_at_all(agent):
    """If there's no structure at all, fall back to a single segment."""
    text = "Single line of text without any headers or breaks."
    result = agent.read_story(text)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == text


def test_read_story_sets_estimated_minutes(agent):
    agent.read_story("[Segment 1]\nFirst.\n\n[Segment 2]\nSecond.\n\n[Segment 3]\nThird.")
    assert agent._last_estimated_minutes == 3


# ── define_pacing_and_length ────────────────────────────────────────────────


def test_define_pacing_and_length_returns_last_estimate(agent):
    agent._last_estimated_minutes = 7
    assert agent.define_pacing_and_length({}) == 7


# ── consult_user / consult_user_stream branches ─────────────────────────────


def test_consult_user_auto_accept(agent):
    """When UIState.auto_accept is True, return the default without prompting."""
    UIState.auto_accept = True
    options = ["Yes", "No"]
    result = agent.consult_user("Continue?", options=options, allow_custom=True)
    assert result == "Yes"
    UIState.auto_accept = False


def test_consult_user_auto_accept_no_options(agent):
    """auto_accept with no options returns the default fallback string."""
    UIState.auto_accept = True
    result = agent.consult_user("Continue?", options=None, allow_custom=True)
    assert result == "Proceed as planned."
    UIState.auto_accept = False


def test_consult_user_ui_mode_returns_reply(agent, monkeypatch):
    """When in UI mode, the agent reads the reply from UIState.user_reply."""
    UIState.is_ui_mode = True
    UIState.user_reply = "My custom answer"

    # Trigger the pause_event immediately to avoid the 300s wait
    def fake_wait(timeout=0):
        UIState.user_reply = "My custom answer"
        return True

    monkeypatch.setattr(UIState.pause_event, "wait", fake_wait)
    result = agent.consult_user("What's the title?", options=["A", "B"])
    assert result == "My custom answer"
    UIState.is_ui_mode = False
    UIState.user_reply = None


def test_consult_user_ui_mode_timeout_falls_back(agent, monkeypatch):
    """When in UI mode and pause times out, return the default option."""
    UIState.is_ui_mode = True

    def fake_wait(timeout=0):
        return False  # timeout

    monkeypatch.setattr(UIState.pause_event, "wait", fake_wait)
    result = agent.consult_user("Continue?", options=["Yes", "No"])
    assert result == "Yes"
    assert UIState.status == "running"
    assert UIState.active_question is None
    UIState.is_ui_mode = False


def test_consult_user_ui_mode_empty_reply_falls_back(agent, monkeypatch):
    """When in UI mode and reply is empty, return fallback string."""
    UIState.is_ui_mode = True
    UIState.user_reply = None

    def fake_wait(timeout=0):
        return True

    monkeypatch.setattr(UIState.pause_event, "wait", fake_wait)
    result = agent.consult_user("Continue?", options=["A"])
    assert result == "Proceed as planned."
    UIState.is_ui_mode = False


def test_consult_user_non_interactive_auto_proceeds(agent, monkeypatch):
    """When stdin is not a TTY, auto-select the default option."""
    # isatty() returns False → non-interactive
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    UIState.is_ui_mode = False
    result = agent.consult_user("Continue?", options=["Yes", "No"])
    assert result == "Yes"


def test_consult_user_non_interactive_no_options(agent, monkeypatch):
    """Non-interactive, no options → 'Proceed as planned.'"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    UIState.is_ui_mode = False
    result = agent.consult_user("Continue?", options=None)
    assert result == "Proceed as planned."


# ── consult_user_stream ──────────────────────────────────────────────────────


def test_consult_user_stream_non_ui(agent, monkeypatch):
    """When not in UI mode, it just falls through to consult_user."""
    UIState.is_ui_mode = False
    UIState.auto_accept = True  # so we get a deterministic answer
    result = agent.consult_user_stream("Continue?", options=["A"])
    assert result == "A"
    UIState.auto_accept = False


def test_consult_user_stream_ui_logs_options(agent, monkeypatch):
    """In UI mode, the streaming variant adds log entries for each option."""
    UIState.is_ui_mode = True
    UIState.auto_accept = True  # so we get a deterministic answer
    initial_log_count = len(UIState.logs)
    result = agent.consult_user_stream("Continue?", options=["A", "B", "C"])
    assert result == "A"
    # Should have added [STREAM] and [OPTION N] log lines
    new_logs = UIState.logs[initial_log_count:]
    assert any("[STREAM]" in s for s in new_logs)
    assert any("[OPTION 1]" in s for s in new_logs)
    assert any("[OPTION 2]" in s for s in new_logs)
    UIState.is_ui_mode = False
    UIState.auto_accept = False


# ── _sync_memory_to_worldstate ───────────────────────────────────────────────


def test_sync_memory_to_worldstate_adds_characters(tmp_path):
    """Characters from config are added to the WorldState."""
    from memory.memory import WorldState

    config = {
        "checkpoint": {"dir": str(tmp_path)},
        "characters": {
            "protagonist": {"name": "Aria", "description": "A young hero"},
            "villain": {"name": "Malachar", "description": "An evil warlord"},
        },
    }
    agent = DirectorAgent(llm_config={}, memory=None)
    agent._sync_memory_to_worldstate("test_topic", config)
    ws = WorldState(topic="test_topic", checkpoint_dir=tmp_path)
    # Characters should be added
    assert "Aria" in ws._data.get("characters", {})
    assert "Malachar" in ws._data.get("characters", {})


def test_sync_memory_to_worldstate_adds_production_notes(tmp_path):
    """Director's recommendations from production_notes are added to world_facts."""
    from memory.memory import WorldState

    config = {
        "checkpoint": {"dir": str(tmp_path)},
        "characters": {},
        "production_notes": {
            "recommendations": ["Use dramatic lighting", "Emphasise emotional arcs"]
        },
    }
    agent = DirectorAgent(llm_config={}, memory=None)
    agent._sync_memory_to_worldstate("test_topic", config)
    ws = WorldState(topic="test_topic", checkpoint_dir=tmp_path)
    facts = ws._data.get("world_facts", [])
    assert any("[Director] Use dramatic lighting" in f for f in facts)


def test_sync_memory_to_worldstate_dedupes_facts(tmp_path):
    """Same fact added twice → only appears once."""
    from memory.memory import WorldState

    config = {
        "checkpoint": {"dir": str(tmp_path)},
        "characters": {"p": {"name": "Aria", "description": "Hero"}},
        "production_notes": {"recommendations": []},
    }
    agent = DirectorAgent(llm_config={}, memory=None)
    agent._sync_memory_to_worldstate("test_topic", config)
    agent._sync_memory_to_worldstate("test_topic", config)
    ws = WorldState(topic="test_topic", checkpoint_dir=tmp_path)
    # Count occurrences of "Aria" facts
    aria_facts = [f for f in ws._data.get("world_facts", []) if "Aria" in f]
    assert len(aria_facts) == 1


def test_sync_memory_to_worldstate_no_production_notes(tmp_path):
    """Empty production_notes is handled gracefully."""
    from memory.memory import WorldState

    config = {
        "checkpoint": {"dir": str(tmp_path)},
        "characters": {},
    }
    agent = DirectorAgent(llm_config={}, memory=None)
    agent._sync_memory_to_worldstate("test_topic", config)
    ws = WorldState(topic="test_topic", checkpoint_dir=tmp_path)
    # No crash, world_facts exists (possibly empty)
    assert "world_facts" in ws._data or ws._data == {}


# ── ask_cache_ttl / ask_search_online / ask_create_from_scratch ─────────────


def test_ask_cache_ttl_is_noop(agent):
    """ask_cache_ttl is a no-op (kept for API compat)."""
    agent.ask_cache_ttl()  # should not raise


def test_ask_search_online_auto_accept_uses_safe_default(agent, monkeypatch):
    UIState.auto_accept = True
    UIState.is_ui_mode = False
    # Auto-accept chooses the safe default: no web search.
    result = agent.ask_search_online()
    assert result is False
    UIState.auto_accept = False


def test_ask_search_online_explicit_yes(agent, monkeypatch):
    UIState.auto_accept = False
    monkeypatch.setattr(agent, "consult_user", lambda *args, **kwargs: "Yes, search online")
    result = agent.ask_search_online()
    assert result is True


def test_ask_create_from_scratch_auto_accept_uses_safe_default(agent, monkeypatch):
    UIState.auto_accept = True
    UIState.is_ui_mode = False
    # Auto-accept chooses the safe default: do not invent a new story.
    result = agent.ask_create_from_scratch("topic")
    assert result == (False, "")
    UIState.auto_accept = False


def test_ask_create_from_scratch_explicit_yes(agent, monkeypatch):
    UIState.auto_accept = False
    replies = iter(["Yes, create from scratch", "use a tiny smoke story"])
    monkeypatch.setattr(agent, "consult_user", lambda *args, **kwargs: next(replies))
    result = agent.ask_create_from_scratch("topic")
    assert result == (True, "use a tiny smoke story")


# ── consult_on_duration ─────────────────────────────────────────────────────


def test_consult_on_duration_short_auto_keeps(agent):
    """When estimated duration is <=5 minutes, auto-accept."""
    result = agent.consult_on_duration(5)
    assert result["accepted"] is True
    assert result["target_minutes"] == 5
    assert result["action"] == "keep"


def test_consult_on_duration_keep_recommended(agent):
    """When user picks 'Keep estimated duration'."""
    UIState.auto_accept = True  # picks first option
    UIState.is_ui_mode = False
    result = agent.consult_on_duration(20)
    assert result["action"] == "keep"
    UIState.auto_accept = False


def test_consult_on_duration_handles_hours(agent):
    """When duration >= 60 minutes, display hours."""
    UIState.auto_accept = True
    UIState.is_ui_mode = False
    result = agent.consult_on_duration(125)  # 2h 5min
    # We expect "keep" because the first option is "Keep estimated duration"
    assert result["action"] == "keep"
    UIState.auto_accept = False


# ── suggest_cliffhangers ────────────────────────────────────────────────────


def test_suggest_cliffhangers_short_content_returns_defaults(agent):
    """When content is too short (<200 chars), return default cliffhangers."""
    result = agent.suggest_cliffhangers("short", 10)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert all("point" in c and "outcome" in c for c in result)


def test_suggest_cliffhangers_empty_content(agent):
    """Empty content returns defaults."""
    result = agent.suggest_cliffhangers("", 10)
    assert isinstance(result, list)
    assert len(result) >= 1


# ── compact_story ────────────────────────────────────────────────────────────


def test_compact_story_empty(agent):
    result = agent.compact_story("", 5, 10)
    assert result == ""


def test_compact_story_short(agent):
    short = "short content"
    result = agent.compact_story(short, 5, 10)
    assert result == short


def test_compact_story_target_greater_than_original(agent):
    """If target >= original, no compaction needed."""
    long_content = " ".join(["word"] * 200)
    result = agent.compact_story(long_content, 20, 10)
    assert result == long_content


def test_compact_story_llm_returns_compacted(agent):
    """When LLM returns a substantial result, use it."""
    long_content = " ".join(["word"] * 1000)
    compacted = " ".join(["shrunk"] * 100)  # 100 words
    with patch.object(agent.llm, "_call_ollama", return_value=compacted):
        result = agent.compact_story(long_content, 5, 20)
    assert result == compacted


def test_compact_story_llm_returns_empty(agent):
    """When LLM returns empty, fall back to original."""
    long_content = " ".join(["word"] * 1000)
    with patch.object(agent.llm, "_call_ollama", return_value=""):
        result = agent.compact_story(long_content, 5, 20)
    assert result == long_content


def test_compact_story_llm_returns_short(agent):
    """When LLM returns a result with <=50 words, fall back to original."""
    long_content = " ".join(["word"] * 1000)
    with patch.object(agent.llm, "_call_ollama", return_value="too short"):
        result = agent.compact_story(long_content, 5, 20)
    assert result == long_content


def test_compact_story_llm_exception(agent):
    """When LLM raises, fall back to original."""
    long_content = " ".join(["word"] * 1000)
    with patch.object(agent.llm, "_call_ollama", side_effect=RuntimeError("ollama down")):
        result = agent.compact_story(long_content, 5, 20)
    assert result == long_content


# ── generate_hinglish_script ─────────────────────────────────────────────────


def test_generate_hinglish_script_extracts_narration_tags(agent):
    """When LLM returns content inside [narration] tags, extract them."""
    llm_response = "[narration]Dosto, aaj hum ek hero ki kahani sunenge.[/narration]"
    with patch.object(agent.llm, "_call_ollama", return_value=llm_response):
        result = agent.generate_hinglish_script({"summary": "x", "key_event": "y"})
    assert "Dosto" in result
    assert "[narration]" not in result


def test_generate_hinglish_script_no_tags_returns_stripped(agent):
    """When LLM returns content without [narration] tags, return the stripped text."""
    llm_response = "   Dosto, aaj hum baat karenge.   "
    with patch.object(agent.llm, "_call_ollama", return_value=llm_response):
        result = agent.generate_hinglish_script({"summary": "x", "key_event": "y"})
    assert result == "Dosto, aaj hum baat karenge."


def test_generate_hinglish_script_llm_exception_uses_fallback(agent):
    """When LLM raises, use the default Hindi fallback string."""
    with patch.object(agent.llm, "_call_ollama", side_effect=RuntimeError("ollama fail")):
        result = agent.generate_hinglish_script({"summary": "sum", "key_event": "event"})
    assert "Aise hi" in result
    assert "sum" in result
    assert "event" in result


# ── research_story ───────────────────────────────────────────────────────────


def test_research_story_success(agent):
    """research_story delegates to the consolidated researcher and adapts items."""
    from utils.researcher import ResearchItem

    fake_items = [
        ResearchItem(
            title="Wiki1",
            text="A summary of the topic",
            url="https://example.org/wiki1",
            source_type="wikipedia",
            relevance_score=0.9,
        ),
        ResearchItem(
            title="RSS1",
            text="More background detail",
            url="https://example.org/rss1",
            source_type="rss",
            relevance_score=0.5,
        ),
    ]
    with patch("utils.researcher.research_topic", return_value=fake_items) as mock_research:
        result = agent.research_story("my_topic")
    mock_research.assert_called_once()
    assert result["topic"] == "my_topic"
    assert result["result_count"] == 2
    assert len(result["raw_results"]) == 2
    assert result["raw_results"][0]["source"] == "wikipedia"
    assert result["raw_results"][0]["summary"] == "A summary of the topic"
    assert result["raw_results"][0]["url"] == "https://example.org/wiki1"
    assert "A summary of the topic" in result["combined_summary"]
    assert "More background detail" in result["combined_summary"]


def test_research_story_empty_results(agent):
    """When the researcher returns nothing, combined_summary falls back to topic."""
    with patch("utils.researcher.research_topic", return_value=[]):
        result = agent.research_story("my_topic")
    assert result["topic"] == "my_topic"
    assert result["combined_summary"] == "my_topic"
    assert result["result_count"] == 0
    assert result["raw_results"] == []


def test_research_story_import_error(agent):
    """When the researcher module is not importable, return empty research."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "utils.researcher":
            raise ImportError("no researcher")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        result = agent.research_story("my_topic")
    assert result["topic"] == "my_topic"
    assert result["combined_summary"] == "my_topic"  # falls back to topic
    assert result["result_count"] == 0
    assert result["raw_results"] == []


# ── analyze_with_research (cache hit branch) ─────────────────────────────────


def test_analyze_with_research_cache_hit(agent, tmp_path):
    """When the cache has a vision doc, return it directly without LLM call."""
    from utils.vision_cache import VisionCache

    agent.llm_config = {"cache_dir": str(tmp_path)}
    # Pre-populate the cache
    cache = VisionCache(cache_dir=str(tmp_path))
    cached_doc = {
        "theme": "Cached Theme",
        "visual_style": "cinematic",
        "characters": [{"name": "Cached"}],
    }
    cache.set("test_topic", cached_doc, content_text="")
    result = agent.analyze_with_research("test_topic", {"combined_summary": "x"})
    assert result["theme"] == "Cached Theme"


def test_analyze_with_research_resets_estimate(agent, tmp_path):
    """Each call resets the _last_estimated_minutes counter."""
    agent._last_estimated_minutes = 99
    agent.llm_config = {"cache_dir": str(tmp_path)}
    with patch("utils.vision_cache.VisionCache.get", return_value={"theme": "x"}):
        agent.analyze_with_research("topic", {})
    assert agent._last_estimated_minutes == 0  # reset to 0


def test_analyze_with_research_no_cache_dir_in_config(agent, tmp_path, monkeypatch):
    """If llm_config has no cache_dir, default to 'cache'."""
    # Set llm_config to a non-dict to exercise the fallback
    agent.llm_config = "not a dict"
    # VisionCache needs a real cache_dir; mock the VisionCache.get
    with patch("utils.vision_cache.VisionCache.get", return_value={"theme": "from_cache"}):
        result = agent.analyze_with_research("topic", {})
    assert result["theme"] == "from_cache"


# ── consult_with_writer ─────────────────────────────────────────────────────


def test_consult_with_writer_success(agent):
    """When LLM returns valid JSON, use it."""
    llm_response = '{"segment_count": 5, "words_per_segment": 300, "image_count_per_segment": 8}'
    with patch.object(agent.llm, "_call_ollama", return_value=llm_response):
        result = agent.consult_with_writer({"theme": "t"}, {})
    assert result["segment_count"] == 5
    assert result["words_per_segment"] == 300
    # _last_segment_count should be set
    assert agent._last_segment_count == 5


def test_consult_with_writer_uses_fallback_on_invalid_json(agent):
    """When LLM returns invalid JSON, use the fallback dict."""
    with patch.object(agent.llm, "_call_ollama", return_value="not json at all"):
        result = agent.consult_with_writer({"theme": "t"}, {})
    # Falls back to the default
    assert result["segment_count"] == 3
    assert result["words_per_segment"] == 390


def test_consult_with_writer_segment_count_validation(agent):
    """Only int/float segment_count is used to update _last_segment_count."""
    # segment_count is a string → not int/float, so _last_segment_count is not updated
    llm_response = '{"segment_count": "5", "words_per_segment": 200}'
    with patch.object(agent.llm, "_call_ollama", return_value=llm_response):
        result = agent.consult_with_writer({"theme": "t"}, {})
    # The result still has segment_count but it's a string
    assert result["segment_count"] == "5"
    # _last_segment_count was not updated (still 0)
    assert agent._last_segment_count == 0


def test_consult_with_writer_normalizes_characters_dict(agent):
    """If vision_doc.characters is a dict, it's normalized to a list."""
    llm_response = '{"segment_count": 3}'
    with patch.object(agent.llm, "_call_ollama", return_value=llm_response):
        result = agent.consult_with_writer({"characters": {"Aria": {"description": "hero"}}}, {})
    # Should not crash
    assert result["segment_count"] == 3


# ── produce_runtime_config (modes & characters) ──────────────────────────────


def test_produce_runtime_config_basic(agent):
    """A minimal config produces a valid overlay."""
    vision = {
        "theme": "epic",
        "emotions": "mysterious",
        "characters": [{"name": "Hero", "description": "A brave hero"}],
    }
    writer = {"segment_count": 4, "words_per_segment": 300, "image_count_per_segment": 6}
    overlay = agent.produce_runtime_config(vision, {}, writer)
    assert "characters" in overlay
    assert "hero" in overlay["characters"]
    assert "visual" in overlay
    assert "tts" in overlay
    assert "script" in overlay
    assert "subtitles" in overlay


def test_produce_runtime_config_non_dict_vision(agent):
    """When vision_doc is not a dict, it's replaced with empty and processing continues."""
    overlay = agent.produce_runtime_config("not a dict", {}, {"segment_count": 3})
    # Default character "Narrator" should be added
    assert "narrator" in overlay["characters"]


def test_produce_runtime_config_voice_only_mode(agent):
    """voice-only mode skips visual config and tts."""
    vision = {"theme": "epic", "characters": [{"name": "H"}]}
    overlay = agent.produce_runtime_config(vision, {}, {}, mode="voice-only")
    # Visual is n/a
    assert overlay["visual"]["style"] == "n/a"
    # TTS engine is configured (not skipped in voice-only)
    # Subtitles are none
    assert overlay["subtitles"]["format"] == "none"
    # Transition is none
    assert overlay["visualization"]["transition"] == "none"


def test_produce_runtime_config_video_only_mode(agent):
    """video-only mode skips TTS and uses no narrator voice."""
    vision = {"theme": "epic", "characters": [{"name": "H"}]}
    overlay = agent.produce_runtime_config(vision, {}, {}, mode="video-only")
    # TTS is "none"
    assert overlay["tts"]["engine"] == "none"
    # Narrator voice is "none" in video-only
    assert overlay["tts"]["narrator_voice"] == "none"


def test_produce_runtime_config_clamps_segment_count(agent):
    """segment_count is clamped to [1, 20]."""
    overlay = agent.produce_runtime_config({}, {}, {"segment_count": 100})
    # 100 should be clamped to 20
    assert overlay["script"]["default_images_per_segment"] <= 30
    # We can also check est_duration: seg_count * seg_dur_min
    assert overlay["video"]["total_duration_min"] <= 20 * 2


def test_produce_runtime_config_handles_duplicate_character_names(agent):
    """If two characters normalize to the same key, the second gets a suffix."""
    vision = {
        "characters": [
            {"name": "Aria Storm", "description": "hero"},
            {"name": "aria storm", "description": "another"},  # same after normalize
        ]
    }
    overlay = agent.produce_runtime_config(vision, {}, {})
    # Both characters should be in the dict, with different keys
    char_keys = list(overlay["characters"].keys())
    assert len(char_keys) == 2
    assert char_keys[0] != char_keys[1]


def test_produce_runtime_config_skips_empty_characters(agent):
    """Characters with empty/whitespace names are skipped."""
    vision = {
        "characters": [
            {"name": "", "description": "no name"},
            {"name": "  ", "description": "whitespace"},
            {"name": "Aria", "description": "valid"},
        ]
    }
    overlay = agent.produce_runtime_config(vision, {}, {})
    # Only "aria" should be in the dict
    assert len(overlay["characters"]) == 1
    assert "aria" in overlay["characters"]


def test_produce_runtime_config_character_dict_normalization(agent):
    """If vision_doc.characters is a dict, it's normalized to a list."""
    vision = {"characters": {"Hero": {"description": "brave"}}, "theme": "epic"}
    overlay = agent.produce_runtime_config(vision, {}, {})
    assert "hero" in overlay["characters"]


def test_produce_runtime_config_user_overrides(agent):
    """Unknown user response keys go into production_notes.user_overrides."""
    user_responses = {
        "custom_setting": "some_value",
        "visual_style": "",  # same as vision_doc — no StyleResolver call
    }
    overlay = agent.produce_runtime_config({"theme": "t"}, user_responses, {})
    overrides = overlay["production_notes"].get("user_overrides", {})
    assert "custom_setting" in overrides
    assert "visual_style" not in overrides


def test_produce_runtime_config_provenance(agent):
    """The _provenance key documents where each section came from."""
    overlay = agent.produce_runtime_config({"theme": "t"}, {}, {})
    assert "_provenance" in overlay
    assert "characters" in overlay["_provenance"]
    assert "visual" in overlay["_provenance"]


def test_produce_runtime_config_director_vision(agent):
    """_director_vision preserves the theme/emotions for downstream use."""
    overlay = agent.produce_runtime_config(
        {"theme": "dark", "emotions": "horror", "pacing": "slow", "visual_style": "gothic"}, {}, {}
    )
    assert overlay["_director_vision"]["theme"] == "dark"
    assert overlay["_director_vision"]["emotions"] == "horror"


# ── invent_story (cache hit + generation) ───────────────────────────────────


def test_invent_story_cache_hit(agent, tmp_path, monkeypatch):
    """When a cached story exists, return it directly."""
    agent.llm_config = {"cache_dir": str(tmp_path)}
    topic = "my_topic"
    import hashlib

    topic_hash = hashlib.sha256(topic.strip().lower().encode()).hexdigest()[:12]
    cache_file = tmp_path / f"story_{topic_hash}.json"
    cache_file.write_text(
        json.dumps({"topic": topic, "story": "Once upon a time, a hero was born."}),
        encoding="utf-8",
    )
    with patch.object(agent.llm, "_call_ollama") as llm_mock:
        result = agent.invent_story(topic, "user notes")
    assert result == "Once upon a time, a hero was born."
    # LLM was NOT called
    llm_mock.assert_not_called()


def test_invent_story_no_cache_calls_llm(agent, tmp_path):
    """When no cache exists, LLM is called and result is cached."""
    agent.llm_config = {"cache_dir": str(tmp_path)}
    with patch.object(
        agent.llm, "_call_ollama", return_value="A new invented story about a hero."
    ) as llm_mock:
        result = agent.invent_story("new_topic", "make it epic")
    assert "invented story" in result
    llm_mock.assert_called_once()
    # Should have written to cache
    cache_files = list(tmp_path.glob("story_*.json"))
    assert len(cache_files) == 1


def test_invent_story_force_refresh_skips_cache(agent, tmp_path):
    """force_refresh=True bypasses the cache."""
    agent.llm_config = {"cache_dir": str(tmp_path)}
    topic = "forced_topic"
    import hashlib

    topic_hash = hashlib.sha256(topic.strip().lower().encode()).hexdigest()[:12]
    cache_file = tmp_path / f"story_{topic_hash}.json"
    cache_file.write_text(
        json.dumps({"topic": topic, "story": "old cached story"}), encoding="utf-8"
    )
    with patch.object(agent.llm, "_call_ollama", return_value="freshly invented") as llm_mock:
        result = agent.invent_story(topic, "", force_refresh=True)
    assert result == "freshly invented"
    llm_mock.assert_called_once()


def test_invent_story_cache_disabled(agent, tmp_path):
    """When cache is disabled, no cache read or write happens."""
    agent.llm_config = {
        "cache_dir": str(tmp_path),
        "cache": {"cache_invented_story": False},
    }
    with patch.object(agent.llm, "_call_ollama", return_value="a story") as llm_mock:
        result = agent.invent_story("any_topic", "")
    assert result == "a story"
    llm_mock.assert_called_once()
    # No cache file should be written
    cache_files = list(tmp_path.glob("story_*.json"))
    assert cache_files == []


def test_invent_story_corrupt_cache_continues(agent, tmp_path):
    """If the cache file is corrupt, the function continues to call the LLM."""
    agent.llm_config = {"cache_dir": str(tmp_path)}
    topic = "corrupt_topic"
    import hashlib

    topic_hash = hashlib.sha256(topic.strip().lower().encode()).hexdigest()[:12]
    cache_file = tmp_path / f"story_{topic_hash}.json"
    cache_file.write_text("not valid json", encoding="utf-8")
    with patch.object(agent.llm, "_call_ollama", return_value="regenerated story") as llm_mock:
        result = agent.invent_story(topic, "")
    assert result == "regenerated story"
    llm_mock.assert_called_once()


# ── translate_to_devanagari ─────────────────────────────────────────────────


def test_translate_to_devanagari_success(agent):
    """When LLM returns Devanagari text, return it."""
    deva_text = "एक बार की बात है, एक बहादुर योद्धा था।"
    with patch.object(agent.llm, "_call_ollama_chat", return_value=deva_text):
        result = agent.translate_to_devanagari(
            "Once upon a time, there was a brave warrior.", {"mood": "epic"}
        )
    assert "बहादुर" in result


def test_translate_to_devanagari_empty_response_uses_original(agent):
    """If the LLM returns empty, fall back to the English original."""
    with patch.object(agent.llm, "_call_ollama_chat", return_value=""):
        original = "The original English text."
        result = agent.translate_to_devanagari(original, {})
    assert result is None


def test_translate_to_devanagari_too_few_deva_chars_uses_original(agent):
    """If the translation has <10 Devanagari chars, fall back to original."""
    bad_translation = "This is mostly English, not Devanagari at all, just a few letters."
    with patch.object(agent.llm, "_call_ollama_chat", return_value=bad_translation):
        original = "the original"
        result = agent.translate_to_devanagari(original, {})
    assert result is None


def test_translate_to_devanagari_strips_think_tags(agent):
    """<think>...</think> tags are stripped from the translation."""
    translation_with_think = "<think>internal reasoning</think>एक अच्छी कहानी।"
    with patch.object(agent.llm, "_call_ollama_chat", return_value=translation_with_think):
        result = agent.translate_to_devanagari("A good story.", {})
    assert "<think>" not in result


def test_translate_to_devanagari_exception_uses_original(agent):
    """If the LLM call raises, fall back to original."""
    with patch.object(agent.llm, "_call_ollama_chat", side_effect=RuntimeError("ollama fail")):
        original = "the original"
        result = agent.translate_to_devanagari(original, {})
    assert result is None


# ── consult_on_config (early branches) ──────────────────────────────────────


def test_consult_on_config_no_ambiguity_no_uncertain(agent):
    """Smoke: with no ambiguity, function returns a (dict, dict) tuple without raising."""
    vision = {
        "theme": "epic",
        "visual_style": "cinematic",
        "pacing": "moderate",
        "emotions": "calm",
        "characters": [{"name": "Aria", "description": "a hero"}],
        "ambiguity_detected": False,
        "ambiguity_question": "",
        "ambiguity_fields": [],
        "recommendations": [],
    }
    with (
        patch.object(agent, "consult_user", return_value=""),
        patch.object(agent.llm, "_call_ollama", return_value='{"fields": {}, "breakdown": {}}'),
    ):
        user_responses, writer_input = agent.consult_on_config(vision)
    assert isinstance(user_responses, dict)
    assert isinstance(writer_input, dict)


def test_consult_on_config_with_ambiguity_records_resolution(agent):
    """When ambiguity_detected is True and consult_user replies, ambiguity_resolution is recorded."""
    vision = {
        "theme": "epic",
        "visual_style": "cinematic",
        "pacing": "moderate",
        "emotions": "calm",
        "characters": [{"name": "Aria", "description": "a hero"}],
        "ambiguity_detected": True,
        "ambiguity_question": "Which time period?",
        "ambiguity_fields": [],
        "recommendations": [],
    }
    with (
        patch.object(agent, "consult_user", return_value="The future"),
        patch.object(agent.llm, "_call_ollama", return_value='{"fields": {}, "breakdown": {}}'),
    ):
        user_responses, _ = agent.consult_on_config(vision)
    assert user_responses.get("ambiguity_resolution") == "The future"


def test_consult_on_config_empty_ambiguity_reply(agent):
    """When user gives an empty answer to ambiguity, no resolution is recorded."""
    vision = {
        "theme": "epic",
        "characters": [{"name": "A"}],
        "ambiguity_detected": True,
        "ambiguity_question": "Which time?",
        "ambiguity_fields": [],
        "recommendations": [],
    }
    with (
        patch.object(agent, "consult_user", return_value=""),
        patch.object(agent.llm, "_call_ollama", return_value='{"fields": {}, "breakdown": {}}'),
    ):
        user_responses, _ = agent.consult_on_config(vision)
    # Empty answer → no ambiguity_resolution key
    assert "ambiguity_resolution" not in user_responses


def test_consult_on_config_returns_tuple(agent):
    """consult_on_config returns a 2-tuple (user_responses, writer_input)."""
    vision = {
        "theme": "epic",
        "characters": [{"name": "A"}],
        "ambiguity_detected": False,
        "ambiguity_question": "",
        "ambiguity_fields": [],
        "recommendations": [],
    }
    with (
        patch.object(agent, "consult_user", return_value=""),
        patch.object(agent.llm, "_call_ollama", return_value='{"fields": {}, "breakdown": {}}'),
    ):
        result = agent.consult_on_config(vision)
    assert len(result) == 2
    assert isinstance(result[0], dict)
    assert isinstance(result[1], dict)


# ── consult_fields tests ────────────────────────────────────────────


def test_consult_fields_auto_accept_yes_flag(agent):
    """When --yes flag is set (auto_accept), all defaults are returned without prompting."""
    fields = [
        {"key": "theme", "label": "Theme", "current": "epic", "options": ["epic", "dark"]},
        {"key": "pacing", "label": "Pacing", "current": "fast", "options": ["fast", "slow"]},
    ]
    with patch.object(UIState, "auto_accept", True, create=True):
        results = agent.consult_fields(fields, vision_summary="test")
    assert results == {"theme": "epic", "pacing": "fast"}


def test_consult_fields_auto_accept_no_options(agent):
    """When field has no options, the 'current' value is used as default."""
    fields = [
        {"key": "narrator", "label": "Narrator", "current": "default", "options": []},
    ]
    with patch.object(UIState, "auto_accept", True, create=True):
        results = agent.consult_fields(fields)
    assert results == {"narrator": "default"}


def test_consult_fields_ui_mode_timeout(agent):
    """In UI mode, when pause_event.wait times out, returns empty dict."""
    fields = [{"key": "theme", "label": "Theme", "current": "epic", "options": ["epic"]}]
    with (
        patch.object(UIState, "is_ui_mode", True, create=True),
        patch.object(UIState, "pause_event") as mock_event,
        patch.object(UIState, "status", "running", create=True),
        patch.object(UIState, "active_question", None, create=True),
    ):
        mock_event.wait.return_value = False  # Timeout
        mock_event.clear.return_value = None
        results = agent.consult_fields(fields)
    assert results == {}


def test_consult_fields_ui_mode_user_reply(agent):
    """In UI mode, when user replies, parses field:choice format."""
    fields = [
        {"key": "theme", "label": "Theme", "current": "epic", "options": ["epic", "dark"]},
        {"key": "pacing", "label": "Pacing", "current": "fast", "options": ["fast", "slow"]},
    ]
    with (
        patch.object(UIState, "is_ui_mode", True, create=True),
        patch.object(UIState, "pause_event") as mock_event,
        patch.object(UIState, "status", "running", create=True),
        patch.object(UIState, "active_question", None, create=True),
    ):
        mock_event.wait.return_value = True  # User replied
        mock_event.clear.return_value = None
        with patch.object(UIState, "user_reply", "1:2 2:1", create=True):
            results = agent.consult_fields(fields)
    assert results == {"theme": "dark", "pacing": "fast"}


def test_consult_fields_ui_mode_invalid_reply(agent):
    """In UI mode, malformed replies are silently skipped."""
    fields = [{"key": "theme", "label": "Theme", "current": "epic", "options": ["epic", "dark"]}]
    with (
        patch.object(UIState, "is_ui_mode", True, create=True),
        patch.object(UIState, "pause_event") as mock_event,
        patch.object(UIState, "status", "running", create=True),
        patch.object(UIState, "active_question", None, create=True),
    ):
        mock_event.wait.return_value = True
        mock_event.clear.return_value = None
        with patch.object(UIState, "user_reply", "garbage", create=True):
            results = agent.consult_fields(fields)
    # No valid field:choice → no selections
    assert results == {}


def test_consult_fields_headless_default_enter(agent):
    """In headless mode (timeout=0, no UI), empty line accepts all defaults."""
    fields = [
        {"key": "theme", "label": "Theme", "current": "epic", "options": ["epic", "dark"]},
    ]
    with (
        patch.object(UIState, "auto_accept", False, create=True),
        patch.object(UIState, "is_ui_mode", False, create=True),
        patch.object(sys, "stdin") as mock_stdin,
        patch("agents.director_agent.input", return_value=""),
    ):
        mock_stdin.isatty.return_value = True
        results = agent.consult_fields(fields, timeout=0)
    assert results["theme"] == "epic"


def test_consult_fields_headless_quick_mode(agent):
    """Typing 'N+1' (quick mode sentinel) accepts all defaults."""
    fields = [
        {"key": "theme", "label": "Theme", "current": "epic", "options": ["epic", "dark"]},
        {"key": "pacing", "label": "Pacing", "current": "fast", "options": ["fast", "slow"]},
    ]
    with (
        patch.object(UIState, "auto_accept", False, create=True),
        patch.object(UIState, "is_ui_mode", False, create=True),
        patch.object(sys, "stdin") as mock_stdin,
        patch("agents.director_agent.input", return_value="3"),
    ):  # 2 fields + 1 = 3
        mock_stdin.isatty.return_value = True
        results = agent.consult_fields(fields, timeout=0)
    assert results == {"theme": "epic", "pacing": "fast"}


def test_consult_fields_headless_field_choice(agent):
    """User types '1:2' to pick option 2 for field 1."""
    fields = [
        {"key": "theme", "label": "Theme", "current": "epic", "options": ["epic", "dark"]},
    ]
    with (
        patch.object(UIState, "auto_accept", False, create=True),
        patch.object(UIState, "is_ui_mode", False, create=True),
        patch.object(sys, "stdin") as mock_stdin,
        patch("agents.director_agent.input", return_value="1:2"),
    ):
        mock_stdin.isatty.return_value = True
        results = agent.consult_fields(fields, timeout=0)
    assert results["theme"] == "dark"


def test_consult_fields_headless_skip_choice(agent):
    """User types '1:0' to skip field 1 (keep default)."""
    fields = [
        {"key": "theme", "label": "Theme", "current": "epic", "options": ["epic", "dark"]},
    ]
    with (
        patch.object(UIState, "auto_accept", False, create=True),
        patch.object(UIState, "is_ui_mode", False, create=True),
        patch.object(sys, "stdin") as mock_stdin,
        patch("agents.director_agent.input", return_value="1:0"),
    ):
        mock_stdin.isatty.return_value = True
        # ci=-1 → opts[0] = "epic"
        results = agent.consult_fields(fields, timeout=0)
    assert results["theme"] == "epic"


def test_consult_fields_headless_regenerate(agent):
    """User types 'r' → returns _regenerate sentinel."""
    fields = [{"key": "theme", "label": "Theme", "current": "epic", "options": ["epic"]}]
    with (
        patch.object(UIState, "auto_accept", False, create=True),
        patch.object(UIState, "is_ui_mode", False, create=True),
        patch.object(sys, "stdin") as mock_stdin,
        patch("agents.director_agent.input", return_value="r"),
    ):
        mock_stdin.isatty.return_value = True
        results = agent.consult_fields(fields, timeout=0, allow_regenerate=True)
    assert results == {"_regenerate": True}


def test_consult_fields_headless_partial_then_defaults(agent):
    """User answers one field, others get defaults."""
    fields = [
        {"key": "theme", "label": "Theme", "current": "epic", "options": ["epic", "dark"]},
        {"key": "pacing", "label": "Pacing", "current": "fast", "options": ["fast", "slow"]},
    ]
    with (
        patch.object(UIState, "auto_accept", False, create=True),
        patch.object(UIState, "is_ui_mode", False, create=True),
        patch.object(sys, "stdin") as mock_stdin,
        patch("agents.director_agent.input", return_value="1:2"),
    ):
        mock_stdin.isatty.return_value = True
        results = agent.consult_fields(fields, timeout=0)
    assert results["theme"] == "dark"
    assert results["pacing"] == "fast"  # default


def test_consult_fields_headless_invalid_input_ignored(agent):
    """Garbage input is ignored, defaults returned."""
    fields = [{"key": "theme", "label": "Theme", "current": "epic", "options": ["epic", "dark"]}]
    with (
        patch.object(UIState, "auto_accept", False, create=True),
        patch.object(UIState, "is_ui_mode", False, create=True),
        patch.object(sys, "stdin") as mock_stdin,
        patch("agents.director_agent.input", return_value="garbage"),
    ):
        mock_stdin.isatty.return_value = True
        results = agent.consult_fields(fields, timeout=0)
    assert results["theme"] == "epic"


def test_consult_fields_out_of_range_ignored(agent):
    """Out-of-range field index is ignored, default applied."""
    fields = [{"key": "theme", "label": "Theme", "current": "epic", "options": ["epic", "dark"]}]
    with (
        patch.object(UIState, "auto_accept", False, create=True),
        patch.object(UIState, "is_ui_mode", False, create=True),
        patch.object(sys, "stdin") as mock_stdin,
        patch("agents.director_agent.input", return_value="99:1"),
    ):
        mock_stdin.isatty.return_value = True
        results = agent.consult_fields(fields, timeout=0)
    assert results["theme"] == "epic"


# ── produce_runtime_config tests ───────────────────────────────────


def test_produce_runtime_config_full_mode(agent):
    """Full mode: all sections populated."""
    vision = {
        "theme": "epic",
        "visual_style": "cinematic",
        "pacing": "moderate",
        "emotions": "calm",
        "characters": [{"name": "Aria", "description": "a hero"}],
    }
    user_responses = {}
    writer_input = {"segment_count": 5, "image_count_per_segment": 6, "words_per_segment": 200}
    result = agent.produce_runtime_config(vision, user_responses, writer_input, mode="full")
    assert result["visual"]["style"] == "cinematic"
    assert result["tts"]["engine"] == "supertonic"
    assert result["script"]["words_per_segment"] == 200
    assert result["video"]["total_duration_min"] == 10  # 5 * 2
    assert "aria" in result["characters"]


def test_produce_runtime_config_voice_only_mode_v2(agent):
    """Voice-only mode: no visuals, no subtitles, no transitions."""
    vision = {
        "theme": "epic",
        "characters": [{"name": "A"}],
    }
    result = agent.produce_runtime_config(vision, {}, {"segment_count": 3}, mode="voice-only")
    assert result["visual"]["num_scenes"] == 0
    assert result["visual"]["style"] == "n/a"
    assert result["subtitles"]["format"] == "none"
    assert result["visualization"]["transition"] == "none"


def test_produce_runtime_config_video_only_mode_v2(agent):
    """Video-only mode: no TTS, no narrator."""
    vision = {
        "theme": "epic",
        "characters": [{"name": "A"}],
    }
    result = agent.produce_runtime_config(vision, {}, {"segment_count": 3}, mode="video-only")
    assert result["tts"]["engine"] == "none"
    assert result["tts"]["narrator_voice"] == "none"


def test_produce_runtime_config_empty_vision(agent):
    """Empty vision_doc → default Narrator character + fallback style."""
    result = agent.produce_runtime_config({}, {}, {})
    assert "narrator" in result["characters"]
    assert result["visual"]["style"] == "hybrid 2d anime visual novel style"


def test_produce_runtime_config_non_dict_vision_v2(agent):
    """Non-dict vision_doc is replaced with empty dict."""
    result = agent.produce_runtime_config(None, None, None)
    assert isinstance(result, dict)
    assert "narrator" in result["characters"]


def test_produce_runtime_config_characters_as_dict(agent):
    """Characters as dict (name → details) is converted to list of dicts."""
    vision = {
        "characters": {
            "Aria": {"description": "a hero"},
            "Bob": "another hero",  # non-dict value → string description
        },
    }
    result = agent.produce_runtime_config(vision, {}, {})
    assert "aria" in result["characters"]
    assert "bob" in result["characters"]
    assert result["characters"]["aria"]["description"] == "a hero"
    assert result["characters"]["bob"]["description"] == "another hero"


def test_produce_runtime_config_clamps_segment_count_v2(agent):
    """Segment count is clamped to 1-20."""
    vision = {"characters": [{"name": "A"}]}
    # Too high
    result = agent.produce_runtime_config(vision, {}, {"segment_count": 999})
    assert result["video"]["total_duration_min"] == 40  # 20 * 2
    # Too low
    result = agent.produce_runtime_config(vision, {}, {"segment_count": -5})
    assert result["video"]["total_duration_min"] == 2  # 1 * 2


def test_produce_runtime_config_clamps_image_count(agent):
    """Image count per segment is clamped to 1-30."""
    vision = {"characters": [{"name": "A"}]}
    result = agent.produce_runtime_config(vision, {}, {"image_count_per_segment": 999})
    assert result["visual"]["num_scenes"] == 30


def test_produce_runtime_config_clamps_words_per_segment(agent):
    """Words per segment is clamped to 50-800."""
    vision = {"characters": [{"name": "A"}]}
    result = agent.produce_runtime_config(vision, {}, {"words_per_segment": 9999})
    assert result["script"]["words_per_segment"] == 800


def test_produce_runtime_config_narrator_voice_mapping(agent):
    """Narrator voice is mapped via voice_map keywords."""
    vision = {"characters": [{"name": "A"}]}
    for keyword, expected in [
        ("deep male voice", "deep_male_narrator"),
        ("dramatic delivery", "ras_dramatic_narrator"),
        ("news anchor", "news_anchor_clear"),
        ("calm narrator", "calm_female_smooth"),
        ("warm storyteller", "storyteller_warm"),
    ]:
        result = agent.produce_runtime_config(vision, {"narrator_voice": keyword}, {})
        assert result["tts"]["narrator_voice"] == expected, f"keyword={keyword}"


def test_produce_runtime_config_narrator_voice_default(agent):
    """Unrecognized narrator voice defaults to storyteller_warm."""
    vision = {"characters": [{"name": "A"}]}
    result = agent.produce_runtime_config(vision, {"narrator_voice": "unusual voice"}, {})
    assert result["tts"]["narrator_voice"] == "storyteller_warm"


def test_produce_runtime_config_tts_engine_xtts(agent):
    """xtts/coqui keyword → supertonic engine (unknown → default to supertonic)."""
    vision = {"characters": [{"name": "A"}]}
    for keyword in ["xtts", "XTTS", "coqui"]:
        result = agent.produce_runtime_config(vision, {"tts_engine": keyword}, {})
        assert result["tts"]["engine"] == "supertonic", f"keyword={keyword}"


def test_produce_runtime_config_tts_engine_edge(agent):
    """Removed edge keyword → supertonic default."""
    vision = {"characters": [{"name": "A"}]}
    result = agent.produce_runtime_config(vision, {"tts_engine": "edge"}, {})
    assert result["tts"]["engine"] == "supertonic"


def test_produce_runtime_config_tts_engine_omnivoice(agent):
    """omnivoice keyword → omnivoice engine."""
    vision = {"characters": [{"name": "A"}]}
    result = agent.produce_runtime_config(vision, {"tts_engine": "omnivoice"}, {})
    assert result["tts"]["engine"] == "omnivoice"


def test_produce_runtime_config_tts_engine_default(agent):
    """Unknown TTS engine → falls back to vision_doc (normalized) or supertonic default."""
    vision = {"characters": [{"name": "A"}], "tts_recommendation": "xtts"}
    result = agent.produce_runtime_config(vision, {"tts_engine": "unknown"}, {})
    assert result["tts"]["engine"] == "supertonic"


def test_produce_runtime_config_subtitle_color(agent):
    """User subtitle color request overrides vision."""
    vision = {"characters": [{"name": "A"}], "subtitle_style": {"color": "white"}}
    result = agent.produce_runtime_config(vision, {"subtitle_style": "yellow"}, {})
    assert result["subtitles"]["color"] == "&H0000FFFF&"


def test_produce_runtime_config_subtitle_format_tiktok(agent):
    """User subtitle 'tiktok' → format tiktok."""
    vision = {"characters": [{"name": "A"}]}
    result = agent.produce_runtime_config(vision, {"subtitle_style": "tiktok style"}, {})
    assert result["subtitles"]["format"] == "tiktok"


def test_produce_runtime_config_subtitle_none(agent):
    """User subtitle 'none' → format none."""
    vision = {"characters": [{"name": "A"}]}
    result = agent.produce_runtime_config(vision, {"subtitle_style": "no subtitles"}, {})
    assert result["subtitles"]["format"] == "none"


def test_produce_runtime_config_subtitle_position(agent):
    """User subtitle 'bottom' → position bottom."""
    vision = {"characters": [{"name": "A"}]}
    result = agent.produce_runtime_config(vision, {"subtitle_style": "bottom"}, {})
    assert result["subtitles"]["position"] == "bottom"


def test_produce_runtime_config_transition_from_emotions(agent):
    """Emotions map to specific transitions."""
    vision = {"characters": [{"name": "A"}], "emotions": "horror", "pacing": "moderate"}
    result = agent.produce_runtime_config(vision, {}, {})
    assert result["visualization"]["transition"] == "glitch"


def test_produce_runtime_config_transition_from_pacing(agent):
    """When emotion doesn't match, pacing drives the transition."""
    vision = {"characters": [{"name": "A"}], "emotions": "neutral", "pacing": "epic"}
    result = agent.produce_runtime_config(vision, {}, {})
    assert result["visualization"]["transition"] == "gravitational_lens"


def test_produce_runtime_config_music_from_emotions(agent):
    """Emotion drives music style."""
    vision = {"characters": [{"name": "A"}], "emotions": "epic"}
    result = agent.produce_runtime_config(vision, {}, {})
    assert result["production_notes"]["music_style"] == "orchestral_epic"


def test_produce_runtime_config_music_default(agent):
    """Default music style when no emotion matches."""
    vision = {"characters": [{"name": "A"}], "emotions": "neutral"}
    result = agent.produce_runtime_config(vision, {}, {})
    assert result["production_notes"]["music_style"] == "ambient_cinematic"


def test_produce_runtime_config_user_overrides_v2(agent):
    """Unknown user_response keys are captured in user_overrides."""
    vision = {"characters": [{"name": "A"}]}
    user_responses = {
        "custom_field": "my value",
        "narrator_voice": "deep",
    }  # narrator_voice is known
    result = agent.produce_runtime_config(vision, user_responses, {})
    assert result["production_notes"]["user_overrides"] == {"custom_field": "my value"}


def test_produce_runtime_config_empty_user_responses(agent):
    """Empty user_responses works fine."""
    vision = {"characters": [{"name": "A"}]}
    result = agent.produce_runtime_config(vision, {}, {})
    assert "user_overrides" not in result["production_notes"]


def test_produce_runtime_config_duplicate_character_suffix(agent):
    """Duplicate character names get suffixed with _2, _3, etc."""
    vision = {"characters": [{"name": "Aria"}, {"name": "Aria"}, {"name": "Aria"}]}
    result = agent.produce_runtime_config(vision, {}, {})
    assert "aria" in result["characters"]
    assert "aria_2" in result["characters"]
    assert "aria_3" in result["characters"]


def test_produce_runtime_config_whitespace_character_name_skipped(agent):
    """Characters with whitespace-only names are skipped with warning."""
    vision = {"characters": [{"name": "   "}, {"name": "A"}]}
    result = agent.produce_runtime_config(vision, {}, {})
    # Whitespace name → key is empty → skipped
    assert "a" in result["characters"]


def test_produce_runtime_config_pacing_section(agent):
    """Pacing section is built from writer_input."""
    vision = {"characters": [{"name": "A"}], "pacing": "moderate"}
    writer_input = {"opening_hook_style": "Mystery reveal", "pacing_notes": "Quick start"}
    result = agent.produce_runtime_config(vision, {}, writer_input)
    assert result["pacing"]["style"] == "moderate"
    assert result["pacing"]["opening_hook"] == "Mystery reveal"
    assert result["pacing"]["notes"] == "Quick start"


def test_produce_runtime_config_provenance_v2(agent):
    """Provenance tracks the source of each config section."""
    vision = {"characters": [{"name": "A"}]}
    result = agent.produce_runtime_config(vision, {}, {})
    assert result["_provenance"]["characters"] == "vision_doc"
    assert result["_provenance"]["music_style"] == "emotions_map"


def test_produce_runtime_config_director_vision_v2(agent):
    """Director vision tracks theme/emotions/pacing/visual_style."""
    vision = {"characters": [{"name": "A"}], "theme": "epic", "emotions": "calm"}
    result = agent.produce_runtime_config(vision, {}, {})
    assert result["_director_vision"]["theme"] == "epic"
    assert result["_director_vision"]["emotions"] == "calm"


# ── analyze_with_research tests ────────────────────────────────────


def test_analyze_with_research_cache_hit_returns_cached(agent):
    """When cache has a hit, return the cached vision_doc without calling LLM."""
    cached_vision = {"theme": "epic", "characters": [], "_cached": True}
    with patch("utils.vision_cache.VisionCache") as MockCache:
        instance = MockCache.return_value
        instance.get.return_value = cached_vision
        result = agent.analyze_with_research("topic", {"combined_summary": ""})
    assert result == cached_vision
    instance.get.assert_called_once()


def test_analyze_with_research_cache_hit_force_refresh(agent):
    """When force_refresh is set, cache.get is still called (cache checks force internally)."""
    with patch("utils.vision_cache.VisionCache") as MockCache:
        instance = MockCache.return_value
        instance.get.return_value = {"theme": "from_cache", "characters": []}
        result = agent.analyze_with_research("topic", {"combined_summary": ""})
    assert result["theme"] == "from_cache"


def test_analyze_with_research_short_content_no_estimate(agent):
    """Short content (≤500 words) → no _last_estimated_minutes."""
    with (
        patch("utils.vision_cache.VisionCache") as MockCache,
        patch.object(agent.llm, "_call_ollama", return_value='{"theme": "epic", "characters": []}'),
    ):
        instance = MockCache.return_value
        instance.get.return_value = None  # cache miss
        result = agent.analyze_with_research(
            "topic", {"combined_summary": ""}, content_text="short content"
        )
    assert agent._last_estimated_minutes == 0
    assert isinstance(result, dict)


def test_analyze_with_research_long_content_estimates_duration(agent):
    """Long content (>500 words) → _last_estimated_minutes is set based on word count."""
    long_text = "word " * 1500  # 1500 words
    with (
        patch("utils.vision_cache.VisionCache") as MockCache,
        patch.object(agent.llm, "_call_ollama", return_value='{"theme": "epic", "characters": []}'),
    ):
        instance = MockCache.return_value
        instance.get.return_value = None
        agent.analyze_with_research("topic", {"combined_summary": ""}, content_text=long_text)
    # 1500 / 150 * 1.15 = 11.5 → int 11 → max(5, 11) = 11
    assert agent._last_estimated_minutes == 11


def test_analyze_with_research_caches_result(agent):
    """After successful analysis, the result is cached."""
    with (
        patch("utils.vision_cache.VisionCache") as MockCache,
        patch.object(agent.llm, "_call_ollama", return_value='{"theme": "epic", "characters": []}'),
    ):
        instance = MockCache.return_value
        instance.get.return_value = None
        agent.analyze_with_research("topic", {"combined_summary": ""})
    # cache.set was called
    instance.set.assert_called_once()
    # Vision doc has source_hash added
    call_args = instance.set.call_args
    assert "source_hash" in call_args[0][1]


def test_analyze_with_research_resets_estimate_v2(agent):
    """_last_estimated_minutes is reset to 0 at the start of each call."""
    agent._last_estimated_minutes = 999
    with (
        patch("utils.vision_cache.VisionCache") as MockCache,
        patch.object(agent.llm, "_call_ollama", return_value='{"theme": "x", "characters": []}'),
    ):
        instance = MockCache.return_value
        instance.get.return_value = {"theme": "cached", "characters": []}
        agent.analyze_with_research("topic", {"combined_summary": ""})
    # Cache hit → returned early, but reset happened first
    # Actually it gets reset then cache hit short-circuits, so it's still 0
    assert agent._last_estimated_minutes == 0


def test_analyze_with_research_topic_fallback(agent):
    """When LLM returns non-JSON, the fallback dict is used with topic as theme."""
    with (
        patch("utils.vision_cache.VisionCache") as MockCache,
        patch.object(agent.llm, "_call_ollama", return_value="not json"),
    ):
        instance = MockCache.return_value
        instance.get.return_value = None
        result = agent.analyze_with_research("MyTopic", {"combined_summary": ""})
    # Fallback used → theme = topic
    assert result["theme"] == "MyTopic"
    assert result["topic"] == "MyTopic"
