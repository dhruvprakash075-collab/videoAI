use anyhow::Result;
use clap::Parser;
use videoai_worker::assets::{run, AssetsCli};

fn main() -> Result<()> {
    run(AssetsCli::parse())
}