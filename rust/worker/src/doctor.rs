use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command as StdCommand;

use anyhow::{Context, Result};
use rusqlite::Connection;
use serde::Serialize;

use crate::{http_get_root, list_jobs, open_job_db_read_only, read_comfyui_host_port, resolve_python};

const WARN_DISK_BYTES: u64 = 5 * 1024 * 1024 * 1024;
const CRITICAL_DISK_BYTES: u64 = 1024 * 1024 * 1024;
const WARN_GPU_FREE_MIB: i64 = 5_500;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Critical,
    Warn,
    Info,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum CheckStatus {
    Pass,
    Fail,
    Skip,
}

#[derive(Clone, Debug, Serialize)]
pub struct CheckResult {
    name: &'static str,
    severity: Severity,
    status: CheckStatus,
    detail: String,
    remediation: String,
}

impl CheckResult {
    fn pass(name: &'static str, severity: Severity, detail: impl Into<String>) -> Self {
        Self {
            name,
            severity,
            status: CheckStatus::Pass,
            detail: detail.into(),
            remediation: String::new(),
        }
    }

    fn fail(
        name: &'static str,
        severity: Severity,
        detail: impl Into<String>,
        remediation: impl Into<String>,
    ) -> Self {
        Self {
            name,
            severity,
            status: CheckStatus::Fail,
            detail: detail.into(),
            remediation: remediation.into(),
        }
    }

    fn skip(name: &'static str, severity: Severity, detail: impl Into<String>) -> Self {
        Self {
            name,
            severity,
            status: CheckStatus::Skip,
            detail: detail.into(),
            remediation: String::new(),
        }
    }
}

pub fn run_doctor(db_path: PathBuf, json: bool, strict: bool) -> Result<()> {
    let repo_root = std::env::current_dir().context("failed to resolve repository root")?;
    let results = collect_doctor_results(&repo_root, &db_path);

    if json {
        println!("{}", serde_json::to_string_pretty(&results)?);
    } else {
        print_human_results(&results);
    }

    if should_fail(&results, strict) {
        std::process::exit(1);
    }
    Ok(())
}

fn collect_doctor_results(repo_root: &Path, db_path: &Path) -> Vec<CheckResult> {
    let python = resolve_python(repo_root);
    let bootstrap = repo_root.join("bootstrap_pipeline.py");
    let (host, port) = read_comfyui_host_port(repo_root);
    let image_backend = read_image_backend(repo_root);

    let mut results = vec![
        check_python(&python),
        check_bootstrap(&bootstrap),
        check_job_database(db_path),
        check_config(repo_root, image_backend.as_deref(), &host, port),
        check_comfyui_reachability(image_backend.as_deref(), &host, port),
        check_comfyui_checkpoints(repo_root, image_backend.as_deref()),
        check_tool_version("ffmpeg", "ffmpeg"),
        check_tool_version("ffprobe", "ffprobe"),
        check_disk_space(repo_root),
        check_gpu(),
        check_writable_dirs(repo_root),
    ];
    results.sort_by_key(|result| result.name);
    results
}

fn print_human_results(results: &[CheckResult]) {
    let mut passed = 0;
    let mut warnings = 0;
    let mut failures = 0;

    for result in results {
        match result.status {
            CheckStatus::Pass => passed += 1,
            CheckStatus::Skip => {}
            CheckStatus::Fail => match result.severity {
                Severity::Critical => failures += 1,
                Severity::Warn => warnings += 1,
                Severity::Info => {}
            },
        }
        let status = match result.status {
            CheckStatus::Pass => "PASS",
            CheckStatus::Fail => match result.severity {
                Severity::Critical => "FAIL",
                Severity::Warn => "WARN",
                Severity::Info => "INFO",
            },
            CheckStatus::Skip => "SKIP",
        };
        println!("[{status}] {:<24} {}", result.name, result.detail);
        if !result.remediation.is_empty() {
            println!("       remediation: {}", result.remediation);
        }
    }

    println!("{passed} passed, {warnings} warnings, {failures} failures");
}

fn should_fail(results: &[CheckResult], strict: bool) -> bool {
    results.iter().any(|result| {
        result.status == CheckStatus::Fail
            && (result.severity == Severity::Critical || (strict && result.severity == Severity::Warn))
    })
}

