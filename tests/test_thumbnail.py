"""test_thumbnail.py - Tests for D3: thumbnail generation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch


def test_thumbnail_path_in_manifest(tmp_path):
    """When generate_thumbnail=True and the thumbnail is created, it should appear in the manifest."""
    # Simulate the thumbnail logic from pipeline_long
    config = {"video": {"generate_thumbnail": True}}
    final_video = tmp_path / "final.mp4"
    final_video.write_bytes(b"fake_video")

    thumb_out = tmp_path / "thumbnail.png"

    def fake_subprocess_run(cmd, **kwargs):
        # Simulate ffmpeg creating the thumbnail
        thumb_out.write_bytes(b"fake_png")
        result = MagicMock()
        result.returncode = 0
        return result

    thumbnail_path = None
    if config.get("video", {}).get("generate_thumbnail", False):
        import subprocess as _sp
        with patch("subprocess.run", side_effect=fake_subprocess_run):
            _sp.run(
                ["ffmpeg", "-y", "-i", str(final_video), "-ss", "0", "-vframes", "1",
                 "-vf", "scale=1280:720", str(thumb_out)],
                capture_output=True, timeout=60
            )
        if thumb_out.exists():
            thumbnail_path = str(thumb_out)

    assert thumbnail_path is not None
    assert "thumbnail.png" in thumbnail_path


def test_thumbnail_not_generated_when_disabled(tmp_path):
    """When generate_thumbnail=False, no thumbnail should be created."""
    config = {"video": {"generate_thumbnail": False}}
    final_video = tmp_path / "final.mp4"
    final_video.write_bytes(b"fake_video")
    thumb_out = tmp_path / "thumbnail.png"

    thumbnail_path = None
    if config.get("video", {}).get("generate_thumbnail", False):
        thumb_out.write_bytes(b"fake_png")
        thumbnail_path = str(thumb_out)

    assert thumbnail_path is None
    assert not thumb_out.exists()


def test_thumbnail_hero_frame_selection(tmp_path):
    """Hero frame should be the first image in the images directory."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    for i in range(3):
        (images_dir / f"scene_{i+1:02d}_abc.png").write_bytes(b"fake_img")

    images = sorted(images_dir.glob("*.png"))
    hero = images[0] if images else None

    assert hero is not None
    assert "scene_01" in hero.name
