# Feature Implementation Helper — Video.AI Native App

Deep research on every planned feature (see `docs/ui-plan.md`): what it is, how
it behaves in real life, the exact code/functions to add or change, and the
problems you will hit while implementing it. Companion docs: `docs/ui-plan.md`
(build plan), `docs/ui-bugs.md` (bug register).

> **Read Part 0 before any feature.** It holds the invariants every feature
> depends on. Skipping it is how you reintroduce the bugs in `ui-bugs.md`.

---

## Part 0 — Shared architecture invariants (apply to ALL features)

The native app is a Rust/egui window that **manages the existing Python engine**
as subprocesses. Every feature must respect:

1. **Engine is Python; UI is Rust.** Image-gen, TTS, agents, config semantics are
   Python-bound (torch / ComfyUI / Ollama). Re-implementing them in Rust violates
   the "engine" contract. New **business logic goes in Python** (backend or a new
   module); the **UI** surfaces it, and thin Rust API shims call it.
2. **One source of truth for jobs = SQLite** (`studio_projects/jobs/video_ai_jobs.db`).
   `JobStore` (Python) and the Rust worker both read/write the **same schema —
   never mutate the schema** (AGENTS.md). New fields ride in `request_json`
   (a JSON blob) or `jobs` columns that already exist.
3. **Config = `config/config.yaml`, atomic writes.** Any Settings/feature that
   edits config must use the temp-file + `os.replace` pattern (like
   `save_ui_config`, `local_ui.py:847-859`) under a lock. Never write in place.
4. **CWD and interpreter.** The app must always launch the **venv python**
   (`venv/Scripts/python.exe`) from the **repo root CWD**, with repo root on
   `sys.path`, or every relative config path and the venv guard break.
5. **Derived, validated `request_json`.** The payload you enqueue IS what the
   worker turns into a `bootstrap_pipeline.py` command. Validate at submit, not
   minutes into the run (bug 15).
6. **Do NOT touch** `bootstrap_pipeline.py`, `core/`, `video/`, the SQLite schema
   unless a feature explicitly requires a guarded change; prefer additive modules
   and wrapping existing functions.
7. **Unicode/Devanagari.** egui's default font lacks Devanagari glyphs; load a
   system font (Nirmala UI / Mangal) for UI text that may show Hindi. Log to
   UTF-8 files, not the console (cp1252 crashes).
8. **Video playback:** egui has no decoder. Native actions = "Play in default
   player" + "Open in Explorer" + FFmpeg-extracted thumbnail. Do not fake an
   embedded player.

### How the app calls the engine (one pattern everywhere)
- Start/attach the FastAPI backend (`utils/local_ui.py`) on `127.0.0.1:8000`.
- For **read/status/DB** prefer in-process rusqlite reads (Rust) where possible.
- For **business actions** (create job, voice upload, config, A/B-free flows) call
  the backend's existing routes, or invoke a **Python module** that reuses the
  same functions (e.g. `JobStore`, `audio_proxy`, `utils.preflight`, `translation`).
- The **worker** (Python or Rust) supervises `bootstrap_pipeline.py`, streams
  `job_events`, and updates heartbeat/output_path. The app auto-drives it (fixes
  bugs 3/7/21).

---

## Part 1 — Tier 1 features

### F1 — Native Script/Story Editor + per-segment iterate
- **What / real-world:** After a run, the user reads/edits the generated per-segment scripts,
  re-runs TTS for one segment, or re-renders one segment, then reassembles. *Like an editor
  timeline*: per-segment script, narration, images, checkpoints.
- **Code to add/change:** Read per-segment artifacts from `run_manifest.json` +
  `studio_outputs/{topic}/segments/*` (already produced); add a `script.json`/outline source
  if not persisted. Edit → write back where the pipeline reads it; "regenerate segment N" →
  invoke `core.segment_runner`'s per-segment closure (checkpointed) then re-run assembly
  (`video/renderer/assembler.py`). UI: egui monospace editor + diff + per-segment actions.
- **Problems you'll hit:** pipeline is stateful/sequential — regenerating one segment must
  reuse the same checkpoint/state or continuity breaks; TTS re-run needs the same ref voice;
  re-assembly must use `global_scheduler` heavy/light slots; serialize with a running worker;
  Devanagari editing needs glyph support (Part 0-7).

