import json
import queue
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from audio import audio_proxy


def test_read_optional_text_empty_missing_and_success(tmp_path: Path):
    assert audio_proxy._read_optional_text("") == ""
    assert audio_proxy._read_optional_text(str(tmp_path / "missing.txt")) == ""
    p = tmp_path / "ref.txt"
    p.write_text(" hello \n", encoding="utf-8")
    assert audio_proxy._read_optional_text(str(p)) == "hello"


def test_call_indicf5_worker_success_error_exception_and_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    audio_proxy._config_cache.clear()
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"wav")
    ref_text = tmp_path / "ref.txt"
    ref_text.write_text("reference", encoding="utf-8")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        res = MagicMock()
        res.stdout = 'noise\n{"status": "success", "wav_path": "x.wav"}\n'
        res.stderr = ""
        return res

    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {
                    "indicf5": {
                        "root": str(tmp_path),
                        "python": "py",
                        "ref_text_file": str(ref_text),
                        "use_pipeline_voice_sample": True,
                        "timeout_seconds": 1,
                    }
                }
            },
        ),
        patch("subprocess.run", side_effect=fake_run),
    ):
        out = audio_proxy._call_indicf5_worker(
            "hello", output_dir=tmp_path / "out", voice_sample=str(voice)
        )
    assert out["status"] == "success"
    assert any(str(voice.resolve()) in arg for arg in captured["cmd"])

    res = MagicMock(stdout="not json", stderr="bad stderr")
    with patch("subprocess.run", return_value=res), patch("audio.audio_proxy.load_config", return_value={}):
        out = audio_proxy._call_indicf5_worker("hello", output_dir=tmp_path / "out2")
    assert out == {"status": "error", "message": "bad stderr"}

    with patch("subprocess.run", side_effect=RuntimeError("boom")):
        out = audio_proxy._call_indicf5_worker("hello", output_dir=tmp_path / "out3")
    assert out["status"] == "error"
    assert "boom" in out["message"]


def test_enqueue_stdout_puts_lines_and_sentinel():
    proc = MagicMock()
    proc.stdout.readline.side_effect = ["a\n", "b\n", ""]
    q = queue.Queue()
    audio_proxy._enqueue_stdout(proc, q)
    assert [q.get(), q.get(), q.get()] == ["a\n", "b\n", ""]

    proc.stdout.readline.side_effect = RuntimeError("ignored")
    q = queue.Queue()
    audio_proxy._enqueue_stdout(proc, q)
    assert q.get() == ""


def test_supertonic_worker_start_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_proxy, "__file__", str(tmp_path / "audio_proxy.py"))
    w = audio_proxy._SupertonicWorker()
    assert w._start() is False
    assert w._failed is True

    worker = tmp_path / "supertonic_worker.py"
    worker.write_text("", encoding="utf-8")
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdout.readline.side_effect = ["junk\n", "{bad\n", '{"status": "ready"}\n', ""]
    w = audio_proxy._SupertonicWorker()
    with patch("subprocess.Popen", return_value=proc):
        assert w._start() is True

    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdout.readline.side_effect = ['{"status": "error", "message": "no model"}\n', ""]
    w = audio_proxy._SupertonicWorker()
    with patch("subprocess.Popen", return_value=proc):
        assert w._start() is False
        assert w._failed is True


def test_supertonic_worker_generate_progress_success_and_failure():
    w = audio_proxy._SupertonicWorker()
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdin = MagicMock()
    w._proc = proc
    w._stdout_q = queue.Queue()
    w._stdout_q.put("not json\n")
    w._stdout_q.put("{bad\n")
    w._stdout_q.put('{"status": "progress"}\n')
    w._stdout_q.put('{"status": "success", "wav_path": "x.wav"}\n')
    with patch.object(w, "_start", return_value=True), patch("time.time", return_value=0):
        assert w.generate({"text": "hi"}, timeout=1)["status"] == "success"

    w = audio_proxy._SupertonicWorker()
    w._proc = proc
    w._stdout_q = queue.Queue()
    w._stdout_q.put("")
    with patch.object(w, "_start", return_value=True):
        assert w.generate({"text": "hi"}, timeout=1) is None
        assert w._failed is True


def test_supertonic_worker_shutdown_and_cleanup():
    w = audio_proxy._SupertonicWorker()
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdin = MagicMock()
    w._proc = proc
    w.shutdown()
    proc.stdin.write.assert_called_once()

    proc = MagicMock()
    proc.kill.side_effect = OSError("ignored")
    w._proc = proc
    w._cleanup_proc()
    assert w._proc is None


