//! Canonical set of `request_json` keys the job worker accepts.
//!
//! This is the single source of truth consulted by both the Rust worker CLI
//! (`main.rs::build_command`) and the native GUI's Create-Job form, so a flag
//! typo is rejected at submit time rather than minutes into a run (ui-bugs #15).
//! Keep in sync with `bootstrap_pipeline.py`'s accepted CLI flags — and with
//! `jobs/worker.py::supported_args`, which is the Python worker's copy of this
//! same list. `tests/gui_contract.rs` fails if the three drift apart.
//!
//! `sentry_smoke` is deliberately absent: it is a standalone diagnostic, not a
//! job parameter.

/// Keys allowed inside a job's `request_json` payload. Anything else is a typo
/// or an unsupported flag and must be rejected before enqueue.
pub fn is_supported_arg(key: &str) -> bool {
    matches!(
        key,
        "topic"
            | "duration"
            | "dry_run"
            | "no_resume"
            | "file"
            | "project"
            | "series"
            | "run_mode"
            | "eval_models"
            | "preview"
            | "skip_preflight"
            | "preflight_only"
            | "words_per_segment"
            | "images_per_segment"
            | "segment_count"
            | "yes"
            | "topics_file"
            | "source"
            | "no_storyboard"
            | "force_storyboard"
            | "force_vision"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_keys_are_supported() {
        assert!(is_supported_arg("topic"));
        assert!(is_supported_arg("segment_count"));
        assert!(is_supported_arg("images_per_segment"));
        assert!(is_supported_arg("run_mode"));
    }

    #[test]
    fn unknown_keys_are_rejected() {
        assert!(!is_supported_arg("segmentCout"));
        assert!(!is_supported_arg("topc"));
        assert!(!is_supported_arg(""));
    }

    #[test]
    fn storyboard_and_vision_flags_are_accepted() {
        // Regression: these are real `bootstrap_pipeline.py` flags that were
        // missing here, so a queued job asking for them had them dropped
        // silently (main.rs::build_command skips unsupported keys).
        assert!(is_supported_arg("no_storyboard"));
        assert!(is_supported_arg("force_storyboard"));
        assert!(is_supported_arg("force_vision"));
    }

    #[test]
    fn sentry_smoke_is_not_a_job_flag() {
        assert!(!is_supported_arg("sentry_smoke"));
    }
}
