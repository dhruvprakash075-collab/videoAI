use std::path::{Path, PathBuf};
use std::process::Stdio;

use anyhow::{bail, Context, Result};
use clap::{Args, Subcommand};
use serde::Serialize;
use serde_json::Value;
use tokio::process::Command;

const EXIT_VALIDATION_FAILURE: i32 = 2;
const MIN_SIZE_MB: f64 = 0.1;
const DURATION_TOLERANCE_RATIO: f64 = 0.20;
const DRIFT_WARNING_SECONDS: f64 = 0.2;
const FPS_TOLERANCE: f64 = 0.1;
const FFPROBE_TIMEOUT_SECONDS: u64 = 30;

#[derive(Debug, Subcommand)]
pub enum MediaCommand {
    /// Inspect media health with ffprobe and emit a structured QC report.
    Inspect(MediaInspectArgs),
}

#[derive(Clone, Debug, Args)]
pub struct MediaInspectArgs {
    /// Media file to inspect.
    #[arg(long)]
    pub input: PathBuf,

    /// Emit machine-readable JSON. Accepted for consistency with other worker subcommands.
    #[arg(long)]
    pub json: bool,

    /// Expect a 1080x1920 portrait render.
    #[arg(long)]
    pub expect_portrait: bool,

    /// Expect a 1920x1080 landscape render.
    #[arg(long)]
    pub expect_landscape: bool,

    /// Expected duration in seconds. Ignored when --requested-duration is set.
    #[arg(long)]
    pub expected_duration: Option<f64>,

    /// User-requested duration in seconds. Takes precedence over --expected-duration.
    #[arg(long)]
    pub requested_duration: Option<f64>,

    /// ffprobe executable path.
    #[arg(long, default_value = "ffprobe")]
    pub ffprobe_bin: PathBuf,
}

