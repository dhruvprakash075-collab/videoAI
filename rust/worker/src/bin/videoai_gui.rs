//! Native Video.AI studio launcher — the one-click entry point.
//!
//! Build with `cargo build --release --features gui` to produce an app that
//! owns backend + worker lifecycle in a single window (no .bat / console).

use anyhow::Result;

fn main() -> Result<()> {
    videoai_worker::gui::run()
}
