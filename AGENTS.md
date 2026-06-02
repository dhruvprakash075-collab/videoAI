# AGENTS.md — Video.AI orientation for AI sessions

> **Last updated:** 2026-06-02 (post security fixes + pipeline plan)
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

## Verified ground truth (2026-06-02)

These are the live values; if a doc disagrees, **the live values win**:

| Item | Live value | Where |
|---|---|---|
| Test count | **281** passing, 0 failing (12 deprecation warnings, all from `crewai`) | `pytest tests/ -q` |
| Test runtime | ~28s on warm cache | pytest output |
| Test coverage | **22.4%** first-party (see `coverage_baseline.txt`) | `coverage run -m pytest; coverage report` |
| `performance.staged_loop` | **true** (C1 enabled) | `config/config.yaml:193` |
| `audio_fx.enabled` | **true** (only `thunder.wav` bundled) | `config/config.yaml:198` |
| `tts.omnivoice.num_step` | **16** (was 24) | `config/config.yaml:49` |
| `script.words_per_segment` | **100** | `config/config.yaml:157` |
| `whisper_model` / `_final` | `tiny` / `base` | `config/config.yaml:187-188` |
| `loudnorm_two_pass` / `target_lufs` | `true` / `-14` | `config/config.yaml:203-204` |
| Python | 3.12.13 in `venv\` (NOT 3.14) | `venv/pyvenv.cfg` |
| Pytest | 9.0.3 | `pip list` |
| PyTorch | 2.11.0+cu128 | `pip list` |
| Git | **0 commits** — `master` has no history; everything is working tree | `git log` |
| Linter | **ruff 0.15.15** (see `LINTING.md`). All checks pass. | `ruff check .` |
| CI | None (no `.github/`) | `Test-Path .github` |

## Read these first (in order)

1. **`.kiro/steering/tech.md`** — tech stack, platform notes, command examples.
2. **`.kiro/steering/structure.md`** — entry points, top-level layout, conventions.
3. **`.kiro/steering/product.md`** — what the product is and who it serves.
4. **`.kiro/steering/ai-tools-guide.md`** — how AI tools should be used here.
5. **`BUGS_AUDIT_2026-05.md`** — authoritative open-bug list. **0 open** as of
   2026-06-01 (78 of 78 fixed; the doc is now a "Resolution history" reference).
   The older `BUGS.md` is historical (B1–B40). The next bug gets the next
   available P-id (P5-5 for refactor-pass, P4-30 for normal).
6. **`PROJECT_STATUS.md`** — current architecture, file map, status tables.
7. **`AI_PROJECT_REFERENCE.md`** — full developer onboarding (has a
   "§24 Recent Refactor" section describing the May→June 2026 changes).
8. **`config/config.yaml`** (217 lines) — actual ground truth for all tunables.
   **Many older docs quote stale values** (e.g. `num_step: 24`, `staged_loop: false`,
   `words_per_segment: 130`) — the config file is right.
9. **`_archive/README.md`** — what's in `_archive/` and how to recover.
10. **`LINTING.md`** — ruff linter/formatter setup, config rationale, what
    the lint pass caught. Run `ruff check .` before any commit.
11. **`pipeline_plan.md`** — pipeline consolidation plan (merge 8 files into 2).

## Recent changes (2026-06-02)

- **Security fixes:** Replaced MD5 with SHA256 for cache keys in `agents/director_agent.py`.
- **Ruff:** All checks pass (0 errors).
- **Pipeline plan:** Created `pipeline_plan.md` to consolidate 8 scripts into 2 (`pipeline.py` + `testpipeline.py`).

### local_ui robustness (this session)
- Updated `utils/local_ui.py` to be more robust and thread-safe for the local dashboard:
  - `GET /api/status`: reads `UIState.logs` under `UIState._log_lock` when available (prevents concurrent read races).
  - `POST /api/ab/generate`: added best-effort VRAM/LLM safety before running SD generation by attempting `core.segment_runner.evict_ollama_models(...)` with a CUDA-cache fallback.
- Verified with:
  - `ruff check .`
  - `pytest tests/ -q`
  - Pipeline dry-run smoke test via `bootstrap_pipeline.py --skip-preflight --dry-run --topic "Real Hero" --yes`

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
| `bootstrap_pipeline.py` | **Primary CLI.** Patches + preflight + args + calls `run_long_pipeline`. Supports `--skip-preflight` and `--preflight-only`. |
| `studio_tui.py` | Operator TUI (Textual). `venv\Scripts\python.exe studio_tui.py` |
| `run.bat` | Windows menu launcher: UI / CLI / Tests. Has the TUI fallback. |
| `run_pipeline.py` | Hardcoded `"Real Hero"` smoke test (P4-27). |
| `utils/local_ui.py` | FastAPI backend for the React dashboard (port 8000). |
| `train_lora.py` | Standalone LoRA face-lock training. |

**Planned consolidation:** REMOVED 2026-06-02. The earlier `pipeline_plan.md`
claim that the entry-point scripts would be merged into `pipeline.py` +
`testpipeline.py` was a misreading. The scripts serve different audiences
(CLI / TUI / web UI / LoRA training / smoke test / Windows launcher) and
are better kept separate. See `pipeline_plan.md` "What this plan did NOT
call for" for the full story. The 2026-06-02 consolidation work that
actually happened was file-system cleanup of root-level orphan test
artifacts (see `pipeline_plan.md` § "File consolidation (2026-06-02)").

**`TUI.bat` does NOT exist** despite being mentioned in `PROJECT_STATUS.md`,
`AI_PROJECT_REFERENCE.md`, and `HOW_TO_RUN_TUI.txt`. Use `run.bat` (or
`studio_tui.py` directly). If you add a new launcher, update those three docs.

The other legacy launchers (`launch_tui.ps1`, `run_final.ps1`,
`run_pipeline_bg.ps1`, `start_pipeline.ps1`, `setup_f5.ps1`, `auto_start.ps1`,
`activate_video_ai.bat`) are convenience wrappers — only `run.bat` and
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
  `make_process_segment(...)` (the closure-builder).
- `post_production.py` (265) — final assembly (concat, thumbnail, manifest, QC).
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
- `specialized_models.py` (350) — **NOT** on B1 breaker yet; has its own
  urllib loop. Low priority (image-engineer degrades gracefully).
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
- `executive_agent.py` (7 lines) — **dead stub** (P4-21). Kept only to avoid
  ImportError in any external reference. Don't import it.
- `decision_engine.py` (201) — `DecisionRecord` authority model.
  Hierarchy: `default < director < writer < user / cli_flag`.

## Tests

**290 tests, run with:**
```powershell
venv\Scripts\python.exe -m pytest tests/ -q
# 290 passed, 12 warnings in ~50s
```

The 12 warnings are all CrewAI `DeprecationWarning`s (`function_calling_llm`,
`reasoning`, `planning_config`) — not your bug, harmless.

Test files (34 `test_*.py` modules + 2 `manual_integration_test_*.py`):
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

**Linter:** `ruff check .` (see `LINTING.md`). All checks pass as of 2026-06-02.
The 2,400 raw errors were auto-fixed + manually triaged; ruff caught **6 real
latent bugs** during the cleanup (see `LINTING.md` for the list). B904
(raise-without-from) was fully enabled after fixing all 9 occurrences.

**Coverage:** `coverage run -m pytest; coverage report` (see
`coverage_baseline.txt`). Baseline: **22.4%** first-party code coverage
across 290 tests. Config in `pyproject.toml [tool.coverage]`.

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
- ❌ Trust `PROJECT_STATUS.md` or `AI_PROJECT_REFERENCE.md` numbers without
  cross-checking `config/config.yaml` — they go stale.
- ❌ Trust `HOW_TO_RUN_TUI.txt` — it tells you to use `TUI.bat` which does
  not exist. Use `run.bat` or `studio_tui.py`.

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
  become active. BUGS.md B32.
- **Studio TUI looks broken:** window too small. Resize the terminal and
  re-run `studio_tui.py`.

## Reference

- **Open bug count** is in `BUGS_AUDIT_2026-05.md` §"Open-bug summary" — **0
  open** as of 2026-06-01 (78 of 78 fixed). New bugs get the next P-id in
  sequence.
- **Regression tests** for the 2026-06-01 fix sweep live in
  `tests/test_2026_06_fixes.py` (25 tests). When fixing a new bug, add a
  regression test that fails without the fix and passes with it.
- **Future roadmap:** `FUTURE_ROADMAP.md` (Tier 1: TTS speed, model
  consolidation; Tier 2: FramePack, Real-ESRGAN, music; Tier 3: multi-language,
  IP-Adapter, voice acting). Note: its "C1 staged loop" section is stale —
  the flag IS now true in `config.yaml`.
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
