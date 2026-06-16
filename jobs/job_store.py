import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path("studio_projects") / "jobs" / "video_ai_jobs.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_CANCEL_REQUESTED = "cancel_requested"
STATUS_CANCELED = "canceled"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'.

    The worker writes heartbeats as "...%H:%M:%SZ" while _now_iso() writes
    "...+00:00". datetime.fromisoformat() rejects the 'Z' suffix on Python
    < 3.11, which previously made mark_stale_running_failed() treat every
    Z-stamped heartbeat as unparseable and wrongly fail healthy running jobs.
    Normalize 'Z' to '+00:00' so both formats parse on all supported versions.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


class JobStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._ensure_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Pragmas per blueprint
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _ensure_db(self) -> None:
        conn = self._connect()
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                topic TEXT,
                request_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                heartbeat_at TEXT,
                progress INTEGER DEFAULT 0,
                attempt INTEGER DEFAULT 0,
                image_backend TEXT,
                comfyui_checkpoint TEXT,
                fallback_backend TEXT,
                output_path TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                event_type TEXT,
                message TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS job_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                key TEXT,
                path TEXT,
                meta TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            """
        )
        conn.commit()
        conn.close()

    def create_job(
        self,
        request_json: dict[str, Any],
        topic: str | None = None,
        *,
        image_backend: str | None = None,
        comfyui_checkpoint: str | None = None,
        fallback_backend: str | None = None,
    ) -> int:
        conn = self._connect()
        cur = conn.cursor()
        now = _now_iso()
        payload = json.dumps(request_json or {})
        cur.execute(
            "INSERT INTO jobs (status, topic, request_json, created_at, updated_at, attempt, image_backend, comfyui_checkpoint, fallback_backend) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                STATUS_QUEUED,
                topic,
                payload,
                now,
                now,
                0,
                image_backend,
                comfyui_checkpoint,
                fallback_backend,
            ),
        )
        job_id = cur.lastrowid
        conn.commit()
        conn.close()
        return job_id

    def claim_next_job(self) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job and mark it running."""
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE;")
            row = cur.execute(
                "SELECT id FROM jobs WHERE status=? ORDER BY created_at LIMIT 1", (STATUS_QUEUED,)
            ).fetchone()
            if not row:
                conn.commit()
                return None
            job_id = row[0]
            now = _now_iso()
            cur.execute(
                "UPDATE jobs SET status=?, heartbeat_at=?, updated_at=?, attempt = attempt + 1 WHERE id=?",
                (STATUS_RUNNING, now, now, job_id),
            )
            cur.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
            job_row = cur.fetchone()
            conn.commit()
            return dict(job_row)
        finally:
            conn.close()

    def update_job(self, job_id: int, **fields) -> None:
        allowed = {
            "status",
            "progress",
            "heartbeat_at",
            "output_path",
            "error",
            "image_backend",
            "comfyui_checkpoint",
            "fallback_backend",
        }
        set_clauses = []
        params: list[Any] = []
        for k, v in fields.items():
            if k in allowed:
                set_clauses.append(f"{k}=?")
                params.append(v)
        if not set_clauses:
            return
        params.extend([_now_iso(), job_id])
        sql = f"UPDATE jobs SET {', '.join(set_clauses)}, updated_at=? WHERE id=?"
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        conn.close()

    def append_event(self, job_id: int, message: str, event_type: str | None = "log") -> None:
        conn = self._connect()
        cur = conn.cursor()
        now = _now_iso()
        cur.execute(
            "INSERT INTO job_events (job_id, ts, event_type, message) VALUES (?,?,?,?)",
            (job_id, now, event_type, message),
        )
        conn.commit()
        conn.close()

    def list_jobs(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        cur = conn.cursor()
        row = cur.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_events(self, job_id: int, limit: int | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        q = "SELECT * FROM job_events WHERE job_id=? ORDER BY id ASC"
        params = [job_id]
        if limit:
            q += " LIMIT ?"
            params.append(limit)
        rows = cur.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def request_cancel(self, job_id: int) -> bool:
        conn = self._connect()
        cur = conn.cursor()
        row = cur.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            conn.close()
            return False
        status = row[0]
        if status in (STATUS_QUEUED, STATUS_RUNNING, STATUS_PAUSED):
            cur.execute(
                "UPDATE jobs SET status=? , updated_at=? WHERE id=?",
                (STATUS_CANCEL_REQUESTED, _now_iso(), job_id),
            )
            cur.execute(
                "INSERT INTO job_events (job_id, ts, event_type, message) VALUES (?,?,?,?)",
                (job_id, _now_iso(), "system", "cancel_requested"),
            )
            conn.commit()
            conn.close()
            return True
        conn.close()
        return False

    def mark_stale_running_failed(self, stale_seconds: int = 120) -> int:
        """Mark running jobs with old heartbeat as failed. Returns count."""
        cutoff = datetime.now(timezone.utc).timestamp() - stale_seconds
        conn = self._connect()
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT id, heartbeat_at FROM jobs WHERE status=?", (STATUS_RUNNING,)
        ).fetchall()
        to_fail: list[int] = []
        for r in rows:
            hb = r[1]
            try:
                if hb is None:
                    to_fail.append(r[0])
                else:
                    ts = _parse_iso(hb).timestamp()
                    if ts < cutoff:
                        to_fail.append(r[0])
            except Exception:
                to_fail.append(r[0])
        for jid in to_fail:
            cur.execute(
                "UPDATE jobs SET status=?, updated_at=? WHERE id=?",
                (STATUS_FAILED, _now_iso(), jid),
            )
            cur.execute(
                "INSERT INTO job_events (job_id, ts, event_type, message) VALUES (?,?,?,?)",
                (jid, _now_iso(), "system", "marked_stale_failed"),
            )
        conn.commit()
        conn.close()
        return len(to_fail)

    def retry_job(self, job_id: int) -> int | None:
        """Create a new queued job from a failed or canceled job. Returns new job id."""
        orig = self.get_job(job_id)
        if not orig:
            return None
        if orig["status"] not in (STATUS_FAILED, STATUS_CANCELED):
            return None
        request = json.loads(orig["request_json"])
        new_id = self.create_job(
            request,
            topic=orig.get("topic"),
            image_backend=orig.get("image_backend"),
            comfyui_checkpoint=orig.get("comfyui_checkpoint"),
            fallback_backend=orig.get("fallback_backend"),
        )
        self.append_event(job_id, f"retry_created:{new_id}", event_type="system")
        return new_id

    def add_artifact(self, job_id: int, key: str, path: str, meta: str | None = None) -> None:
        """Record a job artifact (output file, manifest, etc.)."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO job_artifacts (job_id, key, path, meta) VALUES (?,?,?,?)",
            (job_id, key, path, meta),
        )
        conn.commit()
        conn.close()

    def get_artifacts(self, job_id: int) -> list[dict]:
        """Return all artifacts for a given job_id as a list of dicts."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, key, path, meta FROM job_artifacts WHERE job_id = ? ORDER BY id",
            (job_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return [
            {"id": r["id"], "key": r["key"], "path": r["path"], "meta": r["meta"]} for r in rows
        ]
