"""ComfyUI runtime management - checks if running, auto-starts when enabled."""

import contextlib
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import BinaryIO

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
        self._stdout_handle: BinaryIO | None = None
        self._stderr_handle: BinaryIO | None = None

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

    def _open_log_handles(self, root_path: Path) -> tuple[BinaryIO, BinaryIO]:
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
            from utils.url_security import open_validated_url

            with open_validated_url(req, timeout=timeout) as response:
                return response.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            return False

    def _reuse_running_port(self) -> bool:
        for port in self.config.get("reuse_ports", []):
            old_port, old_url = self.port, self._base_url
            self.port = int(port)
            self._base_url = f"http://{self.host}:{self.port}"
            if self.is_running(timeout=1.0):
                log.info(f"[ComfyUI] Reusing running instance at {self._base_url}")
                return True
            self.port, self._base_url = old_port, old_url
        return False

    def _owns_running_instance(self) -> bool:
        """True when the responding instance was spawned by a pipeline runtime.

        Ownership is a pid marker file written by start(); the pid must still live.
        """
        try:
            pid = int((self._resolve_path(self.root) / "comfyui_runtime.pid").read_text().strip())
        except (OSError, ValueError):
            return False
        try:
            import psutil

            return psutil.pid_exists(pid)
        except ImportError:
            # Can't verify — don't kill blindly.
            log.debug("[ComfyUI] psutil unavailable; assuming instance is ours")
            return True

    def _kill_port_owner(self) -> bool:
        """Terminate whatever listens on our port. Best-effort."""
        try:
            import psutil
        except ImportError:
            return False
        killed = False
        try:
            for conn in psutil.net_connections(kind="tcp"):
                if (
                    conn.laddr
                    and conn.laddr.port == self.port
                    and conn.status == psutil.CONN_LISTEN
                    and conn.pid
                ):
                    try:
                        proc = psutil.Process(conn.pid)
                        log.warning(f"[ComfyUI] Killing foreign instance PID {conn.pid}")
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except psutil.TimeoutExpired:
                            proc.kill()
                        killed = True
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                        log.warning(f"[ComfyUI] Could not kill PID {conn.pid}: {e}")
        except Exception as e:
            log.warning(f"[ComfyUI] Port-owner scan failed: {e}")
        return killed

    def ensure_running(self, timeout: float = 60.0) -> bool:
        """Ensure ComfyUI is running, starting it if auto_start is enabled."""
        if self.is_running():
            if self._owns_running_instance():
                log.info(f"[ComfyUI] Already running at {self._base_url}")
                return True
            # ponytail: a foreign ComfyUI may hold a dead stdout pipe; every prompt
            # then dies in tqdm with [Errno 22] (seen in production). Another process's
            # handles can't be inspected, so restart it under our own log files.
            log.warning(
                f"[ComfyUI] Instance at {self._base_url} was not started by this pipeline — "
                "restarting under pipeline management (foreign stdout can crash prompts)"
            )
            if self._kill_port_owner() and self.auto_start:
                time.sleep(1.0)
                return self.start(timeout=timeout)
            log.warning("[ComfyUI] Could not replace foreign instance — using it as-is")
            return True
        if self._reuse_running_port():
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

            for subdir in ("input", "output"):
                (root_path / subdir).mkdir(parents=True, exist_ok=True)

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
                    self._close_log_handles()
                except PermissionError:
                    log.warning(
                        "[ComfyUI] Hidden process launch was denied; retrying without "
                        "Windows creation flags"
                    )
                    self._close_log_handles()
                    stdout_handle, stderr_handle = self._open_log_handles(root_path)
                    self._process = subprocess.Popen(
                        cmd,
                        cwd=str(root_path),
                        env=env,
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                    )
                    self._close_log_handles()

                log.info(f"[ComfyUI] Started process PID {self._process.pid}")
                (root_path / "comfyui_runtime.pid").write_text(str(self._process.pid))

            except Exception as e:
                log.error(f"[ComfyUI] Failed to start: {e}")
                self._process = None
                self._close_log_handles()
                return False

        deadline = time.time() + timeout
        while True:
            if time.time() >= deadline:
                break
            if self.is_running(timeout=2.0):
                log.info(f"[ComfyUI] Ready at {self._base_url}")
                return True
            time.sleep(1)

        log.error(f"[ComfyUI] Failed to start within {timeout}s")
        self.stop()
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
                    with contextlib.suppress(OSError):
                        (self._resolve_path(self.root) / "comfyui_runtime.pid").unlink(
                            missing_ok=True
                        )

    @property
    def base_url(self) -> str:
        return self._base_url


def get_comfyui_runtime(config: dict) -> ComfyUIRuntime:
    """Factory function to create ComfyUI runtime from config."""
    return ComfyUIRuntime(config)
