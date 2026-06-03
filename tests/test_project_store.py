"""Tests for three-tier memory: ProjectStore, StoryStore, PermanentMemoryLog shim.

Covers Requirements 12, 13 and Correctness Property 8 (store isolation).
"""

import json
import logging
from unittest.mock import patch

import pytest

from memory.project_store import PermanentMemoryLog, ProjectStore, StoryStore

# ── ProjectStore ────────────────────────────────────────────────────────────


def test_project_character_persist(tmp_root):
    ps = ProjectStore("proj", root=tmp_root)
    ps.log_character("Hero", "tall, silver hair, blue cloak", "voice.wav")
    got = ps.get_character("Hero")
    assert got["name"] == "Hero"
    assert "silver hair" in got["visual_description"]
    # persisted to disk
    assert (tmp_root / "proj" / "project.json").exists()


def test_project_reload_keeps_data(tmp_root):
    ps = ProjectStore("proj", root=tmp_root)
    ps.log_character("Hero", "tall, silver hair, blue cloak", "")
    # New instance reads the same data
    ps2 = ProjectStore("proj", root=tmp_root)
    assert ps2.get_character("Hero") is not None


def test_visual_lock_set_and_get(tmp_root):
    ps = ProjectStore("proj", root=tmp_root)
    ps.set_visual_lock("hero", "tall, silver hair, blue cloak", seed=42, lora_path="x.safetensors")
    lock = ps.get_visual_lock("hero")
    assert lock["seed"] == 42
    assert lock["lora_path"] == "x.safetensors"


def test_visual_lock_sparse_description_skipped(tmp_root):
    ps = ProjectStore("proj", root=tmp_root)
    ps.set_visual_lock("ghost", "x")  # too short
    assert ps.get_visual_lock("ghost") is None


def test_motif_persist(tmp_root):
    ps = ProjectStore("proj", root=tmp_root)
    ps.log_recurring_motif("thorn", "a black thorn symbol recurs")
    data = json.loads((tmp_root / "proj" / "project.json").read_text())
    assert "thorn" in data["motifs"]


# ── StoryStore isolation (Property 8) ───────────────────────────────────────


def test_story_segment_save(tmp_root):
    ss = StoryStore("story1", project_name="proj", root=tmp_root)
    ss.save_segment(1, "the script", "a summary")
    assert len(ss._data["segments"]) == 1
    assert ss.load_recent_context(3).startswith("Segment 1:")


def test_story_segment_dedup_on_resave(tmp_root):
    ss = StoryStore("story1", project_name="proj", root=tmp_root)
    ss.save_segment(1, "v1", "summary v1")
    ss.save_segment(1, "v2", "summary v2")  # same segment number
    assert len(ss._data["segments"]) == 1
    assert ss._data["segments"][0]["script"] == "v2"


def test_one_time_run_isolated_path(tmp_root):
    ss = StoryStore("oneshot", project_name=None, root=tmp_root)
    assert "_one_time" in str(ss._dir)
    # one-time run never creates a project.json
    ss.save_segment(1, "s", "sum")
    assert not (tmp_root / "oneshot" / "project.json").exists()


def test_project_and_story_separated(tmp_root):
    ps = ProjectStore("proj", root=tmp_root)
    ps.log_character("Hero", "tall, silver hair", "")
    ss = StoryStore("story1", project_name="proj", root=tmp_root)
    ss.save_segment(1, "script", "summary")
    # Project store holds characters; story store holds segments — separate files
    assert (tmp_root / "proj" / "project.json").exists()
    assert (tmp_root / "proj" / "stories" / "story1" / "story.json").exists()


# ── PermanentMemoryLog compatibility shim ───────────────────────────────────


def test_permanent_memory_log_compat_one_time(tmp_path, monkeypatch):
    # one-time (no project) — character stored in the in-memory data view
    pml = PermanentMemoryLog(topic="t", base_dir=str(tmp_path))
    pml.log_character("The Protagonist", "dark practical clothing, striking eyes", "")
    char = pml.get_character("The Protagonist")
    assert char is not None
    assert char["name"] == "The Protagonist"


