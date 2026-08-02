# Unused / Stale — 2026-08-02

## Tracked — delete candidates (verified, one line each)

- `.gitlab-ci.yml` + `GITLAB_SETUP_INSTRUCTIONS.md` — obsolete: remote is GitHub (`github-origin`); the GitHub twin was already deleted last session as obsolete; delete both.
- `coverage_baseline.txt` — zero references anywhere (no CI, no script, no doc); stale baseline; delete or regenerate.
- `model_eval/` — 14 dated eval runs (JSON + PNG + WAV, ~hundreds of files) committed to git; eval output belongs on disk, not in history; untrack + gitignore.
- `task_plan.md` — "Oversized Module Refactor" plan with 27 unchecked boxes, but the WS-1..5 sprint it tracks is reported complete; stale tracking artifact; reconcile or delete.

## Gitignored junk — safe to delete (not tracked, cleanup-candidate)

- `comfy_test_err.txt`, `comfy_test_out.txt` — leftover smoke-gate debug output.
- `color_output.png`, `fast_output.png` — ad-hoc ComfyUI test renders.
- `comfy_color_workflow.json`, `comfy_fast_workflow.json` — stale experiment workflows (canonical ones live in `config/comfyui/workflows/`).
- `director.db` — SQLite runtime DB at repo root; consider moving under a data dir.
- `jobs/_temp_content.txt` — stray temp file.

## Checked — NOT dead, keep (preempts false positives)

- `style_resolver.py` — imported by `agents/director/config_production.py:641` + `tests/test_style_resolver.py`.
- `basicsr/__init__.pyi`, `realesrgan.pyi`, `trafilatura.pyi`, `videoai_worker_native.pyi` — type stubs for lazy optional imports (`video/image_gen/image_gen.py:254`, `utils/source_loader.py:213`, `audio/audio_fx.py:41`); live contracts.
- `setup_youtube_profile.py` — referenced by `utils/youtube_uploader.py:65` + 10 tests.

## Open item

- Function-level no-caller sweep incomplete (MCP crashed); re-run
  `MATCH (f:Function) OPTIONAL MATCH (x)-[:CALLS]->(f) WHERE x IS NULL ...` when
  the graph server is back.
