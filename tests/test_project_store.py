"""Tests for three-tier memory: ProjectStore, StoryStore, PermanentMemoryLog shim.

Covers Requirements 12, 13 and Correctness Property 8 (store isolation).
"""

import json

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
    legacy.write_text(json.dumps({
        "characters": {}, "motifs": {},
        "segments": [{"segment": 1, "script": "old script", "summary": "old summary"}],
        "audit_log": [],
    }), encoding="utf-8")
    pml = PermanentMemoryLog(topic="legacy_topic", base_dir=str(tmp_path))
    # migrated segment should be present in the story store
    assert len(pml._story._data["segments"]) == 1
    assert pml._story._data["segments"][0]["script"] == "old script"


def test_continuity_check_pass(tmp_path, monkeypatch):
    import memory.project_store as psmod
    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)
    pml = PermanentMemoryLog(topic="story", base_dir=str(tmp_path), project_name="proj")
    pml.log_character("The Protagonist", "blue eyes, dark hair", "")
    ok = pml.check_continuity({"seg_num": 1, "script": "The Protagonist walked", "visual_prompt": "blue eyes"})
    assert ok is True


def test_continuity_check_violation(tmp_path, monkeypatch):
    import memory.project_store as psmod
    monkeypatch.setattr(psmod, "PROJECTS_ROOT", tmp_path)
    pml = PermanentMemoryLog(topic="story", base_dir=str(tmp_path), project_name="proj")
    pml.log_character("The Protagonist", "blue eyes, black hair", "")
    ok = pml.check_continuity({"seg_num": 2, "script": "The Protagonist appeared", "visual_prompt": "red eyes glowing"})
    assert ok is False
