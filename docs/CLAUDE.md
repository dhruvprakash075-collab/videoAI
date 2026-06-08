# CLAUDE.md — Video.AI Project Instructions

> ECC-integrated guidance for Claude Code. Based on [Everything Claude Code](https://github.com/affaan-m/ECC) principles.

---

# ⚠️ STRATEGIC NOTICE FOR AI ASSISTANTS (2026-06-08) ⚠️

**READ THIS FIRST before following any plan, bug list, or roadmap in this repo.**

## Current State (VERIFIED 2026-06-08)

- **All bugs from the 2026-06-04 audit and the 2026-06-08 pipeline hardening are FIXED.** 1,644 backend tests pass (clean exit). 165 dashboard tests pass (silent stderr). `ruff check .` clean. `pip check` clean.
- **Image backend migrated to Bonsai 4B** (was Stable Diffusion 1.5 + LoRA). Character consistency now via IP-Adapter FLUX v2 + master portraits. See `docs/system_architecture.md`.
- **TTS promoted to Supertonic 3** as default (was OmniVoice). 4.5x faster, zero VRAM. See `docs/supertonic_pipeline.md`.
- **Next major initiative: Director System V2** — chat-based AI orchestrator with 3 modes (production / build / off), 2-tier model (qwen2.5:0.5b collector + hermes-director reasoner), sqlite-vec RAG, permission system, builder with revert. See `docs/director_system.md`.

## DO NOT follow these (outdated)

- `docs/bug-fix-plan.md` — historical, 204 items all resolved. Kept for archive only.
- `docs/implementation_plan.md` — old 73 KB pipeline-fix plan. **Replaced** by Director V2 plan.
- `docs/RESEARCH_WHAT_TO_ADD.md` — old backlog. **Rewritten** with current Tier 1/2/3.
- `AUDIT_REPORT.md`, `ISSUES.md` (root) — old audit material. **Moved to `docs/archive/`**.

## Authoritative current docs

- `docs/AGENTS.md` — orientation (read first)
- `docs/director_system.md` — **NEW** Director V2 architecture
- `docs/director_modes.md` — **NEW** 3-mode behavior
- `docs/director_builder.md` — **NEW** builder + revert
- `docs/director_api_reference.md` — **NEW** SSE endpoints
- `docs/system_architecture.md` — current pipeline
- `docs/configuration_reference.md` — current config
- `docs/runtime_safety_guide.md` — VRAM/cleanup contracts
- `docs/testing_and_linting.md` — test conventions

If a doc and code disagree, **code wins**. Verified ground truth lives in `docs/AGENTS.md` "Verified ground truth" table.

---

## Project Overview

A local video-generation pipeline. Single operator, Windows 11, RTX 4050 6GB VRAM, Python 3.12.13. Takes a topic → plans a story → writes per-segment scripts → generates Hindi/Devanagari voice-over with **Supertonic 3 TTS** (DIY Hindi voice clone, 4.5x faster than OmniVoice, CPU ONNX) → Stable Diffusion images with character/LoRA face-lock → Ken Burns MP4 with Devanagari subtitles. All local. No cloud.

## ECC Core Principles (adapted for Video.AI)

1. **Agent-first delegation** — Use specialized agents for planning, review, security, and TDD. Invoke proactively, don't wait for user prompts.
2. **Test-driven development** — RED → GREEN → REFACTOR. 80%+ coverage target. Write tests before implementation.
3. **Security-first** — No hardcoded secrets. Validate at boundaries. Use environment variables for API keys.
4. **Immutability** — Create new objects, never mutate existing ones. Prefer `frozen=True` dataclasses and `NamedTuple`.
5. **Planning before execution** — Always plan before implementing. Use the planner agent for complex features.

## Critical Rules (DO NOT BREAK)

- **Run through `bootstrap_pipeline.py`**, never `python -m core.pipeline_long` directly. Bootstrap now has a **venv guard** — it enforces `venv\Scripts\python.exe` and rejects system Python.
- **Only ONE model in VRAM at a time.** Ollama models must be force-evicted before any GPU task.
- **Serialize ALL CrewAI `kickoff()`** through `utils.concurrency.crewai_lock` (RLock).
- **Use `global_scheduler.task("heavy", ...)`** for any GPU work (SD, OmniVoice).
- **Supertonic 3 is CPU-only — does NOT need VRAM eviction.** It can run concurrently with Stable Diffusion (SD=VRAM, Supertonic=CPU).
- **Hindi text with danda (`।`) needs the P6-1 fix in `supertonic_worker.py`.** Don't remove the `text.replace("।", ". ")` line.
- **Worker subprocesses on Windows need `PYTHONIOENCODING=utf-8` (P6-2).** All TTS worker spawns already pass it; new workers must too.
- **All config changes go in `config/config.yaml`**, not in Python. Add matching Pydantic field in `config/config_schemas.py`.
- **All paths are `pathlib.Path`**, no POSIX assumptions.
- **Atomic writes only** (temp + replace) for any persisted JSON.
- **Call `crew.kickoff()` only through `guarded_crewai_kickoff`** — never raw.
- **`pip check` must stay clean.** Two METADATA patches are required (`cached-path` rich upper bound removed, `wandb` click pinned down). Re-apply if `pip install` replaces them.
- **Do not remove the pyarrow stub in `tests/conftest.py`** — it prevents a Windows atexit access violation from native DLL unloading. Keep `PYARROW_IGNORE_CPP_SHUTDOWN=1`.

## Agent Orchestration

Available agents in `agents/`:

| Agent | Purpose | When to Use |
|-------|---------|-------------|
| **planner** | Implementation planning | Complex features, refactoring |
| **architect** | System design | Architectural decisions |
| **code-reviewer** | Code review | After writing/modifying code |
| **security-reviewer** | Security analysis | Before commits, new endpoints |
| **tdd-guide** | Test-driven development | New features, bug fixes |
| **python-reviewer** | Python-specific review | After Python code changes |
| **performance-optimizer** | Performance analysis | When code is slow |

**Immediate agent usage (no user prompt needed):**
- Code changes → code-reviewer
- New features / bugs → tdd-guide
- Complex features → planner
- Architectural decisions → architect
- Before commits → security-reviewer

## TDD Workflow

1. Write test first (RED)
2. Run test — it should FAIL
3. Write minimal implementation (GREEN)
4. Run test — it should PASS
5. Refactor (IMPROVE)
6. Verify coverage (80%+)

```powershell
# Run tests (1,682 passing, clean exit)
venv\Scripts\python.exe -m pytest tests/ -q

# Run with coverage
venv\Scripts\python.exe -m coverage run -m pytest tests/ -q
venv\Scripts\python.exe -m coverage report

# Dashboard tests (165 passing, Vitest 3.2.6)
cd dashboard && npm run test:run

# Check pip consistency
pip check
```

## Coding Standards

- **PEP 8** conventions, **ruff** for linting (`ruff check .`)
- **Type annotations** on all function signatures
- **Immutability** — prefer `frozen=True` dataclasses, `NamedTuple`, or tuples
- **Files**: 200-400 lines typical, 800 max. Split large modules.
- **Functions**: <50 lines, <5 params. Split when nesting >4 levels.
- **No magic numbers** — use named constants from config
- **pathlib.Path** for all file paths
- **Atomic writes** (temp + replace) for persisted data
- **Context managers** for resource management

## Security Checklist (before every commit)

- [ ] No hardcoded secrets (API keys, passwords, tokens)
- [ ] All user inputs validated
- [ ] Paths validated against traversal
- [ ] Error messages don't leak sensitive data
- [ ] Config values read from config, not hardcoded

## Architecture

```
CLI/UI → bootstrap → pipeline_long → Director (plan) → Writer (script) → Reviewer
       → translate → TTS (audio/supertonic_worker.py, default; omnivoice/edge fallback) → SFX (audio/)
       → Stable Diffusion (video/image_gen) → render segments → concatenate (video/renderer) → final MP4
       ↕ StoryMemory (memory/) for continuity, Checkpoints for resume
       ↕ DIY voice clone (character_voices/*.json) extracted via external/supertonic_embed
```

See `system_architecture.md` for the full diagram and `supertonic_pipeline.md` for TTS subsystem detail.

## File Organization Rules

- Many small files > few large files
- Organize by feature/domain, not by type
- High cohesion, low coupling
- Extract utilities from large modules
- Keep re-exports in `core/pipeline_long.py` — don't delete without grepping importers

## Error Handling

- Handle errors explicitly at every level
- Never silently swallow errors
- Use the circuit breaker (`BreakerOpen`) for Ollama/CrewAI failures
- Log detailed error context; provide user-friendly messages in UI

## Performance Guidelines

- GPU work through `global_scheduler.task("heavy", ...)`
- HEAVY slot = 1 (1800s wait), LIGHT slot = 16 (60s wait)
- Lazy-load heavy models (SD, TTS) only when needed
- Cache expensive computations (vision cache, story memory)

## Common Commands

```powershell
# Run pipeline
venv\Scripts\python.exe bootstrap_pipeline.py --topic "Your Topic" --duration 10 --no-resume

# Run tests
venv\Scripts\python.exe -m pytest tests/ -q

# Lint
ruff check .

# Dashboard
cd dashboard && npm run dev
```

## Debugging

- **Breaker keeps opening:** Check `studio_outputs/*/logs/breaker.log`
- **VRAM OOM:** Check `studio_outputs/*/oom_report.json`
- **TTS hangs:** `guarded_crewai_kickoff` trips after 240s with `BreakerOpen`
- **Segment stuck:** Delete checkpoint from `studio_checkpoints/` and rerun with `--no-resume`
