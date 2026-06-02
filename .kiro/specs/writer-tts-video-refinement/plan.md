# Writer / TTS / Video Refinement — Builder-Ready Plan (v2)

> Author: planning pass (not a coder). Hand this to the implementer.
> Verified against live code + a real pipeline run + 2026 web research on 2026-05-31.
> Target hardware: Windows, RTX 4050 6GB, Python 3.12 venv.
> Keep the 189 pytest tests green; follow project conventions (config-driven, pathlib, module logger, additive UIState).

---

## ⚠️ PLAN REVIEW — flaws found in v1 and corrected in this v2

A self-review against the real code + CrewAI/litellm docs found 5 real flaws in the first draft. All fixed below:

1. **`num_retries=0` is NOT a guaranteed fix (v1 over-claimed it).** The retries we saw
   (`stainless-python-retry-*` duplicate idempotency keys) come from the **OpenAI Python client** that
   litellm wraps, and there is a known litellm bug (#18968) where per-deployment `num_retries` is not
   always honored. → **Correction:** the *primary* lever is the `max_tokens` cap (W1), which prevents the
   timeout from happening at all. `num_retries=0` is best-effort; if it doesn't suppress the retry, set
   the OpenAI client retries to 0 via env (`OPENAI_MAX_RETRIES=0`) in `bootstrap_pipeline.py`. Verify by log.
2. **CrewAI `output_pydantic` may NOT constrain a local Ollama model (v1 listed it as co-equal Option A).**
   CrewAI bug #2729 shows `supports_response_schema` is wrong for some non-OpenAI models, so CrewAI may only
   *post-validate* (and retry) instead of truly constraining generation. → **Correction:** the real fix is
   **direct Ollama structured output** (`format=<json schema>`) via the existing `OllamaClient` — that
   constrains at the model level. Make this the PRIMARY path (W2), not a co-equal option. Bonus: it also
   puts the writer under the B1 circuit breaker.
3. **v1 ignored the review-rejection REVISION crew** (`revision_crew.kickoff()` at `pipeline_long.py` ~1745).
   That is a SECOND full writer LLM call that can also hit the 240s timeout. → **Correction:** W1's
   max_tokens cap covers it; W2 should route it through the same structured path; called out explicitly now.
4. **W1 test was unsafe** — it assumed CrewAI's `LLM` exposes `.max_tokens`/`.num_retries` as readable attrs.
   That is not guaranteed. → **Correction:** the test asserts the *factory wiring* (the value it passes),
   using a monkeypatched `LLM` constructor that records its kwargs, not by reading the real object's attrs.
5. **W5 (switch default scratch writer to zephyr) was framed as a pure win — it isn't.** `cra-guided-7b`
   exists specifically for flowery creative invention; zephyr may produce blander stories. → **Correction:**
   W1+W2+W3 likely make `cra-guided-7b` usable again (constrained output + sanitizer), so W5 is now LAST
   RESORT / operator-choice, not a default flip.

Plus the user-requested change: **F5-TTS is now MANDATORY (the default TTS), and its install/download starts
in Phase 1** (see W0). OmniVoice/edge become fallbacks only.

---

## Phases

- **Phase W (Writer + F5 setup)** — fixes the LLM death spiral AND kicks off the F5-TTS download. Do first.
- **Phase T (TTS cutover)** — wire F5-TTS as the default engine (download already done in W0).
- **Phase V (Video)** — optional FramePack image-to-video motion. Opt-in, last.

---

# PHASE W — Writer LLM fix + F5 download kickoff

### W0 · Start the F5-TTS download NOW (runs in background while W1–W5 are coded)
- **Why first:** F5 is mandatory (user decision) and its model is multi-GB; downloading it during Phase 1
  means it's ready by the time the TTS cutover (Phase T) lands. Do this on a real machine, not in tests.
- **Action (operator, one-time, document in `requirements.txt` + a `setup_f5.ps1`):**
  ```powershell
  venv\Scripts\pip.exe install f5-tts soundfile
  # Hindi voice-clone checkpoint — pick ONE (see note):
  venv\Scripts\huggingface-cli.exe download SPRINGLab/F5-Hindi-24KHz --local-dir hf_cache\f5_hindi
  ```
- **Model choice (verified to exist on HF, 2026-05-31):** `SPRINGLab/F5-Hindi-24KHz` (Hindi from scratch),
  `Futurix-AI/Hindi-TTS` (MIT, F5 arch), `multilingual-tts/F5-TTS-OpenBible-Hindi`. **RISK to validate:**
  some Hindi fine-tunes are trained-from-scratch and may do reference-audio **voice cloning** worse than the
  base multilingual F5. The implementer MUST test one clip with `character_voices/narration_voice.wav` as the
  reference and confirm the cloned voice is acceptable BEFORE making F5 the hard default. If cloning is poor,
  use the base multilingual F5 + Hindi text (accept a generic voice) OR keep OmniVoice for cloning.
- **6GB note:** F5 (~1-2GB in fp16) fits alongside nothing else — it must run in the HEAVY scheduler slot
  after a verified evict, same as OmniVoice today.
- **Deliverable:** model present under `hf_cache/f5_hindi`, `f5-tts` importable in the venv.

### W1 · Cap `max_tokens` per agent role (THE primary timeout fix)
- **File:** `core/main.py` — `_create_ollama_llm` (~line 31), `create_director` (~57), `create_writer` (~100).
- **Problem:** `max_tokens=8192` hardcoded for ALL agents. A 150–400 word script ≈ 600 tokens; 8192 lets the
  model run ~4 min and hit the 240s timeout → the openai client silently retries (proven in the run log).
- **Change:**
  - Add `max_tokens: int = 2048` param to `_create_ollama_llm(...)`; pass to `LLM(max_tokens=max_tokens, ...)`.
  - `create_writer`: `max_tokens=config.get("script", {}).get("writer_max_tokens", 1024)`.
  - `create_director`: `max_tokens=config.get("models", {}).get("director_max_tokens", 2048)`.
  - Best-effort: also pass `num_retries=0` to `LLM(...)`. **Verify in the log it actually suppresses the
    duplicate `idempotency_key`.** If it does NOT, add `os.environ.setdefault("OPENAI_MAX_RETRIES", "0")`
    in `bootstrap_pipeline.py` bootstrap() (before any LLM import) as the real kill-switch.
- **Config keys:**
  ```yaml
  script:
    writer_max_tokens: 1024
  models:
    director_max_tokens: 2048
  ```
- **Verify:** `--yes --dry-run` PTY run → segment-1 writer finishes well under 240s, NO duplicate
  `idempotency_key` line in the log.
- **Test:** `tests/test_llm_factory.py` — monkeypatch `core.main.LLM` with a recorder that captures kwargs;
  call `create_writer(cfg)` / `create_director(cfg)`; assert the recorded `max_tokens` == 1024 / 2048.
  (Do NOT read attrs off a real LLM object — that's not guaranteed to exist.)

### W2 · Writer returns STRUCTURED JSON via direct Ollama (PRIMARY) — kills HTML + meta-commentary
- **File:** `core/pipeline_long.py` — writer `crew.kickoff()` (~1690), revision `revision_crew.kickoff()` (~1745).
- **Problem:** free-text lets `cra-guided-7b` emit `</section>`, `<span ...>`, `</body></html>`,
  `[END_OF_TEXT]` AND meta-prose ("In response to your critique…") that the TTS then SPEAKS.
- **Change (PRIMARY — direct Ollama structured output, the only path that truly constrains generation):**
  - Replace the writer CrewAI kickoff with a call to `utils.ollama_client.get_ollama_client(config).generate(prompt, model=<writer model>, format_json=True)` where the prompt ends with the schema instruction:
    `Return ONLY JSON: {"narration": "<spoken narration text only, no HTML, no markdown, no stage directions, no commentary about your writing>"}`.
  - `json.loads()` the result, take `["narration"]`. This also brings the writer under the **B1 circuit breaker**.
  - Apply the SAME structured call to the revision path (flaw #3).
  - **Keep the CrewAI agents** (`create_writer`) only if other code depends on them; otherwise the writer can
    move fully to OllamaClient. Resolve the writer model name the same way `create_writer` does
    (`models.writer` with the scratch/adapt selection already computed upstream).
- **Fallback:** if `json.loads` fails or `narration` missing, fall back to the existing free-text +
  `_sanitize_narration` path so a bad response never crashes the run.
- **Verify:** logs show clean prose — no `</section>`, `<span`, `</html>`, "In response to your critique".
- **Test:** `tests/test_writer_structured.py` — mock `OllamaClient.generate` → `'{"narration":"Hello."}'`,
  assert extraction == "Hello."; mock malformed → assert free-text fallback runs without raising.

### W3 · Harden `_sanitize_narration` against meta-commentary (defense-in-depth)
- **File:** `core/pipeline_long.py` `_sanitize_narration` (~line 436).
- **Problem:** strips tags but not meta sentences; needed as a safety net even with W2.
- **Change:** add conservative, case-insensitive removal of meta lines/sentences starting with:
  `In response to (your|the) (critique|feedback|instructions)`, `The changes reflect`,
  `This version (aims|is|reflects)`, `Revised Script`, `Here('?s| is) the (revised|rewritten)`,
  `Now, each (detail|layer)`. Strip `**bold**` markers, residual `<...>`, `[END_OF_TEXT]`, `<!-- ... -->`.
  Only remove clearly non-spoken meta — never touch normal narration.
- **Verify:** feed the saved 591-word garbage fixture → clean spoken prose out; Devanagari untouched.
- **Test:** `tests/test_sanitize_meta.py` using `tests/fixtures/messy_writer_output.txt` (the real run sample).

### W4 · Replace word-count "correction" LLM calls with a LOCAL trim
- **File:** `core/pipeline_long.py` — the `_wc_max_retries` loop building `_fix_crew` (~1759–1810).
- **Problem:** over/under target fires up to 2 MORE full writer kickoffs (~240s each + retry).
- **Change:** local deterministic logic, no LLM:
  - **Over target:** keep whole sentences from the start until ≤ upper band `hi`, ending on a boundary
    (`.`/`।`/`!`/`?`).
  - **Under target:** cannot invent words — just **log and proceed** (slightly short narration is acceptable).
  - Only fall back to ONE LLM rewrite if `script.llm_word_fix: true` (default false).
- **Config:**
  ```yaml
  script:
    llm_word_fix: false
  ```
- **Verify:** a 591→150 trim happens in <1s with zero extra LLM calls.
- **Test:** `tests/test_word_trim.py` — over-target trims to ≤ `hi` on a boundary; under-target unchanged;
  Devanagari danda (।) is a boundary.

### W5 · (LAST RESORT, operator choice — NOT a default flip)
- `cra-guided-7b` leaks formatting, but W1+W2+W3 should make it usable (constrained JSON + sanitizer).
- Only if output is still poor AFTER W1-W3: document switching `models.writer_scratch` to `zephyr-writer`
  in config, with the honest trade-off (zephyr = cleaner but less flowery/creative). Do NOT change the
  default in code — leave it to the operator.

**PHASE W GATE:** `py_compile` touched files; `pytest tests/ -q` green; one `--yes --dry-run` where segment-1
script finishes <60s, NO duplicate idempotency_key, log shows clean narration. F5 model downloaded (W0).

---

# PHASE T — Make F5-TTS the default engine (mandatory)

### T1 · F5-TTS engine adapter + persistent worker
- **File:** new `audio/f5_worker.py` (mirror `audio/omnivoice_worker.py` persistent `--serve` design:
  load model once, line-delimited JSON over stdin). New `_call_f5_tts(...)` branch in `audio/audio_proxy.py`
  `tts_generate` dispatch.
- **Change:**
  - `normalize_tts_engine` learns `"f5"` (aliases: `f5`, `f5-tts`, `f5tts`).
  - Use `character_voices/narration_voice.wav` as the clone reference; pass `tts.f5.ref_text` to skip ASR.
  - Run inside the HEAVY scheduler slot after a verified evict (6GB rule), same as OmniVoice.
- **Config (F5 becomes the DEFAULT):**
  ```yaml
  tts:
    engine: "f5"            # MANDATORY default now (was omnivoice)
    f5:
      model_path: "hf_cache/f5_hindi"   # from W0 download
      ref_text: ""          # transcript of narration_voice.wav (skips ASR, saves VRAM)
      nfe_step: 16          # denoising steps; lower = faster
  ```
- **Fallback chain (keep robustness):** if F5 import/model is missing or errors at runtime →
  fall back to `omnivoice` → `edge`, and record a B2 degradation (`tts_engine_fallback`). This means a
  machine without the F5 download still runs (just slower), so the change is safe.
- **Verify:** default run uses F5; a segment's audio renders faster than the OmniVoice baseline; voice is
  intelligible Hindi cloned from the reference; killing the F5 model triggers the OmniVoice fallback + degradation.
- **Test:** `tests/test_tts_engine_select.py` — `normalize_tts_engine("f5")=="f5"`; with F5 worker mocked,
  `tts_generate` returns the mocked wav; on F5 error it falls back to omnivoice and records a degradation.

**PHASE T GATE:** existing TTS tests pass; default config now `engine: f5`; real run produces Hindi audio
faster than OmniVoice; fallback proven by simulating a missing F5 model.

---

# PHASE V — Real motion video (optional, heavy, opt-in)

### V1 · FramePack image-to-video behind a flag
- **File:** new `video/image_gen/framepack_i2v.py` + a hook in `core/pipeline_long.py` after images, before render.
- **Install (document, do NOT auto-install):** FramePack (lllyasviel) weights → `hf_cache/`. Multi-GB, slow on
  6GB — a quality, not speed, feature.
- **Change:** when `video.motion_engine: "framepack"`, turn each scene PNG into a short MP4 (2–4s motion),
  feed clips to the assembler. When `"none"` (default) → current Ken Burns, unchanged. Must run in the HEAVY
  slot after verified evict; never co-resident with SD/LLM/F5.
- **Config:**
  ```yaml
  video:
    motion_engine: "none"          # none = Ken Burns (default) | framepack = real i2v motion
    motion_seconds_per_image: 3
  ```
- **Verify:** `framepack` produces moving clips + final MP4 on 6GB without OOM; `none` is byte-identical to today.
- **Test:** `tests/test_motion_engine.py` — resolver returns static path for `none`, framepack path for
  `framepack` (mock the call; assert heavy-slot wiring).

**PHASE V GATE:** default-off identical to today; flag-on produces motion on 6GB without OOM.

---

## All new config keys (one block)

```yaml
script:
  writer_max_tokens: 1024
  llm_word_fix: false
models:
  director_max_tokens: 2048
tts:
  engine: "f5"              # MANDATORY default (fallback: omnivoice -> edge)
  f5:
    model_path: "hf_cache/f5_hindi"
    ref_text: ""
    nfe_step: 16
video:
  motion_engine: "none"     # none | framepack
  motion_seconds_per_image: 3
```
No schema-class edits needed (`script`/`models`/`tts`/`video` are loose Dict / extra='allow'; `memory` was
already fixed Dict[str,str] → Dict[str,Any]).

## New tests
`test_llm_factory`, `test_writer_structured`, `test_sanitize_meta`, `test_word_trim`,
`test_tts_engine_select`, `test_motion_engine`.
Fixture: save the real 591-word garbage writer output as `tests/fixtures/messy_writer_output.txt`.

## Build order
1. **W0** — start F5 download in the background (operator, one command). Runs while W1–W5 are coded.
2. **W1** (max_tokens + retry kill) — fixes the timeout storm. Ship first.
3. **W3** (sanitizer meta-strip) — safety net.
4. **W4** (local word trim) — removes extra LLM rewrites.
5. **W2** (Ollama structured output) — the proper fix; brings writer under B1.
6. **W gate** → segment script time ~10-18 min → <1-2 min.
7. **T1** (F5 default) — only after W0 download is verified + a clone test passed.
8. **V1** (FramePack) — last, opt-in.

## Hard constraints
- 6GB: one resident model at a time; HEAVY slot + verified evict before any GPU load (writer LLM, F5, SD, FramePack).
- `max_workers=1` stays 1. Additive UIState only; `/api/status` unchanged.
- F5 is the default but MUST gracefully fall back to omnivoice→edge if its model/lib is absent — so a fresh
  checkout without the download still runs.
- All 189 existing tests stay green; add the 6 new tests.
- Read every new tunable via `config.get(section, {}).get(key, default)`.

## Open risk the implementer MUST resolve before T1 ships
F5 Hindi voice-CLONING quality from `narration_voice.wav` is unverified. Test one clip first. If cloning is
weak, either (a) accept F5's generic Hindi voice, or (b) keep OmniVoice as the cloning engine and use F5 only
where cloning isn't needed. Do not hard-cut OmniVoice out until the clone test passes.