### F2 — Series / Project Studio
- **What / real-world:** Create/resume a multi-episode project (`run_mode=project`), with
  locked characters, visual style, plot threads persisted per project; browse project memory
  before the next episode. *A "season" manager.*
- **Code to add/change:** Drive `memory/project_store.py` (`ProjectStore`) + `StoryStore`:
  list projects, read locked chars/visual locks/memory; pass `project` + `run_mode=project`
  in `request_json` (already supported by `build_command`). UI: project list → new/continue →
  enqueue next-episode job with same project.
- **Problems:** continuity depends on `StoryStore._load/_save_story` semantics (plain dict,
  caps segments at 100); `_one_time` vs project layout; lock/race on project files with
  concurrent jobs; `DecisionRecord` is one-time pre-prod — per-episode care needed.

### F3 — Batch Queue
- **What / real-world:** Import a `.txt` of topics, enqueue all, watch aggregate progress.
  *A queue-wrangler for volume work.*
- **Code to add/change:** Reuse `--topics-file` (bootstrap) **or** enqueue N jobs via
  `JobStore.create_job`; UI = file picker + count + per-job grid + aggregate ETA (reuse F11).
  Put a `batch_id` tag inside `request_json` (schema-safe).
- **Problems:** worker claims one job at a time and ComfyUI is serialized (heavy slot); long
  queues — `list_jobs` pagination already exists; cancel-all needs iterating `request_cancel`;
  never spawn multiple workers (bug 21).

### F4 — Storyboard Review & character approval
- **What / real-world:** Show the generated storyboard (`core/storyboard.py`), approve/reject
  panels; approvals update character approved/rejected galleries + memory. *A frame sign-off.*
- **Code to add/change:** Reuse storyboard output (persisted per story in `StoryStore`); list
  sheets for a project; approve/reject → write into the character store `approved/`/`rejected/`
  folders (the `CharactersPanel` route already reads these). Small Python helper to record;
  UI = sheet viewer + A/R buttons.
- **Problems:** the pipeline gate already `consult_user`s — reconcile gate vs manual panel to
  avoid double prompting; `identity_hash` matching; Devanagari panel text.

---


### F5 — TTS Audition
- **What / real-world:** Pick voice + engine + language, generate a short sample, play it
  in-app before committing. *A "try before you buy" for narration.*
- **Code to add/change:** Reuse `audio/audio_proxy` (`generate`/`synthesize` with a short
  sample text → temp wav). Expose engine choice (`indicf5`/`supertonic`/`omnivoice`). UI:
  engine+voice+language pickers + play button.
- **Problems:** engines differ in config (indicf5 `ref_audio`, supertonic voice JSON,
  omnivoice speed); TTS must run through `global_scheduler.task("light")` to avoid VRAM
  contention (runtime guide); slow first-load warmup → show progress; timeout → show error,
  don't hang.

### F6 — Input-source enrichment (URL / PDF / DOCX)
- **What / real-world:** Add a URL/PDF/DOCX/markdown source; the pipeline researches +
  outlines and uses each chunk as a segment script. *A "give me a document, make a video".*
- **Code to add/change:** `bootstrap --source` handles `.txt/.md/.pdf/.docx` + URL;
  `utils/researcher.py` + `agents/director/story.py` do research/outline. UI: native
  file/URL picker per source; pass `source` + `words_per_segment`/`segment_count` in
  `request_json`. Enforce `source.allowed_extensions` + `max_words`.
- **Problems:** URL fetching is slow/timeout-bound (`url_timeout_s`); research is network
  dependent (offline degrades) — surface it; PDF/DOCX parsing needs the `source` module
  libs installed; validate before enqueue (Part 0-5).

### F7 — Style Packs / presets
- **What / real-world:** Curated combos of `visual.style` + `image_gen` params (manga /
  cinematic / anime) selectable as a preset. *A one-click visual theme.*
- **Code to add/change:** Reuse **`style_resolver.py`** (`StyleResolver.resolve` +
  `styles.yaml`); add named presets (each a dict of `visual.style` + `image_gen` overrides)
  applied into config (atomic write) or per-job. UI: preset picker.
