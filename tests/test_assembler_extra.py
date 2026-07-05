"""test_assembler_extra.py - Extensive unit tests targeting all remaining paths in video/renderer/assembler.py"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure parent directory is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video.renderer import assembler

# Setup dummy logger
log = logging.getLogger("video.renderer.assembler")


@pytest.fixture(autouse=True)
def reset_whisper_cache():
    """Reset global whisper caching variables between tests to avoid bleed."""
    assembler._whisper_models.clear()
    assembler._cached_codec = None
    assembler._encoder_support_cache.clear()


def test_ts_boundaries():
    """Test boundary and error conditions for timestamp helper _ts."""
    # Test valid floats
    assert assembler._ts(0.0) == "00:00:00,000"
    assert assembler._ts(125.45) == "00:02:05,450"

    # Test invalid values (should log warning and return default)
    assert assembler._ts("invalid") == "00:00:00,000"
    assert assembler._ts(-5.0) == "00:00:00,000"
    assert assembler._ts(float("nan")) == "00:00:00,000"
    assert assembler._ts(float("inf")) == "00:00:00,000"

    # Test millisecond rounding boundary (ms >= 1000)
    # 0.9999 seconds is 999.9 milliseconds, which rounds to 1000.
    # The code caps it at 999.
    assert assembler._ts(0.9999) == "00:00:00,999"


def test_get_whisper_model_final_cpu():
    """Test _get_whisper_model for final render using config values."""
    mock_config = {
        "performance": {
            "whisper_model_final": "medium",
        }
    }

    with (
        patch("config.load_config", return_value=mock_config),
        patch("faster_whisper.WhisperModel") as mock_faster,
    ):
        # Test final run path (should use CPU and int8 to save VRAM)
        model = assembler._get_whisper_model(is_final=True)
        assert model is not None
        mock_faster.assert_called_once_with("medium", device="cpu", compute_type="int8")


def test_get_whisper_model_preview_gpu():
    mock_config = {
        "performance": {
            "whisper_model": "tiny",
        }
    }

    with (
        patch("config.load_config", return_value=mock_config),
        patch("torch.cuda.is_available", return_value=True),
        patch("faster_whisper.WhisperModel") as mock_faster,
    ):
        # Test non-final preview path
        model = assembler._get_whisper_model(is_final=False)
        assert model is not None
        mock_faster.assert_called_once_with("tiny", device="cuda", compute_type="float16")


def test_get_whisper_model_returns_none_when_faster_whisper_fails():
    """Test _get_whisper_model returns None when faster-whisper fails."""
    mock_config = {}

    with (
        patch("config.load_config", return_value=mock_config),
        patch("faster_whisper.WhisperModel", side_effect=ImportError("faster-whisper missing")),
    ):
        model = assembler._get_whisper_model(is_final=False)
        assert model is None


def test_get_video_codec_win32_nvenc():
    """Test video codec selection on Win32 platform when nvenc is present."""
    # Mock platform as win32
    with patch("sys.platform", "win32"), patch("subprocess.run") as mock_run:
        # Setup mock stdout with h264_nvenc listed
        mock_res = MagicMock()
        mock_res.stdout = (
            "encoders:\n V..... h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)"
        )
        mock_run.return_value = mock_res

        codec = assembler._get_video_codec()
        assert "h264_nvenc" in codec
        mock_run.assert_called_once_with(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=5
        )


def test_get_video_codec_fallback_to_libx264():
    """Test video codec fallback to libx264 when nvenc check fails or is missing."""
    # Case 1: nvenc missing in ffmpeg encoders
    with patch.object(assembler, "_encoder_support_cache", {"h264_nvenc": False}):
        assembler._cached_codec = None
        codec = assembler._get_video_codec()
        assert "libx264" in codec
        assert "h264_nvenc" not in codec

    # Reset cache
    assembler._cached_codec = None

    # Case 2: ffmpeg subprocess exception (probe fails)
    with (
        patch.object(assembler, "_encoder_support_cache", {}),
        patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")),
    ):
        codec = assembler._get_video_codec()
        assert "libx264" in codec


def test_encoder_args():
    """Test _encoder_args constructs correct ffmpeg args based on config."""
    # Case 1: h264_nvenc selected with encoder extra args
    config = {
        "video": {
            "encoder": "h264_nvenc",
            "encoder_preset": "p6",
            "video_bitrate": "12M",
            "encoder_extra": "-spatial-aq 1 -temporal-aq 1",
        }
    }
    with patch("video.renderer.assembler._ffmpeg_supports_encoder", return_value=True):
        args = assembler._encoder_args(config)
    assert "-c:v" in args
    assert "h264_nvenc" in args
    assert "-preset" in args
    assert "p6" in args
    assert "-b:v" in args
    assert "12M" in args
    assert "-spatial-aq" in args
    assert "-temporal-aq" in args

    # Case 2: default/other encoder (should use _get_video_codec)
    config_other = {"video": {"encoder": "libx264"}}
    with patch(
        "video.renderer.assembler._get_video_codec", return_value=["-c:v", "libx264"]
    ) as mock_get_codec:
        args_other = assembler._encoder_args(config_other)
        assert args_other == ["-c:v", "libx264"]
        mock_get_codec.assert_called_once()


def test_run_helper_error_handling():
    """Test error handling in _run subprocess wrapper."""
    # Case 1: TimeoutExpired
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["ffmpeg"], 10)):
        with pytest.raises(RuntimeError) as exc_info:
            assembler._run(["ffmpeg"], timeout=10)
        assert "ffmpeg timeout" in str(exc_info.value)

    # Case 2: Return code non-zero with critical error
    mock_res = MagicMock()
    mock_res.returncode = 1
    mock_res.stderr = b"Error: failed to open codec"
    with patch("subprocess.run", return_value=mock_res):
        with pytest.raises(RuntimeError) as exc_info:
            assembler._run(["ffmpeg"])
        assert "ffmpeg error" in str(exc_info.value)

    # Case 3: Return code non-zero but contains only non-fatal deprecation warnings
    mock_res_warn = MagicMock()
    mock_res_warn.returncode = (
        0  # wait, if returncode is 0 it won't raise anyway. Let's test returncode != 0
    )
    mock_res_warn.returncode = 1
    mock_res_warn.stderr = b"ffmpeg version 5.0 deprecated option used here"
    with patch("subprocess.run", return_value=mock_res_warn):
        # Should not raise RuntimeError since it only contains 'deprecated' and no critical indicators
        # Wait! The logic is:
        # is_critical = any(ind in stderr.lower() for ind in critical_indicators)
        # if is_critical or 'deprecated' not in stderr.lower():
        #     raise RuntimeError(...)
        # So if 'deprecated' IS in stderr.lower() AND no critical indicators are found, it logs warning and doesn't raise!
        # Let's verify this behavior:
        assembler._run(["ffmpeg"])  # Should complete without error!


def test_write_srt_word_timestamps_json(tmp_path):
    """Test _write_srt when word_timestamps_json is provided and valid."""
    srt_path = tmp_path / "test.srt"
    words_json_path = tmp_path / "words.json"

    words_data = [
        {"word": "Test", "start": 0.0, "end": 1.0},
        {"word": "srt", "start": 1.0, "end": 2.0},
    ]
    words_json_path.write_text(json.dumps(words_data), encoding="utf-8")

    # Classic style
    assembler._write_srt(
        script="Test srt",
        path=srt_path,
        duration=2.0,
        word_timestamps_json=words_json_path,
        format_style="classic",
    )

    assert srt_path.exists()
    content = srt_path.read_text(encoding="utf-8-sig")
    assert "00:00:00,000 --> 00:00:02,000" in content
    assert "Test srt" in content


def test_write_srt_english_uses_caption_script_not_hindi_timestamp_words(tmp_path):
    timestamps = tmp_path / "words.json"
    timestamps.write_text(
        json.dumps([{"word": "हिंदी", "start": 0.0, "end": 1.0}]), encoding="utf-8"
    )
    output = tmp_path / "english.srt"

    assembler._write_srt(
        "# Story Title\nThe lantern keeper entered the ancient ruins with a bright blue flame.",
        output,
        4.0,
        word_timestamps_json=timestamps,
        subtitle_language="en",
    )

    content = output.read_text(encoding="utf-8-sig")
    assert "The lantern keeper" in content
    assert "हिंदी" not in content
    assert "# Story Title" not in content
    blocks = [block.splitlines() for block in content.strip().split("\n\n")]
    assert max(len(" ".join(block[2:]).split()) for block in blocks) <= 7


def test_write_srt_whisper_fallback(tmp_path):
    """Test _write_srt when json is missing but audio exists, falling back to Whisper."""
    srt_path = tmp_path / "test.srt"
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"dummy")

    # Mock Whisper model
    mock_model = MagicMock()
    # Mock faster-whisper behavior
    mock_word = MagicMock()
    mock_word.word = "hello"
    mock_word.start = 0.1
    mock_word.end = 0.9

    mock_segment = MagicMock()
    mock_segment.words = [mock_word]

    mock_model.transcribe.return_value = ([mock_segment], None)

    with patch("video.renderer.assembler._get_whisper_model", return_value=mock_model):
        # Test default transcribe call (no translation)
        assembler._write_srt(
            script="hello", path=srt_path, duration=1.0, audio=audio_path, subtitle_language="auto"
        )

        mock_model.transcribe.assert_called_once_with(
            str(audio_path), beam_size=1, word_timestamps=True, vad_filter=True
        )
        assert srt_path.exists()
        content = srt_path.read_text(encoding="utf-8-sig")
        assert "hello" in content.lower()

    # Reset
    srt_path.unlink(missing_ok=True)

    mock_translation_model = MagicMock()
    mock_word.word = "world"
    mock_word.start = 0.2
    mock_word.end = 1.2
    mock_translation_model.transcribe.return_value = ([mock_segment], None)

    with patch("video.renderer.assembler._get_whisper_model", return_value=mock_translation_model):
        assembler._write_srt(
            script="world",
            path=srt_path,
            duration=1.5,
            audio=audio_path,
            subtitle_language="es",  # triggers translation
        )

        mock_translation_model.transcribe.assert_called_once_with(
            str(audio_path),
            beam_size=1,
            word_timestamps=True,
            vad_filter=True,
            task="translate",
            language="es",
        )
        assert srt_path.exists()
        content = srt_path.read_text(encoding="utf-8-sig")
        assert "world" in content.lower()


def test_write_srt_proportional_fallback(tmp_path):
    """Test _write_srt fallback to proportional timing logic."""
    srt_path = tmp_path / "test.srt"

    # Call with no audio and no json
    assembler._write_srt(
        script="यह एक परीक्षण वाक्य है। और यह दूसरा है।",
        path=srt_path,
        duration=10.0,
        format_style="classic",
    )

    assert srt_path.exists()
    content = srt_path.read_text(encoding="utf-8-sig")
    # Verify Devanagari full stop / danda (।) replacement
    assert "परीक्षण वाक्य है" in content
    assert "दूसरा है" in content


def test_create_segment_mp4_kb_modes(tmp_path):
    """Test create_segment_mp4 under different Ken Burns modes."""
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"dummy")

    images = [tmp_path / "img1.png", tmp_path / "img2.png"]
    for img in images:
        img.write_bytes(b"dummy")

    words_json = tmp_path / "words.json"
    words_json.write_text("[]", encoding="utf-8")

    # Define base configurations
    base_config = {
        "video": {
            "resolution": "1280x720",
            "fps": 30,
            "crossfade_duration": 0.5,
            "audio_crossfade_ms": 0,
        },
        "subtitles": {
            "format": "tiktok",
            "font": "Arial",
            "size": 30,
            "color": "&HFFFFFF&",
        },
        "tts": {"lang": "hi"},
        "performance": {"ffmpeg_threads": 2},
    }

    # Case 1: kb_mode == "full" (zoompan)
    config_full = dict(base_config)
    config_full["video"]["ken_burns"] = "full"

    run_cmds = []

    def fake_run(cmd, timeout=300):
        run_cmds.append(cmd)
        (tmp_path / "segment_02.mp4").write_bytes(b"fake_mp4")

    with (
        patch("video.renderer.assembler._run", side_effect=fake_run),
        patch("video.renderer.assembler.get_audio_duration", return_value=6.0),
    ):
        assembler.create_segment_mp4(
            seg_num=2,
            audio=audio,
            script="Hello world",
            out_dir=tmp_path,
            config=config_full,
            images=images,
            word_timestamps_json=words_json,
        )

    assert len(run_cmds) == 1
    cmd_str = " ".join(str(x) for x in run_cmds[0])
    assert "zoompan" in cmd_str
    # Verify filter threads argument passed
    assert "-filter_threads 2" in cmd_str

    # Reset
    run_cmds.clear()

    # Case 2: kb_mode == "off" (static scaling)
    config_off = dict(base_config)
    config_off["video"]["ken_burns"] = "off"

    with (
        patch("video.renderer.assembler._run", side_effect=fake_run),
        patch("video.renderer.assembler.get_audio_duration", return_value=6.0),
    ):
        assembler.create_segment_mp4(
            seg_num=2,
            audio=audio,
            script="Hello world",
            out_dir=tmp_path,
            config=config_off,
            images=images,
            word_timestamps_json=words_json,
        )
    assert len(run_cmds) == 1
    cmd_str_off = " ".join(str(x) for x in run_cmds[0])
    assert "force_original_aspect_ratio" in cmd_str_off
    assert "zoompan" not in cmd_str_off


def test_concatenate_segments_loudnorm(tmp_path):
    """Test concatenate_segments when EBU R128 loudnorm is enabled."""
    seg1 = tmp_path / "seg1.mp4"
    seg1.write_bytes(b"seg1")
    seg2 = tmp_path / "seg2.mp4"
    seg2.write_bytes(b"seg2")

    out_mp4 = tmp_path / "final.mp4"

    config = {"audio_fx": {"program_loudnorm": True, "target_lufs": -16.0}}

    run_calls = []

    def fake_run(cmd, timeout=600):
        run_calls.append(cmd)

        # When running the second pass or fallback, write the output file
        # We find the output path in cmd list
        out_path = Path(cmd[-1])
        out_path.write_bytes(b"rendered_output")

    # Mock subprocess.run for loudnorm measurement pass
    mock_measurement = MagicMock()
    mock_measurement.returncode = 0
    # Simulate FFmpeg JSON stderr output for R128 measurement
    mock_measurement.stderr = """
