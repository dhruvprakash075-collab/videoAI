# Video.AI v6 — Unified Pipeline Plan (Revised)

Merge the best of **Video.AI** and **Ghost Creator AI** into a single local-only pipeline.
This plan **supersedes** the previous `implementation_plan.md` (v5) and incorporates
all 4 of your original design requirements + 2 corrections.

---

## Your Design Requirements (NON-NEGOTIABLE)

1. **Two entry options** at the start: **Upload Source** OR **Fresh Idea**.
2. **Upload Source path** → the Writer+Director hybrid's **only** job is to **split
   the source into parts** that are easy to make. No full story invention.
3. **Production node is a Writer+Director Hybrid**, not two separate nodes.
   Writing and self-critique happen in **one** node.
4. **Both paths converge** into the same production loop.

## Model Roster (v6.1, locked 2026-06-02)

You confirmed: **only 2 text models**, plus image + voice:

| Role | Model | Purpose | Always loaded? |
|---|---|---|---|
| **Director** | `hermes-director` (8B Q4) | Pre-production planning, story outline, SEO | No — evicts before GPU tasks |
| **Writer** | `zephyr-writer` (7B Q4) | Writes scripts **and** self-critiques via prompt swap | No — evicts before GPU tasks |
| ~~Critic~~ | ~~`script-reviewer`~~ | **REMOVED** — never created, was auto-approving everything | — |
| Image | SD 1.5 + LoRA | Image generation | Yes (own VRAM) |
| Voice | OmniVoice Hindi | TTS | Yes (own VRAM) |

