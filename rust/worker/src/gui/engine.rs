//! Phase-1 engine access for the native GUI: resolve the repo/interpreter,
//! read/write the job queue DB, supervise the worker subprocess, and probe
//! ComfyUI/Ollama health.
//!
//! This module deliberately contains NO egui code so it can be unit-tested
//! without opening a window. It reuses the exact SQLite schema and status
//! vocabulary of `jobs/job_store.py` (do not mutate the schema — AGENTS).

use std::net::{TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

use anyhow::{Context, Result};
use chrono::Utc;
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};

const STATUS_QUEUED: &str = "queued";
const STATUS_CANCELED: &str = "canceled";
const STATUS_FAILED: &str = "failed";
const STATUS_RUNNING: &str = "running";
const STATUS_PAUSED: &str = "paused";
const STATUS_CANCEL_REQUESTED: &str = "cancel_requested";

/// Resolve the repository root: `VIDEOAI_ROOT` env wins, then the current
/// directory (a native double-click lands here because the launcher sets CWD).
pub fn resolve_root() -> PathBuf {
    if let Ok(root) = std::env::var("VIDEOAI_ROOT") {
        if !root.trim().is_empty() {
            return PathBuf::from(root);
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

pub fn resolve_python(root: &std::path::Path) -> PathBuf {
    if let Ok(path) = std::env::var("VIDEOAI_PYTHON") {
        return PathBuf::from(path);
    }
    if cfg!(windows) {
        root.join("venv").join("Scripts").join("python.exe")
    } else {
        root.join("venv").join("bin").join("python")
    }
}

fn db_path(root: &std::path::Path) -> PathBuf {
    root.join("studio_projects")
        .join("jobs")
        .join("video_ai_jobs.db")
}

fn now_iso() -> String {
    Utc::now().format("%Y-%m-%dT%H:%M:%S%.6f+00:00").to_string()
}

fn connect(root: &std::path::Path) -> Result<Connection> {
    let path = db_path(root);
    if !path.is_file() {
        return Err(anyhow::anyhow!(
            "job database not found: {} — run the app once from the repo root",
            path.display()
        ));
    }
    let conn = Connection::open_with_flags(
        &path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| format!("failed to open job database {}", path.display()))?;
    conn.busy_timeout(Duration::from_millis(5_000))
        .context("failed to set busy timeout")?;
    Ok(conn)
}

#[derive(Clone, Debug)]
pub struct JobRec {
    pub id: i64,
    pub status: String,
    pub topic: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub progress: Option<i64>,
    pub output_path: Option<String>,
    pub error: Option<String>,
}

#[derive(Clone, Debug)]
pub struct EventRec {
    pub ts: String,
    pub event_type: Option<String>,
    pub message: Option<String>,
}

#[derive(Clone, Debug, Default)]
pub struct DashStats {
    pub total: usize,
    pub queued: usize,
    pub running: usize,
    pub succeeded: usize,
    pub failed: usize,
    pub canceled: usize,
}

/// List the most recent jobs, newest first (mirror of `JobStore.list_jobs`).
pub fn list_jobs(root: &std::path::Path, limit: i64) -> Result<Vec<JobRec>> {
    let conn = connect(root)?;
    let mut stmt = conn
        .prepare(
            "SELECT id, status, topic, created_at, updated_at, progress, output_path, error \
             FROM jobs ORDER BY created_at DESC LIMIT ?1",
        )
        .context("failed to prepare list-jobs query")?;
    let rows = stmt
        .query_map([limit], |row| {
            Ok(JobRec {
                id: row.get("id")?,
                status: row.get("status")?,
                topic: row.get("topic")?,
                created_at: row.get("created_at")?,
                updated_at: row.get("updated_at")?,
                progress: row.get("progress")?,
                output_path: row.get("output_path")?,
                error: row.get("error")?,
            })
        })
        .context("failed to query jobs")?;
    let mut jobs = Vec::new();
    for row in rows {
        jobs.push(row.context("failed to read job row")?);
    }
    Ok(jobs)
}

pub fn get_job(root: &std::path::Path, job_id: i64) -> Result<Option<JobRec>> {
    let conn = connect(root)?;
    let mut stmt = conn
        .prepare(
            "SELECT id, status, topic, created_at, updated_at, progress, output_path, error \
             FROM jobs WHERE id=?1",
        )
        .context("failed to prepare job query")?;
    let job = stmt
        .query_row([job_id], |row| {
            Ok(JobRec {
                id: row.get("id")?,
                status: row.get("status")?,
                topic: row.get("topic")?,
                created_at: row.get("created_at")?,
                updated_at: row.get("updated_at")?,
                progress: row.get("progress")?,
                output_path: row.get("output_path")?,
                error: row.get("error")?,
            })
        })
        .optional()
        .context("failed to query job")?;
    Ok(job)
}

pub fn get_events(root: &std::path::Path, job_id: i64, limit: i64) -> Result<Vec<EventRec>> {
    let conn = connect(root)?;
    let mut stmt = conn
        .prepare(
            "SELECT ts, event_type, message FROM job_events \
             WHERE job_id=?1 ORDER BY id ASC LIMIT ?2",
        )
        .context("failed to prepare events query")?;
    let rows = stmt
        .query_map(params![job_id, limit], |row| {
            Ok(EventRec {
                ts: row.get("ts")?,
                event_type: row.get("event_type")?,
                message: row.get("message")?,
            })
        })
        .context("failed to query events")?;
    let mut events = Vec::new();
    for row in rows {
        events.push(row.context("failed to read event row")?);
    }
    Ok(events)
}

pub fn dash_stats(root: &std::path::Path) -> Result<DashStats> {
    let conn = connect(root)?;
    let mut stats = DashStats::default();
    for rec in list_jobs(root, 5000)? {
        stats.total += 1;
        match rec.status.as_str() {
            STATUS_QUEUED => stats.queued += 1,
            STATUS_RUNNING | STATUS_PAUSED => stats.running += 1,
            "succeeded" => stats.succeeded += 1,
            STATUS_FAILED => stats.failed += 1,
            STATUS_CANCELED => stats.canceled += 1,
            _ => {}
        }
    }
    let _ = conn;
    Ok(stats)
}

/// Either an invalid field name (typo -> bug #15) or a value that is not JSON.
#[derive(Debug)]
pub enum JobValidationError {
    UnknownField(String),
    Value(String),
}

/// Build a `request_json` value from an ordered list of (key, value) pairs.
/// Only canonical worker flags are accepted (shared `request::is_supported_arg`),
/// so flag typos fail here, at submit time, not minutes into a run.
pub fn build_request_json(
    fields: &[(&str, serde_json::Value)],
) -> Result<serde_json::Value, JobValidationError> {
    let mut map = serde_json::Map::new();
    for (key, value) in fields {
        if !crate::request::is_supported_arg(key) {
            return Err(JobValidationError::UnknownField((*key).to_string()));
        }
        if value.is_null() || (value.is_string() && value.as_str() == Some("")) {
            continue;
        }
        map.insert((*key).to_string(), value.clone());
    }
    Ok(serde_json::Value::Object(map))
}

/// Insert a queued job. Returns the new job id. Mirrors `JobStore.create_job`.
pub fn create_job(
    root: &std::path::Path,
    request_json: &serde_json::Value,
    topic: Option<&str>,
    image_backend: Option<&str>,
    comfyui_checkpoint: Option<&str>,
) -> Result<i64> {
    let conn = connect(root)?;
    let now = now_iso();
    let payload = request_json.to_string();
    conn.execute(
        "INSERT INTO jobs (status, topic, request_json, created_at, updated_at, attempt, image_backend, comfyui_checkpoint) \
         VALUES (?1,?2,?3,?4,?4,0,?5,?6)",
        params![STATUS_QUEUED, topic, payload, now, image_backend, comfyui_checkpoint],
    )
    .context("failed to insert job")?;
    Ok(conn.last_insert_rowid())
}

/// Request cancellation, mirroring `JobStore.request_cancel` (only queued /
/// running / paused jobs may be canceled; worker clamps them cleanly).
pub fn request_cancel(root: &std::path::Path, job_id: i64) -> Result<bool> {
    let conn = connect(root)?;
    let now = now_iso();
    let changed = conn
        .execute(
            "UPDATE jobs SET status=?1, updated_at=?2 WHERE id=?3 AND status IN (?4, ?5, ?6)",
            params![
                STATUS_CANCEL_REQUESTED,
                now,
                job_id,
                STATUS_QUEUED,
                STATUS_RUNNING,
                STATUS_PAUSED
            ],
        )
        .context("failed to request cancel")?;
    if changed > 0 {
        conn.execute(
            "INSERT INTO job_events (job_id, ts, event_type, message) VALUES (?1,?2,'system','cancel_requested')",
            params![job_id, now],
        )?;
    }
    Ok(changed > 0)
}

/// Retry a failed/canceled job from its original payload (mirror `retry_job`).
pub fn retry_job(root: &std::path::Path, job_id: i64) -> Result<Option<i64>> {
    let job = get_job(root, job_id)?;
    let Some(job) = job else { return Ok(None) };
    if job.status != STATUS_FAILED && job.status != STATUS_CANCELED {
        return Ok(None);
    }
    let conn = connect(root)?;
    let (payload, image_backend, comfyui_checkpoint, fallback_backend): (
        String,
        Option<String>,
        Option<String>,
        Option<String>,
    ) = conn
        .query_row(
            "SELECT request_json, image_backend, comfyui_checkpoint, fallback_backend FROM jobs WHERE id=?1",
            [job_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .context("failed to read retry source job")?;
    let now = now_iso();
    conn.execute(
        "INSERT INTO jobs (status, topic, request_json, created_at, updated_at, attempt, image_backend, comfyui_checkpoint, fallback_backend) \
         VALUES (?1,?2,?3,?4,?4,0,?5,?6,?7)",
        params![
            STATUS_QUEUED,
            job.topic,
            payload,
            now,
            image_backend,
            comfyui_checkpoint,
            fallback_backend
        ],
    )
    .context("failed to insert retry job")?;
    let new_id = conn.last_insert_rowid();
    conn.execute(
        "INSERT INTO job_events (job_id, ts, event_type, message) VALUES (?1,?2,'system',?3)",
        params![job_id, now, format!("retry_created:{new_id}")],
    )?;
    Ok(Some(new_id))
}

/// TCP reachability probe used by the status rail (generic, lightweight).
pub fn host_reachable(host: &str, port: u16, timeout_ms: u64) -> bool {
    let addr = format!("{host}:{port}");
    let Ok(mut addrs) = addr.to_socket_addrs() else {
        return false;
    };
    let Some(addr) = addrs.next() else {
        return false;
    };
    TcpStream::connect_timeout(&addr, Duration::from_millis(timeout_ms)).is_ok()
}

/// A supervised worker subprocess (the Python `jobs/run_worker.py` loop).
/// The GUI spawns exactly ONE — the app owns the queue, fixing bugs #3/#7/#21.
pub struct SupervisedWorker {
    root: PathBuf,
    child: Option<Child>,
}

impl SupervisedWorker {
    pub fn new(root: &std::path::Path) -> Self {
        Self {
            root: root.to_path_buf(),
            child: None,
        }
    }

    /// Spawn the worker if it is not already running.
    pub fn ensure_running(&mut self) -> Result<bool> {
        if self.alive() {
            return Ok(true);
        }
        let python = resolve_python(&self.root);
        let script = self.root.join("jobs").join("run_worker.py");
        // RECALL: run_worker.py inserts repo root into sys.path at runtime, so
        // CWD does not matter; set CWD to repo root anyway to satisfy the venv
        // guard's "this must be a Video.AI checkout" check.
        let child = Command::new(&python)
            .arg(&script)
            .current_dir(&self.root)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .with_context(|| {
                format!(
                    "failed to spawn worker: {} {}",
                    python.display(),
                    script.display()
                )
            })?;
        self.child = Some(child);
        Ok(self.alive())
    }

    pub fn is_alive(&mut self) -> bool {
        if let Some(child) = self.child.as_mut() {
            match child.try_wait() {
                Ok(Some(_)) => {
                    self.child = None;
                    false
                }
                Ok(None) => true,
                Err(_) => {
                    self.child = None;
                    false
                }
            }
        } else {
            false
        }
    }

    pub fn stop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    /// Reap a dead child so `is_alive()` reports current state.
    fn alive(&mut self) -> bool {
        self.is_alive()
    }
}

/// Read ComfyUI host/port from `config/config.yaml`, falling back to the
/// historical default `127.0.0.1:8188`. Mirrors `main.rs::read_comfyui_host_port`.
pub fn comfyui_addr(root: &std::path::Path) -> (String, u16) {
    let cfg = root.join("config").join("config.yaml");
    let Ok(text) = std::fs::read_to_string(cfg) else {
        return ("127.0.0.1".to_string(), 8188);
    };
    let mut host = "127.0.0.1".to_string();
    let mut port = 8188_u16;
    let mut in_comfyui = false;
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("image_gen:") {
            in_comfyui = false;
            continue;
        }
        if in_comfyui && trimmed.starts_with("comfyui:") {
            in_comfyui = true;
            continue;
        }
        if trimmed.starts_with("comfyui:") {
            in_comfyui = true;
            continue;
        }
        if in_comfyui && trimmed.starts_with("host:") {
            host = trimmed
                .trim_start_matches("host:")
                .trim()
                .trim_matches(['\'', '"'])
                .to_string();
        }
        if in_comfyui && trimmed.starts_with("port:") {
            if let Ok(parsed) = trimmed.trim_start_matches("port:").trim().parse::<u16>() {
                port = parsed;
            }
        }
    }
    (host, port)
}

/// Open a file/folder in the OS file manager (Explorer on Windows).
pub fn open_in_explorer(path: &std::path::Path) {
    let target = path.to_string_lossy().to_string();
    #[cfg(windows)]
    {
        let _ = Command::new("explorer")
            .arg(format!("/select,{target}"))
            .spawn();
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("xdg-open").arg(&target).spawn();
    }
}

/// Open a media file in the OS default player.
pub fn open_in_player(path: &std::path::Path) {
    let target = path.to_string_lossy().to_string();
    #[cfg(windows)]
    {
        let _ = Command::new("cmd")
            .arg("/C")
            .arg("start")
            .arg("")
            .arg(&target)
            .spawn();
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("xdg-open").arg(&target).spawn();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root_with_db() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let db_dir = dir.path().join("studio_projects").join("jobs");
        std::fs::create_dir_all(&db_dir).unwrap();
        let conn = Connection::open(db_dir.join("video_ai_jobs.db")).unwrap();
        conn.execute_batch(
            r#"
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
            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                event_type TEXT,
                message TEXT
            );
            "#,
        )
        .unwrap();
        let _ = conn.close();
        dir
    }

    #[test]
    fn build_request_json_rejects_unknown_flag_and_empty_values() {
        let err = build_request_json(&[
            ("segmetn_count", serde_json::json!(2)), // typo #15
        ])
        .unwrap_err();
        assert!(matches!(err, JobValidationError::UnknownField(_)));

        let ok = build_request_json(&[
            ("segment_count", serde_json::json!(2)),
            ("topic", serde_json::Value::String(String::new())),
        ])
        .unwrap();
        assert_eq!(ok["segment_count"], 2);
        assert!(ok.get("topic").is_none());
    }

    #[test]
    fn create_list_cancel_retry_roundtrip() {
        let dir = temp_root_with_db();
        let root = dir.path();
        let json = first_request_json();
        let id = create_job(root, &json, Some("Round Trip"), None, None).unwrap();
        assert!(id >= 1);

        let jobs = list_jobs(root, 10).unwrap();
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].status, STATUS_QUEUED);
        assert_eq!(jobs[0].topic.as_deref(), Some("Round Trip"));

        assert!(request_cancel(root, id).unwrap());
        // Worker's cancel sweep converts queued cancel_requested -> canceled
        // (mirror of `Worker::cancel_sweep`). Only then is retry allowed.
        let conn = connect(root).unwrap();
        conn.execute(
            "UPDATE jobs SET status=? WHERE id=?",
            params![STATUS_CANCELED, id],
        )
        .unwrap();
        drop(conn);
        let job = get_job(root, id).unwrap().unwrap();
        assert_eq!(job.status, STATUS_CANCELED);

        // Canceled jobs can be retried; the new job is queued.
        let new_id = retry_job(root, id).unwrap().unwrap();
        assert_ne!(new_id, id);
        let new_job = get_job(root, new_id).unwrap().unwrap();
        assert_eq!(new_job.status, STATUS_QUEUED);
        assert_eq!(new_job.topic.as_deref(), Some("Round Trip"));
    }

    #[test]
    fn retry_preserves_fallback_backend() {
        // Regression: the insert used to omit fallback_backend, silently
        // dropping the degradation chain on every retry (the Python reference
        // `JobStore.retry_job` carries it over). The roundtrip test above did
        // not set the column, so it could not catch this.
        let dir = temp_root_with_db();
        let root = dir.path();
        let id = create_job(
            root,
            &first_request_json(),
            Some("Degrade"),
            Some("comfyui"),
            Some("sdxl.safetensors"),
        )
        .unwrap();
        let conn = connect(root).unwrap();
        conn.execute(
            "UPDATE jobs SET status=?, fallback_backend=? WHERE id=?",
            params![STATUS_FAILED, "diffusers", id],
        )
        .unwrap();
        drop(conn);

        let new_id = retry_job(root, id).unwrap().unwrap();
        let conn = connect(root).unwrap();
        let carried: Option<String> = conn
            .query_row(
                "SELECT fallback_backend FROM jobs WHERE id=?1",
                [new_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(
            carried.as_deref(),
            Some("diffusers"),
            "retry dropped fallback_backend"
        );
    }

    #[test]
    fn cancel_fails_on_terminal_job() {
        let dir = temp_root_with_db();
        let root = dir.path();
        let id = create_job(root, &first_request_json(), Some("t"), None, None).unwrap();
        let conn = connect(root).unwrap();
        conn.execute(
            "UPDATE jobs SET status=? WHERE id=?",
            params![STATUS_FAILED, id],
        )
        .unwrap();
        drop(conn);
        assert!(!request_cancel(root, id).unwrap());
    }

    fn first_request_json() -> serde_json::Value {
        build_request_json(&[
            ("topic", serde_json::json!("t")),
            ("segment_count", serde_json::json!(1)),
        ])
        .unwrap()
    }
}
