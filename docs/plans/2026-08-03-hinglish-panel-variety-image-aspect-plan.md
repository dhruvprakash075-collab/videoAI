# Video.AI Fix Plan — TTS Hindi register + tail stutter, panel-layout variety, per-panel aspect generation

Date: 2026-08-03. Basis: three parallel codebase investigations (all root causes
confirmed against primary source; no code changed yet). User-reported symptoms:

1. TTS speaks overly complex Hindi; last word/syllable repeats at the end of
   every sentence ("ga" tail stutter).
2. Manga pages always use the same panel layout despite the Roboflow layout
   dataset being provided.
3. Generated images waste space — blurred side regions; image should be
   generated to fit the panel cutout, not letterboxed.

Status: **IMPLEMENTED 2026-08-03 — Parts A + C done (all tests green), B done**
(B2.1 verdict: user heard no stutter in either comparison sample; kept
`tts.indicf5.speed: 0.85`. B2.2 stays as fallback if the tail reappears in real
runs. Bonus bug found & fixed during B2.1: trailing `\n` in text silently broke
the IndicF5 batch line — `indicf5_worker.py` now strips it, test-locked.)

---

## Part A — TTS: complex Hindi (register)

### A1. Root cause (CONFIRMED)

The Hindi register is decided by exactly one 8-word instruction with an empty
system message, fed to `sarvam-translate` at temperature 0:

- `agents/director/translation.py:41-44` — live prompt:
  `"Translate to natural spoken Hindi in Devanagari only. Output only the translation:"`
- `agents/director/translation.py:69-71` — `system_msg=""` explicitly disables
  the default translator system message (`agents/director/llm_shims.py:44-46`).

Literary English in → literary Hindi out (परन्तु/अतः/प्रयास). Contributors:

- The English source is written literary: writer persona "award-winning
  Screenwriter… immersive, captivating" (`core/main.py:180-193`), prompt demands
  "highly emotional and dramatic… cinematic" (`utils/story_planner.py:287-294`).
  Critic rubric (`utils/critic.py:75+`) has no vocabulary-simplicity dimension.
- The good "simple spoken Hindi / Hinglish" prompt ALREADY EXISTS but is dead
  code: `audio/audio_proxy.py:856-870` (`translate_hinglish`, hi-branch — only
  test callers) and `prompts.yaml:193-210` (`generate_hinglish`, marked Legacy).
- `protect_hinglish` was deliberately removed (ponytail comment
  `translation.py:32-37`: sarvam degenerates on placeholder tokens). Its config
  knob `tts.devanagari.hinglish_ratio: 0.4` (`config/config.yaml:50`) is now
  dead — no production reader.
- Note: the Devanagari-ratio guard (≥0.90, `translation.py:118-157`) and
  `transliterate_latin_runs` (`translation.py:78`) force everything into
  Devanagari script — so "Hindi with English" must mean English loanwords
  written phonetically in Devanagari (फोन, कार, टाइम), which is exactly how
  Hinglish is spoken. No guard changes needed.

### A2. Fix (code, small)

A2.1. Rewrite the instruction in `agents/director/translation.py:41-44` to steer
register (port the rules from the dead `audio_proxy.py:856-870` prompt):

```
"Translate to simple, everyday spoken Hindi (Devanagari only), the way people
actually talk. Use common, easy words — avoid literary, bookish, or heavy
Sanskrit/Urdu-origin words (परन्तु→लेकिन, अतः→इसलिए, प्रयास→कोशिश). Keep common
English loanwords as natural Devanagari phonetics (फोन, कार, टाइम, गेम). Keep
names and dramatic punctuation as-is. Output only the translation:"
```

A2.2. Simplify the English source too (translator mirrors register): add one
line to `build_segment_prompt` (`utils/story_planner.py:287-294`):
"Use simple everyday words (grade-5 level), short sentences."
(Zero-code alternative available today: set
`production_notes.custom_instructions` in `config/config.yaml` — appended to the
writer prompt at `core/segment_runner.py:299-303`. Do the prompt edit anyway;
keep custom_instructions as user override.)

A2.3. Delete or wire the dead knob `tts.devanagari.hinglish_ratio`
(`config/config.yaml:50`, schema `config_schemas.py:158`). Recommend delete —
the new prompt makes it meaningless. (Separate one-liner; can ride along.)

### A3. Verification gate

- `venv\Scripts\python.exe -X utf8 -m pytest tests/test_devanagari_translation.py -q`
  (update expectations if they assert on the old instruction text).
- Manual: translate one real segment script, diff vocabulary before/after
  (spot-check no परन्तु/अतः/प्रयास-class words; loanwords present).
- Real 1-seg run → listen.

---

## Part B — TTS: "ga" tail stutter at sentence ends

### B1. Root cause (best-supported hypothesis)

