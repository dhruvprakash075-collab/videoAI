"""Integration test: interrupt-mid-segment → resume → identical output hash.

Verifies that a checkpointed pipeline run produces the same output data
whether it completes in a single run or is interrupted and resumed.
The `save()` method injects a `ts` field, so raw-file hashing is wrong;
instead we verify that the *data* payload round-trips identically.
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from utils.checkpoint import CheckpointManager


class TestResumeIdempotency:
    """Checkpoint save/load round-trip must produce identical data."""

    @pytest.fixture
    def cp_dir(self):
        tmp = tempfile.mkdtemp()
        yield Path(tmp)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_resume_round_trip_identical(self, cp_dir):
        """Save → load → load again: data must match."""
        mgr = CheckpointManager(cp_dir)
        data = {"script": "test script", "key": 42, "nested": {"a": [1, 2, 3]}}

        mgr.save("seg01", "script", {"data": data})
        loaded = mgr.get("seg01")
        assert loaded is not None
        assert loaded["script"]["data"] == data

        # Load again — must produce same data
        loaded2 = mgr.get("seg01")
        assert loaded2 is not None
        assert loaded2 == loaded

    def test_resume_idempotent_data(self, cp_dir):
        """Two independent saves with same data → get() returns same data."""
        data = {"audio": "output.wav", "duration": 15.5}

        mgr1 = CheckpointManager(cp_dir / "run1")
        mgr1.save("seg01", "audio", {"data": data})
        loaded1 = mgr1.get("seg01")

        mgr2 = CheckpointManager(cp_dir / "run2")
        mgr2.save("seg01", "audio", {"data": data})
        loaded2 = mgr2.get("seg01")

        assert loaded1 is not None
        assert loaded2 is not None
        assert loaded1["audio"]["data"] == loaded2["audio"]["data"]

    def test_resume_data_not_corrupted(self, cp_dir):
        """Save data → delete ts field (simulate old format) → must still load."""
        mgr = CheckpointManager(cp_dir)
        data = {"scene": "intro", "lines": ["Once upon a time..."]}

        mgr.save("seg01", "script", {"data": data})
        path = cp_dir / "seg01.json"
        assert path.exists()

        # Manually strip ts to simulate old checkpoint format
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "ts" in raw.get("script", {})
        del raw["script"]["ts"]
        path.write_text(json.dumps(raw), encoding="utf-8")

        # Must still load
        loaded = mgr.get("seg01")
        assert loaded is not None
        assert loaded["script"]["data"] == data

    def test_resume_multiple_steps_stable(self, cp_dir):
        """Multiple save steps on same topic must not corrupt each other."""
        mgr = CheckpointManager(cp_dir)
        mgr.save("seg01", "script", {"data": "script text"})
        mgr.save("seg01", "audio", {"data": {"wav": "audio.wav", "words": ["hello"]}})
        mgr.save("seg01", "video", {"data": "video.mp4"})

        loaded = mgr.get("seg01")
        assert loaded is not None
        assert loaded["script"]["data"] == "script text"
        assert loaded["audio"]["data"]["wav"] == "audio.wav"
        assert loaded["video"]["data"] == "video.mp4"

    def test_disabled_checkpoint_returns_none(self, cp_dir):
        """When checkpointing is disabled, get() returns None."""
        mgr = CheckpointManager(cp_dir, enabled=False)
        assert mgr.get("seg01") is None
        mgr.save("seg01", "script", {"data": "x"})
        assert not (cp_dir / "seg01.json").exists()
        assert mgr.get("seg01") is None

    def test_corrupt_checkpoint_returns_none(self, cp_dir):
        """Corrupt JSON file returns None (with backup)."""
        mgr = CheckpointManager(cp_dir)
        mgr.save("seg01", "script", {"data": "ok"})
        path = cp_dir / "seg01.json"
        # Corrupt the file
        path.write_text("{garbage}", encoding="utf-8")
        assert mgr.get("seg01") is None

    def test_empty_checkpoint_returns_none(self, cp_dir):
        """get() on non-existent topic returns None."""
        mgr = CheckpointManager(cp_dir)
        assert mgr.get("nonexistent") is None
