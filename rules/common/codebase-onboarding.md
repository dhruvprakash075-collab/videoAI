---
---
# Codebase Onboarding

> From ECC codebase-onboarding skill.

## When to Use

- First time opening a project with Claude Code
- User asks "help me understand this codebase"
- User says "onboard me" or "walk me through this repo"

## How It Works

### Phase 1: Reconnaissance

Gather raw signals without reading every file:

1. **Package manifest detection**
   - `pyproject.toml`, `requirements.txt`, `package.json`

2. **Framework fingerprinting**
   - `config/config.yaml`, `prompts.yaml`, `styles.yaml`

3. **Entry point identification**
   - `bootstrap_pipeline.py`, `studio_tui.py`, `run.bat`

4. **Directory structure snapshot**
   - Top 2 levels, ignoring `__pycache__`, `venv/`, `node_modules/`

5. **Config and tooling detection**
   - `ruff check .`, `pyproject.toml`, `.claude/`

6. **Test structure detection**
   - `tests/`, `pytest.ini`, `conftest.py`

### Phase 2: Architecture Mapping

**Tech Stack**
- Python 3.12.13 in `venv/`
- CrewAI for multi-agent orchestration
- Ollama for local LLM serving
- Stable Diffusion for image generation
- FastAPI for local control API
- React 19 + Vite 8 for dashboard

**Architecture Pattern**
- Pipeline orchestration (pre-production → segments → post-production)
- Circuit breaker for external services
- GPU-aware task scheduling

**Key Directories**
```
agents/        CrewAI agent definitions
audio/         TTS, SFX
config/        Config files and schemas
core/          Pipeline orchestration
dashboard/     React frontend
memory/        Story memory & world state
utils/         Cross-cutting helpers
video/         Image generation + rendering
```

### Phase 3: Convention Detection

**Naming Conventions**
- Files: `snake_case.py`
- Functions: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`

**Code Patterns**
- Circuit breaker for Ollama/CrewAI
- Config-driven (all tunables in config.yaml)
- pathlib.Path for all file paths
- Atomic writes for persisted JSON

### Phase 4: Generate Onboarding Artifacts

See `AGENTS.md` and `CLAUDE.md` for the full onboarding guide.

## Video.AI Quick Reference

| Task | Command |
|------|---------|
| Run pipeline | `venv\Scripts\python.exe bootstrap_pipeline.py --topic "Topic"` |
| Run tests | `venv\Scripts\python.exe -m pytest tests/ -q` |
| Lint | `ruff check .` |
| Dashboard | `cd dashboard && npm run dev` |
| Local API | `venv\Scripts\python.exe utils\local_ui.py` |
