use std::fs;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use chrono::Utc;
use clap::{Args, Subcommand};
use serde::Serialize;
use serde_json::{Map, Value};

const RETRY_ATTEMPTS: u32 = 5;
const RETRY_SLEEP: Duration = Duration::from_millis(500);

#[derive(Debug, Subcommand)]
pub enum CheckpointCommand {
    /// Read a checkpoint JSON file.
    Get(CheckpointGetArgs),

    /// Save a single step into a checkpoint JSON file.
    Save(CheckpointSaveArgs),

    /// Clear a checkpoint and its dirty siblings.
    Clear(CheckpointClearArgs),
}

#[derive(Clone, Debug, Args)]
pub struct CheckpointGetArgs {
    /// Checkpoint directory.
    #[arg(long, default_value = "studio_checkpoints")]
    pub dir: PathBuf,

    /// Topic name used to derive the checkpoint filename.
    #[arg(long)]
    pub topic: String,

    /// Soft warning threshold in hours. Zero disables the threshold warning.
    #[arg(long, default_value_t = 0.0)]
    pub max_age_hours: f64,
}

#[derive(Clone, Debug, Args)]
pub struct CheckpointSaveArgs {
    /// Checkpoint directory.
    #[arg(long, default_value = "studio_checkpoints")]
    pub dir: PathBuf,

    /// Topic name used to derive the checkpoint filename.
    #[arg(long)]
    pub topic: String,

    /// Step key to update.
    #[arg(long)]
    pub step: String,

    /// JSON object to store for the step.
    #[arg(long)]
    pub data_json: String,
}

#[derive(Clone, Debug, Args)]
pub struct CheckpointClearArgs {
    /// Checkpoint directory.
    #[arg(long, default_value = "studio_checkpoints")]
    pub dir: PathBuf,