#[derive(Clone, Debug, Default)]
struct MediaInspectConfig {
    expect_portrait: bool,
    expect_landscape: bool,
    expected_duration: Option<f64>,
    requested_duration: Option<f64>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct MediaInspectReport {
    pub passed: bool,
    pub input: String,
    pub size_mb: Option<f64>,
    pub resolution: Option<Resolution>,
    pub fps: Option<f64>,
    pub codecs: Codecs,
    pub duration_s: Option<f64>,
    pub audio: AudioReport,
    pub drift_s: Option<f64>,
    pub issues: Vec<String>,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct Resolution {
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Debug, Default, Serialize, PartialEq, Eq)]
pub struct Codecs {
    pub video: Option<String>,
    pub audio: Option<String>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct AudioReport {
    pub present: bool,
    pub sample_rate: Option<u32>,
    pub channels: Option<u32>,
}

pub fn run_command(command: MediaCommand) -> Result<()> {
    match command {
        MediaCommand::Inspect(args) => {
            let report = inspect_path(&args)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
            if !report.passed {
                std::process::exit(EXIT_VALIDATION_FAILURE);
            }
        }
    }
    Ok(())
}

pub fn inspect_path(args: &MediaInspectArgs) -> Result<MediaInspectReport> {
    if args.expect_portrait && args.expect_landscape {
        bail!("--expect-portrait and --expect-landscape are mutually exclusive");
    }

    let input = &args.input;
    let metadata = input
        .metadata()
        .with_context(|| format!("input media not found or unreadable: {}", input.display()))?;
    let file_size_bytes = metadata.len();

    let probe = tokio::runtime::Runtime::new()
        .context("failed to create tokio runtime")?
        .block_on(run_ffprobe(&args.ffprobe_bin, input))?;

    let config = MediaInspectConfig {
        expect_portrait: args.expect_portrait,
        expect_landscape: args.expect_landscape,
        expected_duration: args.expected_duration,
        requested_duration: args.requested_duration,
    };

    inspect_probe(
        &probe,
        Some(file_size_bytes),
        &config,
        &input.to_string_lossy(),
    )
}

async fn run_ffprobe(ffprobe_bin: &Path, input: &Path) -> Result<Value> {
    let child = Command::new(ffprobe_bin)
        .args([
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate:stream=width,height,avg_frame_rate,codec_name,codec_type,sample_rate,channels,duration",
            "-of",
            "json",
        ])
        .arg(input)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .with_context(|| format!("failed to spawn {}", ffprobe_bin.display()))?;

    let wait = child.wait_with_output();
    let output = tokio::time::timeout(std::time::Duration::from_secs(FFPROBE_TIMEOUT_SECONDS), wait)
        .await
        .with_context(|| format!("ffprobe timeout (> {FFPROBE_TIMEOUT_SECONDS}s)"))??;

    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if !output.status.success() {
        bail!("ffprobe error: {}", stderr_tail(&stderr));
    }

    serde_json::from_slice(&output.stdout).context("ffprobe output invalid JSON")
}

fn inspect_probe(
    probe: &Value,
    file_size_bytes: Option<u64>,
    config: &MediaInspectConfig,
    input: &str,
) -> Result<MediaInspectReport> {
    let mut issues = Vec::new();
    let mut warnings = Vec::new();

    let size_mb = file_size_bytes.map(|bytes| bytes as f64 / (1024.0 * 1024.0));
    if let Some(size) = size_mb {
        if size < MIN_SIZE_MB {
            issues.push(format!("File too small: {size:.2}MB"));
        }
    }

    let streams = probe
        .get("streams")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let video_stream = streams
        .iter()
        .find(|stream| stream.get("codec_type").and_then(Value::as_str) == Some("video"));
    let audio_stream = streams
        .iter()
        .find(|stream| stream.get("codec_type").and_then(Value::as_str) == Some("audio"));

    if video_stream.is_none() {
        issues.push("No video stream found".to_string());
    }
    if audio_stream.is_none() {
        issues.push("No audio stream found".to_string());
    }

    let format = probe.get("format").unwrap_or(&Value::Null);
    let duration_s = parse_f64(format.get("duration"));
    if format.get("duration").is_some() && duration_s.is_none() {
        issues.push(
            "Could not read video duration (ffprobe returned N/A or invalid value)".to_string(),
        );
    }

    if let Some(expected_s) = config
        .requested_duration
        .filter(|value| *value > 0.0)
        .or_else(|| config.expected_duration.filter(|value| *value > 0.0))
    {
        if let Some(duration) = duration_s.filter(|value| *value > 0.0) {
            let tolerance = expected_s * DURATION_TOLERANCE_RATIO;
            if (duration - expected_s).abs() > tolerance {
                issues.push(format!(
                    "Duration mismatch: {:.0}s vs expected {:.0}s",
                    duration, expected_s
                ));
            }
        }
    }

    let resolution = video_stream.and_then(|stream| {
        let width = parse_u32(stream.get("width"))?;
        let height = parse_u32(stream.get("height"))?;
        Some(Resolution { width, height })
    });

    if let Some(expected) = expected_resolution(config) {
        match resolution.as_ref() {
            Some(actual) if (actual.width, actual.height) != expected => {
                issues.push(format!(
                    "Resolution: {}x{} vs expected {}x{}",
                    actual.width, actual.height, expected.0, expected.1
                ));
            }
            _ => {}
        }
    } else if let Some(actual) = resolution.as_ref() {
        if !is_standard_resolution(actual.width, actual.height) {
            warnings.push("Non-standard canvas resolution detected".to_string());
        }
    }

    let fps = video_stream
        .and_then(|stream| stream.get("avg_frame_rate").and_then(Value::as_str))
        .and_then(parse_frame_rate);
    if let Some(value) = fps {
        if (value - 24.0).abs() > FPS_TOLERANCE && (value - 12.0).abs() > FPS_TOLERANCE {
            warnings.push(
                "Framerate differs from target classic 24fps or zoompan 12fps".to_string(),
            );
        }
    }

    let audio_duration = audio_stream.and_then(|stream| parse_f64(stream.get("duration")));
    let drift_s = match (duration_s, audio_duration) {
        (Some(format_duration), Some(audio_duration)) => {
            Some((format_duration - audio_duration).abs())
        }
        _ => None,
    };
    if drift_s.is_some_and(|drift| drift > DRIFT_WARNING_SECONDS) {
        warnings.push(format!(
            "Audio-video drift detected: {:.2}s",
            drift_s.unwrap_or_default()
        ));
    }

    let codecs = Codecs {
        video: video_stream
            .and_then(|stream| stream.get("codec_name").and_then(Value::as_str))
            .map(ToString::to_string),
        audio: audio_stream
            .and_then(|stream| stream.get("codec_name").and_then(Value::as_str))
            .map(ToString::to_string),
    };

    let audio = AudioReport {
        present: audio_stream.is_some(),
        sample_rate: audio_stream.and_then(|stream| parse_u32(stream.get("sample_rate"))),
        channels: audio_stream.and_then(|stream| parse_u32(stream.get("channels"))),
    };

    Ok(MediaInspectReport {
        passed: issues.is_empty(),
        input: input.replace('\\', "/"),
        size_mb: size_mb.map(round_2),
        resolution,
        fps: fps.map(round_2),
        codecs,
        duration_s: duration_s.map(round_2),
        audio,
        drift_s: drift_s.map(round_2),
        issues,
        warnings,
    })
}

fn expected_resolution(config: &MediaInspectConfig) -> Option<(u32, u32)> {
    if config.expect_portrait {
        Some((1080, 1920))
    } else if config.expect_landscape {
        Some((1920, 1080))
    } else {
        None
    }
}

fn is_standard_resolution(width: u32, height: u32) -> bool {
    (width == 1920 && height == 1080) || (width == 1080 && height == 1920)
}

fn parse_f64(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(number)) => number.as_f64(),
        Some(Value::String(text)) => text.parse::<f64>().ok(),
        _ => None,
    }
}

