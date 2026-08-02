# Codebase Report — 2026-08-02

Human-grade audit of `C:\Video.AI`: every tracked Python file checked for actual
usage, entry-points executed (dry, no model runs intended), silent failures
hunted at source, duplicates/consistency scanned, tests included as suspects.
Findings are equal-weight (no HIGH/MED/LOW). Frontend `dashboard/` exempt.

| File | Contents |
|---|---|
| [README.md](README.md) | This index |
| [usage.md](usage.md) | Per-file usage verdict for all 228 tracked .py files |
| [python-scripts.md](python-scripts.md) | Catalog of runnable entry scripts |
| [unused.md](unused.md) | Dead, stale, and junk files — tracked and gitignored |
| [duplicates.md](duplicates.md) | Duplicate scan + cross-file consistency (words_per_segment etc.) |
| [bugs.md](bugs.md) | Bugs, breaks, and silent-failure findings (equal weight) |
| [test-audit.md](test-audit.md) | Test-side findings — tests as suspects |
| [execution-log.md](execution-log.md) | Every entry-point executed + exit code + verdict |
| [nested-loops.md](nested-loops.md) | Loop nesting analysis (transitive depth, hidden O(n²)) |
| [other-findings.md](other-findings.md) | Complexity hotspots, alloc-in-loop, uncommitted work |

## Method

1. **Usage audit**: AST import-graph over all tracked `.py` (228 files, excluding
   `venv/`, `external/`, `dashboard/`, `rust/`). Orphan candidates verified by
   grep for string references, then each file read at its import sites.
2. **Syntax**: `compileall` over all tracked `.py` — exit 0.
3. **Silent-failure sweep**: AST for `except: pass/continue` + `except Exception:
   pass/continue` + bare-`return` on error + `os._exit` + swallow patterns;
   23 candidates located, every code site read, verdict per site (bugs.md).
4. **Execution sweep**: every entry-point run at safe boundaries (`--help`,
   `--days-old 1`, dry runs) with the sanctioned venv interpreter
   (`.\venv\Scripts\python.exe`, 3.12.13); also ran under system `python`
   3.14.5 to expose interpreter-drift. Full log: execution-log.md.
5. **Duplicate scan**: token n-gram similarity ≥0.55 over production functions
   (≥12 lines) — one near-hit found, manually compared (duplicates.md).
6. **Tests**: full suite run (2048 passed / 5 skipped), plus vacuous-assert and
   mock-target checks on test files themselves.
7. **Rust**: `cargo test` (73 passed) + `clippy -D warnings` + `fmt --check` — all green.
8. **Nested loops**: AST transitive-depth + direct-depth analysis, hottest
   functions manually reviewed (nested-loops.md).

## Key results (details in linked files)

- **Zero genuine duplicates**, zero orphan files in active use; 6 production
  modules are unwired (only tests import them) — most consequential:
  `audio/audio_fx.py` (SFX mixing + premium voice processing never runs) and
  `video/image_gen/ip_adapter.py` (face-consistency never runs).
- **Zero true silent failures** — 23 candidate swallow sites all verified as
  deliberate, guarded fallbacks.
- **CLI contract breaks**: `jobs/worker.py` and `scripts/comfyui_smoke.py`
  ignore `--help` (worker starts polling; smoke ran a REAL ComfyUI workflow
  during this audit). `utils/preflight.py` and `core/pipeline_cli.py` crash
  with `ModuleNotFoundError` when invoked as scripts instead of `-m`.
- **Consistency**: `words_per_segment` naming is consistent repo-wide, but the
  prompt contract (100–400) and the code clamp (50–800) disagree; the
  vision/writer prompts disagree on `image_count_per_segment` (2–4 vs 5–12).
- **Environment**: PATH `python` is 3.14.5 — outside the project's
  `>=3.10,<3.14` range; venv is 3.12.13. GPU has ~2.6/6.0 GB VRAM free,
  under the 4.5 GB preflight floor for SD.
