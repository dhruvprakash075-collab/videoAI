"""test_tts_alignment.py - Tests for the TTS->alignment->renderer flow.

Two test layers:
1. align_audio() unit tests (NEW Phase 0.5.5b) - the worker-side helper
2. assembler._write_srt() tests (Phase 0.5.5 originally) - the renderer-side
   consumer; verifies the word_timestamps_json path is honored
"""
import json
from contextlib import suppress
from pathlib import Path
from unittest.mock import MagicMock, patch

from video.renderer import assembler


def test_align_audio_writes_words_json_next_to_wav(tmp_path):
    """align_audio() writes {wav}.words.json with the right structure."""
    from audio.tts_alignment import align_audio

    wav = tmp_path / "seg.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 100)

    fake_word = MagicMock()
    fake_word.word = "hello"
    fake_word.start = 0.0
    fake_word.end = 0.5
    fake_seg = MagicMock()
    fake_seg.words = [fake_word]
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter([fake_seg]), MagicMock())

    with patch("audio.tts_alignment._get_alignment_model", return_value=fake_model):
        result = align_audio(wav, model_name="base")

    assert result == wav.with_suffix(".words.json")
    assert result.exists()
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data == [{"word": "hello", "start": 0.0, "end": 0.5}]


def test_align_audio_returns_none_if_wav_missing(tmp_path):
    """align_audio() must NOT raise when the WAV is missing - returns None."""
    from audio.tts_alignment import align_audio
    assert align_audio(tmp_path / "nope.wav") is None


def test_align_audio_returns_none_on_whisper_failure(tmp_path):
    """align_audio() must NOT raise when faster-whisper fails - returns None."""
    from audio.tts_alignment import align_audio
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"x")

    fake_model = MagicMock()
    fake_model.transcribe.side_effect = RuntimeError("boom")
    with patch("audio.tts_alignment._get_alignment_model", return_value=fake_model):
        assert align_audio(wav) is None


def test_alignment_model_is_cached_across_calls():
    """Module-level cache: same model name should be reused without re-loading.

    We patch the WhisperModel constructor to count how many times it is called
    and verify the module-level cache short-circuits the second call.
    """
    from audio import tts_alignment

    tts_alignment._alignment_model = None
    tts_alignment._alignment_model_name = None

    fake_model = MagicMock()
    constructor_calls = {"count": 0}

    def fake_constructor(model_name, device, compute_type):
        constructor_calls["count"] += 1
        return fake_model

    with patch.dict("sys.modules", {"faster_whisper": MagicMock(WhisperModel=fake_constructor)}):
        m1 = tts_alignment._get_alignment_model("base", "cpu", "int8")
        m2 = tts_alignment._get_alignment_model("base", "cpu", "int8")
        assert m1 is m2
        assert constructor_calls["count"] == 1, (
            f"WhisperModel constructor should run once; got {constructor_calls['count']}"
        )


def test_tts_worker_source_contains_word_timestamps_key():
    """Regression: the TTS worker's success JSON must include the word_timestamps
    key (value may be null), so audio_proxy.py:898 doesn't fall through to Whisper."""
    src = Path("audio/omnivoice_worker.py").read_text(encoding="utf-8")
    assert '"word_timestamps"' in src, "omnivoice_worker.py must emit 'word_timestamps' key in success JSON"
    src2 = Path("audio/f5_worker.py").read_text(encoding="utf-8")
    assert '"word_timestamps"' in src2, "f5_worker.py must emit 'word_timestamps' key in success JSON"


