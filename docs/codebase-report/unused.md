# Unused / Stale — 2026-08-02

## Tracked — delete candidates (verified, one line each)

- `.gitlab-ci.yml` + `GITLAB_SETUP_INSTRUCTIONS.md` — obsolete: remote is GitHub (`github-origin`); the GitHub twin was already deleted last session as obsolete; delete both.
- `coverage_baseline.txt` — zero references anywhere (no CI, no script, no doc); stale baseline; delete or regenerate.
- `model_eval/` — 14 dated eval runs (JSON + PNG + WAV, ~hundreds of files) committed to git; eval output belongs on disk, not in history; untrack + gitignore.
- `task_plan.md` — "Oversized Module Refactor" plan with 27 unchecked boxes, but the WS-1..5 sprint it tracks is reported complete; stale tracking artifact; reconcile or delete.
- `vendors/indicf5/` (`__init__.py`, `model.py`) — zero references in all 229 tracked .py and all configs; stale duplicate of the live `external/IndicF5` (config.yaml `tts.indicf5.root`); delete.
- `Modelfile.cra-guided` — zero references anywhere in code/config/docs; dead.
- `Modelfile.zephyr-writer`, `Modelfile.hermes-director` — stale local build recipes: `FROM C:\models\*.gguf`, all 3 target files absent on disk (verified False x3); models now come via `ollama pull hermes-director` (README.md:25); re-running `ollama create -f` fails. Regenerate or delete (bugs.md #35).
- `plans/README.md` — stale 2026-06-21 plan index (plans 001/002 TODO, written for "an executor with no access to the originating conversation"); same class as root `task_plan.md`; reconcile or delete.
- `.opencode/skills/planning-with-files/scripts/session-catchup.py` — tracked; the 229th .py absent from the usage.md table (pass-1 sweep excluded dot-dirs); opencode tooling script, not audited.

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
- `static/ab_picker.html` — mounted by `utils/local_ui.py:259`.
- `sfx/thunder.wav` — referenced by `audio/audio_fx.py:21` (module unwired; the asset goes with it, not separately dead).
- `projects/series_1.yaml` — loadable project data (`config/config.py:26,62`; `core/pipeline_cli.py --project`); used only when named on the CLI.

## Open item — RESOLVED (pass 5, MCP graph live)

- Function-level no-caller sweep: DONE via `search_graph` max_degree=0 +
  per-candidate grep. 272 degree-0 functions (251 tests, 13 dashboard, 4
  production: 3 FastAPI handlers + 1 tooling script — all false positives)
  + 7 degree-0 methods (3 __init__, 2 _cleanup_proc — false positives;
  **2 genuinely dead**: `get_temp_items`/`clear_temp_items`
  project_store.py:992-1000, and the `_call_ollama_streaming`/
  `_prewarm_ollama` chain llm_client.py:130-199 + llm_shims.py:50-54 —
  tests-only). See bugs.md #37. `query_graph` aggregate Cypher crashes the
  server (bugs.md #39); use `search_graph` degree filters.