def test_permanent_memory_log_one_time_persists_to_disk(tmp_path, monkeypatch):
    """P2-7: one-time mode must persist characters/motifs to disk so resume works."""
    import memory.project_store as psmod

    # Redirect PROJECTS_ROOT so the test doesn't write to the real studio_projects/
    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path / "projects")
    # Also patch StoryStore's default root argument by passing it explicitly via
    # a subclass — but since PermanentMemoryLog doesn't expose root, we patch
    # StoryStore to capture the root it would use.
    original_story_store_init = psmod.StoryStore.__init__

    def patched_story_store_init(self, story_name, project_name=None, root=None):
        if root is None:
            root = tmp_path / "projects"
        original_story_store_init(self, story_name, project_name=project_name, root=root)

    monkeypatch.setattr(psmod.StoryStore, "__init__", patched_story_store_init)

    pml = psmod.PermanentMemoryLog(topic="my_story", base_dir=str(tmp_path))
    pml.log_character("The Hero", "tall, silver hair, blue cloak", "voice.wav")
    pml.log_recurring_motif("thorn", "a black thorn symbol recurs in every scene")

    # Verify the story.json file was written to the one-time directory
    story_json = tmp_path / "projects" / "_one_time" / "my_story" / "story.json"
    assert story_json.exists(), "story.json must be written to disk in one-time mode"

    data = json.loads(story_json.read_text(encoding="utf-8"))
    assert "the_hero" in data["characters"], "character must be persisted to disk"
    assert "thorn" in data["motifs"], "motif must be persisted to disk"

    # Simulate resume: create a new PermanentMemoryLog for the same topic
    pml2 = psmod.PermanentMemoryLog(topic="my_story", base_dir=str(tmp_path))
    char = pml2.get_character("The Hero")
    assert char is not None, "character must survive a resume (new PermanentMemoryLog instance)"
    assert char["name"] == "The Hero"


def test_permanent_memory_log_project(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)
    pml = PermanentMemoryLog(topic="story", base_dir=str(tmp_path), project_name="myproj")
    pml.log_character("Hero", "tall, silver hair, blue cloak", "")
    # routed to the project store
    assert pml.get_character("Hero")["name"] == "Hero"


def test_legacy_migration(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path / "projects")
    # Write a legacy flat memory file
    legacy = tmp_path / "legacy_topic_memory.json"
    legacy.write_text(
        json.dumps(
            {
                "characters": {},
                "motifs": {},
                "segments": [{"segment": 1, "script": "old script", "summary": "old summary"}],
                "audit_log": [],
            }
        ),
        encoding="utf-8",
    )
    pml = PermanentMemoryLog(topic="legacy_topic", base_dir=str(tmp_path))
    # migrated segment should be present in the story store
    assert len(pml._story._data["segments"]) == 1
    assert pml._story._data["segments"][0]["script"] == "old script"


def test_continuity_check_pass(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)
    pml = PermanentMemoryLog(topic="story", base_dir=str(tmp_path), project_name="proj")
    pml.log_character("The Protagonist", "blue eyes, dark hair", "")
    ok = pml.check_continuity(
        {"seg_num": 1, "script": "The Protagonist walked", "visual_prompt": "blue eyes"}
    )
    assert ok is True


def test_continuity_check_violation(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)
    pml = PermanentMemoryLog(topic="story", base_dir=str(tmp_path), project_name="proj")
    pml.log_character("The Protagonist", "blue eyes, black hair", "")
    ok = pml.check_continuity(
        {"seg_num": 2, "script": "The Protagonist appeared", "visual_prompt": "red eyes glowing"}
    )
    assert ok is False


# ── Extra project store coverage tests ──


