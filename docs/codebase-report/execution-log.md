# Execution Log — every entry-point run, with verdict

Interpreter legend: **venv** = `.\venv\Scripts\python.exe` (Python 3.12.13,
the sanctioned interpreter); **system** = `python` on PATH (`C:\Python314`,
3.14.5 — outside the project's `>=3.10,<3.14` range, used only to expose
interpreter drift).

## Static gates

| Command | Result |
|---|---|
| `compileall` over all tracked .py (228 files) | exit 0 — no syntax errors |
| `python -m pytest tests` (system 3.14.5) | 2048 passed, 5 skipped, 1 warning, 26.43 s |
| `cargo test` (rust/worker) | 73 passed (49 lib + 23 main + 1 checkpoint_cli) |
| `cargo clippy -- -D warnings` | clean |
| `cargo fmt --check` | clean |

## Entry points (all at safe boundaries: --help / dry flags)

| Command | Interp | Result | Verdict |
|---|---|---|---|
| `bootstrap_pipeline.py --help` | system | venv-guard error, rc=1 | OK — guard works as designed |
| `bootstrap_pipeline.py --help` | venv | help printed | OK |
| `core/pipeline_cli.py --help` (as script) | venv | `ModuleNotFoundError: No module named 'core'` | BUG #4 (needs `-m`) |
| `python -m core.pipeline_cli --help` | venv | help printed | OK |
| `jobs/worker.py --help` / `-m jobs.worker --help` | venv | **hangs** — poll loop starts, no output, killed after 12 s | BUG #1 |
| `jobs/run_worker.py --help` | venv | `ModuleNotFoundError: No module named 'config'` | BUG #4 |
| `audio/omnivoice_worker.py --help` | system | help printed | OK |
| `audio/indicf5_worker.py --help` | system | help printed | OK |
| `audio/supertonic_worker.py --help` | system | help printed | OK |
| `scripts/check_environment.py` | system | failed (3.14 environment) | interpreter drift, BUG #12 |
| `scripts/check_environment.py` | venv | **8/8 passed** (FFmpeg 8.1.1, Ollama 9 models, VRAM 0.6/6.0 GB warning, disk 64 GB, dirs, config) | OK; VRAM low warning |
| `scripts/cleanup_artifacts.py --days-old 1` | system | listed artifacts, no delete | OK |
| `scripts/comfyui_smoke.py --help` | venv | **ran the real smoke**: live ComfyUI, frame produced `scene_01.png` 533 136 bytes, "SMOKE OK (1 new frame(s))" | BUG #2 — `--help` ignored, real workflow executed (accidental; passed, but crossed the no-real-tools boundary) |
| `tools/ab_compare_t2i.py --help` | system | help printed | OK |
| `tools/import_manga_panel_dataset.py --help` | system | help printed | OK |
| `tools/import_anime_face_dataset.py --help` | system | help printed | OK |
| `setup_youtube_profile.py --help` | system | help printed | OK |
| `utils/model_eval.py --help` | system | help printed | OK |
| `utils/diagnose.py --help` | venv | `[ERROR] Unknown diagnostic command: --help`, rc=1 | BUG #5 |
| `utils/preflight.py` (as script) | system | checks fail: `No module named 'config'` (ollama_ping, director_model); vram 2.6/6.0 < 4.5; playwright 2-command fallback works; net result: ok=3 fail=3 skip=2 | BUG #3 + env VRAM (BUG #13) |
| `utils/preflight.py` (as script) | venv | same `config` failures | BUG #3 confirmed interpreter-independent |
| `utils/local_ui.py --help` (as script) | venv | `No module named 'agents'`, rc=1 | BUG #14 |
| `python -m utils.local_ui --help` | venv | **`--help` ignored — live uvicorn on 127.0.0.1:8000, killed after 60 s, no listener left** | BUG #15 |
| `cargo run --help` (rust/worker) | — | rc=101 "could not determine which binary" | BUG #16 (no `default-run`) |
| `cargo run --bin videoai-worker -- --help` | — | rc=0, 12 subcommands | OK |
| `cargo run --bin videoai-worker -- doctor --json` (from rust/worker) | — | `bootstrap_pipeline` + `config_yaml` false "missing" (CWD-relative root); comfyui skipped "image backend is not comfyui" | BUG #17 |
| `cargo run --bin {audio_analyze,text_split,videoai_checkpoint} -- --help` | — | rc=0, proper clap help | OK |
| `validate_config(config.yaml)` (venv, pydantic 2.12.5) | venv | VALID, 22 sections | OK |
| workflows scan (6 JSON, config/comfyui/workflows) | — | all node classes resolve; seeds fixed (42×5, 0×1) | OK |
| `cargo run --manifest-path rust/worker/Cargo.toml -- list-jobs` (README.md:64-67 verbatim) | — | rc=101 "could not determine which binary" — README's own commands fail live | BUG #26 |
| config.yaml path cross-check (Test-Path batch: checkpoints, VAEs, LoRAs, reference images, IndicF5, ComfyUI, character_voices) | — | all present except `narration_ref_9s_mono24k.txt` (falls back to inline ref_text, audio_proxy.py:121-125) + `chrome_profile` (uploads disabled) | OK — both benign (BUG #33) |
| `python -m pytest tests` rerun sanity (config/CI round) | venv | 2048 passed, 5 skipped (matches execution-log:13) | OK |
| MCP `index_status` (codebase-memory v0.10.0) | — | 6561 nodes / 22671 edges, status ready | OK — graph usable |
| MCP `query_graph` aggregate Cypher (OPTIONAL MATCH + count) | — | **server crash** (pipe closed) | BUG #39 — use search_graph degree filters |
| MCP `search_graph` max_degree=0, exclude_entry_points | — | 272 functions + 48 methods; production hits grep-verified | OK — closed unused.md open item |

## Import smoke (all 228 tracked modules importable)

- venv: **all production modules import clean** — zero import breakage.
- system 3.14: `core/main.py` fails (`No module named 'crewai'` — not
  installed in system python). Tests pass anyway via conftest stubs.
  Confirms interpreter drift (BUG #12) rather than a code bug.

## Side effects created (be transparent)

- One real ComfyUI smoke ran (bug #2 above). Its output frame landed in a
  gitignored output dir — `git status` shows only `?? docs/codebase-report/`.
- One `jobs/worker.py --help` run started a poll loop; killed via
  Stop-Process. No jobs were queued, DB untouched (`studio_projects/jobs/`).
- One `python -m utils.local_ui --help` run started a uvicorn server on
  127.0.0.1:8000 (BUG #15); killed via Stop-Process, verified no listener
  remained. No state written.

## Not executed (per user boundary: no real models/tools)

- No Ollama generation, no ComfyUI *intended* run (except bug #2), no
  YouTube upload, no model eval, no cleanup deletes (`--days-old 1` only
  lists), no dataset imports, no video rendering, no whisper/faster-whisper.
- `--help`-safe entries verified; remaining tools not exercised beyond help.

## Fix-run gates

- **Clamp contract correction (post-fix review)**: the words_per_segment clamp
  change originally updated only DecisionRecord._clamp (100-400); the four
  Pydantic Fields (config_schemas.py:81, :97, :198, :705) still said ge=50,
  le=800, so the schema accepted 50-800 and _clamp silently coerced. Tightened
  all four Fields to ge=100, le=400 so schema and clamp agree. Verified: no
  user/project yaml carries out-of-range words_per_segment (only
  config.yaml:100); all test paths using words=50 (bootstrap_source, local_ui
  api, segment_runner tests) bypass the Pydantic Fields (raw dicts / untyped
  request) — suite still 1948 passed / 5 skipped.
 (2026-08-02, findings-cleanup execution)

| Command | Result | Verdict |
|---|---|---|
| `git status --porcelain` (pre-fix) | clean, 0 changes | OK |
| `python -m pytest tests` (baseline, pre-fix) | 2048 passed, 5 skipped, 1 warning, 32.51 s | OK — matches report baseline |
| Silent-failure 23-site re-sweep (read-only agent) | 0 true silent failures; 5 path drifts in bugs.md:79-89 citations (agents/ollama_client.py→utils/, utils/check_environment.py→scripts/, core/preflight.py→utils/, utils/main.py→core/, tests/test_no_broad_suppress.py→tests/unit/) — line numbers + behavior accurate, no fabrication | OK — audit claim holds |
| Bonus except-pass sweep (core/audio/video/agents/memory/utils) | all benign (omnivoice_worker:57, comfyui_client:266, assembler:334, critic:217, media_analyzer:63/68, main:138, segment_runner:251, pre_production:462, identity:108) | OK |
| `python -m pytest tests` (post-fix, Phase 1 complete) | 1948 passed, 5 skipped (user-run) | OK — matches fix plan (~100 test deletions) |
| `scripts/comfyui_smoke.py` (post-fix, helpers.py root resolution #21) | venv | `submitted: 2ac131a9-…`, `scene_01.png` 558 066 bytes, "SMOKE OK (1 new frame(s))" | OK — #21 verified live; smoke gate green (2026-08-02) |
| Phase 2 deletions | — | removed: comfy_test_err.txt, comfy_test_out.txt, color_output.png, fast_output.png, comfy_color_workflow.json, comfy_fast_workflow.json, director.db (8 MB, zero refs), jobs/_temp_content.txt, vendors/indicf5/; git rm: .gitlab-ci.yml, GITLAB_SETUP_INSTRUCTIONS.md, coverage_baseline.txt, task_plan.md, plans/README.md; model_eval/ untracked + ignored (.gitignore:154); Modelfile.* ×3 already gone | OK (2026-08-02) |
| Second-pass CLI gates (verification pass) | — | `jobs/worker.py --help` → **rc=1 ModuleNotFoundError: config** (argparse fix was incomplete — no path bootstrap); `jobs/run_worker.py --help` → **rc=1 ModuleNotFoundError: jobs** (same); `utils/diagnose.py --help` rc=0; `scripts/comfyui_smoke.py --help` rc=0 (no workflow run); `-m utils.local_ui --help` rc=0 | 2 BROKEN → fixed |
| Second-pass fixes | — | worker.py + run_worker.py: repo-root sys.path bootstrap added before first-party imports (same pattern as preflight.py:343); quality_check.py:139 "Quality check: FAIL" → advisory wording (no test asserted on the text; `passed` stays strict in JSON) | FIXED (2026-08-02) |
| Post-fix re-verify | — | `jobs/worker.py --help` rc=0 usage; `jobs/run_worker.py --help` rc=0 usage | OK |
| Re-audit pass (removed + rejected claims, 2026-08-02) | — | 19/20 claims re-verified correct; exceptions: sfx/thunder.wav orphaned after audio_fx deletion → deleted (gitignored `sfx/` :43, never tracked); series_1.yaml "config.py:26,62" anchors were fabricated (file is dynamic `--project` input only); audio_fx.enabled comment now states module deleted (enabled stays first key for P4-8 regex test); PONYTAIL-DEBT.md 37→38 (quality_check:139 row) | 2 leftovers fixed |

## Items 3/4/5/7/8/9 execution (2026-08-02 user-approved sweep)

| Item | Change | Verdict |
|---|---|---|
| #8/#9 disk deletes | `sfx/` (empty after thunder.wav) and `model_eval/` (untracked eval runs) removed from disk; both gitignored (`.gitignore:43,154`) so nothing tracked | DONE |
| #7 CI broken-ref fix + pin check | **Found: ci.yml typecheck job referenced `utils/retry_manager.py` and `tests/test_retry_manager.py` — both deleted in Phase 1, so GitHub CI mypy was failing on load.** Removed both dead refs. Added a lint-job step that verifies the vendored ComfyUI pin (`2cdaaf4a`) still resolves upstream (GitHub API) — a real drift-diff can't run in CI because `external/comfyui` is gitignored; this guards a stale/fabricated pin. | FIXED |
| #3 warning_count dedupe | `UIState.warning_count` had no independent writers (only `add_degradation` +++ and `reset_run`), so it always == `len(degradations)`. Removed the field; `post_production.py` now emits `len(_UIS.degradations)`. `degradations` is the single source of truth. | DONE (65 tests green) |
| #4 tier-4 dead-code sweep | Re-indexed graph (6159 nodes); degree-0 Function/Method sweep + per-candidate grep. 9 methods had zero production refs → deleted: ProjectStore.set_visual_lock/get_visual_lock/add_pose_variant/add_world_lore/get_world_lore, StoryStore.load_recent_context, PreflightCheck.is_ok (is_fail kept — live), CircuitBreaker.is_open, OllamaClient.get_resident_models. Pruned 6 matching test functions. False positives confirmed live: `@app.*` handlers, chained proxy/mixin methods, pydantic validators, thread targets. | 1942 passed / 5 skipped (= 1948 − 6 test deletions) |
| #5 make_process_segment cognitive refactor | Extracted module-level `_gather_memory_items` (deduped 3× block) and `_review_important_images` (the 125-line identity review peak, formerly `LocalGraphContext.do_important_image_review`). Signature + graph wiring + return contract unchanged; single caller intact. **Cognitive 358 → 267 (−25%)** per graph; function 1060 → 929 lines. | 106 segment_runner/pipeline tests green |
| Test order-dependence fix (found during #4) | `test_translate_hinglish_delegates_and_records_seg_on_failure` passed only because an earlier test imported pipeline_long (registering `UIState.add_degradation` as the degradation callback). Made the test self-contained (registers/restores the callback itself). | FIXED — order-independent now |
| Full gates | `pytest tests/` 1942 passed / 5 skipped (23.85s); ruff clean; compileall exit 0; config load + schema valid (refine_upscale under `image_gen:`, enabled); `cargo test` + `cargo clippy -D warnings` + `cargo fmt --check` all clean | ALL GREEN |

## Final deep-clean pass (2026-08-02, user-approved A + B)

| Item | Change | Verdict |
|---|---|---|
| Dead stubs (tracked) | `git rm realesrgan.pyi basicsr/__init__.pyi basicsr/archs/__init__.pyi basicsr/archs/rrdbnet_arch.pyi` — zero production references; their only consumer paths (image_gen.py:254, audio_fx.py) were deleted in Phase 1. `unused.md:26` claim corrected. | DONE |
| Gitignored disk residue | `logs/` (9 MB today), `tts_output/` (26 MB), `temp_srt_files/`, `custom_checkpoints_path/`, `vendors/`, `studio_checkpoints/temp/`, `studio_outputs/ab_test/segments`, `studio_projects/_one_time/*`, other empty test-residue dirs, `rust/{cache,temp_srt_files,tts_output,studio_*}`. | DONE |
| Kept (user decision) | `hf_cache/` (144 MB) — model cache kept to avoid re-download | KEPT |
| Untracked but useful (flagged) | `tests/test_outline_shaping.py` — tests `core/outline_shaping.py` shape_outline (committed); 2 tests pass; no other coverage exists. NOT committed (user said "ask first", not "commit it") — flagged for future. | FLAGGED |
| Gates | `pytest tests/` **1942 passed / 5 skipped**; ruff exit 0; config loads | ALL GREEN |
