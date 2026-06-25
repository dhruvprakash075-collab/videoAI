"""Additional tests for supported audio_proxy paths."""

from unittest.mock import MagicMock, patch

from audio import audio_proxy


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
            patch("audio.audio_proxy._call_omnivoice_oneshot", return_value=fake_oneshot) as oneshot,
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


class TestCallOmnivoiceOneshot:
    def test_success_parses_json_from_stdout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out_wav = tmp_path / "out.wav"
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


class TestShutdownFunctions:
    def test_shutdown_omnivoice_worker_no_crash(self):
        with patch.object(audio_proxy._omnivoice_worker, "shutdown") as sh:
            audio_proxy.shutdown_omnivoice_worker()
        sh.assert_called_once()

    def test_omnivoice_worker_shutdown_with_live_proc(self):
        w = audio_proxy._OmniVoiceWorker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdin = MagicMock()
        w._proc = fake_proc

        w.shutdown()
        fake_proc.stdin.write.assert_called()


class TestTtsGenerateVoiceSample:
    def test_auto_detects_narration_voice_wav(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")

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

        assert "narration_voice" in captured["voice_sample"]

    def test_ref_only_files_excluded_from_auto_detect(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")

        voices_dir = tmp_path / "character_voices"
        voices_dir.mkdir()
        ref_file = voices_dir / "voice_ref8s_mono.wav"
        ref_file.write_bytes(b"WAV")

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

        assert captured["voice_sample"] == ""


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

    def test_speed_passed_to_supertonic(self, tmp_path):
        audio_proxy._config_cache.clear()
        wav_out = tmp_path / "x.wav"
        wav_out.write_bytes(b"RIFF")
        captured = {}

        def fake_super(text, lang, output_dir, speed_override):
            captured["speed"] = speed_override
            return {"status": "success", "wav_path": str(wav_out)}

        with (
            patch(
                "audio.audio_proxy.load_config",
                return_value={"tts": {"engine": "supertonic", "lang": "hi", "voice_profile": {}}},
            ),
            patch("audio.audio_proxy._call_supertonic_worker", side_effect=fake_super),
        ):
            audio_proxy.tts_generate("hello", speed=1.1, output_dir=tmp_path)

        assert abs(captured["speed"] - 1.1) < 0.01


class TestWorkerStartupExceptions:
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

    def test_omnivoice_worker_error_status_startup(self):
        w = audio_proxy._OmniVoiceWorker()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        # ponytail: side_effect returns one line then EOF so _enqueue_stdout thread
        # terminates instead of looping forever and starving the next test's CPU.
        fake_proc.stdout.readline.side_effect = ['{"status": "error", "message": "init error"}\n', ""]

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.Popen", return_value=fake_proc),
        ):
            res = w._start()
            assert res is False


class TestOmnivoiceSynthesizeSentenceGap:
    def test_synthesize_sentence_gap_ms_handling(self, tmp_path, monkeypatch):
        import sys
        from unittest.mock import MagicMock, patch

        # Stub torch and torchaudio before importing audio.omnivoice_worker
        monkeypatch.setitem(sys.modules, "torch", MagicMock())
        monkeypatch.setitem(sys.modules, "torchaudio", MagicMock())

        import numpy as np
        import soundfile as sf

        from audio.omnivoice_worker import _synthesize

        # Mock the OmniVoice model
        mock_model = MagicMock()
        mock_model.sample_rate = 24000

        # model.generate returns a fresh 1D numpy array of size 24000 (1 second) per call
        mock_model.generate.side_effect = lambda **kw: [np.ones(24000, dtype=np.float32)]

        # We synthesize a text with 2 sentences so it splits into 2 chunks
        text = "Hello. World."

        with (
            patch("audio.omnivoice_worker._gen_config", return_value=MagicMock()),
            patch("audio.omnivoice_worker._split_text_chunks", return_value=["Hello.", "World."]),
        ):
            # Case 1: sentence_gap_ms=0 (no crossfade, output should be 48000 samples)
            out_path_0 = tmp_path / "out_0.wav"
            _synthesize(mock_model, text, str(out_path_0), sentence_gap_ms=0)
            audio_0, sr_0 = sf.read(str(out_path_0))
            assert sr_0 == 24000
            assert len(audio_0) == 48000

            # Case 2: sentence_gap_ms=200 (default, 200ms at 24000Hz is 4800 samples)
            # 48000 - 4800 = 43200 samples
            out_path_200 = tmp_path / "out_200.wav"
            _synthesize(mock_model, text, str(out_path_200), sentence_gap_ms=200)
            audio_200, _sr_200 = sf.read(str(out_path_200))
            assert len(audio_200) == 43200

            # Case 3: sentence_gap_ms=None (default to 200, so 43200 samples)
            out_path_none = tmp_path / "out_none.wav"
            _synthesize(mock_model, text, str(out_path_none), sentence_gap_ms=None)
            audio_none, _sr_none = sf.read(str(out_path_none))
            assert len(audio_none) == 43200

            # Case 4: sentence_gap_ms=-50 (negative, should clamp to 0, so 48000 samples)
            out_path_neg = tmp_path / "out_neg.wav"
            _synthesize(mock_model, text, str(out_path_neg), sentence_gap_ms=-50)
            audio_neg, _sr_neg = sf.read(str(out_path_neg))
            assert len(audio_neg) == 48000



