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
use videoai_worker::audio::{self, AudioCommand};
use videoai_worker::checkpoint::{self, CheckpointCommand};
use videoai_worker::ffmpeg_plan::{self, FfmpegCommand};
use videoai_worker::media::{self, MediaCommand};
use videoai_worker::text::{self, TextCommand};

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

    /// Analyze and validate audio files.
    Audio {
        #[command(subcommand)]
        command: AudioCommand,
    },

    /// Manage crash-safe checkpoint state files.
    Checkpoint {
        #[command(subcommand)]
        command: CheckpointCommand,
    },

    /// Inspect media files for file-level QC.
    Media {
        #[command(subcommand)]
        command: MediaCommand,
    },

    /// Split source text into per-segment chunks.
    Text {
        #[command(subcommand)]
        command: TextCommand,
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
        Commands::Audio { command } => audio::run_command(command)?,
        Commands::Checkpoint { command } => checkpoint::run_command(command)?,
        Commands::Media { command } => media::run_command(command)?,
        Commands::Text { command } => text::run_command(command)?,
        Commands::Ffmpeg { command } => ffmpeg_plan::run_command(command)?,
    }

    Ok(())
}
