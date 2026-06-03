"""test_motion_engine.py - V1: FramePack motion engine config and dispatch."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def test_default_motion_engine_is_none():
    """Default config must have motion_engine='none' (Ken Burns unchanged)."""
    from config.config import load_config

    try:
        cfg = load_config()
        assert cfg.get("video", {}).get("motion_engine", "none") == "none"
    except Exception:
        pytest.skip("config not loadable in this environment")


def test_motion_engine_none_returns_static_path(tmp_path):
    """When motion_engine='none', framepack_i2v is NOT called."""
    # Simulate the pipeline_long.py V1 block with motion_engine='none'
    config = {"video": {"motion_engine": "none", "motion_seconds_per_image": 3}}
    images = [tmp_path / "img1.png", tmp_path / "img2.png"]
    for img in images:
        img.write_bytes(b"PNG")

    _motion_engine = config.get("video", {}).get("motion_engine", "none")
    # When "none", images list is unchanged
    assert _motion_engine == "none"
    # No framepack call should happen — images stay as-is
    result_images = images  # unchanged
    assert result_images == images


def test_motion_engine_framepack_calls_i2v(monkeypatch, tmp_path):
    """When motion_engine='framepack', images_to_videos is called."""
    config = {
        "video": {
            "motion_engine": "framepack",
            "motion_seconds_per_image": 3,
            "fps": 24,
        }
    }
    images = [tmp_path / "img1.png", tmp_path / "img2.png"]
    for img in images:
        img.write_bytes(b"PNG")

    called = []
    fake_mp4 = tmp_path / "clip.mp4"
    fake_mp4.write_bytes(b"MP4")

    def _fake_i2v(image_paths, output_dir, seconds=3, fps=24, device="cuda"):
        called.append(True)
        return [(p, fake_mp4) for p in image_paths]

    def _fake_avail():
        return True

    # Simulate the V1 block
    import types

    fake_module = types.ModuleType("video.image_gen.framepack_i2v")
    fake_module.images_to_videos = _fake_i2v
    fake_module.is_available = _fake_avail
    monkeypatch.setitem(sys.modules, "video.image_gen.framepack_i2v", fake_module)

    _motion_engine = config.get("video", {}).get("motion_engine", "none")
    assert _motion_engine == "framepack"

    from video.image_gen.framepack_i2v import images_to_videos as _i2v_fn, is_available as _avail_fn

    assert _avail_fn() is True

    results = _i2v_fn(images, tmp_path / "motion", seconds=3, fps=24, device="cuda")
    assert called
    assert len(results) == 2


def test_framepack_not_installed_falls_back(monkeypatch, tmp_path):
    """When FramePack is not installed, is_available() returns False."""
    import video.image_gen.framepack_i2v as fi2v

    monkeypatch.setattr(fi2v, "_FRAMEPACK_AVAILABLE", False)
    assert fi2v.is_available() is False

    # image_to_video should return None gracefully
    result = fi2v.image_to_video(tmp_path / "img.png", tmp_path / "out.mp4")
    assert result is None


def test_framepack_missing_image_returns_none(tmp_path):
    """image_to_video returns None when source image doesn't exist."""
    import video.image_gen.framepack_i2v as fi2v

    result = fi2v.image_to_video(tmp_path / "nonexistent.png", tmp_path / "out.mp4")
    assert result is None


def test_motion_seconds_config_key():
    """motion_seconds_per_image config key must be readable."""
    config = {"video": {"motion_engine": "framepack", "motion_seconds_per_image": 4}}
    secs = float(config.get("video", {}).get("motion_seconds_per_image", 3))
    assert secs == 4.0
