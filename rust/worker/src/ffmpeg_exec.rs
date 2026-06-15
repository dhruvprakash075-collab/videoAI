use std::fs;
use std::path::{Path, PathBuf};
use std::process::Stdio;

use anyhow::{bail, Context, Result};
use tokio::process::Command;

use crate::ffmpeg_plan::{
    concat_list_content, discover_segments, display_path, loudnorm_apply_argv,
    options_from_concat_args, thumbnail_argv, FfmpegConcatArgs, FfmpegThumbnailArgs, LoudnormStats,
};

const STDERR_TAIL_BYTES: usize = 4_000;

pub fn run_concat(args: FfmpegConcatArgs) -> Result<()> {
    tokio::runtime::Runtime::new()
        .context("failed to create tokio runtime")?
        .block_on(run_concat_async(args))
}

async fn run_concat_async(args: FfmpegConcatArgs) -> Result<()> {
    let suffix = chrono::Utc::now()
        .timestamp_nanos_opt()
        .map(|value| value.to_string())
        .unwrap_or_else(|| chrono::Utc::now().timestamp_millis().to_string());
    let temp_dir = args
        .run_dir
        .join(".rust_tmp")
        .join(format!("ffmpeg-concat-{suffix}"));
    let guard = TempDirGuard::create(temp_dir.clone())?;
    let options = options_from_concat_args(&args, temp_dir);

    if let Some(parent) = options.output.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create output directory {}", parent.display()))?;
    }

    let segments = discover_segments(&options.run_dir)?;
    if segments.is_empty() {
        bail!(
            "no segment MP4 files found under {}",
            options.run_dir.display()
        );
    }

    write_concat_list(&options.concat_list, &segments)?;

    let concat_output = if options.loudnorm {
        options.temp_dir.join("concat_prenorm.mp4")
    } else {
        options.output.clone()
    };

    let concat_argv = crate::ffmpeg_plan::build_plan(&options)?
        .commands
        .into_iter()
        .find(|command| command.phase == "concat")
        .map(|command| command.argv)
        .context("internal error: concat command missing from plan")?;
    run_argv(&concat_argv, 900).await?;

    if options.loudnorm {
        let stats = measure_loudnorm(&options.ffmpeg_bin, &concat_output).await?;
        let apply_argv = loudnorm_apply_argv(&options, &concat_output, &stats);
        run_argv(&apply_argv, 600).await?;
    }

    guard.cleanup()?;
    Ok(())
}

pub fn run_thumbnail(args: FfmpegThumbnailArgs) -> Result<()> {
    tokio::runtime::Runtime::new()
        .context("failed to create tokio runtime")?
        .block_on(run_thumbnail_async(args))
}

async fn run_thumbnail_async(args: FfmpegThumbnailArgs) -> Result<()> {
    if !args.video.is_file() {
        bail!("input video not found: {}", args.video.display());
    }

    if let Some(parent) = args.out.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).with_context(|| {
                format!("failed to create output directory {}", parent.display())
            })?;
        }
    }

    let argv = thumbnail_argv(&args)?;
    run_argv(&argv, 60).await?;

    if !args.out.is_file() {
        bail!(
            "thumbnail generation produced no output at {}",
            args.out.display()
        );
    }

    Ok(())
}

fn write_concat_list(path: &Path, segments: &[PathBuf]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create temp directory {}", parent.display()))?;
    }
    fs::write(path, concat_list_content(segments))
        .with_context(|| format!("failed to write concat list {}", path.display()))?;
    Ok(())
}

async fn measure_loudnorm(ffmpeg_bin: &Path, input: &Path) -> Result<LoudnormStats> {
    let argv = vec![
        display_path(ffmpeg_bin),
        "-y".to_string(),
        "-i".to_string(),
        display_path(input),
        "-af".to_string(),
        crate::ffmpeg_plan::loudnorm_filter(None),
        "-f".to_string(),
        "null".to_string(),
        "-".to_string(),
    ];
    let stderr = run_argv_capture_stderr(&argv, 600).await?;
    parse_loudnorm_json(&stderr)
}

async fn run_argv(argv: &[String], timeout_seconds: u64) -> Result<()> {
    let stderr = run_argv_capture_stderr(argv, timeout_seconds).await?;
    if !stderr.is_empty() {
        eprintln!("{}", stderr_tail(&stderr));
    }
    Ok(())
}

async fn run_argv_capture_stderr(argv: &[String], timeout_seconds: u64) -> Result<String> {
    let (program, args) = argv
        .split_first()
        .context("internal error: empty ffmpeg argv")?;
    let child = Command::new(program)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .with_context(|| format!("failed to spawn {program}"))?;

    let wait = child.wait_with_output();
    let output = tokio::time::timeout(std::time::Duration::from_secs(timeout_seconds), wait)
        .await
        .with_context(|| format!("ffmpeg timeout (> {timeout_seconds}s): {}", argv.join(" ")))??;

    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    if !output.status.success() {
        bail!("ffmpeg failed: {}", stderr_tail(&stderr));
    }
    Ok(stderr)
}

