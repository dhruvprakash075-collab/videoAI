import json
import os
import signal
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from jobs.job_store import (
    STATUS_CANCEL_REQUESTED,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    JobStore,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PY = REPO_ROOT / "venv" / "Scripts" / "python.exe"
BOOTSTRAP = REPO_ROOT / "bootstrap_pipeline.py"

HEARTBEAT_INTERVAL = 10
CANCEL_WAIT_SECONDS = 30


class Worker:
    def __init__(self, store: JobStore | None = None):
        self.store = store or JobStore()
        self._stop = threading.Event()

    def _preflight_comfyui(self, job: dict[str, Any]) -> None:
        backend = job.get("image_backend")
        if backend != "comfyui":
            return
        # Basic checks: checkpoint path exists and ComfyUI server reachable
        checkpoint = job.get("comfyui_checkpoint")
        if checkpoint:
            cp = Path(checkpoint)
            if not cp.exists():
                raise RuntimeError(f"ComfyUI checkpoint not found: {cp}")
        # Check default ComfyUI server URL
        try:
            import urllib.request

            with urllib.request.urlopen("http://127.0.0.1:8188/", timeout=2) as resp:  # type: ignore
                if resp.status >= 400:
                    raise RuntimeError("ComfyUI server returned error")
        except Exception as exc:
            raise RuntimeError(f"ComfyUI preflight failed: {exc}") from exc

    def _build_command(self, job: dict[str, Any]) -> list:
        req = job.get("request_json")
        if isinstance(req, str):
            try:
                req = json.loads(req)
            except Exception:
                req = {}
        elif req is None:
            req = {}
        cmd = [str(VENV_PY), str(BOOTSTRAP)]
        topic = req.get("topic") or job.get("topic")
        if topic:
            cmd += ["--topic", str(topic)]
        for k, v in req.items():
            if k == "topic":
                continue
            arg = f"--{k.replace('_', '-') }"
            if isinstance(v, bool):
                if v:
                    cmd.append(arg)
            elif v is None:
                continue
            else:
                cmd += [arg, str(v)]
        return cmd

    def _heartbeat_loop(self, job_id: int):
        while not self._stop.is_set():
            with suppress(Exception):
                self.store.update_job(job_id, heartbeat_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            time.sleep(HEARTBEAT_INTERVAL)

    def _stream_process(self, proc: subprocess.Popen, job_id: int):
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip("\n")
            self.store.append_event(job_id, text, event_type="log")
            # also refresh heartbeat on output
            with suppress(Exception):
                self.store.update_job(job_id, heartbeat_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def run_once(self) -> int | None:
        job = self.store.claim_next_job()
        if not job:
            return None
        job_id = job["id"]
        # Preflight
        try:
            self._preflight_comfyui(job)
        except Exception as exc:
            self.store.append_event(job_id, f"preflight_failed: {exc}", event_type="system")
            self.store.update_job(job_id, status=STATUS_FAILED, error=str(exc))
            return job_id

        cmd = self._build_command(job)
        self.store.append_event(job_id, f"starting: {' '.join(cmd)}", event_type="system")

        proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=0)

        # Start heartbeat thread
        hb_thread = threading.Thread(target=self._heartbeat_loop, args=(job_id,), daemon=True)
        hb_thread.start()

        # Start output streaming thread
        out_thread = threading.Thread(target=self._stream_process, args=(proc, job_id), daemon=True)
        out_thread.start()

        # Monitor process and cancel requests
        try:
            while proc.poll() is None:
                j = self.store.get_job(job_id)
                if j and j.get("status") == STATUS_CANCEL_REQUESTED:
                    self.store.append_event(job_id, "cancellation_requested", event_type="system")
                    # send interrupt
                    try:
                        if os.name == "nt":
                            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore
                        else:
                            proc.send_signal(signal.SIGINT)
                    except Exception:
                        proc.terminate()
                    # wait up to CANCEL_WAIT_SECONDS
                    waited = 0
                    while proc.poll() is None and waited < CANCEL_WAIT_SECONDS:
                        time.sleep(1)
                        waited += 1
                    if proc.poll() is None:
                        proc.kill()
                    break
                time.sleep(1)
        finally:
            # Ensure threads can stop
            self._stop.set()
            out_thread.join(timeout=2)
            hb_thread.join(timeout=2)

        rc = proc.poll()
        if rc == 0:
            self.store.update_job(job_id, status=STATUS_SUCCEEDED, progress=100)
            self.store.append_event(job_id, f"process_exited: {rc}", event_type="system")
        else:
            self.store.update_job(job_id, status=STATUS_FAILED, error=f"exit_code:{rc}")
            self.store.append_event(job_id, f"process_failed: {rc}", event_type="system")
        return job_id

    def run_forever(self, poll_interval: int = 5):
        while True:
            try:
                jid = self.run_once()
                if jid is None:
                    time.sleep(poll_interval)
                else:
                    # after finishing a job, small pause to allow UI to catch up
                    time.sleep(1)
            except KeyboardInterrupt:
                break
            except Exception as exc:
                # record unexpected errors and continue
                with suppress(Exception):
                    self.store.append_event(0, f"worker_error: {exc}", event_type="system")
                time.sleep(poll_interval)


if __name__ == "__main__":
    w = Worker()
    w.run_forever()