fn parse_u32(value: Option<&Value>) -> Option<u32> {
    match value {
        Some(Value::Number(number)) => number.as_u64().and_then(|value| u32::try_from(value).ok()),
        Some(Value::String(text)) => text.parse::<u32>().ok(),
        _ => None,
    }
}

fn parse_frame_rate(value: &str) -> Option<f64> {
    let (num, den) = value.split_once('/')?;
    let num = num.parse::<f64>().ok()?;
    let den = den.parse::<f64>().ok()?;
    if den == 0.0 {
        return None;
    }
    Some(num / den)
}

fn round_2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn stderr_tail(stderr: &str) -> String {
    const TAIL_BYTES: usize = 4_000;
    if stderr.len() <= TAIL_BYTES {
        return stderr.to_string();
    }
    stderr[stderr.len().saturating_sub(TAIL_BYTES)..].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn base_probe(width: u32, height: u32, fps: &str, audio_duration: &str) -> Value {
        json!({
            "format": {
                "duration": "10.0",
                "size": "1048576",
                "bit_rate": "838860"
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": width,
                    "height": height,
                    "avg_frame_rate": fps
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                    "duration": audio_duration
                }
            ]
        })
    }

    #[test]
    fn inspect_passes_for_expected_landscape() -> Result<()> {
        let config = MediaInspectConfig {
            expect_landscape: true,
            expected_duration: Some(10.0),
            ..MediaInspectConfig::default()
        };

        let report = inspect_probe(
            &base_probe(1920, 1080, "24/1", "10.0"),
            Some(1024 * 1024),
            &config,
            "out.mp4",
        )?;

        assert!(report.passed);
        assert!(report.issues.is_empty());
        assert!(report.warnings.is_empty());
        assert_eq!(
            report.resolution,
            Some(Resolution {
                width: 1920,
                height: 1080
            })
        );
        assert_eq!(report.audio.sample_rate, Some(48000));
        Ok(())
    }

    #[test]
    fn inspect_passes_for_expected_portrait() -> Result<()> {
        let config = MediaInspectConfig {
            expect_portrait: true,
            ..MediaInspectConfig::default()
        };

        let report = inspect_probe(
            &base_probe(1080, 1920, "24/1", "10.0"),
            Some(1024 * 1024),
            &config,
            "out.mp4",
        )?;

        assert!(report.passed);
        assert!(report.issues.is_empty());
        Ok(())
    }

    #[test]
    fn inspect_records_missing_audio_as_issue() -> Result<()> {
        let probe = json!({
            "format": { "duration": "10.0" },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24/1"
                }
            ]
        });

        let report = inspect_probe(
            &probe,
            Some(1024 * 1024),
            &MediaInspectConfig::default(),
            "out.mp4",
        )?;

        assert!(!report.passed);
        assert!(report
            .issues
            .iter()
            .any(|issue| issue == "No audio stream found"));
        assert!(!report.audio.present);
        Ok(())
    }

    #[test]
    fn inspect_uses_requested_duration_before_expected_duration() -> Result<()> {
        let config = MediaInspectConfig {
            expected_duration: Some(10.0),
            requested_duration: Some(30.0),
            ..MediaInspectConfig::default()
        };

        let report = inspect_probe(
            &base_probe(1920, 1080, "24/1", "10.0"),
            Some(1024 * 1024),
            &config,
            "out.mp4",
        )?;

        assert!(!report.passed);
        assert!(report
            .issues
            .iter()
            .any(|issue| issue.contains("Duration mismatch: 10s vs expected 30s")));
        Ok(())
    }

    #[test]
    fn inspect_warns_for_drift_nonstandard_resolution_and_fps() -> Result<()> {
        let report = inspect_probe(
            &base_probe(1000, 1000, "30/1", "9.5"),
            Some(1024 * 1024),
            &MediaInspectConfig::default(),
            "out.mp4",
        )?;

        assert!(report.passed);
        assert_eq!(report.warnings.len(), 3);
        assert!(report
            .warnings
            .iter()
            .any(|warning| warning.contains("Non-standard")));
        assert!(report
            .warnings
            .iter()
            .any(|warning| warning.contains("Framerate")));
        assert!(report
            .warnings
            .iter()
            .any(|warning| warning.contains("drift")));
        assert_eq!(report.drift_s, Some(0.5));
        Ok(())
    }

    #[test]
    fn inspect_fails_for_tiny_file_and_resolution_mismatch() -> Result<()> {
        let config = MediaInspectConfig {
            expect_landscape: true,
            ..MediaInspectConfig::default()
        };

        let report = inspect_probe(
            &base_probe(1080, 1920, "24/1", "10.0"),
            Some(1024),
            &config,
            "out.mp4",
        )?;

        assert!(!report.passed);
        assert!(report
            .issues
            .iter()
            .any(|issue| issue.contains("File too small")));
        assert!(report
            .issues
            .iter()
            .any(|issue| issue.contains("Resolution")));
        Ok(())
    }
}
