use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::ToSocketAddrs;
use std::path::{Path, PathBuf};
use std::process::{Command as StdCommand, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use chrono::Utc;
use clap::{Parser, Subcommand};
use rusqlite::{params, Connection, OpenFlags, TransactionBehavior};
use serde::Serialize;
use serde_json::{Map, Value};

const DEFAULT_DB_PATH: &str = "studio_projects/jobs/video_ai_jobs.db";
const HEARTBEAT_INTERVAL_SECONDS: u64 = 10;
const CANCEL_WAIT_SECONDS: u64 = 30;
const POLL_INTERVAL_SECONDS: u64 = 5;

const STATUS_QUEUED: &str = "queued";
const STATUS_RUNNING: &str = "running";
const STATUS_CANCEL_REQUESTED: &str = "cancel_requested";
const STATUS_CANCELED: &str = "canceled";
const STATUS_SUCCEEDED: &str = "succeeded";
const STATUS_FAILED: &str = "failed";

#[derive(Debug, Parser)]
#[command(name = "videoai-worker")]
#[command(about = "Video.AI Rust sidecar worker")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Subcommand)]
enum Commands {
    /// List jobs from the Video.AI SQLite queue.
    ListJobs {
        /// Path to job database.
        #[arg(long, default_value = DEFAULT_DB_PATH)]
        db_path: PathBuf,

        /// Maximum number of jobs to print.
        #[arg(long, default_value_t = 100)]
        limit: u32,

        /// Number of jobs to skip.
        #[arg(long, default_value_t = 0)]
        offset: u32,
    },

    /// Run the Rust job supervisor.
    Run {
        /// Claim and run at most one job, then exit.
        #[arg(long)]
        once: bool,

        /// Path to job database.
        #[arg(long, default_value = DEFAULT_DB_PATH)]
        db_path: PathBuf,
    },
}

#[derive(Debug, Serialize)]
struct JobSummary {
    id: i64,
    status: String,
    topic: Option<String>,
    created_at: String,
}

#[derive(Clone, Debug)]
struct WorkerConfig {
    repo_root: PathBuf,
    db_path: PathBuf,
    python: PathBuf,
    bootstrap: PathBuf,
}

impl WorkerConfig {
    fn from_cli(db_path: PathBuf) -> Result<Self> {
        let repo_root = std::env::current_dir().context("failed to resolve repository root")?;
        let python = resolve_python(&repo_root);
        let bootstrap = repo_root.join("bootstrap_pipeline.py");
        Ok(Self {
            repo_root,
            db_path,
            python,
            bootstrap,
        })
    }
}

#[derive(Clone, Debug)]
struct JobRow {
    id: i64,
    topic: Option<String>,
    request_json: String,
    image_backend: Option<String>,
    comfyui_checkpoint: Option<String>,
}

#[derive(Debug)]
struct BuiltCommand {
    cmd: Vec<String>,
    temp_content_file: Option<PathBuf>,
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::ListJobs {
            db_path,
            limit,
            offset,
        } => {
            let conn = open_job_db_read_only(&db_path)?;
            let jobs = list_jobs(&conn, limit, offset)?;
            print_jobs(&jobs)?;
        }
        Commands::Run { once, db_path } => {
            let worker = Worker::new(WorkerConfig::from_cli(db_path)?);
            if once {
                let _ = worker.run_once()?;
            } else {
                worker.run_forever()?;
            }
        }
    }

    Ok(())
}

fn resolve_python(repo_root: &Path) -> PathBuf {
    if let Ok(path) = std::env::var("VIDEOAI_PYTHON") {
        return PathBuf::from(path);
    }
    if cfg!(windows) {
        repo_root.join("venv").join("Scripts").join("python.exe")
    } else {
        repo_root.join("venv").join("bin").join("python")
    }
}

fn now_iso() -> String {
    Utc::now().format("%Y-%m-%dT%H:%M:%S%.6f+00:00").to_string()
}

fn open_job_db_read_only(db_path: &Path) -> Result<Connection> {
    if !db_path.is_file() {
        bail!(
            "job database not found: {} — start the Python app once to create it",
            db_path.display()
        );
    }

    let conn = Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| format!("job database not found or unreadable: {}", db_path.display()))?;

    conn.busy_timeout(Duration::from_millis(5_000))
        .context("failed to set SQLite busy timeout")?;

    Ok(conn)
}

fn open_job_db_read_write(db_path: &Path) -> Result<Connection> {
    if !db_path.is_file() {
        bail!(
            "job database not found: {} — start the Python app once to create it",
            db_path.display()
        );
    }

    let conn = Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| format!("job database not found or unreadable: {}", db_path.display()))?;

    conn.busy_timeout(Duration::from_millis(5_000))
        .context("failed to set SQLite busy timeout")?;

    Ok(conn)
}