Stitching is innocent: `external/IndicF5/run_indic.py:213-228` is plain concat +
250 ms silence, no overlap/crossfade; `_split_sentences` (`run_indic.py:126-161`)
never duplicates boundary words; `_postprocess_in_place` copies samples 1:1.
Renderer/concat downstream have no overlap.

The artifact is the well-documented **F5 per-chunk duration-overrun tail**: each
sentence-ish chunk is an independent F5 inference whose frame budget is
estimated from ref rate ÷ text length; surplus mel frames get filled by
prolonging/repeating the final phoneme ("…karega" → "…karega-ga"). The
chunk-per-sentence architecture maps 1:1 to the symptom (every chunk tail IS a
sentence end). Aggravators present here:

- `tts.indicf5.speed: 0.85` (`config/config.yaml:29`) inflates the frame budget
  ~18% over natural length.
- Comma re-splits (`run_indic.py:151`) leave chunks ending mid-phrase on ","
  with no sentence-final intonation.
- Possible ref_text/ref_audio mismatch: configured `ref_text`
  (`config/config.yaml:26-27`) differs from the hardcoded fallback
  (`audio/audio_proxy.py:153-156`), and the transcript file
  `character_voices/narration_ref_9s_mono24k.txt` does not exist on disk.
  Unverifiable from repo; skews F5's rate ratio.

Also found (ride-along bug): `_call_indicf5_worker` accepts `speed_override`
(`audio/audio_proxy.py:131`) but never uses it (line 158 reads config only) —
mood-based speed silently dropped on the IndicF5 path.

### B2. Fix (test-first, then code only if needed)

B2.1. Config experiment (no code): set `tts.indicf5.speed: 1.0`, run 1 segment,
listen. If the tail disappears → keep 1.0 (or 0.95), done.

B2.2. If tail persists: in `external/IndicF5/run_indic.py`, strip trailing
sentence punctuation from chunk text before inference (the model doesn't need
the danda/comma to end cleanly; pause comes from the 250 ms gap), e.g.
`chunk = chunk.rstrip("।,،.!?")` in `_infer_chunk`'s caller. Verify by listening.

B2.3. Recreate `character_voices/narration_ref_9s_mono24k.txt` with the exact
transcript of the ref WAV (listen and transcribe), and align
`config/config.yaml:26-27` to it. Skewed ref rate is a classic tail-repeat
cause; cheap to eliminate.

B2.4. Ride-along one-liner: honor `speed_override` in
`audio/audio_proxy.py:158`:
`speed = float(speed_override) if speed_override is not None else float(indic_cfg.get("speed", 0.85))`.

### B3. Verification gate

- Per step: 1-seg real run, listen to every sentence boundary.
- Check worker stderr for `[WARNING] infer_process failed` (fallback path
  ignores speed/steps — if seen, that is a separate bug to fix first).
- Keep `tests/test_audio_proxy*.py` green.

---

## Part C — Panel layout variety + per-panel aspect (ONE workstream)

These two must land together: restoring layout rotation (C1) increases the
number of non-3:2 panels, which makes the blur-fill (C2) MORE visible unless
per-panel generation lands with it. Same two files.

### C1. Root cause: layout rotation defeated (CONFIRMED, regression)

- Dataset is wired and loaded: `config/panel_layouts.roboflow.json` (150
  layouts; 108 five-panel), config `image_gen.panel_composite.layout_file`
  (`config/config.yaml:140-148`), fallback `config/panel_layouts.json`.
- But `video/image_gen/panel_compositor.py:53-69` `_layout_rects` collects ALL
  rotated candidates and returns `min(candidates, key=_layout_aspect_score)` —
  a deterministic argmin. Verified empirically at 1920×1080: every 5-panel page
  gets `roboflow_031` (of 75 valid). The `(page_index + offset)` rotation is a
  leftover that iterates every offset regardless.
- Regression introduced in `bb172aa84` ("Fix ruff lint errors blocking CI" —
  behavior change smuggled into a lint commit). Original `d9cdcce56` returned
  the first valid layout in rotated order → per-page variety.
- Locked in by test `tests/test_panel_compositor.py:94-103`
  (`test_layout_selection_prefers_panels_that_fit_landscape_shots`).

### C2. Root cause: blurred sides (CONFIRMED, structural)

- Every image is generated at one fixed 768×512 (3:2):
  `video/image_gen/image_gen.py:250-251` (`config/config.yaml:133-134,170-171`).
- Layout is chosen only AFTER all images exist
  (`image_gen.py:324-338` → `panel_compositor.compose_panel_pages:136-149`).
- Panel rects in the dataset span aspects 0.47–7.61 (36% portrait).
- `_panel_image` (`panel_compositor.py:102-113`) letterboxes with
  `ImageOps.contain` and fills the uncovered area with a
  `GaussianBlur(12)` + darkened crop of the same image — that fill IS the
  blurred region. Deliberate behavior, test-locked
  (`tests/test_panel_compositor.py:75`).

