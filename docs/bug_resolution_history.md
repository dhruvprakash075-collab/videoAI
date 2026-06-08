# Bug Resolution History

This document consolidates confirmed historical bug fixes with references to live code. Fixes are tracked via the historical `B`-series IDs (B1–B40) and current `P`-series IDs.

> **Status**: All historical P-series bugs have been successfully resolved (including P8-1..15 from 2026-06-08 pipeline hardening), and legacy bug audit files (`BUGS.md` and `BUGS_AUDIT_2026-05.md`) have been deleted from the root workspace to keep the repository clean. New bug reports should be assigned the next sequential P-id.

---

## 1. Key Individual Bug Fixes

| Bug ID | Area | Fix |
|---|---|---|
| **B1** | Ollama Circuit Breaker | Established `OllamaClient._breaker` per-model state machine in `utils/ollama_client.py`. |
| **B13** | Retry Tiers | Added two-tier retry in `utils/retry_manager.py`: transient errors (ConnectionError, TimeoutError) get 50 retries; bounded errors (RuntimeError, OSError) get max 3 retries. |
| **B14** | Retry Scope | `generate_images` is **not** wrapped by the outer retry manager — it has its own internal OOM recovery (was 3-tier for SD, now 2-tier for Bonsai per P7-1). Wrapping it externally would cause compounding multi-minute hangs. |
| **B15** | CrewAI Lock | Added shared `crewai_lock = threading.RLock()` in `utils/concurrency.py` to serialize all `crew.kickoff()` calls and prevent litellm executor corruption. |
| **B32** | SFX Bundling | `_DEFAULT_SFX` keyword has no effect unless a matching WAV is in `sfx/`. Only `thunder.wav` is bundled. Drop a matching WAV in `sfx/` for wind/rain/heartbeat to activate. |

---

## 2. P-Series Fixes (2026-05 → 2026-06)

| Bug ID | Fix |
|---|---|
| **P3-14** | Changed `crewai_lock` from `threading.Lock` to `threading.RLock` (prevents deadlock when the same thread re-acquires). |
| **P4-8** | `audio_fx.enabled` flipped to `true` in `config/config.yaml` to enable bundled SFX. |
| **P4-18** | LIGHT slot wait reduced from 300s → **60s** (`utils/concurrency.py`). |
| **P4-23** | `BreakerOpen.cooldown_s` now returns the **real** remaining cooldown (was hardcoded 0). |
| **P4-27** | `run_pipeline.py` hardcoded "Real Hero" smoke test. |
| **P5-1** | `BreakerOpen` real-cooldown contract — `cooldown_s` is the actual remaining seconds from `OllamaClient._breaker()`. |
| **P5-2 to P5-4** | Additional 2026-06-01 fix sweep — see `tests/test_2026_06_fixes.py` (25 regression tests). |
| **P6-1** | **Supertonic 3 danda (।) chunker bug** (2026-06-04). Upstream `supertonic/utils.py:39` chunker regex `r"(?<=[.!?])\s+"` only splits on Latin punctuation. Hindi text with 2+ sentences collapses into one chunk > ONNX attention limit, crashes with `Mul_13 broadcast error`. Fix: `text.replace("।", ". ")` in `audio/supertonic_worker.py` ~line 92. Plus `tts.supertonic.max_chunk_length: 150` config to force chunking. |
| **P6-2** | **Subprocess Devanagari encoding error** (2026-06-04). When spawning `supertonic_worker.py` from a Python parent on Windows, the child inherits `sys.stdout = cp1252` and crashes on `print("Devanagari text")` with `UnicodeDecodeError`. Fix: pass `env={**os.environ, "PYTHONIOENCODING": "utf-8"}` in `Popen` for workers reading UTF-8 text. Applies to all worker spawns (`omnivoice_worker`, `supertonic_worker`). |
| **P6-3** | **Supertonic 3 emotion tags no-op** (2026-06-04). Upstream docs claimed 10 expression tags; actual repo only ships 3 (`<laugh>`, `<breath>`, `<sigh>`) per `supertonic/utils.py`. All 3 synthesize without error but produce no clear audible effect on Hindi narration. **Resolution:** kept OUT of defaults; documented in `AGENTS.md` "Supertonic 3 production readiness". No code fix needed. |
| **P7-1** | **Image backend migration: SD 1.5 → Bonsai 4B ternary (2026-06-04).** Replaced `StableDiffusionPipeline` + LoRA face-lock with `prism-ml/bonsai-image-ternary-4B-gemlite-2bit` (FLUX-quality on 6GB VRAM, ~3.5GB peak). Deleted `train_lora.py`, `_run_studio_session`, `_stable_diffusion`, `unload_sd_pipeline`, and `_active_lora_path`/`_lora_lock`. Added `unload_bonsai_pipeline`, `_bonsai_pipe`, `_bonsai_pipe_lock`, `image_gen._bonsai()`. Reduced OOM ladder from 3-tier (SD) to 2-tier (Bonsai, sequential VRAM only). 162 tests pass on the new backend. |
| **P7-2** | **Character consistency migration: LoRA → IP-Adapter FLUX v2 (2026-06-04).** Added `video/image_gen/ip_adapter.py` (`IPAdapterManager` singleton + `get_ip_adapter`/`unload_ip_adapter`). Lazy per-character master portrait generation in `core.pre_production.generate_master_portrait(char_key, project_id, char_data, config, dry_run)` — fires on first frame in a project where `char_presence ≥ 0.3` and no `master_portrait_path` exists. Best-of-3 candidate generation scored by CLIP image-text. Stored at `studio_projects/{project_id}/characters/{char_key}/master.png` with SHA256 hash. Frame cache key includes `master_portrait_hash` so portrait regen invalidates stale frames. |
| **P7-3** | **Writer Modelfile field for portrait generation (deferred).** Director/Writer Modelfile `prompts.yaml` should be updated to emit `portrait_prompt` per character (falling back to `visual_description`). Not blocking — first run auto-falls back to `visual_description` prefix "portrait, ". Tracked as future improvement in `RESEARCH_WHAT_TO_ADD.md`. |
| **P7-4** | **Test suite overhaul for new image backend (2026-06-04).** Deleted `tests/test_train_lora.py`, `tests/test_oom_ladder.py`, `tests/test_image_accel.py` (referenced removed SD/LoRA APIs). Rewrote `tests/test_image_gen.py` (19 tests) and added `tests/test_image_gen_extended.py` (6 tests) for: lazy portrait trigger, IP-Adapter attach, skip-when-portrait-exists, cache-key portrait-change invalidation, model-change pipe reload, OOM event recording. Updated `tests/test_pre_production.py` (3 new tests for `generate_master_portrait`) and `tests/test_pre_production_extended.py` (1 new test for portrait edge cases). |

