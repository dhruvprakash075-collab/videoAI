use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use rusqlite::{Connection, OpenFlags};
use serde::Serialize;

const DEFAULT_DB_PATH: &str = "studio_projects/jobs/video_ai_jobs.db";

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
}

#[derive(Debug, Serialize)]
struct JobSummary {
    id: i64,
    status: String,
    topic: Option<String>,
    created_at: String,
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::ListJobs {
            db_path,
            limit,
            offset,
        } => {
            let conn = open_job_db(&db_path)?;
            let jobs = list_jobs(&conn, limit, offset)?;
            print_jobs(&jobs)?;
        }
    }

    Ok(())
}

fn open_job_db(db_path: &Path) -> Result<Connection> {
    if let Some(parent) = db_path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create database directory {}", parent.display()))?;
    }

    let conn = Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE,
    )
    .with_context(|| format!("failed to open job database {}", db_path.display()))?;

    conn.busy_timeout(Duration::from_millis(5_000))
        .context("failed to set SQLite busy timeout")?;
    conn.pragma_update(None, "journal_mode", "WAL")
        .context("failed to enable SQLite WAL mode")?;
    conn.pragma_update(None, "busy_timeout", 5_000)
        .context("failed to apply SQLite busy_timeout pragma")?;
    conn.pragma_update(None, "foreign_keys", "ON")
        .context("failed to enable SQLite foreign keys")?;

    ensure_schema(&conn)?;

    Ok(conn)
}

fn ensure_schema(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        r#"
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
        "#,
    )
    .context("failed to ensure job database schema")?;

    Ok(())
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn list_jobs_orders_newest_first_and_projects_expected_columns() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        let db_path = temp_dir.path().join("jobs.db");
        let conn = open_job_db(&db_path)?;
        conn.execute_batch(
            r#"
            INSERT INTO jobs (status, topic, request_json, created_at, updated_at)
            VALUES
                ('queued', 'First', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
                ('running', 'Second', '{}', '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00');
            "#,
        )?;

        let jobs = list_jobs(&conn, 100, 0)?;

        assert_eq!(jobs.len(), 2);
        assert_eq!(jobs[0].topic.as_deref(), Some("Second"));
        assert_eq!(jobs[0].status, "running");
        assert_eq!(jobs[1].topic.as_deref(), Some("First"));

        Ok(())
    }

    #[test]
    fn list_jobs_respects_limit_and_offset() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        let db_path = temp_dir.path().join("jobs.db");
        let conn = open_job_db(&db_path)?;
        conn.execute_batch(
            r#"
            INSERT INTO jobs (status, topic, request_json, created_at, updated_at)
            VALUES
                ('queued', 'First', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
                ('queued', 'Second', '{}', '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00'),
                ('queued', 'Third', '{}', '2026-01-03T00:00:00+00:00', '2026-01-03T00:00:00+00:00');
            "#,
        )?;

        let jobs = list_jobs(&conn, 1, 1)?;

        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].topic.as_deref(), Some("Second"));

        Ok(())
    }
}
