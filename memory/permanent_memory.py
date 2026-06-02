"""permanent_memory.py - Permanent Memory Log (compatibility re-export).

The implementation has moved to memory/project_store.py which provides the
three-tier ProjectStore / StoryStore / PermanentMemoryLog architecture.
This module re-exports PermanentMemoryLog so all existing imports keep working.
"""

from .project_store import PermanentMemoryLog, ProjectStore, StoryStore

__all__ = ["PermanentMemoryLog", "ProjectStore", "StoryStore"]
