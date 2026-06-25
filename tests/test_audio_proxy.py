"""test_audio_proxy.py - audio_proxy normalize, dispatch, translate, capabilities."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from audio import audio_proxy


def test_normalize_tts_engine_omnivoice_aliases():
    for s in ["omnivoice", "omni", "voice_clone", "clone", "OmniVoice"]:
        assert audio_proxy.normalize_tts_engine(s) == "omnivoice"


def test_normalize_tts_engine_removed_aliases_default_to_supertonic():
    for s in ["f5", "edge", "edge-tts", "indicf5", "microsoft"]:
        assert audio_proxy.normalize_tts_engine(s) == "supertonic"


def test_normalize_tts_engine_unknown_defaults_to_supertonic():
    with patch("audio.audio_proxy.log") as lg:
        assert audio_proxy.normalize_tts_engine("some random voice") == "supertonic"
        assert lg.warning.called


def test_normalize_tts_engine_non_string_defaults_to_supertonic():
    with patch("audio.audio_proxy.log") as lg:
        assert audio_proxy.normalize_tts_engine(None) == "supertonic"
        assert audio_proxy.normalize_tts_engine(123) == "supertonic"
        assert lg.warning.called


def test_tts_capabilities_keys():
    caps = audio_proxy.tts_capabilities()
    assert set(caps) == {"supertonic", "omnivoice"}
    assert caps["supertonic"]["vram_hint_gb"] == 0.0
    assert caps["omnivoice"]["voice_cloning"] is True


def test_get_config_caches():
    audio_proxy._config_cache.clear()
    with patch("audio.audio_proxy.load_config", return_value={"k": 1}) as lc:
        a = audio_proxy._get_config()
        b = audio_proxy._get_config()
    assert a is b
    assert lc.call_count == 1


def test_get_config_falls_back_to_empty_on_error():
    audio_proxy._config_cache.clear()
    with patch("audio.audio_proxy.load_config", side_effect=RuntimeError("boom")):
        out = audio_proxy._get_config()
    assert out == {}


def test_resolve_omnivoice_python_falls_back_to_sys_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_proxy, "__file__", str(tmp_path / "fake_audio" / "audio_proxy.py"))
    assert audio_proxy._resolve_omnivoice_python() == audio_proxy.sys.executable


def test_omnivoice_worker_already_running():
    w = audio_proxy._OmniVoiceWorker()
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    w._proc = fake_proc
    assert w._start() is True


def test_omnivoice_worker_marked_failed_does_not_retry():
    w = audio_proxy._OmniVoiceWorker()
    w._failed = True
    assert w._start() is False


def test_omnivoice_worker_start_unavailable_raises():
    w = audio_proxy._OmniVoiceWorker()
    with patch("subprocess.Popen", side_effect=FileNotFoundError):
        assert w._start() is False
        assert w._failed is True


def test_omnivoice_worker_generate_returns_none_when_start_fails():
    w = audio_proxy._OmniVoiceWorker()
    w._failed = True
    assert w.generate({"text": "x"}) is None


def test_omnivoice_worker_shutdown_when_no_proc():
    w = audio_proxy._OmniVoiceWorker()
    w.shutdown()


def test_omnivoice_worker_generate_handles_success():
    w = audio_proxy._OmniVoiceWorker()
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.stdin = MagicMock()
    fake_proc.stdout = MagicMock()
    fake_proc.stdout.readline.side_effect = [
        json.dumps({"status": "success", "wav_path": "/tmp/x.wav"}) + "\n",
    ]
    w._proc = fake_proc
    with patch.object(w, "_start", return_value=True):
        out = w.generate({"text": "hello"})
    assert out == {"status": "success", "wav_path": "/tmp/x.wav"}


def test_tts_generate_dispatches_to_omnivoice(tmp_path: Path):
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "x.wav"
    wav_out.write_bytes(b"RIFF")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"tts": {"engine": "omnivoice", "lang": "hi", "voice_profile": {}}},
        ),
        patch(
            "audio.audio_proxy._call_omnivoice_worker",
            return_value={"status": "success", "wav_path": str(wav_out)},
        ) as omni,
    ):
        out = audio_proxy.tts_generate("hello", output_dir=tmp_path)
    assert omni.called
    assert out["wav_path"] == wav_out


def test_tts_generate_unknown_engine_falls_back_to_supertonic(tmp_path: Path):
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "u.wav"
    wav_out.write_bytes(b"RIFF")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"tts": {"engine": "weirdo", "lang": "hi", "voice_profile": {}}},
        ),
        patch(
            "audio.audio_proxy._call_supertonic_worker",
            return_value={"status": "success", "wav_path": str(wav_out)},
        ) as supertonic,
    ):
        out = audio_proxy.tts_generate("x", output_dir=tmp_path)
    assert supertonic.called
    assert out["wav_path"] == wav_out


def test_tts_generate_supertonic_fails_then_omnivoice_succeeds(tmp_path: Path):
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "ov.wav"
    wav_out.write_bytes(b"RIFF")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"tts": {"engine": "supertonic", "lang": "hi", "voice_profile": {}}},
        ),
        patch(
            "audio.audio_proxy._call_supertonic_worker",
            return_value={"status": "error", "message": "super fail"},
        ),
        patch(
            "audio.audio_proxy._call_omnivoice_worker",
            return_value={"status": "success", "wav_path": str(wav_out)},
        ),
    ):
        out = audio_proxy.tts_generate("hello", output_dir=tmp_path)
    assert out["wav_path"] == wav_out


def test_tts_generate_raises_when_all_engines_fail():
    audio_proxy._config_cache.clear()
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"tts": {"engine": "supertonic", "lang": "hi", "voice_profile": {}}},
        ),
        patch(
            "audio.audio_proxy._call_supertonic_worker",
            return_value={"status": "error", "message": "super fail"},
        ),
        patch(
            "audio.audio_proxy._call_omnivoice_worker",
            return_value={"status": "error", "message": "omni fail"},
        ),
    ):
        with pytest.raises(RuntimeError, match="TTS generation failed"):
            audio_proxy.tts_generate("hello")


def test_tts_generate_includes_word_timestamps(tmp_path: Path):
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "x.wav"
    wav_out.write_bytes(b"RIFF")
    ts_path = tmp_path / "ts.json"
    ts_path.write_text("[]")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"tts": {"engine": "omnivoice", "lang": "hi", "voice_profile": {}}},
        ),
        patch(
            "audio.audio_proxy._call_omnivoice_worker",
            return_value={
                "status": "success",
                "wav_path": str(wav_out),
                "word_timestamps": str(ts_path),
            },
        ),
    ):
        out = audio_proxy.tts_generate("hi", output_dir=tmp_path)
    assert out["word_timestamps"] == ts_path


def test_tts_generate_missing_output_file_raises(tmp_path: Path):
    audio_proxy._config_cache.clear()
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"tts": {"engine": "supertonic", "lang": "hi", "voice_profile": {}}},
        ),
        patch(
            "audio.audio_proxy._call_supertonic_worker",
            return_value={"status": "success", "wav_path": str(tmp_path / "nope.wav")},
        ),
    ):
        with pytest.raises(RuntimeError, match="TTS file not found"):
            audio_proxy.tts_generate("x", output_dir=tmp_path)


def test_translate_hinglish_uses_devanagari_prompt_by_default():
    fake_client = MagicMock()
    fake_client.generate.return_value = "अनुवादित पाठ"
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "supertonic", "lang": "hi"},
                "models": {"writer": "zephyr"},
                "ollama": {"host": "http://localhost:11434"},
            },
        ),
        patch("utils.ollama_client.get_ollama_client", return_value=fake_client),
    ):
        out = audio_proxy.translate_hinglish("hello world")
    assert "अनुवादित" in out
    assert "Romanized Hindi" not in fake_client.generate.call_args.args[0]


def test_translate_hinglish_uses_romanized_for_non_hi():
    fake_client = MagicMock()
    fake_client.generate.return_value = "Hinglish text"
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "supertonic", "lang": "en"},
                "models": {"writer": "zephyr"},
                "ollama": {"host": "http://localhost:11434"},
            },
        ),
        patch("utils.ollama_client.get_ollama_client", return_value=fake_client),
    ):
        out = audio_proxy.translate_hinglish("hello")
    assert out == "Hinglish text"
    assert "Romanized Hindi" in fake_client.generate.call_args.args[0]


def test_translate_hinglish_falls_back_on_ollama_failure():
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"tts": {"engine": "supertonic", "lang": "hi"}},
        ),
        patch("utils.ollama_client.get_ollama_client", side_effect=RuntimeError("ollama down")),
    ):
        out = audio_proxy.translate_hinglish("hello world")
    assert out == "hello world"


def test_get_audio_duration_re_export(tmp_path: Path):
    p = tmp_path / "x.wav"
    p.write_bytes(b"RIFF")
    with patch("audio.audio_proxy._get_audio_duration_utils", return_value=12.5):
        out = audio_proxy.get_audio_duration(p)
    assert out == 12.5