def test_atomic_write_error(tmp_path):
    import memory.project_store as psmod

    # Try to write to a directory path instead of a file path, which raises OSError/PermissionError
    invalid_path = tmp_path / "subdir"
    invalid_path.mkdir()

    with pytest.raises(OSError):
        psmod._atomic_write(invalid_path, {"test": 1})


def test_load_json_error(tmp_path, caplog):
    import memory.project_store as psmod

    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("invalid json", encoding="utf-8")

    # Defaults handling
    assert psmod._load_json(corrupt_file, {"default": 1}) == {"default": 1}
    assert any("Could not load" in msg for msg in caplog.messages)


def test_get_character_none(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)

    # Project store get non-existent char
    ps = ProjectStore("proj", root=tmp_path)
    assert ps.get_character("Unknown") is None

    # PML get non-existent char
    pml = PermanentMemoryLog(topic="story", base_dir=str(tmp_path))
    assert pml.get_character("Unknown") is None


def test_world_lore(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)

    ps = ProjectStore("proj", root=tmp_path)
    ps.add_world_lore("magic", "requires a wand")
    assert ps.get_world_lore() == {"magic": "requires a wand"}


def test_story_store_segment_capping(tmp_path):
    ss = StoryStore("story_cap", project_name=None, root=tmp_path)

    # Save 105 segments
    for i in range(105):
        ss.save_segment(i, f"script {i}", f"summary {i}")

    # Should cap at 100
    assert len(ss._data["segments"]) == 100
    # The oldest 5 should have been dropped, so segment 0..4 are not there
    segments = [s["segment"] for s in ss._data["segments"]]
    assert 0 not in segments
    assert 104 in segments


def test_story_store_audit_capping(tmp_path):
    ss = StoryStore("story_audit_cap", project_name=None, root=tmp_path)

    # Run check_continuity 105 times
    for i in range(105):
        ss.check_continuity({"seg_num": i, "prompt": "no violations"})

    # Read audit.json
    audit_data = json.loads(ss._audit_path.read_text(encoding="utf-8"))
    assert len(audit_data["entries"]) == 100


def test_project_store_continuity_black_hair_violation(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)
    pml = PermanentMemoryLog(topic="story", base_dir=str(tmp_path), project_name="proj")
    pml.log_character("The Protagonist", "blue eyes, black hair", "")
    # Continuity checks blonde hair vs black hair
    ok = pml.check_continuity(
        {"seg_num": 2, "script": "The Protagonist appeared", "visual_prompt": "blonde hair"}
    )
    assert ok is False


def test_legacy_migration_with_chars_and_motifs(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path / "projects")

    # Legacy file with characters and motifs
    legacy = tmp_path / "legacy_full_memory.json"
    legacy.write_text(
        json.dumps(
            {
                "characters": {
                    "hero": {
                        "name": "Hero",
                        "visual_description": "cape",
                        "voice_reference": "hero.wav",
                    }
                },
                "motifs": {"rose": {"name": "Rose", "details": "red rose"}},
                "segments": [{"segment": 1, "script": "script", "summary": "summary"}],
                "audit_log": [],
            }
        ),
        encoding="utf-8",
    )

    pml = PermanentMemoryLog(topic="legacy_full", base_dir=str(tmp_path), project_name="proj")
    # Verify character and motif migrated to project store
    assert pml.get_character("Hero")["visual_description"] == "cape"
    assert pml._project._data["motifs"]["rose"]["details"] == "red rose"


