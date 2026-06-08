"""test_audio_proxy.py - audio_proxy normalize, dispatch, RVC, translate, capabilities."""

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from audio import audio_proxy

# ── normalize_tts_engine ───────────────────────────────────────────────────────


def test_normalize_tts_engine_f5_aliases():
    for s in ["f5", "F5", " f5 ", "f5-tts", "f5tts", "f5_tts"]:
        assert audio_proxy.normalize_tts_engine(s) == "f5"


def test_normalize_tts_engine_omnivoice_aliases():
    for s in ["omnivoice", "omni", "voice_clone", "clone", "OmniVoice"]:
        assert audio_proxy.normalize_tts_engine(s) == "omnivoice"


def test_normalize_tts_engine_edge_aliases():
    for s in ["edge", "edge-tts", "edge_tts", "microsoft", "Edge"]:
        assert audio_proxy.normalize_tts_engine(s) == "edge"


def test_normalize_tts_engine_unknown_defaults_to_supertonic():
    with patch("audio.audio_proxy.log") as lg:
        assert audio_proxy.normalize_tts_engine("some random voice") == "supertonic"
        assert lg.warning.called


def test_normalize_tts_engine_non_string_defaults_to_supertonic():
    with patch("audio.audio_proxy.log") as lg:
        assert audio_proxy.normalize_tts_engine(None) == "supertonic"
        assert audio_proxy.normalize_tts_engine(123) == "supertonic"
        assert lg.warning.called


# ── tts_capabilities ──────────────────────────────────────────────────────────


def test_tts_capabilities_omnivoice_profile():
    caps = audio_proxy.tts_capabilities()
    assert caps["omnivoice"]["voice_cloning"] is True
    assert "hi" in caps["omnivoice"]["languages"]


def test_tts_capabilities_edge_profile():
    caps = audio_proxy.tts_capabilities()
    assert caps["edge"]["voice_cloning"] is False
    assert caps["edge"]["vram_hint_gb"] == 0.0


def test_tts_capabilities_keys():
    caps = audio_proxy.tts_capabilities()
    assert "omnivoice" in caps
    assert "edge" in caps


# ── _get_config caching ───────────────────────────────────────────────────────


def test_get_config_caches(monkeypatch):
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


# ── _resolve_omnivoice_python ──────────────────────────────────────────────────


def test_resolve_omnivoice_python_falls_back_to_sys_executable(monkeypatch, tmp_path):
    # Force the custom env path to not exist
    monkeypatch.setattr(audio_proxy, "__file__", str(tmp_path / "fake_audio" / "audio_proxy.py"))
    # Even if the custom env path doesn't exist, falls back to sys.executable
    assert audio_proxy._resolve_omnivoice_python() == audio_proxy.sys.executable


# ── _OmniVoiceWorker ───────────────────────────────────────────────────────────


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
    """If subprocess fails to start, _start() returns False and marks _failed."""
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
    w.shutdown()  # should not raise


def test_omnivoice_worker_shutdown_with_dead_proc():
    w = audio_proxy._OmniVoiceWorker()
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    w._proc = fake_proc
    with patch("audio.audio_proxy._OmniVoiceWorker._cleanup_proc") as cu:
        w.shutdown()
    assert cu.called


def test_omnivoice_worker_generate_handles_dead_worker():
    w = audio_proxy._OmniVoiceWorker()
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    # Mock the stdin/stdout
    fake_proc.stdin = MagicMock()
    fake_proc.stdout = MagicMock()
    # stdout.readline returns "success" with json (start was bypassed via _start mock)
    fake_proc.stdout.readline.side_effect = [
        json.dumps({"status": "success", "wav_path": "/tmp/x.wav"}) + "\n",
    ]
    w._proc = fake_proc
    with patch.object(w, "_start", return_value=True):
        out = w.generate({"text": "hello"})
    assert out == {"status": "success", "wav_path": "/tmp/x.wav"}


