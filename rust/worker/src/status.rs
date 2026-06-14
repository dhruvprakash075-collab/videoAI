use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{bail, Context, Result};
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use tokio::net::TcpListener;

#[derive(Clone)]
struct AppState {
    db_path: Arc<PathBuf>,
}

#[derive(Debug, Serialize)]
struct HealthResponse {
    ok: bool,
}

#[derive(Debug, Serialize)]
struct ReadyResponse {
    ready: bool,
    database: String,
    detail: String,
}

#[derive(Debug, Serialize)]
struct ErrorResponse {
    error: String,
}

#[derive(Debug, Serialize)]
struct StatsResponse {
    total: i64,
    by_status: BTreeMap<String, i64>,
    queued: i64,
    running: i64,
    cancel_requested: i64,
    succeeded: i64,
    failed: i64,
    canceled: i64,
}

#[derive(Debug, Serialize)]
struct JobRecord {
    id: i64,
    status: String,
    topic: Option<String>,
    request_json: Option<String>,
    created_at: String,
    updated_at: String,
    heartbeat_at: Option<String>,
    progress: Option<i64>,
    attempt: Option<i64>,
    image_backend: Option<String>,
    comfyui_checkpoint: Option<String>,
    fallback_backend: Option<String>,
    output_path: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize)]
struct JobsResponse {
    jobs: Vec<JobRecord>,
    limit: u32,
    offset: u32,
}

#[derive(Debug, Serialize)]
struct JobEventRecord {
    id: i64,
    ts: String,
    event_type: Option<String>,
    message: Option<String>,
}

#[derive(Debug, Serialize)]
struct JobArtifactRecord {
    id: i64,
    key: Option<String>,
    path: Option<String>,
    meta: Option<String>,
}

#[derive(Debug, Serialize)]
struct JobDetailResponse {
    job: JobRecord,
    events: Vec<JobEventRecord>,
    artifacts: Vec<JobArtifactRecord>,
}

#[derive(Debug, Deserialize)]
struct PageParams {
    limit: Option<u32>,
    offset: Option<u32>,
}

pub async fn run_server(db_path: PathBuf, host: String, port: u16) -> Result<()> {
    if !db_path.is_file() {
        bail!(
            "job database not found: {} — start the Python app once to create it",
            db_path.display()
        );
    }
    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .with_context(|| format!("invalid serve address: {host}:{port}"))?;
    let listener = TcpListener::bind(addr)
        .await
        .with_context(|| format!("failed to bind status endpoint on {addr}"))?;
    axum::serve(listener, build_router(db_path))
        .await
        .context("status endpoint failed")?;
    Ok(())
}

fn build_router(db_path: PathBuf) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route("/readyz", get(readyz))
        .route("/stats", get(stats))
        .route("/jobs", get(jobs))
        .route("/jobs/:id", get(job_detail))
        .with_state(AppState {
            db_path: Arc::new(db_path),
        })
}

async fn healthz() -> Json<HealthResponse> {
    Json(HealthResponse { ok: true })
}

async fn readyz(State(state): State<AppState>) -> Response {
    match open_read_only(&state.db_path).and_then(|conn| validate_schema(&conn)) {
        Ok(()) => (
            StatusCode::OK,
            Json(ReadyResponse {
                ready: true,
                database: state.db_path.display().to_string(),
                detail: "job database is readable".to_string(),
            }),
        )
            .into_response(),
        Err(err) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(ReadyResponse {
                ready: false,
                database: state.db_path.display().to_string(),
                detail: err.to_string(),
            }),
        )
            .into_response(),
    }
}

async fn stats(State(state): State<AppState>) -> Response {
    match read_stats(&state.db_path) {
        Ok(response) => (StatusCode::OK, Json(response)).into_response(),
        Err(err) => error_response(StatusCode::SERVICE_UNAVAILABLE, err),
    }
}

async fn jobs(State(state): State<AppState>, Query(params): Query<PageParams>) -> Response {
    let limit = params.limit.unwrap_or(100).min(500);
    let offset = params.offset.unwrap_or(0);
    match read_jobs(&state.db_path, limit, offset) {
        Ok(jobs) => (StatusCode::OK, Json(JobsResponse { jobs, limit, offset })).into_response(),
        Err(err) => error_response(StatusCode::SERVICE_UNAVAILABLE, err),
    }
}

