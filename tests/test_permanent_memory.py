"""test_permanent_memory.py - compatibility re-export of project_store names."""

from memory.permanent_memory import PermanentMemoryLog, ProjectStore, StoryStore


def test_reexports_three_names():
    assert PermanentMemoryLog is not None
    assert ProjectStore is not None
    assert StoryStore is not None


def test_reexports_match_source_module():
    from memory.project_store import (
        PermanentMemoryLog as _PML,
        ProjectStore as _PS,
        StoryStore as _SS,
    )

    assert PermanentMemoryLog is _PML
    assert ProjectStore is _PS
    assert StoryStore is _SS