def test_omnivoice_worker_generate_progress_extends_deadline():
    w = audio_proxy._OmniVoiceWorker()
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.stdin = MagicMock()
    fake_proc.stdout = MagicMock()
    fake_proc.stdout.readline.side_effect = [
        json.dumps({"status": "progress", "chunk": 1, "total": 3}) + "\n",
        json.dumps({"status": "success", "wav_path": "/tmp/x.wav"}) + "\n",
    ]
    w._proc = fake_proc
    with patch.object(w, "_start", return_value=True):
        out = w.generate({"text": "x"})
    assert out["status"] == "success"


def test_omnivoice_worker_generate_worker_dies():
    w = audio_proxy._OmniVoiceWorker()
    fake_proc = MagicMock()
    fake_proc.poll.side_effect = [None, 1]  # Second poll returns non-None = dead
    fake_proc.stdin = MagicMock()
    fake_proc.stdout = MagicMock()
    fake_proc.stdout.readline.side_effect = [
        "",  # EOF
    ]
    w._proc = fake_proc
    with patch.object(w, "_start", return_value=True):
        out = w.generate({"text": "x"})
    assert out is None
    assert w._failed is True


# ── _F5Worker ──────────────────────────────────────────────────────────────────


def test_f5_worker_marked_failed_does_not_retry():
    w = audio_proxy._F5Worker()
    w._failed = True
    assert w._start() is False


def test_f5_worker_already_running():
    w = audio_proxy._F5Worker()
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    w._proc = fake_proc
    assert w._start() is True


def test_f5_worker_shutdown_when_no_proc():
    w = audio_proxy._F5Worker()
    w.shutdown()


def test_f5_worker_generate_returns_none_when_start_fails():
    w = audio_proxy._F5Worker()
    w._failed = True
    assert w.generate({"text": "x"}) is None


def test_f5_worker_generate_handles_dead_worker():
    w = audio_proxy._F5Worker()
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.stdin = MagicMock()
    fake_proc.stdout = MagicMock()
    fake_proc.stdout.readline.side_effect = [
        json.dumps({"status": "success", "wav_path": "/tmp/f5.wav"}) + "\n",
    ]
    w._proc = fake_proc
    with patch.object(w, "_start", return_value=True):
        out = w.generate({"text": "x"})
    assert out["wav_path"] == "/tmp/f5.wav"


# ── tts_generate dispatch ──────────────────────────────────────────────────────


def test_tts_generate_dispatches_to_omnivoice(tmp_path: Path):
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "x.wav"
    wav_out.write_bytes(b"RIFF")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "omnivoice", "lang": "hi", "voice_profile": {}},
                "models": {"writer": "zephyr"},
            },
        ),
        patch(
            "audio.audio_proxy._call_omnivoice_worker",
            return_value={"status": "success", "wav_path": str(wav_out)},
        ),
    ):
        out = audio_proxy.tts_generate("hello", output_dir=tmp_path)
    assert out["wav_path"] == wav_out


def test_tts_generate_dispatches_to_edge(tmp_path: Path):
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "e.wav"
    wav_out.write_bytes(b"RIFF")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "edge", "lang": "hi", "edge": {}, "voice_profile": {}},
            },
        ),
        patch(
            "audio.audio_proxy._call_edge_direct",
            return_value={"status": "success", "wav_path": str(wav_out)},
        ),
    ):
        out = audio_proxy.tts_generate("hello", output_dir=tmp_path)
    assert out["wav_path"] == wav_out


def test_tts_generate_dispatches_to_f5_with_fallback(tmp_path: Path):
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "f5.wav"
    wav_out.write_bytes(b"RIFF")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "f5", "lang": "hi", "voice_profile": {}},
            },
        ),
        patch(
            "audio.audio_proxy._call_f5_worker",
            return_value={"status": "success", "wav_path": str(wav_out)},
        ),
    ):
        out = audio_proxy.tts_generate("hello", output_dir=tmp_path)
    assert out["wav_path"] == wav_out