**Why this justifies the hybrid node (Req #3):** the writer does both roles via
a prompt swap, not a model swap. Same model, same VRAM, one graph node —
no extra eviction between write and critique. The "two separate nodes" pattern
only makes sense when the critic is a *different* model. We're not doing that.

**Why removing the third model is correct:** your config.yaml line 18 says
`reviewer: "script-reviewer" # ... NOT created — degrades gracefully (auto-approve)`.
That means the critic was a no-op auto-approving every script. Self-critique via
prompt swap is strictly better than a fake critic.

## Your Corrections (carry over from v5 → locked in v6)

- ❌ **Remove Gemini** — use local Ollama models only (`hermes-director`, `zephyr-writer`).
  No cloud LLMs.
- ❌ **No stock footage** — Stable Diffusion + LoRA face-lock only. No Pexels, no yt-dlp
  B-roll, no Imagen fallback.
- ❌ **Long-form only** — 16:9 (1920×1080), segments of ~100 words each, target
  3 min – 3 hr. No 9:16 short-form mode.

---

## Architecture: Dual-Entry Pipeline

```
[ENTRY DECISION]
      │
      ├─── Upload Source ───► [Source Loader] ──► [Hybrid Node in SPLIT-MODE]
      │                                                   │
      └─── Fresh Idea ──────► [Web Researcher (Wikipedia + RSS)] ─┤
                              OR [Topic: user-provided] ───┤
                                                          ▼
                                                 [Story Plan: N segments]
                                                          │
      ┌────────────── [PRODUCTION LOOP, per segment] ──────────────────┐
      │  [Writer+Director Hybrid: write + self-critique, or pass-through│
      │   source text if source_chunks is set — no writing, no critique]│
      │      │ rejected & rewrites < max                               │
      │      └──── loop back to hybrid ────┐                            │
      │      ▼ approved                     │                            │
      │  [Translate: Sarvam → Devanagari]    │                            │
      │  [TTS: OmniVoice Hindi clone]        │                            │
      │  [SD Image Gen: LoRA face-lock]      │                            │
      │  [FFmpeg Render: Ken Burns + SRT]    │                            │
      └──────────────────────────────────────────────────────────────┘
                                                          │
                                                          ▼
                                            [Post: Concat + Thumbnail + Manifest]
                                                          │
                                                          ▼
                                            [SEO: Title + Description + Tags]
                                                          │
                                                          ▼
                                            {upload.enabled?} ─yes─► [Playwright YT Upload]
                                                          │ no
                                                          ▼
                                                    [Final MP4]
```

---

## What's Already Built (do NOT redo)

| File | Status | What it does |
|---|---|---|
| `core/pipeline_graph.py` | ✅ Built (v5) | LangGraph `StateGraph` skeleton with 6 nodes |
| `core/segment_runner.py:400-712` | ✅ Built (v5) | Rewired to call the graph instead of linear flow |
| `utils/youtube_uploader.py` | ✅ Built (v5) | Playwright upload to YouTube Studio |
| `setup_youtube_profile.py` | ✅ Built (v5) | One-time Chrome login helper |
| `utils/seo_generator.py` | ⚠️ Partial (v5) | Title + tags only — **missing description + chapters** |
| `critic_node` inside graph | ❌ Will be **removed** in Phase 4 | Replaced by writer self-critique via prompt swap |
| `video/renderer/assembler.py:585` (Whisper fallback) | ❌ Becomes dead code in Phase 0.5 | TTS worker pre-computes timestamps; assembler never falls back |

**Verified:** `pytest tests/ -q` → **290 passed** after the v5 changes. No regression.

---

## What's MISSING or Broken (must do in Phase 0)

These are the technical debt items the v5 work left behind. **Do this first, before
building anything new**, so we have a clean baseline.

| # | Problem | File | Fix |
|---|---|---|---|
| 1 | `langgraph`, `playwright`, `pytest-playwright` not pinned | `requirements.txt` | Add exact versions |
| 2 | `critic.*`, `research.*`, `seo.*`, `source.*` config keys missing (`upload:` already exists) | `config/config.yaml` + `config_schemas.py` | Add 4 missing sections; `upload:` already has all keys |
| 3 | Bare `except:` swallows the "Show More" click | `utils/youtube_uploader.py:106-107` | Catch `PlaywrightTimeoutError` only |
| 4 | Unused `import os` | `utils/youtube_uploader.py:4` | Delete |
| 5 | Zero tests for new code | `tests/test_*.py` | Add `test_pipeline_graph.py` + `test_youtube_uploader.py` + `test_seo_generator.py` |
| 6 | SEO description + chapter timestamps missing | `utils/seo_generator.py` | Add 2 new fields, prompt Ollama for them |
| 7 | `critic_node` calls a non-existent model (auto-approves everything) | `core/segment_runner.py:491` + `config.yaml:18` | **Remove `script-reviewer` from config and the reviewer call from segment_runner** — replaced by writer self-critique (see v6.1 model roster) |
| 8 | `config_schema.py` referenced in plan, but actual file is `config_schemas.py` (plural) | plan doc | Update all references |

**Phase 0 effort:** ~2–3 hours. **No new functionality**, just hygiene.

---

## Component Plan (build incrementally, ONE PER SESSION)

### Phase 0 — Hygiene & Baseline (this session)
- Add `langgraph>=0.2`, `playwright>=1.40`, `pytest-playwright>=0.5` to `requirements.txt`
- Add `critic:`, `research:`, `seo:`, `source:` sections to `config/config.yaml`
  (with matching Pydantic fields in `config/config_schemas.py`). `upload:` already exists — skip it.
- Add `critic_max_rewrites: 2` and `critic_threshold: 60` to the existing `script:` section in
  `config/config.yaml` so operators can tune without touching code (currently only read via `.get()` defaults)
- Add `source:` section keys: `allowed_extensions`, `max_words` (default 50000, soft cap), `url_timeout_s` (default 30), `user_agent` (default `VideoAI/6.0 (+https://github.com/...)`)
- Fix `youtube_uploader.py`: bare except + unused import
- Write `tests/test_pipeline_graph.py`:
  - graph builds without error
  - `route_after_critic` returns END on `aborted`
  - `route_after_critic` returns `write_script_node` when `critic_approved=False` and `rewrites < max`
  - `route_after_critic` returns `translate_node` when `rewrites >= max` (forgiving escape hatch)
  - `route_after_critic` returns `translate_node` when `critic_approved=True`
  - state propagation: `script` survives across `write → critic` (regression for AGENTS.md "atomic state" rule)
- Add 2 missing fields to `seo_generator.py` (`description`, `chapters`)
- Verify: `pytest tests/ -q` → 290+N passed, `ruff check .` → 0 errors

### Phase 0.5 — TTS Timestamp Fix (move Whisper from renderer to TTS worker)

**Bug discovered during v6.1 review:** OmniVoice and F5 workers return only
`{"status": "success", "wav_path": wav}` — no `word_timestamps` key. The
pipeline checks for this key in `audio/audio_proxy.py:898`, gets `None`, and
the renderer (`assembler.py:585`) falls back to running Whisper for **every
single segment** to generate subtitle timing.

**Cost today:** ~10 min CPU per 60-segment video + 15-25% WER on Hindi
subtitles (because generic `tiny`/`base` Whisper mis-segments Devanagari).

**The fix:** move the Whisper alignment call from the renderer to the TTS
worker. After TTS produces audio, run faster-whisper once on that audio,
save word timestamps as `{wav_path}.words.json`, and include the path in
the worker's JSON output. The renderer's existing Step 1 (use provided
JSON) then always succeeds, and Step 2 (Whisper fallback) is never taken.

#### [NEW] [audio/tts_alignment.py](file:///C:/Video.AI/audio/tts_alignment.py)
- Single function: `align_audio(wav_path: Path, model_name: str = "base") -> Path`
  - Loads faster-whisper (cached module-level, same pattern as assembler)
  - Runs `transcribe(wav_path, word_timestamps=True, vad_filter=True)` on **CPU int8**
    (no VRAM impact; doesn't need to evict Ollama because TTS already evicted)
  - Writes `{wav_path}.words.json` with `[{"word", "start", "end"}, ...]`
  - Returns the JSON path
- Config-driven: `tts.alignment.enabled: true` (default on), `tts.alignment.model: "base"`
- Graceful failure: if Whisper errors, log warning and return None (don't fail TTS)

#### [MODIFY] [audio/omnivoice_worker.py](file:///C:/Video.AI/audio/omnivoice_worker.py)
- In both persistent (`_run_persistent`) and one-shot (`_run_oneshot`) modes,
  after `wav` is generated:
  - If `tts.alignment.enabled`: call `align_audio(wav)`, get JSON path
  - Include `word_timestamps: <path>` in the JSON output
- Persistent mode: align runs **once per request, on the worker side** (no
  IPC change — just one more line in the result dict)

#### [MODIFY] [audio/f5_worker.py](file:///C:/Video.AI/audio/f5_worker.py)
- Same treatment as OmniVoice worker

#### [MODIFY] [video/renderer/assembler.py](file:///C:/Video.AI/video/renderer/assembler.py)
- Add a `WARNING` log (not ERROR) when the Whisper fallback is invoked, with
  the segment number. This is a regression detector: if Phase 0.5 is working,
  this log should fire **0 times** per run.
- No other code change — the existing 3-step priority (JSON → Whisper →
  proportional) already handles the new JSON correctly.

#### [MODIFY] [config/config.yaml](file:///C:/Video.AI/config/config.yaml)
```yaml
tts:
  alignment:
    enabled: true          # run faster-whisper on TTS output for word timestamps
    model: "base"          # tiny | base | small | vasista22/whisper-hindi-small
    device: "cpu"          # CPU int8 — no VRAM contention
    compute_type: "int8"
```

#### [MODIFY] [config/config_schemas.py](file:///C:/Video.AI/config/config_schemas.py)
- Add `AlignmentConfig` Pydantic model with the 4 fields above
- Add `alignment: AlignmentConfig` field to existing `TTSConfig`

#### [NEW] [tests/test_tts_alignment.py](file:///C:/Video.AI/tests/test_tts_alignment.py)
- 10 tests:
  - `align_audio()` writes JSON next to WAV
  - JSON format matches what `assembler._words_to_srt_lines()` expects
  - Whisper exception → returns None, doesn't raise
  - Config flag `enabled: false` → `align_audio()` not called by TTS worker
  - `enabled: true, model: base` → correct model name passed to faster-whisper
- Update `tests/test_audio_crossfade.py` (existing 8 tests) to assert
  that `word_timestamps_json` path is populated, not None
- Add 1 integration test: TTS worker mock + alignment enabled → result dict
  has `word_timestamps` key with a real path

**Phase 0.5 effort:** ~80 LoC + ~150 LoC tests = **~1 day**

**Acceptance criteria specific to Phase 0.5:**
1. ✅ All Phase 0 tests still pass
2. ✅ 10 new alignment tests pass
3. ✅ `ruff check .` clean
4. ✅ Manual: run a real segment; `studio_outputs/<topic>/audio/seg01.json` shows
   `"word_timestamps": "...path..."` (not `null`)
5. ✅ Manual: renderer log shows `"Using provided word timestamps JSON"` for
   every segment, **never** `"Generating word-level subtitles using Whisper"`
6. ✅ Manual: zero warnings from the new `assembler.py` regression detector

### Phase 1 — Source Ingestion (`utils/source_loader.py`) [new]
- Accepts **5 input types**:
  - **`.txt`** — read as UTF-8, strip BOM, normalize line endings
  - **`.md`** — same as `.txt` + strip front-matter (YAML between `---` markers) into metadata
  - **`.pdf`** — `pypdf` for text extraction (no OCR; scanned PDFs are rejected with a clear error)
  - **`.docx`** — `python-docx`, iterates paragraphs in order, preserves headings
  - **URL** — `trafilatura` for main-content extraction + `requests` for fetching; sets a `User-Agent` header per Wikimedia ToS (same convention as Phase 3)
  - **Pasted text** — passed through directly (no parsing)
- Detection: dispatch by `pathlib.Path.suffix` for files, by `http(s)://` prefix for URLs, by string type for paste
- Output: `SourceDocument { text, word_count, language, source_type, metadata }`
  - `source_type` is one of: `txt | md | pdf | docx | url | paste`
  - `metadata` includes: filename/URL, page count (PDF), author (docx), fetch date (URL)
- Pure function — no LLM calls
- Config: `source.allowed_extensions`, `source.max_words` (default 50000, **soft cap — warn but proceed**), `source.url_timeout_s` (default 30), `source.user_agent` (default `VideoAI/6.0 (+https://github.com/...)`)
- **Tests** `tests/test_source_loader.py` (21 tests): 5 input types × 4 edge cases (empty / binary / oversize / malformed) + 1 dispatcher test
- **New Phase 1 dependencies** (add to `requirements.txt`):
  - `pypdf>=4.0` for `.pdf`
  - `python-docx>=1.1` for `.docx`
  - `trafilatura>=1.6` for URL main-content extraction
  - `requests>=2.31` (likely already present, verify)

### Phase 2 — Source Splitter (`utils/source_splitter.py`) [new, addresses Req #2 + #3]
- Reuses the Writer+Director LLM in a new **`mode="split"`** state
- Takes `SourceDocument` + planned segment count
- Returns `List[SegmentChunk]` with:
  - `text` (the actual source excerpt for that segment)
  - `b_roll_hint` (optional: 1-line visual cue for SD prompt)
  - `key_event` (1-line summary used in story plan)
- In **split mode**, the LLM is told:
  > "You are a **story structure analyst**, not a writer. Split the source text into
  > exactly N parts that flow as a coherent narrative. **Do not invent, do not paraphrase
  > heavily.** Preserve the original voice."
- This satisfies Req #2 (no full writing on source path) and Req #3 (hybrid node)
- **Config:** `source.split_strategy: by_chapter | by_word_count | by_llm`
  - `by_chapter` reads H1/H2 in `.md` and Heading 1/2 styles in `.docx`; falls back to `by_word_count` for `.txt`/`.pdf`/URL
  - `by_word_count` splits at every ~100 words with sentence-boundary detection
  - `by_llm` calls the writer model once to chunk intelligently (most flexible, slowest)
- **Tests** `tests/test_source_splitter.py` (10 tests): mocks LLM, verifies chunk count + boundaries + preserves source verbatim in non-by-llm mode

### Phase 3 — Web Researcher (`utils/web_researcher.py`) [new, addresses v5 C4 gap]
- Named `web_researcher.py` to avoid collision with existing `utils/topic_researcher.py` (LLM brainstorming).
- **No Tavily, no paid APIs, no Google Trends (flaky).** All local-friendly:
  - **Wikipedia REST API** (`https://en.wikipedia.org/api/rest_v1/`) — search + page summary
  - **Wikimedia REST API** (`https://api.wikimedia.org/`) — pageviews, most-read
  - **RSS feeds** (TechCrunch, NDTV, BBC, etc., configurable list) — uses `feedparser`
- **All HTTP calls set a `User-Agent` header** (Wikimedia ToS requirement; gets 403'd without it)
- Returns `TrendingTopic { title, summary, source_url, source_type, score }`
- Config: `research.sources: [wikipedia, rss]`, `research.rss_urls: [...]`, `research.user_agent: "VideoAI/6.0 ..."`
- Called from `bootstrap_pipeline.py` ONLY when `--topic` is empty AND `--no-auto-research` is NOT set
- **Tests** `tests/test_web_researcher.py` (10 tests, all HTTP mocked): no internet needed in CI

### Phase 4 — Writer Self-Critique (`utils/writer_hybrid.py` + `prompts/critic_prompt.txt`) [new, addresses v5 C2 gap + v6.1 model roster]
- **One model, two prompts, one graph node.** The writer model gets:
  - `WRITER_PROMPT` for drafting the script
  - `CRITIC_PROMPT` for self-reviewing the draft
- Both prompts are versioned text files in `prompts/` so you can iterate without touching code
- **5-dimension rubric** (adopted from Ghost Creator, scaled to 0-20 each → 0-100 total):
  - **Hook** (0-20): do first 2 sentences grab attention?
  - **Emotional arc** (0-20): does tension build?
  - **Pacing** (0-20): sentence-length variety, no info dumps?
  - **Retention** (0-20): would a viewer stay for the next segment?
  - **TTS-friendliness** (0-20): no unicode that OmniVoice mangles, no abbreviations like "Dr." that break pacing, no <100ms pauses worth of punctuation clusters
- Returns `WriterVerdict { script, score, breakdown, feedback, attempts }`
- Threshold default 60, max 2 rewrites (config-driven, CLI-overridable)
- **Why tts_friendliness?** Because OmniVoice's Hindi TTS has known artifacts on certain inputs. Better to catch them before TTS than after.
- **Tests** `tests/test_writer_hybrid.py` (15 tests): mocked Ollama, rubric boundary tests (59→reject, 60→approve, all 5 categories independently scored, tts-friendliness catches `Dr.`, `etc.`, mixed scripts)

### Phase 5 — SEO Completion (`utils/seo_generator.py`) [extend existing]
- Add 2 fields to `SEOMetadata`:
  - `description` (str, max 5000 chars): "In this video..." + chapter timestamps — generated by Ollama
  - `chapters` (list of `{ts, title}`): computed deterministically from `run_manifest.json` segment boundaries — **no LLM needed**
- Description is a single Ollama call; chapters are pure timestamp formatting (`HH:MM:SS`)
- **Tests** `tests/test_seo_generator.py` (8 tests): exact field validation, chapter timestamp format `HH:MM:SS`

### Phase 6 — YouTube Upload Hardening
- `tests/test_youtube_uploader.py` (10 tests): mock Playwright, verify file selection, tag fill, visibility radio, done-button click
- `upload:` config section already exists in `config/config.yaml` (enabled, platform, visibility, profile_dir) — no config changes needed
- Consider changing `upload.enabled` default from `true` to `false` for safer opt-in behavior

---

## Critical Architectural Decisions (v5 → v6)

### Hybrid Node Design (Req #3)

The current v5 graph has 6 separate nodes. The user's Req #3 says "it is not only
writer node it is writer and director hybrid." The cleanest interpretation:

**Inside the Writer+Director Hybrid Node, the LLM is called in a loop** —
each iteration = 1 writer call + 1 critic call. With max 2 rewrites, worst
case is 3 iterations = **6 LLM calls** (2–6 typical). The node itself is a
single state transition:

```
┌────────────────────────────────────────────────────────┐
│  WRITER + DIRECTOR HYBRID NODE                         │
│  (2–6 LLM calls: 1 writer + 1 critic per iteration)   │
│                                                        │
│  for attempt in range(max_rewrites + 1):               │
│    1. call_llm(WRITER_PROMPT) → draft script           │
│    2. call_llm(CRITIC_PROMPT, draft) → score + feedback│
│    3. if score >= threshold: break                      │
│       else: feed feedback back into next iteration     │
│                                                        │
│  return { script, critic_score, critic_feedback,       │
│            rewrites_attempted }                        │
└────────────────────────────────────────────────────────┘
```

**Why a single node, not two separate nodes (v6.1 justification)?** Because the
writer and critic are the **same model** (`zephyr-writer`) with a prompt swap.
Ollama keeps it loaded (`keep_alive: 3m` per `config.yaml:25`), so the swap is
essentially free. Splitting them into separate nodes would force a
`route_after_critic` conditional edge and lose the "always-on" model residency.
**One node is the correct architecture for the 2-model roster.**

The previous concern was: "the critic's feedback
needs to feed back into the same LLM's *next* call for the rewrite." This is
still true: in the 2-model setup, keeping them fused in one node means the
prompt-swap is local to the node and no state round-trip is needed.

**Concretely, this changes the v5 graph:** merge `write_script_node` +
`critic_node` into one `writer_director_hybrid_node`. The `route_after_critic`
edge goes away — the hybrid decides internally and returns a state with
either `critic_approved=True` (continue) or routes to END on failure.

**Translate stays as a separate graph node** (not inside the hybrid). The hybrid's
job is write+critique only. Translation uses the Sarvam model — a different model
from Writer/Director — so there's no model-load savings from merging it in. The
existing `translate_node` in `pipeline_graph.py` is correct as-is.

### Dual-Entry Handling (Req #1 + #2)

The `SourceLoader` is called from `bootstrap_pipeline.py` based on a new
flag `--source <path|url>` (all 5 input types supported). This is a **new,
distinct flag** — the existing `--file` flag (adaptation mode) is kept
separate. The `SegmentState` gets a new field
`source_chunks: List[SegmentChunk]` that is populated by Phase 2's splitter.

**On the source path:**
1. Director still runs (once) — it sets `target_word_count`, `target_duration`,
   `narrative_arc` for the run. It does **not** invent segment topics.
2. Splitter runs (once) — chunks the source into N `SegmentChunk`s using
   `by_chapter | by_word_count | by_llm` strategy.
3. In the production loop, the hybrid node reads `source_chunks[i]` and
   **skips the write+critique loop entirely** — no LLM call per segment.
4. Translation (Sarvam) runs **always** (even if source is already Devanagari) —
   Sarvam normalizes spelling/formatting. This is the safe, predictable default.
5. Result: **1-2 LLM calls per run** (Director + optional Splitter), vs 60-360
   for the fresh-idea path. ~99% LLM-call reduction.

**On the fresh-idea path:**
1. Director runs (once) — invents segment topics.
2. No splitter.
3. In the production loop, the hybrid node writes + critiques per segment.
4. Translation runs as today.
5. Result: 1 + (2-6 × N) LLM calls per run.

### VRAM Safety (Reuse Existing Guard Rails)

Per AGENTS.md "Critical rules": every GPU task goes through
`global_scheduler.task("heavy", ...)`. Both TTS and SD already do this.
**No new code may touch GPU without this wrapper** — it is the project rule.

---

## Locked Decisions (from user, 2026-06-02)

| Question | Answer |
|---|---|
| **Model roster (v6.1)** | **2 text models only**: `hermes-director` + `zephyr-writer`. Remove `script-reviewer`. Writer self-critiques via prompt swap. |
| **Source formats (Phase 1)** | **5 types**: `.txt`, `.md`, `.pdf`, `.docx`, URL fetch (plus pasted text) |
| **Research sources (Phase 3)** | Wikipedia REST + Wikimedia REST + RSS (skip Google Trends — flaky) |
| **Script critic (Phase 4)** | Threshold **60**, max **2 rewrites**, 5-dimension rubric (incl. tts_friendliness) |
| **YouTube upload (Phase 6)** | `enabled: false` (opt-in), with full test coverage |
| **Config schema file** | `config_schemas.py` (plural) — plan doc had a typo |
| **Director on source path** | Runs but constrained: sets target_word_count + arc, doesn't invent topics |
| **`--source` vs `--file`** | New distinct flag (`--source` for the new dual-entry flow; `--file` kept for adaptation mode) |
| **Source size cap** | Soft cap (default 50,000 words): warn but proceed if over |
| **Translation on source path** | Always runs (Sarvam), even if source is already Devanagari |
| **TTS timestamp alignment (Phase 0.5)** | Move faster-whisper from renderer to TTS worker; TTS always returns `word_timestamps`; renderer fallback path becomes dead code (regression detector warns if it fires) |

---

## Acceptance Criteria (per phase)

For every phase to count as "done":

1. ✅ All new tests pass (`pytest tests/ -q`)
2. ✅ All 290+ existing tests still pass (no regression)
3. ✅ `ruff check .` is clean
4. ✅ New config keys added to **both** `config/config.yaml` AND `config/config_schemas.py`
5. ✅ If the new code touches GPU, it uses `global_scheduler.task("heavy", ...)`
6. ✅ If the new code calls Ollama, it uses `guarded_ollama_call` (B1 breaker) or
   `OllamaClient` — never raw `urllib`
7. ✅ If the new code calls `crew.kickoff()`, it uses `guarded_crewai_kickoff`
   and acquires `crewai_lock`
8. ✅ A short note in the relevant `.kiro/specs/` or `docs/` directory
   summarizing what was added and why (so the next session has context)

---

## Effort Estimates

| Phase | New LoC | New Tests | Calendar Time |
|---|---|---|---|
| 0: Hygiene | ~50 | ~30 | 2–3 hrs |
| **0.5: TTS timestamp fix** | **~80** | **~150** | **1 day** |
| 1: Source loader (5 types) | ~280 | ~21 → ~100 (with parametrize) | 1.5 days |
| 2: Source splitter | ~150 | ~200 | 1 day |
| 3: Researcher | ~250 | ~300 | 1.5 days |
| 4: Script critic (5-dim) | ~180 | ~250 | 1 day |
| 5: SEO complete | ~80 | ~120 | 0.5 day |
| 6: YT upload tests | ~30 | ~150 | 0.5 day |
| **Total** | **~1100** | **~1350** | **~8–9 working days** |

The plan is designed to ship in **incremental, reviewable chunks**, not all
at once. Each phase ends with a green test suite you can run.

---

## What This Plan Did NOT Call For

- ❌ Refactoring `core/director_agent.py` (2,400 lines) — that's a separate
  multi-day effort tracked elsewhere
- ❌ Changing the local-only constraint or the 6GB VRAM profile
- ❌ Adding new models to Ollama (you have 2 text models + 1 image + 1 voice; more is a future roadmap item)
- ❌ Multi-language beyond Hindi (Hindi-first, English support is "what comes out
  of Sarvam translation")
- ❌ IP-Adapter, FramePack, Real-ESRGAN, music, voice acting — see `FUTURE_ROADMAP.md`
