import json
from pathlib import Path
from unittest.mock import patch

from jobs.job_store import (
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    JobStore,
    _now_iso,
)
from jobs.worker import Worker


class _FakeThread:
    def __init__(self, target, args=(), daemon=False):
        self.target = target
        self.args = args

    def start(self):
        if getattr(self.target, "__name__", "") == "_stream_process":
            self.target(*self.args)

    def join(self, timeout=None):
        return None


class _FakeProcess:
    def __init__(self, rc=0, stdout=None):
        self.rc = rc
        self.stdout = stdout if stdout is not None else ["log line\n"]

    def poll(self):
        return self.rc


def _store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.db")


def _events(store: JobStore, job_id: int) -> list[str]:
    return [event["message"] for event in store.get_events(job_id)]


def test_worker_cancels_queued_cancel_requests(tmp_path: Path):
    store = _store(tmp_path)
    job_id = store.create_job({"topic": "topic"}, topic="topic")
    assert store.request_cancel(job_id) is True

    assert Worker(store).run_once() is None

    assert store.get_job(job_id)["status"] == STATUS_CANCELED
    assert "canceled_from_queued" in _events(store, job_id)


def test_worker_marks_preflight_failure(tmp_path: Path):
    store = _store(tmp_path)
    job_id = store.create_job({"topic": "topic"}, topic="topic", image_backend="comfyui")
    worker = Worker(store)

    with patch.object(worker, "_preflight_comfyui", side_effect=RuntimeError("no comfy")):
        assert worker.run_once() == job_id

    job = store.get_job(job_id)
    assert job["status"] == STATUS_FAILED
    assert job["error"] == "no comfy"
    assert any(message.startswith("preflight_failed: no comfy") for message in _events(store, job_id))


def test_worker_marks_invalid_request_json_failed(tmp_path: Path):
    store = _store(tmp_path)
    conn = store._connect()
    now = _now_iso()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO jobs (status, topic, request_json, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("queued", "topic", "{bad-json", now, now),
    )
    job_id = cur.lastrowid
    conn.commit()
    conn.close()

    worker = Worker(store)
    with patch.object(worker, "_preflight_comfyui"):
        assert worker.run_once() == job_id

    job = store.get_job(job_id)
    assert job["status"] == STATUS_FAILED
    assert "Invalid request_json JSON" in job["error"]


def test_worker_records_failed_process_exit(tmp_path: Path):
    store = _store(tmp_path)
    job_id = store.create_job({"topic": "topic"}, topic="topic")
    worker = Worker(store)

    with (
        patch.object(worker, "_preflight_comfyui"),
        patch("jobs.worker.threading.Thread", _FakeThread),
        patch("jobs.worker.subprocess.Popen", return_value=_FakeProcess(rc=7)),
        patch("jobs.worker.REPO_ROOT", tmp_path),
    ):
        assert worker.run_once() == job_id

    job = store.get_job(job_id)
    assert job["status"] == STATUS_FAILED
    assert job["error"] == "exit_code:7"
    assert "process_failed: 7" in _events(store, job_id)


def test_worker_success_streams_logs_captures_artifacts_and_cleans_content(tmp_path: Path):
    store = _store(tmp_path)
    job_id = store.create_job(
        {"topic": "topic", "content_text": "body", "dry_run": True, "ignored": "x"},
        topic="topic",
    )
    repo = tmp_path
    (repo / "jobs").mkdir()
    output_root = repo / "studio_outputs" / "topic"
    output_root.mkdir(parents=True)
    video = output_root / "out.mp4"
    video.write_bytes(b"mp4")
    manifest = output_root / "run_manifest.json"
    manifest.write_text(json.dumps({"ok": True}), encoding="utf-8")

    worker = Worker(store)
    with (
        patch.object(worker, "_preflight_comfyui"),
        patch("jobs.worker.threading.Thread", _FakeThread),
        patch("jobs.worker.subprocess.Popen", return_value=_FakeProcess(rc=0, stdout=["hello\n"])),
        patch("jobs.worker.REPO_ROOT", repo),
    ):
        assert worker.run_once() == job_id

    job = store.get_job(job_id)
    assert job["status"] == STATUS_SUCCEEDED
    assert job["progress"] == 100
    assert job["output_path"] == str(video)
    assert not (repo / "jobs" / f"_{job_id}_content.txt").exists()
    artifacts = {artifact["key"]: artifact["path"] for artifact in store.get_artifacts(job_id)}
    assert artifacts == {"output_video": str(video), "manifest": str(manifest)}
    messages = _events(store, job_id)
    assert "hello" in messages
    assert "process_exited: 0" in messages


def test_build_command_filters_supported_args_and_writes_content_file(tmp_path: Path):
    store = _store(tmp_path)
    worker = Worker(store)
    repo = tmp_path
    (repo / "jobs").mkdir()

    with patch("jobs.worker.REPO_ROOT", repo):
        cmd = worker._build_command(
            {
                "id": 4,
                "topic": "fallback",
                "request_json": {
                    "topic": "primary",
                    "content_text": "body",
                    "dry_run": True,
                    "duration": 12,
                    "skip_me": "x",
                    "preview": False,
                    "source": None,
                },
            }
        )

    assert "--topic" in cmd and "primary" in cmd
    assert "--dry-run" in cmd
    assert "--duration" in cmd and "12" in cmd
    assert "--skip-me" not in cmd
    assert "--preview" not in cmd
    assert "--source" not in cmd
    assert (repo / "jobs" / "_4_content.txt").read_text(encoding="utf-8") == "body"


