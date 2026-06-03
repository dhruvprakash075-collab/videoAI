"""test_audio_proxy_extended.py - Additional tests for uncovered audio_proxy paths.

Covers: _call_f5_worker, _call_omnivoice_worker, _call_omnivoice_oneshot,
shutdown_omnivoice_worker, shutdown_f5_worker, voice sample auto-detection,
tts_generate speed/voice-sample paths, translate_hinglish degradation ledger,
and _F5Worker / _OmniVoiceWorker edge cases.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from audio import audio_proxy

# ── _call_omnivoice_worker ────────────────────────────────────────────────────


class TestCallOmnivoiceWorker:
    def test_persistent_worker_success(self, tmp_path):
        audio_proxy._config_cache.clear()
        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={
                    "tts": {"omnivoice": {"speed": 0.85, "num_step": 16, "guidance_scale": 2.5}}
                },
            ),
            patch.object(
                audio_proxy._omnivoice_worker,
                "generate",
                return_value={"status": "success", "wav_path": str(tmp_path / "out.wav")},
            ),
        ):
            result = audio_proxy._call_omnivoice_worker("hello", output_dir=tmp_path)
        assert result["status"] == "success"

    def test_falls_back_to_oneshot_when_worker_returns_none(self, tmp_path):
        audio_proxy._config_cache.clear()
        fake_oneshot = {"status": "success", "wav_path": str(tmp_path / "o.wav")}
        with (
            patch("audio.audio_proxy.load_config", return_value={"tts": {"omnivoice": {}}}),
            patch.object(audio_proxy._omnivoice_worker, "generate", return_value=None),
            patch(
                "audio.audio_proxy._call_omnivoice_oneshot", return_value=fake_oneshot
            ) as oneshot,
        ):
            result = audio_proxy._call_omnivoice_worker("hello", output_dir=tmp_path)
        oneshot.assert_called_once()
        assert result["status"] == "success"

    def test_speed_override_takes_precedence(self, tmp_path):
        audio_proxy._config_cache.clear()
        captured = {}

        def fake_generate(req, **kw):
            captured["speed"] = req.get("speed")
            return {"status": "success", "wav_path": str(tmp_path / "o.wav")}

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"omnivoice": {"speed": 0.85}}},
            ),
            patch.object(audio_proxy._omnivoice_worker, "generate", side_effect=fake_generate),
        ):
            audio_proxy._call_omnivoice_worker("hello", speed_override=1.1, output_dir=tmp_path)

        assert abs(captured["speed"] - 1.1) < 0.01

    def test_sentence_gap_ms_in_request(self, tmp_path):
        audio_proxy._config_cache.clear()
        captured = {}

        def fake_generate(req, **kw):
            captured["gap"] = req.get("sentence_gap_ms")
            return {"status": "success", "wav_path": "x.wav"}

        with (
            patch("audio.audio_proxy.load_config", return_value={"tts": {"omnivoice": {}}}),
            patch.object(audio_proxy._omnivoice_worker, "generate", side_effect=fake_generate),
        ):
            audio_proxy._call_omnivoice_worker("hello", sentence_gap_ms=300, output_dir=tmp_path)

        assert captured["gap"] == 300

    def test_no_sentence_gap_when_none(self, tmp_path):
        audio_proxy._config_cache.clear()
        captured = {}

        def fake_generate(req, **kw):
            captured["has_gap"] = "sentence_gap_ms" in req
            return {"status": "success", "wav_path": "x.wav"}

        with (
            patch("audio.audio_proxy.load_config", return_value={"tts": {"omnivoice": {}}}),
            patch.object(audio_proxy._omnivoice_worker, "generate", side_effect=fake_generate),
        ):
            audio_proxy._call_omnivoice_worker("hello", sentence_gap_ms=None, output_dir=tmp_path)

        assert captured["has_gap"] is False

    def test_default_output_dir_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()

        with (
            patch("audio.audio_proxy.load_config", return_value={"tts": {"omnivoice": {}}}),
            patch.object(
                audio_proxy._omnivoice_worker,
                "generate",
                return_value={"status": "success", "wav_path": "x.wav"},
            ),
        ):
            audio_proxy._call_omnivoice_worker("hello")  # no output_dir

        assert (tmp_path / "tts_output").exists()


# ── _call_omnivoice_oneshot ───────────────────────────────────────────────────


class TestCallOmnivoiceOneshot:
    def test_success_parses_json_from_stdout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out_wav = tmp_path / "out.wav"
        # The oneshot reads json from stdout — provide valid json
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = (
            f'{{"status": "success", "wav_path": "{str(out_wav).replace(chr(92), "/")}"}}' + "\n"
        )
        fake_result.stderr = ""

        with patch("subprocess.run", return_value=fake_result):
            result = audio_proxy._call_omnivoice_oneshot(
                "text", output_dir=tmp_path, out_wav=out_wav
            )

        # The function returns the parsed json directly
        assert result["status"] == "success"

    def test_failure_when_returncode_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out_wav = tmp_path / "out.wav"
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        fake_result.stderr = "model not found"

        with patch("subprocess.run", return_value=fake_result):
            result = audio_proxy._call_omnivoice_oneshot(
                "text", output_dir=tmp_path, out_wav=out_wav
            )

        assert result["status"] == "error"

    def test_exception_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out_wav = tmp_path / "out.wav"

        with patch("subprocess.run", side_effect=RuntimeError("crash")):
            result = audio_proxy._call_omnivoice_oneshot(
                "text", output_dir=tmp_path, out_wav=out_wav
            )

        assert result["status"] == "error"
        assert "crash" in result["message"]

    def test_temp_file_cleaned_up_on_success(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out_wav = tmp_path / "out.wav"
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = '{"status": "success", "wav_path": "x.wav"}\n'
        fake_result.stderr = ""

        with patch("subprocess.run", return_value=fake_result):
            audio_proxy._call_omnivoice_oneshot("text", output_dir=tmp_path, out_wav=out_wav)

        # temp files in studio_checkpoints/temp should be cleaned up
        temp_dir = tmp_path / "studio_checkpoints" / "temp"
        if temp_dir.exists():
            remaining = list(temp_dir.glob("omnivoice_input_*.txt"))
            assert len(remaining) == 0

    def test_ref_text_appended_when_provided(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out_wav = tmp_path / "out.wav"
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stdout = '{"status": "success"}'
            m.stderr = ""
            return m

        with patch("subprocess.run", side_effect=fake_run):
            audio_proxy._call_omnivoice_oneshot(
                "text", output_dir=tmp_path, out_wav=out_wav, ref_text="reference"
            )

        assert any("--ref-text=reference" in c for c in captured_cmd)

    def test_no_json_in_stdout_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out_wav = tmp_path / "out.wav"
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "no json here at all\n"
        fake_result.stderr = ""

        with patch("subprocess.run", return_value=fake_result):
            result = audio_proxy._call_omnivoice_oneshot(
                "text", output_dir=tmp_path, out_wav=out_wav
            )

        assert result["status"] == "error"


# ── _call_f5_worker ───────────────────────────────────────────────────────────


class TestCallF5Worker:
    def test_returns_persistent_worker_response(self, tmp_path):
        audio_proxy._config_cache.clear()
        fake_resp = {"status": "success", "wav_path": str(tmp_path / "f5.wav")}

        with (
            patch("audio.audio_proxy.load_config", return_value={"tts": {"f5": {"nfe_step": 16}}}),
            patch.object(audio_proxy._f5_worker, "generate", return_value=fake_resp),
        ):
            result = audio_proxy._call_f5_worker("hello", output_dir=tmp_path)

        assert result["status"] == "success"

    def test_falls_back_to_oneshot_when_worker_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = '{"status": "success", "wav_path": "x.wav"}'
        fake_result.stderr = ""

        with (
            patch("audio.audio_proxy.load_config", return_value={"tts": {"f5": {}}}),
            patch.object(audio_proxy._f5_worker, "generate", return_value=None),
            patch("subprocess.run", return_value=fake_result),
        ):
            result = audio_proxy._call_f5_worker("hello", output_dir=tmp_path)

        assert result["status"] == "success"

    def test_speed_override_applied(self, tmp_path):
        audio_proxy._config_cache.clear()
        captured = {}

        def fake_gen(req, **kw):
            captured["speed"] = req.get("speed")
            return {"status": "success", "wav_path": "x.wav"}

        with (
            patch("audio.audio_proxy.load_config", return_value={"tts": {"f5": {}}}),
            patch.object(audio_proxy._f5_worker, "generate", side_effect=fake_gen),
        ):
            audio_proxy._call_f5_worker("hello", speed_override=1.2, output_dir=tmp_path)

        assert abs(captured["speed"] - 1.2) < 0.01

    def test_oneshot_exception_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()

        with (
            patch("audio.audio_proxy.load_config", return_value={"tts": {"f5": {}}}),
            patch.object(audio_proxy._f5_worker, "generate", return_value=None),
            patch("subprocess.run", side_effect=RuntimeError("crash")),
        ):
            result = audio_proxy._call_f5_worker("hello", output_dir=tmp_path)

        assert result["status"] == "error"

    def test_config_load_failure_uses_defaults(self, tmp_path):
        audio_proxy._config_cache.clear()
        captured = {}

        def fake_gen(req, **kw):
            captured["nfe_step"] = req.get("nfe_step")
            return {"status": "success", "wav_path": "x.wav"}

        with (
            patch("audio.audio_proxy.load_config", side_effect=RuntimeError("no config")),
            patch.object(audio_proxy._f5_worker, "generate", side_effect=fake_gen),
        ):
            audio_proxy._call_f5_worker("hello", output_dir=tmp_path)

        assert captured["nfe_step"] == 16  # default nfe_step


# ── shutdown functions ────────────────────────────────────────────────────────


class TestShutdownFunctions:
    def test_shutdown_omnivoice_worker_no_crash(self):
        with patch.object(audio_proxy._omnivoice_worker, "shutdown") as sh:
            audio_proxy.shutdown_omnivoice_worker()
        sh.assert_called_once()

    def test_shutdown_f5_worker_no_crash(self):
        with patch.object(audio_proxy._f5_worker, "shutdown") as sh:
            audio_proxy.shutdown_f5_worker()
        sh.assert_called_once()

    def test_f5_worker_shutdown_with_live_proc(self):
        w = audio_proxy._F5Worker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None  # still running
        fake_proc.stdin = MagicMock()
        w._proc = fake_proc

        w.shutdown()  # Should send shutdown cmd and cleanup
        fake_proc.stdin.write.assert_called()

    def test_omnivoice_worker_shutdown_with_live_proc(self):
        w = audio_proxy._OmniVoiceWorker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdin = MagicMock()
        w._proc = fake_proc

        w.shutdown()
        fake_proc.stdin.write.assert_called()

    def test_f5_worker_shutdown_exception_silenced(self):
        w = audio_proxy._F5Worker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdin.write.side_effect = RuntimeError("broken pipe")
        w._proc = fake_proc

        # Should not raise
        w.shutdown()


# ── tts_generate voice sample auto-detection ─────────────────────────────────


class TestTtsGenerateVoiceSample:
    def test_auto_detects_narration_voice_wav(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")

        # Create the narration voice file
        voices_dir = tmp_path / "character_voices"
        voices_dir.mkdir()
        narration = voices_dir / "narration_voice.wav"
        narration.write_bytes(b"WAV")

        captured = {}

        def fake_omni(text, lang, output_dir, voice_sample, speed_override, sentence_gap_ms):
            captured["voice_sample"] = voice_sample
            return {"status": "success", "wav_path": str(wav_out)}

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "omnivoice", "lang": "hi", "voice_profile": {}}},
            ),
            patch("audio.audio_proxy._call_omnivoice_worker", side_effect=fake_omni),
        ):
            audio_proxy.tts_generate("hello", output_dir=tmp_path)

        # voice_sample should reference narration_voice.wav (relative or absolute path)
        assert "narration_voice" in captured["voice_sample"]

    def test_auto_detects_any_wav_when_no_narration_voice(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")

        # Create character_voices dir with a wav (no narration_voice.wav)
        voices_dir = tmp_path / "character_voices"
        voices_dir.mkdir()
        any_wav = voices_dir / "other_voice.wav"
        any_wav.write_bytes(b"WAV")

        captured = {}

        def fake_edge(text, lang, output_dir, voice_profile, speed):
            captured["voice_sample"] = "from_edge"
            return {"status": "success", "wav_path": str(wav_out)}

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "edge", "lang": "hi", "voice_profile": {}}},
            ),
            patch("audio.audio_proxy._call_edge_direct", side_effect=fake_edge),
        ):
            audio_proxy.tts_generate("hello", output_dir=tmp_path)

        # Should not crash — auto-detection is for omnivoice/f5 path

    def test_no_auto_detect_when_no_voices_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "edge", "lang": "hi", "voice_profile": {}}},
            ),
            patch(
                "audio.audio_proxy._call_edge_direct",
                return_value={"status": "success", "wav_path": str(wav_out)},
            ),
        ):
            result = audio_proxy.tts_generate("hello", output_dir=tmp_path)

        assert result["wav_path"] == wav_out

    def test_ref_only_files_excluded_from_auto_detect(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")

        # Only ref/mono files → should not be selected
        voices_dir = tmp_path / "character_voices"
        voices_dir.mkdir()
        ref_file = voices_dir / "voice_ref8s_mono.wav"
        ref_file.write_bytes(b"WAV")

        captured_vs = {}

        def fake_omni(text, lang, output_dir, voice_sample, speed_override, sentence_gap_ms):
            captured_vs["vs"] = voice_sample
            return {"status": "success", "wav_path": str(wav_out)}

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "omnivoice", "lang": "hi", "voice_profile": {}}},
            ),
            patch("audio.audio_proxy._call_omnivoice_worker", side_effect=fake_omni),
        ):
            audio_proxy.tts_generate("hello", output_dir=tmp_path)

        # ref/mono file should NOT have been selected
        assert "ref8s_mono" not in captured_vs.get("vs", "")


# ── tts_generate speed passthrough ───────────────────────────────────────────


class TestTtsGenerateSpeedPassthrough:
    def test_speed_passed_to_omnivoice(self, tmp_path):
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")
        captured = {}

        def fake_omni(text, lang, output_dir, voice_sample, speed_override, sentence_gap_ms):
            captured["speed"] = speed_override
            return {"status": "success", "wav_path": str(wav_out)}

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "omnivoice", "lang": "hi", "voice_profile": {}}},
            ),
            patch("audio.audio_proxy._call_omnivoice_worker", side_effect=fake_omni),
        ):
            audio_proxy.tts_generate("hello", speed=0.9, output_dir=tmp_path)

        assert abs(captured["speed"] - 0.9) < 0.01

    def test_speed_passed_to_edge(self, tmp_path):
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")
        captured = {}

        def fake_edge(text, lang, output_dir, voice_profile, speed):
            captured["speed"] = speed
            return {"status": "success", "wav_path": str(wav_out)}

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "edge", "lang": "hi", "voice_profile": {}}},
            ),
            patch("audio.audio_proxy._call_edge_direct", side_effect=fake_edge),
        ):
            audio_proxy.tts_generate("hello", speed=1.1, output_dir=tmp_path)

        assert abs(captured["speed"] - 1.1) < 0.01

    def test_speed_passed_to_f5(self, tmp_path):
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")
        captured = {}

        def fake_f5(text, lang, output_dir, voice_sample, speed_override):
            captured["speed"] = speed_override
            return {"status": "success", "wav_path": str(wav_out)}

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "f5", "lang": "hi", "voice_profile": {}}},
            ),
            patch("audio.audio_proxy._call_f5_worker", side_effect=fake_f5),
        ):
            audio_proxy.tts_generate("hello", speed=0.8, output_dir=tmp_path)

        assert abs(captured["speed"] - 0.8) < 0.01


# ── translate_hinglish degradation ledger ─────────────────────────────────────


class TestTranslateHinglishDegradation:
    def test_adds_degradation_on_failure(self):
        audio_proxy._config_cache.clear()
        fake_uis = MagicMock()
        fake_director = MagicMock()
        fake_director.UIState = fake_uis

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={
                    "tts": {"engine": "omnivoice", "lang": "hi"},
                    "models": {"writer": "zephyr"},
                },
            ),
            patch("utils.ollama_client.get_ollama_client", side_effect=RuntimeError("ollama down")),
            patch.dict("sys.modules", {"agents.director_agent": fake_director}),
        ):
            result = audio_proxy.translate_hinglish("hello", seg=2)

        # Falls back to original
        assert result == "hello"
        fake_uis.add_degradation.assert_called()

    def test_degradation_ledger_failure_is_silent(self):
        audio_proxy._config_cache.clear()
        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={
                    "tts": {"engine": "omnivoice", "lang": "hi"},
                    "models": {"writer": "zephyr"},
                },
            ),
            patch("utils.ollama_client.get_ollama_client", side_effect=RuntimeError("down")),
            patch.dict("sys.modules", {"agents.director_agent": None}),
        ):
            result = audio_proxy.translate_hinglish("original")

        assert result == "original"


# ── _OmniVoiceWorker edge cases ───────────────────────────────────────────────


class TestOmniVoiceWorkerEdgeCases:
    def test_generate_with_non_json_lines_skipped(self):
        w = audio_proxy._OmniVoiceWorker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdin = MagicMock()
        fake_proc.stdout = MagicMock()
        fake_proc.stdout.readline.side_effect = [
            "not json\n",
            "also not json\n",
            json.dumps({"status": "success", "wav_path": "/tmp/x.wav"}) + "\n",
        ]
        w._proc = fake_proc

        with patch.object(w, "_start", return_value=True):
            result = w.generate({"text": "hello"})

        assert result["status"] == "success"

    def test_generate_skips_lines_not_starting_with_brace(self):
        w = audio_proxy._OmniVoiceWorker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdin = MagicMock()
        fake_proc.stdout = MagicMock()
        fake_proc.stdout.readline.side_effect = [
            "INFO: loading model\n",
            '{"status": "ready"}\n',  # This is a terminal response
        ]
        w._proc = fake_proc

        with patch.object(w, "_start", return_value=True):
            result = w.generate({"text": "x"})

        # "ready" is a terminal response
        assert result is not None

    def test_cleanup_proc_kills_process(self):
        w = audio_proxy._OmniVoiceWorker()
        fake_proc = MagicMock()
        w._proc = fake_proc
        w._cleanup_proc()
        fake_proc.kill.assert_called_once()
        assert w._proc is None

    def test_f5_worker_cleanup_proc(self):
        w = audio_proxy._F5Worker()
        fake_proc = MagicMock()
        w._proc = fake_proc
        w._cleanup_proc()
        fake_proc.kill.assert_called_once()
        assert w._proc is None

    def test_f5_worker_generate_progress_extends_deadline(self):
        w = audio_proxy._F5Worker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdin = MagicMock()
        fake_proc.stdout = MagicMock()
        fake_proc.stdout.readline.side_effect = [
            json.dumps({"status": "progress", "chunk": 1, "total": 3}) + "\n",
            json.dumps({"status": "success", "wav_path": "/tmp/f5.wav"}) + "\n",
        ]
        w._proc = fake_proc

        with patch.object(w, "_start", return_value=True):
            result = w.generate({"text": "x"})

        assert result["status"] == "success"

    def test_f5_worker_generate_worker_dies_mid_request(self):
        w = audio_proxy._F5Worker()
        fake_proc = MagicMock()
        fake_proc.poll.side_effect = [None, 1]  # dies on second poll
        fake_proc.stdin = MagicMock()
        fake_proc.stdout = MagicMock()
        fake_proc.stdout.readline.side_effect = [""]  # EOF

        w._proc = fake_proc
        with patch.object(w, "_start", return_value=True):
            result = w.generate({"text": "x"})

        assert result is None
        assert w._failed is True


# ── Edge TTS Direct Fallback Exception Paths ─────────────────────────────────


class TestEdgeTtsDirectExceptions:
    def test_edge_direct_speed_parse_error(self, tmp_path):
        # Passing invalid speed string should catch TypeError/ValueError and succeed using default rate
        mock_comm = MagicMock()
        mock_comm.return_value.save = AsyncMock()
        with patch("edge_tts.Communicate", mock_comm):
            res = audio_proxy._call_edge_direct("hello", speed="invalid-speed", output_dir=tmp_path)
            assert res["status"] == "success"

    def test_edge_direct_loop_error_nest_asyncio_success(self, tmp_path):
        # Raise RuntimeError in asyncio.run to simulate active event loop, then import nest_asyncio
        def mock_run(coro):
            raise RuntimeError("Event loop is already running")

        mock_nest = MagicMock()
        mock_loop = MagicMock()
        mock_comm = MagicMock()
        mock_comm.return_value.save = AsyncMock()

        with (
            patch("asyncio.run", side_effect=mock_run),
            patch.dict("sys.modules", {"nest_asyncio": mock_nest}),
            patch("asyncio.get_event_loop", return_value=mock_loop),
            patch("edge_tts.Communicate", mock_comm),
        ):
            res = audio_proxy._call_edge_direct("hello", output_dir=tmp_path)
            assert res["status"] == "success"
            mock_nest.apply.assert_called_once()
            mock_loop.run_until_complete.assert_called_once()

    def test_edge_direct_loop_error_nest_asyncio_import_error(self, tmp_path):
        def mock_run(coro):
            raise RuntimeError("Event loop already running")

        mock_comm = MagicMock()
        mock_comm.return_value.save = AsyncMock()

        with (
            patch("asyncio.run", side_effect=mock_run),
            patch.dict("sys.modules", {"nest_asyncio": None}),  # Force ImportError
            patch("asyncio.new_event_loop") as mock_new_loop,
            patch("edge_tts.Communicate", mock_comm),
        ):
            mock_loop_inst = MagicMock()
            mock_new_loop.return_value = mock_loop_inst
            res = audio_proxy._call_edge_direct("hello", output_dir=tmp_path)
            assert res["status"] == "success"
            mock_new_loop.assert_called_once()
            mock_loop_inst.run_until_complete.assert_called_once()
            mock_loop_inst.close.assert_called_once()

    def test_edge_direct_pydub_failure_fallback_duration(self, tmp_path):
        mock_comm = MagicMock()
        mock_comm.return_value.save = AsyncMock()
        with (
            patch("edge_tts.Communicate", mock_comm),
            patch("pydub.AudioSegment.from_file", side_effect=Exception("pydub failed")),
        ):
            res = audio_proxy._call_edge_direct(
                "hello this is a moderately long sentence", output_dir=tmp_path
            )
            assert res["status"] == "success"
            # duration fallback: len(text) / 150.0 = 42 / 150 = 0.28
            assert abs(res["duration"] - 0.28) < 0.05

    def test_edge_direct_general_exception(self, tmp_path):
        with patch("edge_tts.Communicate", side_effect=Exception("Communicate crash")):
            res = audio_proxy._call_edge_direct("hello", output_dir=tmp_path)
            assert res["status"] == "error"
            assert "Communicate crash" in res["message"]


# ── F5/OmniVoice Worker Startup Exception Paths ──────────────────────────────


class TestWorkerStartupExceptions:
    def test_f5_worker_missing_script_file(self):
        w = audio_proxy._F5Worker()
        with patch("pathlib.Path.exists", return_value=False):
            res = w._start()
            assert res is False
            assert w._failed is True

    def test_f5_worker_config_load_failure(self):
        w = audio_proxy._F5Worker()

        # Mock script exists, but config load raises exception
        def mock_exists(path_obj):
            return "f5_worker.py" in str(path_obj)

        with (
            patch("pathlib.Path.exists", mock_exists),
            patch("audio.audio_proxy.load_config", side_effect=Exception("config load fail")),
        ):
            res = w._start()
            assert res is False

    def test_f5_worker_resolve_model_path_failure(self):
        w = audio_proxy._F5Worker()

        def mock_exists(path_obj):
            return "f5_worker.py" in str(path_obj)

        mock_f5_worker_mod = MagicMock()
        mock_f5_worker_mod._resolve_model_path.side_effect = Exception("hub resolve fail")

        with (
            patch("pathlib.Path.exists", mock_exists),
            patch.dict("sys.modules", {"audio.f5_worker": mock_f5_worker_mod}),
        ):
            res = w._start()
            assert res is False

    def test_f5_worker_exited_during_startup(self):
        w = audio_proxy._F5Worker()

        def mock_exists(path_obj):
            return True

        fake_proc = MagicMock()
        fake_proc.poll.return_value = 1
        fake_proc.stdout.readline.return_value = ""

        with (
            patch("pathlib.Path.exists", mock_exists),
            patch("subprocess.Popen", return_value=fake_proc),
        ):
            res = w._start()
            assert res is False
            assert w._failed is True

    def test_f5_worker_invalid_readiness_json(self):
        w = audio_proxy._F5Worker()

        def mock_exists(path_obj):
            return True

        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdout.readline.side_effect = ["invalid json line\n", ""]

        with (
            patch("pathlib.Path.exists", mock_exists),
            patch("subprocess.Popen", return_value=fake_proc),
        ):
            res = w._start()
            assert res is False

    def test_f5_worker_error_status_startup(self):
        w = audio_proxy._F5Worker()

        def mock_exists(path_obj):
            return True

        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdout.readline.return_value = '{"status": "error", "message": "init failed"}\n'

        with (
            patch("pathlib.Path.exists", mock_exists),
            patch("subprocess.Popen", return_value=fake_proc),
        ):
            res = w._start()
            assert res is False

    def test_f5_worker_readiness_timeout(self):
        w = audio_proxy._F5Worker()

        def mock_exists(path_obj):
            return True

        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdout.readline.return_value = "\n"

        # Provide plenty of values or function to avoid StopIteration on logging
        with (
            patch("pathlib.Path.exists", mock_exists),
            patch("subprocess.Popen", return_value=fake_proc),
            patch("time.time", side_effect=[100, 500] + [500] * 100),
        ):
            res = w._start()
            assert res is False

    def test_omnivoice_worker_exited_during_startup(self):
        w = audio_proxy._OmniVoiceWorker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = 1
        fake_proc.stdout.readline.return_value = ""

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.Popen", return_value=fake_proc),
        ):
            res = w._start()
            assert res is False
            assert w._failed is True

    def test_omnivoice_worker_invalid_readiness_json(self):
        w = audio_proxy._OmniVoiceWorker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdout.readline.side_effect = ["bad json\n", ""]

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.Popen", return_value=fake_proc),
        ):
            res = w._start()
            assert res is False

    def test_omnivoice_worker_error_status_startup(self):
        w = audio_proxy._OmniVoiceWorker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdout.readline.return_value = '{"status": "error", "message": "init error"}\n'

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.Popen", return_value=fake_proc),
        ):
            res = w._start()
            assert res is False

    def test_omnivoice_worker_readiness_timeout(self):
        w = audio_proxy._OmniVoiceWorker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdout.readline.return_value = "\n"

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.Popen", return_value=fake_proc),
            patch("time.time", side_effect=[100, 500] + [500] * 100),
        ):
            res = w._start()
            assert res is False


class TestAudioProxyExtendedUncovered:
    def test_call_f5_worker_json_errors_and_unparseable(self, tmp_path, monkeypatch):
        """Test one-shot subprocess error handling and JSON parsing in _call_f5_worker."""
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()

        # 1. returncode = 0 but stdout is not JSON
        fake_res = MagicMock()
        fake_res.returncode = 0
        fake_res.stdout = "not-json\n"
        fake_res.stderr = "some logs"

        with (
            patch("audio.audio_proxy.load_config", return_value={"tts": {"f5": {}}}),
            patch.object(audio_proxy._f5_worker, "generate", return_value=None),
            patch("subprocess.run", return_value=fake_res),
        ):
            res = audio_proxy._call_f5_worker("hello", output_dir=tmp_path)
            assert res["status"] == "error"
            assert "some logs" in res["message"]

        # 2. returncode = 0 but invalid JSON braces
        fake_res.stdout = "{invalid-json}\n"
        with (
            patch("audio.audio_proxy.load_config", return_value={"tts": {"f5": {}}}),
            patch.object(audio_proxy._f5_worker, "generate", return_value=None),
            patch("subprocess.run", return_value=fake_res),
        ):
            res = audio_proxy._call_f5_worker("hello", output_dir=tmp_path)
            assert res["status"] == "error"

    def test_tts_generate_unknown_engine_fallback(self, tmp_path):
        """Test that unknown engine falls back to edge-tts."""
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "unknown_engine_name", "lang": "hi"}},
            ),
            patch(
                "audio.audio_proxy._call_edge_direct",
                return_value={"status": "success", "wav_path": str(wav_out)},
            ) as mock_edge,
        ):
            res = audio_proxy.tts_generate("hello", output_dir=tmp_path)
            assert res["wav_path"] == wav_out
            mock_edge.assert_called_once()

    def test_tts_generate_auto_detect_narration_sample_fallback(self, tmp_path, monkeypatch):
        """Test narrator sample fallback path when narration_voice.wav does not exist but other files do."""
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()

        voices_dir = tmp_path / "character_voices"
        voices_dir.mkdir()

        # 1. Try with no files inside character_voices, should return None/empty voice_sample
        captured = {}

        def fake_omni(text, lang, output_dir, voice_sample, speed_override, sentence_gap_ms):
            captured["voice_sample"] = voice_sample
            wav_path = Path(output_dir) / "x.wav"
            wav_path.write_bytes(b"RIFF")
            return {"status": "success", "wav_path": str(wav_path)}

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "omnivoice", "lang": "hi", "voice_profile": {}}},
            ),
            patch("audio.audio_proxy._call_omnivoice_worker", side_effect=fake_omni),
        ):
            audio_proxy.tts_generate("hello", output_dir=tmp_path)
            assert captured["voice_sample"] == ""

        # 2. Try with valid files (should be sorted and ref/mono files excluded)
        ref_file = voices_dir / "voice_ref8s_mono.wav"
        ref_file.write_bytes(b"1")
        valid_file_b = voices_dir / "b_voice.wav"
        valid_file_b.write_bytes(b"2")
        valid_file_a = voices_dir / "a_voice.wav"
        valid_file_a.write_bytes(b"3")

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "omnivoice", "lang": "hi", "voice_profile": {}}},
            ),
            patch("audio.audio_proxy._call_omnivoice_worker", side_effect=fake_omni),
        ):
            audio_proxy.tts_generate("hello", output_dir=tmp_path)
            # Should choose a_voice.wav because of alphabetical sorting and exclusion of ref/mono
            assert "a_voice.wav" in captured["voice_sample"]

    def test_tts_generate_missing_word_timestamps(self, tmp_path):
        """Test that word_timestamps path is cleared to None if it does not exist."""
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "omnivoice", "lang": "hi"}},
            ),
            patch(
                "audio.audio_proxy._call_omnivoice_worker",
                return_value={
                    "status": "success",
                    "wav_path": str(wav_out),
                    "word_timestamps": "nonexistent_words.json",
                },
            ),
        ):
            res = audio_proxy.tts_generate("hello", output_dir=tmp_path)
            assert res["wav_path"] == wav_out
            assert res["word_timestamps"] is None

    def test_tts_generate_degradation_ledger_exceptions(self, tmp_path):
        """Test that UIState degradation logging failures are swallowed gracefully."""
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")

        fake_uis = MagicMock()
        fake_uis.add_degradation.side_effect = Exception("degrade logging failed")

        # 1. Unknown engine -> edge-tts degradation log error
        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "unknown_name", "lang": "hi"}},
            ),
            patch(
                "audio.audio_proxy._call_edge_direct",
                return_value={"status": "success", "wav_path": str(wav_out)},
            ),
            patch("agents.director_agent.UIState", fake_uis),
        ):
            res = audio_proxy.tts_generate("hello", output_dir=tmp_path)
            assert res["wav_path"] == wav_out

        # 2. F5 failure -> omnivoice degradation log error
        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "f5", "lang": "hi"}},
            ),
            patch("audio.audio_proxy._call_f5_worker", return_value={"status": "error"}),
            patch(
                "audio.audio_proxy._call_omnivoice_worker",
                return_value={"status": "success", "wav_path": str(wav_out)},
            ),
            patch("agents.director_agent.UIState", fake_uis),
        ):
            res = audio_proxy.tts_generate("hello", output_dir=tmp_path)
            assert res["wav_path"] == wav_out

    def test_translate_hinglish_markdown_templates(self):
        """Test translate_hinglish markdown extraction and cleanup blocks."""
        audio_proxy._config_cache.clear()
        mock_client = MagicMock()
        # Simulated markdown code blocks
        mock_client.generate.return_value = "```markdown\nयह एक परीक्षण है।\n```"

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "omnivoice", "lang": "hi"}},
            ),
            patch("utils.ollama_client.get_ollama_client", return_value=mock_client),
        ):
            result = audio_proxy.translate_hinglish("hello")
            assert result == "यह एक परीक्षण है।"

    def test_translate_hinglish_empty_translation(self):
        """Test translate_hinglish when LLM client returns empty string."""
        audio_proxy._config_cache.clear()
        mock_client = MagicMock()
        mock_client.generate.return_value = "   "

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "omnivoice", "lang": "hi"}},
            ),
            patch("utils.ollama_client.get_ollama_client", return_value=mock_client),
        ):
            result = audio_proxy.translate_hinglish("original text")
            # Should fall back to original text
            assert result == "original text"

    def test_worker_start_config_load_fail(self):
        """Test that worker start handles config load failures."""
        w = audio_proxy._F5Worker()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("audio.audio_proxy.load_config", side_effect=Exception("Load fail")),
            patch("subprocess.Popen") as mock_popen,
        ):
            w._start()
            # Popen should have been called with default hf_cache model path
            called_cmd = mock_popen.call_args[0][0]
            assert any("f5_worker.py" in x for x in called_cmd)
            assert any("snapshots/main" in x for x in called_cmd)
