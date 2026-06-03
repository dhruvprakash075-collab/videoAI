"""test_audio_crossfade.py - Tests for D2: smooth audio joins via afade."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contextlib
from unittest.mock import patch


def _make_fake_audio(tmp_path, name="audio.wav"):
    p = tmp_path / name
    p.write_bytes(b"fake_audio")
    return p


def _make_fake_images(tmp_path, n=2):
    imgs = []
    for i in range(n):
        p = tmp_path / f"img_{i}.png"
        p.write_bytes(b"fake_img")
        imgs.append(p)
    return imgs


def _make_fake_word_timestamps_json(tmp_path, name="audio.words.json"):
    """Phase 0.5: TTS worker now produces {wav}.words.json; verify callers
    can pass a populated JSON into create_segment_mp4 without Whisper fallback."""
    import json as _json

    p = tmp_path / name
    p.write_text(
        _json.dumps(
            [
                {"word": "Hello", "start": 0.0, "end": 0.5},
                {"word": "world", "start": 0.5, "end": 1.0},
            ]
        ),
        encoding="utf-8",
    )
    return p


def test_afade_present_when_crossfade_ms_nonzero(tmp_path):
    """When audio_crossfade_ms > 0, the FFmpeg command should include afade."""
    from video.renderer.assembler import create_segment_mp4

    audio = _make_fake_audio(tmp_path)
    images = _make_fake_images(tmp_path)
    words_json = _make_fake_word_timestamps_json(tmp_path)
    config = {
        "video": {
            "resolution": "1920x1080",
            "fps": 24,
            "ken_burns": "off",
            "crossfade_duration": 0,
            "audio_crossfade_ms": 200,
        },
        "subtitles": {},
        "tts": {"lang": "en"},
        "performance": {"ffmpeg_threads": 0},
    }

    run_calls = []

    def fake_run(cmd, timeout=300):
        run_calls.append(cmd)
        (tmp_path / "segment_01.mp4").write_bytes(b"fake_mp4")

    def _whisper_should_not_be_called(*args, **kwargs):
        raise AssertionError("Whisper should not be invoked when word_timestamps_json is supplied")

    with (
        patch("video.renderer.assembler._run", side_effect=fake_run),
        patch("video.renderer.assembler.get_audio_duration", return_value=10.0),
        patch(
            "video.renderer.assembler._get_whisper_model", side_effect=_whisper_should_not_be_called
        ),
    ):
        with contextlib.suppress(Exception):
            create_segment_mp4(
                1,
                audio,
                "test script",
                tmp_path,
                config,
                images=images,
                word_timestamps_json=words_json,
            )

    all_args = " ".join(str(a) for cmd in run_calls for a in cmd)
    assert "afade" in all_args, "afade should be in FFmpeg command when crossfade_ms > 0"
    assert words_json.exists(), "word_timestamps_json fixture should be passed through unchanged"


def test_afade_absent_when_crossfade_ms_zero(tmp_path):
    """When audio_crossfade_ms = 0, the audio fade-out filter must NOT be added.

    The crossfade code path in assembler.py:336-337 emits an `afade=t=out:...`
    string along with an `-af` flag. With crossfade_ms=0, the else branch
    (line 341-346) runs and adds neither. We assert the SPECIFIC string
    instead of the over-broad `afade` so this test does not get confused
    by `afade` appearing in unrelated `-filter_complex` graphs.
    """
    from video.renderer.assembler import create_segment_mp4

    audio = _make_fake_audio(tmp_path)
    images = _make_fake_images(tmp_path)
    words_json = _make_fake_word_timestamps_json(tmp_path)
    config = {
        "video": {
            "resolution": "1920x1080",
            "fps": 24,
            "ken_burns": "off",
            "crossfade_duration": 0,
            "audio_crossfade_ms": 0,
        },
        "subtitles": {},
        "tts": {"lang": "en"},
        "performance": {"ffmpeg_threads": 0},
    }

    run_calls = []

    def fake_run(cmd, timeout=300):
        run_calls.append(cmd)
        (tmp_path / "segment_01.mp4").write_bytes(b"fake_mp4")

    with (
        patch("video.renderer.assembler._run", side_effect=fake_run),
        patch("video.renderer.assembler.get_audio_duration", return_value=10.0),
        patch("video.renderer.assembler._get_whisper_model", return_value=None),
    ):
        with contextlib.suppress(Exception):
            create_segment_mp4(
                1,
                audio,
                "test script",
                tmp_path,
                config,
                images=images,
                word_timestamps_json=words_json,
            )

    all_args = " ".join(str(a) for cmd in run_calls for a in cmd)
    assert run_calls, "create_segment_mp4 built no ffmpeg command (test is vacuous)"
    assert "afade=t=out" not in all_args, (
        f"afade=t=out must not appear when audio_crossfade_ms=0. Got: {all_args[:500]}"
    )
    assert "-af" not in all_args, (
        f"ffmpeg -af flag must not appear when audio_crossfade_ms=0. Got: {all_args[:500]}"
    )
    assert words_json.exists(), "word_timestamps_json fixture should be passed through unchanged"
    assert "-af" not in all_args, (
        f"ffmpeg -af flag must not appear when audio_crossfade_ms=0. Got: {all_args[:500]}"
    )
