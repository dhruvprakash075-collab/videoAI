"""Tests for VIDEOAI_RUST_AUDIO opt-in behavior."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from audio.audio_fx import _try_native_audio_master
from utils.media_analyzer import _native_analyze_audio_wave


def test_native_audio_master_skips_import_when_flag_unset(tmp_path, monkeypatch):
    master_audio = MagicMock(side_effect=AssertionError("native bridge should stay disabled"))
    monkeypatch.setitem(
        __import__("sys").modules,
        "videoai_worker_native",
        SimpleNamespace(master_audio=master_audio),
    )
    monkeypatch.delenv("VIDEOAI_RUST_AUDIO", raising=False)

    assert _try_native_audio_master(tmp_path / "in.wav", tmp_path / "out.wav") is False
    master_audio.assert_not_called()


def test_native_audio_master_uses_bridge_when_flag_enabled(tmp_path, monkeypatch):
    input_path = tmp_path / "in.wav"
    output_path = tmp_path / "out.wav"
    input_path.write_bytes(b"RIFF")

    def master_audio(in_path: str, out_path: str) -> str:
        assert in_path == str(input_path)
        assert out_path == str(output_path)
        output_path.write_bytes(b"RIFF")
        return json.dumps({"passed": True})

    master_audio_mock = MagicMock(side_effect=master_audio)
    monkeypatch.setitem(
        __import__("sys").modules,
        "videoai_worker_native",
        SimpleNamespace(master_audio=master_audio_mock),
    )
    monkeypatch.setenv("VIDEOAI_RUST_AUDIO", "1")

    assert _try_native_audio_master(input_path, output_path) is True
    master_audio_mock.assert_called_once()


def test_native_audio_analysis_skips_import_when_flag_unset(tmp_path, monkeypatch):
    analyze_audio_wave = MagicMock(side_effect=AssertionError("native bridge should stay disabled"))
    monkeypatch.setitem(
        __import__("sys").modules,
        "videoai_worker_native",
        SimpleNamespace(analyze_audio_wave=analyze_audio_wave),
    )
    monkeypatch.delenv("VIDEOAI_RUST_AUDIO", raising=False)

    assert _native_analyze_audio_wave(tmp_path / "in.wav") is None
    analyze_audio_wave.assert_not_called()


def test_native_audio_analysis_uses_bridge_when_flag_enabled(tmp_path, monkeypatch):
    wav_path = tmp_path / "in.wav"
    wav_path.write_bytes(b"RIFF")
    report = {"sample_rate": 44100, "sample_width_bits": 16, "duration_s": 0.1}
    analyze_audio_wave = MagicMock(return_value=json.dumps(report))
    monkeypatch.setitem(
        __import__("sys").modules,
        "videoai_worker_native",
        SimpleNamespace(analyze_audio_wave=analyze_audio_wave),
    )
    monkeypatch.setenv("VIDEOAI_RUST_AUDIO", "1")

    assert _native_analyze_audio_wave(wav_path) == report
    analyze_audio_wave.assert_called_once_with(str(wav_path))
