import json
import subprocess
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from scripts import phase6_acceptance
from scripts.phase6_acceptance import (
    AcceptanceError,
    FirewallBlock,
    MemorySample,
    Watchdog,
    WHEEL_NAME,
    approve_output,
    build_config,
    create_background,
    fetch_json,
    gpu_compute_processes,
    parse_args,
    quote_ps,
    require_resource_headroom,
    run_command,
    scan_startup_log,
    sha256_file,
    start_comfy,
    stop_process_tree,
    install_matching_nunchaku,
    prove_public_network_blocked,
    directory_snapshot,
    prepare_identity,
    validate_live_workflow,
    validate_output,
    validate_static_files,
    wait_for_comfy,
    _comfyui_is_running,
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


def test_run_command_success_and_checked_failure(monkeypatch):
    ok = subprocess.CompletedProcess(["cmd"], 0, stdout="ok", stderr="")
    bad = subprocess.CompletedProcess(["cmd"], 2, stdout="", stderr="bad")
    monkeypatch.setattr(phase6_acceptance.subprocess, "run", MagicMock(return_value=ok))
    assert run_command(["cmd"], timeout=1).stdout == "ok"

    phase6_acceptance.subprocess.run.return_value = bad
    with pytest.raises(AcceptanceError, match="Command failed"):
        run_command(["cmd"], timeout=1)
    assert run_command(["cmd"], timeout=1, check=False).returncode == 2


def test_small_helpers_and_fetch_json(monkeypatch, tmp_path):
    assert quote_ps("a'b") == "'a''b'"
    monkeypatch.setattr(
        phase6_acceptance,
        "run_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="123\n\n456\n", stderr=""),
    )
    assert phase6_acceptance.gpu_memory_mib() == 123
    assert gpu_compute_processes() == ["123", "456"]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true}'

    monkeypatch.setattr(phase6_acceptance.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    assert fetch_json("http://local") == {"ok": True}

    log = tmp_path / "stderr.log"
    log.write_text("CUDA out of memory", encoding="utf-8")
    with pytest.raises(AcceptanceError, match="startup log contains errors"):
        scan_startup_log(log)
    scan_startup_log(tmp_path / "missing.log")


def test_require_resource_headroom_success_and_failures(monkeypatch):
    monkeypatch.setattr(
        phase6_acceptance,
        "memory_sample",
        lambda: MemorySample(available_ram_gib=8, commit_headroom_gib=30, commit_percent=20),
    )
    monkeypatch.setattr(phase6_acceptance.shutil, "disk_usage", lambda _root: SimpleNamespace(free=20 * 1024**3))
    monkeypatch.setattr(phase6_acceptance, "gpu_memory_mib", lambda: 10)
    monkeypatch.setattr(phase6_acceptance, "gpu_compute_processes", lambda: [])

    report = require_resource_headroom()
    assert report["available_ram_gib"] == 8
    assert report["baseline_gpu_mib"] == 10

    monkeypatch.setattr(
        phase6_acceptance,
        "memory_sample",
        lambda: MemorySample(available_ram_gib=1, commit_headroom_gib=1, commit_percent=95),
    )
    monkeypatch.setattr(phase6_acceptance.shutil, "disk_usage", lambda _root: SimpleNamespace(free=1))
    monkeypatch.setattr(phase6_acceptance, "gpu_memory_mib", lambda: 999)
    monkeypatch.setattr(phase6_acceptance, "gpu_compute_processes", lambda: ["python.exe"])
    monkeypatch.setattr(phase6_acceptance.urllib.request, "urlopen", MagicMock(side_effect=RuntimeError("down")))

    with pytest.raises(AcceptanceError, match="Resource preflight failed"):
        require_resource_headroom()


def test_firewall_block_skip_create_and_verify_failure(monkeypatch):
    monkeypatch.setattr(phase6_acceptance, "is_admin", lambda: False)
    with FirewallBlock() as fw:
        assert fw.created is False

    calls = []
    monkeypatch.setattr(phase6_acceptance, "is_admin", lambda: True)
    monkeypatch.setattr(phase6_acceptance, "powershell", lambda script, **kwargs: calls.append((script, kwargs)))
    with FirewallBlock() as fw:
        assert fw.created is True
    assert any("New-NetFirewallRule" in call[0] for call in calls)
    assert any("Remove-NetFirewallRule" in call[0] for call in calls)

    def fail_verify(script, **kwargs):
        calls.append((script, kwargs))
        if "Get-NetFirewallRule" in script:
            raise RuntimeError("verify")

    monkeypatch.setattr(phase6_acceptance, "powershell", fail_verify)
    with pytest.raises(RuntimeError, match="verify"):
        FirewallBlock().__enter__()


def test_wait_for_comfy_and_stop_process_tree(monkeypatch):
    process = MagicMock()
    process.poll.return_value = None
    monkeypatch.setattr(phase6_acceptance, "fetch_json", MagicMock(return_value={"ready": True}))
    assert wait_for_comfy(process, timeout=1) == {"ready": True}

    process.poll.return_value = 3
    process.returncode = 3
    with pytest.raises(AcceptanceError, match="exited during startup"):
        wait_for_comfy(process, timeout=1)

    stop_process_tree(None)
    done = MagicMock()
    done.poll.return_value = 0
    stop_process_tree(done)
    done.wait.assert_not_called()

    running = MagicMock(pid=123)
    running.poll.return_value = None
    running.wait.side_effect = subprocess.TimeoutExpired("taskkill", 1)
    monkeypatch.setattr(phase6_acceptance, "run_command", MagicMock())
    stop_process_tree(running)
    running.kill.assert_called_once()


def test_parse_args_and_main_dispatch(monkeypatch, tmp_path):
    assert parse_args(["--run"]).run is True
    assert parse_args(["--install-nunchaku"]).install_nunchaku is True
    run_dir = tmp_path / "run"

    monkeypatch.setattr(phase6_acceptance, "install_matching_nunchaku", MagicMock())
    monkeypatch.setattr(phase6_acceptance, "package_version", lambda *_args: "1.2.1")
    assert phase6_acceptance.main(["--install-nunchaku"]) == 0

    monkeypatch.setattr(phase6_acceptance, "run_acceptance", lambda: tmp_path / "out.png")
    assert phase6_acceptance.main(["--run"]) == 0

    monkeypatch.setattr(phase6_acceptance, "approve_output", lambda path: path / "qwen_edit.png")
    assert phase6_acceptance.main(["--approve-output", str(run_dir)]) == 0

    monkeypatch.setattr(phase6_acceptance, "static_report", lambda: {"ok": True})
    assert phase6_acceptance.main([]) == 0

    monkeypatch.setattr(phase6_acceptance, "static_report", MagicMock(side_effect=RuntimeError("closed")))
    assert phase6_acceptance.main([]) == 1


def test_build_config_and_static_report(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("image_gen:\n  comfyui:\n    old: true\n", encoding="utf-8")
    monkeypatch.setattr(phase6_acceptance, "CONFIG_PATH", config_path)
    monkeypatch.setattr(phase6_acceptance, "COMFY_ROOT", tmp_path / "ComfyUI")
    monkeypatch.setattr(phase6_acceptance, "COMFY_PYTHON", tmp_path / "python.exe")
    monkeypatch.setattr(phase6_acceptance, "WORKFLOW_PATH", tmp_path / "workflow.json")
    monkeypatch.setattr(phase6_acceptance, "MODEL_FILES", {"diffusion": tmp_path / "model.safetensors"})

    cfg = build_config(tmp_path / "run")
    assert cfg["image_gen"]["comfyui"]["auto_start"] is False
    assert cfg["image_gen"]["qwen_edit"]["enabled"] is True
    assert cfg["image_gen"]["qwen_edit"]["cache_dir"].endswith("cache")

    monkeypatch.setattr(phase6_acceptance, "validate_static_files", lambda: {"ok": True})
    monkeypatch.setattr(phase6_acceptance, "package_version", lambda *_args: "1.2.1")
    monkeypatch.setattr(phase6_acceptance, "memory_sample", lambda: MemorySample(8, 30, 20))
    monkeypatch.setattr(phase6_acceptance, "gpu_memory_mib", lambda: 1)
    monkeypatch.setattr(phase6_acceptance, "is_admin", lambda: False)
    phase6_acceptance.COMFY_PYTHON.parent.mkdir(parents=True, exist_ok=True)
    phase6_acceptance.COMFY_PYTHON.write_text("", encoding="utf-8")

    report = phase6_acceptance.static_report()
    assert report["ok"] is True
    assert report["nunchaku"] == "1.2.1"


def test_start_comfy_success_and_popen_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(phase6_acceptance, "COMFY_ROOT", tmp_path)
    monkeypatch.setattr(phase6_acceptance, "COMFY_PYTHON", tmp_path / "python.exe")
    process = MagicMock()
    monkeypatch.setattr(phase6_acceptance.subprocess, "Popen", MagicMock(return_value=process))

    proc, stdout, stderr = start_comfy(tmp_path)
    stdout.close()
    stderr.close()
    assert proc is process
    assert phase6_acceptance.subprocess.Popen.call_args.kwargs["cwd"] == str(tmp_path)

    monkeypatch.setattr(phase6_acceptance.subprocess, "Popen", MagicMock(side_effect=RuntimeError("nope")))
    with pytest.raises(RuntimeError, match="nope"):
        start_comfy(tmp_path)


def test_live_workflow_and_comfy_running(monkeypatch, tmp_path):
    monkeypatch.setattr(phase6_acceptance, "fetch_json", MagicMock(return_value={"ok": True}))
    assert _comfyui_is_running() is True
    phase6_acceptance.fetch_json.side_effect = RuntimeError("down")
    assert _comfyui_is_running() is False

    monkeypatch.setattr(
        "video.image_gen.qwen_repose._patch_qwen_workflow",
        MagicMock(return_value={"1": {"class_type": "NunchakuQwenImageDiTLoader", "inputs": {"x": 1}}}),
    )
    object_info = {
        "NunchakuQwenImageDiTLoader": {"input": {"required": {"x": ["INT"]}}},
        "NunchakuZImageDiTLoader": {"input": {"required": {}}},
    }
    validate_live_workflow(object_info, {"image_gen": {"qwen_edit": {}}}, tmp_path / "out.png")

    with pytest.raises(AcceptanceError, match="Live workflow schema failed"):
        validate_live_workflow({}, {"image_gen": {"qwen_edit": {}}}, tmp_path / "out.png")


def test_watchdog_samples_and_violation(monkeypatch, tmp_path):
    class Nvidia:
        stdout = iter(["bad\n", "100\n", "9999\n"])

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    process = MagicMock()
    watchdog = Watchdog(process, tmp_path / "metrics.csv")
    watchdog._nvidia = Nvidia()
    monkeypatch.setattr(phase6_acceptance, "memory_sample", lambda: MemorySample(8, 30, 20))
    monkeypatch.setattr(phase6_acceptance, "stop_process_tree", MagicMock())

    watchdog._run()

    assert watchdog.peak_gpu_mib == 9999
    assert "GPU memory reached" in watchdog.violation
    assert (tmp_path / "metrics.csv").read_text(encoding="utf-8").splitlines()[0].startswith("timestamp")
    phase6_acceptance.stop_process_tree.assert_called_once_with(process)

    waiting = Watchdog(process, tmp_path / "never.csv")
    with pytest.raises(AcceptanceError, match="did not produce"):
        waiting.wait_ready(timeout=0)


def test_approve_output_rejects_bad_run_dirs(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    run_dir = evidence / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(phase6_acceptance, "EVIDENCE_ROOT", evidence)

    with pytest.raises(AcceptanceError, match="inside evidence"):
        approve_output(tmp_path / "outside")

    with pytest.raises(FileNotFoundError):
        approve_output(run_dir)

    (run_dir / "report.json").write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    with pytest.raises(AcceptanceError, match="not awaiting visual approval"):
        approve_output(run_dir)


def test_install_matching_nunchaku_rejects_bad_environment_and_missing_asset(monkeypatch, tmp_path):
    monkeypatch.setattr(
        phase6_acceptance,
        "run_command",
        MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout="3.11|2.8|12.1", stderr="")),
    )
    with pytest.raises(AcceptanceError, match="Unsupported ComfyUI environment"):
        install_matching_nunchaku(tmp_path)

    phase6_acceptance.run_command.return_value = subprocess.CompletedProcess([], 0, stdout="3.12|2.9.1|12.8", stderr="")
    monkeypatch.setattr(phase6_acceptance, "fetch_json", lambda *_args, **_kwargs: {"assets": []})
    with pytest.raises(AcceptanceError, match="Official release asset not found"):
        install_matching_nunchaku(tmp_path)

    monkeypatch.setattr(
        phase6_acceptance,
        "fetch_json",
        lambda *_args, **_kwargs: {"assets": [{"name": WHEEL_NAME, "digest": "", "browser_download_url": "u"}]},
    )
    with pytest.raises(AcceptanceError, match="no SHA-256 digest"):
        install_matching_nunchaku(tmp_path)


def test_public_network_probe_and_validate_output_edges(monkeypatch, tmp_path):
    monkeypatch.setattr(
        phase6_acceptance,
        "run_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    )
    with pytest.raises(AcceptanceError, match="public URL"):
        prove_public_network_blocked()

    background = tmp_path / "background.png"
    portrait = tmp_path / "portrait.png"
    output = tmp_path / "output.png"
    Image.new("RGB", (10, 10), "black").save(background)
    Image.new("RGB", (10, 10), "white").save(portrait)

    with pytest.raises(AcceptanceError, match="not created"):
        validate_output(output, background, portrait)

    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(output)
    with pytest.raises(AcceptanceError, match="transparent"):
        validate_output(output, background, portrait)

    Image.new("RGB", (10, 10), "red").save(output)
    with pytest.raises(AcceptanceError, match="uniform"):
        validate_output(output, background, portrait)


def test_directory_snapshot_safetensors_and_prepare_identity(monkeypatch, tmp_path):
    comfy_root = tmp_path / "ComfyUI"
    model_dir = comfy_root / "models" / "diffusion_models"
    model_dir.mkdir(parents=True)
    model = model_dir / "x.bin"
    model.write_bytes(b"x")
    monkeypatch.setattr(phase6_acceptance, "COMFY_ROOT", comfy_root)
    monkeypatch.setattr(phase6_acceptance, "WATCH_DIRS", [model_dir])
    assert "models\\diffusion_models\\x.bin" in directory_snapshot() or "models/diffusion_models/x.bin" in directory_snapshot()

    monkeypatch.setattr(phase6_acceptance, "sha256_file", lambda path: f"hash:{Path(path).name}")
    monkeypatch.setattr(phase6_acceptance, "create_background", lambda path: "hash:bg")
    character = tmp_path / "hero.png"
    Image.new("RGB", (2, 2), "white").save(character)
    monkeypatch.setattr(phase6_acceptance, "CHARACTER_PATH", character)
    monkeypatch.setattr(phase6_acceptance, "ROOT", tmp_path)

    class Store:
        def __init__(self, _project):
            self.character = None

        def get_character(self, _key):
            return self.character

        def log_character(self, key, desc):
            self.character = {"key": key, "desc": desc}

        def set_master_portrait(self, _key, path, digest):
            self.path = path
            self.digest = digest

        def get_master_portrait_path(self, _key):
            return self.path

        def get_master_portrait_hash(self, _key):
            return self.digest

    monkeypatch.setattr("memory.project_store.ProjectStore", Store)
    result = prepare_identity(tmp_path / "bg.png")
    assert result == {"portrait_sha256": "hash:hero.png", "background_sha256": "hash:bg"}