- **Second pass (Rust, config, workflows)**: `utils/local_ui.py` CLI broken
  (script crash; `-m` ignores `--help` and starts a live uvicorn); `cargo run`
  needs `--bin` (no `default-run`); Rust `doctor` false-fails from
  `rust/worker` (CWD-based root); Rust stale-120 s constant is doc-only;
  `_director_vision` silently dropped by pydantic round-trip (latent);
  `IndicF5SubConfig` default points at `D:\IndicF5`; dead schema fields
  (`max_images_per_segment`, `uncapped_scaling`); TTS-engine and segment-length
  drift between prompts.yaml and config.yaml; all 6 workflows use fixed seeds;
  skip inventory clean (6 conditional skips, 0 xfails). Details: bugs.md
  #14-25, duplicates.md corrections.
- **Third pass (config.yaml, CI, hot path, Rust tests, docs)**: config.yaml
  fully cross-checked against disk — only 2 missing references, both benign
  (`narration_ref_9s_mono24k.txt` falls back to inline ref_text;
  `chrome_profile` unused with uploads off); all ComfyUI models/VAE/LoRAs on
  disk; 3 CI workflows + pyproject read (pinned SHAs, rust CI on `rust/**`
  paths, maturin wheel); hot-path module bodies (pipeline_long, segment_runner,
  pipeline_graph, pre/post_production, main, preflight, image_gen, renderer,
  assembler ≈5.1k lines) read — no CRITICAL, minors only (bugs.md #26-34):
  preflight.py:50 third `D:\IndicF5` hardcode; YouTube upload exception
  unguarded (lost manifest on upload crash); image_gen `_refine_upscale`
  hardcodes node indices "1"/"11"; 4 dead SD-era image_gen helpers still
  advertised in docstring; README Rust commands fail live (rc=101, verified);
  docs stale in 3 places (workflow name, IndicF5 root, 2 test counts); Rust
  67 `#[test]` bodies read (main.rs, text.rs, doctor.rs) — solid.
- **Fourth pass (re-check, user: "close gap and check again")**: usage.md
  count verified against `git ls-files` — 229 tracked .py (report said 228;
  the +1 is a dot-dir tooling script, no production file missed); `vendors/
  indicf5/` confirmed dead duplicate of `external/IndicF5` (zero refs in all
  tracked .py); `Modelfile.*` ×3 stale — all `FROM C:\models\*.gguf` targets
  absent on disk, runtime uses `ollama pull hermes-director` (README.md:25),
  `cra-guided` zero refs (bugs.md #35-36, unused.md); `static/ab_picker.html`
  live (local_ui.py:259), `sfx/thunder.wav` deleted with its owner audio_fx (unused.md),
  `projects/series_1.yaml` live via `--project`; `plans/README.md` stale.
  Un-audited by design: `rules/` (27 agent-guidance md), `.agents/`,
  `.opencode/`, `.cursor/` (tooling/guidance, not code).
- **Fifth pass (user: "do 3 and 4")**: (3) function-level dead-code sweep
  via the codebase-memory graph — server back up (v0.10.0, 6561 nodes);
  `query_graph` aggregate Cypher crashes it, `search_graph` degree filters
  work. 272 degree-0 functions + 48 methods swept, candidates grep-verified:
  **2 new dead spots** — `get_temp_items`/`clear_temp_items`
  (project_store.py:992-1000) and the `_call_ollama_streaming`/
  `_prewarm_ollama` chain (llm_client.py:130-199, llm_shims.py:50-54,
  "live UI feedback"/"background warm-up" features never wired); the rest
  are FastAPI decorator handlers + constructor edges (false positives).
  (4) All 24 `rules/` + .cursor/ponytail.mdc + .agents skills + .opencode
  skills/commands read: **api-design.md documents a nonexistent
  `/api/v1/*`+`/health` contract** (actual API is `/api/*`), onboarding says
  "Stable Diffusion" (ComfyUI now) and prescribes the crashing local_ui
  invocation, testing rules teach unregistered pytest markers, security rule
  mandates bandit (never installed). Full list bugs.md #38.

## Open items

- Function-level no-caller query (codebase-memory MCP) crashed mid-session;
  file-level sweep below is AST + git verified instead.
- **Post-audit status (2026-08-02)**: all approved fixes applied (HANDOFF.md
  #1-43; execution-log.md), suite re-run 1948 passed / 5 skipped, ComfyUI
  smoke gate green, Phase 2 deletions done (gitlab CI files, coverage
  baseline, task_plan.md, plans/, model_eval untracked+ignored, director.db
  and ComfyUI test artifacts removed). Report-first constraint lifted —
  fixes were executed and verified, see HANDOFF.md for the per-item status.