[Parsed_loudnorm_0 @ 0000021c172dcc80]
{
	"input_i" : "-20.5",
	"input_tp" : "-1.1",
	"input_lra" : "8.2",
	"input_thresh" : "-31.0",
	"output_i" : "-16.1",
	"output_tp" : "-1.5",
	"output_lra" : "6.5",
	"output_thresh" : "-26.5",
	"normalization_type" : "dynamic",
	"target_offset" : "0.1"
}
    """

    with (
        patch("subprocess.run", return_value=mock_measurement),
        patch("video.renderer.assembler._run", side_effect=fake_run),
    ):
        res_path = assembler.concatenate_segments(
            segments=[seg1, seg2], output=out_mp4, config=config
        )

        assert res_path == out_mp4
        assert out_mp4.exists()

        # Check first pass command was constructed with loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json
        measurement_args = subprocess.run.call_args[0][0]
        assert "loudnorm=I=-16.0:TP=-1.5:LRA=11:print_format=json" in measurement_args

        # Check second pass applied the measured properties
        second_pass_cmd = " ".join(run_calls[1])
        assert "measured_I=-20.5" in second_pass_cmd
        assert "measured_TP=-1.1" in second_pass_cmd
        assert "measured_LRA=8.2" in second_pass_cmd
        assert "measured_thresh=-31.0" in second_pass_cmd
        assert "offset=0.1" in second_pass_cmd


def test_concatenate_segments_loudnorm_failure_fallback(tmp_path):
    """Test concatenate_segments fallback to copying file on loudnorm measurement failure."""
    seg1 = tmp_path / "seg1.mp4"
    seg1.write_bytes(b"seg1")

    out_mp4 = tmp_path / "final.mp4"

    config = {"audio_fx": {"program_loudnorm": True, "target_lufs": -16.0}}

    # Simulate subprocess.run raising timeout or failing to parse json
    with (
        patch("subprocess.run", side_effect=subprocess.SubprocessError("FFmpeg command failed")),
        patch("video.renderer.assembler._run") as mock_run,
    ):
        # Setup run mock to simulate the first concat (before loudnorm)
        def fake_run(cmd, timeout=600):
            Path(cmd[-1]).write_bytes(b"prenorm_output")

        mock_run.side_effect = fake_run

        res_path = assembler.concatenate_segments(segments=[seg1], output=out_mp4, config=config)

        assert res_path == out_mp4
        # Verify it fallback-copied the prenorm output to output path
        assert out_mp4.read_bytes() == b"prenorm_output"


def test_concatenate_segments_ducking_music(tmp_path):
    """Test concatenate_segments mixes music with sidechain ducking filter when music ducking enabled."""
    seg = tmp_path / "seg.mp4"
    seg.write_bytes(b"seg")

    music = tmp_path / "music.wav"
    music.write_bytes(b"music")

    out_mp4 = tmp_path / "final.mp4"

    config = {"music": {"ducking": True, "duck_ratio": 0.5}}

    run_cmds = []

    def fake_run(cmd, timeout=900):
        run_cmds.append(cmd)
        out_mp4.write_bytes(b"ducked_out")

    with patch("video.renderer.assembler._run", side_effect=fake_run):
        assembler.concatenate_segments(segments=[seg], output=out_mp4, music=music, config=config)

    assert len(run_cmds) == 1
    cmd_str = " ".join(str(x) for x in run_cmds[0])

    # Verify sidechaincompress filter details
    assert "sidechaincompress" in cmd_str
    # Duck ratio of 0.5 maps to compressor ratio 6.0:1 (1.0 + 0.5 * 10 = 6.0)
    assert "ratio=6.0" in cmd_str
    assert "threshold=0.05" in cmd_str


def test_concatenate_segments_loudnorm_unparseable_json(tmp_path):
    """Test loudnorm measurement JSON parsing failure triggers single-pass fallback."""
    seg1 = tmp_path / "seg1.mp4"
    seg1.write_bytes(b"seg1")

    out_mp4 = tmp_path / "final.mp4"
    config = {"audio_fx": {"program_loudnorm": True, "target_lufs": -16.0}}

    run_calls = []

    def fake_run(cmd, timeout=600):
        run_calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"fallback_rendered")

    mock_measurement = MagicMock()
    mock_measurement.returncode = 0
    mock_measurement.stderr = "unparseable garbage that does not contain JSON braces"

    with (
        patch("subprocess.run", return_value=mock_measurement),
        patch("video.renderer.assembler._run", side_effect=fake_run),
    ):
        res_path = assembler.concatenate_segments(segments=[seg1], output=out_mp4, config=config)
        assert res_path == out_mp4
        assert out_mp4.exists()

        # The second run call should be the single-pass fallback
        assert len(run_calls) >= 2
        single_pass_cmd = " ".join(run_calls[1])
        assert "loudnorm=I=-16.0:TP=-1.5:LRA=11:linear=true" in single_pass_cmd


def test_assembler_s_to_ts_overflow():
    # float('inf') triggers OverflowError in int(s)
    res = assembler._ts(float("inf"))
    assert res == "00:00:00,000"


def test_assembler_run_critical_error():
    mock_res = MagicMock()
    mock_res.returncode = 1
    mock_res.stderr = b"ffmpeg critical error occurred"

    with patch("subprocess.run", return_value=mock_res):
        with pytest.raises(RuntimeError, match="ffmpeg error:"):
            assembler._run(["ffmpeg", "-i", "dummy.mp4"])


def test_assembler_run_deprecation_warning():
    mock_res = MagicMock()
    mock_res.returncode = 1
    mock_res.stderr = b"deprecated warning option used"

    # With check=False, if it is only deprecation, it logs and does not raise
    with patch("subprocess.run", return_value=mock_res):
        assembler._run(["ffmpeg", "-i", "dummy.mp4"])


def test_assembler_run_timeout():
    # Simulate TimeoutExpired
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=10)):
        with pytest.raises(RuntimeError, match="ffmpeg timeout"):
            assembler._run(["ffmpeg"])


def test_assembler_run_general_exception():
    # Simulate general Exception
    with patch("subprocess.run", side_effect=OSError("file not found")):
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            assembler._run(["ffmpeg"])


def test_assembler_get_video_codec_nvidia_h264_nvenc(monkeypatch):
    # Reset cached codec to force execution of the platform detection
    monkeypatch.setattr(assembler, "_cached_codec", None)

    with patch.object(assembler, "_encoder_support_cache", {"h264_nvenc": True}):
        codec = assembler._get_video_codec()
        assert "h264_nvenc" in codec


def test_assembler_get_video_codec_nvenc_missing(monkeypatch):
    monkeypatch.setattr(assembler, "_cached_codec", None)

    with patch.object(assembler, "_encoder_support_cache", {"h264_nvenc": False}):
        codec = assembler._get_video_codec()
        assert "libx264" in codec


def test_assembler_get_video_codec_exception(monkeypatch):
    monkeypatch.setattr(assembler, "_cached_codec", None)

    with patch("subprocess.run", side_effect=Exception("command failed")):
        assembler._encoder_support_cache.clear()
        codec = assembler._get_video_codec()
        assert "libx264" in codec


def test_assembler_get_video_codec_not_windows(monkeypatch):
    monkeypatch.setattr(assembler, "_cached_codec", None)

    with patch.object(assembler, "_encoder_support_cache", {"h264_nvenc": False}):
        codec = assembler._get_video_codec()
        assert "libx264" in codec


def test_assembler_get_whisper_model_exception_fallback():
    """Test exception fallback in _get_whisper_model when load_config fails."""
    with (
        patch("config.load_config", side_effect=Exception("Load failed")),
        patch("faster_whisper.WhisperModel") as mock_faster,
    ):
        model = assembler._get_whisper_model(is_final=False)
        assert model is not None
        mock_faster.assert_called_once_with("tiny", device="cpu", compute_type="int8")


def test_assembler_get_video_codec_cached(monkeypatch):
    """Test that _get_video_codec returns the cached codec immediately."""
    monkeypatch.setattr(assembler, "_cached_codec", ["-c:v", "cached_encoder"])
    assert assembler._get_video_codec() == ["-c:v", "cached_encoder"]


def test_create_segment_mp4_no_images(tmp_path):
    """Test create_segment_mp4 with empty images list."""
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"dummy")

    config = {
        "video": {"resolution": "1280x720", "fps": 30},
        "subtitles": {"format": "classic"},
        "tts": {"lang": "hi"},
    }

    run_cmds = []

    def fake_run(cmd, timeout=300):
        run_cmds.append(cmd)
        (tmp_path / "segment_03.mp4").write_bytes(b"fake")

    with (
        patch("video.renderer.assembler._run", side_effect=fake_run),
        patch("video.renderer.assembler.get_audio_duration", return_value=5.0),
    ):
        res = assembler.create_segment_mp4(
            seg_num=3,
            audio=audio,
            script="No images here.",
            out_dir=tmp_path,
            config=config,
            images=[],
        )
    assert res.exists()
    assert any("color=c=black:s=1280x720" in str(x) for x in run_cmds[0])


def test_create_segment_mp4_cleanup_manifest_exception(tmp_path):
    """Test manifest write exception handling in create_segment_mp4."""
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"dummy")

    config = {
        "video": {"resolution": "1280x720", "fps": 30},
        "subtitles": {"format": "classic"},
        "tts": {"lang": "hi"},
    }

    def fake_run(cmd, timeout=300):
        (tmp_path / "segment_04.mp4").write_bytes(b"fake")

    original_write_text = Path.write_text

    def mock_write_text(self, *args, **kwargs):
        if "cleanup_manifest.json" in str(self):
            raise Exception("Write failed")
        return original_write_text(self, *args, **kwargs)

    with (
        patch("video.renderer.assembler._run", side_effect=fake_run),
        patch("video.renderer.assembler.get_audio_duration", return_value=5.0),
        # Cause write_text on manifest to raise an exception
        patch("pathlib.Path.write_text", mock_write_text),
    ):
        res = assembler.create_segment_mp4(
            seg_num=4,
            audio=audio,
            script="Hello",
            out_dir=tmp_path,
            config=config,
            images=[],
        )
    assert res.exists()


def test_create_segment_mp4_unlink_exception(tmp_path):
    """Test exception handling during temp srt unlink in create_segment_mp4."""
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"dummy")

    config = {
        "video": {"resolution": "1280x720", "fps": 30},
        "subtitles": {"format": "classic"},
        "tts": {"lang": "hi"},
    }

    def fake_run(cmd, timeout=300):
        (tmp_path / "segment_05.mp4").write_bytes(b"fake")

    with (
        patch("video.renderer.assembler._run", side_effect=fake_run),
        patch("video.renderer.assembler.get_audio_duration", return_value=5.0),
        patch("pathlib.Path.unlink", side_effect=Exception("Unlink failed")),
    ):
        res = assembler.create_segment_mp4(
            seg_num=5,
            audio=audio,
            script="Hello",
            out_dir=tmp_path,
            config=config,
            images=[],
        )
    assert res.exists()


def test_concatenate_segments_empty_list():
    """Test that empty segments list raises ValueError."""
    with pytest.raises(ValueError, match="No segments to concatenate"):
        assembler.concatenate_segments([], Path("out.mp4"))


def test_write_srt_json_exception_fallback(tmp_path):
    """Test json loading error fallback inside _write_srt."""
    srt_path = tmp_path / "test.srt"
    words_json_path = tmp_path / "words.json"
    words_json_path.write_text("invalid json format", encoding="utf-8")

    # Should log warning, fall back to proportional, and write srt successfully
    assembler._write_srt(
        script="Proportional fallback text.",
        path=srt_path,
        duration=5.0,
        word_timestamps_json=words_json_path,
        format_style="classic",
    )
    assert srt_path.exists()
    assert "Proportional fallback text" in srt_path.read_text(encoding="utf-8-sig")


def test_write_srt_faster_whisper_no_lang(tmp_path):
    """Test _write_srt using faster-whisper with no language override."""
    srt_path = tmp_path / "test.srt"
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"dummy")

    mock_model = MagicMock()
    mock_word = MagicMock(word="world", start=0.2, end=1.2)
    mock_segment = MagicMock(words=[mock_word])
    mock_model.transcribe.return_value = ([mock_segment], None)

    with patch("video.renderer.assembler._get_whisper_model", return_value=mock_model):
        assembler._write_srt(
            script="world",
            path=srt_path,
            duration=1.5,
            audio=audio_path,
            subtitle_language=None,
        )

        mock_model.transcribe.assert_called_once_with(
            str(audio_path), beam_size=1, word_timestamps=True, vad_filter=True
        )
        assert srt_path.exists()


def test_write_srt_empty_raw_words_fallback(tmp_path):
    """Test _write_srt fallback to proportional split when transcribing results in empty raw_words."""
    srt_path = tmp_path / "test.srt"
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"dummy")

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], None)

    with patch("video.renderer.assembler._get_whisper_model", return_value=mock_model):
        # Should fall back to proportional split
        assembler._write_srt(
            script="Fallback sentence.",
            path=srt_path,
            duration=4.0,
            audio=audio_path,
        )
        assert srt_path.exists()
        assert "Fallback sentence" in srt_path.read_text(encoding="utf-8-sig")


def test_write_srt_proportional_empty_sentences(tmp_path):
    """Test proportional split when no sentences match."""
    srt_path = tmp_path / "test.srt"
    # An empty or whitespace-only script
    assembler._write_srt(
        script="   ",
        path=srt_path,
        duration=2.0,
    )
    assert srt_path.exists()
    content = srt_path.read_text(encoding="utf-8-sig")
    assert "00:00:00,000 --> 00:00:01,000" in content


def test_words_to_srt_lines_empty():
    """Test _words_to_srt_lines when word_data has no valid words."""
    assert assembler._words_to_srt_lines([], "classic", 3) == []
    assert assembler._words_to_srt_lines([{"word": " "}], "tiktok", 1) == []


def test_words_to_srt_lines_tiktok():
    """Test _words_to_srt_lines in tiktok format."""
    words = [
        {"word": "hello", "start": 0.5, "end": 1.0},
        {"word": "world", "start": 1.0, "end": 1.5},
    ]
    res = assembler._words_to_srt_lines(words, "tiktok", 1)
    assert res == [
        "1",
        "00:00:00,500 --> 00:00:01,000",
        "HELLO",
        "",
        "2",
        "00:00:01,000 --> 00:00:01,500",
        "WORLD",
        "",
    ]


def test_ts_conversion_overflow_value_error(monkeypatch):
    """Test that _ts conversion handles value or overflow errors gracefully."""
    with patch("builtins.divmod", side_effect=OverflowError("overflow")):
        res = assembler._ts(5.0)
        assert res == "00:00:00,000"


def test_run_success():
    """Test that _run succeeds when returncode is 0."""
    mock_res = MagicMock()
    mock_res.returncode = 0
    with patch("subprocess.run", return_value=mock_res):
        assembler._run(["ffmpeg", "-version"])


def test_assembler_create_segment_mp4_font_selection(tmp_path):
    """Test font and style selection in create_segment_mp4."""
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"dummy")

    config_tiktok = {
        "video": {"resolution": "1280x720", "fps": 30},
        "subtitles": {"format": "tiktok", "size": 36},
        "tts": {"lang": "hi"},
    }

    config_hindi = {
        "video": {"resolution": "1280x720", "fps": 30},
        "subtitles": {"format": "custom"},
        "tts": {"lang": "hi"},
    }

    config_en = {
        "video": {"resolution": "1280x720", "fps": 30},
        "subtitles": {"format": "custom"},
        "tts": {"lang": "en"},
    }

    run_cmds = []

    def fake_run(cmd, timeout=300):
        run_cmds.append(cmd)
        (tmp_path / "segment_06.mp4").write_bytes(b"fake")

    with (
        patch("video.renderer.assembler._run", side_effect=fake_run),
        patch("video.renderer.assembler.get_audio_duration", return_value=5.0),
    ):
        # 1. Tiktok style font selection
        assembler.create_segment_mp4(
            seg_num=6,
            audio=audio,
            script="hello",
            out_dir=tmp_path,
            config=config_tiktok,
            images=[],
        )
        assert "Fontname=Impact" in run_cmds[-1][-2]

        # 2. Hindi custom style font selection
        assembler.create_segment_mp4(
            seg_num=6,
            audio=audio,
            script="hello",
            out_dir=tmp_path,
            config=config_hindi,
            images=[],
        )
        assert "Fontname=Nirmala UI" in run_cmds[-1][-2]

        # 3. English custom style font selection
        assembler.create_segment_mp4(
            seg_num=6,
            audio=audio,
            script="hello",
            out_dir=tmp_path,
            config=config_en,
            images=[],
        )
        assert "Fontname=Arial" in run_cmds[-1][-2]


def test_assembler_create_segment_mp4_crossfade_duration_parsing_error(tmp_path):
    """Test crossfade duration parsing ValueError/IndexError exception block."""
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"dummy")

    images = [tmp_path / "img1.png", tmp_path / "img2.png"]
    for img in images:
        img.write_bytes(b"dummy")

    config = {
        "video": {"resolution": "1280x720", "fps": 30, "crossfade_duration": 0.5},
        "subtitles": {"format": "classic"},
        "tts": {"lang": "hi"},
    }

    def fake_run(cmd, timeout=300):
        for idx in range(len(cmd) - 1):
            if cmd[idx] == "-t":
                cmd[idx + 1] = "unparseable_duration_value"
        (tmp_path / "segment_07.mp4").write_bytes(b"fake")

    with (
        patch("video.renderer.assembler._run", side_effect=fake_run),
        patch("video.renderer.assembler.get_audio_duration", return_value=6.0),
    ):
        res = assembler.create_segment_mp4(
            seg_num=7,
            audio=audio,
            script="Hello world",
            out_dir=tmp_path,
            config=config,
            images=images,
        )
    assert res.exists()
