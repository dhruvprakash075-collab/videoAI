# Duplicates & Consistency

## Duplicate scan (production code)

Method: token n-gram (k=6) similarity over function bodies of ≥12 lines, all
first-party prod modules; threshold ≥0.55. Full AST scan, no fuzzy matching
dependencies.

**Result: 1 near-hit. The `tts_capabilities` "duplicate" was retracted after
source verification (2026-08-02):**

- ~~`audio/audio_proxy.py:1063` `tts_capabilities()` vs `config/config.py:78`
  `_default_config()` — token similarity 1.00 (identical body).~~ **RETRACTED** —
  `config/config.py:78` is `_default_config()` and does NOT define
  `tts_capabilities`; the function exists in exactly one place
  (`audio/audio_proxy.py:1063`). The reported match was an n-gram artifact.
  No fix needed; the "two sources of truth" row in HANDOFF.md #3 is void.

No other pair reached 0.55 — the rest of the production codebase has no
duplicated functions.

## Cross-file consistency (user's check: words_per_segment everywhere)

**Naming is consistent repo-wide** — `words_per_segment` appears as the same
key in:
- `prompts.yaml:53` (vision_document spec), `prompts.yaml:80` (example value
  280), `prompts.yaml:144` (writer_breakdown spec)
- `bootstrap_pipeline.py:184,264-268` (CLI + config plumbing)
- `config/config.py:96` (`script.words_per_segment` default 130, `min_words` 20)
- `agents/decision_engine.py` `_IMPACT` table (`words_per_segment: 9`) and
  `DecisionRecord._clamp` (default 130)
- `config/config.yaml` (runtime config)

No place uses a different name for it. The user's specific fear is cleared.

## Range/contract divergences (real inconsistencies found)

1. **`image_count_per_segment`: the two prompts contradict each other.**
   - `prompts.yaml:54` (vision_document): `int 2-4. Prefer fewer, stronger...`
   - `prompts.yaml:145` (writer_breakdown): `int 5-12`
   The writer (5-12) can produce up to 12 images/segment where the director
   spec says 2-4. Same key, two contracts.
2. **Prompt contract vs code clamp diverge for `words_per_segment`:**
   - prompts.yaml:53/144: `int 100-400`
   - `DecisionRecord._clamp`: (50, 800) — code silently accepts 50-99 and
     401-800, outside the prompt contract. Pacing silently off-spec.
3. **`images_per_segment` clamp (1, 30) vs prompt ranges (2-4 / 5-12)** —
   the clamp is wider than both prompt contracts.
4. Minor: default 130 (config) vs prompt example 280 (prompts.yaml:80) —
   cosmetic, both within contract.

## Other consistency checks (all clean)

- `rust/worker/src/assets.rs` hand-rolls SHA-256 (~200 lines) instead of using
  the `sha2` crate — deliberate zero-dep choice (Cargo.toml has no sha2),
  known-vector tests pass (hello → 2cf24dba…, 1000×'a' → 41edece4…). Not a
  duplicate; a review candidate: hand-rolled crypto is a bigger surface than
  the pinned `sha2` crate if deps are ever relaxed.

- `negative_prompt` — present in config.yaml:179, schema accepts it.
- `make_process_segment` — single caller, matching signature.
- heartbeat 10s / cancel grace 30s / poll 5s — mirror-exact between
  `jobs/worker.py` and the Rust worker (main.rs constants). **Correction: the
  stale 120 s constant is NOT implemented in Rust** — `STALE_JOB_SECONDS`
  appears only as a doc comment (`rust/worker/src/lib.rs:19`); the Rust worker
  loop is stale-blind (bugs.md #18). The AGENTS.md mirror rule is aspirational
  for that constant.
- `VIDEOAI_PYTHON` resolution + `venv/Scripts/python.exe` fallback — used
  consistently in both `jobs/worker.py` and the Rust worker.
- Deleted-symbol surface (`VideoAIConfig`, V1 `INPUT_TYPES`, input bridge):
  zero stale references (compileall + import smoke + suite).
- Frontend `dashboard/` exempt (user instruction); not scanned.
