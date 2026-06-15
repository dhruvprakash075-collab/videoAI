use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use clap::{Args, Subcommand};
use serde::Serialize;

const DEFAULT_TARGET_LUFS: f64 = -14.0;
const DEFAULT_TRUE_PEAK: f64 = -1.5;
const DEFAULT_LRA: f64 = 11.0;

#[derive(Debug, Subcommand)]
pub enum FfmpegCommand {
    /// Dry-run final assembly and print the FFmpeg argv plan.
    Plan(FfmpegPlanArgs),

    /// Execute final assembly with FFmpeg.
    Concat(FfmpegConcatArgs),
}

#[derive(Clone, Debug, Args)]
pub struct FfmpegPlanArgs {
    /// Run directory containing rendered segment MP4s.
    #[arg(long)]
    pub run_dir: PathBuf,

    /// Optional background music track.
    #[arg(long)]
    pub music: Option<PathBuf>,

    /// Emit machine-readable JSON.
    #[arg(long)]
    pub json: bool,

    /// FFmpeg filter thread count.
    #[arg(long, default_value_t = 0)]
    pub ffmpeg_threads: u32,

    /// Disable 2-pass EBU R128 loudnorm.
    #[arg(long)]
    pub no_loudnorm: bool,

    /// Disable sidechain music ducking.
    #[arg(long)]
    pub no_duck: bool,

    /// FFmpeg executable path.
    #[arg(long, default_value = "ffmpeg")]
    pub ffmpeg_bin: PathBuf,
}

#[derive(Clone, Debug, Args)]
pub struct FfmpegConcatArgs {
    /// Run directory containing rendered segment MP4s.
    #[arg(long)]
    pub run_dir: PathBuf,

    /// Final MP4 output path.
    #[arg(long)]
    pub out: PathBuf,

    /// Optional background music track.
    #[arg(long)]
    pub music: Option<PathBuf>,

    /// FFmpeg filter thread count.
    #[arg(long, default_value_t = 0)]
    pub ffmpeg_threads: u32,

    /// Disable 2-pass EBU R128 loudnorm.
    #[arg(long)]
    pub no_loudnorm: bool,

    /// Disable sidechain music ducking.
    #[arg(long)]
    pub no_duck: bool,

