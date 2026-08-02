# Bugs & Breaks — equal-weight findings

Every finding below was reproduced or verified at source. No severity ranking —
all are equally reportable. No fixes applied (report-first; approval required).

## CLI contract breaks (entry points that misbehave)

1. **`jobs/worker.py` ignores `--help` (and all args) and starts the worker loop.**
   `if __name__ == "__main__": w = Worker(); w.run_forever()` (worker.py:295-297)
   — no argument parsing at all. Running `.\venv\Scripts\python.exe -m jobs.worker --help`
   hung for 12+ s (poll loop, no output) and had to be killed. An operator
   asking for help gets a running worker. Same for `jobs/run_worker.py`.
   Evidence: execution-log.md.

2. **`scripts/comfyui_smoke.py` ignores `--help` and executes a real ComfyUI
   workflow.** Running `scripts\comfyui_smoke.py --help` during this audit ran
   the full smoke end-to-end: it spawned/used a live ComfyUI and produced a
   frame (`scene_01.png`, 533 136 bytes, "SMOKE OK"). No argument parsing in
   the script — any args are silently dropped. This is both a CLI bug and a
   "runs real models" surprise for anyone invoking it with arguments.

3. **`utils/preflight.py` breaks when run standalone.**
   `.\venv\Scripts\python.exe utils\preflight.py` → check functions fail with
   `ModuleNotFoundError: No module named 'config'` (and `director_model`).
   It only works when repo root is on `sys.path` (i.e., invoked through
   `bootstrap_pipeline.py` or `-m`). Its `__main__` block advertises standalone
   use. Fix: insert repo root into `sys.path` in `__main__` (or document
   `-m utils.preflight`).

4. **Mixed invocation conventions, undocumented.** `python core/pipeline_cli.py`
   → `ModuleNotFoundError: No module named 'core'`; only `python -m core.pipeline_cli`
   works. Same for `jobs/worker.py` / `jobs/run_worker.py` (`config`/`jobs`).
   Direct-script invocation is used by `bootstrap_pipeline.py`, `scripts/*`,
   `tools/*` and works; `core/*`, `jobs/*` require `-m`. No doc or guard
   explains which is which — the failure mode is a confusing traceback, not a
   helpful message.

5. **`utils/diagnose.py` rejects `--help`.** `utils/diagnose.py --help` →
   `[ERROR] Unknown diagnostic command: --help` (rc=1), because `--help` is
   dispatched as a diagnostic name. Usage is printed only after the error, and
   a valid diagnostic (`gpu`, `media`, `system`, `all`) is required first.

## Unwired production code (config promises, code doesn't deliver)

6. **`audio/audio_fx.py` never runs.** `audio_fx.enabled: true` in
   `config/config.yaml` + schema + 3 tests, but 0 production importers. SFX
   mixing and premium voice processing are silently absent from every pipeline
   run. Either wire it in or delete the config keys and module. (The loudnorm
   part is reimplemented inline in `video/renderer/assembler.py` — that half
   works, which makes the silent gap harder to notice.)

7. **`video/image_gen/ip_adapter.py` never runs.** `ip_adapter_scale` config and
   the `ip_adapter_ref` review decision exist, but the adapter is never loaded;
   character face-consistency silently never happens.

## Dead code (safe to delete; tests keep it alive)

8. `utils/retry_manager.py` — superseded by `core/segment/retry.py`.
9. `utils/web_search.py` — deprecated (story.py:51); only tests import it.
10. `utils/media_analyzer.py` — CLI-only tool, no prod callers (also the only
    gateway to the opt-in Rust audio analyzer, `_native_analyze_audio_wave`).
11. `utils/diagnose.py` — CLI-only, referenced by no launcher or doc.

## Environment (not code, but blocks/paints runs)

12. **PATH `python` is 3.14.5 (`C:\Python314`)** — violates the project's
    `requires-python >=3.10,<3.14` (pyproject.toml). `bootstrap_pipeline.py`
    correctly rejects it (venv guard). The sanctioned interpreter
    `.\venv\Scripts\python.exe` (3.12.13) is not on PATH — anyone typing
    `python bootstrap_pipeline.py` gets the venv-guard error.
13. **VRAM is under the preflight floor**: 2.6/6.0 GB free vs 4.5 GB required
    for SD (preflight check). Ollama's resident models are the likely consumer.
    Runs will fail preflight until freed.

## Silent-failure sweep — result: 0 true silent failures

23 candidate swallow sites located by AST, every one read at source and
verified as a deliberate, guarded fallback:
- `consultation.py:349` + `:228` — skip malformed tokens from LLM replies,
  defaults filled later (benign).
- `llm_client.py:180`, `ollama_client.py:207`, `audio_proxy.py:262/324/447/538/610/769`,
  `utils/utils.py:366` — JSON-line protocol skips with timeout/error handling
  around them.
