use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{bail, Context, Result};
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

    fn create_seeded_job_db(seed_sql: &str) -> Result<(tempfile::TempDir, Connection)> {
        let temp_dir = tempfile::tempdir()?;
        let db_path = temp_dir.path().join("jobs.db");
        let conn = Connection::open(&db_path)?;
        create_test_schema(&conn)?;
        conn.execute_batch(seed_sql)?;
        drop(conn);

        let read_conn = open_job_db(&db_path)?;
        Ok((temp_dir, read_conn))
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

    #[test]
    fn list_jobs_orders_newest_first_and_projects_expected_columns() -> Result<()> {
        let (_temp_dir, conn) = create_seeded_job_db(
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
        let (_temp_dir, conn) = create_seeded_job_db(
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

    #[test]
    fn missing_database_errors_without_creating_files() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        let db_path = temp_dir.path().join("missing").join("jobs.db");

        let err = open_job_db(&db_path).expect_err("missing database should error");

        assert!(err.to_string().contains("job database not found"));
        assert!(!db_path.exists());
        assert!(!db_path.parent().expect("db path has parent").exists());

        Ok(())
    }
}
