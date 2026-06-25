# Plan 002: Render authentic manga panels with exact speech bubbles

> **Executor instructions:** Follow this plan step by step and run each focused verification before continuing. The repository already has a dirty worktree: preserve every pre-existing change, never use destructive Git commands, and do not modify files outside this plan's scope. If a STOP condition occurs, report it instead of improvising. When complete, update Plan 002 in `plans/README.md` unless a reviewer says they own the index.
>
> **Drift check (run first):** `git diff --stat e416a9cc..HEAD -- core/pipeline_graph.py core/segment_runner.py utils/scene_director.py config/config.yaml config/config_schemas.py video/renderer/renderer.py video/renderer/assembler.py tests/test_writer_structured.py tests/test_segment_runner_extended.py tests/test_scene_director.py tests/test_operator_preferences.py tests/test_config_schemas.py tests/test_renderer.py tests/test_assembler_extra.py`
>
> If an in-scope path changed after commit `e416a9cc`, compare the live code with the Current state excerpts below. Stop on a contract mismatch and update this plan before implementing.

## Status

- **Priority:** P1
- **Effort:** M
- **Risk:** MEDIUM
- **Depends on:** none
- **Category:** direction
- **Planned at:** commit `e416a9cc`, 2026-06-24
- **Reference:** `C:\Users\dhruv\Downloads\WhatsApp Video 2026-06-24 at 11.46.11 AM.mp4`

## Why this matters

The target is a manga-panel video, but the current style path groups `manga` with anime/webtoon rendering and automatically adds soft cell shading. That can produce polished anime key art instead of manga. The output also has no trustworthy representation of who spoke which exact line, so asking the image model to generate speech bubbles would create spelling and attribution errors. This plan separates the problem into text-free manga art plus deterministic lettering from validated dialogue data.

## Operator decisions that must not drift

1. The target is **manga**, not glossy anime illustration, Arcane-like painterly art, webtoon rendering, 3D, or photorealism.
2. The reference is colorized, so the first target is **inked/screentone manga with restrained flat muted color**, not mandatory monochrome.
3. A speech bubble is required when a visible character actually speaks.
4. Bubble text must be exactly what that character said. Incorrect, invented, duplicated, or misspelled text is a failure.
5. Image generation must not draw the bubble. Program code letters it afterward.
6. Narrator-only frames do not receive fabricated dialogue.

## Target panel specification

Each shot is one full-frame 16:9 manga panel with:

- strong black contours and varied line weight;
- visible screentone, cross-hatching, solid blacks, and deliberate white space;
- a limited flat color wash, with no glossy gradients or painterly lighting;
- expressive manga acting and purposeful panel framing;
- at most one exact, readable speech balloon attached to the speaking character;
- clean 1920x1080 output without black bars, a panel grid, logo, signature, or watermark;
- subtle panel motion, reaction cuts, and detail reframes;
- audible narration and existing subtitles.

The first implementation deliberately excludes multi-panel pages. They make faces and lettering too small for video and caused awkward unused space in the reference.

## Current state

- `core/pipeline_graph.py:11-42` defines `SegmentState`. It carries script, TTS text, timestamps, images, and MP4 path, but no dialogue metadata:

  ```python
  class SegmentState(TypedDict, total=False):
      script: str
      script_for_tts: str
      word_timestamps_json: str
      images: list[str]
      mp4_path: str
  ```

- `core/segment_runner.py:479-503` requests and extracts narration only:

  ```python
  + '{"narration": "<spoken narration text only ...>"}'
  _parsed = _json_w.loads(_raw_json)
  _narration = _parsed.get("narration", "").strip()
  ```

- `core/segment_runner.py:888-923` calls `render_with_assets()` with images, script, subtitle text, timestamps, style, and config but no dialogue cues.
- `video/renderer/renderer.py:220-233` and `:301-309` expose no dialogue-cue parameter before calling `create_segment_mp4()`.
- `video/renderer/assembler.py:242-357` gives images equal time, applies one global Ken Burns mode, caps `full` motion at 12 internal fps, and burns subtitles after concatenation.
- `utils/scene_director.py:131-133` groups `manga` with anime/webtoon styles. At `:245`, that branch appends `webtoon art, soft cell shading`, which directly explains the anime-looking sample.
- `config/config_schemas.py:131-135` uses `extra: forbid` and does not expose the global visual negative prompt already read by `get_dynamic_negative_prompt()`.
- `requirements.txt:49` already includes `pillow>=12.2.0`; no new drawing dependency is needed.
- `tests/test_writer_structured.py` is the current writer-contract test location. Its existing baseline was verified at plan time: `5 passed in 0.11s`.

