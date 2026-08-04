# Ponytail Debt Ledger

Harvested 2026-07-31, re-harvested 2026-08-02 (4 new markers, 3 drifted
line refs corrected). One row per `ponytail:` marker. `no-trigger` rows name
no explicit upgrade trigger — those are the silent-rot risk; revisit them on
any touch of the enclosing code.

## Production code

| Location | What was simplified | Ceiling | Upgrade trigger |
|---|---|---|---|
| agents/ui_state.py:53 | Global singleton registry (TUI + FastAPI + pipeline thread) | Per-run instances would isolate tenants | Multi-tenant UI ever needed |
| agents/hinglish_glossary.py:365 | `no-trigger` — English-orthography rules checked before letter walk | Order is heuristic, not score-based | Transliteration quality regressions reported |
| audio/audio_proxy.py:116 | IndicF5 calibrated against one known-good 9s ref | Single-reference calibration | New reference voice adopted |
| audio/indicf5_worker.py:39 | `no-trigger` — batch mode for Unicode/long text, one job per wrapper call | No batching API surface | Wrapper needs multi-job calls |
| audio/indicf5_worker.py:66 | Absolute batch targets may be ignored by some IndicF5 builds | Emitted frame count unreliable | Model build upgraded |
| audio/omnivoice_worker.py:37 | torch imported lazily in `_load_model`/`_set_seed`/patch install | Cold-start latency on first TTS call | Startup perf matters |
| audio/omnivoice_worker.py:98 | Patch installed lazily before omnivoice import | Same cold-start window | Startup perf matters |
| comfyui_nodes/video_ai_nodes/nodes.py:314 | `_lora_options` static snapshot at schema-registration time | Lora list frozen per process | Dynamic lora list required |
| core/outline_shaping.py:9 | Colliding aliases merged by max weight (module docstring) | Max-weight merge is heuristic until stable IDs | Stable alias IDs available |
| core/outline_shaping.py:112 | Positional aliases kept at max weight | Heuristic until stable IDs exist | Stable alias IDs available |
| core/pipeline_long.py:36 | Heavy imports deferred to `_ensure_init()` | First-run startup cost (CUDA/diffusers) | Import cost acceptable at CLI |
| core/pipeline_long.py:283 | Evict per phase (5/batch) instead of 1/batch | Each phase loads a different model set | Memory pressure per batch |
| core/pipeline_long.py:285 | No abort check between phases within a batch | Flag only checked at batch boundary | Fine-grained cancellation needed |
| core/post_production.py:362 | QC advisory — duration mismatch warning-only | Wrong duration passes through | QC must gate the pipeline |
| core/pre_production.py:218 | Empty default → base config `tts.engine` wins | Default resolution single-level | Layered config precedence needed |
| core/segment_runner.py:415 | Checkpoints predating the engine field lack it | Old checkpoints untyped | Checkpoint schema bump |
| core/segment_runner.py:820 | Task-wise phase helpers — phases strictly sequential, state passed via checkpoint exchange | No phase pipelining | Phase pipelining |
| core/segment_runner.py:1042 | memory_review loads Ollama in same phase as render | Sequential phase overlap | Phase pipelining |
| memory/memory.py:145 | Shared sanitizer reused for filename safety (inline `lower().replace()` let `/\:` through) | Topic safety depends on `_safe_filename` contract | Sanitizer rules change |
| utils/compatibility.py:56 | Dependency checks run during CLI/module startup | Startup cost for every module import | On-demand checks needed |
| utils/local_ui.py:793 | ComfyUI root path validation (no `..`, exists, is dir) | Manual string checks | Shared path-validation util |
| utils/local_ui.py:802 | ComfyUI python path validation | Same | Shared path-validation util |
| utils/local_ui.py:811 | ComfyUI workflow path validation | Same | Shared path-validation util |
| utils/utils.py:38 | `sys.stdout.reconfigure(errors="replace")` replaces SafeStream wrapper | Drops custom stream buffering | Stream behavior beyond encoding |
| utils/quality_check.py:139 | QC "FAIL" log word removed — advisory wording; `passed` stays strict in JSON | Log says "issues" while data says failed | QC ever gates the pipeline |
| video/image_gen/comfyui_runtime.py:156 | Foreign ComfyUI may hold a dead stdout pipe — every prompt revalidates | Per-prompt validation cost | Owned ComfyUI process |
| video/image_gen/panel_compositor.py:82 | Manga-panel overlap threshold 1.5% (was 3%) | Roboflow annotation calibration | Labeler/model change |
| video/image_gen/panel_compositor.py:93 | Overlap threshold 5% (was 2%) | Same | Labeler/model change |
| video/renderer/assembler.py:15 | Module-level state container (isolated for tests) | Global state per process | Multi-instance assembly |
| external/IndicF5/run_indic.py `_tail_trim` (gitignored, session log only) | Fixed-ratio budget cut, `pad=0.90` (was 1.05) to excise F5's tail-stretch "गई" head | Rarely clips a final syllable ("है") when the duration model overruns the budget; pad >0.92 reintroduces the filler | Word-aligned cut via the worker's whisper `words.json` timestamps |

## Tests

| Location | What was simplified | Ceiling |
|---|---|---|
| tests/conftest.py:21 | Manual live tests excluded from collection | Suite covers only automated paths |
| tests/test_audio_proxy_extended.py:267 | `_enqueue_stdout` fed one line then EOF | Enqueuer quiescence tested shallowly |
| tests/test_devanagari_translation.py:17 | Bootstrap skipped (venv guard would sys.exit) | No full-pipeline coverage here |
| tests/test_pipeline_long.py:107 | Asserts in-place list append | — |
| tests/test_preflight.py:20 | Aggregate preflight tests must not touch local services/hardware | No real-service coverage in suite | Opt-in live-service tests wanted |
| tests/test_post_production.py:439 | QC advisory — file still produced | Matches prod behavior |
| tests/test_video_ai_nodes_execution.py:739 | `fingerprint_inputs` optional | Only checkpoint/ksampler/portrait implement it |
| tests/test_youtube_uploader.py:33 | Stale module cache purged | Uploader statefulness untested |

## Rotten rows

- **hinglish_glossary.py:365** (no-trigger)
- **indicf5_worker.py:39** (no-trigger)
- **pipeline_long.py:36** (no-trigger)

38 markers, 3 with no trigger.
