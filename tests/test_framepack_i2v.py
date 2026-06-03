"""test_framepack_i2v.py - Unit tests for video/image_gen/framepack_i2v.py"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video.image_gen import framepack_i2v


def test_is_available():
    with patch("video.image_gen.framepack_i2v._FRAMEPACK_AVAILABLE", True):
        assert framepack_i2v.is_available() is True
    with patch("video.image_gen.framepack_i2v._FRAMEPACK_AVAILABLE", False):
        assert framepack_i2v.is_available() is False


def test_image_to_video_not_available():
    with patch("video.image_gen.framepack_i2v._FRAMEPACK_AVAILABLE", False):
        result = framepack_i2v.image_to_video(Path("test.png"), Path("output.mp4"))
        assert result is None


def test_image_to_video_image_not_exists():
    with patch("video.image_gen.framepack_i2v._FRAMEPACK_AVAILABLE", True):
        result = framepack_i2v.image_to_video(Path("does_not_exist.png"), Path("output.mp4"))
        assert result is None


def test_image_to_video_success(tmp_path):
    img = tmp_path / "test.png"
    img.write_text("dummy")
    out = tmp_path / "output.mp4"

    mock_fp = MagicMock()

    # Write empty file to simulate generation success
    def fake_generate(image, output, duration, fps, device):
        Path(output).write_text("dummy mp4")
        return output

    mock_fp.generate.side_effect = fake_generate

    with (
        patch("video.image_gen.framepack_i2v._FRAMEPACK_AVAILABLE", True),
        patch("video.image_gen.framepack_i2v._fp_module", mock_fp, create=True),
    ):
        result = framepack_i2v.image_to_video(img, out)
        assert result == out
        mock_fp.generate.assert_called_once_with(
            image=str(img), output=str(out), duration=3.0, fps=24, device="cuda"
        )


def test_image_to_video_generate_returns_path_but_no_output_file(tmp_path):
    img = tmp_path / "test.png"
    img.write_text("dummy")
    out = tmp_path / "output.mp4"

    mock_fp = MagicMock()
    mock_fp.generate.return_value = str(out)  # returns path, but path doesn't actually exist

    with (
        patch("video.image_gen.framepack_i2v._FRAMEPACK_AVAILABLE", True),
        patch("video.image_gen.framepack_i2v._fp_module", mock_fp, create=True),
    ):
        result = framepack_i2v.image_to_video(img, out)
        assert result is None


def test_image_to_video_exception(tmp_path):
    img = tmp_path / "test.png"
    img.write_text("dummy")
    out = tmp_path / "output.mp4"

    mock_fp = MagicMock()
    mock_fp.generate.side_effect = Exception("failed")

    with (
        patch("video.image_gen.framepack_i2v._FRAMEPACK_AVAILABLE", True),
        patch("video.image_gen.framepack_i2v._fp_module", mock_fp, create=True),
    ):
        result = framepack_i2v.image_to_video(img, out)
        assert result is None


def test_images_to_videos(tmp_path):
    img1 = tmp_path / "img1.png"
    img1.write_text("1")
    img2 = tmp_path / "img2.png"
    img2.write_text("2")

    mock_fp = MagicMock()

    def fake_generate(image, output, duration, fps, device):
        Path(output).write_text("dummy mp4")
        return output

    mock_fp.generate.side_effect = fake_generate

    with (
        patch("video.image_gen.framepack_i2v._FRAMEPACK_AVAILABLE", True),
        patch("video.image_gen.framepack_i2v._fp_module", mock_fp, create=True),
    ):
        results = framepack_i2v.images_to_videos([img1, img2], tmp_path)
        assert len(results) == 2
        assert results[0][0] == img1
        assert results[0][1] == tmp_path / "img1_motion.mp4"
        assert results[1][0] == img2
        assert results[1][1] == tmp_path / "img2_motion.mp4"
