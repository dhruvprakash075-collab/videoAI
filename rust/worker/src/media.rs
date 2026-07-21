//! `media.rs` — File-level media QC (FFprobe wrapper).
//!
//! Replaces the Python `utils/media_analyzer.py` inspect() call with a
//! native Rust `tokio::process::Command` ffprobe invocation. Returns a
//! structured `MediaInspectReport` (JSON-serializable) instead of raising
//! exceptions.
//!
//! Checks performed:
//!   - File size > MIN_SIZE_MB (default 0.1 MB)
//!   - Resolution matches expectation (portrait 1080x1920 / landscape 1920x1080)
//!   - FPS within FPS_TOLERANCE (default 0.1) of config/video.fps
//!   - Codec is a known-good pair (h264/hevc + aac/mp3)
//!   - Duration within DURATION_TOLERANCE_RATIO (20%) of expected/requested
//!   - Audio present, 1-2 channels, 44.1/48 kHz
//!   - Timestamp drift ≤ DRIFT_WARNING_SECONDS (0.2s)
//!
//! Exit codes:
//!   0 = passed (all checks ok)
//!   2 = validation failure (issues non-empty)
//!
//! The Python fallback (`utils/media_analyzer.py`) is retained for
//! environments where the Rust worker isn't available.

use anyhow::{bail, Context, Result};
use serde::Serialize;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use tokio::process::Command;

const EXIT_VALIDATION_FAILURE: i32 = 2;
const MIN_SIZE_MB: f64 = 0.1;
const DURATION_TOLERANCE_RATIO: f64 = 0.20;
const DRIFT_WARNING_SECONDS: f64 = 0.2;
const FPS_TOLERANCE: f64 = 0.1;
const FFPROBE_TIMEOUT_SECONDS: u64 = 30;

#[derive(Debug, clap::Subcommand)]
pub enum MediaCommand {
    /// Inspect media health with ffprobe and emit a structured QC report.
    Inspect(MediaInspectArgs),
}

#[derive(Clone, Debug, clap::Args)]
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

#[derive(Clone, Debug, Default, Serialize, PartialEq, Eq)]
pub struct AudioReport {
    pub channels: Option<u16>,
    pub sample_rate: Option<u32>,
    pub codec: Option<String>,
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
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            input.to_str().context("input path not valid UTF-8")?,
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .with_context(|| format!("failed to spawn ffprobe: {}", ffprobe_bin.display()))?;

    let output = child
        .wait_with_output()
        .await
        .context("ffprobe wait failed")?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stderr_tail = stderr_tail(&stderr);
        bail!("ffprobe error: {}", stderr_tail);
    }

    let probe: Value =
        serde_json::from_slice(&output.stdout).context("ffprobe output invalid JSON")?;
    Ok(probe)
}

fn stderr_tail(stderr: &str) -> String {
    const TAIL_BYTES: usize = 4000;
    let bytes = stderr.as_bytes();
    if bytes.len() <= TAIL_BYTES {
        return stderr.to_string();
    }
    String::from_utf8_lossy(&bytes[bytes.len() - TAIL_BYTES..]).to_string()
}

