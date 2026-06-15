use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{bail, Context, Result};
use clap::{Args, Subcommand};
use serde::Serialize;

const TARGET_RAW_SAMPLE_RATE: u32 = 24_000;
const TARGET_PREMIUM_SAMPLE_RATE: u32 = 44_100;
const PEAK_CLIPPING_DBFS: f64 = -0.5;
const PEAK_SAFE_MIN_DBFS: f64 = -2.0;
const PEAK_LIMIT_DBFS: f64 = -1.0;
const TARGET_RMS_DBFS: f64 = -14.0;
const RMS_TOLERANCE_DB: f64 = 2.5;
const CLIPPING_I16_THRESHOLD: i16 = 32_760;
const AUDIO_MASTER_FILTER: &str =
    "highpass=f=60,acompressor=threshold=-24dB:ratio=2:attack=10:release=100,loudnorm=I=-14:TP=-1.5:LRA=9";
const STDERR_TAIL_BYTES: usize = 4_000;

#[derive(Debug, Subcommand)]
pub enum AudioCommand {
    /// Analyze WAV structure and mastering metrics.
    Analyze(AudioAnalyzeArgs),

    /// Apply the Rust-side FFmpeg mastering fallback chain to a WAV file.
    Master(AudioMasterArgs),
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

#[derive(Clone, Debug, Args)]
pub struct AudioMasterArgs {
    /// Input WAV file to master.
    #[arg(long)]
    pub input: PathBuf,

    /// Output WAV file to write.
    #[arg(long)]
    pub output: PathBuf,

    /// Emit machine-readable JSON. Accepted for consistency with other worker subcommands.
    #[arg(long)]
    pub json: bool,

    /// FFmpeg executable path.
    #[arg(long, default_value = "ffmpeg")]
    pub ffmpeg_bin: PathBuf,
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

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct AudioMasterReport {
    pub passed: bool,
    pub input: String,
    pub output: String,
    pub skipped: bool,
    pub copied_original: bool,
    pub native_pcm_mastered: bool,
    pub ffmpeg_filter: String,
    pub before: Option<AudioAnalyzeReport>,
    pub after: Option<AudioAnalyzeReport>,
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

#[derive(Clone, Debug)]
struct Pcm16Wav {
    channels: u16,
    sample_rate: u32,
    samples: Vec<i16>,
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
        AudioCommand::Master(args) => {
            let report = master_path(&args)?;
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

pub fn master_path(args: &AudioMasterArgs) -> Result<AudioMasterReport> {
    if !args.input.is_file() {
        bail!("input audio not found: {}", args.input.display());
    }
    if let Some(parent) = args.output.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).with_context(|| {
                format!("failed to create output directory {}", parent.display())
            })?;
        }
    }

    let before = analyze_path(&AudioAnalyzeArgs {
        input: args.input.clone(),
        json: true,
        expected_duration: None,
        requested_duration: None,
    })
    .ok();

    let mut issues = Vec::new();
    let mut warnings = Vec::new();
    let mut skipped = false;
    let mut copied_original = false;
    let mut native_pcm_mastered = false;

    if args
        .input
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.to_ascii_lowercase().contains("silence"))
    {
        copy_audio(&args.input, &args.output)?;
        skipped = true;
        copied_original = true;
        warnings.push("Skipped mastering for silence audio file".to_string());
    } else {
        match run_native_pcm_master(&args.input, &args.output) {
            Ok(()) => {
                native_pcm_mastered = true;
            }
            Err(native_err) => {
                warnings.push(format!(
                    "Native PCM mastering unavailable; used FFmpeg fallback: {native_err}"
                ));
                let argv = master_argv(args);
                if let Err(err) = run_master_argv(&argv) {
                    warnings.push(format!(
                        "FFmpeg mastering fallback failed; copied original audio: {err}"
                    ));
                    copy_audio(&args.input, &args.output)?;
                    copied_original = true;
                }
            }
        }
    }

    if !args.output.is_file() {
        issues.push(format!(
            "mastering produced no output at {}",
            args.output.display()
        ));
    }

    let after = if args.output.is_file() {
        analyze_path(&AudioAnalyzeArgs {
            input: args.output.clone(),
            json: true,
            expected_duration: None,
            requested_duration: None,
        })
        .ok()
    } else {
        None
    };

    Ok(AudioMasterReport {
        passed: issues.is_empty(),
        input: display_path(&args.input),
        output: display_path(&args.output),
        skipped,
        copied_original,
        native_pcm_mastered,
        ffmpeg_filter: AUDIO_MASTER_FILTER.to_string(),
        before,
        after,
        issues,
        warnings,
    })
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
    let wav = decode_pcm_wav(bytes)?;
    let frames = u32::try_from(wav.samples.len() / usize::from(wav.channels))
        .context("WAV has too many frames")?;
    let (peak_db, rms_db, clipping_pct) = analyze_i16_samples(&wav.samples);