fn list_jobs(conn: &Connection, limit: u32, offset: u32) -> Result<Vec<JobSummary>> {
    let limit = i64::from(limit);
    let offset = i64::from(offset);
    let mut stmt = conn
        .prepare(
            "SELECT id, status, topic, created_at \
             FROM jobs \
             ORDER BY created_at DESC \
             LIMIT ?1 OFFSET ?2",
        )
        .context("failed to prepare list-jobs query")?;

    let rows = stmt
        .query_map((limit, offset), |row| {
            Ok(JobSummary {
                id: row.get("id")?,
                status: row.get("status")?,
                topic: row.get("topic")?,
                created_at: row.get("created_at")?,
            })
        })
        .context("failed to query jobs")?;

    let mut jobs = Vec::new();
    for row in rows {
        jobs.push(row.context("failed to read job row")?);
    }

    Ok(jobs)
}

fn print_jobs(jobs: &[JobSummary]) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(jobs)?);
    Ok(())
}

struct Worker {
    config: WorkerConfig,
}

impl Worker {
    fn new(config: WorkerConfig) -> Self {
        Self { config }
    }

    fn run_forever(&self) -> Result<()> {
        loop {
            match self.run_once() {
                Ok(Some(_)) => thread::sleep(Duration::from_secs(1)),
                Ok(None) => thread::sleep(Duration::from_secs(POLL_INTERVAL_SECONDS)),
                Err(err) => {
                    let _ = append_event_path(
                        &self.config.db_path,
                        0,
                        &format!("worker_error: {err}"),
                        "system",
                    );
                    thread::sleep(Duration::from_secs(POLL_INTERVAL_SECONDS));
                }
            }
        }
    }

