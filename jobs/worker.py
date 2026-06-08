import json
import os
import signal
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

if os.name == "nt":
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CTRL_BREAK_EVENT = signal.CTRL_BREAK_EVENT
else:
    CREATE_NEW_PROCESS_GROUP = 0
    CTRL_BREAK_EVENT = signal.SIGINT

from jobs.job_store import (
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELED,
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
        # Check checkpoint: if it's just a name (no path separators), resolve to ComfyUI models/checkpoints
        checkpoint = job.get("comfyui_checkpoint")
        if checkpoint:
            if "\\" not in checkpoint and "/" not in checkpoint:
                # It's a model name, resolve to ComfyUI path
                comfyui_root = Path("external/ComfyUI").resolve()
                cp_path = comfyui_root / "models" / "checkpoints" / checkpoint
                if not cp_path.exists():
                    raise RuntimeError(f"ComfyUI checkpoint not found: {cp_path} (resolved from model name: {checkpoint})")
            else:
                # It's a file path
                cp = Path(checkpoint)
                if not cp.exists():
                    raise RuntimeError(f"ComfyUI checkpoint not found: {cp}")
        # Check default ComfyUI server URL
        try:
            import urllib.request

            with urllib.request.urlopen("http://127.0.0.1:8188/", timeout=5) as resp:  # type: ignore
                if resp.status >= 400:
                    raise RuntimeError("ComfyUI server returned error")
        except Exception as exc:
            raise RuntimeError(f"ComfyUI preflight failed: {exc}") from exc

    def _build_command(self, job: dict[str, Any]) -> list:
        req = job.get("request_json")
        if isinstance(req, str):
            try:
                req = json.loads(req)
            except Exception as exc:
                raise ValueError(f"Invalid request_json JSON: {exc}") from exc
        elif req is None:
            req = {}
        cmd = [str(VENV_PY), str(BOOTSTRAP)]
        topic = req.get("topic") or job.get("topic")
        if topic:
            cmd += ["--topic", str(topic)]

        # Handle content_text: save to temp file and pass via --file
        content_text = req.pop("content_text", None)
        if content_text:
            job_id = job.get("id", "temp")
            temp_file = REPO_ROOT / "jobs" / f"_{job_id}_content.txt"
            temp_file.write_text(content_text, encoding="utf-8")
            cmd += ["--file", str(temp_file)]

        # Supported bootstrap_pipeline.py args (filter out job metadata keys)
        supported_args = {
            "duration", "dry_run", "no_resume", "skip_rvc", "file", "project",
            "series", "director_mode", "run_mode", "eval_models", "preview",
            "skip_preflight", "preflight_only", "words_per_segment",
            "images_per_segment", "segment_count", "yes", "topics_file", "source"
        }
        for k, v in req.items():
            if k == "topic" or k not in supported_args:
                continue
            arg = f"--{k.replace('_', '-')}"
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
        if proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                text = line.rstrip("\n")
                self.store.append_event(job_id, text, event_type="log")
                # also refresh heartbeat on output
                with suppress(Exception):
                    self.store.update_job(job_id, heartbeat_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        except Exception as exc:
            self.store.append_event(job_id, f"stream_error: {exc}", event_type="system")

    def run_once(self) -> int | None:
        # First, check for queued jobs marked cancel_requested and mark them canceled
        conn = self.store._connect()
        cur = conn.cursor()
        rows = cur.execute("SELECT id FROM jobs WHERE status=?", (STATUS_CANCEL_REQUESTED,)).fetchall()
        for r in rows:
            job_id = r[0]
            self.store.update_job(job_id, status=STATUS_CANCELED)
            self.store.append_event(job_id, "canceled_from_queued", event_type="system")
        conn.close()

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

        try:
            cmd = self._build_command(job)
        except ValueError as exc:
            self.store.append_event(job_id, f"invalid_request: {exc}", event_type="system")
            self.store.update_job(job_id, status=STATUS_FAILED, error=str(exc))
            return job_id

        self.store.append_event(job_id, f"starting: {' '.join(cmd)}", event_type="system")

        proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NEW_PROCESS_GROUP)

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
                        proc.send_signal(CTRL_BREAK_EVENT)
                    except Exception:
                        proc.terminate()
                    # wait up to CANCEL_WAIT_SECONDS
                    waited = 0
                    while proc.poll() is None and waited < CANCEL_WAIT_SECONDS:
                        time.sleep(1)
                        waited += 1
                    if proc.poll() is None:
                        proc.kill()
                    self.store.update_job(job_id, status=STATUS_CANCELED)
                    break
                time.sleep(1)
        finally:
            # Allow threads to stop and reset stop event for next job
            self._stop.set()
            out_thread.join(timeout=2)
            hb_thread.join(timeout=2)
            # Clear stop event so heartbeat/output threads run for next job
            self._stop.clear()

        rc = proc.poll()
        # Only update status if not already canceled
        j = self.store.get_job(job_id)
        if j and j.get("status") != STATUS_CANCELED:
            if rc == 0:
                self.store.update_job(job_id, status=STATUS_SUCCEEDED, progress=100)
                self.store.append_event(job_id, f"process_exited: {rc}", event_type="system")
                # Try to capture output artifacts
                try:
                    topic = j.get("topic") or "unknown"
                    output_root = REPO_ROOT / "studio_outputs" / topic
                    if output_root.exists():
                        # Find latest video
                        videos = list(output_root.glob("*.mp4"))
                        if videos:
                            latest_video = max(videos, key=lambda p: p.stat().st_mtime)
                            self.store.update_job(job_id, output_path=str(latest_video))
                            self.store.append_event(job_id, f"output_video: {latest_video.name}", event_type="artifact")
                        # Capture manifest if present
                        manifest = output_root / "manifest.json"
                        if manifest.exists():
                            self.store.append_event(job_id, "manifest: manifest.json", event_type="artifact")
                except Exception as exc:
                    self.store.append_event(job_id, f"artifact_capture_failed: {exc}", event_type="system")
            else:
                self.store.update_job(job_id, status=STATUS_FAILED, error=f"exit_code:{rc}")
                self.store.append_event(job_id, f"process_failed: {rc}", event_type="system")

        # Cleanup temp content file if it was created
        try:
            temp_file = REPO_ROOT / "jobs" / f"_{job_id}_content.txt"
            if temp_file.exists():
                temp_file.unlink()
        except Exception as exc:
            self.store.append_event(job_id, f"cleanup_warning: {exc}", event_type="system")
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
