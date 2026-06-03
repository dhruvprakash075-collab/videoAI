"""test_checkpoint.py - Unit tests for utils/checkpoint.py"""

import json
import logging
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure parent directory is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.checkpoint import CheckpointManager, build_checkpoint_manager


def test_checkpoint_manager_init(tmp_path):
    """Test CheckpointManager initialization settings."""
    # Enabled creates directory
    checkpoint_dir = tmp_path / "checkpoints_enabled"
    mgr_enabled = CheckpointManager(checkpoint_dir=checkpoint_dir, enabled=True)
    assert mgr_enabled.enabled is True
    assert checkpoint_dir.exists()

    # Disabled does not create directory
    checkpoint_dir_disabled = tmp_path / "checkpoints_disabled"
    mgr_disabled = CheckpointManager(checkpoint_dir=checkpoint_dir_disabled, enabled=False)
    assert mgr_disabled.enabled is False
    assert not checkpoint_dir_disabled.exists()


def test_checkpoint_path_safety(tmp_path):
    """Test filename safety logic in _path."""
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)

    # Path should use _safe_filename (which replaces spaces/accents/slashes etc.)
    p = mgr._path("Hello World / Test?")
    assert p.parent == tmp_path
    assert "Hello_World" in p.name
    assert "/" not in p.name
    assert "?" not in p.name


def test_checkpoint_get_variations(tmp_path, caplog):
    """Test get behavior: missing, old checkpoints, or disabled state."""
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    topic = "my_topic"
    p = mgr._path(topic)

    # 1. Disabled manager returns None
    mgr_disabled = CheckpointManager(checkpoint_dir=tmp_path, enabled=False)
    assert mgr_disabled.get(topic) is None

    # 2. Path does not exist returns None
    assert mgr.get(topic) is None

    # 3. Valid recent checkpoint returns data
    data = {"step_1": {"done": True}}
    p.write_text(json.dumps(data), encoding="utf-8")
    assert mgr.get(topic) == data

    # 4. Old checkpoint (>48h) logs loud warning but still returns it
    mtime_49h_ago = time.time() - (49 * 3600)
    import os

    os.utime(p, (mtime_49h_ago, mtime_49h_ago))

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        res = mgr.get(topic)
        assert res == data
        assert any("checkpoint is 49.0h old — resuming anyway" in msg for msg in caplog.messages)

    # 5. Configured threshold (e.g. max_age_hours = 12h) exceeded (but <48h)
    mgr_ttl = CheckpointManager(checkpoint_dir=tmp_path, enabled=True, max_age_hours=12)
    mtime_24h_ago = time.time() - (24 * 3600)
    os.utime(p, (mtime_24h_ago, mtime_24h_ago))

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        res = mgr_ttl.get(topic)
        assert res == data
        assert any("configured threshold: 12h) — resuming anyway" in msg for msg in caplog.messages)


