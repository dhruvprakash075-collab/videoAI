"""test_checkpoint_idempotency.py - Verify checkpoint save/load is idempotent."""

import json
from pathlib import Path

from utils.checkpoint import CheckpointManager


def test_checkpoint_save_idempotent(tmp_path: Path):
    """Saving the same data twice produces the same result (ignoring timestamps)."""
    cp = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    cp.save("topic1", "step1", {"data": "hello"})
    first = json.loads((tmp_path / "topic1.json").read_text(encoding="utf-8"))

    cp.save("topic1", "step1", {"data": "hello"})
    second = json.loads((tmp_path / "topic1.json").read_text(encoding="utf-8"))

    # Data payloads should match; timestamps differ
    assert first["step1"]["data"] == second["step1"]["data"]
    assert "ts" in first["step1"]
    assert "ts" in second["step1"]


def test_checkpoint_save_multiple_steps(tmp_path: Path):
    """Multiple step saves accumulate, not overwrite."""
    cp = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    cp.save("topic1", "step1", {"data": "first"})
    cp.save("topic1", "step2", {"data": "second"})

    raw = json.loads((tmp_path / "topic1.json").read_text(encoding="utf-8"))
    assert "step1" in raw
    assert "step2" in raw
    assert raw["step1"]["data"] == "first"
    assert raw["step2"]["data"] == "second"


def test_checkpoint_clear_and_resave(tmp_path: Path):
    """After clear, save starts fresh."""
    cp = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    cp.save("topic1", "step1", {"data": "old"})
    cp.clear("topic1")
    cp.save("topic1", "step1", {"data": "new"})

    raw = json.loads((tmp_path / "topic1.json").read_text(encoding="utf-8"))
    assert raw["step1"]["data"] == "new"


def test_checkpoint_disabled_no_file(tmp_path: Path):
    """When disabled, no file is written."""
    cp = CheckpointManager(checkpoint_dir=tmp_path, enabled=False)
    cp.save("topic1", "step1", {"data": "x"})
    assert not (tmp_path / "topic1.json").exists()
    assert cp.get("topic1") is None


def test_checkpoint_corrupt_file_returns_none(tmp_path: Path):
    """Corrupt checkpoint returns None and doesn't crash."""
    cp = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    (tmp_path / "topic1.json").write_text("not valid json", encoding="utf-8")
    assert cp.get("topic1") is None


def test_checkpoint_get_empty_if_not_exists(tmp_path: Path):
    cp = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    assert cp.get("nonexistent") is None
