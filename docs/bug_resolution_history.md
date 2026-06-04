# Bug Resolution History

This document consolidates confirmed historical bug fixes with references to live code. Fixes are tracked via the historical `B`-series IDs (B1–B40) and current `P`-series IDs.

> **Status**: All 78 historical P-series bugs have been successfully resolved (now 85 with P6-1..3, P7-1..4 from 2026-06-04 Bonsai migration), and legacy bug audit files (`BUGS.md` and `BUGS_AUDIT_2026-05.md`) have been deleted from the root workspace to keep the repository clean. New bug reports should be assigned the next sequential P-id.

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

## 5. Research Sources (Confirmed)

The v6 web research module (`utils/researcher.py`) uses exactly **3 sources**:
1. **Wikipedia REST API**
2. **Wikimedia REST API**
3. **RSS feeds**

These are configurable via `config.yaml` under the `research:` section. DuckDuckGo is **not** used in this codebase.
