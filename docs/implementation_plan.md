# Video.AI — Implementation Plan

> **⚠️ HISTORICAL DOCUMENT (2026-06-02) — NOT AUTHORITATIVE ⚠️**
> This document describes the v6 pipeline implementation plan. The v6 phases
> (source loader, splitter, researcher, critic, SEO, YouTube upload) are all
> **complete**. Later work (Supertonic 3 TTS, Bonsai 4B + IP-Adapter, pipeline
> hardening, Dashboard fixes) is tracked in `docs/AGENTS.md` and
> `docs/bug_resolution_history.md`. See `docs/CLAUDE.md` for current state.
>
> **v6.2 (latest alteration, 2026-06-02)**
> This document consolidated three previously-separate documents
> (v6 plan, architecture diagram, Phase 0+0.5 brief) into one file.
> The originals are preserved under `_archive/v6_planning_2026-06-02/`.

> **Read in this order:**
> 1. §1 Plan — what we're building and why (the v6.1 model roster, the dual-entry flow, the hybrid node)
> 2. §2 Diagram — visual flow + node inventory
> 3. §3 Brief — Phase 0 + 0.5 implementation-ready tasks (start here to execute)
> 4. Appendix A — the local-UI thread-safety plan (recently executed, preserved as prior alteration)
> 5. Appendix B — pointers to archived source documents

---

## Status: Milestone Tracker (2026-06-04)

| Milestone | Status | Date | Reference |
|---|---|---|---|
| Phase 0 hygiene (8 fixes) | ✅ Done | 2026-06-02 | §3.3 below |
| Phase 0.5 TTS timestamp fix | ✅ Done | 2026-06-02 | §3.4 below |
| v6 Phase 1: source loader | ✅ Done | 2026-06-02 | `test_source_loader.py` (57 tests) |
| v6 Phase 2: source splitter | ✅ Done | 2026-06-02 | `test_source_splitter.py` (57 tests) |
| v6 Phase 3: researcher | ✅ Done | 2026-06-02 | `test_researcher.py` (31 tests) |
| v6 Phase 4: critic + bypass | ✅ Done | 2026-06-02 | `test_critic.py` (51 tests) |
| v6 Phase 5: SEO generator | ✅ Done | 2026-06-02 | `test_seo_generator_extended.py` (58 tests) |
| v6 Phase 6: YouTube upload | ✅ Done | 2026-06-02 | `test_youtube_uploader.py` (24) + `test_youtube_profile_setup.py` (10) |
| **Dashboard refactor + Vitest** | ✅ Done | 2026-06-03 | 163 tests, 96.04% coverage |
| **Supertonic 3 TTS integration** | ✅ Done | 2026-06-03 | `audio/supertonic_worker.py` |
| **Supertonic 3 = default TTS** | ✅ Done | 2026-06-04 | `config/config.yaml:31` |
| **3 DIY voice JSONs** | ✅ Done | 2026-06-04 | `character_voices/dhruv_voice_*.json` |
| **P6-1..3 bug fixes** | ✅ Done | 2026-06-04 | `bug_resolution_history.md` |
| TTS fallback chain | ✅ Done | 2026-06-04 | `audio/audio_proxy.py::tts_generate()` |
| Production 3-hour video | ⏳ Pending | — | Bottleneck now = image gen, not TTS |
| DMD2/LCM image acceleration | 🔜 Next | — | Would cut image gen 50% |
| FramePack motion | 🔜 Tier 2 | — | `RESEARCH_WHAT_TO_ADD.md` |
| Real-ESRGAN upscaler | 🔜 Tier 2 | — | Replaces Lanczos |

---

## Table of Contents