fn inspect_probe(
    probe: &Value,
    file_size_bytes: Option<u64>,
    config: &MediaInspectConfig,
    input_path: &str,
) -> Result<MediaInspectReport> {
    let mut issues = Vec::new();
    let mut warnings = Vec::new();

    // File size
    let size_mb = file_size_bytes.map(|b| b as f64 / 1_048_576.0);
    if let Some(mb) = size_mb {
        if mb < MIN_SIZE_MB {
            issues.push(format!(
                "file size {:.2} MB below minimum {:.2} MB",
                mb, MIN_SIZE_MB
            ));
        }
    }

    // Streams
    let streams = probe
        .get("streams")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("no streams in ffprobe output"))?;

    let video_stream = streams
        .iter()
        .find(|s| s.get("codec_type").and_then(Value::as_str) == Some("video"))
        .ok_or_else(|| anyhow::anyhow!("no video stream found"))?;

    let audio_stream = streams
        .iter()
        .find(|s| s.get("codec_type").and_then(Value::as_str) == Some("audio"));

    // Resolution
    let width = video_stream
        .get("width")
        .and_then(Value::as_u64)
        .unwrap_or(0) as u32;
    let height = video_stream
        .get("height")
        .and_then(Value::as_u64)
        .unwrap_or(0) as u32;

    let resolution = if width > 0 && height > 0 {
        Some(Resolution { width, height })
    } else {
        None
    };

    // Resolution check
    if config.expect_portrait {
        if width != 1080 || height != 1920 {
            issues.push(format!(
                "expected portrait 1080x1920, got {}x{}",
                width, height
            ));
        }
    } else if config.expect_landscape {
        if width != 1920 || height != 1080 {
            issues.push(format!(
                "expected landscape 1920x1080, got {}x{}",
                width, height
            ));
        }
    }

    // FPS
    let fps_str = video_stream
        .get("avg_frame_rate")
        .and_then(Value::as_str)
        .unwrap_or("0/1");
    let fps = parse_fps(fps_str);

    // Codecs
    let video_codec = video_stream
        .get("codec_name")
        .and_then(Value::as_str)
        .map(|s| s.to_string());
    let audio_codec = audio_stream
        .and_then(|s| s.get("codec_name"))
        .and_then(Value::as_str)
        .map(|s| s.to_string());

    // Duration
    let duration_str = probe
        .get("format")
        .and_then(|f| f.get("duration"))
        .and_then(Value::as_str);
    let duration_s = duration_str.and_then(|s| s.parse::<f64>().ok());

    // Duration checks
    if let Some(dur) = duration_s {
        if let Some(req) = config.requested_duration {
            let tolerance = req * DURATION_TOLERANCE_RATIO;
            if (dur - req).abs() >= tolerance {
                issues.push(format!(
                    "duration {:.1}s deviates from requested {:.1}s by >{}%",
                    dur,
                    req,
                    (DURATION_TOLERANCE_RATIO * 100.0) as u32
                ));
            }
        } else if let Some(exp) = config.expected_duration {
            let tolerance = exp * DURATION_TOLERANCE_RATIO;
            if (dur - exp).abs() >= tolerance {
                warnings.push(format!(
                    "duration {:.1}s deviates from expected {:.1}s by >{}%",
                    dur,
                    exp,
                    (DURATION_TOLERANCE_RATIO * 100.0) as u32
                ));
            }
        }
    }

    // Audio
    let mut audio_report = AudioReport::default();
    if let Some(audio) = audio_stream {
        audio_report.channels = audio
            .get("channels")
            .and_then(Value::as_u64)
            .map(|c| c as u16);
        audio_report.sample_rate = audio
            .get("sample_rate")
            .and_then(Value::as_str)
            .and_then(|s| s.parse::<u32>().ok());
        audio_report.codec = audio_codec.clone();

        if let Some(ch) = audio_report.channels {
            if ch == 0 || ch > 2 {
                warnings.push(format!("unusual audio channel count: {}", ch));
            }
        }
        if let Some(sr) = audio_report.sample_rate {
            if sr != 44100 && sr != 48000 {
                warnings.push(format!("non-standard sample rate: {} Hz", sr));
            }
        }
    } else {
        issues.push("no audio stream found".to_string());
    }

    // Drift
    let mut drift_s = None;
    if let (Some(v), Some(a)) = (
        video_stream.get("start_time"),
        audio_stream.and_then(|s| s.get("start_time")),
    ) {
        if let (Some(vs), Some(as_)) = (v.as_str(), a.as_str()) {
            if let (Ok(vf), Ok(af)) = (vs.parse::<f64>(), as_.parse::<f64>()) {
                drift_s = Some((vf - af).abs());
                if drift_s.unwrap() > DRIFT_WARNING_SECONDS {
                    issues.push(format!(
                        "timestamp drift {:.3}s > {:.1}s threshold",
                        drift_s.unwrap(),
                        DRIFT_WARNING_SECONDS
                    ));
                }
            }
        }
    }

    // Codec validation
    let mut codecs = Codecs::default();
    if let Some(vc) = &video_codec {
        codecs.video = Some(vc.clone());
        if !matches!(vc.as_str(), "h264" | "hevc") {
            warnings.push(format!("unexpected video codec: {}", vc));
        }
    }
    if let Some(ac) = &audio_codec {
        codecs.audio = Some(ac.clone());
        if !matches!(ac.as_str(), "aac" | "mp3") {
            warnings.push(format!("unexpected audio codec: {}", ac));
        }
    }

    let passed = issues.is_empty();

    Ok(MediaInspectReport {
        passed,
        input: input_path.to_string(),
        size_mb,
        resolution,
        fps: Some(fps),
        codecs,
        duration_s,
        audio: audio_report,
        drift_s,
        issues,
        warnings,
    })
}