pub fn parse_loudnorm_json(stderr: &str) -> Result<LoudnormStats> {
    let object = find_loudnorm_object(stderr).context("loudnorm JSON block not found in stderr")?;
    let value: serde_json::Value = serde_json::from_str(object)
        .with_context(|| format!("failed to parse loudnorm JSON block: {object}"))?;

    Ok(LoudnormStats {
        input_i: json_string(&value, "input_i")?,
        input_tp: json_string(&value, "input_tp")?,
        input_lra: json_string(&value, "input_lra")?,
        input_thresh: json_string(&value, "input_thresh")?,
        target_offset: json_string(&value, "target_offset")?,
    })
}

fn find_loudnorm_object(stderr: &str) -> Option<&str> {
    let input_i = stderr.find("\"input_i\"")?;
    let before = stderr[..input_i].rfind('{')?;
    let after = stderr[input_i..].find('}')? + input_i + 1;
    stderr.get(before..after)
}

fn json_string(value: &serde_json::Value, key: &str) -> Result<String> {
    value
        .get(key)
        .and_then(serde_json::Value::as_str)
        .map(ToString::to_string)
        .with_context(|| format!("loudnorm JSON missing string field {key}"))
}

fn stderr_tail(stderr: &str) -> String {
    if stderr.len() <= STDERR_TAIL_BYTES {
        return stderr.to_string();
    }
    stderr[stderr.len().saturating_sub(STDERR_TAIL_BYTES)..].to_string()
}

struct TempDirGuard {
    path: PathBuf,
    cleaned: bool,
}

impl TempDirGuard {
    fn create(path: PathBuf) -> Result<Self> {
        fs::create_dir_all(&path)
            .with_context(|| format!("failed to create temp directory {}", path.display()))?;
        Ok(Self {
            path,
            cleaned: false,
        })
    }

    fn cleanup(mut self) -> Result<()> {
        fs::remove_dir_all(&self.path)
            .with_context(|| format!("failed to remove temp directory {}", self.path.display()))?;
        self.cleaned = true;
        Ok(())
    }
}

impl Drop for TempDirGuard {
    fn drop(&mut self) {
        if !self.cleaned {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_loudnorm_json_reads_real_stderr_shape() -> Result<()> {
        let stderr = r#"
[Parsed_loudnorm_0 @ 000001]
{
        "input_i" : "-22.13",
        "input_tp" : "-3.41",
        "input_lra" : "7.80",
        "input_thresh" : "-32.77",
        "output_i" : "-14.07",
        "output_tp" : "-1.49",
        "output_lra" : "8.10",
        "output_thresh" : "-24.99",
        "normalization_type" : "dynamic",
        "target_offset" : "0.07"
}
"#;

        let stats = parse_loudnorm_json(stderr)?;

        assert_eq!(stats.input_i, "-22.13");
        assert_eq!(stats.input_tp, "-3.41");
        assert_eq!(stats.input_lra, "7.80");
        assert_eq!(stats.input_thresh, "-32.77");
        assert_eq!(stats.target_offset, "0.07");
        Ok(())
    }

    #[test]
    fn parse_loudnorm_json_errors_without_block() {
        let err =
            parse_loudnorm_json("no json here").expect_err("missing loudnorm JSON should fail");
        assert!(err.to_string().contains("loudnorm JSON block not found"));
    }

    #[test]
    fn run_thumbnail_errors_when_video_missing() {
        let args = FfmpegThumbnailArgs {
            video: PathBuf::from("does/not/exist.mp4"),
            out: PathBuf::from("thumbnail.png"),
            at: 0.0,
            size: "1280x720".to_string(),
            ffmpeg_bin: PathBuf::from("ffmpeg"),
        };

        let err = run_thumbnail(args).expect_err("missing input video should error");
        assert!(err.to_string().contains("input video not found"));
    }

    #[cfg(feature = "ffmpeg-integration")]
    #[test]
    fn thumbnail_generates_non_empty_png_with_real_ffmpeg() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let video = temp.path().join("input.mp4");
        let thumb = temp.path().join("thumbnail.png");

        let fixture = std::process::Command::new("ffmpeg")
            .args([
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=1:size=320x240:rate=1",
                video.to_string_lossy().as_ref(),
            ])
            .output()
            .context("failed to spawn ffmpeg for fixture clip")?;
        assert!(fixture.status.success(), "fixture ffmpeg invocation failed");

        run_thumbnail(FfmpegThumbnailArgs {
            video: video.clone(),
            out: thumb.clone(),
            at: 0.0,
            size: "1280x720".to_string(),
            ffmpeg_bin: PathBuf::from("ffmpeg"),
        })?;

        let metadata = std::fs::metadata(&thumb)?;
        assert!(metadata.len() > 0, "thumbnail PNG should be non-empty");
        Ok(())
    }
}