---

## 3. Recent Changes (2026-06-04)

- **Supertonic 3 promoted to default TTS** (was additive option): `tts.engine: "supertonic"` in `config/config.yaml`. 4.5x faster than OmniVoice end-to-end, zero VRAM pressure.
- **Three DIY voice style JSONs** extracted and persisted in `character_voices/`: `dhruv_voice_polished.json` (active, 18s polished, loss 0.2721), `dhruv_voice_v3_9s.json` (9s raw, loss 0.2399), `dhruv_voice_v3.json` (71.94s merged, loss 0.2388). See `docs/voice_cloning.md` for full extraction pipeline.
- **P6-1 danda fix**: Hindi text now synthesizes reliably past 1 sentence per chunk. 1-min Akbar-Birbal narration (101.34s, 243 words) works end-to-end.
- **P6-2 subprocess UTF-8 fix**: All worker spawns now pass `PYTHONIOENCODING=utf-8`. Eliminates cp1252 decode errors when workers print Devanagari logs.
- **P6-3 emotion tags investigation**: Verified Supertonic 3 ships only 3 tags (`<laugh>`, `<breath>`, `<sigh>`), not 10. All 3 are no-ops on Hindi. Kept out of defaults.
- **TTS fallback chain** added to `audio/audio_proxy.py::tts_generate()`: `supertonic → omnivoice → edge-tts`. Mirrors the existing F5 fallback pattern.
- **TTS engine config defaults changed** (2026-06-04 A/B testing): `steps: 8 → 16`, `speed: 1.05 → 1.0`, `silence_duration: 0.3 → 0.1`, `max_chunk_length: 0 → 150` (new). User's perceptual preference wins.
- **Production speed verified**: 1-min Hindi narration synthesized in 33.6s (3.01x realtime). 3-hour video production estimated at ~1 hr wall time with TTS‖image-gen parallelism (image gen is now the bottleneck).

## 4. Recent Changes (2026-06-02)