async fn job_detail(State(state): State<AppState>, AxumPath(id): AxumPath<i64>) -> Response {
    match read_job_detail(&state.db_path, id) {
        Ok(Some(response)) => (StatusCode::OK, Json(response)).into_response(),
        Ok(None) => error_response(StatusCode::NOT_FOUND, anyhow::anyhow!("job not found: {id}")),
        Err(err) => error_response(StatusCode::SERVICE_UNAVAILABLE, err),
    }
}

fn error_response(status: StatusCode, err: anyhow::Error) -> Response {
    (status, Json(ErrorResponse { error: err.to_string() })).into_response()
}

fn open_read_only(db_path: &Path) -> Result<Connection> {
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
    conn.busy_timeout(std::time::Duration::from_millis(5_000))
        .context("failed to set SQLite busy timeout")?;
    Ok(conn)
}

fn validate_schema(conn: &Connection) -> Result<()> {
    for table in ["schema_meta", "jobs", "job_events", "job_artifacts"] {
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?1",
            [table],
            |row| row.get(0),
        )?;
        if count == 0 {
            bail!("missing required table: {table}");
        }
    }
    Ok(())
}

fn read_stats(db_path: &Path) -> Result<StatsResponse> {
    let conn = open_read_only(db_path)?;
    validate_schema(&conn)?;
    let total = conn.query_row("SELECT COUNT(*) FROM jobs", [], |row| row.get(0))?;
    let mut by_status = BTreeMap::new();
    let mut stmt = conn.prepare("SELECT status, COUNT(*) FROM jobs GROUP BY status ORDER BY status")?;
    let rows = stmt.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?)))?;
    for row in rows {
        let (status, count) = row?;
        by_status.insert(status, count);
    }
    Ok(StatsResponse {
        total,
        queued: count_status(&by_status, "queued"),
        running: count_status(&by_status, "running"),
        cancel_requested: count_status(&by_status, "cancel_requested"),
        succeeded: count_status(&by_status, "succeeded"),
        failed: count_status(&by_status, "failed"),
        canceled: count_status(&by_status, "canceled"),
        by_status,
    })
}

fn count_status(by_status: &BTreeMap<String, i64>, status: &str) -> i64 {
    by_status.get(status).copied().unwrap_or(0)
}

fn read_jobs(db_path: &Path, limit: u32, offset: u32) -> Result<Vec<JobRecord>> {
    let conn = open_read_only(db_path)?;
    validate_schema(&conn)?;
    let mut stmt = conn.prepare(
        "SELECT id, status, topic, request_json, created_at, updated_at, heartbeat_at, progress, attempt, image_backend, comfyui_checkpoint, fallback_backend, output_path, error \
         FROM jobs ORDER BY created_at DESC LIMIT ?1 OFFSET ?2",
    )?;
    let rows = stmt.query_map((i64::from(limit), i64::from(offset)), row_to_job_record)?;
    collect_rows(rows)
}

fn read_job_detail(db_path: &Path, id: i64) -> Result<Option<JobDetailResponse>> {
    let conn = open_read_only(db_path)?;
    validate_schema(&conn)?;
    let mut stmt = conn.prepare(
        "SELECT id, status, topic, request_json, created_at, updated_at, heartbeat_at, progress, attempt, image_backend, comfyui_checkpoint, fallback_backend, output_path, error \
         FROM jobs WHERE id=?1",
    )?;
    let job = match stmt.query_row([id], row_to_job_record) {
        Ok(job) => job,
        Err(rusqlite::Error::QueryReturnedNoRows) => return Ok(None),
        Err(err) => return Err(err.into()),
    };
    let mut events_stmt = conn.prepare(
        "SELECT id, ts, event_type, message FROM job_events WHERE job_id=?1 ORDER BY id ASC",
    )?;
    let events = collect_rows(events_stmt.query_map([id], |row| {
        Ok(JobEventRecord {
            id: row.get("id")?,
            ts: row.get("ts")?,
            event_type: row.get("event_type")?,
            message: row.get("message")?,
        })
    })?)?;
    let mut artifacts_stmt = conn.prepare(
        "SELECT id, key, path, meta FROM job_artifacts WHERE job_id=?1 ORDER BY id ASC",
    )?;
    let artifacts = collect_rows(artifacts_stmt.query_map([id], |row| {
        Ok(JobArtifactRecord {
            id: row.get("id")?,
            key: row.get("key")?,
            path: row.get("path")?,
            meta: row.get("meta")?,
        })
    })?)?;
    Ok(Some(JobDetailResponse { job, events, artifacts }))
}