    /// Topic name used to derive the checkpoint filename.
    #[arg(long)]
    pub topic: String,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct CheckpointReadReport {
    pub found: bool,
    pub path: String,
    pub data: Option<Value>,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct CheckpointSaveReport {
    pub path: String,
    pub attempts: u32,
    pub backed_up: bool,
    pub data: Value,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct CheckpointClearReport {
    pub path: String,
    pub removed: Vec<String>,
    pub warnings: Vec<String>,
}

pub fn run_command(command: CheckpointCommand) -> Result<()> {
    match command {
        CheckpointCommand::Get(args) => {
            let report = get_checkpoint(&args.dir, &args.topic, args.max_age_hours)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        CheckpointCommand::Save(args) => {
            let data: Value =
                serde_json::from_str(&args.data_json).context("--data-json must be valid JSON")?;
            let report = save_checkpoint(&args.dir, &args.topic, &args.step, data)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        CheckpointCommand::Clear(args) => {
            let report = clear_checkpoint(&args.dir, &args.topic)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
    }
    Ok(())
}

pub fn checkpoint_path(dir: &Path, topic: &str) -> PathBuf {
    dir.join(format!("{}.json", safe_filename(topic)))
}

pub fn get_checkpoint(dir: &Path, topic: &str, max_age_hours: f64) -> Result<CheckpointReadReport> {
    let path = checkpoint_path(dir, topic);
    if !path.exists() {
        return Ok(CheckpointReadReport {
            found: false,
            path: display_path(&path),
            data: None,
            warnings: Vec::new(),
        });
    }

    let mut warnings = checkpoint_age_warnings(&path, topic, max_age_hours)?;
    let text = fs::read_to_string(&path)
        .with_context(|| format!("failed to read checkpoint {}", path.display()))?;
    let data = match serde_json::from_str(&text) {
        Ok(data) => Some(data),
        Err(err) => {
            warnings.push(format!(
                "Corrupt checkpoint {}; ignoring: {err}",
                path.display()
            ));
            None
        }
    };

    Ok(CheckpointReadReport {
        found: data.is_some(),
        path: display_path(&path),
        data,
        warnings,
    })
}

pub fn save_checkpoint(
    dir: &Path,
    topic: &str,
    step: &str,
    data: Value,
) -> Result<CheckpointSaveReport> {
    fs::create_dir_all(dir)
        .with_context(|| format!("failed to create checkpoint directory {}", dir.display()))?;

    let path = checkpoint_path(dir, topic);
    let tmp = tmp_path(&path);
    let mut body = read_raw_checkpoint(&path)?;
    let mut step_body = match data {
        Value::Object(map) => map,
        other => {
            let mut map = Map::new();
            map.insert("value".to_string(), other);
            map
        }
    };
    step_body.insert("ts".to_string(), Value::String(Utc::now().to_rfc3339()));
    body.insert(step.to_string(), Value::Object(step_body));
    let final_body = Value::Object(body.clone());

    fs::write(
        &tmp,
        serde_json::to_string_pretty(&final_body).context("failed to encode checkpoint JSON")?,
    )
    .with_context(|| format!("failed to write temp checkpoint {}", tmp.display()))?;

    let mut attempts = 0;
    let mut backed_up = false;
    loop {
        attempts += 1;
        let attempt_result = (|| -> Result<()> {
            if path.exists() {
                fs::copy(&path, bak_path(&path))
                    .with_context(|| format!("failed to back up checkpoint {}", path.display()))?;
                backed_up = true;
            }
            fs::rename(&tmp, &path).with_context(|| {
                format!(
                    "failed to atomically replace checkpoint {} from {}",
                    path.display(),
                    tmp.display()
                )
            })?;
            Ok(())
        })();

        match attempt_result {
            Ok(()) => break,
            Err(err) if attempts < RETRY_ATTEMPTS => {
                thread::sleep(RETRY_SLEEP);
                if !tmp.exists() {
                    fs::write(
                        &tmp,
                        serde_json::to_string_pretty(&final_body)
                            .context("failed to re-encode checkpoint JSON")?,
                    )?;
                }
                drop(err);
            }
            Err(err) => {
                let _ = fs::remove_file(&tmp);
                return Err(err);
            }
        }
    }

    Ok(CheckpointSaveReport {
        path: display_path(&path),
        attempts,
        backed_up,
        data: Value::Object(body),
    })
}

pub fn clear_checkpoint(dir: &Path, topic: &str) -> Result<CheckpointClearReport> {
    let path = checkpoint_path(dir, topic);
    let mut removed = Vec::new();
    let mut warnings = Vec::new();

    for candidate in clear_candidates(&path)? {
        if !candidate.exists() {
            continue;
        }
        match fs::remove_file(&candidate) {
            Ok(()) => removed.push(display_path(&candidate)),
            Err(err) => warnings.push(format!(
                "Could not remove checkpoint sibling {}: {err}",
                candidate.display()
            )),
        }
    }

    Ok(CheckpointClearReport {
        path: display_path(&path),
        removed,
        warnings,
    })
}

fn read_raw_checkpoint(path: &Path) -> Result<Map<String, Value>> {
    if !path.exists() {
        return Ok(Map::new());
    }
    let text = fs::read_to_string(path)
        .with_context(|| format!("failed to read checkpoint {}", path.display()))?;
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(map)) => Ok(map),
        Ok(_) => Ok(Map::new()),
        Err(_) => {
            let corrupt = corrupt_path(path);
            let _ = fs::copy(path, corrupt);
            Ok(Map::new())
        }
    }
}

fn checkpoint_age_warnings(path: &Path, topic: &str, max_age_hours: f64) -> Result<Vec<String>> {
    let modified = fs::metadata(path)
        .with_context(|| format!("failed to stat checkpoint {}", path.display()))?
        .modified()
 .context("checkpoint modified time unavailable")?;
    let age_h = SystemTime::now()
        .duration_since(modified)
        .unwrap_or_default()
        .as_secs_f64()
        / 3600.0;
    let mut warnings = Vec::new();
    if age_h > 48.0 {
        warnings.push(format!(
            "[Checkpoint] '{topic}' checkpoint is {age_h:.1}h old — resuming anyway. Call checkpoint.clear() to start fresh."
        ));
    } else if max_age_hours > 0.0 && age_h > max_age_hours {
        warnings.push(format!(
            "[Checkpoint] '{topic}' checkpoint is {age_h:.1}h old (configured threshold: {max_age_hours}h) — resuming anyway."
        ));
    }
    Ok(warnings)
}

fn safe_filename(name: &str) -> String {
    let mut safe = name
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' {
                ch
            } else {
                '_'
            }
        })
        .collect::<String>();
    safe = safe.trim_start_matches(['.', '_']).to_string();
    if safe.is_empty() {
        safe = "_".to_string();
    }
    safe.chars().take(80).collect()
}

fn display_path(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

fn bak_path(path: &Path) -> PathBuf {
    path.with_extension("bak")
}

fn tmp_path(path: &Path) -> PathBuf {
    let ext = path.extension().and_then(|ext| ext.to_str()).unwrap_or("");
    path.with_extension(format!("{ext}.tmp"))
}

fn corrupt_path(path: &Path) -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("checkpoint.json");
    path.with_file_name(format!("{file_name}.corrupt.{stamp}"))
}

fn clear_candidates(path: &Path) -> Result<Vec<PathBuf>> {
    let mut candidates = vec![path.to_path_buf(), bak_path(path), tmp_path(path)];
    let Some(parent) = path.parent() else {
        return Ok(candidates);
    };
    let Some(file_name) = path.file_name().and_then(|name| name.to_str()) else {
        return Ok(candidates);
    };
    if parent.exists() {
        for entry in fs::read_dir(parent)
            .with_context(|| format!("failed to scan checkpoint directory {}", parent.display()))?
        {
            let entry = entry?;
            let candidate = entry.path();
            let Some(candidate_name) = candidate.file_name().and_then(|name| name.to_str()) else {
                continue;
            };
            if candidate_name.starts_with(&format!("{file_name}.corrupt."))
                && !candidates.contains(&candidate)
            {
                candidates.push(candidate);
            }
        }
    }
    Ok(candidates)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checkpoint_path_sanitizes_topic() {
        let path = checkpoint_path(Path::new("checkpoints"), "Hello World / Test?");
        assert_eq!(display_path(&path), "checkpoints/Hello_World___Test_.json");
    }

    #[test]
    fn save_accumulates_steps_and_creates_backup() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let first = serde_json::json!({"done": true});
        let second = serde_json::json!({"path": "out.mp4"});

        let first_report = save_checkpoint(temp.path(), "topic", "step_1", first)?;
        assert!(!first_report.backed_up);
        let second_report = save_checkpoint(temp.path(), "topic", "step_2", second)?;
        assert!(second_report.backed_up);

        let read = get_checkpoint(temp.path(), "topic", 0.0)?;
        let data = read.data.expect("checkpoint should be found");
        assert_eq!(data["step_1"]["done"], true);
        assert_eq!(data["step_2"]["path"], "out.mp4");
        assert!(data["step_1"]["ts"].is_string());
        assert!(temp.path().join("topic.bak").exists());
        Ok(())
    }

    #[test]
    fn corrupt_checkpoint_is_backed_up_before_save() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let path = checkpoint_path(temp.path(), "topic");
        fs::write(&path, "not json")?;

        save_checkpoint(
            temp.path(),
            "topic",
            "step",
            serde_json::json!({"ok": true}),
        )?;

        let corrupt_files = fs::read_dir(temp.path())?
            .filter_map(|entry| entry.ok())
            .filter(|entry| entry.file_name().to_string_lossy().contains(".corrupt."))
            .count();
        assert_eq!(corrupt_files, 1);
        Ok(())
    }

    #[test]
    fn clear_removes_checkpoint_siblings_only() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let path = checkpoint_path(temp.path(), "topic");
        fs::write(&path, "{}")?;
        fs::write(bak_path(&path), "{}")?;
        fs::write(tmp_path(&path), "{}")?;
        fs::write(temp.path().join("topic.json.corrupt.1"), "bad")?;
        fs::write(temp.path().join("unrelated.json"), "{}")?;

        let report = clear_checkpoint(temp.path(), "topic")?;

        assert_eq!(report.removed.len(), 4);
        assert!(!path.exists());
        assert!(temp.path().join("unrelated.json").exists());
        Ok(())
    }
}