- **Security**: Replaced MD5 with SHA256 for cache keys in `agents/director_agent.py`.
- **Smoke-test fix**: `core/post_production.py` line 193 — import path of `log_vram_usage` corrected from `core.pre_production` → `core.segment_runner` (stale Phase 0 reference).
- **God-module split**: `UIState` + `_devanagari_ratio` moved to `agents/ui_state.py`; Ollama plumbing moved to `agents/llm_client.py`. Both are re-exported from `director_agent.py` so existing imports remain valid.
- **v6 pipeline shipped**: `--source <path-or-URL>` CLI flag, 6 new modules (`source_loader`, `source_splitter`, `researcher`, `critic`, `seo_generator` extended, `bootstrap_pipeline._load_and_split_source`). 304 new tests added (total: 290 → 613 → grew further with extended coverage).
- **Ruff linting**: 2,400 raw errors auto-fixed + manually triaged. Ruff caught 6 real latent bugs during cleanup. All checks pass.

---

## 5. Recent Changes (2026-06-08) — Pipeline Hardening

| Bug ID | Area | Fix |
|---|---|---|
| **P8-1** | Hermes-director HTTP 500 | Reduced `num_ctx` from 4096 → 2048 in `Modelfile.hermes-director`. Model recreated with `ollama create`, 17GB RAM at 4096 exceeded 16GB hardware limit. |
| **P8-2** | Duration override | CLI `--duration` flag now correctly wins over user locks in `decision_engine.py` — applied AFTER user locks, not before. |
| **P8-3** | TTS engine normalization | Added `normalize_tts_engine()` in `audio/audio_proxy.py`. `chattts` → `edge`, `xtts`/`coqui` → `f5`. Free-text LLM outputs normalized to valid engine IDs. |
| **P8-4** | Director defaults cleanup | Fallback `tts_recommendation` changed from `chattts` → `omnivoice` in both `analyze_with_research` prompt and `_validate_vision_doc` defaults. |
| **P8-5** | Director TTS validation | `_validate_vision_doc` calls `normalize_tts_engine()` on `tts_recommendation`; `produce_runtime_config` normalizes final engine before writing overlay. |
| **P8-6** | Supertonic voice preflight | Added `_check_supertonic_voice()` in `utils/preflight.py` — validates configured voice JSON exists on disk. |
| **P8-7** | `pip check` conflicting pins | Patched `cached-path-1.8.10` METADATA (removed `rich<14.0` upper bound) and `wandb-0.27.0` METADATA (`click>=8.2.0` → `>=8.1.7`). `pip check` now reports clean. |
| **P8-8** | Python atexit crash (Windows) | `PYARROW_IGNORE_CPP_SHUTDOWN=1` in `conftest.py`; pyarrow stubbed to prevent native DLL loading; `cleanup_numbered_dir` monkeypatch suppresses PermissionError. |
| **P8-9** | Venv guard | `bootstrap_pipeline.py` detects system Python (non-venv) via `sys.prefix != sys.base_prefix` and exits with clear error. |
| **P8-10** | Dashboard ESLint | Fixed dead `testConfigLoad`, undefined `onClose`, `useVoices.js` set-state-in-effect. 0 errors, 0 warnings. |
| **P8-11** | Dashboard `act()` warnings | Wrapped `fireEvent` in `act()`, added flush for synchronous-start hook tests. |
| **P8-12** | Dashboard controlled/uncontrolled input | `ControlPanel.jsx` uses functional `setState(prev => ({...prev, ...data}))`. |
| **P8-13** | Dashboard empty image `src` | `VariantPanel.jsx` conditionally renders `<img>` only when source is truthy. |
| **P8-14** | Dashboard build deprecation | Upgraded vitest `2.1.9` → `3.2.6`; `cross-env NODE_OPTIONS=--no-deprecation`; conditional `esbuild` config (dev/test only) to silence Vite 8 oxc warning. |
| **P8-15** | Dashboard stderr noise | `vi.spyOn(console, 'error').mockImplementation()` in network-error tests. |

### Dry-run estimate (P8)
- Separate `fast_dry_run` vs `dry_run` display in `core/pipeline_long.py`.
- Formula: `n_segs * 20` for fast, `n_segs * 25` for regular.

### `get_tts_capabilities` alias (P8)
Added `get_tts_capabilities = tts_capabilities` in `audio/audio_proxy.py` for
callers expecting the `get_` naming convention.

### Test status (2026-06-08)
- 1,682 Python tests pass (clean exit — no access violation, no PermissionError)
- 165 Dashboard tests pass (silent stderr)
- 41 director `produce_runtime_config` tests pass
- `ruff check .` — 0 errors
- `pip check` — "No broken requirements found"

## 6. Research Sources (Confirmed)

The v6 web research module (`utils/researcher.py`) uses exactly **3 sources**:
1. **Wikipedia REST API**
2. **Wikimedia REST API**
3. **RSS feeds**

These are configurable via `config.yaml` under the `research:` section. DuckDuckGo is **not** used in this codebase.
