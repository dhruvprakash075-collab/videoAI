# Video.AI Oversized Module Refactor

## Goal
Split four god-files into single-purpose modules while preserving all public imports via re-export shims. Behavior byte-identical; 100+ test suite + 80% coverage gate is safety net.

## Phases

### PR1 — refactor/shared-utils (small self-contained helpers) ✅
- [x] Create `utils/narration_sanitize.py` (+ tests via re-export)
- [x] Create `utils/time_format.py` (+ tests via re-export)
- [x] Create `core/runtime/abort.py`
- [x] Create `core/segment/budget.py`
- [x] Create `core/segment/identity.py`
- [x] Create `core/decision_record.py` (consolidate 3× dup block; NOTE: at `core/decision_record.py`, not `core/pre_production/decision.py` — the latter was shadowed by `core/pre_production.py` file)
- [x] Re-export all symbols from original modules
- [x] Create `tests/test_refactor_import_surface.py`
- [x] `ruff check . && pytest -q` green (2048 pass, 0 fail)
- [x] Coverage: 92.13% (>=80 passes)
- [x] Smoke: pipeline boots and runs through all phases correctly

### PR2 — refactor/pipeline-long
- [ ] Extract `core/outline_shaping.py` with `shape_outline()`
- [ ] Extract `core/pipeline_cli.py` with `main()`
- [ ] Slim `core/pipeline_long.py` orchestrator
- [ ] All re-exports intact
- [ ] `ruff check . && pytest -q` green

### PR3 — refactor/pre-production
- [ ] Create `core/preflight.py`
- [ ] Create `core/pre_production/memory_seed.py`
- [x] `core/decision_record.py` done in PR1 (moved from `core/pre_production/decision.py` to avoid file-shadow conflict)
- [ ] Create `core/pre_production/flows.py`
- [ ] Slim `core/pre_production.py` dispatcher
- [ ] Re-export all public API
- [ ] `ruff check . && pytest -q` green

### PR4 — refactor/segment-runner
- [ ] Create `core/segment/context.py` (SegmentContext dataclass)
- [ ] Create `core/runtime/ollama_server.py`
- [ ] Create `core/runtime/vram.py`
- [ ] Create `core/segment/nodes/*.py`
- [ ] Slim `core/segment_runner.py` with _SegmentGraphAdapter
- [ ] 6-tuple return contract preserved
- [ ] `ruff check . && pytest -q` green

### PR5 — refactor/director-agent
- [ ] Create `agents/director/` package with 7 mixins
- [ ] Slim `agents/director_agent.py` facade
- [ ] Remove PLR/C901 per-file ignore from pyproject.toml
- [ ] `ruff check . && pytest -q` green

## Definition of Done
- [ ] `ruff check .` clean (line-length 100, target py310)
- [ ] `pytest -q` green, coverage >= 80
- [ ] `tests/test_refactor_import_surface.py` passes
- [ ] `python -m core.pipeline_long --topic "smoke" --dry-run --no-resume` runs to completion
- [ ] No diff to ComfyUI-owned paths

## Non-interference Contract
- DO NOT touch: `comfyui_nodes/video_ai_nodes/**`, `tests/test_video_ai_nodes.py`, `.github/workflows/ci.yml`, `config/comfyui/workflows/**`
- DO NOT modify: `memory/project_store.py`, `config/config.yaml`, `.gitlab-ci.yml`, `.github/workflows/ci.yml`
