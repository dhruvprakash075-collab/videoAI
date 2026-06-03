# AGENTS.md — Video.AI orientation for AI sessions

> **Last updated:** 2026-06-03 (post dashboard test suite + doc updates)
> **Purpose:** Quick context for the next AI session. Read this BEFORE grepping.
>
> **ECC Integration:** This project now includes [Everything Claude Code](https://github.com/affaan-m/ECC) patterns.
> See `CLAUDE.md` for project instructions and `agents/` for agent definitions.
> See `rules/` for coding standards and guidelines.

## What this is

A local video-generation pipeline. Single operator, Windows 11, RTX 4050 6GB
VRAM, **Python 3.12.13** in `venv/`. Takes a topic → plans a story → writes
per-segment scripts → generates Hindi/Devanagari voice-over with OmniVoice TTS
→ Stable Diffusion images with character/LoRA face-lock → Ken Burns MP4 with
Devanagari subtitles. All local. No cloud.

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

## Verified ground truth (2026-06-03)

These are the live values; if a doc disagrees, **the live values win**:

| Item | Live value | Where |
|---|---|---|
| Backend test count | **1,743** passing, 1 skipped, 0 failing (26 deprecation warnings, all from `crewai`) | `pytest tests/ -q` |
| Frontend test count | **163** passing, 0 failing (20 files, Vitest + RTL) | `cd dashboard && npm run test:run` |
| Frontend coverage | **96.04%** stmts, **93.48%** branches, **90.9%** funcs | `cd dashboard && npm run test:coverage` |
| Test runtime (backend) | ~3 min on warm cache | pytest output |
| Test runtime (frontend) | ~3 s (full suite) | `vitest run` |
| Test coverage (backend) | **22.4%** first-party (see `coverage_baseline.txt`) | `coverage run -m pytest; coverage report` |
| `performance.staged_loop` | **true** (C1 enabled) | `config/config.yaml:193` |
| `audio_fx.enabled` | **true** (only `thunder.wav` bundled) | `config/config.yaml:198` |
| `tts.omnivoice.num_step` | **16** (was 24) | `config/config.yaml:49` |
| `script.words_per_segment` | **100** | `config/config.yaml:157` |
| `whisper_model` / `_final` | `tiny` / `base` | `config/config.yaml:187-188` |
| `loudnorm_two_pass` / `target_lufs` | `true` / `-14` | `config/config.yaml:203-204` |
| Python | 3.12.13 in `venv\` (NOT 3.14) | `venv/pyvenv.cfg` |
| Pytest | 9.0.3 | `pip list` |
| PyTorch | 2.11.0+cu128 | `pip list` |
| Git | **1 commit** (initial, 2026-06-02) — `master` has v6 unified pipeline + CLI `--source` | `git log` |
| Backend linter | **ruff 0.15.15** (see `LINTING.md`). All checks pass. | `ruff check .` |
| Frontend linter | **ESLint 9 (flat config)** — see `dashboard/eslint.config.js`. All checks pass. | `cd dashboard && npm run lint` |
| CI | None (no `.github/`) | `Test-Path .github` |

## Read these first (in order)

1. **`../.kiro/steering/tech.md`** — tech stack, platform notes, command examples.
2. **`../.kiro/steering/structure.md`** — entry points, top-level layout, conventions.
3. **`../.kiro/steering/product.md`** — what the product is and who it serves.
4. **`../.kiro/steering/ai-tools-guide.md`** — how AI tools should be used here.
5. **`system_architecture.md`** — main system structure, modules map, and execution flow.
6. **`runtime_safety_guide.md`** — safety measures, VRAM/GPU evictions, circuit breakers, and SD OOM recovery ladder.
7. **`testing_and_linting.md`** — pytest guidelines, coverage configuration, and Ruff setups.
8. **`configuration_reference.md`** — parameter details, prompting layouts, and visual presets.
9. **`bug_resolution_history.md`** — summary of historical fixes (B1–B40, P-series).
10. **`../config/config.yaml`** (292 lines) — actual ground truth for all config parameters. Always trust this file over doc claims.

## Recent changes (2026-06-03)

- **v6 unified pipeline shipped (CLI `--source` flag):** 6 new modules
  (`source_loader`, `source_splitter`, `researcher`, `critic`, `seo_generator`
  extended, `bootstrap_pipeline._load_and_split_source`). 304 new tests.
  Total: 290 → 613 → **1,743** (including extended coverage test modules added 2026-06-03).
- **Smoke-test bug fixes:** `core/post_production.py:193` `log_vram_usage`
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
  matching Pydantic field in `config/config_schema.py`.
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
| `train_lora.py` | Standalone LoRA face-lock training. |

**Planned consolidation:** The plan to merge the entry-point scripts into `pipeline.py` + `testpipeline.py` was rejected as the scripts serve different audiences (CLI / TUI / web UI / LoRA training / smoke test / Windows launcher) and are better kept separate. The consolidation work that actually happened was file-system cleanup of root-level orphan test artifacts.

**`TUI.bat` does NOT exist**. Use `run.bat` (or `studio_tui.py` directly).

The only first-class launcher is `run.bat`. `bootstrap_pipeline.py` and
`studio_tui.py` are the only first-class entry points.
`bootstrap_pipeline.py` are first-class. Don't write a new one without
deleting an old one.

## Post-refactor module map (June 2026)

### `core/`
- `pipeline_long.py` (612) — thin orchestrator + re-exports. **Re-exports many
  private names** for backward compat (`_sanitize_narration`, `_evict_ollama_models`
  (re-exported as both `evict_ollama_models` and the old name), `process_segment`,
  `make_process_segment`, etc.). Do NOT delete the re-exports without grepping
  for importers in `bootstrap_pipeline.py`, `studio_tui.py`, `tests/`.
- `pre_production.py` (826) — Director phase.
- `segment_runner.py` (1146) — per-segment loop. Contains
  `make_process_segment(...)` (the closure-builder). **v6 Phase 4:** `make_process_segment(...,
  source_chunks=None)` — when set, segments 1..N receive `state["source_chunk"] = source_chunks[i-1]`,
  which short-circuits the writer LLM call in `write_script_node` and auto-approves
  the critic (verbatim source, no 5-dim rubric).
- `pipeline_graph.py` — **v6 Phase 4:** added `source_chunk: Any` to `SegmentState`
  TypedDict. Wiring point for the per-segment writer-bypass.
- `post_production.py` (265) — final assembly (concat, thumbnail, manifest, QC).
  **2026-06-02 smoke-test fix:** line 193 import of `log_vram_usage` corrected to
  `core.segment_runner` (was `core.pre_production` — stale Phase 0 ref).
- `main.py` (156) — CrewAI agent factory (`create_director`, `create_writer`).

### `utils/`
- `crewai_breaker.py` (197) — `guarded_crewai_kickoff(crew, model_name,
  timeout_s=240, lock=None)`. Raises `BreakerOpen(model, cooldown_s)` when the
  per-model breaker is OPEN. Reuses `OllamaClient._breaker()` so Ollama +
  CrewAI calls share state. `cooldown_s` is the **real** remaining cooldown
  (P5-1 fix — was hardcoded 0).
- `concurrency.py` (108) — `global_scheduler` + `crewai_lock` (RLock). Light
  slot wait is **60s** (P4-18 fix; was 300s).
- `ollama_client.py` (321) — B1 per-model circuit breaker. Has
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
- `specialized_models.py` (350) — **NOT** on B1 breaker yet; has its own
  urllib loop. Low priority (image-engineer degrades gracefully).
  **Pre-existing stale-bug:** `extract_world_state()` at line 290 still references
  the removed `script-reviewer` model — non-fatal warning fires on every memory
  write when `memory.llm_world_state: true`. Phase 0 cleanup miss; Tier 2 fix.
- `story_planner.py` (299), `context_manager.py` (269) — both use
  `guarded_crewai_kickoff`.

### `agents/`
- `director_agent.py` (2407 lines, 85KB) — `DirectorAgent`. 2026-06-02 split:
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

## Tests

**Backend — 1,743 tests, run with:**
```powershell
venv\Scripts\python.exe -m pytest tests/ -q
# 1743 passed, 1 skipped, 26 warnings in ~3min
```

**Frontend — 163 tests (dashboard), run with:**
```powershell
cd dashboard
npm run test:run         # single-shot
npm run test:coverage    # v8 coverage report
# 163 passed, 0 failed, ~3s
```

The 26 warnings are all CrewAI `DeprecationWarning`s (`function_calling_llm`,
`reasoning`, `planning_config`) — not your bug, harmless.

> **Windows note**: A benign `PermissionError: [WinError 5]` about `pytest-current` symlink cleanup in `%TEMP%` appears after the run — it does NOT indicate a test failure.

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

**Linter:** `ruff check .` (see `testing_and_linting.md`). All checks pass as of 2026-06-02.
The 2,400 raw errors were auto-fixed + manually triaged; ruff caught **6 real
latent bugs** during the cleanup. B904 (raise-without-from) was fully enabled after fixing all 9 occurrences.

**Coverage:** `coverage run -m pytest; coverage report` (see
`coverage_baseline.txt`). Baseline: **22.4%** first-party code coverage
across 1,743 tests. Config in `pyproject.toml [tool.coverage]`.

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
2. Add field to `config/config_schema.py` (Pydantic). The schema uses
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
- ❌ Modify `config.yaml` defaults without updating `config_schema.py`.
- ❌ Bypass `crewai_lock` for "performance" — it prevents executor corruption.
- ❌ Add a new `UIState` class attribute without also resetting it in
  `tests/conftest.py`.
- ❌ Trust stale document numbers without cross-checking `config/config.yaml` — they go stale.
- ❌ Trust stale references to `TUI.bat` — it does not exist. Use `run.bat`
  or `studio_tui.py`.

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

## Reference

- **Open bug count:** 0 open bugs (all 78 historical bugs fixed). New bugs get the next P-id in sequence.
- **Regression tests** for the 2026-06-01 fix sweep live in
  `tests/test_2026_06_fixes.py` (25 tests). When fixing a new bug, add a
  regression test that fails without the fix and passes with it.
- **Future roadmap:** Tier 1: TTS speed, model consolidation; Tier 2: FramePack, Real-ESRGAN, music; Tier 3: multi-language, IP-Adapter, voice acting.
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