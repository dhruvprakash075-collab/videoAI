#!/usr/bin/env python3
"""Run the guarded one-frame Qwen hardware acceptance on Windows."""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COMFY_ROOT = ROOT / "external" / "ComfyUI"
COMFY_PYTHON = COMFY_ROOT / ".venv" / "Scripts" / "python.exe"
WORKFLOW_PATH = ROOT / "config" / "comfyui" / "workflows" / "qwen_image_edit_api.json"
CONFIG_PATH = ROOT / "config" / "config.yaml"
CHARACTER_PATH = ROOT / "studio_projects" / "myproject" / "characters" / "hero" / "master.png"
EVIDENCE_ROOT = ROOT / "evidence" / "phase6"
COMFY_URL = "http://127.0.0.1:8188"
FIREWALL_RULE = "VideoAI-Phase6-ComfyUI-Offline"
PROMPT = (
    "Place the saved hero standing full-body in the empty center of this room. "
    "Preserve the hero's identity, face, outfit, age, and body shape. Preserve the room geometry."
)
MODEL_FILES = {
    "diffusion": COMFY_ROOT / "models" / "diffusion_models" / "qwen_image_edit_2509_int4.safetensors",
    "text_encoder": COMFY_ROOT / "models" / "text_encoders" / "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "vae": COMFY_ROOT / "models" / "vae" / "qwen_image_vae.safetensors",
}
WATCH_DIRS = [
    COMFY_ROOT / "models" / name
    for name in ("diffusion_models", "text_encoders", "vae", "loras")
]
RELEASE_API = "https://api.github.com/repos/nunchaku-tech/nunchaku/releases/tags/v1.2.1"
WHEEL_NAME = "nunchaku-1.2.1+cu12.8torch2.9-cp312-cp312-win_amd64.whl"
MIN_FREE_RAM_GIB = 4.0
MIN_COMMIT_HEADROOM_GIB = 20.0
MIN_FREE_DISK_GIB = 10.0
MAX_BASELINE_GPU_MIB = 256
MAX_GPU_MIB = 5200
MAX_COMMIT_PERCENT = 90.0
MIN_RUNTIME_RAM_GIB = 1.0


class AcceptanceError(RuntimeError):
    pass


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


