use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command as StdCommand, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};

use super::{
    add_artifact_path, append_event_path, get_job_path, get_job_status_path,
    join_thread_with_timeout, now_iso, safe_filename, send_interrupt, spawn_heartbeat_thread,
    spawn_stream_thread, update_job_path, JobUpdate, Worker, CANCEL_WAIT_SECONDS, STATUS_CANCELED,
    STATUS_CANCEL_REQUESTED,
};

const RUST_ASSEMBLY_ENV: &str = "VIDEOAI_RUST_ASSEMBLY";
const FFMPEG_BIN_ENV: &str = "VIDEOAI_FFMPEG_BIN";
const DEFAULT_FFMPEG_BIN: &str = "ffmpeg";
const ASSEMBLY_STEP_TIMEOUT_SECONDS: u64 = 1_800;

pub(super) fn run_if_enabled(worker: &Worker, job_id: i64) -> Result<()> {
    if !rust_assembly_enabled() {
        return Ok(());
    }

    let job = get_job_path(&worker.config.db_path, job_id)?
        .context("job disappeared before Rust assembly")?;
    let topic_raw = job.topic.unwrap_or_else(|| "unknown".to_string());
    let run_dir = worker
        .config
        .repo_root
        .join("studio_outputs")
        .join(safe_filename(&topic_raw));

    if !run_dir.is_dir() {
        append_event_path(
            &worker.config.db_path,
            job_id,
            &format!(
                "rust_assembly_skipped: run directory not found: {}",
                run_dir.display()
            ),
            "system",
        )?;
        return Ok(());
    }

    append_event_path(
        &worker.config.db_path,
        job_id,
        "rust_assembly_started",
        "system",
    )?;

    let current_exe = std::env::current_exe().context("failed to resolve current worker binary")?;
    let ffmpeg_bin = ffmpeg_bin();
    let rust_video = run_dir.join("rust_final_video.mp4");
    let rust_thumbnail = run_dir.join("rust_thumbnail.png");

    run_optional_step(
        worker,
        job_id,
        "rust_assets_inspect",
        vec![
            current_exe.clone(),
            PathBuf::from("assets"),
            PathBuf::from("inspect"),
            PathBuf::from("--run-dir"),
            run_dir.clone(),
        ],
    )?;

    run_optional_step(
        worker,
        job_id,
        "rust_ffmpeg_concat",
        vec![
            current_exe.clone(),
            PathBuf::from("ffmpeg"),
            PathBuf::from("concat"),
            PathBuf::from("--run-dir"),
            run_dir.clone(),
            PathBuf::from("--out"),
            rust_video.clone(),
            PathBuf::from("--ffmpeg-bin"),
            ffmpeg_bin.clone(),
        ],
    )?;

    if rust_video.is_file() {
        add_artifact_path(
            &worker.config.db_path,
            job_id,
            "rust_output_video",
            &rust_video.to_string_lossy(),
            None,
        )?;
        append_event_path(
            &worker.config.db_path,
            job_id,
            "rust_output_video: rust_final_video.mp4",
            "artifact",
        )?;
    }

    let thumbnail_input = if rust_video.is_file() {
        Some(rust_video.clone())
    } else {
        latest_mp4_in(&run_dir)?
    };

    if let Some(video) = thumbnail_input {
        run_optional_step(
            worker,
            job_id,
            "rust_ffmpeg_thumbnail",
            vec![
                current_exe.clone(),
                PathBuf::from("ffmpeg"),
                PathBuf::from("thumbnail"),
                PathBuf::from("--video"),
                video,
                PathBuf::from("--out"),
                rust_thumbnail.clone(),
                PathBuf::from("--ffmpeg-bin"),
                ffmpeg_bin,
            ],
        )?;

        if rust_thumbnail.is_file() {
            add_artifact_path(
                &worker.config.db_path,
                job_id,
                "rust_thumbnail",
                &rust_thumbnail.to_string_lossy(),
                None,
            )?;
            append_event_path(
                &worker.config.db_path,
                job_id,
                "rust_thumbnail: rust_thumbnail.png",
                "artifact",
            )?;
        }
    } else {
        append_event_path(
            &worker.config.db_path,
            job_id,
            "rust_ffmpeg_thumbnail_skipped: no MP4 input found",
            "system",
        )?;
    }

    run_optional_step(
        worker,
        job_id,
        "rust_assets_validate",
        vec![
            current_exe,
            PathBuf::from("assets"),
            PathBuf::from("validate"),
            PathBuf::from("--run-dir"),
            run_dir,
        ],
    )?;

    append_event_path(
        &worker.config.db_path,
        job_id,
        "rust_assembly_finished",
        "system",
    )?;

    Ok(())
}

fn rust_assembly_enabled() -> bool {
    match std::env::var(RUST_ASSEMBLY_ENV) {
        Ok(value) => value == "1",
        Err(_) => false,
    }
}

fn ffmpeg_bin() -> PathBuf {
    match std::env::var(FFMPEG_BIN_ENV) {
        Ok(value) if !value.is_empty() => PathBuf::from(value),
        _ => PathBuf::from(DEFAULT_FFMPEG_BIN),
    }
}