## Required dialogue contract

Extend the structured writer response from narration-only JSON to:

```json
{
  "narration": "Mira looked at the sealed door. She whispered, Do not open it yet.",
  "dialogue_cues": [
    {
      "frame_index": 1,
      "speaker_id": "mira",
      "speaker_side": "left",
      "text": "Do not open it yet."
    }
  ]
}
```

Validation rules:

- `frame_index` is zero-based and within `plan["num_images"]`.
- `speaker_id` exists in `config["characters"]` and has `char_presence >= 0.3` in that frame.
- `speaker_side` is exactly `left`, `right`, or `center`.
- `text` contains 1–18 words, has no control characters, and occurs verbatim in English `narration`.
- The first version allows one cue per frame.
- Invalid cues are dropped individually and recorded as a degradation; valid narration continues.
- The raw/CrewAI fallback returns no cues unless it provides the same valid structure. Do not infer speakers with quote regexes.

Add a `ponytail:` comment at the one-cue rule: its ceiling is overlapping conversation; the upgrade path is an ordered cue list with collision-aware placement after this path is proven.

## Manga prompt contract

Manga frames must receive style tokens equivalent to:

> authentic colorized Japanese manga panel, professional black ink line art, varied line weight, visible screentone and cross-hatching, bold solid blacks, restrained flat muted colors, expressive manga acting, clean negative space, cinematic panel composition

Suppress:

> anime key visual, glossy anime illustration, painterly rendering, Arcane style, webtoon rendering, photorealism, 3D render, soft airbrush shading, dramatic gradient lighting, generated text, letters, speech bubbles, captions, logos, signatures, watermarks, panel grid, black bars, blank frame

For a dialogue frame, append composition instructions derived from the cue:

- put the named speaker on the declared side;
- keep the face and mouth clear;
- reserve uncluttered upper space near that speaker for one balloon;
- place other characters away from the reserved space;
- draw no text or balloon in the generated artwork.

The negative `speech bubbles` token applies only to ComfyUI output. The deterministic renderer adds the approved balloon later.

## Bubble visual contract

- White oval or softly irregular manga balloon.
- Solid 3–4 px black outline at 1920x1080.
- A connected triangular/curved tail aimed toward the declared speaker side.
- Black bold condensed lettering with exact source case and punctuation.
- Pixel-measured word wrapping using Pillow font metrics, never fixed character counts.
- 44–56 px base font at 1080p, scaled with output height.
- Internal padding of at least one text-line height.
- Maximum 34% frame width and 32% frame height.
- At least 5% safe margin from every frame edge.
- No face, eye, mouth, or subtitle overlap in the accepted preview.

Placement:

- left speaker → upper-left/upper-center balloon, tail down-left;
- right speaker → upper-right/upper-center balloon, tail down-right;
- center speaker → balloon above the speaker, short centered tail.

Resolve a configured font first, then an installed Windows font fallback. Stop if no installed font can render the chosen language; do not add a font package silently.

## Commands

Run sequentially; never run the full suite or start/download model services without operator authorization.

| Purpose | Command | Expected success |
|---|---|---|
| Writer/state | `venv\Scripts\python.exe -m pytest tests/test_writer_structured.py tests/test_segment_runner_extended.py -q` | exit 0; selected tests pass |
| Prompt/config | `venv\Scripts\python.exe -m pytest tests/test_scene_director.py tests/test_operator_preferences.py tests/test_config_schemas.py -q` | exit 0; selected tests pass |
| Bubble/render | `venv\Scripts\python.exe -m pytest tests/test_speech_bubbles.py tests/test_assembler_extra.py tests/test_renderer.py -q` | exit 0; selected tests pass |
| Focused lint | `venv\Scripts\ruff.exe check <changed Python files>` | exit 0, no findings |
| Scope | `git status --short` | no new changes outside this plan's scope |

## Scope

**Only these implementation files may change:**