fn row_to_job_record(row: &rusqlite::Row<'_>) -> rusqlite::Result<JobRecord> {
    Ok(JobRecord {
        id: row.get("id")?,
        status: row.get("status")?,
        topic: row.get("topic")?,
        request_json: row.get("request_json")?,
        created_at: row.get("created_at")?,
        updated_at: row.get("updated_at")?,
        heartbeat_at: row.get("heartbeat_at")?,
        progress: row.get("progress")?,
        attempt: row.get("attempt")?,
        image_backend: row.get("image_backend")?,
        comfyui_checkpoint: row.get("comfyui_checkpoint")?,
        fallback_backend: row.get("fallback_backend")?,
        output_path: row.get("output_path")?,
        error: row.get("error")?,
    })
}

fn collect_rows<T, F>(rows: rusqlite::MappedRows<'_, F>) -> Result<Vec<T>>
where
    F: FnMut(&rusqlite::Row<'_>) -> rusqlite::Result<T>,
{
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::{to_bytes, Body};
    use axum::http::{Request, StatusCode};
    use serde_json::json;
    use tower::ServiceExt;

    fn create_test_db() -> Result<(tempfile::TempDir, PathBuf)> {
        let temp = tempfile::tempdir()?;
        let db_path = temp.path().join("jobs.db");
        let conn = Connection::open(&db_path)?;
        conn.execute_batch(
            r#"
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT);
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
            CREATE TABLE job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                event_type TEXT,
                message TEXT
            );
            CREATE TABLE job_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                key TEXT,
                path TEXT,
                meta TEXT
            );
            INSERT INTO jobs (status, topic, request_json, created_at, updated_at, progress, attempt)
            VALUES
                ('queued', 'First', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 0, 0),
                ('running', 'Second', '{}', '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00', 10, 1);
            INSERT INTO job_events (job_id, ts, event_type, message)
            VALUES (2, '2026-01-02T00:00:01+00:00', 'log', 'started');
            INSERT INTO job_artifacts (job_id, key, path, meta)
            VALUES (2, 'output_video', 'studio_outputs/Second/out.mp4', NULL);
            "#,
        )?;
        drop(conn);
        Ok((temp, db_path))
    }

    async fn request_json(router: Router, uri: &str) -> Result<(StatusCode, serde_json::Value)> {
        let response = router
            .oneshot(Request::builder().uri(uri).body(Body::empty())?)
            .await?;
        let status = response.status();
        let bytes = to_bytes(response.into_body(), usize::MAX).await?;
        Ok((status, serde_json::from_slice(&bytes)?))
    }

    #[tokio::test]
    async fn healthz_returns_ok() -> Result<()> {
        let (_temp, db_path) = create_test_db()?;
        let (status, body) = request_json(build_router(db_path), "/healthz").await?;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(body, json!({"ok": true}));
        Ok(())
    }

    #[tokio::test]
    async fn readyz_fails_for_missing_database_without_creating_file() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let db_path = temp.path().join("missing").join("jobs.db");
        let (status, body) = request_json(build_router(db_path.clone()), "/readyz").await?;

        assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(body["ready"], false);
        assert!(!db_path.exists());
        assert!(!db_path.parent().expect("path has parent").exists());
        Ok(())
    }

    #[tokio::test]
    async fn stats_reports_status_counts() -> Result<()> {
        let (_temp, db_path) = create_test_db()?;
        let (status, body) = request_json(build_router(db_path), "/stats").await?;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["total"], 2);
        assert_eq!(body["queued"], 1);
        assert_eq!(body["running"], 1);
        Ok(())
    }

    #[tokio::test]
    async fn jobs_lists_newest_first_with_limit() -> Result<()> {
        let (_temp, db_path) = create_test_db()?;
        let (status, body) = request_json(build_router(db_path), "/jobs?limit=1").await?;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["jobs"].as_array().expect("jobs array").len(), 1);
        assert_eq!(body["jobs"][0]["topic"], "Second");
        Ok(())
    }

    #[tokio::test]
    async fn job_detail_includes_events_and_artifacts() -> Result<()> {
        let (_temp, db_path) = create_test_db()?;
        let (status, body) = request_json(build_router(db_path), "/jobs/2").await?;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["job"]["topic"], "Second");
        assert_eq!(body["events"].as_array().expect("events array").len(), 1);
        assert_eq!(body["artifacts"].as_array().expect("artifacts array").len(), 1);
        Ok(())
    }

    #[tokio::test]
    async fn job_detail_404_for_missing_job() -> Result<()> {
        let (_temp, db_path) = create_test_db()?;
        let (status, body) = request_json(build_router(db_path), "/jobs/999").await?;

        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(body["error"], "job not found: 999");
        Ok(())
    }
}