- [§1 Plan — Video.AI v6 Unified Pipeline](#1-plan--videoai-v6-unified-pipeline)
  - [1.1 Design Requirements (NON-NEGOTIABLE)](#11-design-requirements-non-negotiable)
  - [1.2 Model Roster (v6.1, locked)](#12-model-roster-v61-locked)
  - [1.3 Architecture: Dual-Entry Pipeline](#13-architecture-dual-entry-pipeline)
  - [1.4 What's Already Built](#14-whats-already-built)
  - [1.5 What's Missing or Broken (Phase 0 hygiene)](#15-whats-missing-or-broken-phase-0-hygiene)
  - [1.6 Phase 0.5 — TTS Timestamp Fix](#16-phase-05--tts-timestamp-fix)
  - [1.7 Phase 1 — Source Ingestion](#17-phase-1--source-ingestion)
  - [1.8 Phase 2 — Source Splitter](#18-phase-2--source-splitter)
  - [1.9 Phase 3 — Web Researcher](#19-phase-3--web-researcher)
  - [1.10 Phase 4 — Writer Self-Critique](#110-phase-4--writer-self-critique)
  - [1.11 Phase 5 — SEO Completion](#111-phase-5--seo-completion)
  - [1.12 Phase 6 — YouTube Upload Hardening](#112-phase-6--youtube-upload-hardening)
  - [1.13 Hybrid Node Design (Req #3 detail)](#113-hybrid-node-design-req-3-detail)
  - [1.14 Dual-Entry Handling (Req #1 + #2)](#114-dual-entry-handling-req-1--2)
  - [1.15 VRAM Safety](#115-vram-safety)
  - [1.16 Locked Decisions](#116-locked-decisions)
  - [1.17 Acceptance Criteria](#117-acceptance-criteria)
  - [1.18 Effort Estimates](#118-effort-estimates)
  - [1.19 What This Plan Did NOT Call For](#119-what-this-plan-did-not-call-for)
- [§2 Architecture Diagram (Mermaid)](#2-architecture-diagram-mermaid)
  - [2.1 Visual Flow](#21-visual-flow)
  - [2.2 Node Inventory](#22-node-inventory)
  - [2.3 Hybrid Node Internals](#23-hybrid-node-internals)
- [§3 Phase 0 + 0.5 Implementation Brief](#3-phase-0--05-implementation-brief)
  - [3.0 Project Context](#30-project-context)
  - [3.1 Critical Rules (DO NOT BREAK)](#31-critical-rules-do-not-break)
  - [3.2 Verification Baseline](#32-verification-baseline)
  - [3.3 Phase 0 Tasks (hygiene — 8 fixes)](#33-phase-0-tasks-hygiene--8-fixes)
  - [3.4 Phase 0.5 Tasks (TTS Timestamp Fix)](#34-phase-05-tasks-tts-timestamp-fix)
  - [3.5 End-to-End Verification](#35-end-to-end-verification)
  - [3.6 Manual Smoke Test](#36-manual-smoke-test)
  - [3.7 Rollback](#37-rollback)
  - [3.8 Common Pitfalls](#38-common-pitfalls)
  - [3.9 Done Criteria](#39-done-criteria)
  - [3.10 Files You Will Touch](#310-files-you-will-touch)
- [Appendix A — Local UI Thread-Safety Plan (prior alteration, preserved)](#appendix-a--local-ui-thread-safety-plan-prior-alteration-preserved)
- [Appendix B — Archived Source Documents](#appendix-b--archived-source-documents)

---

# §1 Plan — Video.AI v6 Unified Pipeline

Merge the best of **Video.AI** and **Ghost Creator AI** into a single local-only pipeline.
This plan **supersedes** the previous `implementation_plan.md` (v5) and incorporates
all 4 of your original design requirements + 2 corrections.

---

## 1.1 Design Requirements (NON-NEGOTIABLE)

1. **Two entry options** at the start: **Upload Source** OR **Fresh Idea**.
2. **Upload Source path** → the Writer+Director hybrid's **only** job is to **split
   the source into parts** that are easy to make. No full story invention.
3. **Production node is a Writer+Director Hybrid**, not two separate nodes.
   Writing and self-critique happen in **one** node.
4. **Both paths converge** into the same production loop.

## 1.2 Model Roster (v6.1, locked 2026-06-02)

You confirmed: **only 2 text models**, plus image + voice:

| Role | Model | Purpose | Always loaded? |
|---|---|---|---|
| **Director** | `hermes-director` (8B Q4) | Pre-production planning, story outline, SEO | No — evicts before GPU tasks |
| **Writer** | `zephyr-writer` (7B Q4) | Writes scripts **and** self-critiques via prompt swap | No — evicts before GPU tasks |
| ~~Critic~~ | ~~`script-reviewer`~~ | **REMOVED** — never created, was auto-approving everything | — |
| **Image (2026-06-04)** | **Bonsai 4B ternary + IP-Adapter FLUX v2** | Image generation — FLUX-quality on 6GB VRAM, lazy per-character master portrait | Yes (~3.5 GB peak) |
| **Voice (2026-06-04)** | **Supertonic 3 + DIY Hindi clone** | TTS — **CPU ONNX, 0 VRAM**, can run concurrent with SD | CPU only |
| Voice (fallback 1) | OmniVoice Hindi | Higher-quality GPU fallback | ~2 GB VRAM |
| Voice (fallback 2) | Edge TTS (Azure neural) | Last-resort cloud-free neural | 0 VRAM (network) |

**Why this justifies the hybrid node (Req #3):** the writer does both roles via
a prompt swap, not a model swap. Same model, same VRAM, one graph node —
no extra eviction between write and critique. The "two separate nodes" pattern
only makes sense when the critic is a *different* model. We're not doing that.

**Why removing the third model is correct:** your config.yaml line 18 says
`reviewer: "script-reviewer" # ... NOT created — degrades gracefully (auto-approve)`.
That means the critic was a no-op auto-approving every script. Self-critique via
prompt swap is strictly better than a fake critic.

## 1.3 Architecture: Dual-Entry Pipeline

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
      │  [Image Gen: Bonsai + IP-Adapter]    │                            │
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

## 1.4 What's Already Built (do NOT redo)

| File | Status | What it does |
|---|---|---|
| `core/pipeline_graph.py` | Built (v5) | LangGraph `StateGraph` skeleton with 6 nodes |
| `core/segment_runner.py:400-712` | Built (v5) | Rewired to call the graph instead of linear flow |
| `utils/youtube_uploader.py` | Built (v5) | Playwright upload to YouTube Studio |
| `setup_youtube_profile.py` | Built (v5) | One-time Chrome login helper |
| `utils/seo_generator.py` | Partial (v5) | Title + tags only — **missing description + chapters** |
| `critic_node` inside graph | Will be **removed** in Phase 4 | Replaced by writer self-critique via prompt swap |
| `video/renderer/assembler.py:585` (Whisper fallback) | Becomes dead code in Phase 0.5 | TTS worker pre-computes timestamps; assembler never falls back |

**Verified:** `pytest tests/ -q` → **290 passed** after the v5 changes. No regression.

## 1.5 What's Missing or Broken (must do in Phase 0)

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
| 8 | ~~`config_schema.py` referenced in plan, but actual file is `config_schemas.py` (plural)~~ | plan doc + `docs/AGENTS.md:380, 558, 590` | **DONE 2026-06-04** — all references updated to `config_schemas.py` (plural) |

**Phase 0 effort:** ~2–3 hours. **No new functionality**, just hygiene.

## 1.6 Phase 0.5 — TTS Timestamp Fix (move Whisper from renderer to TTS worker)

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

### [NEW] `audio/tts_alignment.py`
- Single function: `align_audio(wav_path: Path, model_name: str = "base") -> Path`
  - Loads faster-whisper (cached module-level, same pattern as assembler)
  - Runs `transcribe(wav_path, word_timestamps=True, vad_filter=True)` on **CPU int8**
    (no VRAM impact; doesn't need to evict Ollama because TTS already evicted)
  - Writes `{wav_path}.words.json` with `[{"word", "start", "end"}, ...]`
  - Returns the JSON path
- Config-driven: `tts.alignment.enabled: true` (default on), `tts.alignment.model: "base"`
- Graceful failure: if Whisper errors, log warning and return None (don't fail TTS)

### [MODIFY] `audio/omnivoice_worker.py`
- In both persistent (`_run_persistent`) and one-shot (`_run_oneshot`) modes,
  after `wav` is generated:
  - If `tts.alignment.enabled`: call `align_audio(wav)`, get JSON path
  - Include `word_timestamps: <path>` in the JSON output
- Persistent mode: align runs **once per request, on the worker side** (no
  IPC change — just one more line in the result dict)

### [MODIFY] `audio/f5_worker.py`
- Same treatment as OmniVoice worker

### [MODIFY] `video/renderer/assembler.py`
- Add a `WARNING` log (not ERROR) when the Whisper fallback is invoked, with
  the segment number. This is a regression detector: if Phase 0.5 is working,
  this log should fire **0 times** per run.
- No other code change — the existing 3-step priority (JSON → Whisper →
  proportional) already handles the new JSON correctly.

### [MODIFY] `config/config.yaml`
```yaml
tts:
  alignment:
    enabled: true          # run faster-whisper on TTS output for word timestamps
    model: "base"          # tiny | base | small | vasista22/whisper-hindi-small
    device: "cpu"          # CPU int8 — no VRAM contention
    compute_type: "int8"
```

### [MODIFY] `config/config_schemas.py`
- Add `AlignmentConfig` Pydantic model with the 4 fields above
- Add `alignment: AlignmentConfig` field to existing `TTSConfig`

### [NEW] `tests/test_tts_alignment.py`
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
1. All Phase 0 tests still pass
2. 10 new alignment tests pass
3. `ruff check .` clean
4. Manual: run a real segment; `studio_outputs/<topic>/audio/seg01.json` shows
   `"word_timestamps": "...path..."` (not `null`)
5. Manual: renderer log shows `"Using provided word timestamps JSON"` for
   every segment, **never** `"Generating word-level subtitles using Whisper"`
6. Manual: zero warnings from the new `assembler.py` regression detector

## 1.7 Phase 1 — Source Ingestion (`utils/source_loader.py`) [new]
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

## 1.8 Phase 2 — Source Splitter (`utils/source_splitter.py`) [new, addresses Req #2 + #3]
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

## 1.9 Phase 3 — Web Researcher (`utils/web_researcher.py`) [new, addresses v5 C4 gap]
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

## 1.10 Phase 4 — Writer Self-Critique (`utils/writer_hybrid.py` + `prompts/critic_prompt.txt`) [new, addresses v5 C2 gap + v6.1 model roster]
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

## 1.11 Phase 5 — SEO Completion (`utils/seo_generator.py`) [extend existing]
- Add 2 fields to `SEOMetadata`:
  - `description` (str, max 5000 chars): "In this video..." + chapter timestamps — generated by Ollama
  - `chapters` (list of `{ts, title}`): computed deterministically from `run_manifest.json` segment boundaries — **no LLM needed**
- Description is a single Ollama call; chapters are pure timestamp formatting (`HH:MM:SS`)
- **Tests** `tests/test_seo_generator.py` (8 tests): exact field validation, chapter timestamp format `HH:MM:SS`

## 1.12 Phase 6 — YouTube Upload Hardening
- `tests/test_youtube_uploader.py` (10 tests): mock Playwright, verify file selection, tag fill, visibility radio, done-button click
- `upload:` config section already exists in `config/config.yaml` (enabled, platform, visibility, profile_dir) — no config changes needed
- Consider changing `upload.enabled` default from `true` to `false` for safer opt-in behavior

## 1.13 Hybrid Node Design (Req #3)

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

## 1.14 Dual-Entry Handling (Req #1 + #2)

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

## 1.15 VRAM Safety (Reuse Existing Guard Rails)

Per AGENTS.md "Critical rules": every GPU task goes through
`global_scheduler.task("heavy", ...)`. Both TTS and SD already do this.
**No new code may touch GPU without this wrapper** — it is the project rule.

## 1.16 Locked Decisions (from user, 2026-06-02)

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

## 1.17 Acceptance Criteria (per phase)

For every phase to count as "done":

1. All new tests pass (`pytest tests/ -q`)
2. All 290+ existing tests still pass (no regression)
3. `ruff check .` is clean
4. New config keys added to **both** `config/config.yaml` AND `config/config_schemas.py`
5. If the new code touches GPU, it uses `global_scheduler.task("heavy", ...)`
6. If the new code calls Ollama, it uses `guarded_ollama_call` (B1 breaker) or
   `OllamaClient` — never raw `urllib`
7. If the new code calls `crew.kickoff()`, it uses `guarded_crewai_kickoff`
   and acquires `crewai_lock`
8. A short note in the relevant `.kiro/specs/` or `docs/` directory
   summarizing what was added and why (so the next session has context)

## 1.18 Effort Estimates

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

## 1.19 What This Plan Did NOT Call For

- Refactoring `core/director_agent.py` (2,400 lines) — that's a separate
  multi-day effort tracked elsewhere
- Changing the local-only constraint or the 6GB VRAM profile
- Adding new models to Ollama (you have 2 text models + 1 image + 1 voice; more is a future roadmap item)
- Multi-language beyond Hindi (Hindi-first, English support is "what comes out
  of Sarvam translation")
- IP-Adapter, FramePack, Real-ESRGAN, music, voice acting — see `FUTURE_ROADMAP.md`

---

# §2 Architecture Diagram (Mermaid)

Visual flow of the unified pipeline, mapped to your design requirements.

> **Req #1:** Two entry options — Upload Source OR Fresh Idea.
> **Req #2:** Upload Source path → hybrid only splits, does not write.
> **Req #3:** Production node is a Writer+Director Hybrid, not separate nodes.
> **Req #4:** Both paths merge into the same production loop.

## 2.1 Visual Flow

```mermaid
graph TD
    Start([User starts pipeline]) --> Choice{Entry choice}

    %% ── Path A: Upload Source (Req #1) ──────────────────────────────
    Choice -->|Upload Source|     SrcLoad[Source Loader<br/>.txt .md .pdf .docx URL + paste]
    SrcLoad --> SrcSplit[Source Splitter<br/>chunks source into N parts<br/>uses writer model with split prompt]
    SrcSplit --> SrcPlan[Story Plan:<br/>N segments from source]

    %% ── Path B: Fresh Idea (Req #1) ─────────────────────────────────
    Choice -->|Fresh Idea + topic| Director[Pre-Production:<br/>AI Director plans story]
    Choice -->|Fresh Idea, no topic|     Researcher[Web Researcher<br/>Wikipedia + RSS]
    Researcher --> Director
    Director --> FreshPlan[Story Plan:<br/>N segments from idea]

    %% ── Both paths converge (Req #4) ────────────────────────────────
    SrcPlan --> Hybrid
    FreshPlan --> Hybrid

    %% ── Production loop, per segment ────────────────────────────────
    subgraph ProdLoop [Production Loop - one pass per segment]
        direction TB
        Hybrid[Writer+Director Hybrid Node<br/>writer writes → self-critiques via prompt swap<br/>Req #3: same model = single node is correct]
        Translate[Translate to Devanagari<br/>Sarvam]
        TTS[Supertonic 3 TTS<br/>DIY Hindi voice clone<br/>2026-06-04]
        SD[Bonsai 4B Image Gen<br/>+ IP-Adapter v2<br/>2026-06-04]
        Render[FFmpeg Render<br/>Ken Burns + SRT]

        Hybrid -->|critic_approved| Translate
        Hybrid -.->|rejected, rewrites<max| Hybrid
        Translate --> TTS
        TTS --> SD
        SD --> Render
    end

    %% ── Post-production, shared ─────────────────────────────────────
    Render --> Post[Post-Production:<br/>Concat + Thumbnail + Manifest]
    Post --> SEO[SEO Generator<br/>Title + Description + Tags]
    SEO --> YT{Upload enabled?}
    YT -->|yes| Uploader[Playwright<br/>YouTube Upload]
    YT -->|no| Done
    Uploader --> Done([Final MP4])

    %% ── Styling ─────────────────────────────────────────────────────
    classDef entry fill:#1e3a5f,stroke:#4a90e2,color:#fff;
    classDef hybrid fill:#2d5a27,stroke:#4CAF50,color:#fff;
    classDef gpu fill:#5a3a1e,stroke:#ff9800,color:#fff;
    classDef post fill:#3d2f5b,stroke:#9c27b0,color:#fff;
    classDef choice fill:#5a1e1e,stroke:#f44336,color:#fff;

    class Choice,YT choice
    class SrcLoad,SrcSplit,Researcher,Director entry
    class Hybrid hybrid
    class TTS,SD,Render,Uploader gpu
    class Post,SEO,Translate post
```

## 2.2 Node Inventory (Req #3: hybrid is ONE node)

| Node | Lives in | Touches GPU? | LLM? |
|---|---|---|---|
| Source Loader | `utils/source_loader.py` (Phase 1) | No | No |
| Source Splitter | `utils/source_splitter.py` (Phase 2) | No | Yes (1 call, writer model, split prompt) |
| Web Researcher | `utils/web_researcher.py` (Phase 3) | No | No (HTTP only) |
| Pre-Production Director | `core/pre_production.py` (existing) | No | Yes |
| **Writer+Director Hybrid** | `core/pipeline_graph.py` (Phase 4) | No | **Yes (2–6 calls)** |
| Translate (Sarvam) | `core/pipeline_graph.py` (existing `translate_node`) | No | Yes |
| TTS (Supertonic 3) | `core/segment_runner.py` (existing) | No (CPU) | No |
| **Image Gen (Bonsai + IP-Adapter)** | `core/segment_runner.py` (existing) | **Yes** | No |
| FFmpeg Render | `core/segment_runner.py` (existing) | No | No |
| Post-Production | `core/post_production.py` (existing) | No | No |
| SEO Generator | `utils/seo_generator.py` (extend Phase 5) | No | Yes |
| YouTube Upload | `utils/youtube_uploader.py` (Phase 6 tests) | No | No |

**VRAM rule (from AGENTS.md):** every GPU-touching node (TTS, SD) MUST go
through `global_scheduler.task("heavy", ...)` so only one model is in VRAM
at a time on the 6GB RTX 4050.

## 2.3 Hybrid Node Internals (Req #3 detail)

The Writer+Director Hybrid is **one graph node** that internally does:

```
hybrid_node(state):
    if state.source_chunks is not None:
        # Source-upload path (Req #2) — no writing, no critique
        script = state.source_chunks[state.i]
        return { script, critic_score: 100, critic_approved: True, rewrites_attempted: 0 }
    else:
        # Fresh-idea path — full creative loop, SAME model, prompt-swap
        for attempt in range(max_rewrites + 1):
            script = call_llm(WRITER_PROMPT, plan, context)        # zephyr-writer
            verdict = call_llm(CRITIC_PROMPT, script)              # zephyr-writer, different prompt
            if verdict.score >= threshold:
                return { script, verdict, rewrites_attempted: attempt }
        return { script, verdict, rewrites_attempted: max_rewrites }   # forgiving escape hatch
```

**One node, justified by v6.1 model roster:** the writer and critic are the
**same model** (`zephyr-writer`) with different system prompts. Ollama keeps
the model loaded between calls (`keep_alive: 3m` per `config.yaml:25`), so
the prompt swap is essentially free. Splitting them into separate graph
nodes would force a `route_after_critic` conditional edge and lose the
"always-on" model residency. **One node is the correct architecture for the
2-model roster.**

---

# §3 Phase 0 + 0.5 Implementation Brief

> **Audience:** Another AI assistant (any model) with no prior context. Pick this
> up, follow it step by step, and produce a green test suite at the end.
>
> **Goal of this brief:** Two small phases, ~1 working day total. Cleans up
> technical debt left by previous work (Phase 0) and fixes a real bug where the
> TTS workers never return word timestamps, causing the renderer to run Whisper
> as a fallback for every single segment (Phase 0.5).

## 3.0 Project Context

**Project:** Video.AI — a local-only video generation pipeline. Topic → story
plan → Hindi voiceover (Supertonic 3 TTS, 2026-06-04) → Bonsai 4B images with
IP-Adapter FLUX v2 face-lock (2026-06-04) → Ken Burns MP4 with Devanagari
subtitles. All local on a Windows 11
RTX 4050 (6 GB VRAM) + 16 GB RAM box. Python 3.12.13 in `venv/`.

**Working directory:** `C:\Video.AI`
**Python:** `venv\Scripts\python.exe` (always use this; never `python` directly)
**OS:** Windows 11, PowerShell 7+
**Git:** Repo with **0 commits** — all changes go in the working tree, no
branches, no history. Just modify files in place.

**This consolidated doc replaces:** the three separate files formerly at
`C:\Users\dhruv\OneDrive\Documents\` (now archived under
`_archive/v6_planning_2026-06-02/`).

## 3.1 Critical Rules (DO NOT BREAK)

These come from `AGENTS.md` at the repo root. **Read that file too.**

1. **Run through `bootstrap_pipeline.py`**, never `python -m core.pipeline_long`
   directly. Bootstrap applies compat patches, runs preflight, handles shutdown.
2. **Only ONE model in VRAM at a time.** Ollama models must be force-evicted
   (`keep_alive=0`) before any GPU task. Use `evict_ollama_models(config, reason)`.
3. **Serialize ALL CrewAI `kickoff()`** through `utils.concurrency.crewai_lock`
   (an RLock).
4. **Use `global_scheduler.task("heavy", ...)`** for any GPU work (SD, TTS).
5. **All config changes go in `config/config.yaml`**, not in Python. Add a
   matching Pydantic field in `config/config_schemas.py` (note: **plural**, with
   the `s`).
6. **All paths are `pathlib.Path`**, no POSIX assumptions.
7. **Atomic writes only** (temp + replace) for any persisted JSON.
8. **`tests/conftest.py` autouse-resets `UIState`** between tests. If you add a
   new `UIState` class attribute, you MUST add it to `conftest.py`.
9. **DO NOT ADD ANY COMMENTS** to code unless explicitly asked. The project
   style is comment-free Python.
10. **Verify before declaring done:** `pytest tests/ -q` must show **290+ tests
    passing, 0 failing** (12 deprecation warnings from `crewai` are expected).
    `ruff check .` must show **0 errors**.

## 3.2 Verification Baseline (BEFORE you start)

Run these and capture the output. You need the "before" numbers to prove you
didn't regress anything.

```powershell
cd C:\Video.AI
venv\Scripts\python.exe -m pytest tests/ -q 2>&1 | Select-Object -Last 5
# Expected: "290 passed, 12 warnings in ~50s"

venv\Scripts\python.exe -m ruff check . 2>&1 | Select-Object -Last 3
# Expected: "All checks passed!"
```

Save the "before" output. You'll compare against it at the end.

## 3.3 Phase 0 Tasks (hygiene — 8 fixes)

Work through these **in order**. Each is small.

### Task 0.1 — Pin missing dependencies in `requirements.txt`

**File:** `C:\Video.AI\requirements.txt`
**Current state:** `langgraph` and `playwright` are installed in `venv\` but
not pinned in this file. `feedparser` (for Phase 3) is also missing.
**Why:** Cloning the repo on a new machine would `ImportError` on first run.

**Action:** Open the file, find the last line, append:

```
langgraph>=0.2
playwright>=1.40
pytest-playwright>=0.5
feedparser>=6.0
```

(Use the actual versions installed: `venv\Scripts\python.exe -m pip show
langgraph playwright pytest-playwright feedparser` to get exact versions, then
pin them as `==X.Y.Z`.)

**Verify:** `venv\Scripts\python.exe -m pip install -r requirements.txt` should
succeed without installing anything new (everything already satisfied).

### Task 0.2 — Add missing config sections to `config/config.yaml`

**File:** `C:\Video.AI\config\config.yaml`
**Current state:** The `tts:`, `script:`, `performance:`, and `upload:`
sections exist. The `critic:`, `research:`, `seo:`, and `source:` sections do
not.

**Action:** Find the `upload:` section at the **end** of the file (around
line 247). **After** it, add these 4 sections:

```yaml
# ── Critic (self-critique quality gate) ─────────────────────────
critic:
  enabled: true
  threshold: 60              # minimum score 0-100 to pass
  max_rewrites: 2            # max rewrite attempts before giving up

# ── Research (auto-topic discovery, Phase 3) ────────────────────
research:
  enabled: true
  sources: ["wikipedia", "rss"]
  rss_urls: []
  user_agent: "VideoAI/6.0 (+https://github.com/...)"

# ── SEO (YouTube metadata generation) ────────────────────────────
seo:
  enabled: true
  title_max_chars: 100
  description_max_chars: 5000
  tags_count: 15

# ── Source (dual-entry ingestion, Phase 1) ───────────────────────
source:
  allowed_extensions: [".txt", ".md", ".pdf", ".docx"]
  max_words: 50000           # soft cap — warn but proceed if over
  url_timeout_s: 30
  user_agent: "VideoAI/6.0 (+https://github.com/...)"
```

Also add **two keys** to the existing `script:` section (find it around
line 156):

```yaml
script:
  words_per_segment: 100     # ← may already exist
  # ... existing keys ...
  critic_enabled: true
  critic_threshold: 60
  critic_max_rewrites: 2
```

**Why these `script.*` keys:** The hybrid node in `core/pipeline_graph.py:83`
reads `critic_max_rewrites` via `self.ctx.config.get("script", {}).get(...)`.
The default fallback is `2`, but with the config key absent, you can never
tune it. Same for threshold.

### Task 0.3 — Add matching Pydantic fields in `config/config_schemas.py`

**File:** `C:\Video.AI\config\config_schemas.py` (note the **plural**)

**Action:** Read the file first to understand the existing Pydantic models
(there are already models for `TTSConfig`, `PerformanceConfig`, etc.). Then
add 4 new models and 1 new field on `TTSConfig`:

```python
class CriticConfig(BaseModel):
    enabled: bool = True
    threshold: int = 60
    max_rewrites: int = 2

class ResearchConfig(BaseModel):
    enabled: bool = True
    sources: list[str] = Field(default_factory=lambda: ["wikipedia", "rss"])
    rss_urls: list[str] = Field(default_factory=list)
    user_agent: str = "VideoAI/6.0 (+https://github.com/...)"

class SEOConfig(BaseModel):
    enabled: bool = True
    title_max_chars: int = 100
    description_max_chars: int = 5000
    tags_count: int = 15

class SourceConfig(BaseModel):
    allowed_extensions: list[str] = Field(default_factory=lambda: [".txt", ".md", ".pdf", ".docx"])
    max_words: int = 50000
    url_timeout_s: int = 30
    user_agent: str = "VideoAI/6.0 (+https://github.com/...)"

class AlignmentConfig(BaseModel):
    enabled: bool = True
    model: str = "base"        # tiny | base | small | vasista22/whisper-hindi-small
    device: str = "cpu"
    compute_type: str = "int8"
```

Then **add** these fields to the top-level config model (look for the existing
`class VideoAIConfig(BaseModel)` or similar):

```python
    critic: CriticConfig = Field(default_factory=CriticConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    seo: SEOConfig = Field(default_factory=SEOConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
```

And add to the existing `TTSConfig`:

```python
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
```

**Verify with:** `venv\Scripts\python.exe -c "from config.config_schemas import VideoAIConfig; c = VideoAIConfig(); print(c.critic.threshold, c.source.max_words, c.tts.alignment.model)"` — should print `60 50000 base`.

### Task 0.4 — Fix bare `except:` in `utils/youtube_uploader.py`

**File:** `C:\Video.AI\utils\youtube_uploader.py`
**Line 106-107** currently has:
```python
            try:
                page.click("ytcp-button#toggle-button", timeout=5000)
            except:
                pass # Might already be expanded
```

**Fix:** Replace with:
```python
            try:
                page.click("ytcp-button#toggle-button", timeout=5000)
            except PlaywrightTimeoutError:
                pass
```

The `PlaywrightTimeoutError` is already imported at line 8.

### Task 0.5 — Remove unused `import os` in `utils/youtube_uploader.py`

**File:** `C:\Video.AI\utils\youtube_uploader.py`
**Line 4:** `import os` — never used in the file.

**Fix:** Delete that line.

### Task 0.6 — Remove `script-reviewer` from `config/config.yaml`

**File:** `C:\Video.AI\config\config.yaml`
**Line 18** currently has:
```yaml
  reviewer: "script-reviewer"      # Fast script review. NOT created — degrades gracefully (auto-approve). Create/pull to enable.
```

**Why remove:** This model is never created in Ollama, so the critic
auto-approves everything. Per the v6.1 decision, the writer self-critiques
via prompt swap, so we don't need a separate reviewer model.

**Fix:** Delete line 18 (and any trailing line that becomes orphaned). Keep the
`director:`, `writer:`, `writer_scratch:`, `writer_adapt:` lines.

### Task 0.7 — Remove reviewer call in `core/segment_runner.py`

**File:** `C:\Video.AI\core\segment_runner.py`
**Lines 491-493** (inside `critic_node`) currently have:
```python
        log.debug(f"  Seg {i}: Reviewing script with script-reviewer...")
        from utils.specialized_models import review_script_fast
        review_result = review_script_fast(script, plan, context, config.get("characters", {}))
```

**Fix:** Replace these 3 lines with a stub that auto-approves (this node will
be properly replaced in Phase 4, but for now we just stop calling the dead
reviewer):
```python
        log.debug(f"  Seg {i}: Critic node — using legacy auto-approve (Phase 4 will replace)")
        review_result = {"approved": True, "review_unavailable": True}
```

And in the same node, **after** the existing `if review_result.get("review_unavailable"):` block, the code already returns `critic_approved=True`. So this stub is the minimum-change fix.

### Task 0.8 — Add tests for the existing LangGraph skeleton

**File:** `C:\Video.AI\tests\test_pipeline_graph.py` (NEW FILE)

**Why:** The previous AI built `core/pipeline_graph.py` (120 LoC) but added
zero tests. We need regression coverage.

**Action:** Create the file with these 6 tests:

```python
"""test_pipeline_graph.py - Regression tests for the LangGraph skeleton in
core/pipeline_graph.py. Verifies the graph builds, the routing logic is
correct, and state propagates through nodes.
"""
import pytest
from core.pipeline_graph import SegmentGraphBuilder, SegmentState, END


class _FakeCtx:
    """Minimal context for SegmentGraphBuilder — supplies config + node fns."""
    def __init__(self, max_rewrites=2):
        self.config = {
            "script": {"critic_max_rewrites": max_rewrites, "critic_threshold": 60},
        }
    def do_write_script(self, state): return {"script": "draft"}
    def do_critic(self, state):       return {"critic_approved": True, "critic_feedback": "", "rewrites_attempted": 1}
    def do_translate(self, state):    return {"devanagari_script": "ट्रांसलेट"}
    def do_tts(self, state):          return {"audio_path": "/tmp/a.wav"}
    def do_image_gen(self, state):    return {"images": ["/tmp/i.png"]}
    def do_render(self, state):       return {"mp4_path": "/tmp/v.mp4"}


def test_graph_builds_without_error():
    builder = SegmentGraphBuilder(_FakeCtx())
    graph = builder.build()
    assert graph is not None


def test_route_after_critic_aborted_returns_end():
    builder = SegmentGraphBuilder(_FakeCtx())
    state = {"aborted": True, "critic_approved": True}
    assert builder.route_after_critic(state) == END


def test_route_after_critic_approved_returns_translate():
    builder = SegmentGraphBuilder(_FakeCtx())
    state = {"aborted": False, "critic_approved": True, "rewrites_attempted": 0}
    assert builder.route_after_critic(state) == "translate_node"


def test_route_after_critic_rejected_under_max_returns_writer():
    builder = SegmentGraphBuilder(_FakeCtx(max_rewrites=2))
    state = {"aborted": False, "critic_approved": False, "rewrites_attempted": 1, "i": 1}
    assert builder.route_after_critic(state) == "write_script_node"


def test_route_after_critic_rejected_at_max_returns_translate():
    builder = SegmentGraphBuilder(_FakeCtx(max_rewrites=2))
    state = {"aborted": False, "critic_approved": False, "rewrites_attempted": 2, "i": 1}
    # Forgiving escape hatch: never loop forever
    assert builder.route_after_critic(state) == "translate_node"


def test_state_script_propagates_across_write_to_critic():
    """Regression: AGENTS.md 'atomic state' rule — script must survive the
    round-trip from write_script_node to critic_node."""
    builder = SegmentGraphBuilder(_FakeCtx())
    state = {"i": 1, "plan": {}, "context": ""}
    write_out = builder.write_script_node(state)
    assert "script" in write_out
    new_state = {**state, **write_out}
    assert new_state["script"] == "draft"
    # Critic can read it
    critic_out = builder.critic_node(new_state)
    assert "critic_approved" in critic_out
```

**Verify:** `venv\Scripts\python.exe -m pytest tests/test_pipeline_graph.py -v`
should show 6 passed.

## 3.4 Phase 0.5 Tasks (TTS Timestamp Fix — 5 changes)

**The bug:** `audio/omnivoice_worker.py:285` and `audio/f5_worker.py:322`
return `{"status": "success", "wav_path": wav}` only — no `word_timestamps`
key. Downstream, `audio/audio_proxy.py:898` reads `result.get("word_timestamps")`
→ `None` → `video/renderer/assembler.py:585` runs Whisper as a fallback for
**every** segment.

**The fix:** Move the Whisper alignment call from the renderer to the TTS worker.

### Task 0.5.1 — Create `audio/tts_alignment.py` (new file)

**File:** `C:\Video.AI\audio\tts_alignment.py` (NEW FILE)

```python
"""tts_alignment.py - Generate word-level timestamps for TTS output.

Wraps faster-whisper (CPU int8, no VRAM impact) to produce per-word timing
JSON. Called from the TTS worker after audio synthesis, so the renderer
always has real audio timing and never falls back to running Whisper itself.
"""
import json
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level cache so multiple TTS calls in one run share the same model.
_alignment_model = None
_alignment_lock = threading.Lock()
_alignment_model_name = None


def _get_alignment_model(model_name: str, device: str, compute_type: str):
    global _alignment_model, _alignment_model_name
    if _alignment_model is not None and _alignment_model_name == model_name:
        return _alignment_model
    with _alignment_lock:
        if _alignment_model is None or _alignment_model_name != model_name:
            from faster_whisper import WhisperModel
            _alignment_model = WhisperModel(model_name, device=device, compute_type=compute_type)
            _alignment_model_name = model_name
            log.info(f"tts_alignment: loaded faster-whisper {model_name} ({device}, {compute_type})")
    return _alignment_model


def align_audio(wav_path: Path, model_name: str = "base",
                device: str = "cpu", compute_type: str = "int8") -> Path | None:
    """Run faster-whisper on a WAV file, write word timestamps to {wav}.words.json.

    Returns the JSON path on success, None on any failure (does not raise).
    """
    wav_path = Path(wav_path)
    if not wav_path.exists():
        log.warning(f"tts_alignment: WAV not found: {wav_path}")
        return None
    json_path = wav_path.with_suffix(".words.json")
    try:
        model = _get_alignment_model(model_name, device, compute_type)
        segments_gen, _info = model.transcribe(
            str(wav_path), beam_size=1, word_timestamps=True, vad_filter=True
        )
        words = [
            {"word": (w.word or "").strip(), "start": w.start, "end": w.end}
            for seg in segments_gen
            for w in (seg.words or [])
            if (w.word or "").strip()
        ]
        json_path.write_text(json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"tts_alignment: wrote {len(words)} words to {json_path.name}")
        return json_path
    except Exception as e:
        log.warning(f"tts_alignment: failed for {wav_path.name}: {e}")
        return None
```

### Task 0.5.2 — Wire alignment into `omnivoice_worker.py`

**File:** `C:\Video.AI\audio\omnivoice_worker.py`

**Find lines 285 and 304** (the two `print(json.dumps({"status": "success", "wav_path": wav}))` calls — one in persistent mode, one in one-shot mode).

**For each**, replace the line with this block. Read `config.yaml` once at module import to know whether alignment is enabled:

At the top of the file (after existing imports), add:
```python
def _maybe_align(wav_path: str) -> str | None:
    try:
        from config import load_config
        cfg = load_config()
        align = cfg.get("tts", {}).get("alignment", {})
        if not align.get("enabled", True):
            return None
        from audio.tts_alignment import align_audio
        result = align_audio(
            Path(wav_path),
            model_name=align.get("model", "base"),
            device=align.get("device", "cpu"),
            compute_type=align.get("compute_type", "int8"),
        )
        return str(result) if result else None
    except Exception as e:
        log.warning(f"_maybe_align failed: {e}")
        return None
```

And add `from pathlib import Path` to the imports at the top if not already there.

Then in **both** success print sites (lines 285 and 304), change:
```python
print(json.dumps({"status": "success", "wav_path": wav}))
```
to:
```python
word_timestamps = _maybe_align(wav)
print(json.dumps({"status": "success", "wav_path": wav, "word_timestamps": word_timestamps}))
```

### Task 0.5.3 — Wire alignment into `f5_worker.py`

**File:** `C:\Video.AI\audio\f5_worker.py`

Same treatment as OmniVoice: copy the `_maybe_align` helper, replace both
success print sites (lines 322 and 342).

### Task 0.5.4 — Add regression-detector warning in `assembler.py`

**File:** `C:\Video.AI\video\renderer/assembler.py`
**Line 590** currently has:
```python
        log.info(f"Generating word-level subtitles using Whisper ({format_style})...")
```

**Fix:** Change to:
```python
        log.warning(
            f"REGRESSION: Whisper fallback fired for seg (format={format_style}). "
            f"TTS worker should have provided word_timestamps JSON. "
            f"Check tts.alignment.enabled in config.yaml."
        )
        log.info(f"Generating word-level subtitles using Whisper ({format_style})...")
```

The WARNING level is critical — it's the regression detector. If Phase 0.5
works, this WARNING should **never** appear in the logs.

### Task 0.5.5 — Add tests for the alignment flow

**File:** `C:\Video.AI\tests\test_tts_alignment.py` (NEW FILE)

```python
"""test_tts_alignment.py - Tests for the TTS→alignment→renderer flow."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def test_align_audio_writes_json_next_to_wav(tmp_path):
    """align_audio() writes {wav}.words.json with the right structure."""
    from audio.tts_alignment import align_audio

    wav = tmp_path / "test.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 100)  # not a real WAV, but exists

    fake_word = MagicMock(word="hello", start=0.0, end=0.5)
    fake_word.word = "hello"
    fake_word.start = 0.0
    fake_word.end = 0.5
    fake_seg = MagicMock(words=[fake_word])
    fake_seg.words = [fake_word]
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter([fake_seg]), MagicMock())

    with patch("audio.tts_alignment._get_alignment_model", return_value=fake_model):
        result = align_audio(wav, model_name="base")

    assert result == wav.with_suffix(".words.json")
    assert result.exists()
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data == [{"word": "hello", "start": 0.0, "end": 0.5}]


def test_align_audio_returns_none_if_wav_missing(tmp_path):
    from audio.tts_alignment import align_audio
    assert align_audio(tmp_path / "nope.wav") is None


def test_align_audio_returns_none_on_whisper_failure(tmp_path):
    from audio.tts_alignment import align_audio
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"x")

    fake_model = MagicMock()
    fake_model.transcribe.side_effect = RuntimeError("boom")
    with patch("audio.tts_alignment._get_alignment_model", return_value=fake_model):
        assert align_audio(wav) is None


def test_alignment_disabled_skips_call(tmp_path, monkeypatch):
    """When tts.alignment.enabled is false, _maybe_align returns None without
    importing or calling faster-whisper."""
    from audio import omnivoice_worker

    fake_cfg = {"tts": {"alignment": {"enabled": False}}}
    monkeypatch.setattr("config.load_config", lambda: fake_cfg)

    called = {"align_audio": False}
    def _spy(*a, **kw):
        called["align_audio"] = True
        return None
    monkeypatch.setattr("audio.tts_alignment.align_audio", _spy)

    assert omnivoice_worker._maybe_align(str(tmp_path / "x.wav")) is None
    assert called["align_audio"] is False


def test_alignment_enabled_calls_align_audio(tmp_path, monkeypatch):
    from audio import omnivoice_worker
    fake_cfg = {"tts": {"alignment": {"enabled": True, "model": "base"}}}
    monkeypatch.setattr("config.load_config", lambda: fake_cfg)

    expected = tmp_path / "out.words.json"
    monkeypatch.setattr("audio.tts_alignment.align_audio", lambda *a, **kw: expected)

    result = omnivoice_worker._maybe_align(str(tmp_path / "x.wav"))
    assert result == str(expected)


def test_tts_worker_result_dict_includes_word_timestamps_key():
    """Regression: the worker's success JSON must include the word_timestamps
    key (value may be null), so audio_proxy.py:898 doesn't fall through."""
    import json as _json
    # Inspect the source string for the literal key — this guards against
    # accidental removal in future edits.
    src = Path("audio/omnivoice_worker.py").read_text(encoding="utf-8")
    assert '"word_timestamps"' in src, "omnivoice_worker.py must emit 'word_timestamps' key in success JSON"

    src2 = Path("audio/f5_worker.py").read_text(encoding="utf-8")
    assert '"word_timestamps"' in src2, "f5_worker.py must emit 'word_timestamps' key in success JSON"
```

**Also** update `C:\Video.AI\tests\test_audio_crossfade.py` to assert that
`word_timestamps_json` is populated. Read that file first to see its existing
patch structure, then add one assertion at the end of each test that currently
verifies audio:

```python
        assert "word_timestamps" in result  # or whatever the local var is
```

(Read the file to find the right place; don't guess at variable names.)

## 3.5 End-to-End Verification

After all tasks, run the full suite. **All of these must pass:**

```powershell
cd C:\Video.AI

# 1. All tests pass
venv\Scripts\python.exe -m pytest tests/ -q 2>&1 | Select-Object -Last 5
# Expected: "296+ passed, 12 warnings in ~55s"  (290 baseline + 6 pipeline_graph + 5 tts_alignment = 301)

# 2. Lint clean
venv\Scripts\python.exe -m ruff check . 2>&1 | Select-Object -Last 3
# Expected: "All checks passed!"

# 3. New config keys are loadable
venv\Scripts\python.exe -c "from config.config_schemas import VideoAIConfig; c = VideoAIConfig(); print(c.critic.threshold, c.source.max_words, c.tts.alignment.model)"
# Expected: "60 50000 base"

# 4. TTS worker source has the word_timestamps key
Select-String -Path "C:\Video.AI\audio\omnivoice_worker.py", "C:\Video.AI\audio\f5_worker.py" -Pattern '"word_timestamps"'
# Expected: 4 lines (2 files × 2 sites each = persistent + oneshot)
```

## 3.6 Manual Smoke Test (if you have time)

If Ollama is running and a 6GB model is available, do a real dry-run:

```powershell
cd C:\Video.AI
venv\Scripts\python.exe bootstrap_pipeline.py --skip-preflight --dry-run --topic "Real Hero Test" --yes
# Look for: "tts_alignment: wrote N words" in the logs
# Look for: NO "REGRESSION: Whisper fallback fired" warnings
# Look for: "Using provided word timestamps JSON" in renderer logs
```

If dry-run can't actually run TTS (no model), the unit tests are sufficient.

## 3.7 Rollback (if something breaks)

All changes are file-level. To undo:

```powershell
cd C:\Video.AI
# Save your changes first
Copy-Item requirements.txt requirements.txt.bak
Copy-Item config\config.yaml config\config.yaml.bak
Copy-Item config\config_schemas.py config\config_schemas.py.bak
# ... etc for each file you touched

# To rollback a single file: edit it back manually
# There is no git history — everything is the working tree
```

## 3.8 Common Pitfalls

Things you might get wrong:

1. **Don't run `python -m pytest` directly.** Always use
   `venv\Scripts\python.exe -m pytest` so the venv is honored.

2. **Don't import the alignment module at the top of `omnivoice_worker.py`.**
   Use a function-local import (`from audio.tts_alignment import align_audio`
   inside `_maybe_align`) so the worker doesn't need faster-whisper just to
   start. The persistence server must boot even if alignment is broken.

3. **Don't put `print()` in the TTS worker — use the existing `log`.** The
   worker uses stdout-JSON for IPC, and `print()` calls outside the JSON
   envelope break the protocol. Use `log.warning()` etc. for diagnostics.

4. **Don't add type comments** like `# type: ignore[xxx]`. The project runs
   without mypy; adding type comments clutters the code.

5. **Don't add docstrings to existing functions** unless you wrote them.
   Existing code is comment-free. New code (your new files) may have a
   module-level docstring, but functions should not.

6. **The `requirements.txt` task is pinning, not installing.** Don't run
   `pip install` for the whole file — just append the lines. The venv already
   has these installed.

7. **`config_schemas.py` is plural.** Don't create `config_schema.py` (singular).

8. **Tests in `tests/conftest.py` reset `UIState` autouse.** If your new tests
   need a clean `UIState`, you get it for free. If you add a new `UIState`
   attribute elsewhere, you must also reset it in `conftest.py`.

## 3.9 Done Criteria (when to mark complete)

You are done when:

- [ ] All 13 tasks (0.1-0.8, 0.5.1-0.5.5) done
- [ ] `pytest tests/ -q` shows **all tests pass**, count went from 290 → 301+
- [ ] `ruff check .` shows **All checks passed!**
- [ ] Config keys load: `VideoAIConfig().critic.threshold == 60`
- [ ] TTS worker source contains `"word_timestamps"` literal in 4 places
- [ ] No new comments added to existing code
- [ ] No `import os` in `youtube_uploader.py`
- [ ] No `script-reviewer` references in `config.yaml` or `segment_runner.py`
- [ ] Two new test files: `tests/test_pipeline_graph.py` and
      `tests/test_tts_alignment.py`
- [ ] (Optional) Manual smoke test passes

**When complete, write a one-paragraph session log to the user summarizing:**
- How many files you touched (count)
- Test count before/after (290 → 30X)
- Any tasks you couldn't complete and why
- Any deviations from this brief

## 3.10 Files You Will Touch (summary)

| File | Type | Reason |
|---|---|---|
| `requirements.txt` | modify | Pin 4 deps |
| `config/config.yaml` | modify | Add 4 sections + 2 keys + remove reviewer |
| `config/config_schemas.py` | modify | Add 4 Pydantic models + 1 field |
| `utils/youtube_uploader.py` | modify | Bare except + unused import |
| `core/segment_runner.py` | modify | Stub out reviewer call |
| `audio/tts_alignment.py` | **new** | Alignment wrapper |
| `audio/omnivoice_worker.py` | modify | Add word_timestamps to output |
| `audio/f5_worker.py` | modify | Add word_timestamps to output |
| `video/renderer/assembler.py` | modify | Regression-detector WARNING |
| `tests/test_pipeline_graph.py` | **new** | 6 graph tests |
| `tests/test_tts_alignment.py` | **new** | 6 alignment tests |
| `tests/test_audio_crossfade.py` | modify | Assert word_timestamps populated |

**12 files total: 8 modified, 4 new.**
**Total estimated LoC:** ~110 production + ~200 tests = **~310 LoC**.
**Estimated time:** 3-5 hours.

---

End of brief. Start with `cd C:\Video.AI` and work through Task 0.1.

---

# Appendix A — Local UI Thread-Safety Plan (prior alteration, preserved)

> **Context:** This 84-line plan was previously the contents of
> `C:\Video.AI\implementation_plan.md`. It describes a 2026-06-02
> thread-safety + VRAM hygiene pass on `utils/local_ui.py`. It has been
> **executed** and verified. Kept here as historical record of the prior
> alteration. The original file is at
> `_archive/local_ui_plan_2026-06-02/implementation_plan.md`.

## [Overview]
Harden and align the local UI backend (`utils/local_ui.py`) with the pipeline's concurrency/VRAM safety and thread-safety expectations, while ensuring the existing API response shapes remain stable and tests continue to pass.

This plan focuses on verifying architectural invariants (VRAM single-model rule, scheduler semantics, thread-safe UI state reads/writes) and then applying minimal, low-risk changes limited to `utils/local_ui.py`.

## [Types]
Single sentence describing type system changes: No type system changes required.

No new types are required beyond using existing FastAPI request parameter types (`int`, `str`) and existing in-memory job structures for A/B.

## [Files]
Single sentence describing file modifications: Modify only `utils/local_ui.py` (no refactors elsewhere).

Detailed breakdown:
- New files to be created:
  - None
- Existing files to be modified:
  - `utils/local_ui.py`
    - Thread-safe read of `UIState.logs` in `GET /api/status`
    - Add VRAM/LLM eviction safety (or equivalent) to background A/B generation worker in `POST /api/ab/generate`
- Files to be deleted or moved:
  - None
- Configuration file updates:
  - None

## [Functions]
Single sentence describing function modifications.

Detailed breakdown:
- New functions:
  - None (optional: a small helper to copy logs under lock could be inlined or added as a private function, but no external API changes)
- Modified functions:
  - `get_system_status()` in `utils/local_ui.py`
    - Current: `logs_obj = getattr(UIState, "logs", [])` then `logs_list = list(logs_obj)[-100:]` without synchronization
    - Required change: copy `UIState.logs` under `UIState._log_lock` (or fall back safely if lock is unavailable), then take the last 100 items from the copied list
    - Ensure response shape remains exactly:
      - `{status, active_question, logs, output_video}`
  - `_run_ab(job_id, pa, pb)` inner worker in `POST /api/ab/generate`
    - Current: wraps `generate_images` in `global_scheduler.task("heavy", ...)` but does not perform Ollama eviction / VRAM release like the main segment loop
    - Required change: before calling `generate_images` for each variant (or once before both variants), perform a VRAM-protection step consistent with the main pipeline:
      - Call `core.segment_runner.evict_ollama_models(config, reason="UI-AB")` if feasible and safe to import
      - If `evict_ollama_models` import is too costly or fails, implement a best-effort fallback that only clears CUDA cache (if torch available), still within the heavy scheduler context
    - This must remain low-risk:
      - No changes to job status transitions (`running -> generating_a/b -> ready/error`)
      - No changes to `images_a`/`images_b` URL construction format
      - Preserve existing input validation behavior for `segment_num`

- Removed functions:
  - None

## [Classes]
Single sentence describing class modifications.

Detailed breakdown:
- No class modifications required. `UIState` remains the single shared state provider.

## [Dependencies]
Single sentence describing dependency modifications.

Details of new packages/version changes:
- No dependency changes.
- Use only already-imported modules and existing project utilities (`core.segment_runner.evict_ollama_models`, `utils.concurrency.global_scheduler`, `utils.load_config`).

## [Testing]
Single sentence describing testing approach.

- Run `ruff check .` to ensure lint stays green.
- Run full test suite: `venv\Scripts\python.exe -m pytest tests/ -q` (target: 281 passed).
- Add/validate manual "API level" checks (no new automated tests required by this plan):
  - `GET /api/status` returns JSON with `logs` always as a list (even during concurrent writes)
  - `POST /api/ab/generate` rejects invalid `segment_num` and maintains traversal protections
  - Ensure A/B generation does not crash under concurrent load (best-effort due to GPU variability)

## [Implementation Order]
Single sentence describing the implementation sequence.

Numbered steps showing logical order:
1. Inspect current `utils/local_ui.py` code paths for `/api/status` and A/B generation worker (confirm response shapes and current validation).
2. Implement thread-safe `UIState.logs` copying in `get_system_status()` using `UIState._log_lock` when available; keep fallback behavior if lock is missing.
3. Implement VRAM/LLM eviction safety in the A/B worker before Bonsai image generation, using `core.segment_runner.evict_ollama_models(config, reason="UI-AB")` and best-effort CUDA cache clearing fallback.
4. Run `ruff check .` and `pytest tests/ -q` to confirm no regressions.
5. Execute a minimal runtime smoke test for `/api/status` and `/api/ab/*` endpoints (manual curl/Invoke-RestMethod), verifying response shapes and error codes.

---

# Appendix B — Archived Source Documents

These three documents were the original sources for the consolidated
plan above. They have been preserved unmodified under `_archive/` for
reference and version control. **Do not edit them — they are historical.**

| Archived file | Path | Lines | Content |
|---|---|---|---|
| v6 unified plan | `_archive/v6_planning_2026-06-02/implementation_plan_v6.md` | 458 | §1 above |
| Phase 0 + 0.5 brief | `_archive/v6_planning_2026-06-02/implementation_brief_phase0.md` | 762 | §3 above |
| Architecture diagram | `_archive/v6_planning_2026-06-02/pipeline_diagram_v6.md` | 114 | §2 above |
| Local UI plan (prior) | `_archive/local_ui_plan_2026-06-02/implementation_plan.md` | 84 | Appendix A above |

The OneDrive originals at `C:\Users\dhruv\OneDrive\Documents\` have been
moved (not copied) into `_archive/`. The repo now has a single canonical
plan at `C:\Video.AI\implementation_plan.md`.