    Ok(WavInfo {
        channels: wav.channels,
        sample_width_bytes: 2,
        sample_rate: wav.sample_rate,
        frames,
        peak_db,
        rms_db,
        clipping_pct,
    })
}

fn decode_pcm_wav(bytes: &[u8]) -> Result<Pcm16Wav> {
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
                fmt = Some((audio_format, channels, sample_rate, bits_per_sample));
            }
            b"data" => data = Some(&bytes[chunk_start..chunk_end]),
            _ => {}
        }

        offset = chunk_end + (chunk_size % 2);
    }

    let Some((audio_format, channels, sample_rate, bits_per_sample)) = fmt else {
        bail!("invalid WAV file: missing fmt chunk");
    };
    let Some(data) = data else {
        bail!("invalid WAV file: missing data chunk");
    };

    if audio_format != 1 {
        bail!("unsupported WAV file: only PCM format is supported");
    }
    if bits_per_sample != 16 {
        bail!("unsupported WAV file: native mastering requires 16-bit PCM");
    }
    if channels == 0 {
        bail!("invalid WAV file: zero audio channels");
    }
    let bytes_per_frame = usize::from(channels) * 2;
    if data.len() % bytes_per_frame != 0 {
        bail!("invalid WAV file: partial PCM frame");
    }

    let samples = data
        .chunks_exact(2)
        .map(|sample_bytes| i16::from_le_bytes([sample_bytes[0], sample_bytes[1]]))
        .collect();

    Ok(Pcm16Wav {
        channels,
        sample_rate,
        samples,
    })
}

fn analyze_i16_samples(samples: &[i16]) -> (f64, f64, f64) {
    if samples.is_empty() {
        return (-99.0, -99.0, 0.0);
    }

    let mut peak = 0.0_f64;
    let mut sum_squares = 0.0_f64;
    let mut clipping_samples = 0usize;

    for sample in samples {
        let normalized = f64::from(*sample).abs() / 32_768.0;
        peak = peak.max(normalized);
        sum_squares += normalized * normalized;
        if *sample == i16::MIN || sample.abs() >= CLIPPING_I16_THRESHOLD {
            clipping_samples += 1;
        }
    }

    let rms = (sum_squares / samples.len() as f64).sqrt();
    let peak_db = if peak > 0.0 {
        20.0 * peak.log10()
    } else {
        -99.0
    };
    let rms_db = if rms > 0.0 { 20.0 * rms.log10() } else { -99.0 };
    let clipping_pct = (clipping_samples as f64 / samples.len() as f64) * 100.0;
    (peak_db, rms_db, clipping_pct)
}

fn run_native_pcm_master(input: &Path, output: &Path) -> Result<()> {
    let bytes = fs::read(input).with_context(|| format!("failed to read {}", input.display()))?;
    let mut wav = decode_pcm_wav(&bytes)?;
    normalize_and_limit_i16(&mut wav.samples);
    let output_bytes = encode_pcm16_wav(&wav)?;
    fs::write(output, output_bytes).with_context(|| format!("failed to write {}", output.display()))?;
    Ok(())
}

fn normalize_and_limit_i16(samples: &mut [i16]) {
    if samples.is_empty() {
        return;
    }

    let peak_limit = dbfs_to_linear(PEAK_LIMIT_DBFS);
    let target_rms = dbfs_to_linear(TARGET_RMS_DBFS);
    let current_peak = samples
        .iter()
        .map(|sample| f64::from(*sample).abs() / 32_768.0)
        .fold(0.0_f64, f64::max);

    if current_peak > peak_limit && current_peak > 0.0 {
        apply_gain_and_limit(samples, peak_limit / current_peak, peak_limit);
    }

    let current_rms = rms_linear(samples);
    if current_rms > 0.0 {
        apply_gain_and_limit(samples, target_rms / current_rms, peak_limit);
    }
}

fn rms_linear(samples: &[i16]) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    let sum_squares = samples
        .iter()
        .map(|sample| {
            let normalized = f64::from(*sample) / 32_768.0;
            normalized * normalized
        })
        .sum::<f64>();
    (sum_squares / samples.len() as f64).sqrt()
}

fn apply_gain_and_limit(samples: &mut [i16], gain: f64, peak_limit: f64) {
    let limit = peak_limit * 32_768.0;
    for sample in samples {
        let scaled = (f64::from(*sample) * gain).clamp(-limit, limit).round();
        *sample = scaled.clamp(f64::from(i16::MIN), f64::from(i16::MAX)) as i16;
    }
}

fn dbfs_to_linear(dbfs: f64) -> f64 {
    10_f64.powf(dbfs / 20.0)
}