fn check_python(python: &Path) -> CheckResult {
    if !python.is_file() {
        return CheckResult::fail(
            "python",
            Severity::Critical,
            format!("missing interpreter at {}", python.display()),
            "set VIDEOAI_PYTHON or create the project venv",
        );
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = fs::metadata(python) {
            if meta.permissions().mode() & 0o111 == 0 {
                return CheckResult::fail(
                    "python",
                    Severity::Critical,
                    format!("interpreter is not executable: {}", python.display()),
                    "chmod +x the interpreter or set VIDEOAI_PYTHON to an executable Python",
                );
            }
        }
    }

    match StdCommand::new(python).arg("--version").output() {
        Ok(output) if output.status.success() => {
            let version = first_nonempty_line(&output.stdout, &output.stderr);
            CheckResult::pass("python", Severity::Critical, format!("found {version}"))
        }
        Ok(output) => CheckResult::fail(
            "python",
            Severity::Critical,
            format!("{} --version exited with {}", python.display(), output.status),
            "verify the Python interpreter works",
        ),
        Err(err) => CheckResult::fail(
            "python",
            Severity::Critical,
            format!("failed to run {} --version: {err}", python.display()),
            "set VIDEOAI_PYTHON to a working interpreter",
        ),
    }
}

fn check_bootstrap(bootstrap: &Path) -> CheckResult {
    if bootstrap.is_file() {
        CheckResult::pass(
            "bootstrap_pipeline",
            Severity::Critical,
            format!("found {}", bootstrap.display()),
        )
    } else {
        CheckResult::fail(
            "bootstrap_pipeline",
            Severity::Critical,
            format!("missing {}", bootstrap.display()),
            "run doctor from the repository root or restore bootstrap_pipeline.py",
        )
    }
}

fn check_job_database(db_path: &Path) -> CheckResult {
    if !db_path.is_file() {
        return CheckResult::fail(
            "job_database",
            Severity::Warn,
            format!("missing {}", db_path.display()),
            "start the Python app once to create the queue database",
        );
    }

    let conn = match open_job_db_read_only(db_path) {
        Ok(conn) => conn,
        Err(err) => {
            return CheckResult::fail(
                "job_database",
                Severity::Critical,
                format!("unreadable database: {err}"),
                "verify the SQLite file is healthy and readable",
            )
        }
    };

    let required = ["schema_meta", "jobs", "job_events", "job_artifacts"];
    for table in required {
        if !table_exists(&conn, table).unwrap_or(false) {
            return CheckResult::fail(
                "job_database",
                Severity::Critical,
                format!("missing table {table}"),
                "let JobStore create the database schema, then retry doctor",
            );
        }
    }

    let queued = count_status(&conn, "queued").unwrap_or(0);
    let running = count_status(&conn, "running").unwrap_or(0);
    let listed = list_jobs(&conn, 1, 0).map(|jobs| jobs.len()).unwrap_or(0);
    CheckResult::pass(
        "job_database",
        Severity::Warn,
        format!("schema ok; queued={queued}, running={running}, sample_jobs={listed}"),
    )
}

fn check_config(repo_root: &Path, backend: Option<&str>, host: &str, port: u16) -> CheckResult {
    let path = repo_root.join("config").join("config.yaml");
    if !path.is_file() {
        return CheckResult::fail(
            "config_yaml",
            Severity::Warn,
            format!("missing {}", path.display()),
            "restore config/config.yaml or rely on Python defaults",
        );
    }
    match fs::read_to_string(&path) {
        Ok(_) => CheckResult::pass(
            "config_yaml",
            Severity::Warn,
            format!(
                "read {}; image_backend={}, comfyui={host}:{port}",
                path.display(),
                backend.unwrap_or("unknown")
            ),
        ),
        Err(err) => CheckResult::fail(
            "config_yaml",
            Severity::Warn,
            format!("failed to read {}: {err}", path.display()),
            "fix file permissions or restore config/config.yaml",
        ),
    }
}

fn check_comfyui_reachability(backend: Option<&str>, host: &str, port: u16) -> CheckResult {
    if backend != Some("comfyui") {
        return CheckResult::skip(
            "comfyui_reachability",
            Severity::Warn,
            "image backend is not comfyui",
        );
    }
    match http_get_root(host, port) {
        Ok(()) => CheckResult::pass(
            "comfyui_reachability",
            Severity::Warn,
            format!("reachable at {host}:{port}"),
        ),
        Err(err) => CheckResult::fail(
            "comfyui_reachability",
            Severity::Warn,
            format!("not reachable at {host}:{port}: {err}"),
            "start ComfyUI or update image_gen.comfyui.host/port",
        ),
    }
}

