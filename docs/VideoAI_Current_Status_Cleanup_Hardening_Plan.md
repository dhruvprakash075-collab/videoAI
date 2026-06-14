# Video.AI Cleanup and Hardening Plan - Current Status

Updated: 2026-06-14
Repository: `C:\Video.AI`
Branch: `codex/videoai-cleanup-hardening`

## Verdict

The cleanup plan is implementable in the current project, and the main implementation is now applied in this worktree. The plan must be treated as a code-aware engineering patch, not as blind find-and-replace instructions, because the live test suite and documentation had additional references beyond the original hand-off file.

## Current End State

- TTS runtime supports only `supertonic` and `omnivoice`.
- `supertonic` is the default TTS engine.
- Removed engine names such as `edge`, `f5`, and `indicf5` normalize to `supertonic`.
- Runtime dispatch no longer contains Edge, F5, or IndicF5 paths.
- F5 and IndicF5 worker files and the IndicF5 setup script are deleted.
- Dashboard voice controls expose only supported engines.
- Auto-upload is disabled by default.
- Image generation remains ComfyUI primary with Bonsai fallback.
- ComfyUI auto-start now resolves project-relative paths, uses the repo ComfyUI venv, uses the current `--disable-auto-launch` flag, and does not keep stdout/stderr pipes in memory.
- Bonsai fallback and master portrait direct use now unload after use to reduce VRAM residency.
- `run_pipeline.py` now delegates to the real CLI instead of ignoring arguments.
- `--fast-dry-run` now stays lightweight: no TTS, no ComfyUI/image generation, no final ffmpeg concat, and no optional per-segment memory-review LLM calls.
- Unattended Director prompts now use conservative defaults: `--yes` selects no web search and no scratch-story generation unless the user explicitly chooses those options interactively.

## Implemented Work

### Runtime

- Updated `audio/audio_proxy.py` to remove Edge/F5/IndicF5 aliases, direct calls, workers, shutdown hooks, and fallback paths.
- Kept direct OmniVoice dispatch and default Supertonic dispatch with OmniVoice fallback.
- Updated `audio/supertonic_worker.py` success payloads to include `word_timestamps`.
- Updated `core/pre_production.py` to recognize only supported TTS engines and unload Bonsai after master portrait generation.
- Updated `core/pipeline_long.py` to default worker fallback to `1` and remove deleted worker shutdown calls.
- Updated `core/pipeline_long.py` so `--fast-dry-run` uses dry-run finalization.
- Updated `core/segment_runner.py` task label from stale XTTS wording to generic `TTS`.
- Updated `core/segment_runner.py` so fast dry-run skips optional memory-review work.
- Updated `studio_tui.py` and `utils/preflight.py` for the reduced TTS engine set.
- Updated `run_pipeline.py` to honor CLI flags by delegating to `core.pipeline_long`.
- Updated `video/image_gen/comfyui_runtime.py` so auto-start uses absolute resolved paths, launches with UTF-8 environment settings, uses the current ComfyUI no-browser flag, and discards child stdout/stderr instead of holding subprocess pipes.
- Updated Director prompt ordering so unattended smoke runs do not accidentally load the scratch writer model.

### Config and Schema

- Removed `tts.f5`, `tts.edge`, `tts.indicf5`, and `tts.voice_profile.edge_*` from `config/config.yaml`.
- Disabled upload by default.
- Updated `config/config_schemas.py` so `tts.engine` accepts only `supertonic` or `omnivoice`.
- Removed dead TTS subconfig models and schema fields.
- Added/updated schema tests for removed-engine rejection.

### UI and Dependencies

- Updated dashboard voice engine choices and tests.
- Removed `edge-tts` from Python dependencies.
- Removed stale pyproject entries for deleted modules/scripts.
- Fixed the compatibility dependency check so installed `peft` is not reported missing just because importing it triggers an optional Torch path.

### Tests and Docs

