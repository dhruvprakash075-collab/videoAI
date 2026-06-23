# Phase 5-8 acceptance and STOP conditions

This branch (codex/phases-5-8) implements Plan 001 "Make the media pipeline
truthful and reliable" Phases 5 through 8, building on Phases 1-4. The goal is
a pipeline whose config, CLI flags, schema, and docs describe only behavior the
code actually runs - no dead backends, no dormant switches, no advertised
features that never execute.

The changes were authored with GitHub tooling that cannot apply diffs, so each
phase is delivered as a git-applicable patch under patches/. See
patches/README.md for the exact apply sequence.

## Status: UNVERIFIED

These patches have NOT been executed in this environment:
  - pytest was not run (no Python runtime with project deps available here).
  - ruff was not run.
  - No GPU / ComfyUI / Ollama smoke test was performed.

They were verified only for:
  - git apply --check success against the branch tree.
  - byte-level round-trip fidelity (git blob SHA) after each push.

Run the acceptance checks below in a real dev environment before merging.

## Acceptance checks

Run from the repo root after applying every patch in patches/README.md:

  1. Lint
       ruff check .
     Expect: no new errors introduced by these phases.

  2. Tests
       python -m pytest tests/ -q
     Expect: the suite collects and passes. Tests for deleted modules
     (FramePack, Layered V3, RVC, replicate/pexels) were removed, so there
     should be no import errors referencing them.

  3. Config / schema consistency
       python -c "from config.config_schemas import load_config; load_config()"
     Expect: config/config.yaml validates against config/config_schemas.py with
     no leftover keys for removed features (motion_engine, layered_v3, rvc,
     duplicate critic_* keys, checkpoint_interval).

  4. Grep for removed surfaces (should return nothing in source or tests):
       rg -n "skip_rvc|rvc_convert|RvcConfig|layered_v3|LayeredV3|motion_engine|framepack|_replicate|_pexels"
     Remaining hits are acceptable only in patches/, this document, and the
     pending manual edits listed below.

  5. Rust worker builds (if rust/ is part of CI):
       cargo build --manifest-path rust/worker/Cargo.toml
     Expect: the supported-argument filter no longer references skip_rvc.

## Manual edits still required (patches cannot carry these)

  - utils/preflight.py: delete the now-unused _check_layered_v3 function body.
    The patch removed only its registration line; the body has literal
    f-string braces a unified diff cannot carry.
  - audio/audio_proxy.py: delete the now-unused rvc_convert function body
    (between get_audio_duration and the engine capability profiles section).
    The patch removed only its __all__ export.
  - rules/common/code-tour.md: change the audio module-map entry from
    "TTS, RVC, SFX" to "TTS, SFX". That line uses a non-ASCII em-dash a unified
    diff cannot carry reliably.

## STOP conditions (do not auto-fix; escalate instead)

Stop and ask before changing anything if you encounter:

  - A ComfyUI workflow that posts to /upload/image and references an "image"
    field (a live image-to-image path, not dead code).
  - A Qwen loader that is NOT the standard LoadImage node (a custom loader may
    be load-bearing).
  - A saved project on disk that uses a feature recommended for deletion.
    Layered V3 was confirmed clear (no saved project depends on it); the other
    delete-recommended features must be re-checked against real project data
    before their removal is finalized.
  - Any change that would add a NEW runtime dependency. The plan only removes
    dead surfaces; introducing a dependency is out of scope.

## Phase summary

  - Phase 5: research routed through utils.researcher (single source of truth).
  - Phase 6: removed dead config switches; critic.enabled is now honored.
  - Phase 7a: removed dormant replicate/pexels image backends.
  - Phase 7b: removed dead FramePack i2v motion engine.
  - Phase 7c: removed dead Layered V3 composition module and residual refs.
  - Phase 7d: removed RVC voice conversion across pipeline, worker, dashboard,
    config, and tests.
  - Phase 8: this acceptance and STOP-conditions document.