fn run_optional_step(worker: &Worker, job_id: i64, label: &str, argv: Vec<PathBuf>) -> Result<()> {
    match run_supervised_step(worker, job_id, label, &argv) {
        Ok(()) => {
            append_event_path(
                &worker.config.db_path,
                job_id,
                &format!("{label}_succeeded"),
                "system",
            )?;
            Ok(())
        }
        Err(err) => {
            if get_job_status_path(&worker.config.db_path, job_id)?.as_deref()
                == Some(STATUS_CANCELED)
            {
                return Err(err);
            }
            append_event_path(
                &worker.config.db_path,
                job_id,
                &format!("{label}_failed: {err}"),
                "system",
            )?;
            Ok(())
        }
    }
}

fn run_supervised_step(worker: &Worker, job_id: i64, label: &str, argv: &[PathBuf]) -> Result<()> {
    let (program, args) = argv
        .split_first()
        .with_context(|| format!("{label}: empty command"))?;

    append_event_path(
        &worker.config.db_path,
        job_id,
        &format!("{label}_starting: {}", format_command(argv)),
        "system",
    )?;

    let mut command = StdCommand::new(program);
    command
        .args(args)
        .current_dir(&worker.config.repo_root)
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
        .with_context(|| format!("{label}: failed to spawn subprocess"))?;
    let child_id = child.id();

    let stop = Arc::new(AtomicBool::new(false));
    let heartbeat =
        spawn_heartbeat_thread(worker.config.db_path.clone(), job_id, Arc::clone(&stop));

    let mut stream_threads = Vec::new();
    if let Some(stdout) = child.stdout.take() {
        stream_threads.push(spawn_stream_thread(
            stdout,
            worker.config.db_path.clone(),
            job_id,
        ));
    }
    if let Some(stderr) = child.stderr.take() {
        stream_threads.push(spawn_stream_thread(
            stderr,
            worker.config.db_path.clone(),
            job_id,
        ));
    }

    let deadline = Instant::now() + Duration::from_secs(ASSEMBLY_STEP_TIMEOUT_SECONDS);
    let mut canceled = false;
    let mut timed_out = false;

    while child
        .try_wait()
        .with_context(|| format!("{label}: failed to poll subprocess"))?
        .is_none()
    {
        if get_job_status_path(&worker.config.db_path, job_id)?.as_deref()
            == Some(STATUS_CANCEL_REQUESTED)
        {
            canceled = true;
            append_event_path(
                &worker.config.db_path,
                job_id,
                &format!("{label}_cancellation_requested"),
                "system",
            )?;
            terminate_child_group(&mut child, child_id, label)?;
            update_job_path(
                &worker.config.db_path,
                job_id,
                &[JobUpdate::Status(STATUS_CANCELED)],
            )?;
            break;
        }

        if Instant::now() >= deadline {
            timed_out = true;
            append_event_path(
                &worker.config.db_path,
                job_id,
                &format!("{label}_timeout"),
                "system",
            )?;
            terminate_child_group(&mut child, child_id, label)?;
            break;
        }

        thread::sleep(Duration::from_secs(1));
    }

    stop.store(true, Ordering::SeqCst);
    join_thread_with_timeout(heartbeat, Duration::from_secs(2));
    for handle in stream_threads {
        join_thread_with_timeout(handle, Duration::from_secs(2));
    }

    if canceled {
        bail!("{label}: canceled");
    }
    if timed_out {
        bail!("{label}: timed out");
    }

    let status = child
        .wait()
        .with_context(|| format!("{label}: failed to wait for subprocess"))?;
    let rc = status.code().unwrap_or(-1);
    if rc != 0 {
        bail!("{label}: exit_code:{rc}");
    }

    let heartbeat_at = now_iso();
    update_job_path(
        &worker.config.db_path,
        job_id,
        &[JobUpdate::Heartbeat(&heartbeat_at)],
    )?;

    Ok(())
}

fn terminate_child_group(
    child: &mut std::process::Child,
    child_id: u32,
    label: &str,
) -> Result<()> {
    send_interrupt(child_id);
    let deadline = Instant::now() + Duration::from_secs(CANCEL_WAIT_SECONDS);
    while child
        .try_wait()
        .with_context(|| format!("{label}: failed to poll subprocess during termination"))?
        .is_none()
        && Instant::now() < deadline
    {
        thread::sleep(Duration::from_secs(1));
    }
    if child
        .try_wait()
        .with_context(|| format!("{label}: failed to poll subprocess after interrupt"))?
        .is_none()
    {
        let _ = child.kill();
    }
    let _ = child.wait();
    Ok(())
}

fn latest_mp4_in(dir: &Path) -> Result<Option<PathBuf>> {
    let mut latest: Option<(PathBuf, std::time::SystemTime)> = None;
    for entry in fs::read_dir(dir)
        .with_context(|| format!("failed to read output directory {}", dir.display()))?
    {
        let entry = entry.context("failed to read output directory entry")?;
        let path = entry.path();
        if path.extension().and_then(|ext| ext.to_str()) != Some("mp4") {
            continue;
        }
        let modified = entry
            .metadata()
            .and_then(|metadata| metadata.modified())
            .with_context(|| format!("failed to stat output video {}", path.display()))?;
        if latest
            .as_ref()
            .map(|(_, current)| modified > *current)
            .unwrap_or(true)
        {
            latest = Some((path, modified));
        }
    }
    Ok(latest.map(|(path, _)| path))
}

fn format_command(argv: &[PathBuf]) -> String {
    argv.iter()
        .map(|part| part.to_string_lossy())
        .collect::<Vec<_>>()
        .join(" ")
}