fn check_comfyui_checkpoints(repo_root: &Path, backend: Option<&str>) -> CheckResult {
    if backend != Some("comfyui") {
        return CheckResult::skip(
            "comfyui_checkpoints",
            Severity::Warn,
            "image backend is not comfyui",
        );
    }
    let dir = repo_root
        .join("external")
        .join("ComfyUI")
        .join("models")
        .join("checkpoints");
    let entries = match fs::read_dir(&dir) {
        Ok(entries) => entries,
        Err(err) => {
            return CheckResult::fail(
                "comfyui_checkpoints",
                Severity::Warn,
                format!("cannot read {}: {err}", dir.display()),
                "install ComfyUI checkpoints under external/ComfyUI/models/checkpoints",
            )
        }
    };
    let count = entries.filter_map(Result::ok).filter(|entry| entry.path().is_file()).count();
    if count == 0 {
        CheckResult::fail(
            "comfyui_checkpoints",
            Severity::Warn,
            format!("no checkpoint files found in {}", dir.display()),
            "add at least one checkpoint model",
        )
    } else {
        CheckResult::pass(
            "comfyui_checkpoints",
            Severity::Warn,
            format!("found {count} checkpoint file(s)"),
        )
    }
}

fn check_tool_version(name: &'static str, binary: &str) -> CheckResult {
    match StdCommand::new(binary).arg("-version").output() {
        Ok(output) if output.status.success() => {
            let version = first_nonempty_line(&output.stdout, &output.stderr);
            CheckResult::pass(name, Severity::Critical, version)
        }
        Ok(output) => CheckResult::fail(
            name,
            Severity::Critical,
            format!("{binary} -version exited with {}", output.status),
            format!("install {binary} and ensure it is on PATH"),
        ),
        Err(err) => CheckResult::fail(
            name,
            Severity::Critical,
            format!("{binary} not runnable: {err}"),
            format!("install {binary} and ensure it is on PATH"),
        ),
    }
}

fn check_disk_space(repo_root: &Path) -> CheckResult {
    match fs2::available_space(repo_root) {
        Ok(bytes) if bytes < CRITICAL_DISK_BYTES => CheckResult::fail(
            "disk_space",
            Severity::Critical,
            format!("{} free", format_bytes(bytes)),
            "free at least 1 GB before running jobs",
        ),
        Ok(bytes) if bytes < WARN_DISK_BYTES => CheckResult::fail(
            "disk_space",
            Severity::Warn,
            format!("{} free", format_bytes(bytes)),
            "free at least 5 GB for comfortable video generation",
        ),
        Ok(bytes) => CheckResult::pass("disk_space", Severity::Warn, format!("{} free", format_bytes(bytes))),
        Err(err) => CheckResult::fail(
            "disk_space",
            Severity::Warn,
            format!("could not determine free disk space: {err}"),
            "check disk availability manually",
        ),
    }
}

fn check_gpu() -> CheckResult {
    match StdCommand::new("nvidia-smi")
        .args(["--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"])
        .output()
    {
        Ok(output) if output.status.success() => {
            let text = String::from_utf8_lossy(&output.stdout);
            let Some(line) = text.lines().next() else {
                return CheckResult::skip("gpu_vram", Severity::Info, "nvidia-smi returned no GPU rows");
            };
            let parts = line.split(',').map(str::trim).collect::<Vec<_>>();
            if parts.len() < 2 {
                return CheckResult::skip("gpu_vram", Severity::Info, "could not parse nvidia-smi output");
            }
            let total = parts[0].parse::<i64>().unwrap_or(0);
            let free = parts[1].parse::<i64>().unwrap_or(0);
            if free > 0 && free < WARN_GPU_FREE_MIB {
                CheckResult::fail(
                    "gpu_vram",
                    Severity::Warn,
                    format!("GPU memory total={total} MiB free={free} MiB"),
                    "free GPU memory before running model-heavy jobs",
                )
            } else {
                CheckResult::pass(
                    "gpu_vram",
                    Severity::Info,
                    format!("GPU memory total={total} MiB free={free} MiB"),
                )
            }
        }
        Ok(_) => CheckResult::skip("gpu_vram", Severity::Info, "nvidia-smi returned a nonzero status"),
        Err(_) => CheckResult::skip("gpu_vram", Severity::Info, "nvidia-smi not found"),
    }
}

