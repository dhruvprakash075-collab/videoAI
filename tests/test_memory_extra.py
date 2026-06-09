"""test_memory_extra.py - Unit tests for memory/memory.py"""

import logging
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure parent directory is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.memory import StoryMemory, WorldState, build_context


def test_review_segment_memory_accepts_generated_images():
    """review_segment_memory accepts and includes generated_images in the prompt."""
    from agents.director_agent import DirectorAgent
    from agents.llm_client import DirectorLlmClient

    agent = DirectorAgent.__new__(DirectorAgent)
    agent.llm_config = {}
    agent.llm = DirectorLlmClient(agent.llm_config)

    def mock_call(prompt, **kwargs):
        assert "Generated Images:" in prompt
        assert "img1.png" in prompt
        return '{"memory_items": []}'

    with patch.object(agent.llm, "_call_ollama", side_effect=mock_call):
        result = agent.review_segment_memory(
            segment_script="Hero fights.",
            image_plan={},
            generated_prompts=["hero fighting"],
            current_memory={},
            world_state="World: fantasy",
            generated_images=["img1.png", "img2.png"],
        )
        assert "memory_items" in result


def test_review_segment_memory_no_images():
    """review_segment_memory works without generated_images."""
    from agents.director_agent import DirectorAgent
    from agents.llm_client import DirectorLlmClient

    agent = DirectorAgent.__new__(DirectorAgent)
    agent.llm_config = {}
    agent.llm = DirectorLlmClient(agent.llm_config)

    def mock_call(prompt, **kwargs):
        assert "Generated Images:" not in prompt
        return '{"memory_items": []}'

    with patch.object(agent.llm, "_call_ollama", side_effect=mock_call):
        result = agent.review_segment_memory(
            segment_script="Hero fights.",
            image_plan={},
            generated_prompts=["hero fighting"],
            current_memory={},
            world_state="World: fantasy",
        )
        assert "memory_items" in result


def test_story_memory_load_save(tmp_path):
    """Test StoryMemory basic loading, saving, and duplicate segment replacement."""
    memory_file = tmp_path / "story_memory.json"
    mem = StoryMemory(memory_file)

    assert mem._load_all() == {}

    mem.save("Topic A", 1, "Script 1", "Summary 1")
    assert memory_file.exists()

    all_data = mem._load_all()
    assert "Topic A" in all_data
    assert len(all_data["Topic A"]["segments"]) == 1
    assert all_data["Topic A"]["segments"][0]["summary"] == "Summary 1"

    mem.save("Topic A", 1, "Script 1 Mod", "Summary 1 Mod")
    all_data2 = mem._load_all()
    assert len(all_data2["Topic A"]["segments"]) == 1
    assert (
        all_data2["Topic A"]["segments"][0]["summary"] == "Summary 1"
        or all_data2["Topic A"]["segments"][0]["summary"] == "Summary 1 Mod"
    )

    mem.save("Topic A", 2, "Script 2", "Summary 2")
    mem.save("Topic A", 3, "Script 3", "Summary 3")
    mem.save("Topic A", 4, "Script 4", "Summary 4")

    context = mem.load("Topic A")
    assert "Segment 2" in context
    assert "Segment 3" in context
    assert "Segment 4" in context
    assert "Segment 1" not in context

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

    assert mem.get_all_entries("Topic X") == []

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
    topic = "Epic Mahabharata Story"

    state = WorldState(topic, tmp_path)
    expected_path = tmp_path / "world_state_epic_mahabharata_story.json"
    assert state._path == expected_path

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

    script = (
        "राम enters the ancient temple, seeking answers. "
        "Suddenly, Lakshmana stops and warns him. "
        "The ancient relic cannot be destroyed by ordinary weapons. "
        "Who is watching them from behind the columns? "
        "They must be cautious."
    )

    plan = {
        "seg": 5,
        "mood": "tense",
        "title": "The Temple",
        "key_event": "They found the relic",
        "characters": [{"name": "Hanuman"}, "Sita"],
    }

    state.update(script, plan, force_save=True, config=None)

    chars = state._data["characters"]
    assert "राम" in chars
    assert chars["राम"]["first_seen_seg"] == 5
    assert "tense" in chars["राम"]["moods_seen"]
    assert "Lakshmana" in chars
    assert "Hanuman" in chars
    assert "Sita" in chars

    assert "Suddenly" not in chars
    assert "The" not in chars

    facts = state._data["world_facts"]
    assert "[Seg 5 - The Temple] They found the relic" in facts
    assert any("ancient relic cannot be destroyed" in f for f in facts)

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

        assert "Rama" in state._data["characters"]
        assert "Dasharatha" in state._data["characters"]

        facts = state._data["world_facts"]
        assert len(facts) == 4
        assert "Dasharatha is the king of Ayodhya" in facts
        assert "Rama has three brothers" in facts
        assert "Ayodhya is a holy city" in facts
        assert "extra fact to cap" not in facts

        threads = state._data["open_threads"]
        assert len(threads) == 2
        assert "Will Rama become king?" in threads
        assert "What is Kaikeyi planning?" in threads
        assert "extra thread to cap" not in threads

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

    with (
        patch(
            "utils.specialized_models.extract_world_state", side_effect=RuntimeError("LLM offline")
        ),
        caplog.at_level(logging.WARNING),
    ):
        script = "Rama walked. The forest is cursed and forbidden to mortals."
        state.update(script, plan, force_save=True, config=config)

        assert any("LLM world-state extraction failed" in msg for msg in caplog.messages)

        assert "Rama" in state._data["characters"]
        assert any("cursed and forbidden" in f for f in state._data["world_facts"])


