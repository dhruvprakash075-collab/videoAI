use anyhow::Result;
use clap::Parser;
use videoai_worker::checkpoint::{run_command, CheckpointCommand};

#[derive(Debug, Parser)]
#[command(name = "videoai-checkpoint")]
#[command(about = "Crash-safe Video.AI checkpoint state store")]
struct Cli {
    #[command(subcommand)]
    command: CheckpointCommand,
}

fn main() -> Result<()> {
    run_command(Cli::parse().command)
}
