import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from audio import supertonic_worker


def test_load_voice_style_builtin(monkeypatch):
    tts = SimpleNamespace(
        set_voice_style=lambda x: None,
        get_voice_style=lambda voice_name=None: None,
    )
    monkeypatch.setattr(supertonic_worker.Path, "exists", lambda _: True)
    result = supertonic_worker._load_voice_style(tts, "M1")
    assert result is None


def test_load_voice_style_custom(monkeypatch, tmp_path):
    voice_file = tmp_path / "voice.json"
    voice_file.write_text('{"voice": "custom"}', encoding="utf-8")
    tts = SimpleNamespace(
        set_voice_style=lambda x: None,
        get_voice_style=lambda voice_name=None: None,
        get_voice_style_from_path=lambda voice_style_path: None,
    )
    monkeypatch.setattr(supertonic_worker.Path, "exists", lambda p: p == voice_file)
    result = supertonic_worker._load_voice_style(tts, str(voice_file))
    assert result is None


def test_load_voice_style_custom_missing(tmp_path):
    tts = SimpleNamespace(
        set_voice_style=lambda x: None,
        get_voice_style=lambda voice_name=None: None,
        get_voice_style_from_path=lambda voice_style_path: None,
    )
    with pytest.raises(FileNotFoundError):
        supertonic_worker._load_voice_style(tts, str(tmp_path / "nonexistent.json"))


# ── _serve protocol tests ─────────────────────────────────────────────────


def _serve_fixture(monkeypatch, tmp_path):
    """Common setup: inject fake supertonic module, mock synth, capture I/O."""
    fake_supertonic = MagicMock()
    fake_supertonic.TTS = MagicMock(return_value=MagicMock(sample_rate=24000))
    monkeypatch.setitem(sys.modules, "supertonic", fake_supertonic)
    monkeypatch.setattr(supertonic_worker, "_synthesize_once", MagicMock(
        return_value={"status": "success", "wav_path": "out.wav", "duration_s": 1.0, "word_timestamps": None}
    ))
    monkeypatch.setattr("sys.stderr", io.StringIO())
    out_lines = []

    def fake_print(*a, **kw):
        if a:
            out_lines.append(a[0])

    monkeypatch.setattr("builtins.print", fake_print)
    return out_lines


def _run_serve(monkeypatch, tmp_path, inputs: list[str]):
    """Feed stdin lines, call _serve(), return (exit_code, stdout_lines)."""
    out_lines = _serve_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(inputs)))
    exit_code = supertonic_worker._serve()
    return exit_code, out_lines


def test_serve_shutdown(monkeypatch, tmp_path):
    exit_code, out_lines = _run_serve(monkeypatch, tmp_path, ['{"cmd": "shutdown"}\n'])
    assert exit_code == 0
    assert any("ready" in l for l in out_lines)
    assert any("shutdown" in l for l in out_lines)


def test_serve_bad_json_then_shutdown(monkeypatch, tmp_path):
    exit_code, out_lines = _run_serve(
        monkeypatch, tmp_path,
        ["bad json\n", '{"cmd": "shutdown"}\n'],
    )
    assert exit_code == 0
    errors = [l for l in out_lines if "bad json" in l.lower()]
    assert any("error" in l for l in errors)
    assert any("shutdown" in l for l in out_lines)


def test_serve_empty_line_skipped(monkeypatch, tmp_path):
    exit_code, out_lines = _run_serve(
        monkeypatch, tmp_path,
        ["\n", "\n", '{"cmd": "shutdown"}\n'],
    )
    assert exit_code == 0
    assert any("shutdown" in l for l in out_lines)


def test_serve_synthesize_then_shutdown(monkeypatch, tmp_path):
    out_lines = _serve_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(supertonic_worker.Path, "exists", lambda p: True)
    inputs = (
        json.dumps({"text": "hello", "output": str(tmp_path / "test.wav")}) + "\n"
        + '{"cmd": "shutdown"}\n'
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(inputs))
    exit_code = supertonic_worker._serve()
    assert exit_code == 0
    success_lines = [l for l in out_lines if '"status": "success"' in l]
    assert len(success_lines) >= 1