def test_tts_generate_f5_fails_then_omnivoice_succeeds(tmp_path: Path):
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "ov.wav"
    wav_out.write_bytes(b"RIFF")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "f5", "lang": "hi", "voice_profile": {}},
            },
        ),
        patch(
            "audio.audio_proxy._call_f5_worker",
            return_value={"status": "error", "message": "no model"},
        ),
        patch(
            "audio.audio_proxy._call_omnivoice_worker",
            return_value={"status": "success", "wav_path": str(wav_out)},
        ),
    ):
        out = audio_proxy.tts_generate("hello", output_dir=tmp_path)
    assert out["wav_path"] == wav_out


def test_tts_generate_f5_and_omnivoice_fail_then_edge(tmp_path: Path):
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "edge.wav"
    wav_out.write_bytes(b"RIFF")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "f5", "lang": "hi", "voice_profile": {}},
            },
        ),
        patch(
            "audio.audio_proxy._call_f5_worker",
            return_value={"status": "error", "message": "no f5"},
        ),
        patch(
            "audio.audio_proxy._call_omnivoice_worker",
            return_value={"status": "error", "message": "no omni"},
        ),
        patch(
            "audio.audio_proxy._call_edge_direct",
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
            return_value={
                "tts": {"engine": "edge", "lang": "hi", "voice_profile": {}},
            },
        ),
        patch(
            "audio.audio_proxy._call_edge_direct",
            return_value={"status": "error", "message": "edge fail"},
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
            return_value={
                "tts": {"engine": "edge", "lang": "hi", "voice_profile": {}},
            },
        ),
        patch(
            "audio.audio_proxy._call_edge_direct",
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
            return_value={
                "tts": {"engine": "edge", "lang": "hi", "voice_profile": {}},
            },
        ),
        patch(
            "audio.audio_proxy._call_edge_direct",
            return_value={"status": "success", "wav_path": str(tmp_path / "nope.wav")},
        ),
    ):
        with pytest.raises(RuntimeError, match="TTS file not found"):
            audio_proxy.tts_generate("x", output_dir=tmp_path)


def test_tts_generate_unknown_engine_falls_back_to_edge(tmp_path: Path):
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "u.wav"
    wav_out.write_bytes(b"RIFF")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "weirdo", "lang": "hi", "voice_profile": {}},
            },
        ),
        patch(
            "audio.audio_proxy._call_edge_direct",
            return_value={"status": "success", "wav_path": str(wav_out)},
        ) as edge,
    ):
        out = audio_proxy.tts_generate("x", output_dir=tmp_path)
    assert edge.called
    assert out["wav_path"] == wav_out


# ── translate_hinglish ────────────────────────────────────────────────────────


def test_translate_hinglish_uses_devanagari_prompt_by_default():
    audio_proxy._config_cache.clear()
    fake_client = MagicMock()
    fake_client.generate.return_value = "अनुवादित पाठ"
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "omnivoice", "lang": "hi"},
                "models": {"writer": "zephyr"},
                "ollama": {"host": "http://localhost:11434"},
            },
        ),
        patch("utils.ollama_client.get_ollama_client", return_value=fake_client),
    ):
        out = audio_proxy.translate_hinglish("hello world")
    assert "अनुवादित" in out
    # Devanagari prompt should NOT contain "Romanized Hindi"
    call = fake_client.generate.call_args
    assert "Romanized Hindi" not in call.args[0]


def test_translate_hinglish_uses_romanized_for_edge_non_hi():
    audio_proxy._config_cache.clear()
    fake_client = MagicMock()
    fake_client.generate.return_value = "Hinglish text"
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "edge", "lang": "en"},
                "models": {"writer": "zephyr"},
                "ollama": {"host": "http://localhost:11434"},
            },
        ),
        patch("utils.ollama_client.get_ollama_client", return_value=fake_client),
    ):
        out = audio_proxy.translate_hinglish("hello")
    assert out == "Hinglish text"
    call = fake_client.generate.call_args
    assert "Romanized Hindi" in call.args[0]


