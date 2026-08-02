# Usage Audit — all 228 tracked .py files

Method: AST import-graph (`audit_usage.py`) + per-file verification of every
ORPHAN-CANDIDATE by grep (absolute + relative import forms) and reading the
import sites. `compileall` over all tracked files: exit 0 (no syntax errors).
Frontend `dashboard/` exempt per user instruction. Tests themselves were
audited too (test-audit.md).

Caveat: the AST scan counts top-level imports; relative/lazy imports were
re-verified by grep. False positives caught that way: the 8 `agents/director/*`
mixins (re-exported through `agents/director/__init__.py`, imported by
`director_agent.py:30-39`) and `agents/llm_client.py` (imported relatively at
`director_agent.py:50`).

## Production packages (non-test, non-entry)

| Module | Verdict |
|---|---|
| `agents/decision_engine.py` | USED — 1 prod importer (pipeline) |
| `agents/director/*.py` (8 mixins) | USED — re-exported via `agents/director/__init__.py` |
| `agents/director_agent.py` | USED — 9 prod importers |
| `agents/llm_client.py` | USED — `director_agent.py:50` (relative import) |
| `agents/ui_state.py` | USED — 6 prod importers |
| `agents/hinglish_glossary.py` | USED |
| `audio/audio_proxy.py` | USED — 6 prod importers |
| `audio/tts_alignment.py` | USED |
| `audio/audio_fx.py` | **UNWIRED** — 0 prod importers; only tests import it |
| `audio/indicf5_worker.py` | ENTRY (CLI) — ran OK (`--help`) |
| `audio/omnivoice_worker.py` | ENTRY (CLI) — ran OK |
| `audio/supertonic_worker.py` | ENTRY (CLI) — ran OK |
| `bootstrap_pipeline.py` | ENTRY (main) — venv guard works |
| `comfyui_nodes/video_ai_nodes/*` | USED — loaded by ComfyUI via sys.path (not by Python import); exercised end-to-end by the smoke gate |
| `config/config.py` | USED — 4 prod importers |
| `config/config_schemas.py` | USED |
| `core/decision_record.py` | USED |
| `core/director_memory.py` | USED |
| `core/main.py` | USED |
| `core/outline_shaping.py` | USED |
| `core/pipeline_cli.py` | ENTRY — runs only as `python -m core.pipeline_cli` (see bugs.md #4) |
| `core/pipeline_graph.py` | USED |
| `core/pipeline_long.py` | USED — 3 prod importers |
| `core/post_production.py` | USED |
| `core/pre_production.py` | USED — 4 prod importers |
| `core/preflight.py` | USED |
| `core/preview.py` | USED |
| `core/runtime/*` (abort, ollama, vram) | USED |
| `core/segment/*` (identity, retry) | USED — `core/segment/retry.py` supersedes `utils/retry_manager.py` |
| `core/segment_runner.py` | USED — 4 prod importers |
| `jobs/job_store.py` | USED — 3 prod importers |
| `jobs/worker.py` | ENTRY — runs; ignores `--help` (bugs.md #1) |
| `jobs/run_worker.py` | ENTRY — same issue |
| `memory/*` (blackboard, memory, permanent_memory, project_store) | USED |
| `scripts/check_environment.py` | ENTRY — ran OK (8/8) |
| `scripts/cleanup_artifacts.py` | ENTRY — ran OK (dry) |
| `scripts/comfyui_smoke.py` | ENTRY — runs; ignores `--help` (bugs.md #2) |
| `setup_youtube_profile.py` | ENTRY — ran OK |
| `style_resolver.py` | USED — `config/config_production.py:641` |
| `tools/*.py` (ab_compare_t2i, import_*_dataset) | ENTRY — ran OK |
| `utils/checkpoint.py` | USED |
| `utils/circuit_breaker.py` | USED — 3 prod importers |
| `utils/compatibility.py` | USED |
| `utils/concurrency.py` | USED |
| `utils/context_manager.py` | USED |
| `utils/crewai_breaker.py` | USED — 7 prod importers |
| `utils/critic.py` | USED |
| `utils/deep_merge.py` | USED |
| `utils/emotion_control.py` | USED |
| `utils/errors.py` | USED |
| `utils/local_ui.py` | ENTRY — as script: `No module named 'agents'` rc=1 (bugs.md #14); `-m` ignores `--help` and starts uvicorn :8000 (bugs.md #15) |
| `utils/model_eval.py` | ENTRY + 1 prod importer — ran OK |
| `utils/narration_sanitize.py` | USED |
| `utils/ollama_client.py` | USED — 5 prod importers |
| `utils/path_utils.py` | USED |
| `utils/preflight.py` | ENTRY + 3 prod importers — standalone run breaks (bugs.md #3) |
| `utils/quality_check.py` | USED |
| `utils/researcher.py` | USED |
| `utils/scene_director.py` | USED |
| `utils/sentry.py` | USED |
| `utils/seo_generator.py` | USED |
| `utils/shutdown.py` | USED |
| `utils/source_loader.py` | USED |
| `utils/source_splitter.py` | USED |
| `utils/specialized_models.py` | USED |
| `utils/story_planner.py` | USED |
| `utils/time_format.py` | USED |
| `utils/topic_researcher.py` | USED |
| `utils/url_security.py` | USED — 13 prod importers |
| `utils/utils.py` | USED — 7 prod importers |
| `utils/vision_cache.py` | USED |
| `utils/youtube_uploader.py` | USED |
| `utils/web_search.py` | **DEAD** — only tests import it; `agents/director/story.py:51` says it is deprecated |
| `utils/diagnose.py` | **DEAD ENTRY** — standalone CLI, not referenced by any launcher/doc; custom dispatch rejects `--help` (bugs.md #5) |
| `utils/media_analyzer.py` | **DEAD ENTRY** — 0 prod importers; the opt-in Rust native audio bridge (`_native_analyze_audio_wave`) is reachable only through this tool |
| `utils/retry_manager.py` | **DEAD** — only tests import it; superseded by `core/segment/retry.py` |
| `video/image_gen/comfyui_client.py` | USED |
| `video/image_gen/comfyui_runtime.py` | USED |
| `video/image_gen/comfyui_workflow.py` | USED |
| `video/image_gen/image_gen.py` | USED — 3 prod importers |
| `video/image_gen/ip_adapter.py` | **UNWIRED** — only tests import it |
| `video/image_gen/panel_compositor.py` | USED |
| `video/renderer/assembler.py` | USED — 2 prod importers |
| `video/renderer/renderer.py` | USED |

## Unwired / dead modules — human verdict (each site read)

1. **`audio/audio_fx.py`** — `mix_sfx`, `master_audio`, `apply_premium_voice_processing`
   have zero production callers. Config `audio_fx.enabled: true`
   (`config/config.yaml`) + schema exist, but no runtime path executes this
   module. Consequence: SFX mixing and premium voice processing are silently
   absent from the pipeline; the loudnorm part exists inline in
   `video/renderer/assembler.py` (its own 2-pass EBU R128), so only the
   mix/master parts are missing. Either a feature gap or dead code — either way
   the config promises behavior the code does not deliver.
2. **`video/image_gen/ip_adapter.py`** — `IPAdapterManager` never loaded in
   production. The `ip_adapter_ref` decision and `ip_adapter_scale` config
   exist, but character face-consistency via IP-Adapter never runs (the only
   prod reference is a review-metadata string, `core/segment_runner.py:738`).
3. **`utils/retry_manager.py`** — superseded; 0 prod importers.
4. **`utils/web_search.py`** — deprecated per `agents/director/story.py:51`;
   tests keep it alive.
5. **`utils/media_analyzer.py`** — CLI-only; no prod callers.
6. **`utils/diagnose.py`** — CLI-only; no launcher references it.

## Tests (109 files)

All test files import at least one prod module (no orphan test files). Full
verdicts and findings in test-audit.md.