- **Problems:** style resolution has exact/fuzzy/LLM layers (confidence threshold 0.45);
  changing `image_gen` params (steps/checkpoint/loras) affects cost/VRAM and may mismatch the
  installed ComfyUI model/loras — validate presets against what's installed.

### F9 — Live telemetry rail
- **What / real-world:** A persistent strip: VRAM, disk, Ollama model state, ComfyUI
  health, heavy-task slot. *A dashboard of health, always visible.*
- **Code to add/change:** Poll `UIState.vram_text`/`run_start_ts`; run a cheap preflight
  subset + `video/runtime/vram`/`ollama` checks on a timer (throttle ~2–5s).
- **Problems:** polling cost (throttle); `torch.cuda.mem_get_info` loads `torch` — guard;
  never let telemetry crashes break the UI (degade gracefully).

### F10 — Degradation ledger viewer
- **What / real-world:** Surface the silent B2 quality fallbacks (`UIState.degradations`)
  so the user knows when quality was quietly reduced. *A "what got degraded and why".*
- **Code to add/change:** Expose `degradations` (+ `segment_manifests`) via a DB/process read;
  render as a list (segment/stage/reason). Optionally persist per job.
- **Problems:** degradations live in **process memory** (lost on restart) → capture per job
  during the run; None-safe reads.

### F11 — ETA / progress forecast
- **What / real-world:** Per-run ETA from segments done / total × elapsed.
  *A live remaining-time readout.*
- **Code to add/change:** Compute from `UIState.segment_current/total` + `run_start_ts`.
  Show "Segment 3/6 — ~12 min left" + per-segment duration rows.
- **Problems:** first segment is planning/warmup (ETA off early); image-gen dominates
  between-segment variance; account for heavy-slot queueing.

### F12 — Windows toast on completion
- **What / real-world:** OS toast when a run finishes (or fails). *A completion ping.*
- **Code to add/change:** Detect job terminal state (or an event) → fire a Windows toast
  (no dep — use `tray-icon`/`notify` crate under the `gui` feature). Include topic + status
  + open action.
- **Problems:** toasting needs a message loop / app identity on some Windows setups; don't
  toast for every batch item (only batch-complete or single-run-complete); failure toast
  should link to the job logs.



## Part 2 — Tier 2 features (real numbers; Phase 2+, user-selected)

Note: Phase-1 **core loop** panels (Create Job, Jobs with live logs, Batch,
Director Canvas, voice/music preview) are scaffold in Phase 1 and documented
under the build-order guide (Part 5), not as numbered features. The numbered
Tier-2 features below are the user-selected extras. (Not selected: 8 Output
Compare, 15 Voice recording, 19 Run diff, 26 Segment timeline, 31 Source
bibliography, 33 A/B static picker, 34 Event hooks.)

### F13 — Subtitle editor
- **What / real-world:** Edit subtitle text, font, size, color before final assembly;
  style burns-in or muxes soft subs. *A subtitle designer before the credits roll.*
- **Code to add/change:** Reuse `subtitles.*` config + the assembler's subtitle stage.
  Native UI loads the current subtitle track (from the run manifest / assembled dir),
  edits text + style, writes back to the subtitle file/format the assembler reads,
  then re-runs assembly (or just the subtitle burn). Validate font/size ranges.
