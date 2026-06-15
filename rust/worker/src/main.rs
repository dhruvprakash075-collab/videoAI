mod doctor;
mod status;
mod supervisor_assembly;

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
use videoai_worker::assets::{self, AssetsCommand};
use videoai_worker::ffmpeg_plan::{self, FfmpegCommand};
use videoai_worker::media::{self, MediaCommand};

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

    /// Run environment health checks.
    Doctor {
        /// Path to job database.
        #[arg(long, default_value = DEFAULT_DB_PATH)]
        db_path: PathBuf,

        /// Emit machine-readable JSON.
        #[arg(long)]
        json: bool,

        /// Treat warnings as failures.
        #[arg(long)]
        strict: bool,
    },

    /// Serve read-only job status endpoints.
    Serve {
        /// Path to job database.
        #[arg(long, default_value = DEFAULT_DB_PATH)]
        db_path: PathBuf,

        /// Host address to bind.
        #[arg(long, default_value = "127.0.0.1")]
        host: String,

        /// Port to bind.
        #[arg(long, default_value_t = 8787)]
        port: u16,
    },

    /// Maintain the SQLite job queue.
    Queue {
        #[command(subcommand)]
        command: QueueCommand,
    },

    /// Inspect and validate run output assets.
    Assets {
        #[command(subcommand)]
        command: AssetsCommand,
    },

    /// Inspect media files for file-level QC.
    Media {
        #[command(subcommand)]
        command: MediaCommand,
    },

    /// Plan and execute FFmpeg final assembly (concat, loudnorm, ducking).
    Ffmpeg {
        #[command(subcommand)]
        command: FfmpegCommand,
    },
}

#[derive(Debug, Subcommand)]
enum QueueCommand {
    /// Delete old terminal jobs and their events/artifacts.
    Gc(QueueGcArgs),
}

#[derive(Debug, Clone, clap::Args)]
struct QueueGcArgs {
    /// Path to job database.
    #[arg(long, default_value = DEFAULT_DB_PATH)]
    db_path: PathBuf,

    /// Retain terminal jobs newer than this many days.
    #[arg(long, default_value_t = 30)]
    older_than_days: u32,

    /// Actually delete matching rows. Without this, GC only prints a dry-run plan.
    #[arg(long)]
    apply: bool,

    /// Maximum jobs to delete in one invocation.
    #[arg(long, default_value_t = 100)]
    limit: u32,
}

#[derive(Debug, Serialize)]
struct JobSummary {
    id: i64,
    status: String,
    topic: Option<String>,
    created_at: String,
}

#[derive(Debug, Serialize)]
struct QueueGcPlan {
    dry_run: bool,
    older_than_days: u32,
    cutoff: String,
    matched_jobs: usize,
    deleted_jobs: usize,
    matched_events: i64,
    matched_artifacts: i64,
    jobs: Vec<JobSummary>,
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
        Commands::Doctor {
            db_path,
            json,
            strict,
        } => doctor::run_doctor(db_path, json, strict)?,
        Commands::Serve {
            db_path,
            host,
            port,
        } => {
            tokio::runtime::Runtime::new()
                .context("failed to create tokio runtime")?
                .block_on(status::run_server(db_path, host, port))?;
        }
        Commands::Queue { command } => match command {
            QueueCommand::Gc(args) => run_queue_gc(args)?,
        },
        Commands::Assets { command } => assets::run_command(command)?,
        Commands::Media { command } => media::run_command(command)?,
        Commands::Ffmpeg { command } => ffmpeg_plan::run_command(command)?,
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
    .with_context(|| {
        format!(
            "job database not found or unreadable: {}",
            db_path.display()
        )
    })?;

