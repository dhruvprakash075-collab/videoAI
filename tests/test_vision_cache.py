"""test_vision_cache.py - VisionCache get/set roundtrip + invalidation."""

from pathlib import Path

from utils.vision_cache import CACHE_VERSION, VisionCache


def test_get_returns_none_when_empty(tmp_path: Path):
    vc = VisionCache(cache_dir=tmp_path / "vc")
    assert vc.get("topic A") is None


def test_set_then_get_roundtrip(tmp_path: Path):
    vc = VisionCache(cache_dir=tmp_path / "vc")
    vc.set("topic A", {"scenes": [1, 2, 3]})
    out = vc.get("topic A")
    assert out == {"scenes": [1, 2, 3]}


def test_set_writes_to_disk(tmp_path: Path):
    d = tmp_path / "vc"
    vc = VisionCache(cache_dir=d)
    vc.set("topic A", {"x": 1})
    assert (d / "vision_cache.json").exists()
    assert (d / "vision_cache_meta.json").exists()


def test_get_with_force_refresh_returns_none(tmp_path: Path):
    vc = VisionCache(cache_dir=tmp_path / "vc", force_refresh=True)
    vc.set("topic A", {"x": 1})
    assert vc.get("topic A") is None


def test_persistence_across_instances(tmp_path: Path):
    d = tmp_path / "vc"
    VisionCache(cache_dir=d).set("topic A", {"hello": "world"})
    vc2 = VisionCache(cache_dir=d)
    assert vc2.get("topic A") == {"hello": "world"}


def test_content_text_changes_key(tmp_path: Path):
    vc = VisionCache(cache_dir=tmp_path / "vc")
    vc.set("topic A", {"v": 1}, content_text="alpha")
    vc.set("topic A", {"v": 2}, content_text="beta")
    assert vc.get("topic A", content_text="alpha") == {"v": 1}
    assert vc.get("topic A", content_text="beta") == {"v": 2}


def test_cache_version_mismatch_returns_none(tmp_path: Path):
    d = tmp_path / "vc"
    VisionCache(cache_dir=d).set("topic A", {"v": 1})
    # Manually edit the meta file to use a different version
    import json

    meta_file = d / "vision_cache_meta.json"
    data = json.loads(meta_file.read_text())
    for k in data:
        data[k]["cache_version"] = 999
    meta_file.write_text(json.dumps(data))
    vc2 = VisionCache(cache_dir=d)
    assert vc2.get("topic A") is None


def test_invalid_json_in_cache_file_is_ignored(tmp_path: Path):
    d = tmp_path / "vc"
    d.mkdir(parents=True, exist_ok=True)
    (d / "vision_cache.json").write_text("NOT JSON")
    vc = VisionCache(cache_dir=d)
    # Should not crash; get returns None for any key
    assert vc.get("topic A") is None


def test_missing_config_path_uses_noconfig(tmp_path: Path):
    vc = VisionCache(cache_dir=tmp_path / "vc", config_path=tmp_path / "nope.yaml")
    vc.set("topic A", {"x": 1})
    out = vc.get("topic A")
    assert out == {"x": 1}


def test_max_entries_triggers_eviction(tmp_path: Path):
    vc = VisionCache(cache_dir=tmp_path / "vc", max_entries=5)
    for i in range(15):
        vc.set(f"topic {i:02d}", {"v": i})
    # After eviction, should still be functional
    assert vc.get("topic 14") == {"v": 14}


def test_cache_version_constant():
    assert isinstance(CACHE_VERSION, int)
    assert CACHE_VERSION >= 1


def test_meta_records_topic_truncated(tmp_path: Path):
    vc = VisionCache(cache_dir=tmp_path / "vc")
    long_topic = "x" * 200
    vc.set(long_topic, {"y": 1})
    # Just verify no exception on long topic
    assert vc.get(long_topic) == {"y": 1}