- `bootstrap_pipeline.py:82/89/108/118` — Windows console/rich/tqdm guards.
- `check_environment.py:147` — nvidia-smi parse skip.
- `preflight.py:219` — two-command playwright fallback.
- `url_security.py:93` — control-flow fallthrough into the localhost check.
- `sentry.py:46`, `main.py:15`, `pipeline_long.py:51`, `compatibility.py:38`,
  `test_no_broad_suppress.py:43` — import/console guards.
No path silently loses data or swallows a fatal error. `utils/errors.py`
defines a strict error taxonomy and the suite (2048 passed) exercises it.

## Second pass — Rust worker, config schemas, YAML, workflows (all verified live)

14. **`utils/local_ui.py` crashes when run as a script.** `python utils/local_ui.py
    --help` → `ModuleNotFoundError: No module named 'agents'` rc=1 — same
    invocation-convention family as #3/#4. It works only via `-m utils.local_ui`.
15. **`python -m utils.local_ui --help` ignores `--help` and starts a live uvicorn
    server on 127.0.0.1:8000** (killed by timeout; no listener left). No argument
    parsing at all — an operator asking for help gets a running server.
16. **`cargo run --help` in `rust/worker` → rc=101 "could not determine which
    binary"** — no `default-run` in Cargo.toml despite 4 binaries
    (`videoai-worker` is the main one). Users must know `--bin videoai-worker`;
    cargo's error doesn't say which binary exists.
17. **Rust `doctor` resolves the repo root from CWD** — running
    `cargo run --bin videoai-worker -- doctor --json` from `rust/worker`
    reports `bootstrap_pipeline` "missing C:\Video.AI\rust\worker\bootstrap_pipeline.py"
    (critical) and `config_yaml` "missing ...\config\config.yaml" (warn).
    False failures; the message doesn't say "run from repo root". The comfyui
    checks skip with "image backend is not comfyui" — reads like an environment
    finding, but is just "comfyui not configured" (misleading skip detail).
18. **Rust implements NO stale-job detection.** `STALE_JOB_SECONDS = 120`
    exists only as a doc comment (`rust/worker/src/lib.rs:19`); the Rust worker
    loop is stale-blind, yet AGENTS.md/duplicates.md claim the constants are
    "mirror-exact" with `jobs/worker.py`. The doc promises behavior the code
    doesn't have.
19. **`_director_vision` is silently dropped by pydantic round-trips.**
    Field `config_schemas.py:505` (leading underscore = pydantic private attr,
    allowlisted :587): `model_dump()` excludes it and
    `ConfigOverlay(**{'_director_vision': {...}})` keeps theme='' — input
    silently discarded (verified live, pydantic 2.12.5). Prod readers
    (`core/director_memory.py:44`, `core/pre_production.py:200`) and writer
    (`agents/director/config_production.py:885`) use raw dicts, so it works
    today — but any future overlay round-trip silently loses vision settings.
    Latent data-loss trap.
20. **`IndicF5SubConfig().root` default = `D:\IndicF5`** (config_schemas.py)
    while `config/config.yaml:22` uses `external\IndicF5`. Schema default
    points at a drive that doesn't exist on this machine.
21. **Hardcoded absolute Windows paths**: `comfyui_nodes/video_ai_nodes/helpers.py:12`
    `DEFAULT_REPO_ROOT = r"C:\Video.AI"` and `config/config.yaml:22-23`
    (`external\IndicF5`, `C:\Video.AI\venv\Scripts\python.exe`). Broken for
    other checkouts and CI.
22. **Dead schema fields** — 0 consumers anywhere: `max_images_per_segment`
    (config_schemas.py:208, ge=0) and `uncapped_scaling` (:209). Config keys
    that silently do nothing (same family as #6/#7 at schema level). Used
    neighbors: `max_words` (:207), `tts_words_per_minute_hi/en` (:204-205).
23. **Prompt/config drift — TTS engine**: vision prompt hardcodes
    `"tts_recommendation": "omnivoice"` (prompts.yaml ~:43) while runtime
    config is `tts.engine: indicf5` (config.yaml). The director will
    recommend an engine the config doesn't use.
24. **Prompt/config drift — segment length**: prompts.yaml:20 tells the model
    "~1 min per segment" while config `segment_length_minutes` is 2.
25. Minor: `jobs/job_store.py:7-8` — `DB_PATH` relative to CWD with `mkdir`
    at import time (CWD-dependent side effect on import).

## Verified clean

- No stale references to deleted symbols (`VideoAIConfig`, legacy V1
  `INPUT_TYPES`, `director_agent.input` bridge) — compileall + import smoke
  (all prod modules import clean on venv) + full test suite green.
- No `config["x"]` direct-dict-indexing in prod (all `.get()`).
- `make_process_segment` 6-arg signature consistent between
  `segment_runner.py:64` and the single caller in `pipeline_long.py`
  (test_2026_06_fixes.py asserts exactly one call).
- `negative_prompt` present in `config/config.yaml:179` — schema-consistent.
