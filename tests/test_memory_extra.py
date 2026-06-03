"""test_memory_extra.py - Unit tests for memory/memory.py"""

import logging
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure parent directory is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.memory import StoryMemory, WorldState, build_context


def test_story_memory_load_save(tmp_path):
    """Test StoryMemory basic loading, saving, and duplicate segment replacement."""
    memory_file = tmp_path / "story_memory.json"
    mem = StoryMemory(memory_file)

    # 1. Loading non-existent file returns empty dict
    assert mem._load_all() == {}

    # 2. Saving a segment creates the file and tracks data
    mem.save("Topic A", 1, "Script 1", "Summary 1")
    assert memory_file.exists()

    all_data = mem._load_all()
    assert "Topic A" in all_data
    assert len(all_data["Topic A"]["segments"]) == 1
    assert all_data["Topic A"]["segments"][0]["summary"] == "Summary 1"

    # 3. Saving the same segment again replaces the old summary (duplicate replacement)
    mem.save("Topic A", 1, "Script 1 Mod", "Summary 1 Mod")
    all_data2 = mem._load_all()
    assert len(all_data2["Topic A"]["segments"]) == 1
    assert (
        all_data2["Topic A"]["segments"][0]["summary"] == "Summary 1"
        or all_data2["Topic A"]["segments"][0]["summary"] == "Summary 1 Mod"
    )

    # 4. Load returns recent 3 segments formatted context
    mem.save("Topic A", 2, "Script 2", "Summary 2")
    mem.save("Topic A", 3, "Script 3", "Summary 3")
    mem.save("Topic A", 4, "Script 4", "Summary 4")

    context = mem.load("Topic A")
    # Should only contain Segment 2, 3, 4 (recent 3)
    assert "Segment 2" in context
    assert "Segment 3" in context
    assert "Segment 4" in context
    assert "Segment 1" not in context

    # 5. Clear wipes the topic
    mem.clear("Topic A")
    assert mem.load("Topic A") == ""
    assert mem._load_all() == {}


def test_story_memory_corrupt_file(tmp_path, caplog):
    """Test StoryMemory handles corrupt files gracefully."""
    memory_file = tmp_path / "corrupt_story.json"
    memory_file.write_text("{invalid json", encoding="utf-8")

    mem = StoryMemory(memory_file)
    with caplog.at_level(logging.WARNING):
        assert mem._load_all() == {}
        assert any("Corrupt memory file" in msg for msg in caplog.messages)


def test_story_memory_get_all_entries(tmp_path):
    """Test get_all_entries returns list of segment dicts."""
    memory_file = tmp_path / "story_memory.json"
    mem = StoryMemory(memory_file)

    # Empty
    assert mem.get_all_entries("Topic X") == []

    # Save some
    mem.save("Topic X", 1, "Script 1", "Summary 1")
    mem.save("Topic X", 2, "Script 2", "Summary 2")

    entries = mem.get_all_entries("Topic X")
    assert len(entries) == 2
    assert entries[0]["segment"] == 1
    assert entries[1]["summary"] == "Summary 2"


def test_build_context():
    """Test build_context wrapper formatting."""
    assert build_context("") == ""
    assert (
        build_context("Segment 1: Hello")
        == "[Previous story context]\nSegment 1: Hello\n[/Previous story context]\n"
    )


