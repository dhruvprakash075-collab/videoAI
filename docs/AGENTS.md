# AGENTS.md — Video.AI orientation for AI sessions

> **Last updated:** 2026-06-08 (venv guard, pyarrow stub, pip check, director TTS normalization, dashboard ESLint/act/controlled-input fixes)
> **Purpose:** Quick context for the next AI session. Read this BEFORE grepping.
>
> **ECC Integration:** This project now includes [Everything Claude Code](https://github.com/affaan-m/ECC) patterns.
> See `CLAUDE.md` for project instructions and `agents/` for agent definitions.
> See `rules/` for coding standards and guidelines.

## What this is

A local video-generation pipeline. Single operator, Windows 11, RTX 4050 6GB
VRAM, **Python 3.12.13** in `venv/`. Takes a topic → plans a story → writes
per-segment scripts → generates Hindi/Devanagari voice-over with **Supertonic 3
TTS + DIY voice clone** (OmniVoice / edge-tts as automatic fallbacks) →
**Bonsai 4B (ternary, gemlite 2-bit) images with IP-Adapter FLUX v2
character consistency** (lazy per-character master portraits, 2026-06-04)
→ Ken Burns MP4 with Devanagari subtitles. All local. No cloud.

## ECC Agents (from Everything Claude Code)

| Agent | Purpose | When to Use |
|-------|---------|-------------|
| `agents/planner.md` | Implementation planning | Complex features, refactoring |
| `agents/architect.md` | System design | Architectural decisions |
| `agents/code-reviewer.md` | Code review | After writing/modifying code |
| `agents/security-reviewer.md` | Security analysis | Before commits, new endpoints |
| `agents/tdd-guide.md` | Test-driven development | New features, bug fixes |
| `agents/python-reviewer.md` | Python-specific review | After Python code changes |
| `agents/performance-optimizer.md` | Performance analysis | When code is slow |

**Immediate agent usage (no user prompt needed):**
- Code changes → `code-reviewer`
- New features / bugs → `tdd-guide`
- Complex features → `planner`
- Architectural decisions → `architect`
- Before commits → `security-reviewer`

## ECC Rules

| Rules | Content |
|-------|---------|
| `rules/common/coding-style.md` | KISS, DRY, YAGNI, immutability, file organization |
| `rules/common/testing.md` | TDD workflow, AAA pattern, 80%+ coverage |
| `rules/common/security.md` | Secret management, security checks |
| `rules/common/performance.md` | GPU memory, caching, concurrency |
| `rules/common/patterns.md` | Circuit breaker, config-driven, atomic writes |
| `rules/common/agents.md` | Agent orchestration patterns |
| `rules/common/error-handling.md` | Custom exceptions, retry with backoff, user-facing errors |
| `rules/common/git-workflow.md` | Conventional commits, branching, PR workflow |
| `rules/common/docker-patterns.md` | Multi-stage builds, compose, security |
| `rules/common/deployment-patterns.md` | Rolling, blue-green, canary deployments |
| `rules/common/codebase-onboarding.md` | Codebase analysis and onboarding |
| `rules/common/ai-regression-testing.md` | AI regression patterns, test where bugs were found |
| `rules/common/github-ops.md` | Issue triage, PR management, CI/CD, releases |
| `rules/common/code-tour.md` | Persona-targeted codebase walkthroughs |
| `rules/common/eval-harness.md` | Eval-driven development, pass@k metrics |
| `rules/common/context-budget.md` | Context window usage audit, token savings |
| `rules/python/coding-style.md` | PEP 8, type hints, pathlib |
| `rules/python/testing.md` | pytest, mocking, coverage |
| `rules/python/security.md` | bandit, dangerous patterns |
| `rules/python/patterns.md` | Protocol, dataclasses, context managers |
| `rules/python/advanced-patterns.md` | EAFP, Protocol, exception chaining, generators, `__slots__` |
| `rules/python/testing-advanced.md` | Fixtures, parametrization, async testing, mocking |
| `rules/fastapi/patterns.md` | App factory, dependency injection, Pydantic schemas |
| `rules/fastapi/api-design.md` | REST API design, status codes, pagination |
| `rules/frontend/patterns.md` | React components, hooks, state management |

## ECC Slash Commands

| Command | Description |
|---------|-------------|
| `/plan` | Implementation planning — restate requirements, assess risks, create step plan |
| `/code-review` | Code review — local changes or GitHub PR |
| `/security-scan` | Security scan — secrets, dangerous patterns, vulnerabilities |
| `/test-coverage` | Test coverage — analyze gaps, generate missing tests |
| `/python-review` | Python-specific review — type safety, Pythonic patterns |
| `/quality-gate` | Quality pipeline — lint, tests, coverage, security |
| `/refactor-clean` | Dead code cleanup — safe deletion with test verification |
| `/build-fix` | Build error fixing — incremental fixes with guardrails |

## Verified ground truth (2026-06-08)

These are the live values; if a doc disagrees, **the live values win**:

| Item | Live value | Where |
|---|---|---|
| Backend test count | **1,644** passing, 0 skipped, 0 failing (16 warnings) | `pytest tests/ -q` | |
| Frontend test count | **165** passing, 0 failing (20 files, Vitest + RTL) | `cd dashboard && npm run test:run` |
| Frontend coverage | **96.04%** stmts, **93.48%** branches, **90.9%** funcs | `cd dashboard && npm run test:coverage` |
| Test runtime (backend) | ~3 min on warm cache | pytest output |
| Test runtime (frontend) | ~6 s (full suite) | `vitest run` |
| Test coverage (backend) | **22.4%** first-party (see `coverage_baseline.txt`) | `coverage run -m pytest; coverage report` |
| `performance.staged_loop` | **true** (C1 enabled) | `config/config.yaml:193` |
| `audio_fx.enabled` | **true** (only `thunder.wav` bundled) | `config/config.yaml:198` |
| `tts.omnivoice.num_step` | **16** (was 24) | `config/config.yaml:54` |
| `tts.engine` (active) | **supertonic** (default; unknown engines normalize to supertonic) | `config/config.yaml:31` |
| `tts.engine` options | `supertonic` (default), `omnivoice`, `f5`, `edge` (xtts/fish_speech removed — no code path) | `config/config_schemas.py:133` |
| `tts.supertonic.voice` (active) | `character_voices/dhruv_voice_polished.json` (DIY extract, 18s polished) | `config/config.yaml:36` |
| `tts.supertonic.steps` (active) | **16** (was 8 — A/B winner 2026-06-04) | `config/config.yaml:37` |
| `tts.supertonic.speed` (active) | **1.0** (was 1.05 — A/B winner 2026-06-04) | `config/config.yaml:38` |
| `tts.supertonic.silence_duration` (active) | **0.1** (was 0.3 — modern snappy) | `config/config.yaml:39` |
| `tts.supertonic.max_chunk_length` (active) | **150** (new — defense vs P6-1 danda bug) | `config/config.yaml:40` |
| TTS fallback chain | `supertonic → omnivoice → edge-tts` (in `audio_proxy.tts_generate`) | `audio/audio_proxy.py:540-620` |
| `script.words_per_segment` | **100** | `config/config.yaml:157` |
| `whisper_model` / `_final` | `tiny` / `base` | `config/config.yaml:187-188` |
| `loudnorm_two_pass` / `target_lufs` | `true` / `-14` | `config/config.yaml:203-204` |
| `image_gen.backend` (active) | **bonsai** (only backend as of 2026-06-04; was Stable Diffusion 1.5 + LoRA — P7-1) | `config/config.yaml:104` |
| `image_gen.bonsai_model` | `prism-ml/bonsai-image-ternary-4B-gemlite-2bit` | `config/config.yaml:105` |
| `image_gen.steps` / `guidance_scale` | **4** / **3.5** (Bonsai is distilled; more steps does not help) | `config/config.yaml:108-109` |
| `image_gen.ip_adapter_scale` | **0.8** (XLabs-AI/flux-ip-adapter-v2, P7-2) | `config/config.yaml:114` |
| Image gen peak VRAM | **~3.5 GB** on RTX 4050 6GB (sequential VRAM, no offload) | measured 2026-06-04 |
| Character consistency | IP-Adapter FLUX v2 + lazy per-project master portrait (best-of-3 + CLIP pick) | `core/pre_production.py:generate_master_portrait` |
| Python | 3.12.13 in `venv\` (NOT 3.14) | `venv/pyvenv.cfg` |
| Pytest | 9.0.3 | `pip list` |
| PyTorch | 2.11.0+cu128 | `pip list` |
| Git | **4 commits** (initial `3f7f4a3` + 3 fix commits) — `master` has v6 unified pipeline + CLI `--source` + latest 2026-06-08 fixes | `git log` |
| Backend linter | **ruff 0.15.15** (see `LINTING.md`). All checks pass. | `ruff check .` |
| Frontend linter | **ESLint 9 (flat config)** — see `dashboard/eslint.config.js`. All checks pass. | `cd dashboard && npm run lint` |
| CI | None (no `.github/`) | `Test-Path .github` |
| `tts.engine` aliases (removed) | `xtts`, `coqui` → f5; `chattts` → edge (no longer aliased) | `audio/audio_proxy.py` |
| Reviewer model | **qwen2.5:0.5b** (was script-reviewer Qwen2.5-3B) | `utils/specialized_models.py` |

## Read these first (in order)

1. **`../.kiro/steering/tech.md`** — tech stack, platform notes, command examples.
2. **`../.kiro/steering/structure.md`** — entry points, top-level layout, conventions.
3. **`../.kiro/steering/product.md`** — what the product is and who it serves.
4. **`../.kiro/steering/ai-tools-guide.md`** — how AI tools should be used here.
5. **`system_architecture.md`** — main system structure, modules map, and execution flow.
6. **`supertonic_pipeline.md`** — **Supertonic 3 TTS subsystem** (default engine, DIY voice clone, production speed math).
7. **`voice_cloning.md`** — **DIY voice JSON extraction** (recipes, switch command, re-extraction guide).
8. **`runtime_safety_guide.md`** — safety measures, VRAM/GPU evictions, circuit breakers, Bonsai OOM recovery ladder (2-tier, 2026-06-04), TTS worker subprocess safety.
9. **`testing_and_linting.md`** — pytest guidelines, coverage configuration, and Ruff setups.
10. **`configuration_reference.md`** — parameter details, prompting layouts, and visual presets.
11. **`bug_resolution_history.md`** — summary of historical fixes (B1–B40, P-series including P6-1..3 and P7-1..4 from 2026-06-04).
12. **`../config/config.yaml`** (292 lines) — actual ground truth for all config parameters. Always trust this file over doc claims.

## Recent changes (2026-06-03)

- **v6 unified pipeline shipped (CLI `--source` flag):** 6 new modules
  (`source_loader`, `source_splitter`, `researcher`, `critic`, `seo_generator`
  extended, `bootstrap_pipeline._load_and_split_source`). 304 new tests.
  Total: 290 → 613 → **1,745** (including extended coverage test modules added 2026-06-03).
- **Smoke-test bug fixes:** `core/post_production.py:209` `log_vram_usage`
  import path corrected; `bootstrap_pipeline._load_and_split_source` now
  forces `--segment-count` to match source chunk count when user didn't
  pass one (with mismatch warning on explicit override). 2 regression tests.
- **Initial git commit:** `3f7f4a3` — 287 files, 43.8 MB, 56,437 insertions.
  Repo-local identity only (`video-ai@local` / `Video.AI`).
- **Security fixes:** Replaced MD5 with SHA256 for cache keys in `agents/director_agent.py`.
- **Ruff:** All checks pass (0 errors).

### local_ui robustness (this session)
- Updated `utils/local_ui.py` to be more robust and thread-safe for the local dashboard:
  - `GET /api/status`: reads `UIState.logs` under `UIState._log_lock` when available (prevents concurrent read races).
  - `POST /api/ab/generate`: added best-effort VRAM/LLM safety before running SD generation by attempting `core.segment_runner.evict_ollama_models(...)` with a CUDA-cache fallback.
- Verified with:
  - `ruff check .`
  - `pytest tests/ -q`
  - Pipeline dry-run smoke test via `bootstrap_pipeline.py --skip-preflight --dry-run --topic "Real Hero" --yes`

### Dashboard refactor + Vitest suite (2026-06-03)
- **Code refactor** (pre-test, this session): 197-line `App.jsx` → 56 lines (CRAP 380 → 42);
  deleted `App.css` (184 LOC) + `static/od-variables.css` (404 lines); created
  `lib/{api,voiceFile}.js`, `hooks/{useStatusPolling,useScriptUpload,useVoices,useVoicePlayer,useABJob}.js`,
  and 10 sub-components (`Sidebar`, `Header`, `PreviewCanvas`, `ConsultationModal`,
  `SettingsDrawer`, `UploadZone`, `VoiceCard`, `VariantPanel`, `ToggleRow`, plus
  the existing `VoiceManager`/`ABPlayground`/`ControlPanel` slimmed down).
  Final fallow health: **90 A**, **1,220 LOC**, **0 dead files**, **0 duplication**,
  `npm run lint` clean, `npm run build` clean.
- **Vitest test suite added** (this session): 20 test files, **163 passing
  tests**, **0 failing**, **96.04% statement coverage** across `dashboard/src/**`.
  - Lib tests (`api.test.js` 7, `voiceFile.test.js` 12) — pure functions.
  - Hook tests (5 files, 42 tests) — async hook calls wrapped in `await act()`;
    polling tests use `vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })`
    so `setTimeout` and microtasks stay real for `waitFor`-style flushes.
  - Component tests (12 files, 91 tests) — RTL with mocked hooks/api.
  - App integration test (11 tests) — mocks `SettingsDrawer`/`ConsultationModal`
    to avoid `AbortSignal` class-mismatch between jsdom and Node fetch.
- **ESLint config updated**: `dashboard/eslint.config.js` now injects
  `globals.node` for `**/*.{test,spec}.{js,jsx}` and `src/test/**` so test files
  can use `global`, `globalThis`, etc. without `no-undef` errors.
- **Vite config**: top-level `esbuild: { jsx: 'automatic' }` is required for
  vitest to compile JSX without `React` in scope. Vite 8's prod build uses
  `oxc` and ignores the esbuild config (warning is expected and harmless).
- **Verified**:
  - `cd dashboard && npm run test:run` → 163 passed, 0 failed
  - `cd dashboard && npm run test:coverage` → 96.04% / 93.48% / 90.9% / 96.04%
  - `cd dashboard && npm run lint` → 0 errors
  - `cd dashboard && npm run build` → built in ~200ms, no errors
- See `docs/testing_and_linting.md` § 4 for the full Vitest inventory and
  the 9 pitfall rules (`AbortSignal` mismatch, `vi.spyOn(window, 'alert')` in
  `beforeEach`, fake-timer scope, async hook `act`, etc.).

### Supertonic 3 TTS integration (2026-06-03, this session)
- **Added** `audio/supertonic_worker.py` (one-shot + persistent `--serve`,
  mirrors the `omnivoice_worker.py` protocol). CPU-only ONNX, no VRAM pressure
  on the SD pipeline.
- **Wired** into `audio/audio_proxy.py`:
  - `normalize_tts_engine()` adds `supertonic` / `supertone` / `supertonic3` /
    `supertonic-3` aliases.
  - New `_call_supertonic_worker()` + `_SupertonicWorker` persistent manager.
  - `tts_generate()` dispatches to supertonic when `tts.engine == "supertonic"`.
  - `tts_capabilities()` entry registered (CPU, 0 VRAM, 31 languages).
  - Exported `shutdown_supertonic_worker()`.
- **Schema**: `config/config_schemas.py` — `tts.engine` Literal now includes
  `"supertonic"` (both `VisionDoc.tts_recommendation` and `TTSConfig.engine`).
  Added flat `supertonic_voice` / `supertonic_steps` / `supertonic_speed` /
  `supertonic_silence_duration` / `supertonic_max_chunk_length` fields
  (matches the `fish_speech_*` convention).
- **Config**: `config/config.yaml` — new `tts.supertonic:` block, now
  active defaults (2026-06-04):
  `voice: "character_voices/dhruv_voice_polished.json"`,
  `steps: 16`, `speed: 1.0`, `silence_duration: 0.1`, `max_chunk_length: 150`.
- **UI**: `dashboard/src/components/ControlPanel.jsx` `VOICE_ENGINES` array
  now lists Supertonic 3 between OmniVoice and Edge TTS.
- **Custom voice cloning** is done via the **free DIY path** using
  `external/supertonic_embed/` (third-party `kdrkdrkdr/supertonic.embed`,
  MIT, 24 stars, 6 forks). This tool reverse-engineers Supertonic's style
  encoder via gradient-based optimization against WavLM-Large perceptual
  loss — produces the same JSON format as Supertone's paid Voice Builder
  (one-time ~$5 purchase per voice). Total tool: ~200 LOC Python.
- **Why free, not paid:** Supertone's official Voice Builder is paid
  ("one-time, permanent purchase" — FAQ). The DIY tool produces an
  identical M1-compatible JSON (`style_ttl[1,50,256] + style_dp[1,8,16]`)
  by optimizing ~12,800 timbre parameters + 128 frozen rhythm parameters
  against WavLM Layer 3 speaker-identity loss. Same output format, same
  runtime, zero cost. **MIT license + no OpenRAIL-M restrictions on
  the extraction** (only the model itself is OpenRAIL-M, unchanged).
- **DIY pipeline** (one-time, ~2 min wall time on RTX 4050):
  1. `git clone --depth=1 https://github.com/kdrkdrkdr/supertonic.embed.git
     external/supertonic_embed`
  2. Copy Supertonic 3 ONNX assets: `Copy-Item -Recurse -Force
     C:\Users\dhruv\.cache\supertonic3\onnx external\supertonic_embed\onnx`
     and same for `voice_styles/`
  3. Copy target wav: `Copy-Item character_voices\narration_ref_9s_mono24k.wav
     external\supertonic_embed\wavs\dhruv.wav`
  4. Download WavLM-Large (3 files: `config.json`, `preprocessor_config.json`,
     `pytorch_model.bin` from
     <https://huggingface.co/microsoft/wavlm-large/tree/main>) to
     `C:\models\wavlm-large\`
  5. `$env:WAVLM_LOCAL_PATH = 'C:\models\wavlm-large'`
  6. `cd external\supertonic_embed; ..\..\venv\Scripts\python.exe
     optimize_style.py dhruv`
  7. `Copy-Item logs\dhruv\dhruv_final.json
     ..\character_voices\dhruv_voice_v3.json`
- **Script patches applied to upstream:**
  - Added `WAVLM_LOCAL_PATH` env var support to `load_wavlm()` so the 1.5 GB
    HF download can be skipped when the model is already on disk.
  - Verified Supertonic 3 ONNX (not just 2) is fully compatible — `helper.py`
    is shape-agnostic, all 4 ONNX models convert cleanly to PyTorch
    (91M params total, matches v3's 99M spec). M1 voice JSON format
    matches DIY tool output byte-for-byte.
- **Output**: `character_voices/dhruv_voice_v3.json` (280 KB). Same
  `style_ttl[1,50,256] + style_dp[1,8,16]` + `metadata` format as
  `~/.cache/supertonic3/voice_styles/M1.json`. Verified by loading and
  inspecting: identical schema, identical type=float32.
- **Auto-select preset**: the DIY tool compares the target WAV against
  all 10 built-in voices (M1-M5, F1-F5) using WavLM Layer 3 distance and
  picks the closest as the starting point for optimization. For
  `narration_ref_9s_mono24k.wav` (Hindi male narration) it picked an
  M-voice (expected — male voice).
- **Verified end-to-end** with the cloned voice:
  - English (worker direct): 3.55 s WAV, 313 KB, 0.8 s synthesis.
  - Hindi (worker direct): 3.62 s WAV, 320 KB, 0.8 s synthesis.
  - Full `tts_generate()` dispatch: 325 KB WAV, success.
  - DIY extraction: 271 steps, best loss 0.2399 (just under the 0.24
    same-speaker threshold), 103.4 s wall time on RTX 4050 Laptop.
  - Model auto-download: ~170 s first time, ~1 s warm cache.
- **Bug fixes during integration**:
  - `tts.synthesize()` returns `(wav_2d, junk_ndarray)` — wav has shape `(1, N)`
    (batch dim), real sample rate is on `tts.sample_rate` (not the tuple).
    Fix: `wav_1d = np.asarray(wav_2d).squeeze(); sf.write(path, wav_1d, tts.sample_rate)`.
  - `get_voice_style_from_path` param is `voice_style_path`, not `path`.
  - `character_voices/` auto-detect must only pass `.json` files to Supertonic
    (not `.wav`); added `Path(voice_sample).suffix.lower() == ".json"` guard.
  - One-shot error truncation: 300 → 2000 chars to surface real stack traces.
- **License**: Supertonic = MIT (code) + OpenRAIL-M (weights). Free for
  commercial use subject to OpenRAIL-M restrictions (no harm, no impersonation
  without consent, attribution required).

### Supertonic 3 production readiness + DIY voice selection (2026-06-04, this session)
- **DIY voice JSONs** — only the active default is on disk (`dhruv_voice_polished.json`,
  generic F1 placeholder). The other two (`dhruv_voice_v3_9s.json`,
  `dhruv_voice_v3.json`) were extracted but never committed — run the
  `external/supertonic_embed/` pipeline to regenerate:

  | JSON | Source audio | Loss | Best for |
  |---|---|---|---|
  | `dhruv_voice_polished.json` (285KB) — **ACTIVE** | Generic placeholder (F1 profile) | — | Replace with real extract |
  | `dhruv_voice_v3_9s.json` — missing | 9s raw auto-trim | 0.2399 | Backup if polished too "clean" |
  | `dhruv_voice_v3.json` — missing | 71.94s merged (18s real + 0.5s silence + 55s OmniVoice-synth) | 0.2388 | Empirical ceiling reference |

- **Empirical ceiling** (from the extraction run): more audio does not yield better loss.
  71.94s processed (0.2388) ≈ 9s raw (0.2399). The DIY optimizer's
  ~12,800-float style vector saturates around loss 0.24. So:
  - 9s is enough for production
  - Polishing 18s helps perceptual clarity, not loss
  - 18s polished was chosen over 9s raw on **listening** (A/B in
    `tts_output/3way_hindi/` and `tts_output/full_compare/`)

- **Polished-18s prep recipe** (`external/polish_18s.py`):
  - High-pass 80Hz (remove rumble)
  - Trim 225ms head + 27ms tail silence
  - Peak normalize to 0.95
  - 50ms fade in/out
  - **NO spectral denoise** (added artifacts — user vetoed)
  - 17.77s output, 1531KB, 24kHz mono

- **Danda fix (P6-1, 2026-06-04)** — `audio/supertonic_worker.py`:
  - `supertonic/utils.py:39` chunker regex is `r"(?<=[.!?])\s+"` — does NOT
    split on Devanagari danda `।`
  - Symptom: 2+ sentence Hindi crashes ONNX with
    `Mul_13 broadcast error` (multi-100-char chunk overflows attention)
  - Fix: `text = text.replace("।", ". ")` before `tts.synthesize()` call
  - Location: `audio/supertonic_worker.py` ~line 92
  - Combined with `max_chunk_length: 150` config, Hindi text up to ~1
    sentence per chunk is now reliable. 1-min Akbar-Birbal narration
    (101.34s, 243 words) synthesizes cleanly in 33.6s wall.

- **Fallback chain** — `audio/audio_proxy.py::tts_generate()` now tries
  `supertonic → omnivoice → edge-tts` in order. Mirrors the existing
  F5 fallback pattern. Exempt for tts_capabilities() reporting.

- **Worker subprocess encoding fix** — when spawning
  `supertonic_worker.py` from a Python parent on Windows, pass
  `env={**os.environ, "PYTHONIOENCODING": "utf-8"}` in `Popen` so the
  worker can `print()` Devanagari text without cp1252 decode errors.
  Root cause: `sys.stdout` in the child inherits cp1252 in PowerShell.

- **A/B test results driving the 2026-06-04 defaults**:
  - **Steps 8 vs 16 vs 24** (speed 1.05, 22.01s audio): user said 16 is
    best quality/speed tradeoff (8 = hissy, 24 = marginal gain)
  - **Speed 0.975 vs 1.0 vs 1.05** (silence 100ms): user picked 1.0
    (0.975 too slow, 1.05 too fast)
  - **Silence 50ms vs 100ms vs 300ms**: user picked 100ms (snappy modern)
  - **3 voice A/B** (`tts_output/3way_hindi/`): 9s ≈ 71.94s ≈ in user's
    ears, but polished 18s "no good just clear" (perceptual issue with
    HP filter on top of low bit depth) — kept polished anyway for
    cleanliness; can switch via config

- **Emotion tags tested** (Supertonic 3 supports 3: `<laugh>`,
  `<breath>`, `<sigh>`) — all synthesize without error but produce no
  clear audible effect on Hindi narration. **Kept OUT of defaults**.
  Future improvement: investigate stronger emotion control or
  hierarchical prosody markers.

- **Production speed verification** (2026-06-04):
  - Cold-start TTS comparison (identical 25.7s Hindi text):
    Supertonic 5.0s total (5.1x realtime) vs OmniVoice 22.8s total
    (1.2x realtime). Supertonic is 4.5x faster end-to-end.
  - 1-min Hindi narration: 101.34s audio in 33.6s synthesis
    (3.01x realtime) — verified with polished voice, steps 16, speed 1.0
  - 3-hour video feasibility:
    - TTS: 53 min wall (was estimated "hours" before — solved)
    - Image gen: 30 min wall (bottleneck) at 4 imgs/segment, 90 segments
    - NVENC encode: 8 min for 3hr @ 24fps 1080p
    - **Serial total: ~1.5-2.5 hr for 3-hour video; parallel: ~1 hr**
  - **Easy speedups**: enable DMD2 acceleration (50% image gen cut),
    parallelize TTS‖image-gen, drop `default_images_per_segment: 4` (not 8)

- **Supertonic 3 supported languages (31)**: hi, en, ko, ja, zh, es, fr, de,
  it, pt, ru, ar, bn, ta, te, mr, gu, kn, ml, pa, or, as, ur, fa, tr, vi,
  th, id, ms, fil, sw. Verify with
  `python -c "from supertonic import langs; print(langs)"`.

- **License caveat**: DIY extraction is MIT (no IP issue). The
  Supertonic 3 ONNX weights are OpenRAIL-M (responsible use only — no
  impersonation without consent, attribution required, no harm).

### Recent changes (2026-06-08)

- **Hermes-director HTTP 500 fix**: Reduced `num_ctx` from 4096 → 2048 in
  `Modelfile.hermes-director`; recreated model via `ollama create`. 17GB RAM
  at 4096 ctx exceeded 16GB hardware limit.
- **Duration override fix**: `--duration` CLI flag now correctly wins in
  `decision_engine.py` (flag applied AFTER user locks, not before).
- **TTS engine normalization**: Added `normalize_tts_engine()` in
  `audio/audio_proxy.py` — maps free-text LLM outputs (`chattts`, `xtts`,
  `coqui`) to valid engine IDs (`edge`, `f5`). `chattts` → `edge` alias
  ensures config default `supertonic` CPU / `omnivoice` GPU fallback safety.
- **Director defaults**: Fallback `tts_recommendation` changed from `chattts`
  → `omnivoice` in both `analyze_with_research` prompt and
  `_validate_vision_doc` defaults. xtts/coqui keyword mapping removed from
  `produce_runtime_config`.
- **Director TTS validation**: `_validate_vision_doc` calls
  `normalize_tts_engine()` on `tts_recommendation`; `produce_runtime_config`
  normalizes final engine before writing overlay.
- **Supertonic voice preflight**: Added `_check_supertonic_voice()` in
  `utils/preflight.py` — validates configured voice JSON exists on disk;
  displayed in summary as `supertonic_voice`.
- **`venv` guard**: `bootstrap_pipeline.py` detects non-venv Python via
  `sys.prefix != sys.base_prefix` and exits with
  `ERROR: This pipeline must run inside the project virtual environment.`
- **`pip check` clean**: Patched `cached-path-1.8.10` METADATA (removed
  `rich<14.0` upper bound) and `wandb-0.27.0` METADATA (`click>=8.2.0` →
  `>=8.1.7`). `pip check` now reports "No broken requirements found".
- **Python atexit crashes (Windows)**: Set `PYARROW_IGNORE_CPP_SHUTDOWN=1`
  in `conftest.py`; stubbed `pyarrow` module to prevent native DLL loading;
  monkeypatched `_pytest.pathlib.cleanup_numbered_dir` to suppress
  `PermissionError`. 1682 tests exit cleanly.
- **Dashboard ESLint**: 0 errors, 0 warnings — fixed dead `testConfigLoad`,
  undefined `onClose`, `useVoices.js` set-state-in-effect.
- **Dashboard `act()` warnings**: Wrapped `fireEvent` in `act()`, added
  `await act(async () => {})` flush to synchronous-start hook tests.
- **Dashboard controlled/uncontrolled input**: `ControlPanel.jsx` uses
  functional `setState(prev => ({...prev, ...data}))` so slider `value`
  never becomes `undefined`.
- **Dashboard empty image `src`**: `VariantPanel.jsx` conditionally renders
  `<img>` only when source is truthy.
- **Dashboard build deprecation**: Upgraded vitest `2.1.9` → `3.2.6` with
  `@vitest/coverage-v8`; added `cross-env NODE_OPTIONS=--no-deprecation` to
  test and build scripts; conditional `esbuild` config (dev/test only).
- **Dashboard stderr noise**: `vi.spyOn(console, 'error').mockImplementation()`
  in network-error tests — expected error messages suppressed.
- **Dry-run estimate**: Separate `fast_dry_run` vs `dry_run` display;
  formula `n_segs * 20` for fast, `n_segs * 25` for regular.
- **`get_tts_capabilities` alias**: Added in `audio_proxy.py`
  (`get_tts_capabilities = tts_capabilities`).
- **Ruff**: All checks pass (0 errors). Fixed import order, used
  `contextlib.suppress` in `conftest.py`.
- **Tests**: 1682 Python pass (clean exit), 165 Dashboard pass (silent stderr),
  41 director `produce_runtime_config` pass.

## Critical rules (DO NOT BREAK)

- **Run through `bootstrap_pipeline.py`**, never `python -m core.pipeline_long`
  directly. Bootstrap applies compat patches (UTF-8 console, rich Win32, FFmpeg
  PATH, CrewAI telemetry off, `OPENAI_MAX_RETRIES=0`), runs a **preflight**
  readiness check (Ollama ping, VRAM, disk, ffmpeg), and registers a
  **graceful shutdown** handler that evicts Ollama models on Ctrl-C.
- **Only ONE model in VRAM at a time.** Ollama models must be force-evicted
  (`keep_alive=0`) before any GPU task (SD, TTS). `evict_ollama_models()`
  in `core/segment_runner.py` does this and polls until VRAM is free.
- **Serialize ALL CrewAI `kickoff()`** through `utils.concurrency.crewai_lock`
  (an **RLock**, not a Lock — P3-14). And wrap each call in
  `utils.crewai_breaker.guarded_crewai_kickoff(crew, model_name, ...)` —
  without it, a hung litellm backend blocks the pipeline for minutes.
- **Use `global_scheduler.task("heavy", ...)`** for any GPU work (SD, TTS).
  HEAVY slot = 1 (1800s wait), LIGHT slot = 16 (60s wait; was 300s — P4-18).
- **All config changes go in `config/config.yaml`**, not in Python. Add a
  matching Pydantic field in `config/config_schemas.py`.
- **All paths are `pathlib.Path`**, no POSIX assumptions.
- **Atomic writes only** (temp + replace) for any persisted JSON.
- **`tests/conftest.py` autouse-resets `UIState`** between tests. If you add a
  test that needs pristine state, you don't need to reset it yourself — but if
  you add a NEW `UIState` class attribute, you MUST add it to `conftest.py`
  (otherwise it bleeds between tests).

## Pipeline entry points

| Entry | Use it for |
|---|---|
| `bootstrap_pipeline.py` | **Primary CLI.** Patches + preflight + args + calls `run_long_pipeline`. Supports `--skip-preflight` and `--preflight-only`. **v6**: `--source <path-or-URL>` for upload-source mode (.txt/.md/.pdf/.docx). |
| `studio_tui.py` | Operator TUI (Textual). `venv\Scripts\python.exe studio_tui.py` |
| `run.bat` | Windows menu launcher: UI / CLI / Tests. Has the TUI fallback. |
| `run_pipeline.py` | Hardcoded `"Real Hero"` smoke test (P4-27). |
| `utils/local_ui.py` | FastAPI backend for the React dashboard (port 8000). |
| ~~`train_lora.py`~~ | **REMOVED 2026-06-04** (P7-1) — character consistency is now via IP-Adapter FLUX v2 referencing per-character master portraits. |

**Planned consolidation:** The plan to merge the entry-point scripts into `pipeline.py` + `testpipeline.py` was rejected as the scripts serve different audiences (CLI / TUI / web UI / smoke test / Windows launcher) and are better kept separate. The consolidation work that actually happened was file-system cleanup of root-level orphan test artifacts.

**`TUI.bat` does NOT exist**. Use `run.bat` (or `studio_tui.py` directly).

The only first-class launcher is `run.bat`. `bootstrap_pipeline.py` and
`studio_tui.py` are the only first-class entry points.
`bootstrap_pipeline.py` are first-class. Don't write a new one without
deleting an old one.

### Bonsai 4B + IP-Adapter migration (2026-06-04, this session)

- **Image backend migration: SD 1.5 → Bonsai 4B ternary** (P7-1). Replaced
  `StableDiffusionPipeline` + LoRA face-lock with
  `prism-ml/bonsai-image-ternary-4B-gemlite-2bit` (FLUX-quality on 6GB
  VRAM, ~3.5 GB peak). Deleted `train_lora.py`, `_run_studio_session`,
  `_stable_diffusion`, `unload_sd_pipeline`, `_active_lora_path`,
  `_lora_lock`. Added `unload_bonsai_pipeline`, `_bonsai_pipe`,
  `_bonsai_pipe_lock`, `image_gen._bonsai()`. Reduced OOM ladder from
  3-tier (SD) to 2-tier (Bonsai, sequential VRAM only). Default
  `steps=4`, `guidance_scale=3.5`, no negative prompt. No
  `enable_model_cpu_offload()` — peak is only ~3.5 GB on 6 GB card.

- **Character consistency migration: LoRA → IP-Adapter FLUX v2** (P7-2).
  Added `video/image_gen/ip_adapter.py` (`IPAdapterManager` singleton
  + `get_ip_adapter`/`unload_ip_adapter`). Lazy per-character master
  portrait generation in
  `core.pre_production.generate_master_portrait(char_key, project_id,
  char_data, config, dry_run)` — fires on first frame in a project
  where `char_presence ≥ 0.3` and no `master_portrait_path` exists.
  Best-of-3 candidate generation scored by CLIP image-text. Stored at
  `studio_projects/{project_id}/characters/{char_key}/master.png` with
  SHA256 hash. Frame cache key includes `master_portrait_hash` so
  portrait regen invalidates stale frames. Dominant character per
  frame (max weight ≥ 0.3) gets the IP-Adapter reference; secondary
  characters get prompt description only.

- **Test suite overhaul** (P7-4). Deleted `tests/test_train_lora.py`,
  `tests/test_oom_ladder.py`, `tests/test_image_accel.py` (referenced
  removed SD/LoRA APIs). Rewrote `tests/test_image_gen.py` (19 tests)
  and added `tests/test_image_gen_extended.py` (6 tests) for: lazy
  portrait trigger, IP-Adapter attach, skip-when-portrait-exists,
  cache-key portrait-change invalidation, model-change pipe reload,
  OOM event recording. Updated `tests/test_pre_production.py` (3 new
  tests for `generate_master_portrait`) and
  `tests/test_pre_production_extended.py` (1 new test for portrait
  edge cases). 162 tests pass on the new backend.

- **Project store extensions** to `memory/project_store.py`:
  `log_character(..., portrait_prompt="")` + new helpers
  `set_master_portrait` / `get_master_portrait_path` /
  `get_master_portrait_hash` / `set_portrait_prompt`. SHA256 used
  for cache invalidation (not mtime) — more robust across
  WSL/Docker/Windows ACLs.

- **Writer Modelfile update deferred** (P7-3). Director/Writer
  Modelfile `prompts.yaml` should be updated to emit `portrait_prompt`
  per character. Not blocking — first run auto-falls back to
  `visual_description` prefix "portrait, ". Tracked in
  `RESEARCH_WHAT_TO_ADD.md` Tier 3.

- **Verified**: `ruff check .` 0 errors, all 162 tests in the touched
  test files pass, no production-code references to `train_lora` /
  `_run_studio_session` / `unload_sd_pipeline` /
  `_stable_diffusion` (LoRA fully removed).

- **Cost**: 1 module deleted (`train_lora.py`), 1 module created
  (`video/image_gen/ip_adapter.py`), 3 test files deleted
  (`test_train_lora.py`, `test_oom_ladder.py`, `test_image_accel.py`),
  2 test files rewritten (`test_image_gen.py`,
  `test_pre_production.py`).

- **Tools**: `tools/ab_compare_t2i.py` retained (deliberately — it's
  the A/B benchmark for SD 1.5 vs Bonsai; not production code).

## Post-refactor module map (June 2026)

### `core/`
- `pipeline_long.py` (692) — thin orchestrator + re-exports. **Re-exports many
  private names** for backward compat (`_sanitize_narration`, `_evict_ollama_models`
  (re-exported as both `evict_ollama_models` and the old name), `process_segment`,
  `make_process_segment`, etc.). Do NOT delete the re-exports without grepping
  for importers in `bootstrap_pipeline.py`, `studio_tui.py`, `tests/`.
- `pre_production.py` (992) — Director phase. **2026-06-04:** added
  `generate_master_portrait(char_key, project_id, char_data, config, dry_run)`
  (lazy per-character master portrait gen for IP-Adapter) +
  `_score_with_clip` (best-of-3 candidate picker) +
  `_record_portrait_to_store` (SHA256 + path write). Replaced
  `_run_studio_session` (LoRA training) — see P7-1.
- `segment_runner.py` (899) — per-segment loop. Contains
  `make_process_segment(...)` (the closure-builder). **v6 Phase 4:** `make_process_segment(...,
  source_chunks=None)` — when set, segments 1..N receive `state["source_chunk"] = source_chunks[i-1]`,
  which short-circuits the writer LLM call in `write_script_node` and auto-approves
  the critic (verbatim source, no 5-dim rubric).
- `pipeline_graph.py` — **v6 Phase 4:** added `source_chunk: Any` to `SegmentState`
  TypedDict. Wiring point for the per-segment writer-bypass.
- `post_production.py` (311) — final assembly (concat, thumbnail, manifest, QC).
  **2026-06-02 smoke-test fix:** line 209 import of `log_vram_usage` corrected to
  `core.segment_runner` (was `core.pre_production` — stale Phase 0 ref).
- `main.py` (207) — CrewAI agent factory (`create_director`, `create_writer`).
- `crewai_breaker.py` (214) — `guarded_crewai_kickoff(crew, model_name,
  timeout_s=240, lock=None)`. Raises `BreakerOpen(model, cooldown_s)` when the
  per-model breaker is OPEN. Reuses `OllamaClient._breaker()` so Ollama + CrewAI
  calls share state. `cooldown_s` is the **real** remaining cooldown (P5-1
  fix — was hardcoded 0).
- `concurrency.py` (110) — `global_scheduler` + `crewai_lock` (RLock). Light
  slot wait is **60s** (P4-18 fix; was 300s).
- `ollama_client.py` (340) — B1 per-model circuit breaker. Has
  `cooldown_remaining_s()` (added 2026-06) for callers that need the real
  remaining cooldown, not 0.
- `preflight.py` — startup readiness checks (Ollama ping, VRAM, disk, ffmpeg).
  Called automatically by `bootstrap_pipeline.py` before any pipeline work.
  Skip via `--skip-preflight`, or run standalone via `--preflight-only`.
- `shutdown.py` — graceful shutdown signal handlers. Wires SIGINT/SIGTERM/SIGBREAK
  to a cleanup chain (Ollama evict on Ctrl-C). Register custom hooks via
  `register_cleanup_hook(fn)`.
- `source_loader.py` (~310) — **v6 Phase 1.** Loads `.txt`/`.md`/`.pdf`/`.docx`/URL/paste
  into `SourceDocument`. Lazy-imports pypdf/python-docx/trafilatura.
- `source_splitter.py` (~344) — **v6 Phase 2.** `split_source(source_doc, n_segments, pf_config)`
  returns `list[SegmentChunk]`. 3 strategies: `by_chapter` (MD `#/##` + DOCX `Heading 1/2`),
  `by_word_count` (Devanagari-aware sentence split), `by_llm` (writer model, falls back to
  `by_word_count` on LLM failure). Default strategy: `by_word_count`.
- `researcher.py` (~280) — **v6 Phase 3.** `research_topic(topic, config)` returns
  `list[ResearchItem]`. 3 sources: Wikipedia REST + Wikimedia REST + RSS. Budget cap
  (default 3), per-source limit, word-overlap dedup. User-Agent required (Wikimedia ToS).
- `critic.py` (~270) — **v6 Phase 4.** Writer self-critique. 5-dim rubric
  (Hook/Emotional-arc/Pacing/Retention/TTS-friendliness × 20pts = 100, approved at ≥60).
  `score_script()`, `rewrite_script()`, `critique_and_rewrite()`. Auto-approves on
  LLM failure (graceful degradation) and on `source_chunk` (verbatim source, no rubric).
- `seo_generator.py` (~345, was 61) — **v6 Phase 5.** `SEOMetadata` TypedDict with
  8 fields. Source-path aware: accepts `source_document` + `research_items` kwargs.
  Deterministic local fallback (no LLM call) derives tags from topic + outline + research
  titles, hashtags from tags (top 5), chapters from outline (deterministic time dist).
  Devanagari matra preservation via `unicodedata.category`. Never raises (graceful
  degradation so SEO failure can't break upload).
- `specialized_models.py` (375) — **NOT** on B1 breaker yet; has its own
  urllib loop. Low priority (image-engineer degrades gracefully).
  **Pre-existing stale-bug:** `extract_world_state()` at line 298 still references
  the removed `script-reviewer` model — non-fatal warning fires on every memory
  write when `memory.llm_world_state: true`. Phase 0 cleanup miss; Tier 2 fix.
- `story_planner.py` (361), `context_manager.py` (270) — both use
  `guarded_crewai_kickoff`.
- `agents/`
- `director_agent.py` (2218 lines, 85.7 KB) — `DirectorAgent`. 2026-06-02 split:
  `UIState` and `_devanagari_ratio` moved to `ui_state.py` (106 LoC);
  LLM client methods (`_call_ollama*`, `_prewarm_ollama`, `_resolve_model`,
  `_ollama_opts`) moved to `llm_client.py` (149 LoC) as `DirectorLlmClient`.
  Both are re-exported from `director_agent.py` so existing imports keep
  working. Further splitting (research, consultation, runtime config,
  story) is a future refactor.
- `ui_state.py` (106 LoC) — `UIState` class + `_devanagari_ratio` helper.
- `llm_client.py` (149 LoC) — `DirectorLlmClient`. Owns the raw Ollama
  plumbing (urllib streaming, retry, model resolution) so the Director
  focuses on creative logic.
- `decision_engine.py` (201) — `DecisionRecord` authority model.
  Hierarchy: `default < director < writer < user / cli_flag`.

### `video/`
- `image_gen/image_gen.py` (650) — **2026-06-04 rewrite:** only image
   backend is now Bonsai 4B (gemlite 2-bit, ternary). Public surface:
   `generate_images(prompts, output_dir, config, char_presence=None, project_id=None)`, `unload_bonsai_pipeline()`,
`get_oom_report()`, `_prompt_cache_key(...)`,
   `_resolve_dominant_char(...)`, `clear_oom_events()`.
   2-tier OOM ladder (Tier 1 = 4 steps, Tier 2 = `max(2, steps*0.5)`,
  then skip+log+record event). Lazy per-character master portrait
  trigger fires before the per-frame loop.
- `image_gen/ip_adapter.py` (220) — **NEW 2026-06-04.** `IPAdapterManager`
  singleton (process-wide; one manager attaches to whichever
  pipeline `_bonsai()` loads). Public: `get_ip_adapter()`,
  `unload_ip_adapter()`. Loads `XLabs-AI/flux-ip-adapter-v2` and
  applies it to the active Bonsai pipeline. Pre-encodes master
  portrait embeddings for `char_key → tensor` lookup; falls back to
  per-frame `ip_adapter_image=` if the pipeline has no
  `encode_image()` method.

## Tests

**Backend — 1,682 tests, run with:**
```powershell
venv\Scripts\python.exe -m pytest tests/ -q
# 1682 passed, 0 skipped, 16 warnings in ~3min (clean exit, no PermissionError)
```

**Frontend — 165 tests (dashboard), run with:**
```powershell
cd dashboard
npm run test:run         # single-shot (Vitest 3.2.6)
npm run test:coverage    # v8 coverage report
# 165 passed, 0 failed, ~6s
```

Warnings are from `torch.jit.script_method` deprecation and `pydub`'s `audioop`
import — harmless. Dashboard tests have zero stderr noise (expected console
errors suppressed via `vi.spyOn`).

> **Windows note**: The old `PermissionError: [WinError 5]` about `pytest-current`
> cleanup is now suppressed via `cleanup_numbered_dir` monkeypatch in
> `tests/conftest.py`. No more noisy exit messages.

Test files (40 `test_*.py` modules + 2 `manual_integration_test_*.py`):
- `test_ollama_client.py` — B1 breaker state machine.
- `test_2026_06_fixes.py` (25 tests) — regression coverage for the 2026-06-01
  fix sweep (P5-1..4, P4-8, P4-23, BreakerOpen real-cooldown contract).
- `test_director_split.py` (7 tests) — regression for the 2026-06-02 God-module
  split: UIState + _devanagari_ratio re-exports and DirectorLlmClient shims.
- `test_story_planner.py`, `test_decision_engine.py`, `test_project_store.py` —
  memory/authority.
- `test_preflight.py` (14 tests) — startup readiness checks.
- `test_shutdown.py` (7 tests) — graceful shutdown signal handlers.
- `test_audio_crossfade.py` — assembler. The previous flake (OR-logic
  vacuous-pass) was hardened to a precise `afade=t=out` + `-af` check
  (2026-06-02). Stable across 8+ full-suite runs.
- **v6 pipeline tests:**
  - `test_pipeline_graph.py` (6 tests) — Phase 0 graph state shape.
  - `test_tts_alignment.py` (11 tests) — Phase 0.5 word-timestamp fix.
  - `test_source_loader.py` (57 tests) — Phase 1 (.txt/.md/.pdf/.docx/URL/paste).
  - `test_source_splitter.py` (57 tests) — Phase 2 (3 strategies).
  - `test_researcher.py` (31 tests) — Phase 3 (Wikipedia/Wikimedia/RSS).
  - `test_critic.py` (51 tests) — Phase 4 (5-dim rubric + rewrite loop).
  - `test_seo_generator_extended.py` (58 tests) — Phase 5 (8-field TypedDict).
  - `test_youtube_uploader.py` (24) + `test_youtube_profile_setup.py` (10) — Phase 6.
  - `test_bootstrap_source.py` (18 tests) — CLI `--source` glue + segment-count override.

**Linter:** `ruff check .` (see `testing_and_linting.md`). All checks pass (verified 2026-06-08).
The 2,400 raw errors were auto-fixed + manually triaged; ruff caught **6 real
latent bugs** during the cleanup. B904 (raise-without-from) was fully enabled after fixing all 9 occurrences.

**`pip check`** reports "No broken requirements found" — two METADATA patches
applied in the venv (`cached-path` rich upper bound removed, `wandb` click
version pinned down). These survive `pip install` because they are
site-packages modifications, not pip overrides.

**Coverage:** `coverage run -m pytest; coverage report` (see
`coverage_baseline.txt`). Baseline: **22.4%** first-party code coverage
across 1,745 tests. Config in `pyproject.toml [tool.coverage]`.

**No type-checker is installed.** mypy/pyrefly were deliberately skipped — the
codebase has minimal annotations and adding type-checking to the 2,618-line
`agents/director_agent.py` is a multi-day project. Re-enable when you add
annotations module-by-module.

## Common tasks (quick recipes)

### Add a new Ollama model
1. Drop the GGUF in `C:\models\` (the Ollama model store).
2. Create a `Modelfile.<name>` at the repo root (see `Modelfile.hermes-director`
   for the template — `FROM C:\models\foo.gguf`, then `PARAMETER` and
   `TEMPLATE`/`SYSTEM`).
3. `ollama create <name> -f Modelfile.<name>`
4. Reference in `config/config.yaml` under `models:`.

### Add a new config key
1. Add to `config/config.yaml`.
2. Add field to `config/config_schemas.py` (Pydantic). The schema uses
   `extra='allow'` per model so unknown keys won't crash — but you lose
   validation.
3. Read via `config.get("section", {}).get("key", default)` — never hardcode.

### Wire a new LLM call site to the breaker
```python
from utils.crewai_breaker import guarded_crewai_kickoff, BreakerOpen

try:
    result = guarded_crewai_kickoff(crew, model_name="my-model", timeout_s=240)
except BreakerOpen as e:
    # e.cooldown_s is the REAL remaining cooldown (not 0)
    log.warning(f"Breaker open for {e.cooldown_s:.1f}s — falling back")
    # ... fall back to a different model or skip
```

### Wire a new direct Ollama call to the breaker
Use `OllamaClient.generate()` / `chat()` — it already goes through the breaker.
Don't add a raw `urllib` loop (the `specialized_models._call_ollama` raw loop
is a known-but-unfixed wart — don't replicate the pattern).

## What NOT to do

- ❌ Run pipeline modules directly (use `bootstrap_pipeline.py` or `run.bat`).
- ❌ Call `crew.kickoff()` outside `guarded_crewai_kickoff`.
- ❌ Call Ollama outside `OllamaClient` (the `specialized_models` raw loop is a
  legacy exception — do not extend it).
- ❌ Add models to VRAM without evicting first.
- ❌ Run HEAVY (GPU) tasks outside `global_scheduler.task("heavy", ...)`.
- ❌ Delete the re-exports in `core/pipeline_long.py` without grepping
  `bootstrap_pipeline.py`, `studio_tui.py`, `tests/`.
- ❌ Modify `config.yaml` defaults without updating `config_schemas.py`.
- ❌ Bypass `crewai_lock` for "performance" — it prevents executor corruption.
- ❌ Add a new `UIState` class attribute without also resetting it in
  `tests/conftest.py`.
- ❌ Trust stale document numbers without cross-checking `config/config.yaml` — they go stale.
- ❌ Trust stale references to `TUI.bat` — it does not exist. Use `run.bat`
  or `studio_tui.py`.
- ❌ Run the pipeline with system Python (3.14) — the venv guard enforces
  `venv\Scripts\python.exe`. Use only the project virtual environment.
- ❌ Patch `cached-path` or `wandb` METADATA globally — the patches are
  venv-local; re-apply if `pip install` replaces them.
- ❌ Remove the `pyarrow` stub from `tests/conftest.py` without verifying
  the Windows atexit crash path is fixed upstream.

## Debugging quick wins

- **Breaker keeps opening on a model:** `cat studio_outputs/*/logs/breaker.log`
  or watch for `[Breaker] → Open` lines in the run log. The breaker
  remembers state per-model until process restart.
- **VRAM OOM during SD:** the OOM recovery ladder (D1) handles tier 1→2→3
  automatically. Check `studio_outputs/*/oom_report.json` (via
  `image_gen.get_oom_report()`).
- **TTS hangs:** `guarded_crewai_kickoff` will trip after 240s and surface
  `BreakerOpen`. Increase `timeout_s` at the call site, or restart Ollama.
- **Segment stuck on checkpoint:** delete the offending step from
  `studio_checkpoints/{topic}_seg{NN}.*.json` and rerun with `--no-resume`.
- **TTS sounds wrong / OOM in Whisper:** make sure `tts.omnivoice.ref_text` is
  set in `config.yaml` (it is by default). Whisper ASR crashes torchcodec on
  Windows; `ref_text` skips it.
- **`_DEFAULT_SFX` keyword has no effect:** only `sfx/thunder.wav` is bundled.
  Drop a matching WAV in `sfx/` for other keywords (wind/rain/heartbeat/...) to
  become active. See bug_resolution_history.md B32.
- **Studio TUI looks broken:** window too small. Resize the terminal and
  re-run `studio_tui.py`.
- **`pip check` broken:** check `venv\Lib\site-packages\cached_path-*/METADATA`
  for `rich` upper bound; check `venv\Lib\site-packages\wandb-*/METADATA` for
  `click` lower bound. Re-apply patches if reinstalled.
- **Python atexit crashes (Windows access violation):** ensure
  `PYARROW_IGNORE_CPP_SHUTDOWN=1` is set. The pyarrow stub in
  `tests/conftest.py` prevents native DLL loading.
- **Pipeline won't run (wrong Python):** run via `venv\Scripts\python.exe`,
  not `python`. The venv guard in `bootstrap_pipeline.py` enforces this.
- **TTS engine name from LLM doesn't match:** check `normalize_tts_engine()`
  in `audio/audio_proxy.py` — valid engines: `supertonic`, `omnivoice`, `f5`, `edge`.
  Unknown strings default to `supertonic`.

## Reference

- **Open bug count:** 0 open bugs (all historical bugs fixed; latest P8 fixes for Hermes-director HTTP 500, duration override, TTS engine normalization, pip check, atexit crashes, dashboard ESLint/act/controlled-input/vitest3 landed 2026-06-08). New bugs get the next P-id in sequence.
- **Regression tests** for the 2026-06-01 fix sweep live in
  `tests/test_2026_06_fixes.py` (25 tests). When fixing a new bug, add a
  regression test that fails without the fix and passes with it.
- **Future roadmap:** Tier 1: DMD2/LCM Bonsai accel, 3-hour video production; Tier 2: FramePack, Real-ESRGAN, music; Tier 3: multi-language voices, multi-ref IP-Adapter, voice acting director. **IP-Adapter shipped 2026-06-04** (P7-2) — moved out of Tier 3.
- **Specs:** `.kiro/specs/` (director-decision-authority,
  model-consolidation-switch-reduction, output-quality-fixes,
  production-quality-fixes, writer-tts-video-refinement, etc.).
- **Kiro hooks:** `.kiro/hooks/` (`max-capability-usage`, `smart-tool-usage`,
  `tui-compile-check`, `uistate-test-runner`) — only relevant if you're in
  the Kiro IDE.
- **Dashboard:** `dashboard/` is a separate React 19 + Vite 8 + Tailwind 4
  app. `dashboard/node_modules/` and `dashboard/dist/` already exist.
  `cd dashboard && npm run dev` (port 5173) for dev, `npm run lint` for ESLint.
  The root `package.json` (with the `hyperframes` dep) is **a leftover — do
  not install it**; the real frontend is in `dashboard/`.
- **New TTS docs (2026-06-04):**
  - `supertonic_pipeline.md` — full Supertonic 3 subsystem + production speed analysis
  - `voice_cloning.md` — DIY voice JSON extraction + 3 voice JSONs comparison
  - `RESEARCH_WHAT_TO_ADD.md` — Tier 1/2/3 backlog (now including the 3-hour video production plan)