    /// FFmpeg executable path.
    #[arg(long, default_value = "ffmpeg")]
    pub ffmpeg_bin: PathBuf,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct FfmpegPlan {
    pub run_dir: String,
    pub output: String,
    pub segments: Vec<String>,
    pub concat_list: String,
    pub temp_dir: String,
    pub loudnorm: bool,
    pub music: Option<String>,
    pub commands: Vec<PlannedCommand>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct PlannedCommand {
    pub phase: String,
    pub argv: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct AssemblyOptions {
    pub run_dir: PathBuf,
    pub output: PathBuf,
    pub music: Option<PathBuf>,
    pub ffmpeg_threads: u32,
    pub loudnorm: bool,
    pub duck: bool,
    pub ffmpeg_bin: PathBuf,
    pub temp_dir: PathBuf,
    pub concat_list: PathBuf,
}

pub fn run_command(command: FfmpegCommand) -> Result<()> {
    match command {
        FfmpegCommand::Plan(args) => {
            let output = args.run_dir.join("final_video.mp4");
            let temp_dir = args.run_dir.join(".rust_tmp").join("ffmpeg-plan");
            let concat_list = temp_dir.join("concat_list.txt");
            let options = AssemblyOptions {
                run_dir: args.run_dir,
                output,
                music: args.music,
                ffmpeg_threads: args.ffmpeg_threads,
                loudnorm: !args.no_loudnorm,
                duck: !args.no_duck,
                ffmpeg_bin: args.ffmpeg_bin,
                temp_dir,
                concat_list,
            };
            let plan = build_plan(&options)?;
            print_plan(&plan, args.json)?;
        }
        FfmpegCommand::Concat(args) => {
            crate::ffmpeg_exec::run_concat(args)?;
        }
    }

    Ok(())
}

pub fn options_from_concat_args(args: &FfmpegConcatArgs, temp_dir: PathBuf) -> AssemblyOptions {
    AssemblyOptions {
        run_dir: args.run_dir.clone(),
        output: args.out.clone(),
        music: args.music.clone(),
        ffmpeg_threads: args.ffmpeg_threads,
        loudnorm: !args.no_loudnorm,
        duck: !args.no_duck,
        ffmpeg_bin: args.ffmpeg_bin.clone(),
        concat_list: temp_dir.join("concat_list.txt"),
        temp_dir,
    }
}

pub fn build_plan(options: &AssemblyOptions) -> Result<FfmpegPlan> {
    ensure_run_dir(&options.run_dir)?;
    let segments = discover_segments(&options.run_dir)?;
    if segments.is_empty() {
        bail!(
            "no segment MP4 files found under {}",
            options.run_dir.display()
        );
    }

    if let Some(music) = options.music.as_deref() {
        if !music.is_file() {
            bail!("music file not found: {}", music.display());
        }
    }

    let mut commands = Vec::new();
    commands.push(PlannedCommand {
        phase: "ffprobe-uniformity".to_string(),
        argv: ffprobe_uniformity_argv(&segments),
    });

    let concat_output = if options.loudnorm {
        options.temp_dir.join("concat_prenorm.mp4")
    } else {
        options.output.clone()
    };

    commands.push(PlannedCommand {
        phase: "concat".to_string(),
        argv: concat_argv(options, &concat_output),
    });

    if options.loudnorm {
        commands.push(PlannedCommand {
            phase: "loudnorm-measure".to_string(),
            argv: loudnorm_measure_argv(options, &concat_output),
        });
        commands.push(PlannedCommand {
            phase: "loudnorm-apply".to_string(),
            argv: loudnorm_apply_argv(options, &concat_output, &LoudnormStats::placeholder()),
        });
    }

    Ok(FfmpegPlan {
        run_dir: display_path(&options.run_dir),
        output: display_path(&options.output),
        segments: segments.iter().map(|p| display_path(p)).collect(),
        concat_list: display_path(&options.concat_list),
        temp_dir: display_path(&options.temp_dir),
        loudnorm: options.loudnorm,
        music: options.music.as_ref().map(|p| display_path(p)),
        commands,
    })
}

pub fn discover_segments(run_dir: &Path) -> Result<Vec<PathBuf>> {
    let mut segments = Vec::new();
    collect_segment_files(run_dir, &mut segments)?;
    segments.sort_by_key(|path| display_path(path));
    Ok(segments)
}

pub fn concat_list_content(segments: &[PathBuf]) -> String {
    let mut lines = segments
        .iter()
        .map(|segment| format!("file '{}'", escape_concat_path(segment)))
        .collect::<Vec<_>>();
    lines.push(String::new());
    lines.join("\n")
}

pub fn escape_concat_path(path: &Path) -> String {
    display_path(path).replace('\'', "'\\''")
}

pub fn display_path(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

pub fn loudnorm_filter(stats: Option<&LoudnormStats>) -> String {
    match stats {
        Some(stats) => format!(
            "loudnorm=I={DEFAULT_TARGET_LUFS}:TP={DEFAULT_TRUE_PEAK}:LRA={DEFAULT_LRA}:measured_I={}:measured_TP={}:measured_LRA={}:measured_thresh={}:offset={}:linear=true",
            stats.input_i, stats.input_tp, stats.input_lra, stats.input_thresh, stats.target_offset
        ),
        None => format!(
            "loudnorm=I={DEFAULT_TARGET_LUFS}:TP={DEFAULT_TRUE_PEAK}:LRA={DEFAULT_LRA}:print_format=json"
        ),
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LoudnormStats {
    pub input_i: String,
    pub input_tp: String,
    pub input_lra: String,
    pub input_thresh: String,
    pub target_offset: String,
}

impl LoudnormStats {
    fn placeholder() -> Self {
        Self {
            input_i: "{input_i}".to_string(),
            input_tp: "{input_tp}".to_string(),
            input_lra: "{input_lra}".to_string(),
            input_thresh: "{input_thresh}".to_string(),
            target_offset: "{target_offset}".to_string(),
        }
    }
}

fn collect_segment_files(dir: &Path, segments: &mut Vec<PathBuf>) -> Result<()> {
    for entry in
        fs::read_dir(dir).with_context(|| format!("failed to read directory {}", dir.display()))?
    {
        let entry = entry.with_context(|| format!("failed to read entry in {}", dir.display()))?;
        let path = entry.path();
        let file_type = entry
            .file_type()
            .with_context(|| format!("failed to read file type for {}", path.display()))?;
        if file_type.is_dir() {
            if path
                .file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name == ".rust_tmp")
            {
                continue;
            }
            collect_segment_files(&path, segments)?;
            continue;
        }
        if file_type.is_file() && is_segment_mp4(&path) {
            segments.push(path);
        }
    }

    Ok(())
}

fn is_segment_mp4(path: &Path) -> bool {
    let is_mp4 = path
        .extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| ext.eq_ignore_ascii_case("mp4"));
    let is_segment = path
        .file_stem()
        .and_then(|stem| stem.to_str())
        .is_some_and(|stem| stem.starts_with("segment_"));
    is_mp4 && is_segment
}

fn ensure_run_dir(run_dir: &Path) -> Result<()> {
    if !run_dir.is_dir() {
        bail!("run directory not found: {}", run_dir.display());
    }
    Ok(())
}

fn ffprobe_uniformity_argv(segments: &[PathBuf]) -> Vec<String> {
    let mut argv = vec![
        "ffprobe".to_string(),
        "-v".to_string(),
        "error".to_string(),
        "-select_streams".to_string(),
        "v:0".to_string(),
        "-show_entries".to_string(),
        "stream=codec_name,pix_fmt,width,height,r_frame_rate,time_base".to_string(),
        "-of".to_string(),
        "json".to_string(),
    ];
    argv.extend(segments.iter().map(|segment| display_path(segment)));
    argv
}

fn concat_argv(options: &AssemblyOptions, output: &Path) -> Vec<String> {
    let ffmpeg = display_path(&options.ffmpeg_bin);
    let concat_list = display_path(&options.concat_list);
    if let Some(music) = options.music.as_deref() {
        let filter = if options.duck {
            let comp_ratio = 4.0_f64;
            format!(
                "[0:a]asplit=2[voice_mix][voice_key];[1:a]volume=0.15,afade=t=in:st=0:d=3[music_in];[music_in][voice_key]sidechaincompress=threshold=0.05:ratio={comp_ratio:.1}:attack=20:release=300[ducked];[voice_mix][ducked]amix=inputs=2:duration=first:normalize=0[outa]"
            )
        } else {
            "[1:a]volume=0.15,afade=t=in:st=0:d=3[bg];[0:a][bg]amix=inputs=2:duration=first[outa]"
                .to_string()
        };
        vec![
            ffmpeg,
            "-y".to_string(),
            "-f".to_string(),
            "concat".to_string(),
            "-safe".to_string(),
            "0".to_string(),
            "-i".to_string(),
            concat_list,
            "-stream_loop".to_string(),
            "-1".to_string(),
            "-i".to_string(),
            display_path(music),
            "-filter_threads".to_string(),
            options.ffmpeg_threads.to_string(),
            "-filter_complex".to_string(),
            filter,
            "-map".to_string(),
            "0:v".to_string(),
            "-map".to_string(),
            "[outa]".to_string(),
            "-c:v".to_string(),
            "copy".to_string(),
            "-c:a".to_string(),
            "aac".to_string(),
            "-b:a".to_string(),
            "192k".to_string(),
            display_path(output),
        ]
    } else {
        vec![
            ffmpeg,
            "-y".to_string(),
            "-f".to_string(),
            "concat".to_string(),
            "-safe".to_string(),
            "0".to_string(),
            "-i".to_string(),
            concat_list,
            "-c:v".to_string(),
            "copy".to_string(),
            "-c:a".to_string(),
            "aac".to_string(),
            "-ar".to_string(),
            "48000".to_string(),
            "-b:a".to_string(),
            "192k".to_string(),
            display_path(output),
        ]
    }
}

fn loudnorm_measure_argv(options: &AssemblyOptions, input: &Path) -> Vec<String> {
    vec![
        display_path(&options.ffmpeg_bin),
        "-y".to_string(),
        "-i".to_string(),
        display_path(input),
        "-af".to_string(),
        loudnorm_filter(None),
        "-f".to_string(),
        "null".to_string(),
        "-".to_string(),
    ]
}

pub fn loudnorm_apply_argv(
    options: &AssemblyOptions,
    input: &Path,
    stats: &LoudnormStats,
) -> Vec<String> {
    vec![
        display_path(&options.ffmpeg_bin),
        "-y".to_string(),
        "-i".to_string(),
        display_path(input),
        "-af".to_string(),
        loudnorm_filter(Some(stats)),
        "-c:v".to_string(),
        "copy".to_string(),
        "-c:a".to_string(),
        "aac".to_string(),
        "-b:a".to_string(),
        "192k".to_string(),
        display_path(&options.output),
    ]
}

fn print_plan(plan: &FfmpegPlan, json: bool) -> Result<()> {
    if json {
        println!("{}", serde_json::to_string_pretty(plan)?);
        return Ok(());
    }
    println!("run_dir: {}", plan.run_dir);
    println!("output: {}", plan.output);
    println!("segments: {}", plan.segments.len());
    for command in &plan.commands {
        println!("{}: {}", command.phase, command.argv.join(" "));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn touch(path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, b"mp4")?;
        Ok(())
    }

    #[test]
    fn concat_list_escapes_spaces_and_single_quotes() {
        let paths = vec![
            PathBuf::from("run/segment 01.mp4"),
            PathBuf::from("run/seg'ment_02.mp4"),
        ];
        let list = concat_list_content(&paths);
        assert_eq!(
            list,
            "file 'run/segment 01.mp4'\nfile 'run/seg'\\''ment_02.mp4'\n"
        );
    }

    #[test]
    fn discover_segments_sorts_and_ignores_non_segments() -> Result<()> {
        let temp = tempfile::tempdir()?;
        touch(&temp.path().join("segment_02.mp4"))?;
        touch(&temp.path().join("nested").join("segment_01.mp4"))?;
        touch(&temp.path().join("final_video.mp4"))?;
        touch(&temp.path().join(".rust_tmp").join("segment_00.mp4"))?;

        let segments = discover_segments(temp.path())?;

        assert_eq!(segments.len(), 2);
        assert!(display_path(&segments[0]).ends_with("nested/segment_01.mp4"));
        assert!(display_path(&segments[1]).ends_with("segment_02.mp4"));
        Ok(())
    }

    #[test]
    fn plan_is_deterministic_and_has_no_side_effects() -> Result<()> {
        let temp = tempfile::tempdir()?;
        touch(&temp.path().join("segment_01.mp4"))?;
        touch(&temp.path().join("segment_02.mp4"))?;
        let options = AssemblyOptions {
            run_dir: temp.path().to_path_buf(),
            output: temp.path().join("final.mp4"),
            music: None,
            ffmpeg_threads: 0,
            loudnorm: true,
            duck: true,
            ffmpeg_bin: PathBuf::from("ffmpeg"),
            temp_dir: temp.path().join(".rust_tmp").join("ffmpeg-plan"),
            concat_list: temp
                .path()
                .join(".rust_tmp")
                .join("ffmpeg-plan")
                .join("concat_list.txt"),
        };

        let plan = build_plan(&options)?;

        assert_eq!(plan.segments.len(), 2);
        assert_eq!(plan.commands.len(), 4);
        assert_eq!(plan.commands[0].phase, "ffprobe-uniformity");
        assert_eq!(plan.commands[1].phase, "concat");
        assert_eq!(plan.commands[2].phase, "loudnorm-measure");
        assert_eq!(plan.commands[3].phase, "loudnorm-apply");
        assert!(!options.temp_dir.exists());
        Ok(())
    }
}
