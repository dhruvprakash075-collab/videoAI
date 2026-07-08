from types import SimpleNamespace

from audio import indicf5_worker


def test_indicf5_recovers_misplaced_wav(tmp_path, monkeypatch):
    root = tmp_path / "indic"
    root.mkdir()
    (root / "run_indic.py").write_text("", encoding="utf-8")
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    output = tmp_path / "out" / "wanted.wav"

    def fake_run(*args, **kwargs):
        misplaced = output.parent / "ignored_name.wav"
        misplaced.write_bytes(b"RIFF")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(indicf5_worker.subprocess, "run", fake_run)

    result = indicf5_worker.generate("नमस्ते", output, root, "python", ref, "ref text", 1)

    assert result == {"status": "success", "wav_path": str(output)}
    assert output.exists()
