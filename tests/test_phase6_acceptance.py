import json
from pathlib import Path

from PIL import Image

from scripts.phase6_acceptance import (
    WHEEL_NAME,
    approve_output,
    create_background,
    sha256_file,
    validate_output,
    validate_static_files,
)


def test_wheel_matches_comfy_python_platform():
    assert "cp312-cp312-win_amd64" in WHEEL_NAME
    assert "cu12.8torch2.9" in WHEEL_NAME


def test_sha256_file_streams_correct_digest(tmp_path: Path):
    path = tmp_path / "sample.bin"
    path.write_bytes(b"phase6")
    assert sha256_file(path) == "80e80cdf47a41f3ae0d34a0816593b6f723dbc586cb8459cdcb26cf323287154"


def test_background_is_deterministic(tmp_path: Path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    assert create_background(first) == create_background(second)
    assert Image.open(first).size == (768, 768)


def test_output_validation_rejects_input_copy(tmp_path: Path):
    background = tmp_path / "background.png"
    portrait = tmp_path / "portrait.png"
    output = tmp_path / "output.png"
    create_background(background)
    Image.new("RGB", (768, 768), "black").save(portrait)
    output.write_bytes(background.read_bytes())

    try:
        validate_output(output, background, portrait)
    except RuntimeError as error:
        assert "identical" in str(error)
    else:
        raise AssertionError("copied input was accepted")


def test_static_files_validate_committed_workflow():
    report = validate_static_files()
    assert report["ok"], json.dumps(report, indent=2)


def test_visual_approval_updates_existing_run_without_inference(tmp_path: Path, monkeypatch):
    from scripts import phase6_acceptance

    evidence = tmp_path / "evidence"
    run_dir = evidence / "run"
    run_dir.mkdir(parents=True)
    background = run_dir / "phase6_background_run.png"
    output = run_dir / "qwen_edit.png"
    portrait = tmp_path / "portrait.png"
    create_background(background)
    Image.new("RGB", (768, 768), "black").save(portrait)
    edited = Image.open(background).convert("RGB")
    for x in range(300, 468):
        for y in range(180, 620):
            edited.putpixel((x, y), (20, 40, 80))
    edited.save(output)
    (run_dir / "report.json").write_text(
        json.dumps({"status": "technical_pass_visual_pending", "output": {"path": str(output)}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(phase6_acceptance, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(phase6_acceptance, "CHARACTER_PATH", portrait)

    assert approve_output(run_dir) == output
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["visual_approved"] is True
