use std::fs;
use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use clap::{Args, Subcommand};
use serde::Serialize;

const TARGET_RAW_SAMPLE_RATE: u32 = 24_000;
const TARGET_PREMIUM_SAMPLE_RATE: u32 = 44_100;
const PEAK_CLIPPING_DBFS: f64 = -0.5;
const PEAK_SAFE_MIN_DBFS: f64 = -2.0;
const TARGET_RMS_DBFS: f64 = -14.0;
const RMS_TOLERANCE_DB: f64 = 2.5;
const CLIPPING_I16_THRESHOLD: i16 = 32_760;

#[derive(Debug, Subcommand)]
pub enum AudioCommand {
    /// Analyze WAV structure and mastering metrics.
    Analyze(AudioAnalyzeArgs),
}

#[derive(Clone, Debug, Args)]
pub struct AudioAnalyzeArgs {
    /// WAV file to analyze.
    #[arg(long)]
    pub input: PathBuf,

    /// Emit machine-readable JSON. Accepted for consistency with other worker subcommands.
    #[arg(long)]
    pub json: bool,

    /// Expected duration in seconds.
    #[arg(long)]
    pub expected_duration: Option<f64>,

    /// User-requested duration in seconds. Takes precedence over --expected-duration.
    #[arg(long)]
    pub requested_duration: Option<f64>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct AudioAnalyzeReport {
    pub passed: bool,
    pub input: String,
    pub format: String,
    pub channels: u16,
    pub sample_width_bits: u16,
    pub sample_rate: u32,
    pub frames: u32,
    pub duration_s: f64,
    pub peak_db: f64,
    pub rms_db: f64,
    pub clipping_pct: f64,
    pub issues: Vec<String>,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug)]
struct WavInfo {
    channels: u16,
    sample_width_bytes: u16,
    sample_rate: u32,
    frames: u32,
    peak_db: f64,
    rms_db: f64,
    clipping_pct: f64,
}

pub fn run_command(command: AudioCommand) -> Result<()> {
    match command {
        AudioCommand::Analyze(args) => {
            let report = analyze_path(&args)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
            if !report.passed {
                std::process::exit(2);
            }
        }
    }
    Ok(())
}

pub fn analyze_path(args: &AudioAnalyzeArgs) -> Result<AudioAnalyzeReport> {
    let bytes = fs::read(&args.input).with_context(|| {
        format!(
            "input audio not found or unreadable: {}",
            args.input.display()
        )
    })?;
    let info = analyze_wav_bytes(&bytes)?;
    Ok(build_report(
        info,
        &args.input.to_string_lossy(),
        args.requested_duration.or(args.expected_duration),
    ))
}

fn build_report(info: WavInfo, input: &str, expected_duration: Option<f64>) -> AudioAnalyzeReport {
    let mut issues = Vec::new();
    let mut warnings = Vec::new();
    let duration_s = if info.sample_rate > 0 {
        f64::from(info.frames) / f64::from(info.sample_rate)
    } else {
        0.0
    };

    if info.channels == 0 {
        issues.push("No audio channels found".to_string());
    }

    if info.sample_width_bytes != 2 {
        warnings.push(format!(
            "Unsupported sample width for full dynamics analysis: {} bits",
            info.sample_width_bytes * 8
        ));
    }

    match info.sample_rate {
        TARGET_RAW_SAMPLE_RATE => warnings.push(
            "Sample rate matches raw OmniVoice worker output; premium post-processing may still be pending".to_string(),
        ),
        TARGET_PREMIUM_SAMPLE_RATE => {}
        other => warnings.push(format!("Non-standard sample rate detected: {other}Hz")),
    }

    if info.peak_db > PEAK_CLIPPING_DBFS {
        issues.push("Critical clipping detected: peak exceeds -0.5 dBFS".to_string());
    } else if info.peak_db > PEAK_SAFE_MIN_DBFS {
        // Safe mastering zone.
    } else {
        warnings.push("Audio peak is attenuated below the -1 to -2 dBFS target zone".to_string());
    }

    if (info.rms_db - TARGET_RMS_DBFS).abs() > RMS_TOLERANCE_DB {
        warnings.push(format!(
            "Narration loudness is outside target RMS range: {:.1} dBFS",
            info.rms_db
        ));
    }

    if info.clipping_pct > 0.0 {
        issues.push(format!(
            "PCM clipping samples detected: {:.4}%",
            info.clipping_pct
        ));
    }

    if let Some(expected) = expected_duration.filter(|value| *value > 0.0) {
        let tolerance = expected * 0.20;
        if (duration_s - expected).abs() > tolerance {
            issues.push(format!(
                "Duration mismatch: {:.2}s vs expected {:.2}s",
                duration_s, expected
            ));
        }
    }

    AudioAnalyzeReport {
        passed: issues.is_empty(),
        input: input.replace('\\', "/"),
        format: "PCM WAV".to_string(),
        channels: info.channels,
        sample_width_bits: info.sample_width_bytes * 8,
        sample_rate: info.sample_rate,
        frames: info.frames,
        duration_s: round_3(duration_s),
        peak_db: round_2(info.peak_db),
        rms_db: round_2(info.rms_db),
        clipping_pct: round_4(info.clipping_pct),
        issues,
        warnings,
    }
}