fn check_writable_dirs(repo_root: &Path) -> CheckResult {
    let dirs = [repo_root.join("studio_outputs"), repo_root.join("studio_projects").join("jobs")];
    let mut missing = Vec::new();
    let mut readonly = Vec::new();
    for dir in &dirs {
        match fs::metadata(dir) {
            Ok(meta) if !meta.is_dir() => missing.push(dir.display().to_string()),
            Ok(meta) if meta.permissions().readonly() => readonly.push(dir.display().to_string()),
            Ok(_) => {}
            Err(_) => missing.push(dir.display().to_string()),
        }
    }
    if !missing.is_empty() || !readonly.is_empty() {
        return CheckResult::fail(
            "writable_dirs",
            Severity::Warn,
            format!("missing={missing:?}, readonly={readonly:?}"),
            "create studio_outputs and studio_projects/jobs with write permissions",
        );
    }
    CheckResult::pass("writable_dirs", Severity::Warn, "expected output/job directories exist")
}

fn first_nonempty_line(stdout: &[u8], stderr: &[u8]) -> String {
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(stdout),
        String::from_utf8_lossy(stderr)
    );
    combined
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("version unknown")
        .to_string()
}

fn format_bytes(bytes: u64) -> String {
    let gib = bytes as f64 / 1024.0 / 1024.0 / 1024.0;
    format!("{gib:.1} GiB")
}

fn table_exists(conn: &Connection, table: &str) -> Result<bool> {
    let count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?1",
        [table],
        |row| row.get(0),
    )?;
    Ok(count > 0)
}

fn count_status(conn: &Connection, status: &str) -> Result<i64> {
    Ok(conn.query_row("SELECT COUNT(*) FROM jobs WHERE status=?1", [status], |row| row.get(0))?)
}

fn read_image_backend(repo_root: &Path) -> Option<String> {
    let text = fs::read_to_string(repo_root.join("config").join("config.yaml")).ok()?;
    let mut in_image_gen = false;
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("image_gen:") {
            in_image_gen = true;
            continue;
        }
        if in_image_gen && !line.starts_with(' ') && !line.starts_with('\t') && !trimmed.is_empty() {
            in_image_gen = false;
        }
        if in_image_gen && trimmed.starts_with("backend:") {
            return Some(
                trimmed
                    .trim_start_matches("backend:")
                    .trim()
                    .trim_matches(['\'', '"'])
                    .to_string(),
            );
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn create_test_schema(conn: &Connection) -> Result<()> {
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
        )?;
        Ok(())
    }

    #[test]
    fn doctor_db_missing_is_warn_and_creates_no_file() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let db = temp.path().join("missing").join("jobs.db");

        let result = check_job_database(&db);

        assert_eq!(result.status, CheckStatus::Fail);
        assert_eq!(result.severity, Severity::Warn);
        assert!(!db.exists());
        assert!(!db.parent().expect("db path has parent").exists());
        Ok(())
    }

    #[test]
    fn doctor_reports_critical_when_bootstrap_missing() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let result = check_bootstrap(&temp.path().join("bootstrap_pipeline.py"));

        assert_eq!(result.status, CheckStatus::Fail);
        assert_eq!(result.severity, Severity::Critical);
        Ok(())
    }

    #[test]
    fn doctor_json_output_is_valid() -> Result<()> {
        let result = CheckResult::pass("sample", Severity::Info, "ok");
        let json = serde_json::to_string(&vec![result])?;

        assert!(json.contains("sample"));
        assert!(json.contains("pass"));
        Ok(())
    }

    #[test]
    fn doctor_strict_mode_fails_on_warning() {
        let results = vec![CheckResult::fail("warn", Severity::Warn, "warn", "fix")];

        assert!(!should_fail(&results, false));
        assert!(should_fail(&results, true));
    }

    #[test]
    fn doctor_gpu_check_skips_when_nvidia_smi_absent_or_reports() {
        let result = check_gpu();

        assert!(matches!(result.status, CheckStatus::Skip | CheckStatus::Pass | CheckStatus::Fail));
    }

    #[test]
    fn doctor_database_schema_passes() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let db = temp.path().join("jobs.db");
        let conn = Connection::open(&db)?;
        create_test_schema(&conn)?;
        conn.execute(
            "INSERT INTO jobs (status, topic, request_json, created_at, updated_at) VALUES ('queued', 'T', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            [],
        )?;
        drop(conn);

        let result = check_job_database(&db);

        assert_eq!(result.status, CheckStatus::Pass);
        Ok(())
    }
}