def test_preflight_and_config_helpers_cover_failure_paths(tmp_path: Path):
    worker = Worker(_store(tmp_path))
    assert worker._preflight_comfyui({"image_backend": "mock"}) is None

    with patch("jobs.worker.REPO_ROOT", tmp_path):
        assert worker._get_comfyui_url() == "http://127.0.0.1:8188/"
        try:
            worker._preflight_comfyui({"image_backend": "comfyui", "comfyui_checkpoint": "missing.safetensors"})
            raise AssertionError("missing checkpoint should fail")
        except RuntimeError as exc:
            assert "checkpoint not found" in str(exc)


def test_stream_process_handles_missing_stdout_and_stream_errors(tmp_path: Path):
    store = _store(tmp_path)
    job_id = store.create_job({"topic": "topic"}, topic="topic")
    worker = Worker(store)

    proc = _FakeProcess(stdout=None)
    worker._stream_process(proc, job_id)

    class BadStdout:
        def __iter__(self):
            raise RuntimeError("pipe broke")

    worker._stream_process(_FakeProcess(stdout=BadStdout()), job_id)
    assert "stream_error: pipe broke" in _events(store, job_id)


def test_run_once_cancellation_terminates_running_process(tmp_path: Path):
    store = _store(tmp_path)
    job_id = store.create_job({"topic": "topic"}, topic="topic")
    worker = Worker(store)

    class CancelProcess:
        stdout = []

        def __init__(self):
            self.polls = 0
            self.signaled = False
            self.terminated = False
            self.killed = False

        def poll(self):
            return None

        def send_signal(self, _signal):
            self.signaled = True
            raise RuntimeError("no ctrl break")

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    proc = CancelProcess()

    def fake_sleep(_seconds):
        if store.get_job(job_id)["status"] == STATUS_RUNNING:
            store.request_cancel(job_id)

    with (
        patch.object(worker, "_preflight_comfyui"),
        patch("jobs.worker.threading.Thread", _FakeThread),
        patch("jobs.worker.subprocess.Popen", return_value=proc),
        patch("jobs.worker.time.sleep", side_effect=fake_sleep),
        patch("jobs.worker.CANCEL_WAIT_SECONDS", 0),
        patch("jobs.worker.REPO_ROOT", tmp_path),
    ):
        assert worker.run_once() == job_id

    assert store.get_job(job_id)["status"] == STATUS_CANCELED
    assert proc.signaled is True
    assert proc.terminated is True
    assert proc.killed is True
    assert "cancellation_requested" in _events(store, job_id)


def test_run_forever_sleeps_when_idle_and_records_unexpected_errors(tmp_path: Path):
    worker = Worker(_store(tmp_path))
    calls = iter([None, RuntimeError("boom"), KeyboardInterrupt()])

    def run_once():
        value = next(calls)
        if isinstance(value, BaseException):
            raise value
        return value

    with (
        patch.object(worker, "run_once", side_effect=run_once),
        patch("jobs.worker.time.sleep") as sleep,
    ):
        worker.run_forever(poll_interval=3)

    assert sleep.call_count == 2


def test_job_store_list_cancel_retry_stale_and_noop_paths(tmp_path: Path):
    store = _store(tmp_path)
    missing_cancel = store.request_cancel(999)
    failed_id = store.create_job({"topic": "failed"}, topic="failed")
    canceled_id = store.create_job({"topic": "canceled"}, topic="canceled")
    running_id = store.create_job({"topic": "running"}, topic="running")
    fresh_id = store.create_job({"topic": "fresh"}, topic="fresh")

    store.update_job(failed_id, status=STATUS_FAILED)
    store.update_job(canceled_id, status=STATUS_CANCELED)
    store.update_job(running_id, status=STATUS_RUNNING, heartbeat_at="2000-01-01T00:00:00Z")
    store.update_job(fresh_id, status=STATUS_SUCCEEDED)
    store.update_job(fresh_id, not_a_column="ignored")

    assert missing_cancel is False
    assert store.request_cancel(fresh_id) is False
    assert store.list_jobs(limit=2, offset=0)
    assert len(store.get_events(failed_id, limit=1)) == 0
    assert store.mark_stale_running_failed(stale_seconds=1) == 1
    assert store.get_job(running_id)["status"] == STATUS_FAILED

    retry_failed = store.retry_job(failed_id)
    retry_canceled = store.retry_job(canceled_id)
    assert retry_failed is not None
    assert retry_canceled is not None
    assert store.retry_job(fresh_id) is None
    assert store.retry_job(999) is None
    assert store.get_job(retry_failed)["status"] == "queued"
    assert any(message.startswith("retry_created:") for message in _events(store, failed_id))


def test_mark_stale_running_failed_handles_bad_heartbeat_and_cancel_terminal_paths(tmp_path: Path):
    store = _store(tmp_path)
    stale_id = store.create_job({"topic": "stale"}, topic="stale")
    none_id = store.create_job({"topic": "none"}, topic="none")
    active_id = store.create_job({"topic": "active"}, topic="active")
    cancel_id = store.create_job({"topic": "cancel"}, topic="cancel")
    store.update_job(stale_id, status=STATUS_RUNNING, heartbeat_at="not-a-date")
    store.update_job(none_id, status=STATUS_RUNNING, heartbeat_at=None)
    store.update_job(active_id, status=STATUS_RUNNING, heartbeat_at=_now_iso())
    store.update_job(cancel_id, status=STATUS_CANCEL_REQUESTED)

    assert store.mark_stale_running_failed(stale_seconds=999999) == 2
    assert store.get_job(stale_id)["status"] == STATUS_FAILED
    assert store.get_job(none_id)["status"] == STATUS_FAILED
    assert store.get_job(active_id)["status"] == STATUS_RUNNING
    assert store.request_cancel(cancel_id) is False