fn analyze_wav_bytes(bytes: &[u8]) -> Result<WavInfo> {
    if bytes.len() < 44 || &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        bail!("unsupported WAV file: missing RIFF/WAVE header");
    }

    let mut offset = 12usize;
    let mut fmt: Option<(u16, u16, u32, u16)> = None;
    let mut data: Option<&[u8]> = None;

    while offset + 8 <= bytes.len() {
        let chunk_id = &bytes[offset..offset + 4];
        let chunk_size = u32::from_le_bytes(bytes[offset + 4..offset + 8].try_into()?) as usize;
        let chunk_start = offset + 8;
        let chunk_end = chunk_start.saturating_add(chunk_size);
        if chunk_end > bytes.len() {
            bail!("invalid WAV file: chunk extends past EOF");
        }

        match chunk_id {
            b"fmt " => {
                if chunk_size < 16 {
                    bail!("invalid WAV file: fmt chunk too small");
                }
                let audio_format =
                    u16::from_le_bytes(bytes[chunk_start..chunk_start + 2].try_into()?);
                let channels =
                    u16::from_le_bytes(bytes[chunk_start + 2..chunk_start + 4].try_into()?);
                let sample_rate =
                    u32::from_le_bytes(bytes[chunk_start + 4..chunk_start + 8].try_into()?);
                let bits_per_sample =
                    u16::from_le_bytes(bytes[chunk_start + 14..chunk_start + 16].try_into()?);
                if audio_format != 1 {
                    bail!("unsupported WAV file: only PCM format is supported");
                }
                if bits_per_sample % 8 != 0 {
                    bail!("unsupported WAV file: bits per sample must be byte-aligned");
                }
                fmt = Some((audio_format, channels, sample_rate, bits_per_sample));
            }
            b"data" => data = Some(&bytes[chunk_start..chunk_end]),
            _ => {}
        }

        offset = chunk_end + (chunk_size % 2);
    }

    let Some((_audio_format, channels, sample_rate, bits_per_sample)) = fmt else {
        bail!("invalid WAV file: missing fmt chunk");
    };
    let Some(data) = data else {
        bail!("invalid WAV file: missing data chunk");
    };

    let sample_width_bytes = bits_per_sample / 8;
    let bytes_per_frame = usize::from(channels) * usize::from(sample_width_bytes);
    if bytes_per_frame == 0 {
        bail!("invalid WAV file: zero-sized audio frame");
    }
    let frames = u32::try_from(data.len() / bytes_per_frame).context("WAV has too many frames")?;

    let (peak_db, rms_db, clipping_pct) = if sample_width_bytes == 2 {
        analyze_i16_pcm(data)
    } else {
        (-99.0, -99.0, 0.0)
    };

    Ok(WavInfo {
        channels,
        sample_width_bytes,
        sample_rate,
        frames,
        peak_db,
        rms_db,
        clipping_pct,
    })
}

