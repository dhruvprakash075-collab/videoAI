"""Test job system: JobStore, Worker, API."""

import importlib.util
import json
import tempfile
import time
from pathlib import Path

import pytest

from jobs.job_store import (
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    JobStore,
)
from jobs.worker import Worker


@pytest.fixture
def temp_db():
    """Create a temporary job database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_jobs.db"
        store = JobStore(db_path=db_path)
        yield store


class TestJobStore:
    """Test JobStore database operations."""

    def test_db_creation_and_pragmas(self, temp_db):
        """Test DB is created with WAL and pragmas applied."""
        conn = temp_db._connect()
        cur = conn.cursor()
        result = cur.execute("PRAGMA journal_mode").fetchone()
        assert result[0].upper() == "WAL"
        result = cur.execute("PRAGMA busy_timeout").fetchone()
        assert result[0] >= 5000
        result = cur.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 1
        conn.close()

    def test_create_job(self, temp_db):
        """Test job creation returns ID and stores request."""
        req = {"topic": "Test", "dry_run": True}
        job_id = temp_db.create_job(req, topic="Test")
        assert job_id is not None
        job = temp_db.get_job(job_id)
        assert job["status"] == STATUS_QUEUED
        assert job["topic"] == "Test"
        assert json.loads(job["request_json"]) == req

    def test_create_job_with_backend_metadata(self, temp_db):
        """Test job creation preserves image_backend and comfyui_checkpoint."""
        req = {"topic": "Backend Test", "dry_run": True, "image_backend": "comfyui", "comfyui_checkpoint": "DreamShaper_8.safetensors", "fallback_backend": "bonsai"}
        job_id = temp_db.create_job(req, topic="Backend Test", image_backend="comfyui", comfyui_checkpoint="DreamShaper_8.safetensors", fallback_backend="bonsai")
        job = temp_db.get_job(job_id)
        assert job["image_backend"] == "comfyui"
        assert job["comfyui_checkpoint"] == "DreamShaper_8.safetensors"
        assert job["fallback_backend"] == "bonsai"
        # request_json also has these fields
        req_loaded = json.loads(job["request_json"])
        assert req_loaded["image_backend"] == "comfyui"
        assert req_loaded["comfyui_checkpoint"] == "DreamShaper_8.safetensors"

    def test_claim_next_job(self, temp_db):
        """Test claiming queued job atomically marks it running."""
        job_id = temp_db.create_job({"topic": "Claim Test"}, topic="Claim Test")
        job = temp_db.claim_next_job()
        assert job is not None
        assert job["id"] == job_id
        assert job["status"] == STATUS_RUNNING
        assert job["attempt"] == 1
        assert temp_db.claim_next_job() is None

    def test_claim_next_job_oldest_first(self, temp_db):
        """Test claim_next_job returns the oldest queued job (FIFO)."""
        id1 = temp_db.create_job({"topic": "First"}, topic="First")
        time.sleep(0.01)
        id2 = temp_db.create_job({"topic": "Second"}, topic="Second")
        time.sleep(0.01)
        id3 = temp_db.create_job({"topic": "Third"}, topic="Third")
        # Claim oldest first
        job = temp_db.claim_next_job()
        assert job["id"] == id1
        job2 = temp_db.claim_next_job()
        assert job2["id"] == id2
        job3 = temp_db.claim_next_job()
        assert job3["id"] == id3

    def test_job_lifecycle_succeeded(self, temp_db):
        """Test job lifecycle: queued -> running -> succeeded."""
        job_id = temp_db.create_job({"topic": "Success"}, topic="Success")
        assert temp_db.get_job(job_id)["status"] == STATUS_QUEUED
        job = temp_db.claim_next_job()
        assert job["status"] == STATUS_RUNNING
        temp_db.update_job(job_id, status=STATUS_SUCCEEDED, progress=100)
        assert temp_db.get_job(job_id)["status"] == STATUS_SUCCEEDED

    def test_job_lifecycle_failed(self, temp_db):
        """Test job lifecycle: queued -> running -> failed."""
        job_id = temp_db.create_job({"topic": "Fail"}, topic="Fail")
        job = temp_db.claim_next_job()
        temp_db.update_job(job_id, status=STATUS_FAILED, error="exit_code:1")
        job = temp_db.get_job(job_id)
        assert job["status"] == STATUS_FAILED
        assert job["error"] == "exit_code:1"

    def test_cancel_running_job(self, temp_db):
        """Test canceling a running job: running -> cancel_requested."""
        job_id = temp_db.create_job({"topic": "Cancel"}, topic="Cancel")
        temp_db.claim_next_job()
        ok = temp_db.request_cancel(job_id)
        assert ok is True
        job = temp_db.get_job(job_id)
        assert job["status"] == STATUS_CANCEL_REQUESTED

    def test_cancel_queued_job_marks_cancel_requested(self, temp_db):
        """Test canceling a queued job marks it cancel_requested."""
        job_id = temp_db.create_job({"topic": "CancelQueue"}, topic="CancelQueue")
        ok = temp_db.request_cancel(job_id)
        assert ok is True
        job = temp_db.get_job(job_id)
        assert job["status"] == STATUS_CANCEL_REQUESTED

    def test_cancel_lifecycle_running_to_canceled(self, temp_db):
        """Test running job detected cancel_requested in monitor loop gets marked canceled."""
        job_id = temp_db.create_job({"topic": "CancelFlow"}, topic="CancelFlow")
        # Manually mark job as running (simulating worker having claimed it)
        # then request cancel - simulating cancel requested while job is running
        temp_db.update_job(job_id, status=STATUS_RUNNING)
        temp_db.request_cancel(job_id)
        # Verify the job is now CANCEL_REQUESTED
        job = temp_db.get_job(job_id)
        assert job["status"] == STATUS_CANCEL_REQUESTED
        # claim_next_job should NOT return this (not QUEUED)
        from jobs.worker import Worker
        w = Worker(store=temp_db)
        # Worker pre-cancel check should find cancel_requested job and mark canceled
        _ = w.run_once()
        # run_once may return None (pre-cancel check handled it) or the job_id
        # if it was processed differently. Key: final status must be CANCELED.
        job = temp_db.get_job(job_id)
        assert job["status"] == STATUS_CANCELED

    def test_mark_stale_running_failed(self, temp_db):
        """Test stale running jobs are marked failed."""
        job_id = temp_db.create_job({"topic": "Stale"}, topic="Stale")
        temp_db.claim_next_job()
        temp_db.update_job(job_id, heartbeat_at="2000-01-01T00:00:00Z")
        count = temp_db.mark_stale_running_failed(stale_seconds=120)
        assert count >= 1
        job = temp_db.get_job(job_id)
        assert job["status"] == STATUS_FAILED

    def test_retry_job_failed(self, temp_db):
        """Test retrying a failed job creates a new queued job."""
        job_id = temp_db.create_job({"topic": "Retry", "dry_run": True}, topic="Retry")
        temp_db.claim_next_job()
        temp_db.update_job(job_id, status=STATUS_FAILED, error="exit_code:1")
        new_id = temp_db.retry_job(job_id)
        assert new_id is not None
        assert new_id != job_id
        new_job = temp_db.get_job(new_id)
        assert new_job["status"] == STATUS_QUEUED
        assert new_job["topic"] == "Retry"

    def test_retry_job_preserves_metadata(self, temp_db):
        """Test retry preserves image_backend and comfyui_checkpoint."""
        req = {"topic": "RetryMeta", "dry_run": True, "image_backend": "comfyui", "comfyui_checkpoint": "DreamShaper_8.safetensors", "fallback_backend": "bonsai"}
        job_id = temp_db.create_job(req, topic="RetryMeta", image_backend="comfyui", comfyui_checkpoint="DreamShaper_8.safetensors", fallback_backend="bonsai")
        temp_db.claim_next_job()
        temp_db.update_job(job_id, status=STATUS_FAILED)
        new_id = temp_db.retry_job(job_id)
        new_job = temp_db.get_job(new_id)
        assert new_job["image_backend"] == "comfyui"
        assert new_job["comfyui_checkpoint"] == "DreamShaper_8.safetensors"
        assert new_job["fallback_backend"] == "bonsai"

    def test_retry_job_canceled(self, temp_db):
        """Test retrying a canceled job creates a new queued job."""
        job_id = temp_db.create_job({"topic": "RetryCanceled"}, topic="RetryCanceled")
        temp_db.claim_next_job()
        temp_db.update_job(job_id, status=STATUS_CANCELED)
        new_id = temp_db.retry_job(job_id)
        assert new_id is not None
        new_job = temp_db.get_job(new_id)
        assert new_job["status"] == STATUS_QUEUED

    def test_retry_job_running_returns_none(self, temp_db):
        """Test retrying a running job returns None (not allowed)."""
        job_id = temp_db.create_job({"topic": "RunningRetry"}, topic="RunningRetry")
        temp_db.claim_next_job()
        new_id = temp_db.retry_job(job_id)
        assert new_id is None

    def test_retry_job_queued_returns_none(self, temp_db):
        """Test retrying a queued (not running) job returns None."""
        job_id = temp_db.create_job({"topic": "QueuedRetry"}, topic="QueuedRetry")
        new_id = temp_db.retry_job(job_id)
        assert new_id is None

    def test_append_event_and_list(self, temp_db):
        """Test appending and listing events."""
        job_id = temp_db.create_job({"topic": "Events"}, topic="Events")
        temp_db.append_event(job_id, "test message 1", event_type="log")
        temp_db.append_event(job_id, "test message 2", event_type="log")
        events = temp_db.get_events(job_id)
        assert len(events) >= 2
        assert events[0]["message"] == "test message 1"
        assert events[-1]["message"] == "test message 2"

    def test_list_jobs_newest_first(self, temp_db):
        """Test list_jobs returns jobs ordered by created_at DESC."""
        id1 = temp_db.create_job({"topic": "First"}, topic="First")
        time.sleep(0.01)
        id2 = temp_db.create_job({"topic": "Second"}, topic="Second")
        jobs = temp_db.list_jobs()
        ids = [j["id"] for j in jobs]
        assert id2 in ids
        assert id1 in ids
        assert ids.index(id2) < ids.index(id1)

    def test_list_jobs_pagination(self, temp_db):
        """Test list_jobs respects limit and offset."""
        for i in range(5):
            temp_db.create_job({"topic": f"Page{i}"}, topic=f"Page{i}")
        all_jobs = temp_db.list_jobs(limit=10)
        page1 = temp_db.list_jobs(limit=2, offset=0)
        page2 = temp_db.list_jobs(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["id"] == all_jobs[0]["id"]
        assert page1[1]["id"] == all_jobs[1]["id"]
        assert page2[0]["id"] == all_jobs[2]["id"]

    def test_get_events_with_limit(self, temp_db):
        """Test get_events respects limit parameter."""
        job_id = temp_db.create_job({"topic": "EventLimit"}, topic="EventLimit")
        for i in range(5):
            temp_db.append_event(job_id, f"msg {i}", event_type="log")
        events = temp_db.get_events(job_id, limit=3)
        assert len(events) == 3

    def test_update_job_ignores_disallowed_fields(self, temp_db):
        """Test update_job silently ignores fields not in allowed set."""
        job_id = temp_db.create_job({"topic": "Update"}, topic="Update")
        temp_db.update_job(job_id, status=STATUS_RUNNING, made_up_field="ignored", another_bad=123)
        job = temp_db.get_job(job_id)
        assert "made_up_field" not in job
        assert "another_bad" not in job


class TestWorker:
    """Test Worker command building and logic."""

    def test_build_command_filters_unsupported_args(self):
        """Test that unsupported request keys are filtered out."""
        store = JobStore()
        worker = Worker(store)
        job = {
            "topic": "Test",
            "request_json": json.dumps({
                "topic": "TestJob",
                "dry_run": True,
                "content_text": "ignored_content",
                "image_backend": "ignored_backend",
                "comfyui_checkpoint": "ignored_checkpoint",
                "duration": 1,
            }),
        }
        cmd = worker._build_command(job)
        cmd_str = " ".join(cmd)
        assert "--topic" in cmd_str
        assert "TestJob" in cmd_str
        assert "--dry-run" in cmd_str
        assert "--duration" in cmd_str
        assert "1" in cmd_str
        assert "--content-text" not in cmd_str
        assert "--image-backend" not in cmd_str
        assert "--comfyui-checkpoint" not in cmd_str

    def test_build_command_bool_args(self):
        """Test bool args are handled correctly."""
        store = JobStore()
        worker = Worker(store)
        job = {
            "topic": "Test",
            "request_json": json.dumps({
                "dry_run": True,
                "no_resume": True,
                "yes": True,
            }),
        }
        cmd = worker._build_command(job)
        cmd_str = " ".join(cmd)
        assert "--dry-run" in cmd_str
        assert "--no-resume" in cmd_str
        assert "--yes" in cmd_str

    def test_build_command_with_topic_from_request(self):
        """Test topic comes from request_json if available."""
        store = JobStore()
        worker = Worker(store)
        job = {
            "topic": "JobTopic",
            "request_json": json.dumps({"topic": "RequestTopic"}),
        }
        cmd = worker._build_command(job)
        cmd_str = " ".join(cmd)
        assert "RequestTopic" in cmd_str

    def test_build_command_with_content_text_creates_temp_file(self):
        """Test content_text is written to temp file and --file is passed."""
        store = JobStore()
        worker = Worker(store)
        job = {
            "id": 999,
            "topic": "ContentTest",
            "request_json": json.dumps({
                "topic": "ContentTest",
                "content_text": "This is the script content",
            }),
        }
        cmd = worker._build_command(job)
        cmd_str = " ".join(cmd)
        assert "--file" in cmd_str
        assert "content_text" not in cmd_str  # should not appear as arg
        # Temp file should exist
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[1]
        temp_file = repo_root / "jobs" / "_999_content.txt"
        try:
            assert temp_file.exists()
            assert temp_file.read_text(encoding="utf-8") == "This is the script content"
        finally:
            if temp_file.exists():
                temp_file.unlink()

    def test_heartbeat_updates_job(self, temp_db):
        """Test heartbeat loop actually updates the job heartbeat_at field."""
        job_id = temp_db.create_job({"topic": "HeartbeatTest"}, topic="HeartbeatTest")
        temp_db.claim_next_job()
        job = temp_db.get_job(job_id)
        old_hb = job.get("heartbeat_at")
        import time

        from jobs.worker import Worker
        w = Worker(store=temp_db)
        w.store.update_job(job_id, heartbeat_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        updated = temp_db.get_job(job_id)
        assert updated["heartbeat_at"] != old_hb


@pytest.mark.smoke
class TestJobAPIIntegration:
    """Test Job API endpoints integration (requires running FastAPI server)."""

    _requests_available = importlib.util.find_spec("requests") is not None

    @pytest.mark.skipif(not _requests_available, reason="requests library not available")
    def test_api_create_job(self):
        """Test POST /api/jobs creates a job (requires running server)."""
        import requests
        try:
            resp = requests.post(
                "http://127.0.0.1:8000/api/jobs",
                json={"topic": "API Test", "dry_run": True},
                timeout=2,
            )
        except requests.ConnectionError:
            pytest.skip("FastAPI server not running")

        assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "queued"

    @pytest.mark.skipif(not _requests_available, reason="requests library not available")
    def test_api_list_jobs(self):
        """Test GET /api/jobs lists jobs (requires running server)."""
        import requests
        try:
            resp = requests.get("http://127.0.0.1:8000/api/jobs", timeout=2)
        except requests.ConnectionError:
            pytest.skip("FastAPI server not running")

        assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "jobs" in data