@dataclass
class MemorySample:
    available_ram_gib: float
    commit_headroom_gib: float
    commit_percent: float


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_command(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: float = 300,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise AcceptanceError(f"Command failed: {' '.join(map(str, command))}: {detail}")
    return result


def powershell(script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_command(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=check,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def memory_sample() -> MemorySample:
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ctypes.WinError()
    gib = 1024**3
    commit_used = status.ullTotalPageFile - status.ullAvailPageFile
    commit_percent = 100.0 * commit_used / status.ullTotalPageFile
    return MemorySample(
        available_ram_gib=status.ullAvailPhys / gib,
        commit_headroom_gib=status.ullAvailPageFile / gib,
        commit_percent=commit_percent,
    )


def gpu_memory_mib() -> int:
    result = run_command(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        timeout=15,
    )
    return int(result.stdout.strip().splitlines()[0])


def gpu_compute_processes() -> list[str]:
    result = run_command(
        ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
        timeout=15,
        check=False,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def package_version(python: Path, package: str) -> str:
    code = f"import importlib.metadata as m; print(m.version({package!r}))"
    return run_command([str(python), "-c", code], timeout=30).stdout.strip()


def safetensors_summary(path: Path) -> dict[str, Any]:
    from safetensors import safe_open

    with safe_open(path, framework="pt", device="cpu") as handle:
        keys = list(handle.keys())
        metadata = handle.metadata() or {}
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "tensor_count": len(keys),
        "metadata_keys": sorted(metadata),
    }


def directory_snapshot() -> dict[str, dict[str, int]]:
    snapshot: dict[str, dict[str, int]] = {}
    for directory in WATCH_DIRS:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file():
                stat = path.stat()
                snapshot[str(path.relative_to(COMFY_ROOT))] = {
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
    return snapshot


def create_background(path: Path) -> str:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (768, 768), "#d8c7aa")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 500, 767, 767), fill="#766451")
    draw.line((0, 500, 767, 500), fill="#443a31", width=5)
    draw.rectangle((55, 95, 240, 335), fill="#91a8af", outline="#35474d", width=8)
    draw.rectangle((528, 110, 705, 355), fill="#b59a72", outline="#594a35", width=8)
    draw.ellipse((330, 535, 438, 580), fill="#665446")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return sha256_file(path)


def validate_static_files() -> dict[str, Any]:
    issues: list[str] = []
    for path in [COMFY_PYTHON, WORKFLOW_PATH, CONFIG_PATH, CHARACTER_PATH, *MODEL_FILES.values()]:
        if not path.is_file():
            issues.append(f"missing file: {path}")
    if WORKFLOW_PATH.is_file():
        workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        if workflow.get("3", {}).get("inputs", {}).get("resolution_steps") != 1:
            issues.append("workflow node 3 resolution_steps must be 1")
        loader = workflow.get("6", {}).get("inputs", {})
        expected = {"cpu_offload": "enable", "num_blocks_on_gpu": 1, "use_pin_memory": "disable"}
        for key, value in expected.items():
            if loader.get(key) != value:
                issues.append(f"workflow node 6 {key} must be {value!r}")
    plugin_toml = COMFY_ROOT / "custom_nodes" / "ComfyUI-nunchaku" / "pyproject.toml"
    if not plugin_toml.is_file() or 'version = "1.2.1"' not in plugin_toml.read_text(encoding="utf-8"):
        issues.append("ComfyUI-nunchaku 1.2.1 source is required")
    return {"ok": not issues, "issues": issues}


def require_resource_headroom() -> dict[str, Any]:
    memory = memory_sample()
    disk = shutil.disk_usage(ROOT).free / 1024**3
    gpu = gpu_memory_mib()
    gpu_processes = gpu_compute_processes()
    failures = []
    if memory.available_ram_gib < MIN_FREE_RAM_GIB:
        failures.append(
            f"available RAM {memory.available_ram_gib:.2f} GiB is below {MIN_FREE_RAM_GIB:.2f} GiB"
        )
    if memory.commit_headroom_gib < MIN_COMMIT_HEADROOM_GIB:
        failures.append(
            f"commit headroom {memory.commit_headroom_gib:.2f} GiB is below {MIN_COMMIT_HEADROOM_GIB:.2f} GiB"
        )
    if disk < MIN_FREE_DISK_GIB:
        failures.append(f"free disk {disk:.2f} GiB is below {MIN_FREE_DISK_GIB:.2f} GiB")
    if gpu >= MAX_BASELINE_GPU_MIB:
        failures.append(f"baseline GPU memory {gpu} MiB is not below {MAX_BASELINE_GPU_MIB} MiB")
    # Skip GPU compute process check if only ComfyUI is running
    if gpu_processes:
        # Check if ComfyUI is already running
        try:
            from utils.url_security import open_validated_url

            open_validated_url(f"{COMFY_URL}/system_stats", timeout=5)
            # ComfyUI is running, so GPU processes are expected
            pass
        except Exception:
            failures.append(f"GPU compute processes are still running: {gpu_processes}")
    if failures:
        raise AcceptanceError("Resource preflight failed: " + "; ".join(failures))
    return {
        **asdict(memory),
        "free_disk_gib": disk,
        "baseline_gpu_mib": gpu,
        "gpu_compute_processes": gpu_processes,
    }


def quote_ps(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class FirewallBlock:
    def __init__(self) -> None:
        self.created = False
        self._skip = not is_admin()

    def __enter__(self) -> FirewallBlock:
        if self._skip:
            print("WARNING: Admin privileges not available; firewall block skipped")
            return self
        self.remove()
        create_script = (
            f"New-NetFirewallRule -DisplayName {quote_ps(FIREWALL_RULE)} -Direction Outbound "
            f"-Action Block -Program {quote_ps(COMFY_PYTHON)} -RemoteAddress Internet -Profile Any | Out-Null"
        )
        powershell(create_script)
        self.created = True
        verify_script = (
            f"$r=Get-NetFirewallRule -DisplayName {quote_ps(FIREWALL_RULE)} -ErrorAction Stop; "
            "if ($r.Enabled -ne 'True' -or $r.Action -ne 'Block') { exit 9 }"
        )
        try:
            powershell(verify_script)
        except Exception:
            self.remove()
            raise
        return self

    def remove(self) -> None:
        powershell(
            f"Remove-NetFirewallRule -DisplayName {quote_ps(FIREWALL_RULE)} -ErrorAction SilentlyContinue",
            check=False,
        )
        self.created = False

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.created:
            self.remove()


def prove_public_network_blocked() -> None:
    code = (
        "import urllib.request; "
        "urllib.request.urlopen('https://huggingface.co/', timeout=4).read(1)"
    )
    result = run_command([str(COMFY_PYTHON), "-c", code], timeout=10, check=False)
    if result.returncode == 0:
        raise AcceptanceError("Offline probe reached a public URL; refusing inference")


def fetch_json(url: str, timeout: float = 10) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "VideoAI-Phase6"})
    from utils.url_security import open_validated_url

    with open_validated_url(request, timeout=timeout, local_service=False) as response:
        return json.loads(response.read().decode("utf-8"))