    conn.busy_timeout(Duration::from_millis(5_000))
        .context("failed to set SQLite busy timeout")?;
    conn.pragma_update(None, "foreign_keys", "ON")
        .context("failed to enable SQLite foreign keys")?;

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
    .with_context(|| {
        format!(
            "job database not found or unreadable: {}",
            db_path.display()
        )
    })?;

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

fn terminal_statuses() -> [&'static str; 3] {
    [STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELED]
}

fn queue_gc_cutoff(days: u32) -> String {
    let days = chrono::Duration::days(i64::from(days));
    (Utc::now() - days)
        .format("%Y-%m-%dT%H:%M:%S%.6f+00:00")
        .to_string()
}

fn run_queue_gc(args: QueueGcArgs) -> Result<()> {
    let cutoff = queue_gc_cutoff(args.older_than_days);
    let mut conn = open_job_db_read_write(&args.db_path)?;
    let plan = queue_gc(
        &mut conn,
        &cutoff,
        args.older_than_days,
        args.limit,
        args.apply,
    )?;
    println!("{}", serde_json::to_string_pretty(&plan)?);
    Ok(())
}

fn queue_gc(
    conn: &mut Connection,
    cutoff: &str,
    older_than_days: u32,
    limit: u32,
    apply: bool,
) -> Result<QueueGcPlan> {
    let statuses = terminal_statuses();
    let mut stmt = conn
        .prepare(
            "SELECT id, status, topic, created_at \
             FROM jobs \
             WHERE status IN (?1, ?2, ?3) AND updated_at < ?4 \
             ORDER BY updated_at ASC, id ASC \
             LIMIT ?5",
        )
        .context("failed to prepare queue gc candidate query")?;

    let rows = stmt
        .query_map(
            params![
                statuses[0],
                statuses[1],
                statuses[2],
                cutoff,
                i64::from(limit)
            ],
            |row| {
                Ok(JobSummary {
                    id: row.get("id")?,
                    status: row.get("status")?,
                    topic: row.get("topic")?,
                    created_at: row.get("created_at")?,
                })
            },
        )
        .context("failed to query queue gc candidates")?;

    let mut jobs = Vec::new();
    for row in rows {
        jobs.push(row.context("failed to read queue gc candidate")?);
    }
    drop(stmt);

    let ids = jobs.iter().map(|job| job.id).collect::<Vec<_>>();
    let matched_events = count_child_rows(conn, "job_events", &ids)?;
    let matched_artifacts = count_child_rows(conn, "job_artifacts", &ids)?;
    let mut deleted_jobs = 0;

    if apply && !ids.is_empty() {
        let tx = conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .context("failed to start queue gc transaction")?;
        for id in &ids {
            let changed = tx
                .execute(
                    "DELETE FROM jobs \
                     WHERE id=?1 AND status IN (?2, ?3, ?4) AND updated_at < ?5",
                    params![id, statuses[0], statuses[1], statuses[2], cutoff],
                )
                .with_context(|| format!("failed to delete terminal job {id}"))?;
            deleted_jobs += changed;
        }
        tx.commit()
            .context("failed to commit queue gc transaction")?;
    }

    Ok(QueueGcPlan {
        dry_run: !apply,
        older_than_days,
        cutoff: cutoff.to_string(),
        matched_jobs: jobs.len(),
        deleted_jobs,
        matched_events,
        matched_artifacts,
        jobs,
    })
}

fn count_child_rows(conn: &Connection, table: &str, job_ids: &[i64]) -> Result<i64> {
    let mut total = 0;
    for job_id in job_ids {
        let sql = format!("SELECT COUNT(*) FROM {table} WHERE job_id=?1");
        total += conn.query_row(&sql, [job_id], |row| row.get::<_, i64>(0))?;
    }
    Ok(total)
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
                &[
                    JobUpdate::Status(STATUS_FAILED),
                    JobUpdate::Error(&err.to_string()),
                ],
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
                    &[
                        JobUpdate::Status(STATUS_FAILED),
                        JobUpdate::Error(&err.to_string()),
                    ],
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

        let mut child = command
            .spawn()
            .context("failed to spawn worker subprocess")?;
        let child_id = child.id();

        let stop = Arc::new(AtomicBool::new(false));
        let heartbeat =
            spawn_heartbeat_thread(self.config.db_path.clone(), job_id, Arc::clone(&stop));

        let mut stream_threads = Vec::new();
        if let Some(stdout) = child.stdout.take() {
            stream_threads.push(spawn_stream_thread(
                stdout,
                self.config.db_path.clone(),
                job_id,
            ));
        }
        if let Some(stderr) = child.stderr.take() {
            stream_threads.push(spawn_stream_thread(
                stderr,
                self.config.db_path.clone(),
                job_id,
            ));
        }

        let mut canceled = false;
        while child
            .try_wait()
            .context("failed to poll worker subprocess")?
            .is_none()
        {
            if let Some(status) = get_job_status_path(&self.config.db_path, job_id)? {
                if status == STATUS_CANCEL_REQUESTED {
                    canceled = true;
                    append_event_path(
                        &self.config.db_path,
                        job_id,
                        "cancellation_requested",
                        "system",
                    )?;
                    send_interrupt(child_id);
                    let deadline = Instant::now() + Duration::from_secs(CANCEL_WAIT_SECONDS);
                    while child
                        .try_wait()
                        .context("failed to poll worker subprocess")?
                        .is_none()
                        && Instant::now() < deadline
                    {
                        thread::sleep(Duration::from_secs(1));
                    }
                    if child
                        .try_wait()
                        .context("failed to poll worker subprocess")?
                        .is_none()
                    {
                        let _ = child.kill();
                    }
                    let _ = child.wait();
                    update_job_path(
                        &self.config.db_path,
                        job_id,
                        &[JobUpdate::Status(STATUS_CANCELED)],
                    )?;
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
            let status = child
                .wait()
                .context("failed to wait for worker subprocess")?;
            let rc = status.code().unwrap_or(-1);
            if get_job_status_path(&self.config.db_path, job_id)?.as_deref()
                != Some(STATUS_CANCELED)
            {
                if rc == 0 {
                    update_job_path(
                        &self.config.db_path,
                        job_id,
                        &[
                            JobUpdate::Status(STATUS_SUCCEEDED),
                            JobUpdate::Progress(100),
                        ],
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
                    if let Err(err) = supervisor_assembly::run_if_enabled(self, job_id) {
                        append_event_path(
                            &self.config.db_path,
                            job_id,
                            &format!("rust_assembly_failed: {err}"),
                            "system",
                        )?;
                    }
                } else {
                    update_job_path(
                        &self.config.db_path,
                        job_id,
                        &[
                            JobUpdate::Status(STATUS_FAILED),
                            JobUpdate::Error(&format!("exit_code:{rc}")),
                        ],
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
            update_job_path(
                &self.config.db_path,
                job_id,
                &[JobUpdate::Status(STATUS_CANCELED)],
            )?;
            append_event_path(
                &self.config.db_path,
                job_id,
                "canceled_from_queued",
                "system",
            )?;
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
            tx.commit()
                .context("failed to commit empty claim transaction")?;
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
        if let Some(content_text) = req
            .remove("content_text")
            .and_then(|v| value_as_nonempty_string(&v))
        {
            let temp_file = self
                .config
                .repo_root
                .join("jobs")
                .join(format!("_{}_content.txt", job.id));
            fs::write(&temp_file, content_text).with_context(|| {
                format!("failed to write temp content file {}", temp_file.display())
            })?;
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

        Ok(BuiltCommand {
            cmd,
            temp_content_file,
        })
    }

    fn capture_artifacts(&self, job_id: i64) -> Result<()> {
        let job = get_job_path(&self.config.db_path, job_id)?
            .context("job disappeared before artifact capture")?;
        let topic_raw = job.topic.unwrap_or_else(|| "unknown".to_string());
        let topic_slug = safe_filename(&topic_raw);
        let output_root = self
            .config
            .repo_root
            .join("studio_outputs")
            .join(topic_slug);
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
            update_job_path(
                &self.config.db_path,
                job_id,
                &[JobUpdate::OutputPath(&video_path)],
            )?;
            add_artifact_path(
                &self.config.db_path,
                job_id,
                "output_video",
                &video_path,
                None,
            )?;
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
    stream.write_all(
        format!("GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n").as_bytes(),
    )?;

    let mut response = [0_u8; 64];
    let n = stream.read(&mut response)?;
    let status = String::from_utf8_lossy(&response[..n]);
    if status.starts_with("HTTP/")
        && !status.starts_with("HTTP/1.0 2")
        && !status.starts_with("HTTP/1.1 2")
        && !status.starts_with("HTTP/2 2")
        && !status.starts_with("HTTP/3 2")
        && !status.starts_with("HTTP/1.0 3")
        && !status.starts_with("HTTP/1.1 3")
    {
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
                    let _ = append_event_path(
                        &db_path,
                        job_id,
                        &format!("stream_error: {err}"),
                        "system",
                    );
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
        let _ = StdCommand::new("kill")
            .arg("-INT")
            .arg(process_group)
            .status();
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