    fn run_once(&self) -> Result<Option<i64>> {
        self.cancel_sweep()?;

        let Some(job) = self.claim_next_job()? else {
            return Ok(None);
        };
        let job_id = job.id;

        if let Err(err) = self.preflight_comfyui(&job) {
            append_event_path(
                &self.config.db_path,
                job_id,
                &format!("preflight_failed: {err}"),
                "system",
            )?;
            update_job_path(
                &self.config.db_path,
                job_id,
                &[JobUpdate::Status(STATUS_FAILED), JobUpdate::Error(&err.to_string())],
            )?;
            return Ok(Some(job_id));
        }

        let built = match self.build_command(&job) {
            Ok(cmd) => cmd,
            Err(err) => {
                append_event_path(
                    &self.config.db_path,
                    job_id,
                    &format!("invalid_request: {err}"),
                    "system",
                )?;
                update_job_path(
                    &self.config.db_path,
                    job_id,
                    &[JobUpdate::Status(STATUS_FAILED), JobUpdate::Error(&err.to_string())],
                )?;
                return Ok(Some(job_id));
            }
        };

        append_event_path(
            &self.config.db_path,
            job_id,
            &format!("starting: {}", built.cmd.join(" ")),
            "system",
        )?;

        let mut command = StdCommand::new(&built.cmd[0]);
        command
            .args(&built.cmd[1..])
            .current_dir(&self.config.repo_root)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }

        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0000_0200);
        }

        let mut child = command.spawn().context("failed to spawn worker subprocess")?;
        let child_id = child.id();

        let stop = Arc::new(AtomicBool::new(false));
        let heartbeat = spawn_heartbeat_thread(self.config.db_path.clone(), job_id, Arc::clone(&stop));

        let mut stream_threads = Vec::new();
        if let Some(stdout) = child.stdout.take() {
            stream_threads.push(spawn_stream_thread(stdout, self.config.db_path.clone(), job_id));
        }
        if let Some(stderr) = child.stderr.take() {
            stream_threads.push(spawn_stream_thread(stderr, self.config.db_path.clone(), job_id));
        }

        let mut canceled = false;
        while child.try_wait().context("failed to poll worker subprocess")?.is_none() {
            if let Some(status) = get_job_status_path(&self.config.db_path, job_id)? {
                if status == STATUS_CANCEL_REQUESTED {
                    canceled = true;
                    append_event_path(&self.config.db_path, job_id, "cancellation_requested", "system")?;
                    send_interrupt(child_id);
                    let deadline = Instant::now() + Duration::from_secs(CANCEL_WAIT_SECONDS);
                    while child.try_wait().context("failed to poll worker subprocess")?.is_none()
                        && Instant::now() < deadline
                    {
                        thread::sleep(Duration::from_secs(1));
                    }
                    if child.try_wait().context("failed to poll worker subprocess")?.is_none() {
                        let _ = child.kill();
                    }
                    let _ = child.wait();
                    update_job_path(&self.config.db_path, job_id, &[JobUpdate::Status(STATUS_CANCELED)])?;
                    break;
                }
            }
            thread::sleep(Duration::from_secs(1));
        }

        stop.store(true, Ordering::SeqCst);
        join_thread_with_timeout(heartbeat, Duration::from_secs(2));
        for handle in stream_threads {
            join_thread_with_timeout(handle, Duration::from_secs(2));
        }

        if !canceled {
            let status = child.wait().context("failed to wait for worker subprocess")?;
            let rc = status.code().unwrap_or(-1);
            if get_job_status_path(&self.config.db_path, job_id)?.as_deref() != Some(STATUS_CANCELED) {
                if rc == 0 {
                    update_job_path(
                        &self.config.db_path,
                        job_id,
                        &[JobUpdate::Status(STATUS_SUCCEEDED), JobUpdate::Progress(100)],
                    )?;
                    append_event_path(
                        &self.config.db_path,
                        job_id,
                        &format!("process_exited: {rc}"),
                        "system",
                    )?;
                    if let Err(err) = self.capture_artifacts(job_id) {
                        append_event_path(
                            &self.config.db_path,
                            job_id,
                            &format!("artifact_capture_failed: {err}"),
                            "system",
                        )?;
                    }
                } else {
                    update_job_path(
                        &self.config.db_path,
                        job_id,
                        &[JobUpdate::Status(STATUS_FAILED), JobUpdate::Error(&format!("exit_code:{rc}"))],
                    )?;
                    append_event_path(
                        &self.config.db_path,
                        job_id,
                        &format!("process_failed: {rc}"),
                        "system",
                    )?;
                }
            }
        }

        if let Some(temp_file) = built.temp_content_file {
            if let Err(err) = fs::remove_file(&temp_file) {
                append_event_path(
                    &self.config.db_path,
                    job_id,
                    &format!("cleanup_warning: {err}"),
                    "system",
                )?;
            }
        }

        Ok(Some(job_id))
    }

    fn cancel_sweep(&self) -> Result<()> {
        let conn = open_job_db_read_write(&self.config.db_path)?;
        let mut stmt = conn
            .prepare("SELECT id FROM jobs WHERE status=?")
            .context("failed to prepare cancel sweep query")?;
        let rows = stmt
            .query_map([STATUS_CANCEL_REQUESTED], |row| row.get::<_, i64>(0))
            .context("failed to query cancel_requested jobs")?;
        let mut ids = Vec::new();
        for row in rows {
            ids.push(row.context("failed to read cancel_requested row")?);
        }
        drop(stmt);
        drop(conn);

        for job_id in ids {
            update_job_path(&self.config.db_path, job_id, &[JobUpdate::Status(STATUS_CANCELED)])?;
            append_event_path(&self.config.db_path, job_id, "canceled_from_queued", "system")?;
        }

        Ok(())
    }

    fn claim_next_job(&self) -> Result<Option<JobRow>> {
        let mut conn = open_job_db_read_write(&self.config.db_path)?;
        let tx = conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .context("failed to begin immediate claim transaction")?;

        let job_id = tx
            .query_row(
                "SELECT id FROM jobs WHERE status=? ORDER BY created_at LIMIT 1",
                [STATUS_QUEUED],
                |row| row.get::<_, i64>(0),
            )
            .optional()?;

        let Some(job_id) = job_id else {
            tx.commit().context("failed to commit empty claim transaction")?;
            return Ok(None);
        };

        let now = now_iso();
        tx.execute(
            "UPDATE jobs SET status=?, heartbeat_at=?, updated_at=?, attempt = attempt + 1 WHERE id=?",
            params![STATUS_RUNNING, now, now, job_id],
        )
        .context("failed to mark job running")?;

        let job = tx
            .query_row("SELECT * FROM jobs WHERE id=?", [job_id], row_to_job)
            .context("failed to reload claimed job")?;
        tx.commit().context("failed to commit claim transaction")?;
        Ok(Some(job))
    }

    fn preflight_comfyui(&self, job: &JobRow) -> Result<()> {
        if job.image_backend.as_deref() != Some("comfyui") {
            return Ok(());
        }

        if let Some(checkpoint) = job.comfyui_checkpoint.as_deref() {
            let cp_path = if checkpoint.contains('/') || checkpoint.contains('\\') {
                PathBuf::from(checkpoint)
            } else {
                self.config
                    .repo_root
                    .join("external")
                    .join("ComfyUI")
                    .join("models")
                    .join("checkpoints")
                    .join(checkpoint)
            };
            if !cp_path.exists() {
                if checkpoint.contains('/') || checkpoint.contains('\\') {
                    bail!("ComfyUI checkpoint not found: {}", cp_path.display());
                }
                bail!(
                    "ComfyUI checkpoint not found: {} (resolved from model name: {})",
                    cp_path.display(),
                    checkpoint
                );
            }
        }

        let (host, port) = read_comfyui_host_port(&self.config.repo_root);
        http_get_root(&host, port).with_context(|| {
            format!("ComfyUI preflight failed: could not GET {{http://{host}}}:{port}/")
        })?;
        Ok(())
    }

    fn build_command(&self, job: &JobRow) -> Result<BuiltCommand> {
        let req_value: Value = serde_json::from_str(&job.request_json)
            .with_context(|| format!("Invalid request_json JSON for job {}", job.id))?;
        let mut req = match req_value {
            Value::Null => Map::new(),
            Value::Object(map) => map,
            _ => bail!("Invalid request_json JSON: expected object"),
        };

        let mut cmd = vec![
            self.config.python.to_string_lossy().to_string(),
            self.config.bootstrap.to_string_lossy().to_string(),
        ];

        let topic = req
            .get("topic")
            .and_then(value_as_nonempty_string)
            .or_else(|| job.topic.clone());
        if let Some(topic) = topic {
            cmd.push("--topic".to_string());
            cmd.push(topic);
        }

        let mut temp_content_file = None;
        if let Some(content_text) = req.remove("content_text").and_then(|v| value_as_nonempty_string(&v)) {
            let temp_file = self.config.repo_root.join("jobs").join(format!("_{}_content.txt", job.id));
            fs::write(&temp_file, content_text)
                .with_context(|| format!("failed to write temp content file {}", temp_file.display()))?;
            cmd.push("--file".to_string());
            cmd.push(temp_file.to_string_lossy().to_string());
            temp_content_file = Some(temp_file);
        }

        for (key, value) in req.iter() {
            if key == "topic" || !is_supported_arg(key) {
                continue;
            }
            let flag = format!("--{}", key.replace('_', "-"));
            match value {
                Value::Bool(true) => cmd.push(flag),
                Value::Bool(false) | Value::Null => {}
                Value::String(s) => {
                    cmd.push(flag);
                    cmd.push(s.clone());
                }
                Value::Number(n) => {
                    cmd.push(flag);
                    cmd.push(n.to_string());
                }
                other => {
                    cmd.push(flag);
                    cmd.push(other.to_string());
                }
            }
        }

        Ok(BuiltCommand { cmd, temp_content_file })
    }

    fn capture_artifacts(&self, job_id: i64) -> Result<()> {
        let job = get_job_path(&self.config.db_path, job_id)?.context("job disappeared before artifact capture")?;
        let topic_raw = job.topic.unwrap_or_else(|| "unknown".to_string());
        let topic_slug = safe_filename(&topic_raw);
        let output_root = self.config.repo_root.join("studio_outputs").join(topic_slug);
        if !output_root.exists() {
            return Ok(());
        }

        let mut latest_video: Option<(PathBuf, std::time::SystemTime)> = None;
        for entry in fs::read_dir(&output_root)
            .with_context(|| format!("failed to read output directory {}", output_root.display()))?
        {
            let entry = entry.context("failed to read output directory entry")?;
            let path = entry.path();
            if path.extension().and_then(|ext| ext.to_str()) != Some("mp4") {
                continue;
            }
            let modified = entry
                .metadata()
                .and_then(|m| m.modified())
                .with_context(|| format!("failed to stat output video {}", path.display()))?;
            if latest_video
                .as_ref()
                .map(|(_, current)| modified > *current)
                .unwrap_or(true)
            {
                latest_video = Some((path, modified));
            }
        }

        if let Some((video, _)) = latest_video {
            let video_path = video.to_string_lossy().to_string();
            update_job_path(&self.config.db_path, job_id, &[JobUpdate::OutputPath(&video_path)])?;
            add_artifact_path(&self.config.db_path, job_id, "output_video", &video_path, None)?;
            let video_name = video
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("output.mp4");
            append_event_path(
                &self.config.db_path,
                job_id,
                &format!("output_video: {video_name}"),
                "artifact",
            )?;
        }

        let manifest = output_root.join("run_manifest.json");
        if manifest.exists() {
            add_artifact_path(
                &self.config.db_path,
                job_id,
                "manifest",
                &manifest.to_string_lossy(),
                None,
            )?;
        }

        Ok(())
    }
}

