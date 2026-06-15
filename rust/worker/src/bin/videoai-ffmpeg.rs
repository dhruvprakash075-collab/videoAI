use anyhow::Result;
use clap::Parser;
use videoai_worker::ffmpeg_plan::{self, FfmpegCommand};

#[derive(Debug, Parser)]
#[command(name = "videoai-ffmpeg")]
#[command(about = "Plan and execute Video.AI Rust FFmpeg final assembly")]
struct Cli {
    #[command(subcommand)]
    command: FfmpegCommand,
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    ffmpeg_plan::run_command(cli.command)
}
