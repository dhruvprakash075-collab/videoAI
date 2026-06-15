use anyhow::Result;
use clap::Parser;
use videoai_worker::text::{self, TextCommand};

#[derive(Debug, Parser)]
#[command(name = "videoai-text-split")]
#[command(about = "Split source text into per-segment chunks")]
struct Cli {
    #[command(subcommand)]
    command: TextCommand,
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    text::run_command(cli.command)
}
