//! Drift guard: the three copies of the accepted-flag list must agree.
//!
//! `request_json` keys are filtered by two independent whitelists — the Rust
//! worker (`request::is_supported_arg`) and the Python worker
//! (`jobs/worker.py::supported_args`) — and both are supposed to mirror the
//! real CLI surface in `bootstrap_pipeline.py`. When they drift, a queued job
//! silently loses the flag (it is skipped, no error), which is how the
//! storyboard/vision flags came to be dropped.
//!
//! This test parses the Python sources rather than trusting a hand-kept list.

use std::collections::BTreeSet;
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    // CARGO_MANIFEST_DIR is <repo>/rust/worker
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .expect("repo root")
        .to_path_buf()
}

/// Extract `"a" | "b" | ...` string literals from the body of a named fn.
fn rust_flag_set(source: &str, fn_name: &str) -> BTreeSet<String> {
    let start = source
        .find(fn_name)
        .unwrap_or_else(|| panic!("{fn_name} not found"));
    let body = &source[start..];
    let end = body.find("\n}").expect("fn body must close");
    let mut out = BTreeSet::new();
    for chunk in body[..end].split('"').skip(1).step_by(2) {
        let t = chunk.trim();
        if !t.is_empty() && t.chars().all(|c| c.is_ascii_lowercase() || c == '_') {
            out.insert(t.to_string());
        }
    }
    out
}

/// Extract quoted identifiers inside the `supported_args = { ... }` literal.
fn python_worker_flag_set(source: &str) -> BTreeSet<String> {
    let start = source
        .find("supported_args = {")
        .expect("supported_args literal not found");
    let body = &source[start..];
    let end = body.find('}').expect("supported_args literal must close");
    let mut out = BTreeSet::new();
    for chunk in body[..end].split('"').skip(1).step_by(2) {
        out.insert(chunk.trim().to_string());
    }
    out
}

/// Extract the `--flag-name` strings declared via argparse in bootstrap.
fn bootstrap_cli_flag_set(source: &str) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    let mut rest = source;
    while let Some(pos) = rest.find("\"--") {
        let after = &rest[pos + 3..];
        let Some(close) = after.find('"') else { break };
        let flag = &after[..close];
        if !flag.is_empty() && flag.chars().all(|c| c.is_ascii_lowercase() || c == '-') {
            out.insert(flag.replace('-', "_"));
        }
        rest = &after[close..];
    }
    out
}

#[test]
fn request_whitelists_match_bootstrap_cli_surface() {
    let root = repo_root();

    let rust_src =
        std::fs::read_to_string(root.join("rust/worker/src/request.rs")).expect("read request.rs");
    let rust_flags = rust_flag_set(&rust_src, "pub fn is_supported_arg");

    let worker_src =
        std::fs::read_to_string(root.join("jobs/worker.py")).expect("read jobs/worker.py");
    let py_flags = python_worker_flag_set(&worker_src);

    let bootstrap_src = std::fs::read_to_string(root.join("bootstrap_pipeline.py"))
        .expect("read bootstrap_pipeline.py");
    let cli_flags = bootstrap_cli_flag_set(&bootstrap_src);

    // `sentry_smoke` is a standalone diagnostic, not a job parameter. `topic`
    // is consumed explicitly by both workers before the whitelist loop, so it
    // is not part of the "flags to forward" contract being compared here.
    let job_cli_flags: BTreeSet<String> = cli_flags
        .iter()
        .filter(|f| f.as_str() != "sentry_smoke" && f.as_str() != "topic")
        .cloned()
        .collect();

    let rust_forwardable: BTreeSet<String> = rust_flags
        .iter()
        .filter(|f| f.as_str() != "topic")
        .cloned()
        .collect();

    let missing_from_rust: Vec<_> = job_cli_flags.difference(&rust_forwardable).collect();
    assert!(
        missing_from_rust.is_empty(),
        "bootstrap_pipeline.py flags missing from request.rs::is_supported_arg: {missing_from_rust:?}"
    );

    let missing_from_py: Vec<_> = job_cli_flags.difference(&py_flags).collect();
    assert!(
        missing_from_py.is_empty(),
        "bootstrap_pipeline.py flags missing from jobs/worker.py::supported_args: {missing_from_py:?}"
    );

    let rust_only: Vec<_> = rust_forwardable.difference(&py_flags).collect();
    assert!(
        rust_only.is_empty(),
        "keys accepted by request.rs but not jobs/worker.py — update jobs/worker.py to match: {rust_only:?}"
    );

    let py_only: Vec<_> = py_flags.difference(&rust_forwardable).collect();
    assert!(
        py_only.is_empty(),
        "keys accepted by jobs/worker.py but not request.rs — update request.rs to match: {py_only:?}"
    );
}