def test_call_supertonic_worker_persistent_and_oneshot_paths(tmp_path):
    audio_proxy._config_cache.clear()
    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={"tts": {"supertonic": {"voice": "", "max_chunk_length": None}}},
        ),
        patch.object(
            audio_proxy._supertonic_worker,
            "generate",
            return_value={"status": "success", "wav_path": str(tmp_path / "x.wav")},
        ) as gen,
    ):
        out = audio_proxy._call_supertonic_worker("hello", output_dir=tmp_path)
    assert out["status"] == "success"
    assert gen.call_args.args[0]["voice"] == "M1"
    assert "max_chunk_length" not in gen.call_args.args[0]

    with patch.object(
        audio_proxy._supertonic_worker,
        "generate",
        return_value={"status": "error", "message": "worker bad"},
    ):
        out = audio_proxy._call_supertonic_worker("hello", output_dir=tmp_path)
    assert out["status"] == "error"

    worker = Path(audio_proxy.__file__).parent / "supertonic_worker.py"
    res = MagicMock(returncode=0, stdout='noise\n{"status":"success"}\n', stderr="")
    with (
        patch.object(audio_proxy._supertonic_worker, "generate", return_value=None),
        patch.object(Path, "exists", return_value=True),
        patch("subprocess.run", return_value=res) as run,
    ):
        out = audio_proxy._call_supertonic_worker("hello", output_dir=tmp_path, speed_override=1.2)
    assert out["status"] == "success"
    assert any("--speed=1.2" in arg for arg in run.call_args.args[0])
    assert str(worker).endswith("supertonic_worker.py")

    res = MagicMock(returncode=1, stdout="", stderr="bad")
    with (
        patch.object(audio_proxy._supertonic_worker, "generate", return_value=None),
        patch.object(Path, "exists", return_value=True),
        patch("subprocess.run", return_value=res),
    ):
        assert audio_proxy._call_supertonic_worker("hello", output_dir=tmp_path)["status"] == "error"

    res = MagicMock(returncode=0, stdout="{bad json}\n", stderr="")
    with (
        patch.object(audio_proxy._supertonic_worker, "generate", return_value=None),
        patch.object(Path, "exists", return_value=True),
        patch("subprocess.run", return_value=res),
    ):
        assert audio_proxy._call_supertonic_worker("hello", output_dir=tmp_path)["status"] == "error"

    with (
        patch.object(audio_proxy._supertonic_worker, "generate", return_value=None),
        patch.object(Path, "exists", return_value=False),
    ):
        assert audio_proxy._call_supertonic_worker("hello", output_dir=tmp_path)["status"] == "error"

    with (
        patch.object(audio_proxy._supertonic_worker, "generate", return_value=None),
        patch.object(Path, "exists", return_value=True),
        patch("subprocess.run", side_effect=RuntimeError("boom")),
    ):
        assert audio_proxy._call_supertonic_worker("hello", output_dir=tmp_path)["status"] == "error"


def test_call_supertonic_worker_defaults_output_dir_and_lang_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_generate(req):
        captured.update(req)
        return {"status": "success", "wav_path": "x.wav"}

    with patch.object(audio_proxy._supertonic_worker, "generate", side_effect=fake_generate):
        out = audio_proxy._call_supertonic_worker("hello", lang="", output_dir=None)
    assert out["status"] == "success"
    assert captured["lang"] is None
    assert Path("tts_output").exists()


def test_omnivoice_generate_progress_and_errors():
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdin = MagicMock()

    w = audio_proxy._OmniVoiceWorker()
    w._proc = proc
    w._stdout_q = queue.Queue()
    w._stdout_q.put("noise\n")
    w._stdout_q.put("{bad\n")
    w._stdout_q.put('{"status": "progress", "chunk": 1, "total": 2}\n')
    w._stdout_q.put('{"status": "success"}\n')
    with patch.object(w, "_start", return_value=True), patch("time.time", return_value=0):
        assert w.generate({"text": "hi"}, timeout=1)["status"] == "success"

    w = audio_proxy._OmniVoiceWorker()
    w._proc = proc
    w._stdout_q = queue.Queue()
    w._stdout_q.put("")
    with patch.object(w, "_start", return_value=True):
        assert w.generate({"text": "hi"}, timeout=1) is None
        assert w._failed is True


def test_omnivoice_oneshot_no_json_voice_sample_exception_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out_wav = tmp_path / "out.wav"
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"wav")

    res = MagicMock(returncode=0, stdout="no json", stderr="")
    with patch("subprocess.run", return_value=res) as run:
        out = audio_proxy._call_omnivoice_oneshot(
            "text", output_dir=tmp_path, out_wav=out_wav, voice_sample=str(voice)
        )
    assert out["status"] == "error"
    assert any(str(voice) in arg for arg in run.call_args.args[0])

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
        out = audio_proxy._call_omnivoice_oneshot("text", output_dir=tmp_path, out_wav=out_wav)
    assert out["status"] == "error"


