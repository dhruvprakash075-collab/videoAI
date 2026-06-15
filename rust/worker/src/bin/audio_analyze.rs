use anyhow::Result;
use clap::Parser;
use videoai_worker::audio::{self, AudioCommand};

#[derive(Debug, Parser)]
#[command(name = "videoai-audio-analyze")]
#[command(about = "Analyze WAV structure and mastering metrics")]
struct Cli {
    #[command(subcommand)]
    command: AudioCommand,
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    audio::run_command(cli.command)
}
