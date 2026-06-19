"""ComfyUI runtime management - checks if running, auto-starts when enabled."""

import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


class ComfyUIRuntime:
    def __init__(self, config: dict):
        self.config = config.get("comfyui") or config.get("image_gen", {}).get("comfyui", {})
        self.server = self.config.get("server", "127.0.0.1")
        self.host = self.config.get("host", "127.0.0.1")
        self.port = self.config.get("port", 8188)
        self.root = self.config.get("root", "external/ComfyUI")
        self.python = self.config.get("python", "python")
        self.auto_start = self.config.get("auto_start", False)
        self.open_browser = self.config.get("open_browser", False)
        self._process: subprocess.Popen | None = None
        self._process_lock = threading.Lock()
        self._base_url = f"http://{self.host}:{self.port}"
        # Validate the base URL is local-only
        from utils.url_security import validate_local_service_base_url
        validate_local_service_base_url(self._base_url)
        self._project_root = Path(__file__).resolve().parents[2]
        self._stdout_handle = None
        self._stderr_handle = None

    def _resolve_path(
        self,
        path_value: str,
        *,
        base: Path | None = None,
        require_file: bool = False,
    ) -> Path:
        path = Path(path_value)
        if path.is_absolute():
            return path
        candidates = []
        if base is not None:
            candidates.append(base / path)
        candidates.append(self._project_root / path)
        for candidate in candidates:
            if candidate.exists() and (not require_file or candidate.is_file()):
                return candidate
        if require_file:
            return path
        return candidates[-1]

    def _open_log_handles(self, root_path: Path) -> tuple[object, object]:
        self._close_log_handles()
        stdout_path = root_path / "comfyui_stdout.log"
        stderr_path = root_path / "comfyui_stderr.log"
        self._stdout_handle = stdout_path.open("ab", buffering=0)
        self._stderr_handle = stderr_path.open("ab", buffering=0)
        return self._stdout_handle, self._stderr_handle

    def _close_log_handles(self) -> None:
        for handle_attr in ("_stdout_handle", "_stderr_handle"):
            handle = getattr(self, handle_attr, None)
            if handle is not None:
                try:
                    handle.close()
                except Exception as e:
                    log.debug(f"[ComfyUI] Error closing log handle: {e}")
                finally:
                    setattr(self, handle_attr, None)

    def is_running(self, timeout: float = 2.0) -> bool:
        """Check if ComfyUI is running and responding."""
        try:
            from utils.url_security import build_validated_url, validate_local_service_base_url

            validated = validate_local_service_base_url(self._base_url)
            req = urllib.request.Request(build_validated_url(validated, "/system_stats"))
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            return False

    def ensure_running(self, timeout: float = 60.0) -> bool:
        """Ensure ComfyUI is running, starting it if auto_start is enabled."""
        if self.is_running():
            log.info(f"[ComfyUI] Already running at {self._base_url}")
            return True

        if not self.auto_start:
            log.warning(f"[ComfyUI] Not running at {self._base_url} and auto_start is disabled")
            return False

        return self.start(timeout=timeout)

    def start(self, timeout: float = 60.0) -> bool:
        """Start ComfyUI headlessly."""
        with self._process_lock:
            if self._process is not None:
                log.info("[ComfyUI] Already starting or running")
                return True

            root_path = self._resolve_path(self.root)
            python_path = self._resolve_path(self.python, base=root_path, require_file=True)

            log.info(f"[ComfyUI] Starting at {self._base_url} (root: {root_path})")

            try:
                cmd = [
                    str(python_path),
                    "main.py",
                    "--listen",
                    self.host,
                    "--port",
                    str(self.port),
                ]

                if not self.open_browser:
                    cmd.append("--disable-auto-launch")

                cmd.extend(["--preview-method", "auto"])

                env = {
                    **os.environ,
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                }
                creationflags = (
                    subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )
                stdout_handle, stderr_handle = self._open_log_handles(root_path)

                try:
                    self._process = subprocess.Popen(
                        cmd,
                        cwd=str(root_path),
                        env=env,
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        creationflags=creationflags,
                    )
                except PermissionError:
                    log.warning(
                        "[ComfyUI] Hidden process launch was denied; retrying without "
                        "Windows creation flags"
                    )
                    stdout_handle, stderr_handle = self._open_log_handles(root_path)
                    self._process = subprocess.Popen(
                        cmd,
                        cwd=str(root_path),
                        env=env,
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                    )

                log.info(f"[ComfyUI] Started process PID {self._process.pid}")

            except Exception as e:
                log.error(f"[ComfyUI] Failed to start: {e}")
                self._process = None
                self._close_log_handles()
                return False

        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.is_running(timeout=2.0):
                log.info(f"[ComfyUI] Ready at {self._base_url}")
                return True
            time.sleep(1)

        log.error(f"[ComfyUI] Failed to start within {timeout}s")
        return False

    def stop(self) -> None:
        """Stop the ComfyUI process if we started it."""
        with self._process_lock:
            if self._process is not None:
                log.info(f"[ComfyUI] Stopping process PID {self._process.pid}")
                try:
                    self._process.terminate()
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                except Exception as e:
                    log.warning(f"[ComfyUI] Error stopping: {e}")
                finally:
                    self._process = None
                    self._close_log_handles()

    @property
    def base_url(self) -> str:
        return self._base_url


def get_comfyui_runtime(config: dict) -> ComfyUIRuntime:
    """Factory function to create ComfyUI runtime from config."""
    return ComfyUIRuntime(config)