def test_legacy_migration_unparsable_handling(tmp_path, caplog):
    legacy = tmp_path / "legacy_corrupt_memory.json"
    legacy.write_text("invalid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        _pml = PermanentMemoryLog(topic="legacy_corrupt", base_dir=str(tmp_path))
        # Should not raise, but log warning
        assert any("Legacy migration failed" in msg for msg in caplog.messages)


def test_load_one_time_memory_error(tmp_path, caplog):
    import memory.project_store as psmod

    # Create the directory for one-time
    one_time_dir = tmp_path / "_one_time_story"
    one_time_dir.mkdir(parents=True, exist_ok=True)

    # Create invalid checkpoint permanent_memory.json
    corrupt_pm = one_time_dir / "permanent_memory.json"
    corrupt_pm.write_text("{invalid json", encoding="utf-8")

    def mock_load_json_fail(path, default=None):
        if "permanent_memory.json" in str(path):
            raise Exception("Simulated load error")
        if not path.exists():
            return default or {}
        return json.loads(path.read_text(encoding="utf-8"))

    with (
        patch("memory.project_store._load_json", side_effect=mock_load_json_fail),
        caplog.at_level(logging.WARNING),
    ):
        # Specify a base_dir where the _one_time_story directory exists
        _pml = psmod.PermanentMemoryLog(topic="story", base_dir=str(tmp_path))
        # Should log warning
        assert any("Could not load one-time memory on resume" in msg for msg in caplog.messages)


def test_pml_read(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)

    pml = PermanentMemoryLog(topic="story", base_dir=str(tmp_path))
    pml.log_character("Hero", "visual description", "")
    pml.log_recurring_motif("motif", "details")

    data = pml.read()
    assert "Hero" in data["characters"]["hero"]["name"]
    assert "motif" in data["motifs"]["motif"]["name"]
    assert data["segments"] == []
    assert data["audit_log"] == []


def test_pml_save_memory_logic(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)

    # Case 1: project-based save memory
    pml_proj = PermanentMemoryLog(topic="story", base_dir=str(tmp_path), project_name="myproj")
    pml_proj.data["characters"]["test_char"] = {
        "name": "Test Char",
        "visual_description": "desc",
        "voice_reference": "",
    }
    pml_proj._save_memory()
    assert "test_char" in pml_proj._project._data["characters"]

    # Case 2: one-time based save memory failure logs warning
    pml_onetime = PermanentMemoryLog(topic="story", base_dir=str(tmp_path))
    pml_onetime.data["characters"]["test_char2"] = {
        "name": "Test Char 2",
        "visual_description": "desc2",
        "voice_reference": "",
    }

    # Force _atomic_write on checkpoint path to raise error
    def mock_atomic_write_fail(path, data):
        if "permanent_memory.json" in str(path):
            raise OSError("disk full")
        # Otherwise do standard write
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    with (
        patch("memory.project_store._atomic_write", side_effect=mock_atomic_write_fail),
        patch("memory.project_store.log") as mock_log,
    ):
        pml_onetime._save_memory()
        # Verify warning was logged
        mock_log.warning.assert_any_call(
            "[PermanentMemoryLog] Could not write one-time memory checkpoint: disk full"
        )


def test_pml_log_character_and_motif_onetime_failures(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)

    pml = PermanentMemoryLog(topic="story", base_dir=str(tmp_path))

    def mock_atomic_write_fail(path, data):
        if "permanent_memory.json" in str(path):
            raise OSError("write error")
        # Otherwise do standard write
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    with (
        patch("memory.project_store._atomic_write", side_effect=mock_atomic_write_fail),
        patch("memory.project_store.log") as mock_log,
    ):
        # log_character handles write error gracefully
        pml.log_character("Hero", "desc", "")
        mock_log.warning.assert_any_call(
            "[PermanentMemoryLog] Could not write one-time memory checkpoint: write error"
        )

        # log_recurring_motif handles write error gracefully
        mock_log.reset_mock()
        pml.log_recurring_motif("motif", "details")
        mock_log.warning.assert_any_call(
            "[PermanentMemoryLog] Could not write one-time memory checkpoint: write error"
        )


def test_project_log_recurring_motif(tmp_path, monkeypatch):
    import memory.project_store as psmod

    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)

    pml = PermanentMemoryLog(topic="story", base_dir=str(tmp_path), project_name="myproj")
    pml.log_recurring_motif("ring", "golden ring")
    assert pml._project._data["motifs"]["ring"]["details"] == "golden ring"