- **Problems you'll hit:** re-render needs the same segment timing/seed map; editing
  Devanagari subtitle text needs glyph support (Part 0-7); soft-sub vs burn-in toggle;
  keep the edit layer outside `video/` (don't modify `core/`/`video/`).

### F14 — Chapters & SEO editor
- **What / real-world:** Edit `chapters.txt` + SEO title/description/tags before export.
  *A pre-export metadata editor.*
- **Code to add/change:** Reuse `seo.*` config + the `chapters.txt` file the exporter
  reads. Native UI: a chapters table (marker + label) + SEO fields bound to
  `config.yaml`/`seo.*`; write atomically under the config lock.
- **Problems:** chapter markers must align to segment boundaries/timestamps already
  baked into the run; SEO fields feed the export bundle (F32) — keep them in one place.

### F16 — Thumbnail picker
- **What / real-world:** Choose which rendered frame becomes the cover/thumbnail.
  *A cover-art selector.*
- **Code to add/change:** Reuse `generate_thumbnail` + Rust `media` thumbnailing. UI =
  grid of candidate frames (auto-sampled from the final mp4) + pick; writes the chosen
  frame path into the run manifest / export bundle.
- **Problems:** candidates must be frame-extracted + cached (FFmpeg); pick must persist
  so the export bundle (F32) uses the same cover.

### F17 — Background music + ducking
- **What / real-world:** Add a music bed that ducks under narration. *Audio skinning
  with volume balance.* (Plan flags `music.*` as **speculative** — confirm before build.)
- **Code to add/change:** Reuse `music.*` config + the assembler's loudnorm/ducking
  stage. Native UI: pick a track (→ F18 asset browser), set `music_volume`/`ducking_db`/
  fade curve; write selections into `request_json`/manifest before the assembler.
- **Problems:** the pipeline's `music.*` support may be dormant — verify in
  `video/renderer/assembler.py`; ducking needs the TTS voice track as side-chain input;
  royalty-free asset selection.

### F18 — Repair-and-continue
- **What / real-world:** Resume from checkpoints + auto-retry only failed segments.
  *A "fix what broke and carry on" recovery.*
- **Code to add/change:** Reuse the **checkpoint manager** + `retry_manager`. Native UI
  shows the last checkpoint per segment; "Resume" re-enqueues only incomplete/failed
  segments using the existing checkpoint state; retry-only-failed filters `segment_manifests`
  by status. Do NOT re-run already-done segments.
- **Problems:** checkpoints must be consistent with segments (a checkpoint implies its
  deps are done); `retry_manager` semantics (backoff/limit) must be respected; partial
  assembly re-stitch must match the new tail.



### F20 — Job templates / favorites
- **What / real-world:** Save a Create-Job parameter+flag set as a named preset and apply
  it to a new job. *Template your most-used runs.*
- **Code to add/change:** Reuse the exact `request_json` shape from `bootstrap_pipeline.build_command`;
  persist a named preset (deep-merged config+flags) in `user_state/presets/` (JSON). Native UI =
  list presets + "save current" + "apply to Create Job". Apply = deep-merge preset into the
  current form (precedence: preset ⊂ user field overrides).
- **Problems:** presets must capture only valid `request_json` keys (don't persist secrets/
  secrets tokens); deep-merge must be explicit (run-time overrides win); share presets
  across Windows/WSL via `user_state`.

### F21 — Seed control / reproducibility
- **What / real-world:** Expose `image_gen.lock_seed` + an explicit seed, and re-run a job
  with the same seed for reproducibility. *Lock the RNG.*
- **Code to add/change:** Read `image_gen.lock_seed` + the run's seed from the manifest;
  native UI = a seed field + lock toggle; a new run copies the seed + `lock_seed=true`
  into `request_json`. Confirm the same checkpoint/seed semantics in the pipeline.
- **Problems:** a "same seed" re-run must also fix the segment order & style & model
  versions (otherwise images won't match); warn if loras/checkpoints changed; seeds
  don't cross the heavy-slot boundary cleanly if order changed.

### F22 — Disk cleanup / archive
- **What / real-world:** One-click free space + archive old runs to a user-chosen volume.
  *A housekeeper that won't delete something you still need.*
- **Code to add/change:** Reuse `scripts/cleanup_artifacts.py` (`remove_temp_dirs`,
  `remove_old_logs`, `remove_stale_outputs`, `remove_empty_dirs`) — **do not reimplement**.
  UI shows per-run sizes (scan `studio_outputs/`, `cache/`, `logs/` via dir-size) + a
  retention form → invoke the same functions (dry-run first, show the diff). For
  archiving, move old run dirs to a user-selected path and rewrite their `output_path`
  in the jobs DB.
- **Problems (bugs):** `remove_stale_outputs` deletes `studio_outputs` files **without
  reconciling the jobs DB** → orphans `output_path`/`job_artifacts` (bug 43);
  `remove_failed_job_logs` keys `logs/{topic}` with **unsanitized topic** (bug 44). Native
  cleanup must reconcile the DB before deleting and sanitize paths. `cache/` is expensive
  to regenerate → require explicit opt-in (`--clean-cache`).

### F23 — Global search
- **What / real-world:** One search box over jobs, memory, characters, and artifacts.
  *Find anything, fast.*
- **Code to add/change:** Wire the box to the existing **list endpoints** (jobs list,
  memory search, character list, artifact index) — run all in parallel and merge results
  grouped by type. UI = results grouped (Jobs / Memory / Characters / Artifacts) +
  keyboard shortcut (`/` to focus). Minimal — no new engine endpoint.
- **Problems:** search is only over what the list endpoints return (no full-text index
  today) → keep it fuzzy + fast; avoid hammering the DB (debounce + cache the last page);
  memory/character search already fuzzy-match — reuse it rather than rebuilding.

### F24 — Subtitle-language selector
- **What / real-world:** Re-render the subtitle track in another language. *Localize subs.*
- **Code to add/change:** Reuse `translation.py` + the existing Devanagari pipeline
  (`TranslationMixin`). Native UI: pick target language (list of `tts.lang` variants /
  translation targets) → re-translate the segment scripts and re-emit subtitles (F13)
  without re-rendering video.
- **Problems:** must reuse the **same** translation pass the pipeline uses (glossary
  protection, `transliterate_latin_runs`) so subtitles match the actual narration; if a
  new TTS language is chosen, that implies **multi-language narration (F41)** — don't
  auto-trigger a full re-render unless requested.

### F25 — Quick-action shortcuts & app icon / tray
- **What / real-world:** Keyboard nav, tray minimize, app icon. *Polish the desktop fit.*
- **Code to add/change:** egui keyboard nav (focus rail via `/`), a Windows tray icon
  (small, self-contained — no new heavy dep), and an `.ico`/`.rsvg` app icon.
- **Problems:** tray icon needs the right crate (keep it optional behind the `gui` gate);
  minimize-to-tray must not kill the running engine worker; ensure a `Ctrl+,`/shortcut
  convention that doesn't collide with egui defaults.



## Part 3 — Tier 3 features (power / automation; Phase 5)

### F27 — Script translation preview / retranslate
- **What / real-world:** Preview the translated (Devanagari) script + glossary, and force a
  re-translation before render. *A pre-narration translation QA step.*
- **Code to add/change:** Reuse `agents/director/translation.py`
  (`TranslationMixin.translate_to_devanagari` + the English-echo detector) +
  `agents/hinglish_glossary.py` (`transliterate_latin_runs`). Native UI: show English
  script, the Devanagari preview, and glossary tokens; "Retranslate" re-runs the Devanagari
  pass on current segment scripts (no re-render). Write preview to a temp file the TTS
  stage reads, or let the engine re-translate from the same source.
- **Problems:** sarvam-translate is a pure-translation model (the `@@N@@` placeholder
  protection was intentionally removed — see the code comment); the English-echo guard
  (`_looks_like_english_echo`) must stay in sync; re-translating must not desync the
  segment↔script↔audio mapping.

### F28 — Model eval runner
- **What / real-world:** Run the existing eval harness in-app and view the report.
  *A/B compare your SD + TTS setup without leaving the app.*
- **Code to add/change:** Reuse `utils/model_eval.py` (`run_image_eval`,
  `run_tts_eval`, `_SAMPLE_PROMPTS`, `_SAMPLE_TTS_TEXT`). Native UI: run button → invoke the
  harness as a background task (light slot — no ComfyUI heavy slot) → show samples in
  `model_eval/{images,audio}/*`. Mirror CLI flags (`--tts-only`/`--image-only`/`--out-dir`).
- **Problems:** the harness reads the **current** SD model + voice and writes to
  `model_eval/`; gate behind a feature flag; throttle so it doesn't contend with a render.

### F29 — "Environment" landing page
- **What / real-world:** Health page with one-click start for Ollama + ComfyUI and auto-fix
  hints. *The "why isn't it working?" page.*
- **Code to add/change:** Reuse `utils/preflight.py` (full check set) + ComfyUI auto-start.
  Native UI: health tiles (Python venv, torch, models loaded, Ollama reachable, ComfyUI
  reachable) + Start Ollama / Start ComfyUI buttons + the auto-fix hint text from preflight.
- **Problems:** preflight probes are **slow** (model load) — cache + show progress; auto-start
  must respect the single-ComfyUI invariant (never two); poll the version endpoint, not a sleep.



### F30 — Outline & decision editor
- **What / real-world:** Review/edit the Director/Writer outline + `DecisionRecord` before
  segment generation (project mode). *A pre-production review gate.*
- **Code to add/change:** Reuse `plan_outline`, `agents/decision_engine.py`
  (`DecisionEngine`/`DecisionRecord`), `memory/blackboard.py`. Native UI: show the outline
  tree + decision records, allow edits, write back before segments generate. Tied to
  `run_mode=project` (F2).
- **Problems:** outline/DecisionRecord are produced **during planning** by agent calls —
  surface them means capturing into a storable form the editor can load + write back;
  editing decisions after the agent moved on can desync the plan (gate behind project mode
  + a "lock" flag).

### F32 — Export bundle
- **What / real-world:** Package a finished run (mp4 + chapters + subtitles + manifest +
  thumbnail) into a shareable folder/zip. *A "publish this run" button.*
- **Code to add/change:** Reuse Rust `media` + the assembler's outputs (chapters, subtitles,
  thumbnail, manifest). Native UI: folder picker + "Export" → assemble the bundle from
  `studio_outputs/{topic}/` artifacts + selected thumbnail (F16) + SEO fields (F14) into a
  zip. Keep it in Rust; the Python side just hands a manifest of paths.
- **Problems:** bundling is I/O heavy — background it; must honor saved "selected
  thumbnail" + "chapters/SEO"; leave source run intact (don't move originals out of
  `studio_outputs`).