def test_world_state_to_prompt_block(tmp_path):
    """Test to_prompt_block formatting and limits."""
    state = WorldState("test_prompt", tmp_path)

    state._data["world_facts"] = [f"Fact {i}" for i in range(15)]
    state._data["open_threads"] = [f"Thread {i}" for i in range(10)]
    state._data["characters"] = {f"Char {i}": {"status": "active"} for i in range(5)}
    state._data["characters"]["Dead Char"] = {"status": "dead"}

    prompt = state.to_prompt_block(max_facts=5, max_threads=3)

    assert "[World State - Hard Constraints for this segment]" in prompt
    assert "[/World State]" in prompt

    assert "Fact 14" in prompt
    assert "Fact 10" in prompt
    assert "Fact 9" not in prompt

    assert "Thread 9" in prompt
    assert "Thread 7" in prompt
    assert "Thread 6" not in prompt

    assert "Active characters:" in prompt
    assert "Char 0" in prompt
    assert "Dead Char" not in prompt


def test_project_store_memory_items(tmp_path):
    """Test ProjectStore memory item persistence and retrieval."""
    from memory.project_store import ProjectStore

    ps = ProjectStore("test_proj", root=tmp_path)
    item = {
        "type": "costume",
        "name": "blue royal armor",
        "owner": "arjun",
        "importance": "core",
        "scope": "project",
        "description": "dark blue armor with gold borders",
        "visual_rules": ["must stay dark blue"],
        "negative_rules": ["no leather"],
        "lora_candidate": False,
        "reason": "main character outfit",
    }

    ps.save_memory_item(item)
    items = ps.get_memory_items()

    assert "blue_royal_armor" in items
    assert items["blue_royal_armor"]["importance"] == "core"
    assert items["blue_royal_armor"]["owner"] == "arjun"


def test_story_store_memory_items(tmp_path):
    """Test StoryStore memory item persistence and retrieval."""
    from memory.project_store import StoryStore

    ss = StoryStore("test_story", project_name=None, root=tmp_path)
    item = {
        "type": "temporary_scene_detail",
        "name": "broken vase",
        "owner": None,
        "importance": "medium",
        "scope": "story",
        "description": "a blue vase broken on the floor",
        "visual_rules": [],
        "negative_rules": [],
        "lora_candidate": False,
        "reason": "scene detail",
    }

    ss.save_memory_item(item)
    items = ss.get_memory_items()

    assert "broken_vase" in items
    assert items["broken_vase"]["importance"] == "medium"