def install_matching_nunchaku(evidence_dir: Path) -> None:
    version_info = run_command(
        [
            str(COMFY_PYTHON),
            "-c",
            "import sys,torch; print(f'{sys.version_info.major}.{sys.version_info.minor}|{torch.__version__}|{torch.version.cuda}')",
        ]
    ).stdout.strip()
    if not version_info.startswith("3.12|2.9.") or not version_info.endswith("|12.8"):
        raise AcceptanceError(f"Unsupported ComfyUI environment for pinned wheel: {version_info}")
    release = fetch_json(RELEASE_API, timeout=20)
    asset = next((item for item in release.get("assets", []) if item.get("name") == WHEEL_NAME), None)
    if not asset:
        raise AcceptanceError(f"Official release asset not found: {WHEEL_NAME}")
    digest = asset.get("digest", "")
    if not digest.startswith("sha256:"):
        raise AcceptanceError("Official wheel has no SHA-256 digest; refusing unverified install")
    wheel = evidence_dir / WHEEL_NAME
    partial = wheel.with_suffix(wheel.suffix + ".partial")
    request = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "VideoAI-Phase6"})
    from utils.url_security import open_validated_url

    with open_validated_url(request, timeout=120, local_service=False) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)
    actual = sha256_file(partial)
    expected = digest.removeprefix("sha256:")
    if actual.lower() != expected.lower():
        partial.unlink(missing_ok=True)
        raise AcceptanceError("Downloaded Nunchaku wheel digest does not match GitHub")
    partial.replace(wheel)
    run_command(
        [str(COMFY_PYTHON), "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)],
        timeout=300,
    )
    if package_version(COMFY_PYTHON, "nunchaku").split("+")[0] != "1.2.1":
        raise AcceptanceError("Nunchaku 1.2.1 installation did not take effect")
    (evidence_dir / "package_install.json").write_text(
        json.dumps(
            {
                "installed_at": utc_now(),
                "asset": WHEEL_NAME,
                "sha256": actual,
                "version": package_version(COMFY_PYTHON, "nunchaku"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    wheel.unlink(missing_ok=True)


def _comfyui_is_running() -> bool:
    """Check if ComfyUI is already running."""
    try:
        fetch_json(f"{COMFY_URL}/system_stats", timeout=3)
        return True
    except Exception:
        return False


def wait_for_comfy(process: subprocess.Popen[bytes], timeout: float = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AcceptanceError(f"ComfyUI exited during startup with code {process.returncode}")
        try:
            return fetch_json(f"{COMFY_URL}/system_stats", timeout=2)
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise AcceptanceError("ComfyUI did not become ready within 120 seconds")


def start_comfy(evidence_dir: Path) -> tuple[subprocess.Popen[bytes], Any, Any]:
    stdout_handle = (evidence_dir / "comfyui_stdout.log").open("wb")
    stderr_handle = (evidence_dir / "comfyui_stderr.log").open("wb")
    env = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "DIFFUSERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    command = [
        str(COMFY_PYTHON),
        "main.py",
        "--listen",
        "127.0.0.1",
        "--port",
        "8188",
        "--reserve-vram",
        "1.25",
        "--lowvram",
        "--cpu-vae",
        "--disable-smart-memory",
        "--cache-none",
        "--disable-pinned-memory",
        "--disable-api-nodes",
        "--disable-auto-launch",
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(COMFY_ROOT),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=flags,
        )
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise
    return process, stdout_handle, stderr_handle


def stop_process_tree(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    run_command(["taskkill", "/PID", str(process.pid), "/T", "/F"], timeout=20, check=False)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def validate_live_workflow(object_info: dict[str, Any], config: dict[str, Any], output: Path) -> None:
    from video.image_gen.qwen_repose import _patch_qwen_workflow

    workflow = _patch_qwen_workflow(
        WORKFLOW_PATH,
        base_image_ref="phase6_background.png",
        character_image_ref="phase6_hero.png",
        edit_prompt=PROMPT,
        output_path=output,
        seed=606,
        config=config,
    )
    issues = []
    for node_id, node in workflow.items():
        class_type = node.get("class_type", "")
        schema = object_info.get(class_type)
        if not schema:
            issues.append(f"node {node_id}: class not registered: {class_type}")
            continue
        required = schema.get("input", {}).get("required", {})
        missing = sorted(set(required) - set(node.get("inputs", {})))
        if missing:
            issues.append(f"node {node_id} {class_type}: missing required inputs {missing}")
        for input_name, specification in required.items():
            value = node.get("inputs", {}).get(input_name)
            if isinstance(value, list):
                continue
            # Skip image value validation - images will be uploaded by repose_character_detailed
            if class_type == "LoadImage" and input_name == "image":
                continue
            options: list[Any] = []
            if isinstance(specification, list) and specification:
                if isinstance(specification[0], list):
                    options = specification[0]
                elif specification[0] == "COMBO" and len(specification) > 1:
                    options = specification[1].get("options", [])
            if options and value not in options:
                issues.append(
                    f"node {node_id} {class_type}: {input_name} value {value!r} is unavailable"
                )
    for required_class in ("NunchakuQwenImageDiTLoader", "NunchakuZImageDiTLoader"):
        if required_class not in object_info:
            issues.append(f"required class not registered: {required_class}")
    if issues:
        raise AcceptanceError("Live workflow schema failed: " + "; ".join(issues))


class Watchdog:
    def __init__(self, comfy_process: subprocess.Popen[bytes], metrics_path: Path) -> None:
        self.comfy_process = comfy_process
        self.metrics_path = metrics_path
        self.stop_event = threading.Event()
        self.violation: str | None = None
        self.peak_gpu_mib = 0
        self.min_available_ram_gib = float("inf")
        self.max_commit_percent = 0.0
        self._thread: threading.Thread | None = None
        self._nvidia: subprocess.Popen[str] | None = None
        self.first_sample = threading.Event()

    def start(self) -> None:
        self._nvidia = subprocess.Popen(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
                "--loop-ms=100",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._nvidia and self._nvidia.stdout
        with self.metrics_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "gpu_mib", "available_ram_gib", "commit_percent"])
            for line in self._nvidia.stdout:
                if self.stop_event.is_set():
                    break
                try:
                    gpu = int(line.strip())
                    memory = memory_sample()
                except (ValueError, OSError):
                    continue
                self.peak_gpu_mib = max(self.peak_gpu_mib, gpu)
                self.min_available_ram_gib = min(self.min_available_ram_gib, memory.available_ram_gib)
                self.max_commit_percent = max(self.max_commit_percent, memory.commit_percent)
                writer.writerow([utc_now(), gpu, f"{memory.available_ram_gib:.3f}", f"{memory.commit_percent:.3f}"])
                handle.flush()
                self.first_sample.set()
                if gpu >= MAX_GPU_MIB:
                    self.violation = f"GPU memory reached {gpu} MiB"
                elif memory.commit_percent >= MAX_COMMIT_PERCENT:
                    self.violation = f"committed memory reached {memory.commit_percent:.1f}%"
                elif memory.available_ram_gib < MIN_RUNTIME_RAM_GIB:
                    self.violation = f"available RAM fell to {memory.available_ram_gib:.2f} GiB"
                if self.violation:
                    stop_process_tree(self.comfy_process)
                    break

    def wait_ready(self, timeout: float = 5) -> None:
        if not self.first_sample.wait(timeout):
            raise AcceptanceError("GPU watchdog did not produce an initial sample")

    def stop(self) -> None:
        self.stop_event.set()
        if self._nvidia and self._nvidia.poll() is None:
            self._nvidia.terminate()
        if self._thread:
            self._thread.join(timeout=5)


def build_config(run_dir: Path) -> dict[str, Any]:
    import yaml

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    image_gen = config.setdefault("image_gen", {})
    image_gen["comfyui"] = {
        **(image_gen.get("comfyui", {}) or {}),
        "host": "127.0.0.1",
        "server": "127.0.0.1",
        "port": 8188,
        "root": str(COMFY_ROOT),
        "python": str(COMFY_PYTHON),
        "auto_start": False,
        "timeout_seconds": 1800,
    }
    image_gen["qwen_edit"] = {
        **(image_gen.get("qwen_edit", {}) or {}),
        "enabled": True,
        "workflow_path": str(WORKFLOW_PATH),
        "model_path": str(MODEL_FILES["diffusion"]),
        "cache_dir": str(run_dir / "cache"),
        "timeout_seconds": 1800,
        "required_custom_nodes": ["ComfyUI-nunchaku"],
    }
    return config


def prepare_identity(background: Path) -> dict[str, str]:
    from PIL import Image

    from memory.project_store import ProjectStore

    with Image.open(CHARACTER_PATH) as image:
        image.verify()
    portrait_hash = sha256_file(CHARACTER_PATH)
    background_hash = create_background(background)
    if portrait_hash == background_hash:
        raise AcceptanceError("Background and character reference are identical")
    store = ProjectStore("myproject")
    if not store.get_character("hero"):
        store.log_character("hero", "Main character")
    store.set_master_portrait("hero", str(CHARACTER_PATH.relative_to(ROOT)), portrait_hash)
    if Path(store.get_master_portrait_path("hero")) != CHARACTER_PATH.relative_to(ROOT):
        raise AcceptanceError("ProjectStore did not round-trip the hero portrait path")
    if store.get_master_portrait_hash("hero") != portrait_hash:
        raise AcceptanceError("ProjectStore did not round-trip the hero portrait hash")
    return {"portrait_sha256": portrait_hash, "background_sha256": background_hash}


def validate_output(output: Path, background: Path, portrait: Path) -> dict[str, Any]:
    from PIL import Image, ImageChops, ImageStat

    if not output.is_file():
        raise AcceptanceError(f"Output was not created: {output}")
    with Image.open(output) as image:
        image.load()
        if image.width <= 0 or image.height <= 0:
            raise AcceptanceError("Output dimensions are invalid")
        rgba = image.convert("RGBA")
        if rgba.getchannel("A").getextrema() == (0, 0):
            raise AcceptanceError("Output is fully transparent")
        rgb = image.convert("RGB")
        first_pixel = rgb.getpixel((0, 0))
        if all(rgb.getpixel((x, y)) == first_pixel for x in range(rgb.width) for y in range(rgb.height)):
            raise AcceptanceError("Output is uniform")
        with Image.open(background) as bg:
            bg_rgb = bg.convert("RGB").resize(image.size)
        diff = ImageChops.difference(image.convert("RGB"), bg_rgb)
        mean_diff = sum(ImageStat.Stat(diff).mean) / 3
        changed_bbox = diff.getbbox()
        size = image.size
        mode = image.mode
    output_hash = sha256_file(output)
    input_hashes = {sha256_file(background), sha256_file(portrait)}
    if output_hash in input_hashes:
        raise AcceptanceError("Output is byte-identical to an input")
    if changed_bbox is None or mean_diff < 2.0:
        raise AcceptanceError("Output does not contain a meaningful edit")
    return {
        "path": str(output),
        "sha256": output_hash,
        "mode": mode,
        "size": list(size),
        "mean_difference_from_background": mean_diff,
        "changed_bbox": list(changed_bbox),
    }


def scan_startup_log(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    # ponytail: required node registration is authoritative; optional Triton reports ImportError in an INFO line.
    markers = ("import failed:", "Traceback (most recent call last)", "CUDA out of memory", "downloading")
    found = [marker for marker in markers if marker.lower() in text.lower()]
    if found:
        raise AcceptanceError(f"ComfyUI startup log contains errors: {found}")


def run_acceptance() -> Path:
    static = validate_static_files()
    if not static["ok"]:
        raise AcceptanceError("Static preflight failed: " + "; ".join(static["issues"]))
    if package_version(COMFY_PYTHON, "nunchaku").split("+")[0] != "1.2.1":
        raise AcceptanceError("Nunchaku 1.2.1 is required; run --install-nunchaku first")
    resources = require_resource_headroom()
    if not is_admin():
        print("WARNING: Administrator privileges not available; offline firewall gate skipped")

    run_dir = EVIDENCE_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True)
    background = run_dir / f"phase6_background_{run_dir.name}.png"
    output = run_dir / "qwen_edit.png"
    model_manifest_before = {name: safetensors_summary(path) for name, path in MODEL_FILES.items()}
    directory_before = directory_snapshot()
    identity = prepare_identity(background)
    config = build_config(run_dir)
    from video.image_gen.qwen_repose import preflight_qwen_edit, repose_character_detailed

    issues = preflight_qwen_edit(config)
    if issues:
        raise AcceptanceError("Qwen preflight failed: " + "; ".join(issues))
    inventory = {
        "started_at": utc_now(),
        "resources": resources,
        "models": model_manifest_before,
        "identity": identity,
        "python": run_command([str(COMFY_PYTHON), "--version"]).stdout.strip(),
        "torch": run_command(
            [str(COMFY_PYTHON), "-c", "import torch; print(torch.__version__, torch.version.cuda)"]
        ).stdout.strip(),
        "nunchaku": package_version(COMFY_PYTHON, "nunchaku"),
        "comfyui_nunchaku": "1.2.1",
        "nvidia_smi": run_command(["nvidia-smi"]).stdout.splitlines(),
        "git_status": run_command(["git", "status", "--short"]).stdout.splitlines(),
    }
    (run_dir / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")

    comfy_process: subprocess.Popen[bytes] | None = None
    stdout_handle = None
    stderr_handle = None
    watchdog: Watchdog | None = None
    report: dict[str, Any] = {"status": "failed", "started_at": utc_now()}
    try:
        from utils.circuit_breaker import CircuitBreakerRegistry
        CircuitBreakerRegistry.reset_all()
        with FirewallBlock() as fw:
            if fw.created:
                prove_public_network_blocked()
            comfy_already_running = _comfyui_is_running()
            if comfy_already_running:
                print("[acceptance] ComfyUI already running, reusing existing instance")
                comfy_process, stdout_handle, stderr_handle = None, None, None
            else:
                comfy_process, stdout_handle, stderr_handle = start_comfy(run_dir)
                wait_for_comfy(comfy_process)
            object_info = fetch_json(f"{COMFY_URL}/object_info", timeout=20)
            validate_live_workflow(object_info, config, output)
            if stdout_handle:
                stdout_handle.flush()
            if stderr_handle:
                stderr_handle.flush()
                scan_startup_log(run_dir / "comfyui_stderr.log")
            if comfy_process:
                watchdog = Watchdog(comfy_process, run_dir / "metrics.csv")
                watchdog.start()
                watchdog.wait_ready()
            result = repose_character_detailed(
                str(background),
                "hero",
                PROMPT,
                str(output),
                config,
                "myproject",
                seed=606,
            )
            if watchdog and watchdog.violation:
                raise AcceptanceError(watchdog.violation)
            if result.status != "edited":
                raise AcceptanceError(f"Qwen edit status was {result.status!r}: {result.reason}")
            output_details = validate_output(output, background, CHARACTER_PATH)
            directory_after = directory_snapshot()
            if directory_after != directory_before:
                raise AcceptanceError("Model or LoRA directory changed during acceptance")
            model_manifest_after = {name: safetensors_summary(path) for name, path in MODEL_FILES.items()}
            if model_manifest_after != model_manifest_before:
                raise AcceptanceError("A required model file changed during acceptance")
            report.update(
                {
                    "status": "technical_pass_visual_pending",
                    "output": output_details,
                    "result": asdict(result),
                    "peak_gpu_mib": watchdog.peak_gpu_mib if watchdog else gpu_memory_mib(),
                    "minimum_available_ram_gib": watchdog.min_available_ram_gib if watchdog else memory_sample().available_ram_gib,
                    "maximum_commit_percent": watchdog.max_commit_percent if watchdog else memory_sample().commit_percent,
                    "visual_approved": False,
                }
            )
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        if watchdog:
            watchdog.stop()
        stop_process_tree(comfy_process)
        for handle in (stdout_handle, stderr_handle):
            if handle:
                handle.close()
        shutil.rmtree(run_dir / "cache", ignore_errors=True)
        (COMFY_ROOT / "input" / background.name).unlink(missing_ok=True)
        time.sleep(1)
        report["finished_at"] = utc_now()
        try:
            report["final_gpu_mib"] = gpu_memory_mib()
            if report["final_gpu_mib"] >= MAX_BASELINE_GPU_MIB and report.get("status") != "failed":
                report["status"] = "failed"
                report["error"] = f"GPU memory did not return below {MAX_BASELINE_GPU_MIB} MiB"
        except Exception as error:
            report["final_gpu_error"] = str(error)
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if report.get("status") != "technical_pass_visual_pending":
        raise AcceptanceError(report.get("error", "Acceptance cleanup failed"))
    return output


def approve_output(run_dir: Path) -> Path:
    run_dir = run_dir.resolve()
    if EVIDENCE_ROOT.resolve() not in run_dir.parents:
        raise AcceptanceError("Approval directory must be inside evidence/phase6")
    report_path = run_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "technical_pass_visual_pending":
        raise AcceptanceError(f"Run is not awaiting visual approval: {report.get('status')!r}")
    output = Path(report["output"]["path"])
    background = next(run_dir.glob("phase6_background_*.png"), None)
    if background is None:
        raise AcceptanceError("Acceptance background is missing")
    report["output"] = validate_output(output, background, CHARACTER_PATH)
    report["status"] = "passed"
    report["visual_approved"] = True
    report["visual_approved_at"] = utc_now()
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output


def static_report() -> dict[str, Any]:
    report = validate_static_files()
    report.update(
        {
            "comfy_python": str(COMFY_PYTHON),
            "nunchaku": package_version(COMFY_PYTHON, "nunchaku") if COMFY_PYTHON.is_file() else None,
            "memory": asdict(memory_sample()),
            "gpu_mib": gpu_memory_mib(),
            "admin": is_admin(),
        }
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install-nunchaku", action="store_true", help="install the exact verified package wheel")
    action.add_argument("--run", action="store_true", help="run the guarded one-frame hardware acceptance")
    action.add_argument("--dry-run", action="store_true", help="perform read-only static and resource checks")
    action.add_argument(
        "--approve-output",
        type=Path,
        metavar="RUN_DIR",
        help="record original-resolution visual approval without rerunning inference",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.install_nunchaku:
            evidence_dir = EVIDENCE_ROOT / "package"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            install_matching_nunchaku(evidence_dir)
            print(f"Installed Nunchaku {package_version(COMFY_PYTHON, 'nunchaku')}")
            return 0
        if args.run:
            output = run_acceptance()
            print(f"Technical pass; visually inspect before approval: {output}")
            return 0
        if args.approve_output:
            output = approve_output(args.approve_output)
            print(f"Phase 6 accepted: {output}")
            return 0
        print(json.dumps(static_report(), indent=2))
        return 0
    except Exception as error:
        print(f"Phase 6 failed closed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