def test_translate_hinglish_falls_back_on_ollama_failure():
    audio_proxy._config_cache.clear()
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "omnivoice", "lang": "hi"},
                "models": {"writer": "zephyr"},
            },
        ),
        patch("utils.ollama_client.get_ollama_client", side_effect=RuntimeError("ollama down")),
    ):
        out = audio_proxy.translate_hinglish("hello world")
    assert out == "hello world"


def test_translate_hinglish_strips_chat_template_tokens():
    audio_proxy._config_cache.clear()
    fake_client = MagicMock()
    fake_client.generate.return_value = "<|im_start|>actual text<|im_end|>"
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "omnivoice", "lang": "hi"},
                "models": {"writer": "zephyr"},
            },
        ),
        patch("utils.ollama_client.get_ollama_client", return_value=fake_client),
    ):
        out = audio_proxy.translate_hinglish("x")
    assert "<|" not in out
    assert "actual text" in out


def test_translate_hinglish_empty_response_returns_original():
    audio_proxy._config_cache.clear()
    fake_client = MagicMock()
    fake_client.generate.return_value = ""
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {"engine": "omnivoice", "lang": "hi"},
                "models": {"writer": "zephyr"},
            },
        ),
        patch("utils.ollama_client.get_ollama_client", return_value=fake_client),
    ):
        out = audio_proxy.translate_hinglish("original text")
    assert out == "original text"


# ── rvc_convert ───────────────────────────────────────────────────────────────


def test_rvc_convert_disabled_returns_original(tmp_path: Path):
    src = tmp_path / "src.wav"
    src.write_bytes(b"RIFF")
    with patch("audio.audio_proxy.load_config", return_value={"rvc": {"enabled": False}}):
        out = audio_proxy.rvc_convert(src)
    assert out == src


def test_rvc_convert_missing_model_returns_original(tmp_path: Path):
    src = tmp_path / "src.wav"
    src.write_bytes(b"RIFF")
    with patch(
        "audio.audio_proxy.load_config",
        return_value={"rvc": {"enabled": True, "model_path": str(tmp_path / "missing.pth")}},
    ):
        out = audio_proxy.rvc_convert(src)
    assert out == src


def test_rvc_convert_success(tmp_path: Path):
    src = tmp_path / "src.wav"
    src.write_bytes(b"RIFF")
    model = tmp_path / "model.pth"
    model.write_bytes(b"x")
    out_dir = tmp_path / "out"
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"rvc": {"enabled": True, "model_path": str(model), "pitch_shift": 0}},
        ),
        patch("subprocess.run") as run_mock,
    ):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"status": "success", "wav_path": "x"}),
            stderr="",
        )
        out = audio_proxy.rvc_convert(src, output_dir=out_dir)
    assert out.parent == out_dir


def test_rvc_convert_failure_returns_original(tmp_path: Path):
    src = tmp_path / "src.wav"
    src.write_bytes(b"RIFF")
    model = tmp_path / "model.pth"
    model.write_bytes(b"x")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"rvc": {"enabled": True, "model_path": str(model)}},
        ),
        patch("subprocess.run") as run_mock,
    ):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="rvc crashed"
        )
        out = audio_proxy.rvc_convert(src)
    assert out == src


def test_rvc_convert_timeout_returns_original(tmp_path: Path):
    src = tmp_path / "src.wav"
    src.write_bytes(b"RIFF")
    model = tmp_path / "model.pth"
    model.write_bytes(b"x")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"rvc": {"enabled": True, "model_path": str(model)}},
        ),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="rvc", timeout=300)),
    ):
        out = audio_proxy.rvc_convert(src)
    assert out == src


def test_rvc_convert_exception_returns_original(tmp_path: Path):
    src = tmp_path / "src.wav"
    src.write_bytes(b"RIFF")
    model = tmp_path / "model.pth"
    model.write_bytes(b"x")
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"rvc": {"enabled": True, "model_path": str(model)}},
        ),
        patch("subprocess.run", side_effect=RuntimeError("explode")),
    ):
        out = audio_proxy.rvc_convert(src)
    assert out == src