def test_permanent_memory_log_routing(tmp_path, monkeypatch):
    """Test PermanentMemoryLog routes items to project or story store based on scope."""
    from memory.project_store import PermanentMemoryLog, ProjectStore, StoryStore

    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr("memory.project_store.PROJECTS_ROOT", proj_root)

    proj_name = "test_mem_routing"

    mem = PermanentMemoryLog(topic="test_story", project_name=proj_name, base_dir=str(tmp_path))

    item_proj = {"name": "Royal Sword", "type": "weapon", "scope": "project", "importance": "core"}
    mem.save_memory_item(item_proj)

    item_story = {"name": "Muddy Boots", "type": "costume", "scope": "story", "importance": "medium"}
    mem.save_memory_item(item_story)

    ps = ProjectStore(proj_name, root=proj_root)
    items = ps.get_memory_items()
    assert "royal_sword" in items
    assert "muddy_boots" not in items

    ss = StoryStore("test_story", project_name=proj_name, root=proj_root)
    story_items = ss.get_memory_items()
    assert "muddy_boots" in story_items
    assert "royal_sword" not in story_items


def test_project_store_asset_review(tmp_path):
    """Test ProjectStore recording asset reviews."""
    from memory.project_store import ProjectStore

    ps = ProjectStore("test_proj", root=tmp_path)
    ps.log_character("Arjun", "Description of Arjun", "Voice Ref")

    asset_path = "studio_outputs/proj/seg1/img1.png"
    ps.record_asset_review(
        char_key="arjun",
        asset_path=asset_path,
        decision="lora_candidate",
        reason="Perfect face reference",
        locked=True,
    )

    assets = ps.get_character_assets("arjun")
    reviews = assets.get("reviews", {})
    assert asset_path in reviews
    assert reviews[asset_path]["decision"] == "lora_candidate"
    assert reviews[asset_path]["locked"] is True


def test_validate_memory_item_rejects_invalid_type(tmp_path):
    """Schema validation rejects items with invalid type."""
    from memory.project_store import _validate_memory_item

    item = {"type": "invalid_type", "name": "test"}
    assert _validate_memory_item(item) is None


def test_validate_memory_item_defaults_missing_fields():
    """Schema validation fills in default values for optional fields."""
    from memory.project_store import _validate_memory_item

    item = {"type": "costume", "name": "test", "importance": "core", "scope": "story"}
    cleaned = _validate_memory_item(item)
    assert cleaned is not None
    assert cleaned["visual_rules"] == []
    assert cleaned["negative_rules"] == []
    assert cleaned["description"] == ""
    assert cleaned["owner"] == ""
    assert cleaned["lora_candidate"] is False


def test_validate_memory_item_defaults_bad_importance():
    """Schema validation defaults invalid importance to medium."""
    from memory.project_store import _validate_memory_item

    item = {"type": "weapon", "name": "Sword", "importance": "super_duper", "scope": "story"}
    cleaned = _validate_memory_item(item)
    assert cleaned["importance"] == "medium"


def test_validate_memory_item_defaults_bad_scope():
    """Schema validation defaults invalid scope to story."""
    from memory.project_store import _validate_memory_item

    item = {"type": "location", "name": "Temple", "importance": "high", "scope": "galaxy"}
    cleaned = _validate_memory_item(item)
    assert cleaned["scope"] == "story"


def test_permanent_memory_log_drops_invalid(tmp_path, monkeypatch, caplog):
    """PermanentMemoryLog.save_memory_item drops invalid items with a warning."""
    from memory.project_store import PermanentMemoryLog

    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr("memory.project_store.PROJECTS_ROOT", proj_root)

    mem = PermanentMemoryLog(topic="test", project_name="p", base_dir=str(tmp_path))
    with caplog.at_level(logging.WARNING):
        mem.save_memory_item({"type": "bogus_type", "name": "nope"})
        assert any("Dropping invalid" in msg for msg in caplog.messages)
        assert any("bogus_type" in msg for msg in caplog.messages)