### F35 — Watch folder
- **What / real-world:** Drop a `.txt` topic into a folder → auto-queue. *A drop-box.*
- **Code to add/change:** Reuse the `--topics-file` pattern (one topic per line/file).
  Native: a configured watch dir + a `ReadDir` poll (~2s) → on new `.txt`, parse topics →
  enqueue via `JobStore.create_job`.
- **Problems:** dedupe (don't enqueue twice); handle partial writes (wait for stable size +
  mtime); single serialized queue (F18) → backlog; surface queued items in the Jobs list.

### F36 — Run analytics / stats
- **What / real-world:** Total runs, avg wall-time, VRAM peaks, failure rate.
  *A stats dashboard for your habit.*
- **Code to add/change:** Reuse `UIState.vram_peaks` + the `jobs` table. Native UI: stats
  panel computed from `JobStore` (count, avg/sec-based wall-time, status breakdown, peak
  VRAM). No new instrumentation.
- **Problems:** `vram_peaks` is process-local to the engine — surface it by reading engine
  state or a persisted peak file, **not** from the DB; wall-time from real
  `started_at`/`completed_at`, not poll lag.

### F37 — Checkpoint & GC manager
- **What / real-world:** Manage `studio_checkpoints` growth and pick a resume point.
  *A checkpoint janitor.*