- `core/pipeline_graph.py`
- `core/segment_runner.py`
- `utils/scene_director.py`
- `config/config.yaml`
- `config/config_schemas.py`
- `video/renderer/renderer.py`
- `video/renderer/assembler.py`
- `video/renderer/speech_bubbles.py` (new; keep bubble drawing isolated and testable)
- focused tests named in Commands, including new `tests/test_speech_bubbles.py`

**Do not touch:**

- `dashboard/` or API/UI controls;
- ComfyUI workflow JSON, checkpoints, samplers, or model choice;
- TTS, translation, voice cloning, or mastering;
- Hyperframes behavior;
- final concatenation/loudnorm;
- database/job schema;
- OCR, multi-panel pages, or multiple simultaneous bubbles.

## Git workflow

- If asked to create a branch, use `codex/manga-speech-bubbles`.
- Suggested single commit: `feat: render manga dialogue bubbles`.
- Do not stage, commit, push, or open a PR unless the operator asks.

## Steps

### Step 1 — Validate and propagate dialogue cues

1. Add a `DialogueCue` `TypedDict` and `dialogue_cues: list[DialogueCue]` to `core/pipeline_graph.py`.
2. Add one private `_validate_dialogue_cues(raw, narration, plan, characters)` helper near existing script helpers in `core/segment_runner.py`. Use ordinary Python; add no validation framework.
3. Extend the structured writer prompt to request `narration` plus `dialogue_cues` and include the exact contract above.
4. Tell the writer to create dialogue only for genuine character speech—not exposition—and keep each bubble line verbatim in narration.
5. Validate frame, configured speaker, `char_presence`, side, length, control characters, verbatim narration membership, and one-cue ceiling.
6. Carry cues through `SegmentState`, the existing segment checkpoint, prompt enrichment, and `render_with_assets()`.
7. Preserve current narration/TTS behavior when cues are missing or invalid.

**Verify:** writer/state command → all selected tests pass.

### Step 2 — Split manga prompting from anime prompting

1. Change `config/config.yaml` to the manga prompt contract above.
2. Add `negative_prompt: str = ""` to `VisualConfig`; `scene_director` already reads it, while strict validation currently rejects it.
3. In `utils/scene_director.py`, detect manga before the broad anime check.
4. Give manga its own ink/screentone/flat-color suffix. Never append `webtoon art, soft cell shading` to a manga prompt.
5. Add side-specific reserved-space instructions only to frames with a valid cue.
6. Keep non-dialogue frames text-free without reserving bubble space.
7. Leave image backend, checkpoint, resolution, identity path, and hardware settings unchanged.

**Verify:** prompt/config command → all selected tests pass.

### Step 3 — Precompose exact manga balloons

1. Implement `video/renderer/speech_bubbles.py` with Pillow. Its public function accepts an image path, one validated cue, resolution, and font config, then returns a new temporary panel path.
2. Fit a copy of the artwork to output resolution before lettering; never overwrite the generated image.
3. Measure glyphs, wrap by pixels, then draw balloon, outline, connected tail, and exact text.
4. In `render_with_assets()` and `create_segment_mp4()`, accept `dialogue_cues` and pass them without reinterpretation.
5. Before building the FFmpeg command, replace only speaking-frame paths with temporary precomposed panels.
6. Use a static hold for speaking frames in version one. This keeps the bubble tail attached and prevents lettering from being cropped. Non-dialogue frames keep manga motion.
7. Keep bottom subtitles for accessibility. They may repeat speech, but must not overlap the upper balloon; do not add timing-sensitive subtitle rewriting now.
8. Delete temporary panels only after FFmpeg succeeds. Preserve them on failure for diagnosis.
9. If precomposition fails, record a degradation and use the original clean frame without a balloon. Never render guessed/corrupted text.

**Verify:** bubble/render command → all selected tests pass.

### Step 4 — Add restrained manga motion

1. Add one deterministic `manga` motion branch in `assembler.py`; do not create a timeline abstraction.
2. Non-dialogue frames cycle through establishing hold, centered push-in, reaction close-up, object/detail crop, and left/right reframe.
3. Speaking frames stay static in the first version.
4. Generate new manga motion at up to 24 internal fps instead of the old `full` branch's 12 fps ceiling.
5. Clamp every crop inside the scaled source; never expose black edges.
6. Prefer clean cuts for reaction/detail changes and 0.10–0.15 s crossfades only for location/time changes.
7. Do not generate extra artwork until reframing the existing 4–8 images has been evaluated.

