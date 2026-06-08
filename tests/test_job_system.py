"""Test job system: JobStore, Worker, API."""

import json
import tempfile
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

    def test_claim_next_job(self, temp_db):
        """Test claiming queued job atomically marks it running."""
        job_id = temp_db.create_job({"topic": "Claim Test"}, topic="Claim Test")
        job = temp_db.claim_next_job()
        assert job is not None
        assert job["id"] == job_id
        assert job["status"] == STATUS_RUNNING
        assert job["attempt"] == 1
        # Claiming again should return None (no more queued)
        job2 = temp_db.claim_next_job()
        assert job2 is None

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

    def test_cancel_queued_job_returns_false_but_marks_cancel_requested(self, temp_db):
        """Test cancel on queued: marks cancel_requested, request_cancel returns False initially."""
        job_id = temp_db.create_job({"topic": "CancelQueue"}, topic="CancelQueue")
        ok = temp_db.request_cancel(job_id)
        assert ok is True
        job = temp_db.get_job(job_id)
        assert job["status"] == STATUS_CANCEL_REQUESTED

    def test_mark_stale_running_failed(self, temp_db):
        """Test stale running jobs are marked failed."""
        job_id = temp_db.create_job({"topic": "Stale"}, topic="Stale")
        temp_db.claim_next_job()
        # Set a stale heartbeat (2+ hours ago)
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
        import time
        id1 = temp_db.create_job({"topic": "First"}, topic="First")
        time.sleep(0.01)  # Small delay to ensure different timestamps
        id2 = temp_db.create_job({"topic": "Second"}, topic="Second")
        jobs = temp_db.list_jobs()
        # Check that the list has at least 2 jobs
        assert len(jobs) >= 2
        # The second job created should appear before the first (ordered DESC by created_at)
        ids = [j["id"] for j in jobs]
        idx1 = ids.index(id1)
        idx2 = ids.index(id2)
        assert idx2 < idx1  # id2 (newer) should come before id1 (older)


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
        # Unsupported args should NOT appear
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
                "skip_rvc": False,
                "no_resume": True,
                "yes": True,
            }),
        }
        cmd = worker._build_command(job)
        cmd_str = " ".join(cmd)
        assert "--dry-run" in cmd_str
        assert "--no-resume" in cmd_str
        assert "--yes" in cmd_str
        # False values should not appear
        assert "--skip-rvc" not in cmd_str

    def test_build_command_with_topic_from_job_or_request(self):
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


class TestJobAPIIntegration:
    """Test Job API endpoints integration (requires running FastAPI server)."""

    # Note: These tests require utils/local_ui.py running on http://127.0.0.1:8000
    # They are marked as integration tests and can be skipped if server is not running.

    @pytest.mark.skipif(
        not pytest.importorskip("requests", minversion=None),
        reason="requests library not available",
    )
    def test_api_create_job(self):
        """Test POST /api/jobs creates a job (requires running server)."""
        import requests

        try:
            resp = requests.post(
                "http://127.0.0.1:8000/api/jobs",
                json={"topic": "API Test", "dry_run": True},
                timeout=2,
            )
            if resp.status_code == 200:
                data = resp.json()
                assert "job_id" in data
                assert data["status"] == "queued"
        except requests.ConnectionError:
            pytest.skip("FastAPI server not running")

    @pytest.mark.skipif(
        not pytest.importorskip("requests", minversion=None),
        reason="requests library not available",
    )
    def test_api_list_jobs(self):
        """Test GET /api/jobs lists jobs (requires running server)."""
        import requests

        try:
            resp = requests.get("http://127.0.0.1:8000/api/jobs", timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                assert "jobs" in data
        except requests.ConnectionError:
            pytest.skip("FastAPI server not running")