- **Code to add/change:** Reuse the **checkpoint manager** (the one backing F18 recovery).
  Native UI: list checkpoints (size + age + associated run/segment), auto-trim by policy
  (keep last N per run, keep successful, drop failed/abandoned), "Resume from here" → F18
  with the selected checkpoint as the resume point.
- **Problems:** GC must never delete a checkpoint still referenced by a queued/running job;
  "Resume from here" must validate the checkpoint's segment set is consistent; don't prune
  project-continuity checkpoints (F2) unless explicit.



## Part 4 — Tier 4 features (final; quality / localization)

### F39 — Character/scene consistency check
- **What / real-world:** Compare generated images against `master` portraits via
  `identity_hash` to catch face drift. *A quality gate against morphing characters.*
- **Code to add/change:** Reuse `characters` store + image-gen outputs + Rust `media`
  embedding/hash. Native UI: after a run, run the check against the master gallery, flag
  segments whose `identity_hash` deviates, and surface them for re-render (→ F18 repair).
- **Problems:** `identity_hash` needs to be computed for every generated frame (heavy —
  light slot, batch over the run); deviation threshold is heuristic — tune, don't hardcode;
  re-render only the failing segments keeps cost down.

### F41 — Multi-language narration
- **What / real-world:** Render the same story in several `tts.lang` variants in one batch.
  *One story, multiple language dubs.*