fn analyze_i16_pcm(data: &[u8]) -> (f64, f64, f64) {
    let sample_count = data.len() / 2;
    if sample_count == 0 {
        return (-99.0, -99.0, 0.0);
    }

    let mut peak = 0.0_f64;
    let mut sum_squares = 0.0_f64;
    let mut clipping_samples = 0usize;

    for sample_bytes in data.chunks_exact(2) {
        let sample = i16::from_le_bytes([sample_bytes[0], sample_bytes[1]]);
        let normalized = f64::from(sample).abs() / 32_768.0;
        peak = peak.max(normalized);
        sum_squares += normalized * normalized;
        if sample == i16::MIN || sample.abs() >= CLIPPING_I16_THRESHOLD {
            clipping_samples += 1;
        }
    }

    let rms = (sum_squares / sample_count as f64).sqrt();
    let peak_db = if peak > 0.0 {
        20.0 * peak.log10()
    } else {
        -99.0
    };
    let rms_db = if rms > 0.0 {
        20.0 * rms.log10()
    } else {
        -99.0
    };
    let clipping_pct = (clipping_samples as f64 / sample_count as f64) * 100.0;
    (peak_db, rms_db, clipping_pct)
}

fn round_2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn round_3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

fn round_4(value: f64) -> f64 {
    (value * 10000.0).round() / 10000.0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wav_i16(sample_rate: u32, channels: u16, samples: &[i16]) -> Vec<u8> {
        let data_bytes = samples.len() * 2;
        let byte_rate = sample_rate * u32::from(channels) * 2;
        let block_align = channels * 2;
        let mut out = Vec::new();
        out.extend_from_slice(b"RIFF");
        out.extend_from_slice(&(36 + data_bytes as u32).to_le_bytes());
        out.extend_from_slice(b"WAVE");
        out.extend_from_slice(b"fmt ");
        out.extend_from_slice(&16u32.to_le_bytes());
        out.extend_from_slice(&1u16.to_le_bytes());
        out.extend_from_slice(&channels.to_le_bytes());
        out.extend_from_slice(&sample_rate.to_le_bytes());
        out.extend_from_slice(&byte_rate.to_le_bytes());
        out.extend_from_slice(&block_align.to_le_bytes());
        out.extend_from_slice(&16u16.to_le_bytes());
        out.extend_from_slice(b"data");
        out.extend_from_slice(&(data_bytes as u32).to_le_bytes());
        for sample in samples {
            out.extend_from_slice(&sample.to_le_bytes());
        }
        out
    }

    #[test]
    fn analyzes_pcm_wav_metrics() -> Result<()> {
        let wav = wav_i16(44_100, 1, &[8192, -8192, 8192, -8192]);
        let info = analyze_wav_bytes(&wav)?;
        let report = build_report(info, "voice.wav", None);

        assert!(report.passed);
        assert_eq!(report.channels, 1);
        assert_eq!(report.sample_width_bits, 16);
        assert_eq!(report.sample_rate, 44_100);
        assert_eq!(report.frames, 4);
        assert_eq!(report.peak_db, -12.04);
        assert_eq!(report.rms_db, -12.04);
        Ok(())
    }

    #[test]
    fn flags_clipping_as_issue() -> Result<()> {
        let wav = wav_i16(44_100, 1, &[32_767, -32_768, 0, 0]);
        let info = analyze_wav_bytes(&wav)?;
        let report = build_report(info, "voice.wav", None);

        assert!(!report.passed);
        assert!(report.issues.iter().any(|issue| issue.contains("clipping")));
        assert!(report.issues.iter().any(|issue| issue.contains("peak")));
        Ok(())
    }

    #[test]
    fn warns_for_raw_or_nonstandard_sample_rate() -> Result<()> {
        let raw = build_report(
            analyze_wav_bytes(&wav_i16(24_000, 1, &[4096, -4096]))?,
            "raw.wav",
            None,
        );
        assert!(raw
            .warnings
            .iter()
            .any(|warning| warning.contains("OmniVoice")));

        let odd = build_report(
            analyze_wav_bytes(&wav_i16(48_000, 1, &[4096, -4096]))?,
            "odd.wav",
            None,
        );
        assert!(odd
            .warnings
            .iter()
            .any(|warning| warning.contains("Non-standard")));
        Ok(())
    }

    #[test]
    fn expected_duration_flags_mismatch() -> Result<()> {
        let wav = wav_i16(10, 1, &[1024; 10]);
        let info = analyze_wav_bytes(&wav)?;
        let report = build_report(info, "voice.wav", Some(3.0));

        assert!(!report.passed);
        assert!(report
            .issues
            .iter()
            .any(|issue| issue.contains("Duration mismatch: 1.00s vs expected 3.00s")));
        Ok(())
    }

    #[test]
    fn rejects_missing_wav_header() {
        let err = analyze_wav_bytes(b"not wav").expect_err("invalid header should fail");
        assert!(err.to_string().contains("RIFF/WAVE"));
    }
}
