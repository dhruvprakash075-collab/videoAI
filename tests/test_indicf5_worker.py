from pathlib import Path
from types import SimpleNamespace

from audio import indicf5_worker


def test_indicf5_recovers_misplaced_wav(tmp_path, monkeypatch):
    root = tmp_path / "indic"
    root.mkdir()
    (root / "run_indic.py").write_text("", encoding="utf-8")
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    output = tmp_path / "out" / "wanted.wav"

    def fake_run(cmd, **kwargs):
        batch_dir = Path(cmd[4]).parent
        assert batch_dir != output.parent, "batch must live in an isolated temp dir"
        misplaced = batch_dir / "ignored_name.wav"
        misplaced.write_bytes(b"RIFF")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(indicf5_worker.subprocess, "run", fake_run)

    result = indicf5_worker.generate("नमस्ते", output, root, "python", ref, "ref text", 1)

    assert result == {"status": "success", "wav_path": str(output)}
    assert output.exists()


def test_indicf5_batch_line_stays_single_line_with_trailing_newline(tmp_path, monkeypatch):
    """Trailing \n in the text must not split the batch line — the runner's
    rsplit('|', 1) turns a second line into 'bad batch line' + empty text."""
    root = tmp_path / "indic"
    root.mkdir()
    (root / "run_indic.py").write_text("", encoding="utf-8")
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    output = tmp_path / "out" / "wanted.wav"
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["lines"] = Path(cmd[4]).read_text(encoding="utf-8").splitlines()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(indicf5_worker.subprocess, "run", fake_run)

    indicf5_worker.generate("नमस्ते दोस्तों\n", output, root, "python", ref, "ref text", 1)

    assert len(captured["lines"]) == 1
    assert captured["lines"][0] == f"नमस्ते दोस्तों|{output}"
