# Nested Loop Analysis — 2026-08-02

Metrics from the codebase-memory graph: `transitive_loop_depth` (loops nested
through the call chain), `loop_depth` (direct loop nesting in one function),
`linear_scan_in_loop` (collection scan inside a loop = O(n²) signal).

## Verdict: no O(n²) hotspots — 3 trivial hits, deep chains are structural

## Hidden linear scans (O(n²) risk) — only 3, all small

- `utils/web_search.py _strip_spoilers` — scan-in-loop 1.
- `rust/worker/src/text.rs markdown_heading_boundaries` — scan-in-loop 1.
- `rust/worker/src/checkpoint.rs clear_candidates` — scan-in-loop 1.

All single-scan, bounded-input; no action needed.

## Deepest call-chain loops (transitive depth 8) — the batch fan-out spine

- `bootstrap_pipeline._run_batch` (8, loop 1) → `run_pipeline_with_args` (8) → `_run_single` (7) — batch → per-video → per-segment → per-image iteration. Structural for a video batch pipeline, not a bug; the depth is where per-batch cancellation/eviction knobs already live (`pipeline_long.py`).

## Depth 7 — render/TTS/eval chains

- `video/image_gen/image_gen.py _comfyui` (7, loop 1) + `generate_images` (7).
- `audio/omnivoice_worker.py main` (7) + `_run_persistent` (7, loop 1) + `_synthesize` (6, loop 1).
- `utils/model_eval.py run_eval` (7) + `run_image_eval` (7).

## Direct double-nesting (loop_depth 2) worth eyes

- `video/image_gen/comfyui_client.py ComfyUIClient.generate_image` (depth 5, 2 direct loops).
- `memory/project_store.py StoryStore.check_continuity` (depth 4, 2 loops, 1 scan).
- `jobs/worker.py Worker.run_once` (depth 4, 2 loops).
- `audio/tts_alignment.py align_audio` (depth 4, 2 loops).

Direct nesting never exceeds 2-3 levels — no stack-deep loops anywhere.

## Note

`make_process_segment` (complexity 155) does not appear here: its loops are
shallow, its problem is breadth (see other-findings.md), not depth.