fn encode_pcm16_wav(wav: &Pcm16Wav) -> Result<Vec<u8>> {
    if wav.channels == 0 {
        bail!("invalid WAV file: zero audio channels");
    }
    if wav.samples.len() % usize::from(wav.channels) != 0 {
        bail!("invalid WAV file: sample count is not frame-aligned");
    }

    let data_bytes = u32::try_from(wav.samples.len() * 2).context("WAV data is too large")?;
    let byte_rate = wav.sample_rate * u32::from(wav.channels) * 2;
    let block_align = wav.channels * 2;
    let mut out = Vec::with_capacity(44 + data_bytes as usize);

    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    out.extend_from_slice(b"WAVE");
    out.extend_from_slice(b"fmt ");
    out.extend_from_slice(&16u32.to_le_bytes());
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&wav.channels.to_le_bytes());
    out.extend_from_slice(&wav.sample_rate.to_le_bytes());
    out.extend_from_slice(&byte_rate.to_le_bytes());
    out.extend_from_slice(&block_align.to_le_bytes());
    out.extend_from_slice(&16u16.to_le_bytes());
    out.extend_from_slice(b"data");
    out.extend_from_slice(&data_bytes.to_le_bytes());
    for sample in &wav.samples {
        out.extend_from_slice(&sample.to_le_bytes());
    }
    Ok(out)
}

fn master_argv(args: &AudioMasterArgs) -> Vec<String> {
    vec![
        display_path(&args.ffmpeg_bin),
        "-y".to_string(),
        "-i".to_string(),
        display_path(&args.input),
        "-af".to_string(),
        AUDIO_MASTER_FILTER.to_string(),
        "-c:a".to_string(),
        "pcm_s16le".to_string(),
        display_path(&args.output),
    ]
}

fn run_master_argv(argv: &[String]) -> Result<()> {
    let (program, command_args) = argv
        .split_first()
        .context("internal error: empty audio master argv")?;
    let output = Command::new(program)
        .args(command_args)
        .output()
        .with_context(|| format!("failed to spawn {program}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        bail!("ffmpeg failed: {}", stderr_tail(&stderr));
    }
    Ok(())
}

fn copy_audio(input: &Path, output: &Path) -> Result<()> {
    fs::copy(input, output).with_context(|| {
        format!(
            "failed to copy audio from {} to {}",
            input.display(),
            output.display()
        )
    })?;
    Ok(())
}

fn display_path(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

fn stderr_tail(stderr: &str) -> String {
    if stderr.len() <= STDERR_TAIL_BYTES {
        return stderr.to_string();
    }
    stderr[stderr.len().saturating_sub(STDERR_TAIL_BYTES)..].to_string()
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
        let wav = Pcm16Wav {
            channels,
            sample_rate,
            samples: samples.to_vec(),
        };
        encode_pcm16_wav(&wav).expect("test WAV should encode")
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

    #[test]
    fn native_mastering_normalizes_and_limits_pcm() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let input = temp.path().join("voice.wav");
        let output = temp.path().join("mastered.wav");
        fs::write(&input, wav_i16(44_100, 1, &[2048, -2048, 2048, -2048]))?;

        let report = master_path(&AudioMasterArgs {
            input,
            output,
            json: true,
            ffmpeg_bin: PathBuf::from("does-not-run"),
        })?;

        assert!(report.passed);
        assert!(report.native_pcm_mastered);
        assert!(!report.copied_original);
        let after = report.after.expect("native master should analyze output");
        assert!(after.peak_db <= PEAK_CLIPPING_DBFS);
        assert!((after.rms_db - TARGET_RMS_DBFS).abs() <= RMS_TOLERANCE_DB);
        Ok(())
    }

    #[test]
    fn master_argv_matches_audio_fx_fallback_chain() {
        let args = AudioMasterArgs {
            input: PathBuf::from("voice.wav"),
            output: PathBuf::from("mastered.wav"),
            json: true,
            ffmpeg_bin: PathBuf::from("ffmpeg"),
        };

        assert_eq!(
            master_argv(&args),
            vec![
                "ffmpeg".to_string(),
                "-y".to_string(),
                "-i".to_string(),
                "voice.wav".to_string(),
                "-af".to_string(),
                "highpass=f=60,acompressor=threshold=-24dB:ratio=2:attack=10:release=100,loudnorm=I=-14:TP=-1.5:LRA=9".to_string(),
                "-c:a".to_string(),
                "pcm_s16le".to_string(),
                "mastered.wav".to_string(),
            ]
        );
    }

    #[test]
    fn silence_mastering_copies_original_without_ffmpeg() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let input = temp.path().join("silence_00.wav");
        let output = temp.path().join("mastered.wav");
        fs::write(&input, wav_i16(44_100, 1, &[0, 0, 0, 0]))?;

        let report = master_path(&AudioMasterArgs {
            input: input.clone(),
            output: output.clone(),
            json: true,
            ffmpeg_bin: PathBuf::from("does-not-run"),
        })?;

        assert!(report.passed);
        assert!(report.skipped);
        assert!(report.copied_original);
        assert!(!report.native_pcm_mastered);
        assert_eq!(fs::read(input)?, fs::read(output)?);
        Ok(())
    }
}