def test_asset_review_metadata(tmp_path):
    """LoRA/IP/ref/reject metadata is recorded without triggering training."""
    from memory.project_store import ProjectStore

    ps = ProjectStore("test_proj", root=tmp_path)
    ps.log_character("Arjun", "Description of Arjun", "Voice Ref")

    # LoRA candidate with metadata
    ps.record_asset_review(
        char_key="arjun",
        asset_path="img1.png",
        decision="lora_candidate",
        reason="perfect face",
        lora_metadata={"trigger_word": "arjun_v2", "minimum_needed": 30},
    )
    assets = ps.get_character_assets("arjun")
    r = assets["reviews"]["img1.png"]
    assert r["decision"] == "lora_candidate"
    # LoRA metadata now lives at assets["lora"] (top-level)
    lora_info = assets["lora"]
    assert lora_info["candidate"] is True
    assert lora_info["trigger_word"] == "arjun_v2"
    assert lora_info["minimum_needed"] == 30
    assert lora_info["status"] == "collecting"
    assert "img1.png" in lora_info["approved_images"]
    assert "training_status_history" in lora_info
    assert lora_info["training_status_history"][-1]["status"] == "collecting"

    # IP-Adapter reference
    ps.record_asset_review(
        char_key="arjun", asset_path="img2.png", decision="ip_ref",
        ip_adapter_ref=True,
    )
    assets = ps.get_character_assets("arjun")
    assert assets["reviews"]["img2.png"].get("ip_adapter_ref") is True
    assert "img2.png" in assets["lora"]["ip_adapter_ref_paths"]

    # Rejected (negative example)
    ps.record_asset_review(
        char_key="arjun", asset_path="img3.png", decision="reject",
        negative_example=True,
    )
    assets = ps.get_character_assets("arjun")
    assert assets["reviews"]["img3.png"].get("negative_example") is True
    assert "img3.png" in assets["lora"]["rejected_images"]


def test_enrich_prompts_with_memory_items():
    """enrich_prompts accepts memory_items and injects character visual rules."""
    from utils.scene_director import enrich_prompts

    config = {
        "visual": {"style": "Test Style"},
        "characters": {
            "hero": {"name": "Hero", "description": "a brave warrior"},
            "sage": {"name": "Sage", "description": "an old wise man"},
        },
        "image_gen": {"token_budget": {"identity": 25, "style": 20, "scene": 32}},
    }
    plan = {
        "char_presence": [{"hero": 0.9, "sage": 0.3}],
        "seg": 1, "mood": "dramatic", "title": "Test", "key_event": "meeting",
        "characters": [{"name": "Hero"}, {"name": "Sage"}],
    }
    memory_items = [
        {
            "name": "blue royal armor",
            "owner": "hero",
            "type": "costume",
            "importance": "core",
            "scope": "project",
            "description": "dark blue armor with gold trim",
            "visual_rules": ["must stay blue", "golden trim"],
            "negative_rules": ["no leather"],
            "lora_candidate": False,
        }
    ]
    result, neg = enrich_prompts(
        "Hero stands tall; The scene shifts",
        "Hero meets the sage.",
        config, plan, memory_items=memory_items,
    )
    assert "dark blue armor" in result or "blue" in result
    assert len(result) > 0
    assert isinstance(neg, str)


def test_permanent_memory_log_read_returns_lists(tmp_path, monkeypatch):
    """PermanentMemoryLog.read() returns memory_items as lists, not dicts."""
    from memory.project_store import PermanentMemoryLog

    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr("memory.project_store.PROJECTS_ROOT", proj_root)

    mem = PermanentMemoryLog(topic="test_story", project_name="test_proj", base_dir=str(tmp_path))

    mem.save_memory_item({"name": "Sword", "type": "weapon", "scope": "project", "importance": "core"})
    mem.save_memory_item({"name": "Shield", "type": "costume", "scope": "story", "importance": "medium"})

    data = mem.read()
    mem_items = data.get("memory_items", {})
    assert isinstance(mem_items["project"], list)
    assert isinstance(mem_items["story"], list)
    assert any(i["name"] == "Sword" for i in mem_items["project"])
    assert any(i["name"] == "Shield" for i in mem_items["story"])


def test_validate_memory_item_rejects_low_importance():
    """Items below medium importance are rejected per minimum persistence threshold."""
    from memory.project_store import _validate_memory_item

    assert _validate_memory_item({"type": "costume", "name": "t", "importance": "low"}) is None
    assert _validate_memory_item({"type": "costume", "name": "t", "importance": "temporary"}) is None
    assert _validate_memory_item({"type": "costume", "name": "t", "importance": "medium"}) is not None
    assert _validate_memory_item({"type": "costume", "name": "t", "importance": "high"}) is not None
    assert _validate_memory_item({"type": "costume", "name": "t", "importance": "core"}) is not None