fn value_as_nonempty_string(value: &Value) -> Option<String> {
    match value {
        Value::String(s) if !s.is_empty() => Some(s.clone()),
        Value::Number(n) => Some(n.to_string()),
        Value::Bool(b) => Some(b.to_string()),
        _ => None,
    }
}

fn is_supported_arg(key: &str) -> bool {
    matches!(
        key,
        "duration"
            | "dry_run"
            | "no_resume"
            | "skip_rvc"
            | "file"
            | "project"
            | "series"
            | "director_mode"
            | "run_mode"
            | "eval_models"
            | "preview"
            | "skip_preflight"
            | "preflight_only"
            | "words_per_segment"
            | "images_per_segment"
            | "segment_count"
            | "yes"
            | "topics_file"
            | "source"
    )
}

fn safe_filename(name: &str) -> String {
    let mut s: String = name
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' {
                ch
            } else {
                '_'
            }
        })
        .collect();
    s = s.trim_start_matches(['.', '_']).to_string();
    if s.is_empty() {
        s = "_".to_string();
    }
    s.chars().take(80).collect()
}

fn read_comfyui_host_port(repo_root: &Path) -> (String, u16) {
    let cfg = repo_root.join("config").join("config.yaml");
    let Ok(text) = fs::read_to_string(cfg) else {
        return ("127.0.0.1".to_string(), 8188);
    };

    let mut host = "127.0.0.1".to_string();
    let mut port = 8188_u16;
    let mut in_image_gen = false;
    let mut in_comfyui = false;

    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("image_gen:") {
            in_image_gen = true;
            in_comfyui = false;
            continue;
        }
        if in_image_gen && trimmed.starts_with("comfyui:") {
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

fn http_get_root(host: &str, port: u16) -> Result<()> {
    let addr = format!("{host}:{port}");
    let mut addrs = addr
        .to_socket_addrs()
        .with_context(|| format!("failed to resolve ComfyUI server address: {addr}"))?;
    let Some(addr) = addrs.next() else {
        bail!("failed to resolve ComfyUI server address: {host}:{port}");
    };
    let mut stream = std::net::TcpStream::connect_timeout(&addr, Duration::from_secs(5))?;
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(5)))?;
    stream.write_all(format!("GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n").as_bytes())?;

    let mut response = [0_u8; 64];
    let n = stream.read(&mut response)?;
    let status = String::from_utf8_lossy(&response[..n]);
    if status.starts_with("HTTP/") && !status.starts_with("HTTP/1.0 2") && !status.starts_with("HTTP/1.1 2") && !status.starts_with("HTTP/2 2") && !status.starts_with("HTTP/3 2") && !status.starts_with("HTTP/1.0 3") && !status.starts_with("HTTP/1.1 3") {
        bail!("ComfyUI server returned error");
    }
    Ok(())
}