def test_call_omnivoice_worker_defaults_output_dir_and_existing_voice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"wav")
    captured = {}

    def fake_generate(req):
        captured.update(req)
        return {"status": "success", "wav_path": "x.wav"}

    with (
        patch("audio.audio_proxy.load_config", return_value={"tts": {"omnivoice": {"ref_text": "ref"}}}),
        patch.object(audio_proxy._omnivoice_worker, "generate", side_effect=fake_generate),
    ):
        out = audio_proxy._call_omnivoice_worker("hello", output_dir=None, voice_sample=str(voice))
    assert out["status"] == "success"
    assert captured["voice_sample"] == str(voice)
    assert captured["ref_text"] == "ref"
    assert Path("tts_output").exists()


def test_tts_generate_alignment_paths_and_autodetect_sorted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "x.wav"
    wav_out.write_bytes(b"RIFF")
    aligned = tmp_path / "aligned.json"
    aligned.write_text("[]", encoding="utf-8")

    voices = tmp_path / "character_voices"
    voices.mkdir()
    (voices / "z.wav").write_bytes(b"wav")
    (voices / "a.wav").write_bytes(b"wav")
    captured = {}

    def fake_super(text, lang, output_dir, speed_override):
        captured["voice"] = None
        return {"status": "success", "wav_path": str(wav_out), "word_timestamps": str(tmp_path / "missing.json")}

    with (
        patch("audio.audio_proxy.load_config", return_value={"tts": {"engine": "supertonic", "alignment": {}}}),
        patch("audio.audio_proxy._call_supertonic_worker", side_effect=fake_super),
        patch("audio.tts_alignment.align_audio", return_value=str(aligned)),
    ):
        out = audio_proxy.tts_generate("hello", output_dir=tmp_path)
    assert out["word_timestamps"] == aligned

    with (
        patch("audio.audio_proxy.load_config", return_value={"tts": {"engine": "supertonic", "alignment": {"enabled": False}}}),
        patch("audio.audio_proxy._call_supertonic_worker", return_value={"status": "success", "wav_path": str(wav_out)}),
    ):
        out = audio_proxy.tts_generate("hello", output_dir=tmp_path)
    assert out["word_timestamps"] is None


def test_tts_generate_voice_profile_and_default_output_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    audio_proxy._config_cache.clear()
    wav_out = tmp_path / "x.wav"
    wav_out.write_bytes(b"RIFF")
    captured = {}

    def fake_omni(text, lang, output_dir, voice_sample, speed_override, sentence_gap_ms):
        captured["gap"] = sentence_gap_ms
        return {"status": "success", "wav_path": str(wav_out)}

    with (
        patch(
            "audio.audio_proxy.load_config",
            return_value={
                "tts": {
                    "engine": "omnivoice",
                    "voice_profile": {"sentence_gap_ms": 333},
                    "alignment": {"enabled": False},
                }
            },
        ),
        patch("audio.audio_proxy._call_omnivoice_worker", side_effect=fake_omni),
    ):
        out = audio_proxy.tts_generate("hello", output_dir=None)
    assert out["wav_path"] == wav_out
    assert captured["gap"] == 333
    assert Path("tts_output").exists()


def test_translate_hinglish_markdown_empty_and_degradation_failure():
    fake_client = MagicMock()
    fake_client.generate.return_value = "```hi\n<|x|>अनुवाद\n```"
    with (
        patch("audio.audio_proxy.load_config", return_value={"tts": {"lang": "hi"}}),
        patch("utils.ollama_client.get_ollama_client", return_value=fake_client),
    ):
        assert audio_proxy.translate_hinglish("hello") == "अनुवाद"

    fake_client.generate.return_value = "<|empty|>"
    with (
        patch("audio.audio_proxy.load_config", return_value={"tts": {"lang": "hi"}}),
        patch("utils.ollama_client.get_ollama_client", return_value=fake_client),
        patch("agents.director_agent.UIState.add_degradation", side_effect=RuntimeError("ui")),
    ):
        assert audio_proxy.translate_hinglish("hello", seg=7) == "hello"

    with (
        patch("audio.audio_proxy._get_config", side_effect=RuntimeError("config")),
        patch("utils.ollama_client.get_ollama_client", side_effect=RuntimeError("ollama")),
    ):
        assert audio_proxy.translate_hinglish("hello") == "hello"