- Updated focused tests that still expected Edge/F5/IndicF5 behavior.
- Deleted obsolete `tests/test_tts_engine_select.py`.
- Updated architecture/config/runtime docs that described removed fallback engines.
- Left historical changelog-style references alone where they describe past work rather than current runtime behavior.

## Verification Strategy

Do not run a monolithic `pytest` on this machine right now; it has already consumed too much RAM by retaining heavy imports across the whole process. Use focused or chunked verification:

```powershell
python -m py_compile agents/director_agent.py audio/audio_proxy.py audio/supertonic_worker.py config/config_schemas.py core/pipeline_long.py core/pre_production.py core/segment_runner.py studio_tui.py utils/preflight.py video/image_gen/image_gen.py
python -m pytest tests/test_audio_proxy.py tests/test_audio_proxy_extended.py tests/test_config_schemas.py -q
python -m pytest tests/test_pipeline_long.py tests/test_pre_production.py tests/test_pre_production_extended.py -q
python -m pytest tests/test_tts_alignment.py tests/test_image_gen.py tests/test_image_gen_extended.py -q
python -m pytest tests/test_director_agent_extended.py tests/test_director_agent_helpers.py tests/test_local_ui_api.py -q
python -m pytest tests/test_pipeline_long.py tests/test_segment_runner_helpers.py tests/test_compatibility_extended.py -q
cd dashboard
npm run test:run -- ControlPanel.test.jsx
```

For full-suite confidence on this 16 GB machine, use one test file per subprocess. The chunked run completed successfully on 2026-06-14 without RAM buildup:

```powershell
$log = "logs\pytest_chunked_20260614_122150.log"
Get-ChildItem tests -Recurse -Filter "test*.py" |
  Sort-Object FullName |
  ForEach-Object {
    .\venv\Scripts\python.exe -m pytest $_.FullName -q
    if ($LASTEXITCODE -ne 0) { throw "pytest failed: $($_.FullName)" }
  }
```

Additional verification completed:

- Chunked full-suite-equivalent run: passed, log at `logs\pytest_chunked_20260614_122150.log`.
- Real ComfyUI smoke: `.\venv\Scripts\python.exe -m pytest tests/test_comfyui_smoke.py --run-smoke -q --tb=short` passed with 2 tests.
- Director/auto-accept/Ollama safety slice: 252 tests passed.
- Fast dry-run smoke: `.\venv\Scripts\python.exe run_pipeline.py --topic "smoke test" --fast-dry-run --no-resume` exited with `Status: DRY_RUN`.

## Known Environment Blockers

- A real production run still requires Ollama running at `http://localhost:11434`.
- A constrained production smoke attempted on 2026-06-14 hung before video work in the scratch-story Ollama path. The root cause was unsafe `--yes` prompt ordering: it auto-selected web search plus scratch story. That has been patched so unattended defaults are conservative.
- Full test execution must be chunked by file on this machine to avoid one Python process retaining Torch/ComfyUI/diffusers-related imports across hundreds of tests.

## Acceptance Criteria

- `load_config()` succeeds with no removed TTS config keys.
- `normalize_tts_engine("edge")`, `normalize_tts_engine("f5")`, and `normalize_tts_engine("indicf5")` return `supertonic`.
- `tts_generate()` contains no Edge, F5, or IndicF5 dispatch path.
- Dashboard exposes only `supertonic` and `omnivoice`.
- Focused tests pass.
- Chunked pytest pass is acceptable in place of monolithic pytest on this RAM-limited workstation.
- Preflight reaches `TTS Engine 'supertonic'`.
- `.\venv\Scripts\python.exe run_pipeline.py --topic "smoke test" --fast-dry-run --no-resume` exits with `Status: DRY_RUN`.
- `.\venv\Scripts\python.exe -m pytest tests/test_comfyui_smoke.py --run-smoke -q --tb=short` passes when ComfyUI dependencies are available.
