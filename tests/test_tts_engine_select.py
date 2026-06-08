"""test_tts_engine_select.py - T1: F5-TTS engine normalization and dispatch."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# normalize_tts_engine
# ---------------------------------------------------------------------------


def test_f5_aliases_normalize_to_f5():
    from audio.audio_proxy import normalize_tts_engine

    for alias in ("f5", "f5-tts", "f5tts", "f5_tts", "F5", "F5-TTS"):
        assert normalize_tts_engine(alias) == "f5", f"Expected 'f5' for alias {alias!r}"


def test_omnivoice_aliases_still_work():
    from audio.audio_proxy import normalize_tts_engine

    for alias in ("omnivoice", "omni", "voice_clone", "clone"):
        assert normalize_tts_engine(alias) == "omnivoice"


def test_edge_aliases_still_work():
    from audio.audio_proxy import normalize_tts_engine

    for alias in ("edge", "edge-tts", "edge_tts", "microsoft", "chattts"):
        assert normalize_tts_engine(alias) == "edge"


def test_unknown_engine_defaults_to_f5():
    """Unknown strings should now default to 'f5' (was 'omnivoice' before T1)."""
    from audio.audio_proxy import normalize_tts_engine

    assert normalize_tts_engine("some random voice description") == "f5"
    assert normalize_tts_engine("xtts") == "f5"


def test_non_string_defaults_to_f5():
    from audio.audio_proxy import normalize_tts_engine

    assert normalize_tts_engine(None) == "f5"
    assert normalize_tts_engine(42) == "f5"


# ---------------------------------------------------------------------------
# tts_generate dispatch — F5 worker mocked
# ---------------------------------------------------------------------------


def test_tts_generate_calls_f5_when_engine_is_f5(monkeypatch, tmp_path):
    """When engine='f5', tts_generate should call _call_f5_worker."""
    import audio.audio_proxy as ap

    called = []
    fake_wav = tmp_path / "out.wav"
    fake_wav.write_bytes(b"RIFF")  # minimal stub

    def _fake_f5(text, lang="hi", output_dir=None, voice_sample="", speed_override=None):
        called.append("f5")
        return {"status": "success", "wav_path": str(fake_wav)}

    monkeypatch.setattr(ap, "_call_f5_worker", _fake_f5)
    monkeypatch.setattr(
        ap,
        "_get_config",
        lambda: {
            "tts": {"engine": "f5", "lang": "hi", "voice_profile": {}, "edge": {}, "f5": {}},
        },
    )

    result = ap.tts_generate("Hello world", output_dir=tmp_path)
    assert "f5" in called
    assert result["wav_path"] == fake_wav


def test_tts_generate_f5_failure_falls_back_to_omnivoice(monkeypatch, tmp_path):
    """When F5 fails, tts_generate should fall back to omnivoice."""
    import audio.audio_proxy as ap

    fallback_wav = tmp_path / "omni.wav"
    fallback_wav.write_bytes(b"RIFF")

    monkeypatch.setattr(
        ap, "_call_f5_worker", lambda *a, **kw: {"status": "error", "message": "F5 not installed"}
    )

    omni_called = []

    def _fake_omni(
        text, lang="hi", output_dir=None, voice_sample="", speed_override=None, sentence_gap_ms=None
    ):
        omni_called.append(True)
        return {"status": "success", "wav_path": str(fallback_wav)}

    monkeypatch.setattr(ap, "_call_omnivoice_worker", _fake_omni)
    monkeypatch.setattr(
        ap,
        "_get_config",
        lambda: {
            "tts": {"engine": "f5", "lang": "hi", "voice_profile": {}, "edge": {}, "f5": {}},
        },
    )

    result = ap.tts_generate("Hello world", output_dir=tmp_path)
    assert omni_called, "omnivoice fallback was not called"
    assert result["wav_path"] == fallback_wav


def test_tts_generate_f5_and_omni_failure_falls_back_to_edge(monkeypatch, tmp_path):
    """When both F5 and omnivoice fail, tts_generate should fall back to edge."""
    import audio.audio_proxy as ap

    edge_wav = tmp_path / "edge.wav"
    edge_wav.write_bytes(b"RIFF")

    monkeypatch.setattr(
        ap, "_call_f5_worker", lambda *a, **kw: {"status": "error", "message": "no F5"}
    )
    monkeypatch.setattr(
        ap, "_call_omnivoice_worker", lambda *a, **kw: {"status": "error", "message": "no omni"}
    )

    edge_called = []

    def _fake_edge(text, lang="hi", output_dir=None, voice_profile=None, speed=None):
        edge_called.append(True)
        return {"status": "success", "wav_path": str(edge_wav)}

    monkeypatch.setattr(ap, "_call_edge_direct", _fake_edge)
    monkeypatch.setattr(
        ap,
        "_get_config",
        lambda: {
            "tts": {"engine": "f5", "lang": "hi", "voice_profile": {}, "edge": {}, "f5": {}},
        },
    )

    result = ap.tts_generate("Hello world", output_dir=tmp_path)
    assert edge_called, "edge fallback was not called"
    assert result["wav_path"] == edge_wav