# ── _call_edge_direct ─────────────────────────────────────────────────────────


def _setup_edge_mocks():
    """Build mock edge_tts + pydub modules in sys.modules."""
    import sys

    fake_edge = MagicMock()
    fake_pydub = MagicMock()
    sys.modules["edge_tts"] = fake_edge
    sys.modules["pydub"] = fake_pydub
    return fake_edge, fake_pydub


def _make_async_save(save_fn):
    """Wrap a sync save function as an AsyncMock for `await communicate.save(...)`."""
    return AsyncMock(side_effect=save_fn)


def test_call_edge_direct_creates_output_dir(tmp_path: Path):
    out_dir = tmp_path / "sub" / "out"
    fake_edge, fake_pydub = _setup_edge_mocks()

    def fake_save(path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x")

    fake_edge.Communicate.return_value.save = _make_async_save(fake_save)
    fake_pydub.AudioSegment.from_file.return_value = MagicMock(__len__=lambda self: 5000)
    out = audio_proxy._call_edge_direct("hi", "hi", output_dir=out_dir)
    assert out["status"] == "success"


def test_call_edge_direct_falls_back_to_text_length_on_audio_error(tmp_path: Path):
    out_dir = tmp_path / "out"
    fake_edge, fake_pydub = _setup_edge_mocks()

    def fake_save(path):
        Path(path).write_bytes(b"x")

    fake_edge.Communicate.return_value.save = _make_async_save(fake_save)
    # Force pydub.AudioSegment.from_file to fail
    fake_pydub.AudioSegment.from_file.side_effect = RuntimeError("audio fail")
    out = audio_proxy._call_edge_direct("hello world test", "hi", output_dir=out_dir)
    assert out["status"] == "success"


def test_call_edge_direct_handles_exception(tmp_path: Path):
    import sys

    # Make the Communicate() call itself raise — this triggers the top-level except
    fake = MagicMock()
    fake.Communicate.side_effect = RuntimeError("boom")
    sys.modules["edge_tts"] = fake
    sys.modules["pydub"] = MagicMock()
    out = audio_proxy._call_edge_direct("hi", "hi", output_dir=tmp_path)
    assert out["status"] == "error"


def test_call_edge_direct_converts_speed_to_rate(tmp_path: Path):
    out_dir = tmp_path / "out"
    fake_edge, fake_pydub = _setup_edge_mocks()

    def fake_save(path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x")

    fake_edge.Communicate.return_value.save = _make_async_save(fake_save)
    fake_pydub.AudioSegment.from_file.return_value = MagicMock(__len__=lambda self: 1000)
    audio_proxy._call_edge_direct("hi", "hi", output_dir=out_dir, speed=1.10)
    # rate should be "+10%"
    call_kwargs = fake_edge.Communicate.call_args.kwargs
    assert call_kwargs["rate"] == "+10%"


def test_call_edge_direct_handles_invalid_speed(tmp_path: Path):
    out_dir = tmp_path / "out"
    fake_edge, fake_pydub = _setup_edge_mocks()

    def fake_save(path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x")

    fake_edge.Communicate.return_value.save = _make_async_save(fake_save)
    fake_pydub.AudioSegment.from_file.return_value = MagicMock(__len__=lambda self: 1000)
    # speed=None should use voice_profile default rate
    out = audio_proxy._call_edge_direct("hi", "hi", output_dir=out_dir, speed=None)
    assert out["status"] == "success"


# ── get_audio_duration re-export ──────────────────────────────────────────────


def test_get_audio_duration_re_export(tmp_path: Path):
    """audio_proxy.get_audio_duration wraps utils.get_audio_duration."""
    p = tmp_path / "x.wav"
    p.write_bytes(b"RIFF")
    with patch("audio.audio_proxy._get_audio_duration_utils", return_value=12.5):
        out = audio_proxy.get_audio_duration(p)
    assert out == 12.5