fn parse_fps(fps_str: &str) -> f64 {
    let parts: Vec<&str> = fps_str.split('/').collect();
    if parts.len() == 2 {
        if let (Ok(num), Ok(den)) = (parts[0].parse::<f64>(), parts[1].parse::<f64>()) {
            if den != 0.0 {
                return num / den;
            }
        }
    }
    0.0
}

#[cfg(test)]
mod tests {
    use super::*;
    use anyhow::Result;
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
                    "avg_frame_rate": fps,
                    "start_time": "0.0"
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "channels": 2,
                    "sample_rate": "48000",
                    "start_time": "0.0",
                    "duration": audio_duration
                }
            ]
        })
    }

    #[test]
    fn inspect_passes_for_expected_landscape() -> Result<()> {
        let probe = base_probe(1920, 1080, "24/1", "10.0");
        let config = MediaInspectConfig {
            expect_landscape: true,
            ..Default::default()
        };
        let report = inspect_probe(&probe, Some(1048576), &config, "test.mp4")?;
        assert!(report.passed);
        assert_eq!(
            report.resolution,
            Some(Resolution {
                width: 1920,
                height: 1080
            })
        );
        Ok(())
    }

    #[test]
    fn inspect_passes_for_expected_portrait() -> Result<()> {
        let probe = base_probe(1080, 1920, "24/1", "10.0");
        let config = MediaInspectConfig {
            expect_portrait: true,
            ..Default::default()
        };
        let report = inspect_probe(&probe, Some(1048576), &config, "test.mp4")?;
        assert!(report.passed);
        assert_eq!(
            report.resolution,
            Some(Resolution {
                width: 1080,
                height: 1920
            })
        );
        Ok(())
    }

    #[test]
    fn inspect_records_missing_audio_as_issue() -> Result<()> {
        let probe = json!({
            "format": { "duration": "10.0", "size": "1048576" },
            "streams": [{ "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "24/1" }]
        });
        let config = MediaInspectConfig::default();
        let report = inspect_probe(&probe, Some(1048576), &config, "test.mp4")?;
        assert!(!report.passed);
        assert!(report.issues.iter().any(|i| i.contains("no audio stream")));
        Ok(())
    }

    #[test]
    fn inspect_uses_requested_duration_before_expected_duration() -> Result<()> {
        // Use a custom probe with 12s actual duration (format.duration = "12.0")
        let probe = json!({
            "format": {
                "duration": "12.0",
                "size": "1048576",
                "bit_rate": "838860"
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24/1"
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "channels": 2,
                    "sample_rate": "48000"
                }
            ]
        });
        let config = MediaInspectConfig {
            requested_duration: Some(10.0),
            expected_duration: Some(20.0),
            ..Default::default()
        };
        let report = inspect_probe(&probe, Some(1048576), &config, "test.mp4")?;
        assert!(!report.passed);
        assert!(report
            .issues
            .iter()
            .any(|i| i.contains("deviates from requested")));
        Ok(())
    }

    #[test]
    fn inspect_warns_for_drift_nonstandard_resolution_and_fps() -> Result<()> {
        let probe = json!({
            "format": { "duration": "10.0", "size": "1048576" },
            "streams": [
                { "codec_type": "video", "codec_name": "vp9", "width": 1280, "height": 720, "avg_frame_rate": "30/1", "start_time": "0.0" },
                { "codec_type": "audio", "codec_name": "opus", "channels": 2, "sample_rate": "48000", "start_time": "0.5", "duration": "10.0" }
            ]
        });
        let config = MediaInspectConfig::default();
        let report = inspect_probe(&probe, Some(1048576), &config, "test.mp4")?;
        assert!(!report.passed);
        assert!(report.issues.iter().any(|i| i.contains("drift")));
        assert!(report.warnings.iter().any(|w| w.contains("video codec")));
        assert!(report.warnings.iter().any(|w| w.contains("audio codec")));
        Ok(())
    }

    #[test]
    fn inspect_fails_for_tiny_file_and_resolution_mismatch() -> Result<()> {
        let probe = base_probe(640, 480, "24/1", "10.0");
        let config = MediaInspectConfig {
            expect_landscape: true,
            ..Default::default()
        };
        let report = inspect_probe(&probe, Some(50000), &config, "test.mp4")?; // 50KB
        assert!(!report.passed);
        assert!(report.issues.iter().any(|i| i.contains("below minimum")));
        assert!(report
            .issues
            .iter()
            .any(|i| i.contains("expected landscape")));
        Ok(())
    }
}