def test_world_state_init_and_corrupt(tmp_path, caplog):
    """Test WorldState init, name normalization, and corruption recovery."""
    # Topic containing spaces and uppercase letters
    topic = "Epic Mahabharata Story"

    # Safe filename should be world_state_epic_mahabharata_story.json
    state = WorldState(topic, tmp_path)
    expected_path = tmp_path / "world_state_epic_mahabharata_story.json"
    assert state._path == expected_path

    # Test corruption recovery
    expected_path.write_text("invalid json {...", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        state_corrupt = WorldState(topic, tmp_path)
        assert state_corrupt._data == {
            "characters": {},
            "world_facts": [],
            "open_threads": [],
            "resolved_threads": [],
        }
        assert any("Corrupt state file" in msg for msg in caplog.messages)


def test_world_state_update_regex_extraction(tmp_path):
    """Test regex extraction for characters (both English and Devanagari initial), facts, and threads."""
    state = WorldState("test_regex", tmp_path)

    # Script containing Devanagari name "राम", English name "Lakshmana", a fact pattern, and a question sentence
    script = (
        "राम enters the ancient temple, seeking answers. "
        "Suddenly, Lakshmana stops and warns him. "
        "The ancient relic cannot be destroyed by ordinary weapons. "
        "Who is watching them from behind the columns? "
        "They must be cautious."
    )

    plan = {
        "seg": 5,  # 5 is divisible by 5, should trigger open thread scan
        "mood": "tense",
        "title": "The Temple",
        "key_event": "They found the relic",
        "characters": [{"name": "Hanuman"}, "Sita"],
    }

    state.update(script, plan, force_save=True, config=None)

    # Verify characters (including Devanagari, plan list dict, and plan list string)
    chars = state._data["characters"]
    assert "राम" in chars
    assert chars["राम"]["first_seen_seg"] == 5
    assert "tense" in chars["राम"]["moods_seen"]
    assert "Lakshmana" in chars
    assert "Hanuman" in chars
    assert "Sita" in chars

    # Verify exclusions (common words like "Suddenly" should not be treated as characters)
    assert "Suddenly" not in chars
    assert "The" not in chars

    # Verify world facts (contains regex extracted and the key event)
    facts = state._data["world_facts"]
    # Key event fact format: "[Seg {seg_num} - {title}] {key_event}"
    assert "[Seg 5 - The Temple] They found the relic" in facts
    # Regex matched ancient relic fact
    assert any("ancient relic cannot be destroyed" in f for f in facts)

    # Verify open threads (triggered because seg_num % 5 == 0)
    threads = state._data["open_threads"]
    assert any("Who is watching them" in t for t in threads)


def test_world_state_update_llm_extraction(tmp_path):
    """Test WorldState update using LLM specialized extraction."""
    state = WorldState("test_llm", tmp_path)

    config = {"memory": {"llm_world_state": True}}

    plan = {"seg": 1, "mood": "calm", "title": "Introduction", "key_event": "Rama was born"}

    mock_llm_result = {
        "characters": ["Rama", "Dasharatha"],
        "facts": [
            "Dasharatha is the king of Ayodhya",
            "Rama has three brothers",
            "Ayodhya is a holy city",
            "extra fact to cap",
        ],
        "open_threads": [
            "Will Rama become king?",
            "What is Kaikeyi planning?",
            "extra thread to cap",
        ],
        "resolved_threads": ["The sage's sacrifice succeeded"],
    }

    with patch(
        "utils.specialized_models.extract_world_state", return_value=mock_llm_result
    ) as mock_extract:
        state.update("Rama was born in Ayodhya.", plan, force_save=True, config=config)

        mock_extract.assert_called_once_with("Rama was born in Ayodhya.", config)

        # Verify merged characters
        assert "Rama" in state._data["characters"]
        assert "Dasharatha" in state._data["characters"]

        # Verify facts capped at 3 new per segment (excluding the key event which is added separately)
        facts = state._data["world_facts"]
        assert len(facts) == 4  # 3 from LLM + 1 key event
        assert "Dasharatha is the king of Ayodhya" in facts
        assert "Rama has three brothers" in facts
        assert "Ayodhya is a holy city" in facts
        assert "extra fact to cap" not in facts  # Capped out

        # Verify threads capped at 2
        threads = state._data["open_threads"]
        assert len(threads) == 2
        assert "Will Rama become king?" in threads
        assert "What is Kaikeyi planning?" in threads
        assert "extra thread to cap" not in threads

        # Verify resolved threads
        assert "The sage's sacrifice succeeded" in state._data["resolved_threads"]


def test_world_state_update_llm_fallback_to_regex(tmp_path, caplog):
    """Test WorldState update falls back to regex when LLM extraction fails."""
    state = WorldState("test_llm_fail", tmp_path)

    config = {"memory": {"llm_world_state": True}}

    plan = {
        "seg": 1,
        "mood": "mysterious",
        "title": "The Forest",
        "key_event": "Rama entered the dark forest",
    }

    # Mock LLM to throw an exception
    with (
        patch(
            "utils.specialized_models.extract_world_state", side_effect=RuntimeError("LLM offline")
        ),
        caplog.at_level(logging.WARNING),
    ):
        # Script with regex-catchable fact
        script = "Rama walked. The forest is cursed and forbidden to mortals."
        state.update(script, plan, force_save=True, config=config)

        # Should log warning
        assert any("LLM world-state extraction failed" in msg for msg in caplog.messages)

        # Regex fallback should have extracted character "Rama" and the curs/forb fact
        assert "Rama" in state._data["characters"]
        assert any("cursed and forbidden" in f for f in state._data["world_facts"])


def test_world_state_to_prompt_block(tmp_path):
    """Test to_prompt_block formatting and limits."""
    state = WorldState("test_prompt", tmp_path)

    # Add dummy data
    state._data["world_facts"] = [f"Fact {i}" for i in range(15)]
    state._data["open_threads"] = [f"Thread {i}" for i in range(10)]
    state._data["characters"] = {f"Char {i}": {"status": "active"} for i in range(5)}
    state._data["characters"]["Dead Char"] = {"status": "dead"}

    prompt = state.to_prompt_block(max_facts=5, max_threads=3)

    assert "[World State - Hard Constraints for this segment]" in prompt
    assert "[/World State]" in prompt

    # Assert limits
    assert "Fact 14" in prompt
    assert "Fact 10" in prompt
    assert "Fact 9" not in prompt  # limited to 5

    assert "Thread 9" in prompt
    assert "Thread 7" in prompt
    assert "Thread 6" not in prompt  # limited to 3

    # Active characters should be listed, dead ones omitted
    assert "Active characters:" in prompt
    assert "Char 0" in prompt
    assert "Dead Char" not in prompt