- **Code to add/change:** Reuse `translation.py` + `audio_proxy` (the per-language TTS path
  in `tts.lang`). Native UI: a multi-language selector; enqueue the same `request_json` with
  `tts.lang` set per target, sharing the segment scripts (use the F27 translation preview
  as input). Each language → its own output subdir; the export bundle (F32) zips them.
- **Problems:** each language re-runs TTS + narration (cheap relative to image gen) but the
  segment scripts must be translated consistently (glossary-protected); batch = N×TTS work
  on the single worker — show as N jobs or one job with a language loop; keep audio tracks
  aligned (same timing) so they mux cleanly.

### F44 — Dry-run cost estimator
- **What / real-world:** Preflight + `--dry-run` → estimated time + storage before a full run.
  *Know the cost before you commit.*
- **Code to add/change:** Reuse `utils/preflight.py` + the pipeline's `--dry-run` mode (if
  the pipeline supports it) — confirm `bootstrap_pipeline` accepts a dry-run that plans
  without rendering. Native UI: a "Estimate" button on Create Job → run preflight + dry-run
  → surface est. minutes + est. disk (segments × estimated bytes).
- **Problems:** time estimates are model/VRAM dependent (heavy vs light slots, queue depth);
  storage must be estimated from segment count × model-output sizes (image + wav + frame
  budget) — calibrate against `studio_outputs` averages; dry-run must not mutate state.

---

## Part 5 — Build order & cross-cutting guidance

### Phase 1 core loop (build first)
1. `gui` feature + `Cargo.toml` (`eframe = "=0.29"`, glow, MSRV ≤ 1.81).
2. Window: navigation rail + **status rail** (Phase 1: backend/worker/ComfyUI/Ollama, bug 1/5).
3. **Engine manager** (features 14/17/18/24/29/31 from `ui-bugs.md`): resolve venv python +
   repo-root CWD + port binding; spawn backend + worker **once** (single worker — bug 21).
4. **Director Canvas**: selected job's `output_path` + thumbnail + Open-in-Explorer/Play
   (bug 10/37/38 — derive from the **selected job**, not web-only `UIState.output_video`).
5. **Create Job**: all sources + flags, **validated at submit** (bug 8/15/24/30).
6. **Jobs**: list/detail/cancel/retry + **live logs** (level/search/time filters).
7. Batch queue (F3) + toast on completion (F12) + ETA/progress (F11) + degradation ledger
   (F10).
8. **Preflight fix** (bug 13/22/23): ComfyUI reachability + TTS-path check, gated at startup.

### Phase 2–3 — creation/iteration + gate
Voice Studio + TTS Audition (F5); Artifacts; Sources (F6); Style Packs (F7); Storyboard
Review (F4); Memory; Characters; Series/Project Studio (F2); Settings with **blast-radius
warnings + relocation safety** + **real save** (bug 40/41/42: include images-per-segment
field, drop dead "Uncapped Scaling", surface validation 422); Job templates (F20); Seed
control (F21); Disk cleanup (F22, bugs 43/44); Global search (F23); Subtitle-language (F24);
Shortcuts/tray (F25). Phase 3: delete old `static/` + `ab_picker` (bug 33) only where safe;
green gate: `cargo test && clippy -D warnings && fmt --check`; `ruff check .`.

### Phase 4–6 — Tier 2/3/4 polish
F13 subtitles · F14 chapters/SEO · F16 thumbnail · F17 music/ducking · F18 repair ·
F27 translation · F28 eval · F29 environment · F30 outline/decision · F32 export bundle ·
F35 watch folder · F36 analytics · F37 checkpoint/GC · F39 consistency · F41 multi-lang ·
F44 cost estimator.

### Cross-cutting reminders (apply everywhere)
- **Atomic config writes + lock** everywhere (bug 12); never trust in-place edits.
- **CWD = repo root, interpreter = venv python** always; validate `request_json` at submit.
- **One engine, one worker**; the app auto-drives the queue; no `.bat`/`.vsh`/browser.
- **Don't touch** `bootstrap_pipeline.py`, `core/`, `video/`, the SQLite schema.
- **Unicode first**: load Devanagari-capable fonts; log to UTF-8 files.
- **Reuse over rewrite**: every "Reuses" column is a standing instruction — if the Python
  already does it, wrap it; do not port it to Rust.


