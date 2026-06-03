"""test_renderer.py — Unit tests for video/renderer/renderer.py."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video.renderer.renderer import build_html, render_html, render_with_assets


def test_build_html_defaults():
    # Verify building composition with simple inputs
    html = build_html(
        audio_path=Path("narration.wav"),
        image_paths=["img1.png", "img2.png"],
        script="यह एक कहानी है।",
        duration=10.0,
    )
    assert '<html lang="hi">' in html
    assert "assets/narration.wav" in html
    assert "assets/img1.png" in html
    assert "assets/img2.png" in html
    assert "यह एक कहानी है" in html
    assert 'data-duration="10.0"' in html


def test_build_html_with_word_timestamps(tmp_path):
    # Write dummy word timestamps JSON
    wts_data = [
        {"word": "यह", "start": 0.0, "end": 1.0},
        {"word": "एक", "start": 1.0, "end": 2.0},
        {"word": "कहानी", "start": 2.0, "end": 3.0},
        {"word": "है।", "start": 3.0, "end": 4.5},
    ]
    wts_file = tmp_path / "timestamps.json"
    wts_file.write_text(json.dumps(wts_data), encoding="utf-8")

    html = build_html(
        audio_path=Path("narration.wav"),
        image_paths=["img1.png"],
        script="यह एक कहानी है।",
        duration=5.0,
        word_timestamps_json=wts_file,
    )
    assert 'data-start="0.000"' in html
    # Length of words in "यह एक कहानी है।" is 4. It fits in one line (< 8 words).
    # Timing should map to first word start (0.0) and last word end (4.5).
    # Duration: t_end - t_start = 4.5
    assert 'data-duration="4.500"' in html
    assert "यह एक कहानी है" in html


def test_render_html_no_npx(monkeypatch):
    monkeypatch.setattr("video.renderer.renderer._NPX", "")
    with pytest.raises(RuntimeError, match="npx not found"):
        render_html(Path("index.html"), Path("output.mp4"))


def test_render_html_wsl_success(monkeypatch, tmp_path):
    monkeypatch.setattr("video.renderer.renderer._NPX", "/usr/bin/npx")

    mock_run = MagicMock()
    mock_run.returncode = 0

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("stdout", "stderr")
    mock_proc.returncode = 0

    # Create mock html file
    html_file = tmp_path / "index.html"
    html_file.write_text("content", encoding="utf-8")

    # Mock output file exists
    out_file = tmp_path / "output.mp4"
    out_file_exists = MagicMock(return_value=True)

    with (
        patch("subprocess.run", return_value=mock_run),
        patch("subprocess.Popen", return_value=mock_proc),
        patch("pathlib.Path.exists", out_file_exists),
    ):
        res = render_html(html_file, out_file, variables={"a": 1})

    assert res == out_file
    assert mock_proc.communicate.called


def test_render_html_wsl_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr("video.renderer.renderer._NPX", "/usr/bin/npx")

    mock_run = MagicMock()
    mock_run.returncode = 0

    mock_proc = MagicMock()
    mock_proc.communicate.side_effect = subprocess.TimeoutExpired(["wsl"], 10)
    mock_proc.pid = 1234

    html_file = tmp_path / "index.html"
    html_file.write_text("content", encoding="utf-8")

    with patch("subprocess.run") as mock_sub_run, patch("subprocess.Popen", return_value=mock_proc):
        mock_sub_run.return_value = mock_run
        with pytest.raises(RuntimeError, match="Hyperframes timed out"):
            render_html(html_file, tmp_path / "output.mp4")

    # Check taskkill is run on Windows or proc.kill on Unix
    if os.name == "nt":
        mock_sub_run.assert_any_call(["taskkill", "/F", "/T", "/PID", "1234"], capture_output=True)
    else:
        mock_proc.kill.assert_called_once()


def test_render_with_assets_assembler_fallback(tmp_path, monkeypatch):
    # Force opt-out of hyperframes
    monkeypatch.setenv("VIDEOAI_USE_HYPERFRAMES", "0")

    mock_create = MagicMock(return_value=tmp_path / "output.mp4")

    comp_dir = tmp_path / "compositions"
    out_path = tmp_path / "output.mp4"

    # Mock config loader
    with (
        patch("video.renderer.assembler.create_segment_mp4", mock_create),
        patch("config.load_config", return_value={}),
    ):
        res = render_with_assets(
            compositions_dir=comp_dir,
            output_path=out_path,
            audio_path=None,
            image_paths=[],
            script="test script",
        )

    assert res == out_path
    mock_create.assert_called_once()


def test_render_with_assets_hyperframes_fallback_to_assembler(tmp_path, monkeypatch):
    # Force opt-in of hyperframes
    monkeypatch.setenv("VIDEOAI_USE_HYPERFRAMES", "1")
    monkeypatch.setattr("video.renderer.renderer._NPX", "/usr/bin/npx")

    # Make hyperframes fail by raising exception inside render_html
    def mock_render_html(html_path, output_path, **kwargs):
        raise RuntimeError("Hyperframes render failed mock")

    comp_dir = tmp_path / "compositions"
    out_path = tmp_path / "segment_1.mp4"  # has segment number
    mock_create = MagicMock(return_value=out_path)

    with (
        patch("video.renderer.renderer.render_html", mock_render_html),
        patch("video.renderer.assembler.create_segment_mp4", mock_create),
        patch("config.load_config", return_value={}),
    ):
        res = render_with_assets(
            compositions_dir=comp_dir,
            output_path=out_path,
            audio_path=None,
            image_paths=[],
            script="test script",
        )

    assert res == out_path
    # Check fallback succeeded
    mock_create.assert_called_once()


# ── Extra renderer coverage tests ──


def test_render_html_wsl_user_and_quiet_false(monkeypatch, tmp_path):
    monkeypatch.setattr("video.renderer.renderer._NPX", "/usr/bin/npx")
    monkeypatch.setenv("VIDEOAI_WSL_USER", "operator")
    monkeypatch.setenv("VIDEOAI_WSL_DISTRO", "Alpine")

    mock_run = MagicMock()
    mock_run.returncode = 0

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("stdout", "stderr")
    mock_proc.returncode = 0

    html_file = tmp_path / "index.html"
    html_file.write_text("content", encoding="utf-8")

    out_file = tmp_path / "output.mp4"
    out_file_exists = MagicMock(return_value=True)

    with (
        patch("subprocess.run", return_value=mock_run),
        patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        patch("pathlib.Path.exists", out_file_exists),
    ):
        render_html(html_file, out_file, quiet=False, variables={"x": 2})

        args = mock_popen.call_args[0][0]
        assert "-u" in args
        assert "operator" in args
        assert "Alpine" in args
        # check hyperframes command in bash
        bash_cmd = args[-1]
        assert "--quiet" not in bash_cmd
        assert "variables" in bash_cmd


def test_render_html_wsl_distro_error(monkeypatch, tmp_path):
    monkeypatch.setattr("video.renderer.renderer._NPX", "/usr/bin/npx")

    mock_run = MagicMock()
    mock_run.returncode = 1  # WSL not running or distro missing

    with patch("subprocess.run", return_value=mock_run):
        with pytest.raises(RuntimeError, match="WSL not available"):
            render_html(tmp_path / "index.html", tmp_path / "out.mp4")


def test_render_html_wsl_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr("video.renderer.renderer._NPX", "/usr/bin/npx")

    with patch("subprocess.run", side_effect=FileNotFoundError("wsl.exe missing")):
        with pytest.raises(RuntimeError, match="WSL unavailable"):
            render_html(tmp_path / "index.html", tmp_path / "out.mp4")


def test_render_html_popen_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("video.renderer.renderer._NPX", "/usr/bin/npx")

    mock_run = MagicMock()
    mock_run.returncode = 0

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("stdout", "rendering error detail")
    mock_proc.returncode = 5

    with (
        patch("subprocess.run", return_value=mock_run),
        patch("subprocess.Popen", return_value=mock_proc),
    ):
        with pytest.raises(RuntimeError, match="Hyperframes failed: rendering error detail"):
            render_html(tmp_path / "index.html", tmp_path / "out.mp4")


def test_render_html_output_not_created(monkeypatch, tmp_path):
    monkeypatch.setattr("video.renderer.renderer._NPX", "/usr/bin/npx")

    mock_run = MagicMock()
    mock_run.returncode = 0

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("stdout", "stderr")
    mock_proc.returncode = 0

    # We patch exists to return False
    with (
        patch("subprocess.run", return_value=mock_run),
        patch("subprocess.Popen", return_value=mock_proc),
        patch("pathlib.Path.exists", return_value=False),
    ):
        with pytest.raises(RuntimeError, match="Output not created"):
            render_html(tmp_path / "index.html", tmp_path / "out.mp4")


def test_build_html_long_sentences():
    # Sentences with >8 words split into blocks of 8
    html = build_html(
        audio_path=None,
        image_paths=[],
        script="one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen.",
        duration=10.0,
    )
    # verify it was split into 2 caption elements
    assert "one two three four five six seven eight" in html
    assert "nine ten eleven twelve thirteen fourteen fifteen sixteen" in html


def test_build_html_empty_script():
    # Empty script
    html = build_html(audio_path=None, image_paths=[], script="", duration=5.0)
    assert '<div id="cap-0"' not in html


def test_build_html_corrupt_timestamps_json(tmp_path):
    # Corrupt JSON file
    corrupt_file = tmp_path / "corrupt_wts.json"
    corrupt_file.write_text("invalid json", encoding="utf-8")

    html = build_html(
        audio_path=None,
        image_paths=[],
        script="यह एक परीक्षण है।",
        duration=5.0,
        word_timestamps_json=corrupt_file,
    )
    # Falls back to equal duration (duration / len(lines))
    assert html is not None
    # Caption line duration should be calculated as 5.0
    assert 'data-duration="5.000"' in html


def test_render_with_assets_copies_files(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOAI_USE_HYPERFRAMES", "0")

    comp_dir = tmp_path / "compositions"
    out_path = tmp_path / "output.mp4"

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")

    img = tmp_path / "image.png"
    img.write_bytes(b"image")

    mock_create = MagicMock(return_value=out_path)

    with (
        patch("video.renderer.assembler.create_segment_mp4", mock_create),
        patch("config.load_config", return_value={}),
    ):
        render_with_assets(
            compositions_dir=comp_dir,
            output_path=out_path,
            audio_path=audio,
            image_paths=[img],
            script="test script",
        )

    # Verify copies were created in assets directory
    assert (comp_dir / "assets" / "audio.wav").exists()
    assert (comp_dir / "assets" / "image.png").exists()


def test_render_with_assets_html_content_direct(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOAI_USE_HYPERFRAMES", "1")
    monkeypatch.setattr("video.renderer.renderer._NPX", "/usr/bin/npx")

    comp_dir = tmp_path / "compositions"
    out_path = tmp_path / "output.mp4"

    mock_render_html = MagicMock(return_value=out_path)

    with patch("video.renderer.renderer.render_html", mock_render_html):
        render_with_assets(
            compositions_dir=comp_dir,
            output_path=out_path,
            audio_path=None,
            image_paths=[],
            script="test script",
            html_content="<custom_html_here>",
        )

    assert (comp_dir / "index.html").read_text(encoding="utf-8") == "<custom_html_here>"
    mock_render_html.assert_called_once()


def test_render_with_assets_degradation_failure_path(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOAI_USE_HYPERFRAMES", "1")
    monkeypatch.setattr("video.renderer.renderer._NPX", "/usr/bin/npx")

    comp_dir = tmp_path / "compositions"
    out_path = tmp_path / "segment_3.mp4"

    # Force hyperframes rendering to raise error
    with (
        patch("video.renderer.renderer.render_html", side_effect=RuntimeError("Hyperframes broke")),
        patch("video.renderer.assembler.create_segment_mp4", return_value=out_path),
        patch("config.load_config", return_value={}),
        patch(
            "agents.director_agent.UIState.add_degradation",
            side_effect=Exception("Failed to log degradation"),
        ),
    ):
        # Should catch add_degradation failure and still complete fallback
        res = render_with_assets(
            compositions_dir=comp_dir,
            output_path=out_path,
            audio_path=None,
            image_paths=[],
            script="test script",
        )
        assert res == out_path