def test_assembler_uses_word_timestamps_json_without_whisper(tmp_path, monkeypatch):
    word_ts = [
        {"word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.5, "end": 1.0},
    ]
    words_json = tmp_path / "seg.words.json"
    words_json.write_text(json.dumps(word_ts), encoding="utf-8")

    srt_path = tmp_path / "seg.srt"

    def _boom(*args, **kwargs):
        raise AssertionError("Whisper fallback should not be invoked when word_timestamps_json exists")

    monkeypatch.setattr(assembler, "_get_whisper_model", _boom)

    assembler._write_srt(
        script="Hello world.",
        path=srt_path,
        duration=1.0,
        audio=None,
        format_style="classic",
        word_timestamps_json=words_json,
        is_final=True,
    )

    assert srt_path.exists()
    content = srt_path.read_text(encoding="utf-8-sig")
    assert "Hello world" in content
    assert "-->" in content


def test_assembler_fallbacks_to_proportional_when_word_json_empty(tmp_path):
    words_json = tmp_path / "empty.words.json"
    words_json.write_text("[]", encoding="utf-8")

    srt_path = tmp_path / "seg.srt"

    assembler._write_srt(
        script="One. Two.",
        path=srt_path,
        duration=2.0,
        audio=None,
        format_style="classic",
        word_timestamps_json=words_json,
        is_final=True,
    )

    assert srt_path.exists()
    content = srt_path.read_text(encoding="utf-8-sig")
    assert "One." in content or "One Two" in content or "One Two." in content
    assert "-->" in content


def test_assembler_regression_warning_emits_when_word_json_missing(tmp_path, monkeypatch, caplog):
    dummy_audio = tmp_path / "seg.wav"
    dummy_audio.write_bytes(b"")  # exists() is all assembler needs for this test

    words_json = tmp_path / "missing.words.json"  # doesn't exist

    def _boom(*args, **kwargs):
        raise AssertionError("Whisper should not be reached in this test; it will boom to stop execution")

    monkeypatch.setattr(assembler, "_get_whisper_model", _boom)

    caplog.set_level("WARNING")
    srt_path = tmp_path / "seg.srt"

    assembler._write_srt(
        script="Hello world.",
        path=srt_path,
        duration=1.0,
        audio=dummy_audio,
        format_style="classic",
        word_timestamps_json=words_json,
        is_final=True,
    )

    assert any("REGRESSION: Whisper fallback fired" in rec.message for rec in caplog.records)


def test_assembler_does_not_warn_when_word_json_present(tmp_path, monkeypatch, caplog):
    dummy_audio = tmp_path / "seg.wav"
    dummy_audio.write_bytes(b"")

    word_ts = [{"word": "Hello", "start": 0.0, "end": 0.5}]
    words_json = tmp_path / "seg.words.json"
    words_json.write_text(json.dumps(word_ts), encoding="utf-8")

    def _boom(*args, **kwargs):
        raise AssertionError("Whisper should not be invoked when word timestamps JSON exists")

    monkeypatch.setattr(assembler, "_get_whisper_model", _boom)

    caplog.set_level("WARNING")
    srt_path = tmp_path / "seg.srt"

    assembler._write_srt(
        script="Hello.",
        path=srt_path,
        duration=1.0,
        audio=dummy_audio,
        format_style="classic",
        word_timestamps_json=words_json,
        is_final=True,
    )

    assert srt_path.exists()
    assert not any("REGRESSION: Whisper fallback fired" in rec.message for rec in caplog.records)


def test_proxy_to_renderer_chain_no_regression_warning_when_word_json_exists(tmp_path, monkeypatch, caplog):
    dummy_audio = tmp_path / "seg.wav"
    dummy_audio.write_bytes(b"")

    words_json = tmp_path / "seg.words.json"
    words_json.write_text(json.dumps([
        {"word": "A", "start": 0.0, "end": 0.4},
        {"word": "B", "start": 0.4, "end": 0.8},
    ]), encoding="utf-8")

    def _boom(*args, **kwargs):
        raise AssertionError("Whisper should not be invoked when chain supplies word_timestamps JSON")

    monkeypatch.setattr(assembler, "_get_whisper_model", _boom)

    caplog.set_level("WARNING")
    srt_path = tmp_path / "seg.srt"

    # Simulate the 'chain' contract:
    # pipeline passes word_timestamps_json into assembler; if present, assembler must not run Whisper.
    assembler._write_srt(
        script="A B.",
        path=srt_path,
        duration=1.0,
        audio=dummy_audio,
        format_style="classic",
        word_timestamps_json=words_json,
        is_final=True,
    )

    assert srt_path.exists()
    assert not any("REGRESSION: Whisper fallback fired" in rec.message for rec in caplog.records)


def test_proxy_to_renderer_chain_warns_when_word_json_missing(tmp_path, monkeypatch, caplog):
    dummy_audio = tmp_path / "seg.wav"
    dummy_audio.write_bytes(b"")

    # Simulate missing word_timestamps_json (pipeline did not provide it)
    words_json = tmp_path / "does_not_exist.words.json"

    def _boom(*args, **kwargs):
        raise AssertionError("Whisper should not be called before regression warning; this will stop execution")

    monkeypatch.setattr(assembler, "_get_whisper_model", _boom)

    caplog.set_level("WARNING")
    srt_path = tmp_path / "seg.srt"

    with suppress(AssertionError):
        assembler._write_srt(
            script="Hello.",
            path=srt_path,
            duration=1.0,
            audio=dummy_audio,
            format_style="classic",
            word_timestamps_json=words_json,
            is_final=True,
        )

    assert any("REGRESSION: Whisper fallback fired" in rec.message for rec in caplog.records)