def test_checkpoint_get_corrupt(tmp_path, caplog):
    """Test get handles JSON corruption gracefully."""
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    topic = "corrupt_topic"
    p = mgr._path(topic)
    p.write_text("invalid json {...", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert mgr.get(topic) is None
        assert any("Corrupt checkpoint" in msg for msg in caplog.messages)


def test_read_raw_corruption_backup(tmp_path, caplog):
    """Test _read_raw backs up corrupt files and returns empty dict."""
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    topic = "corrupt_raw"
    p = mgr._path(topic)
    p.write_text("corrupt content", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        raw = mgr._read_raw(topic)
        assert raw == {}
        assert any("Corrupt checkpoint backed up to" in msg for msg in caplog.messages)

        # Verify backup file got created
        backups = list(tmp_path.glob("corrupt_raw.json.corrupt.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "corrupt content"


def test_checkpoint_save_and_atomic_encoder(tmp_path):
    """Test atomic saving and custom Path serialization in CustomEncoder."""
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    topic = "encode_topic"
    p = mgr._path(topic)

    # Save a dict containing Path objects
    data = {"output_file": Path("c:/video/output.mp4"), "resolution": "1920x1080"}

    mgr.save(topic, "segment_1", data)

    assert p.exists()
    saved_data = json.loads(p.read_text(encoding="utf-8"))
    assert (
        saved_data["segment_1"]["output_file"] == "c:\\video\\output.mp4"
        or saved_data["segment_1"]["output_file"] == "c:/video/output.mp4"
    )
    assert saved_data["segment_1"]["resolution"] == "1920x1080"
    assert "ts" in saved_data["segment_1"]

    # Verify backup .bak file was created during second write
    mgr.save(topic, "segment_2", {"done": True})
    bak_path = p.with_suffix(".bak")
    assert bak_path.exists()
    bak_data = json.loads(bak_path.read_text(encoding="utf-8"))
    assert "segment_1" in bak_data
    assert "segment_2" not in bak_data  # bak is the *previous* state


def test_checkpoint_save_windows_defender_retry(tmp_path):
    """Test Windows Defender PermissionError retry loops."""
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    topic = "retry_topic"
    p = mgr._path(topic)

    # 1. Success on 3rd attempt
    call_count = 0
    original_replace = Path.replace

    def mock_replace(self, target):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise PermissionError("[WinError 5] Access is denied")
        original_replace(self, target)

    with patch.object(Path, "replace", mock_replace), patch("time.sleep") as mock_sleep:
        mgr.save(topic, "step1", {"val": 1})
        assert p.exists()
        assert call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.5)

    # 2. Permanent failure after 5 attempts
    call_count = 0

    def mock_replace_fail(self, target):
        nonlocal call_count
        call_count += 1
        raise PermissionError("[WinError 5] Access is denied")

    with (
        patch.object(Path, "replace", mock_replace_fail),
        patch("time.sleep") as mock_sleep,
        pytest.raises(PermissionError),
    ):
        mgr.save(topic, "step2", {"val": 2})

    assert call_count == 5
    # The temporary file (.tmp) should be cleaned up on permanent failure
    tmp_file = p.with_suffix(".json.tmp")
    assert not tmp_file.exists()


def test_checkpoint_clear_siblings(tmp_path):
    """Test clear deletes main checkpoint and all dirty sibling files."""
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    topic = "clear_topic"
    p = mgr._path(topic)

    # Create main, backup, temp, corrupt, and unrelated file
    p.write_text("{}", encoding="utf-8")
    p.with_suffix(".bak").write_text("{}", encoding="utf-8")
    p.with_suffix(".json.tmp").write_text("{}", encoding="utf-8")
    (tmp_path / "clear_topic.json.corrupt.12345").write_text("{}", encoding="utf-8")

    # Unrelated file that shouldn't be touched
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text("{}", encoding="utf-8")

    mgr.clear(topic)

    assert not p.exists()
    assert not p.with_suffix(".bak").exists()
    assert not p.with_suffix(".json.tmp").exists()
    assert not (tmp_path / "clear_topic.json.corrupt.12345").exists()
    assert unrelated.exists()


def test_build_checkpoint_manager():
    """Test build_checkpoint_manager parses config structure."""
    config = {
        "checkpoint": {"dir": "custom_checkpoints_path", "enabled": True, "max_age_hours": 24}
    }

    mgr = build_checkpoint_manager(config)
    assert mgr.dir == Path("custom_checkpoints_path")
    assert mgr.enabled is True
    assert mgr.max_age_hours == 24


def test_checkpoint_save_disabled(tmp_path):
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=False)
    # Save should return immediately
    mgr.save("topic", "step", {})
    assert not (tmp_path / "topic.json").exists()


def test_checkpoint_delete_alias(tmp_path):
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    p = mgr._path("topic")
    p.write_text("{}", encoding="utf-8")
    assert p.exists()
    mgr.delete("topic")
    assert not p.exists()


def test_checkpoint_corrupt_backup_fails(tmp_path):
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    p = mgr._path("topic")
    p.write_text("corrupt content", encoding="utf-8")
    with patch("shutil.copy2", side_effect=RuntimeError("disk full")):
        raw = mgr._read_raw("topic")
        assert raw == {}


def test_checkpoint_custom_encoder_fallback(tmp_path):
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)

    # Object that cannot be serialized naturally
    class Unserializable:
        def __repr__(self):
            return "unserializable_repr"

    mgr.save("topic", "step", {"obj": Unserializable()})
    p = mgr._path("topic")
    assert p.exists()
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["step"]["obj"] == "unserializable_repr"


def test_checkpoint_save_tmp_unlink_fails(tmp_path):
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    topic = "topic_unlink_fails"
    # force permanent failure
    with (
        patch.object(Path, "replace", side_effect=PermissionError("locked")),
        patch.object(Path, "unlink", side_effect=RuntimeError("cannot delete")),
        patch("time.sleep"),
        pytest.raises(PermissionError),
    ):
        mgr.save(topic, "step", {"val": 1})


def test_checkpoint_clear_unlink_sibling_fails(tmp_path):
    mgr = CheckpointManager(checkpoint_dir=tmp_path, enabled=True)
    topic = "clear_sibling_fails"
    p = mgr._path(topic)
    p.write_text("{}", encoding="utf-8")
    p.with_suffix(".bak").write_text("{}", encoding="utf-8")

    # Mock Path.unlink to fail for .bak
    original_unlink = Path.unlink

    def mock_unlink(self):
        if self.suffix == ".bak":
            raise RuntimeError("locked bak")
        original_unlink(self)

    with patch.object(Path, "unlink", mock_unlink):
        mgr.clear(topic)
        assert not p.exists()
        assert p.with_suffix(".bak").exists()