fn row_to_job(row: &rusqlite::Row<'_>) -> rusqlite::Result<JobRow> {
    Ok(JobRow {
        id: row.get("id")?,
        topic: row.get("topic")?,
        request_json: row.get("request_json")?,
        image_backend: row.get("image_backend")?,
        comfyui_checkpoint: row.get("comfyui_checkpoint")?,
    })
}

enum JobUpdate<'a> {
    Status(&'a str),
    Progress(i64),
    Heartbeat(&'a str),
    OutputPath(&'a str),
    Error(&'a str),
}

fn update_job_path(db_path: &Path, job_id: i64, fields: &[JobUpdate<'_>]) -> Result<()> {
    if fields.is_empty() {
        return Ok(());
    }

    let now = now_iso();
    let conn = open_job_db_read_write(db_path)?;
    for field in fields {
        match field {
            JobUpdate::Status(value) => {
                conn.execute(
                    "UPDATE jobs SET status=?, updated_at=? WHERE id=?",
                    params![value, now, job_id],
                )?;
            }
            JobUpdate::Progress(value) => {
                conn.execute(
                    "UPDATE jobs SET progress=?, updated_at=? WHERE id=?",
                    params![value, now, job_id],
                )?;
            }
            JobUpdate::Heartbeat(value) => {
                conn.execute(
                    "UPDATE jobs SET heartbeat_at=?, updated_at=? WHERE id=?",
                    params![value, now, job_id],
                )?;
            }
            JobUpdate::OutputPath(value) => {
                conn.execute(
                    "UPDATE jobs SET output_path=?, updated_at=? WHERE id=?",
                    params![value, now, job_id],
                )?;
            }
            JobUpdate::Error(value) => {
                conn.execute(
                    "UPDATE jobs SET error=?, updated_at=? WHERE id=?",
                    params![value, now, job_id],
                )?;
            }
        }
    }
    Ok(())
}

fn append_event_path(db_path: &Path, job_id: i64, message: &str, event_type: &str) -> Result<()> {
    let conn = open_job_db_read_write(db_path)?;
    conn.execute(
        "INSERT INTO job_events (job_id, ts, event_type, message) VALUES (?,?,?,?)",
        params![job_id, now_iso(), event_type, message],
    )
    .context("failed to append job event")?;
    Ok(())
}

fn add_artifact_path(
    db_path: &Path,
    job_id: i64,
    key: &str,
    path: &str,
    meta: Option<&str>,
) -> Result<()> {
    let conn = open_job_db_read_write(db_path)?;
    conn.execute(
        "INSERT INTO job_artifacts (job_id, key, path, meta) VALUES (?,?,?,?)",
        params![job_id, key, path, meta],
    )
    .context("failed to add job artifact")?;
    Ok(())
}

fn get_job_path(db_path: &Path, job_id: i64) -> Result<Option<JobRow>> {
    let conn = open_job_db_read_only(db_path)?;
    let mut stmt = conn.prepare("SELECT * FROM jobs WHERE id=?")?;
    match stmt.query_row([job_id], row_to_job) {
        Ok(job) => Ok(Some(job)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(err) => Err(err).context("failed to query job"),
    }
}

fn get_job_status_path(db_path: &Path, job_id: i64) -> Result<Option<String>> {
    let conn = open_job_db_read_only(db_path)?;
    let mut stmt = conn.prepare("SELECT status FROM jobs WHERE id=?")?;
    match stmt.query_row([job_id], |row| row.get::<_, String>(0)) {
        Ok(status) => Ok(Some(status)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(err) => Err(err).context("failed to query job status"),
    }
}

fn spawn_heartbeat_thread(
    db_path: PathBuf,
    job_id: i64,
    stop: Arc<AtomicBool>,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        while !stop.load(Ordering::SeqCst) {
            let heartbeat = now_iso();
            let _ = update_job_path(&db_path, job_id, &[JobUpdate::Heartbeat(&heartbeat)]);
            let mut waited = 0_u64;
            while waited < HEARTBEAT_INTERVAL_SECONDS * 10 && !stop.load(Ordering::SeqCst) {
                thread::sleep(Duration::from_millis(100));
                waited += 1;
            }
        }
    })
}

fn spawn_stream_thread<R>(reader: R, db_path: PathBuf, job_id: i64) -> thread::JoinHandle<()>
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let reader = BufReader::new(reader);
        for line in reader.lines() {
            match line {
                Ok(text) => {
                    let _ = append_event_path(&db_path, job_id, &text, "log");
                    let heartbeat = now_iso();
                    let _ = update_job_path(&db_path, job_id, &[JobUpdate::Heartbeat(&heartbeat)]);
                }
                Err(err) => {
                    let _ = append_event_path(&db_path, job_id, &format!("stream_error: {err}"), "system");
                    break;
                }
            }
        }
    })
}

fn join_thread_with_timeout(handle: thread::JoinHandle<()>, timeout: Duration) {
    let start = Instant::now();
    while !handle.is_finished() && start.elapsed() < timeout {
        thread::sleep(Duration::from_millis(50));
    }
    if handle.is_finished() {
        let _ = handle.join();
    }
}

fn send_interrupt(child_id: u32) {
    #[cfg(unix)]
    {
        let process_group = format!("-{child_id}");
        let _ = StdCommand::new("kill").arg("-INT").arg(process_group).status();
    }

    #[cfg(windows)]
    {
        let _ = StdCommand::new("taskkill")
            .args(["/PID", &child_id.to_string(), "/T"])
            .status();
    }
}

trait OptionalExtension<T> {
    fn optional(self) -> Result<Option<T>>;
}

impl<T> OptionalExtension<T> for rusqlite::Result<T> {
    fn optional(self) -> Result<Option<T>> {
        match self {
            Ok(value) => Ok(Some(value)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(err) => Err(err.into()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    fn create_seeded_job_db(seed_sql: &str) -> Result<(tempfile::TempDir, PathBuf)> {
        let temp_dir = tempfile::tempdir()?;
        let db_path = temp_dir.path().join("jobs.db");
        let conn = Connection::open(&db_path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        create_test_schema(&conn)?;
        conn.execute_batch(seed_sql)?;
        drop(conn);
        Ok((temp_dir, db_path))
    }

    fn create_test_schema(conn: &Connection) -> Result<()> {
        conn.execute_batch(
            r#"
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE jobs (
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
            CREATE INDEX idx_jobs_status_created ON jobs(status, created_at);
            CREATE TABLE job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                event_type TEXT,
                message TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            CREATE TABLE job_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                key TEXT,
                path TEXT,
                meta TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            "#,
        )
        .context("failed to create test job database schema")?;

        Ok(())
    }

    fn worker_for(temp_dir: &tempfile::TempDir, db_path: PathBuf, fake_python: PathBuf) -> Worker {
        Worker::new(WorkerConfig {
            repo_root: temp_dir.path().to_path_buf(),
            db_path,
            python: fake_python,
            bootstrap: temp_dir.path().join("bootstrap_pipeline.py"),
        })
    }

    fn write_fake_python(repo_root: &Path, body: &str) -> Result<PathBuf> {
        let path = repo_root.join("fake_python.sh");
        fs::write(&path, body)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&path)?.permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&path, perms)?;
        }
        Ok(path)
    }

    fn fetch_job(db_path: &Path, job_id: i64) -> Result<BTreeMap<String, String>> {
        let conn = open_job_db_read_only(db_path)?;
        let mut stmt = conn.prepare(
            "SELECT status, attempt, progress, heartbeat_at, output_path, error FROM jobs WHERE id=?",
        )?;
        let row = stmt.query_row([job_id], |row| {
            let mut map = BTreeMap::new();
            map.insert("status".to_string(), row.get::<_, String>(0)?);
            map.insert("attempt".to_string(), row.get::<_, i64>(1)?.to_string());
            map.insert("progress".to_string(), row.get::<_, i64>(2)?.to_string());
            map.insert(
                "heartbeat_at".to_string(),
                row.get::<_, Option<String>>(3)?.unwrap_or_default(),
            );
            map.insert(
                "output_path".to_string(),
                row.get::<_, Option<String>>(4)?.unwrap_or_default(),
            );
            map.insert("error".to_string(), row.get::<_, Option<String>>(5)?.unwrap_or_default());
            Ok(map)
        })?;
        Ok(row)
    }

    fn count_rows(db_path: &Path, table: &str, where_clause: &str) -> Result<i64> {
        let conn = open_job_db_read_only(db_path)?;
        let sql = format!("SELECT COUNT(*) FROM {table} {where_clause}");
        Ok(conn.query_row(&sql, [], |row| row.get(0))?)
    }

    #[test]
    fn list_jobs_orders_newest_first_and_projects_expected_columns() -> Result<()> {
        let (_temp_dir, db_path) = create_seeded_job_db(
            r#"
            INSERT INTO jobs (status, topic, request_json, created_at, updated_at)
            VALUES
                ('queued', 'First', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
                ('running', 'Second', '{}', '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00');
            "#,
        )?;
        let conn = open_job_db_read_only(&db_path)?;

        let jobs = list_jobs(&conn, 100, 0)?;

        assert_eq!(jobs.len(), 2);
        assert_eq!(jobs[0].topic.as_deref(), Some("Second"));
        assert_eq!(jobs[0].status, "running");
        assert_eq!(jobs[1].topic.as_deref(), Some("First"));

        Ok(())
    }

    #[test]
    fn list_jobs_respects_limit_and_offset() -> Result<()> {
        let (_temp_dir, db_path) = create_seeded_job_db(
            r#"
            INSERT INTO jobs (status, topic, request_json, created_at, updated_at)
            VALUES
                ('queued', 'First', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
                ('queued', 'Second', '{}', '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00'),
                ('queued', 'Third', '{}', '2026-01-03T00:00:00+00:00', '2026-01-03T00:00:00+00:00');
            "#,
        )?;
        let conn = open_job_db_read_only(&db_path)?;

        let jobs = list_jobs(&conn, 1, 1)?;

        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].topic.as_deref(), Some("Second"));

        Ok(())
    }

    #[test]
    fn missing_database_errors_without_creating_files() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        let db_path = temp_dir.path().join("missing").join("jobs.db");

        let err = open_job_db_read_only(&db_path).expect_err("missing database should error");

        assert!(err.to_string().contains("job database not found"));
        assert!(!db_path.exists());
        assert!(!db_path.parent().expect("db path has parent").exists());

        Ok(())
    }

    #[test]
    fn atomic_claim_allows_only_one_worker() -> Result<()> {
        let (temp_dir, db_path) = create_seeded_job_db(
            r#"
            INSERT INTO jobs (status, topic, request_json, created_at, updated_at, attempt)
            VALUES ('queued', 'Atomic', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 0);
            "#,
        )?;
        let fake = write_fake_python(temp_dir.path(), "#!/bin/sh\nexit 0\n")?;
        let worker_a = worker_for(&temp_dir, db_path.clone(), fake.clone());
        let worker_b = worker_for(&temp_dir, db_path.clone(), fake);

        let a = thread::spawn(move || worker_a.claim_next_job().map(|j| j.map(|job| job.id)));
        let b = thread::spawn(move || worker_b.claim_next_job().map(|j| j.map(|job| job.id)));
        let a = a.join().expect("claim thread should join")?;
        let b = b.join().expect("claim thread should join")?;

        let claimed = [a, b].into_iter().flatten().collect::<Vec<_>>();
        assert_eq!(claimed, vec![1]);
        let job = fetch_job(&db_path, 1)?;
        assert_eq!(job.get("attempt").map(String::as_str), Some("1"));
        assert_eq!(job.get("status").map(String::as_str), Some("running"));

        Ok(())
    }

    #[test]
    fn happy_path_captures_video_and_manifest() -> Result<()> {
        let (temp_dir, db_path) = create_seeded_job_db(
            r#"
            INSERT INTO jobs (status, topic, request_json, created_at, updated_at)
            VALUES ('queued', 'Happy Topic', '{"dry_run":true}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
            "#,
        )?;
        let fake = write_fake_python(
            temp_dir.path(),
            "#!/bin/sh\necho fake-log\nmkdir -p studio_outputs/Happy_Topic\nprintf video > studio_outputs/Happy_Topic/out.mp4\nprintf '{}' > studio_outputs/Happy_Topic/run_manifest.json\nexit 0\n",
        )?;
        let worker = worker_for(&temp_dir, db_path.clone(), fake);

        assert_eq!(worker.run_once()?, Some(1));

        let job = fetch_job(&db_path, 1)?;
        assert_eq!(job.get("status").map(String::as_str), Some("succeeded"));
        assert_eq!(job.get("progress").map(String::as_str), Some("100"));
        assert!(job.get("output_path").is_some_and(|p| p.ends_with("out.mp4")));
        assert_eq!(count_rows(&db_path, "job_artifacts", "WHERE job_id=1")?, 2);
        assert_eq!(count_rows(&db_path, "job_events", "WHERE job_id=1 AND event_type='log'")?, 1);

        Ok(())
    }

    #[test]
    fn cancel_requested_mid_run_marks_job_canceled() -> Result<()> {
        let (temp_dir, db_path) = create_seeded_job_db(
            r#"
            INSERT INTO jobs (status, topic, request_json, created_at, updated_at)
            VALUES ('queued', 'Cancel Topic', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
            "#,
        )?;
        let fake = write_fake_python(temp_dir.path(), "#!/bin/sh\necho started\nsleep 60\nexit 0\n")?;
        let worker = worker_for(&temp_dir, db_path.clone(), fake);
        let db_for_thread = db_path.clone();
        let handle = thread::spawn(move || worker.run_once());

        for _ in 0..30 {
            if fetch_job(&db_path, 1)?.get("status").map(String::as_str) == Some("running") {
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }
        update_job_path(&db_path, 1, &[JobUpdate::Status(STATUS_CANCEL_REQUESTED)])?;

        assert_eq!(handle.join().expect("worker thread should join")?, Some(1));
        let job = fetch_job(&db_for_thread, 1)?;
        assert_eq!(job.get("status").map(String::as_str), Some("canceled"));

        Ok(())
    }

    #[test]
    fn failed_pipeline_sets_exit_code_and_heartbeat_stops() -> Result<()> {
        let (temp_dir, db_path) = create_seeded_job_db(
            r#"
            INSERT INTO jobs (status, topic, request_json, created_at, updated_at)
            VALUES ('queued', 'Fail Topic', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
            "#,
        )?;
        let fake = write_fake_python(temp_dir.path(), "#!/bin/sh\necho failing\nexit 7\n")?;
        let worker = worker_for(&temp_dir, db_path.clone(), fake);

        assert_eq!(worker.run_once()?, Some(1));
        let after = fetch_job(&db_path, 1)?;
        let heartbeat = after.get("heartbeat_at").cloned().unwrap_or_default();
        assert_eq!(after.get("status").map(String::as_str), Some("failed"));
        assert_eq!(after.get("error").map(String::as_str), Some("exit_code:7"));

        thread::sleep(Duration::from_secs(2));
        let later = fetch_job(&db_path, 1)?;
        assert_eq!(later.get("heartbeat_at"), Some(&heartbeat));

        Ok(())
    }

    #[test]
    fn read_only_list_jobs_succeeds_while_worker_runs() -> Result<()> {
        let (temp_dir, db_path) = create_seeded_job_db(
            r#"
            INSERT INTO jobs (status, topic, request_json, created_at, updated_at)
            VALUES ('queued', 'Read Topic', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
            "#,
        )?;
        let fake = write_fake_python(temp_dir.path(), "#!/bin/sh\necho started\nsleep 2\nexit 0\n")?;
        let worker = worker_for(&temp_dir, db_path.clone(), fake);
        let handle = thread::spawn(move || worker.run_once());

        for _ in 0..30 {
            if fetch_job(&db_path, 1)?.get("status").map(String::as_str) == Some("running") {
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }

        let conn = open_job_db_read_only(&db_path)?;
        let jobs = list_jobs(&conn, 100, 0)?;
        assert_eq!(jobs.len(), 1);

        assert_eq!(handle.join().expect("worker thread should join")?, Some(1));
        Ok(())
    }
}