Add a `ponytail:` comment beside the fixed motion cycle: its ceiling is deterministic shot selection; upgrade to per-shot Director camera metadata only if previews prove it insufficient.

**Verify:** bubble/render command and focused Ruff → exit 0.

### Step 5 — Run one calibrated preview

Only with explicit operator authorization, render 20–30 seconds containing:

1. an establishing panel without dialogue;
2. a two-character panel with a left-side speaker and exact bubble;
3. a reaction close-up;
4. a right-side speaker panel with different punctuation;
5. audible narration and current subtitles.

Visual pass conditions:

- unmistakable ink/screentone colorized manga—not polished anime illustration;
- exact dialogue spelling, case, and punctuation;
- one bubble connected to the correct speaker;
- no model-generated lettering or second bubble;
- no face/subtitle collision;
- no black bars, blank transitions, signatures, logos, or watermarks;
- speaker remains aligned with the tail for the whole speaking shot;
- wide, interaction, reaction, and detail shots are distinct.

Media checks:

```powershell
ffprobe -v error -show_streams -show_format -of json <preview.mp4>
ffmpeg -hide_banner -i <preview.mp4> -vf "blackdetect=d=0.08:pix_th=0.10" -an -f null NUL
ffmpeg -hide_banner -i <preview.mp4> -af "volumedetect" -vn -f null NUL
```

Expected: 1920x1080, 24 fps, no unexplained black intervals, audio materially above digital silence, and duration aligned with narration.

## Test plan

- `tests/test_writer_structured.py`: valid cue plus malformed, unknown speaker, absent speaker, invalid side/frame, overlong, and non-verbatim cases.
- `tests/test_segment_runner_extended.py`: cue state/checkpoint/render propagation and graceful invalid-cue fallback.
- `tests/test_scene_director.py`: manga-specific suffix, absence of webtoon/soft-cell tokens, and left/right/center reserved-space instructions.
- `tests/test_operator_preferences.py` and `tests/test_config_schemas.py`: configured manga style and schema-valid negative prompt.
- New `tests/test_speech_bubbles.py`: exact case/punctuation, pixel-based wrapping, left/right/center tails, safe bounds, Unicode font failure, source preservation, and cleanup behavior.
- `tests/test_assembler_extra.py`: follow `test_create_segment_mp4_kb_modes`; capture argv and assert speaking frames hold while non-speaking frames use manga motion.
- `tests/test_renderer.py`: cue propagation to the default assembler path.

## Done criteria

- [ ] All three focused pytest commands exit 0.
- [ ] Focused Ruff exits 0 for every changed Python file.
- [ ] Manga prompts never receive `webtoon art, soft cell shading`.
- [ ] A fixture containing `Do not open it yet.` renders that exact case and punctuation once.
- [ ] Invalid cues produce no balloon and never break narration/video rendering.
- [ ] No generated source image is overwritten.
- [ ] No new dependency is added.
- [ ] Scope check shows no new changes outside declared files plus pre-existing user work.
- [ ] One authorized preview passes all visual/media checks.
- [ ] Plan 002 is marked DONE only after that preview passes.

## STOP conditions

Stop and report if:

- the operator wants monochrome manga instead of the locked colorized target;
- two characters must speak simultaneously in one panel;
- bubble language must differ from the English source script/subtitles;
- no installed font can render the required language;
- the image model still ignores the manga contract in an authorized preview;
- implementation requires a checkpoint/model change, new dependency, database change, or out-of-scope file;
- live code no longer matches the Current state contracts;
- any focused verification fails twice after a reasonable targeted correction.

## Maintenance notes

- Review exact text preservation, speaker validation, font fallback, safe margins, and cleanup before subjective polish.
- Static speaking frames are intentional. Add anchor-aware motion only after the fixed bubble/speaker relationship is proven.
- One bubble per frame is intentional. Multiple speakers require collision-aware layout and a richer cue timeline in a separate plan.
- If subtitle or TTS language changes, explicitly decide whether bubbles follow source or localized dialogue; never silently translate inside the renderer.
- Do not call the result manga if it still reads as anime illustration, and do not call a bubble correct unless its text and speaker attribution are exact.