### C3. Fix

C3.1. Layout-first: in `image_gen.generate_images` (before the ComfyUI loop),
compute the page assignment deterministically (page = i//5, slot = i%5 — same
chunking as `compose_panel_pages:136`), pick each page's layout via
`_layout_rects`, and derive each image's target panel aspect (rect w/h at the
1920×1080 page). Requires exposing a "plan" helper in `panel_compositor.py` that
both `generate_images` and `compose_panel_pages` call, so generation and
composition always agree on the layout.

C3.2. Per-panel generation size: in `_comfyui`'s loop
(`image_gen.py:262-320`), snap the panel aspect to an SD1.5-safe bucket at the
same ≈393k-pixel budget (multiples of 64): landscape 768×512, portrait
512×768, square 640×608, wide 832×448. `WorkflowPatcher.patch_width_height`
(`comfyui_workflow.py:211-227`) already substitutes per call — no workflow JSON
changes. Note: refine passes (face_detail/upscale) are aspect-preserving, so
they compose unchanged.

C3.3. Restore rotation in `_layout_rects` (`panel_compositor.py:53-69`): return
the first valid layout in `(page_index + offset)` rotated order (the original
`d9cdcce56` behavior). With C3.2 in place, `_layout_aspect_score`'s 3:2
preference is no longer needed for blur avoidance — plain rotation maximizes
variety. (If C3.2 is deferred, rotate among top-K by aspect score instead.)

C3.4. `_panel_image` becomes a near-exact fit (rounding-error bars only). Keep
the blur fill as the fallback for odd aspects — it degrades gracefully instead
of failing. Optional knob `panel_composite.fit_mode: blur|crop` if the user
wants hard-crop instead; not required.

C3.5. Update tests: `tests/test_panel_compositor.py:94-103` (argmin lock-in →
assert rotation/variety), add a unit test that per-image sizes follow page
layout aspects, keep the rest of the file green.

### C4. Verification gate

- `python -m pytest tests/test_panel_compositor.py tests/test_video_ai_nodes_execution.py -q`
- Compose test with ≥6 images: assert ≥2 distinct layouts across pages.
- Real 1-seg run → open `manga_page_*.png`: pages differ, panels fill their
  rects without visible blur bars.
- ComfyUI smoke gate afterwards (AGENTS.md rule for workflow-touching changes —
  C3.2 doesn't edit JSONs, but run it anyway since generation sizes change):
  `venv\Scripts\python.exe scripts\comfyui_smoke.py`.

---

## Sequencing

1. **A (translation register)** — independent, smallest, immediate audible win.
2. **B2.1 (speed 1.0 experiment)** — config-only, 15 min, answers whether B
   needs code at all.
3. **C (layout-first + per-panel aspect + rotation)** — one PR, the two files
   `panel_compositor.py` + `image_gen.py` (+ tests).
4. B2.2–B2.4 only if B2.1 insufficient.

## Decision points

- **D1 (A2.1):** DECIDED 2026-08-03 — Hinglish-lean register (common English
  loanwords phonetic in Devanagari). User also chose "plan only, don't
  implement yet" — implementation awaits explicit go-ahead.
- **D2 (A2.3):** OK to delete dead `tts.devanagari.hinglish_ratio` knob?
- **D3 (C3.3):** plain rotation (max variety, recommended once C3.2 lands) vs
  top-K rotation (keeps layouts near 3:2 if per-panel generation is deferred).
- **D4 (C3.4):** keep blur fill as fallback (recommended) vs add `fit_mode:
  blur|crop` knob now.

## Findings (uncovered during investigation, per repo reporting rule)

- Dead code: `translate_hinglish` (`audio/audio_proxy.py:856-870`),
  `generate_hinglish_script` (`translation.py:168-196`), `prompts.yaml:193-210`
  — delete after A2 lands (its rules get ported into the live prompt).
- Dead knob: `tts.devanagari.max_predict_tokens` read at
  `agents/llm_client.py:112` but `DevanagariConfig` is `extra="forbid"`
  (`config_schemas.py:156-160`) — can never be set; add to schema or remove.
- IndicF5 ignores `tts.voice_profile.sentence_gap_ms` (hardcoded 250 ms at
  `run_indic.py:59`; config 200 ms only reaches OmniVoice) — pass through if gap
  parity matters.
- Process smell: `bb172aa84` ("lint fix") changed layout-selection behavior +
  added letterboxing. Unrelated behavior changes in lint commits hide
  regressions — flag in review practice.
- Stale docstring: `video/image_gen/__init__.py:8` advertises "speech bubble
  compositing" — no bubble code exists.
- PONYTAIL-DEBT rows touched by this plan: `indicf5_worker.py:66` (cleared as a
  suspect — file-location fallback only), `hinglish_glossary.py:365`
  (no-trigger row; A2 doesn't touch the glossary path).
