"""Tests for the Blackboard shared workspace.

Covers Requirement 10 and Correctness Properties 1, 9 (atomic, no-VRAM, round-trip).
"""

import threading

from config.config_schemas import DecisionRecord
from memory.blackboard import Blackboard, get_blackboard


def test_write_read_decision_round_trip(tmp_root):
    bb = Blackboard(tmp_root)
    r = DecisionRecord()
    r.set("segment_count", 11, "user", lock=True)
    r.set("words_per_segment", 175, "writer")
    bb.write_decision(r)

    loaded = bb.read_decision()
    assert loaded is not None
    assert loaded.segment_count.value == 11
    assert loaded.segment_count.locked is True
    assert loaded.words_per_segment.value == 175


def test_read_empty_returns_none(tmp_root):
    bb = Blackboard(tmp_root)
    assert bb.read_decision() is None
    assert bb.read() == {}


def test_atomic_write_no_tmp_leftover(tmp_root):
    bb = Blackboard(tmp_root)
    bb.write({"a": 1})
    bb.write({"b": 2})
    leftover = list(tmp_root.glob("*.tmp"))
    assert leftover == []
    data = bb.read()
    assert data == {"a": 1, "b": 2}


def test_write_merges(tmp_root):
    bb = Blackboard(tmp_root)
    bb.write({"x": 1})
    bb.write({"y": 2})
    assert bb.read() == {"x": 1, "y": 2}


def test_concurrent_writes_serialize(tmp_root):
    bb = Blackboard(tmp_root)

    def worker(n):
        for i in range(20):
            bb.write({f"k{n}_{i}": i})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # File must remain valid JSON (no corruption from concurrent writes)
    data = bb.read()
    assert isinstance(data, dict)
    assert len(data) == 4 * 20


def test_get_blackboard_uses_checkpoint_dir(tmp_path):
    cfg = {"checkpoint": {"dir": str(tmp_path)}}
    bb = get_blackboard(cfg)
    r = DecisionRecord()
    bb.write_decision(r)
    assert (tmp_path / "blackboard.json").exists()


def test_get_blackboard_topic_slug_keys_file(tmp_path):
    """P2-9: different topics must use separate blackboard files."""
    cfg = {"checkpoint": {"dir": str(tmp_path)}}
    bb_a = get_blackboard(cfg, topic_slug="topic_a")
    bb_b = get_blackboard(cfg, topic_slug="topic_b")

    r_a = DecisionRecord()
    r_a.set("segment_count", 5, "user")
    bb_a.write_decision(r_a)

    r_b = DecisionRecord()
    r_b.set("segment_count", 10, "user")
    bb_b.write_decision(r_b)

    # Each topic reads its own value — no cross-topic leakage
    loaded_a = bb_a.read_decision()
    loaded_b = bb_b.read_decision()
    assert loaded_a.segment_count.value == 5
    assert loaded_b.segment_count.value == 10

    # Files are distinct on disk
    assert (tmp_path / "blackboard_topic_a.json").exists()
    assert (tmp_path / "blackboard_topic_b.json").exists()
    assert not (tmp_path / "blackboard.json").exists()


def test_clear(tmp_root):
    bb = Blackboard(tmp_root)
    bb.write({"a": 1})
    bb.clear()
    assert bb.read() == {}


def test_read_no_model_required(tmp_root):
    """Reading the blackboard must not require any ML model import/load."""
    bb = Blackboard(tmp_root)
    bb.write_decision(DecisionRecord())
    # If this needed torch/diffusers/ollama it would be slow or fail; it must not.
    rec = bb.read_decision()
    assert rec is not None
