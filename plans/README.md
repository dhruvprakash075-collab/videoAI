# Implementation Plans

Generated on 2026-06-21 for commit `2ba9b863`. The report is written for an executor with no access to the originating conversation. Read it fully before modifying code.

## Execution order and status

| Plan | Title | Priority | Effort | Depends on | Status |
|---|---|---:|---:|---|---|
| 001 | Make the media pipeline truthful and reliable | P1 | L, phased | — | TODO |
| 002 | Render authentic manga panels with exact speech bubbles | P1 | M | — | TODO |

Status values: TODO, IN PROGRESS, DONE, BLOCKED, REJECTED.

## Phase dependency order

1. Implement ComfyUI input upload.
2. Stage Qwen inputs and expose real outcomes.
3. Make character-presence routing deterministic.
4. Make seed locking truthful.
5. Consolidate research.
6. Repair or remove no-op switches.
7. Remove or explicitly defer feature shells.
8. Perform focused automated acceptance, followed by an explicitly authorized one-frame hardware test.

Qwen web-UI wiring is intentionally excluded.

Plan 002 is independent of Plan 001. It reuses the current ComfyUI and FFmpeg path and must preserve all pre-existing work while changing only its declared scope.

## Resource-safety rule

Never run verification commands concurrently. Never run the complete Python test suite, start model services, download models, or perform GPU inference without explicit operator authorization. Use only the focused test commands in the plan.

## Considered and rejected

- Letting the Director LLM freely choose backend names: rejected because structured `char_presence` plus deterministic routing is more reliable and testable.
- Wiring Qwen into the dashboard now: rejected by explicit operator direction.
- Keeping dead feature shells merely for possible future use: rejected; Git history already preserves them.
- Building LoRA training before proving Qwen and IP-Adapter: rejected as premature scope.
- Wiring FramePack immediately: rejected because it lacks production integration and needs a separate GPU-calibrated spike.
