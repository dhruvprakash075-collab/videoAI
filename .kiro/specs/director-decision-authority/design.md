# Design Document

## Overview

This design introduces a single authoritative **Decision Record** plus a **shared
blackboard** that the Director, Writer, story planner, and pipeline all read from and
write to, replacing today's master–slave hand-down where the pipeline silently
re-derives structural values. It also reorganizes continuity memory into a three-tier
**project / story / audit** layout, makes user overrides explicit **locks**, wires the
two Director flows (CLI and dashboard) onto one pathway, and implements the
currently-stubbed length controls (cliffhanger / compaction).

Design priorities, in order:

1. **Correctness of authority** — the value the Director/Writer/user agreed on is the
   value the runtime uses.
2. **Zero runtime cost** — the decision/coordination layer is disk/RAM-only and adds no
   model calls or concurrent VRAM load (per the Performance requirements).
3. **Backward compatibility** — existing configs, checkpoints, CLI flags keep working.
4. **Minimal blast radius** — reuse existing modules (`config_schemas.py`,
   `CheckpointManager`, `WorldState`, `PermanentMemoryLog`, `WorkloadScheduler`) rather
   than replacing them.

### Research-grounded decisions

- **Blackboard pattern** for multi-agent coordination — agents converge on shared state
  rather than a controller dictating downstream
  ([blackboard multi-agent systems, Google Research](https://research.google/pubs/blackboard-multi-agent-systems-for-information-discovery-in-data-science/)).
  Content was rephrased for compliance.
- **Atomic JSON writes** (temp file + `os.replace`) and **cross-process file locking**
  for the shared state, matching the state-management pattern of "JSON persistence with
  atomic writes, file locking, schema versioning and migration"
  ([state-management patterns](https://lobehub.com/skills/akaszubski-autonomous-dev-state-management-patterns),
  [python-atomicwrites](https://python-atomicwrites.readthedocs.io/en/latest/)).
  Content was rephrased for compliance. The repo already uses temp-file + `os.replace`
  in `CheckpointManager`, `WorldState`, and `PermanentMemoryLog`, so this extends an
  existing convention.
- **Single source of truth** to avoid the same value defined in multiple places
  ([DRY / multiple sources of truth](https://pranaypourkar.gitbook.io/the-programmers-guide/system-design/design-principles-and-patterns/software-design-principles/dry)).
  Content was rephrased for compliance.
- **Provenance / auditability** — track where each value came from and reconstruct the
  chain on demand
  ([provenance & auditability](https://signalsandsystems.substack.com/p/provenance-traceability-and-auditability)).
  Content was rephrased for compliance.
- **Schema versioning + migration** for persisted models, following Pydantic's model
  evolution guidance ([Pydantic migration](https://docs.pydantic.dev/latest/migration/)).
  Content was rephrased for compliance.

## Architecture

### Current (problem) flow

```
CLI:        bootstrap → run_pre_production → produce_runtime_config(overlay)
                              │                       │
                              ▼                       ▼  (overlay merged into config)
                        run_long_pipeline ── re-derives n_segs = total/seg_min  ← drops segment_count
                                          └─ words_per_seg = config.script (fallback only)
                                          └─ story_planner picks target_word_count independently

Dashboard:  local_ui.run_pipeline_thread → read_story → define_pacing_and_length  ← separate, divergent
```

### Proposed flow (blackboard + decision record)

```
                ┌─────────────────────── Blackboard (disk/RAM) ───────────────────────┐
                │  DecisionRecord (versioned, provenance per field, locks)            │
                │  Project store · Story store · Continuity/Audit log                 │
                └─────────────────────────────────────────────────────────────────────┘
                      ▲ read/write            ▲ read/write            ▲ read
                      │                        │                        │
   Director ──proposes──►            Writer ──consents/adjusts──►   Pipeline + story_planner
   (analyze, duration,                (sees proposal, returns       (READ agreed values;
    cliffhanger/compact)               agreement + rationale)        never re-derive)
                      ▲
                      │ locks (strict overrides, provenance=user / cli_flag)
                    User

   Entry points (both build the SAME DecisionRecord):
     CLI:        bootstrap → run_pre_production → DecisionEngine → run_long_pipeline
     Dashboard:  local_ui → run_pre_production → DecisionEngine → run_long_pipeline (shared)
```

### New / changed components

| Component | Location (proposed) | Role |
|---|---|---|
| `DecisionRecord` model | `config/config_schemas.py` (extend existing) | Validated, versioned structural decisions + provenance + locks |
| `Blackboard` | `memory/blackboard.py` (new) | Atomic, lock-guarded read/write of the shared workspace on disk |
| `DecisionEngine` | `agents/decision_engine.py` (new, thin) | Orchestrates propose→consent→lock→reconcile into a `DecisionRecord` |
| `ProjectStore` / `StoryStore` | `memory/` (refactor of `PermanentMemoryLog`) | Three-tier memory: project-shared vs story-local vs audit |
| Director methods | `agents/director_agent.py` | Implement `suggest_cliffhangers`, `compact_story`; feed proposals to engine |
| Pipeline wiring | `core/pipeline_long.py` | Read structural values from `DecisionRecord`, not arithmetic |
| Dashboard wiring | `utils/local_ui.py` | Call the unified pre-production + `run_long_pipeline` |

## Components and Interfaces

### 1. DecisionRecord (the single source of truth)

Extends `config/config_schemas.py` (which is already imported by `config/__init__.py`
but unused). Each structural field is wrapped so it carries a value, provenance, and a
lock flag.

```python
# config/config_schemas.py (additions)
from typing import Literal, Optional, Dict, Any
from pydantic import BaseModel, Field

Provenance = Literal["default", "director", "writer", "user", "cli_flag"]

DECISION_SCHEMA_VERSION = 1

class Decision(BaseModel):
    """A single structural value with origin and lock state."""
    value: Any
    provenance: Provenance = "default"
    locked: bool = False          # True => downstream MUST NOT change
    rationale: str = ""           # optional, e.g. Writer's reason

class PerSegmentOverride(BaseModel):
    seg: int = Field(ge=1)
    words: Optional[int] = Field(default=None, ge=50, le=800)
    images: Optional[int] = Field(default=None, ge=1, le=30)
    locked: bool = False

class DecisionRecord(BaseModel):
    version: int = DECISION_SCHEMA_VERSION
    # structural decisions (each is a Decision wrapper)
    total_duration_min: Decision
    segment_count: Decision
    segment_duration_min: Decision
    words_per_segment: Decision
    images_per_segment: Decision
    # optional per-segment fine control
    per_segment: list[PerSegmentOverride] = []
    # length-control outcome
    end_mode: Decision           # value in {"full","cliffhanger","compact"}
    cliffhanger_point: Optional[Decision] = None
    # housekeeping
    run_mode: Decision           # value in {"project","one_time"}
    project_name: Optional[str] = None
    notes: Dict[str, Any] = {}
```

Helper methods on `DecisionRecord`:

- `set(field, value, provenance, *, lock=False)` — sets a value, refusing to overwrite
  a `locked` field unless the incoming provenance is `user`/`cli_flag` (the only ones
  allowed to (re)lock).
- `resolve_conflicts()` — checks `segment_count` vs `total_duration_min /
  segment_duration_min`; if both locked and inconsistent, raises a
  `DecisionConflict` (surfaced to user per Req 3.3); if one is locked, the locked one
  wins and the other is recomputed; otherwise prefers `segment_count` and logs.
- `to_overlay()` — flattens to the existing config-overlay shape so
  `_deep_merge(config, overlay)` keeps working unchanged.
- `provenance_report()` — dict of field → {value, provenance, locked} for the manifest.

Validation reuses the range clamps already in `config_schemas.py`
(`words_per_segment` 50–800, `images_per_segment` 1–30, segment count bounded). On
field validation failure → documented default + `provenance="default"` + warning
(Req 1.4).

### 2. Blackboard (shared workspace persistence)

```python
# memory/blackboard.py (new)
class Blackboard:
    def __init__(self, root: Path): ...
    def read(self) -> dict: ...
    def write(self, patch: dict) -> None:   # merge + atomic write under lock
    def read_decision(self) -> DecisionRecord | None
    def write_decision(self, rec: DecisionRecord) -> None
```

Persistence rules (matching existing repo conventions and research):

- **Atomic write**: serialize to `*.tmp`, then `os.replace()` — identical to the
  pattern already in `CheckpointManager.save` and `WorldState._save`.
- **Cross-process lock**: a lightweight lockfile guards writes so the FastAPI dashboard
  process and a CLI pipeline process cannot corrupt the file (Req 10.4). Prefer the
  `filelock` library if available; otherwise fall back to an `os.O_CREAT|O_EXCL`
  lockfile with retry (no hard new dependency required).
- **In-process lock**: reuse a `threading.RLock` (as `PermanentMemoryLog` does) for
  thread safety within the pipeline's worker threads.
- **Never in VRAM**: pure JSON; readable/writable with no model loaded (Req 10.3,
  Performance 1–2).

### 3. DecisionEngine (orchestration, no new model calls beyond existing consults)

```python
# agents/decision_engine.py (new, thin)
def build_decision_record(
    director: DirectorAgent,
    vision_doc: dict,
    user_locks: dict,         # explicit, typed user overrides (Req 3.5)
    cli_flags: dict,          # e.g. {"total_duration_min": 180} from --duration
    config: dict,
) -> DecisionRecord:
    """
    1. Seed defaults from config (provenance='default').
    2. Apply Director proposals from vision_doc (provenance='director').
    3. Consult Writer with the proposal; apply agreed/adjusted values
       (provenance='writer', record rationale).   # Req 2, Req 14
    4. Apply user locks and cli_flags (provenance='user'/'cli_flag', locked=True).  # Req 3
    5. rec.resolve_conflicts()                      # Req 3.3, Req 4.2
    6. return validated rec
    """
```

The Writer consult (Req 14) passes the Director's proposed structural numbers into the
existing `consult_with_writer` prompt and captures agreement/adjustment + rationale.
This is a **one-time pre-production** step (Performance 4).

### 4. Length controls (implement the stubs) — Req 6

- `DirectorAgent.suggest_cliffhangers(content, current_minutes)` → returns a list of
  `{point: float(0-100), outcome: str, minutes: int}` candidates (≥2) by asking the
  Director model to find high-note end points. One pre-production LLM call, only when
  the user chooses cliffhanger mode (Performance 5).
- `DirectorAgent.compact_story(content, target_minutes, original_minutes)` → returns
  condensed story text sized to `target_minutes`. One pre-production LLM call, only when
  the user chooses compact mode.
- Both write their outcome into the `DecisionRecord` (`end_mode`, `cliffhanger_point`,
  adjusted `total_duration_min`) with `provenance="user"`.
- Available on **both** `--topic` and `--file` entry points (Req 6.2/6.3): for `--topic`
  with no source text, negotiation uses the Director's estimated duration; cliffhanger/
  compaction that need source text are offered only when source text exists.

### 5. Runtime obeys the record — Req 4

In `core/pipeline_long.run_long_pipeline`:

```python
# BEFORE
total   = config["video"]["total_duration_min"]
seg_min = config["video"]["segment_duration_min"]
n_segs  = max(1, -(-total // seg_min))          # drops agreed segment_count
words_per_seg = config.get("script", {}).get("words_per_segment", 390)

# AFTER
rec      = blackboard.read_decision()           # single source of truth
n_segs   = rec.segment_count.value              # honored, not re-derived
words_per_seg = rec.words_per_segment.value
log.info(f"[DECISION] segments={n_segs} ({rec.segment_count.provenance}), "
         f"words/seg={words_per_seg} ({rec.words_per_segment.provenance})")
```

`story_planner.plan_story` receives `words_per_segment` as the authoritative average and
keeps per-segment `target_word_count` within a documented band (e.g. ±40%); when a
`PerSegmentOverride` is locked, it emits exactly that value (Req 4.3/4.4).

### 6. Word-count enforcement — Req 15

After each segment script is generated (the existing light LLM stage), extend
`validate_script`:

```
actual = len(words(script))
target = per_segment_target
if abs(actual - target) / target > TOLERANCE:   # e.g. 0.25
    for attempt in range(MAX_FIX_RETRIES):       # bounded (e.g. 2)
        regenerate-with-stronger-length-instruction OR trim/pad
        if within tolerance: break
    accept best result; log deviation
```

Constraints: text-stage only, bounded retries, no image/heavy GPU calls (Req 15.6).
Tightest tolerance for user-locked per-segment values (Req 15.4).

### 7. Three-tier memory — Req 12, 13

Refactor `PermanentMemoryLog` into:

```
studio_projects/
  {project}/
    project.json        # ProjectStore: characters, world lore, motifs,
                        #   visual_locks{char: {description, seed, lora_path, provenance}}
    decisions/          # past DecisionRecords for continuity / series
    stories/
      {story}/
        story.json      # StoryStore: this story's segments, arc, decision record
        audit.json      # continuity/audit log for this story
```

- **One-time-use** runs (Req 8/9) write only to a temporary/isolated story store and do
  NOT touch any `project.json`.
- `ProjectStore` exposes the existing `get_character` / `log_character` /
  `log_recurring_motif` / `check_continuity` API so callers change minimally; the data
  just lands in the project tier vs the story tier.
- **Visual lock** (Req 13): stored in `project.json` under `visual_locks`; image-gen
  reads it (continuing current description-injection + LoRA face-lock); reused across
  all stories in the project. Sparse description → skip + log, no failure.
- Backward-compat shim: if `studio_checkpoints/{topic}_memory.json` exists and no
  project store does, load it as a one-time story store.

### 8. Unify the two flows — Req 5

- `utils/local_ui.run_pipeline_thread` stops calling `read_story →
  define_pacing_and_length → generate_hinglish_script` directly. Instead it calls the
  same `run_pre_production` + `run_long_pipeline` path the CLI uses, passing the
  uploaded text as `content_text` and threading UI status/pause through the existing
  `UIState` hooks (Req 5.4).
- `define_pacing_and_length` becomes a thin shim that delegates to the
  `DecisionEngine` (or is removed if no caller remains).
- `DirectorAgent.__init__` is called consistently with the full `config` (not just
  `config["models"]`) so the dashboard path stops losing `characters` /
  `_director_vision` context. (Fixes the inconsistency noted during audit.)

### 9. Run mode + project loading — Req 8, 9

- New backend inputs: `--project {name}` (already exists; now actually used) and a new
  `--run-mode {project|one_time}` flag (+ matching API form field). Default documented
  (one_time) and logged (Req 9.5).
- Fix the dead wiring: `run_long_pipeline` passes `project_name` into `load_config()`
  so `projects/{name}.yaml` overrides load (Req 8.3 / Req 9 backward-compat item).

## Data Models

```
DecisionRecord (versioned)
 ├─ version: int
 ├─ total_duration_min: Decision{value,provenance,locked}
 ├─ segment_count:       Decision
 ├─ segment_duration_min:Decision
 ├─ words_per_segment:   Decision
 ├─ images_per_segment:  Decision
 ├─ per_segment: [PerSegmentOverride{seg,words,images,locked}]
 ├─ end_mode: Decision  # full | cliffhanger | compact
 ├─ cliffhanger_point: Decision?
 ├─ run_mode: Decision  # project | one_time
 ├─ project_name: str?
 └─ notes: {}

Blackboard (on disk)
 ├─ decision_record.json     # the DecisionRecord
 ├─ (project/story/audit stores live under studio_projects/)
 └─ written atomically, guarded by file + thread lock
```

Persistence locations:

- Decision record: persisted via `CheckpointManager` (so resume reloads it, Req 1.5 /
  7.2) AND surfaced in `run_manifest.json` (Req 17).
- Project/story/audit: under `studio_projects/{project}/...` as above.

## Schema Versioning & Migration — Req 16

```python
def load_decision_record(raw: dict) -> DecisionRecord:
    v = raw.get("version", 0)
    if v < DECISION_SCHEMA_VERSION:
        raw = migrate_decision_record(raw, from_version=v)   # fill new fields, provenance="default"
    try:
        return DecisionRecord(**raw)
    except ValidationError as e:
        log.warning(f"Decision record unmigratable ({e}) — rebuilding from config")
        return build_default_decision_record(config)
```

- `migrate_decision_record` applies ordered, idempotent steps (v0→v1, v1→v2, …),
  filling new fields with documented defaults.
- Newly written records always stamp the current `DECISION_SCHEMA_VERSION`.

## Provenance Report in Manifest — Req 17

`_write_manifest` (already in `pipeline_long.py`) gains a `decisions` block:

```json
"decisions": {
  "schema_version": 1,
  "fields": {
    "total_duration_min": {"value": 18, "provenance": "user", "locked": true},
    "segment_count":      {"value": 9,  "provenance": "writer", "locked": false},
    "words_per_segment":  {"value": 150,"provenance": "director","locked": false}
  },
  "resolved": {"segment_count": 9, "per_segment_words": [...], "images": [...]},
  "adjustments": [
    {"field": "words_per_segment", "type": "clamp", "from": 900, "to": 800},
    {"field": "segment_count", "type": "conflict_resolved", "rule": "user-lock wins"},
    {"seg": 3, "type": "word_count_correction", "target": 150, "actual": 95, "result": 142}
  ]
}
```

End-of-run, no model calls (Performance / Req 17.5).

## Correctness Properties

These are the invariants the implementation must preserve. They are the testable heart
of "Director proposes → Writer consents → User can hard-override → runtime obeys."

### Property 1: Single source of truth

For every structural value, the value used by the pipeline, story planner, and any
agent equals the value in the `DecisionRecord`. No stage recomputes a structural value
locally.

**Validates: Requirements 1.1, 4.1, 10.2**

### Property 2: Lock immutability

Once a field is `locked` with provenance `user` or `cli_flag`, no subsequent `set()` by
`director`/`writer`/`default` changes it. Only `user`/`cli_flag` may relock.

**Validates: Requirements 3.1, 3.2**

### Property 3: No silent conflict resolution

If two locked values are mutually inconsistent, the system raises/surfaces a conflict;
it never picks one silently.

**Validates: Requirements 3.3, 4.2**

### Property 4: Provenance auditability

Every value in the final record has a provenance, and every clamp, conflict resolution,
and word-count correction is recorded in the manifest. The decision chain is fully
reconstructable.

**Validates: Requirements 1.2, 17.1, 17.2**

### Property 5: Determinism of resume

Loading a persisted decision record and rebuilding the overlay yields the same
structural values as the original run (no re-prompt, no re-derive).

**Validates: Requirements 1.5, 7.2**

### Property 6: Bounded correction

Word-count enforcement always terminates within the retry limit and only ever invokes
the light text stage — never image-gen or heavy GPU work.

**Validates: Requirements 15.3, 15.6**

### Property 7: Migration safety

Any older persisted record either migrates to the current schema or is rebuilt from
config; it never crashes a resumed run.

**Validates: Requirements 16.3, 16.4**

### Property 8: Store isolation

A one-time-use run never writes to any `project.json`; a project run writes story-local
state only to its story store and shared continuity only to the project store.

**Validates: Requirements 9.3, 12.2, 12.3**

### Property 9: No added VRAM pressure

The decision/blackboard layer requires no model to be resident to read/write, and adds
zero per-segment model calls; the one-heavy-task scheduler is unchanged.

**Validates: Requirements 10.3, 15.6**

## Error Handling

| Condition | Behavior |
|---|---|
| Field validation fails | Default + `provenance="default"` + warning; continue (Req 1.4) |
| Two locked values conflict | Raise `DecisionConflict`; surface to user; do not silently pick (Req 3.3) |
| Writer unavailable / junk response | Keep Director proposal; log consent defaulted (Req 2.4 / 14.4) |
| Ollama / web search down | Fall back to documented defaults; continue (Req 9.4) |
| Decision record unmigratable | Rebuild from config; warn; no crash (Req 16.4) |
| Blackboard write contention | File lock + retry; atomic temp-replace prevents partial writes |
| Word-count never converges | Accept best after bounded retries; log (Req 15.3) |
| Visual lock can't be created | Skip + log; continue (Req 13.5) |
| One-time run | Never writes to any `project.json` (Req 9.3 / 12.3) |

## Testing Strategy

Unit (light, no GPU/LLM):

- `DecisionRecord.set` respects locks; only `user`/`cli_flag` may relock.
- `resolve_conflicts`: locked-wins, both-locked-raises, prefer-segment_count.
- Validation clamps out-of-range values and records the clamp.
- Migration: v0 record (no `version`) → current, with defaults filled.
- `to_overlay()` round-trips into `_deep_merge` without changing unrelated keys.
- Blackboard atomic write + concurrent-write lock (two threads/processes).
- Word-count enforcement: tolerance band, bounded retries, no heavy calls invoked
  (mock image-gen asserts zero calls).
- ProjectStore vs StoryStore isolation; one-time run does not write project.json.

Integration:

- CLI `--topic` dry-run: decision record built, `n_segs` equals
  `rec.segment_count.value`, manifest contains provenance report.
- Dashboard path uses unified pre-production (no `define_pacing_and_length`).
- `--project foo` actually loads `projects/foo.yaml`.
- Resume: persisted decision record reloaded; no re-prompt (Req 7.2).
- Backward-compat: legacy `{topic}_memory.json` loads as a one-time story store.

Manual / on-hardware (documented, not automated):

- A real `--file` run with a user duration lock and a cliffhanger choice, confirming
  the produced video length matches the locked value.

## Migration & Rollout

1. Add `DecisionRecord`/`Decision` to `config_schemas.py` and `Blackboard`
   (additive, nothing else changes yet).
2. Introduce `DecisionEngine`; have `run_pre_production` build a record and persist it,
   while still producing today's overlay via `to_overlay()` (behavior unchanged).
3. Switch `run_long_pipeline` to read `n_segs`/`words` from the record (the real fix).
4. Implement cliffhanger/compaction and word-count enforcement.
5. Refactor memory into project/story/audit with the backward-compat shim.
6. Re-point the dashboard at the unified pathway; reduce/remove
   `define_pacing_and_length`.
7. Add manifest provenance + schema migration.

Each step is independently shippable and reversible, keeping blast radius small.

## Resolved Decisions (was Open Questions)

1. **Per-segment override granularity** — RESOLVED: include the `PerSegmentOverride`
   data model in v1 so the record can carry it, but do NOT expose per-segment locks via
   CLI/UI in this iteration. Keeps v1 focused on global structural authority.
2. **`studio_projects/` vs `studio_checkpoints/`** — RESOLVED: introduce a new
   top-level `studio_projects/` directory. The steering docs treat `studio_checkpoints/`
   as resume-state; keeping project/story continuity separate avoids conflating the two
   and makes one-time vs project isolation obvious.
3. **Locking strategy** — RESOLVED: the runtime is effectively single-writer (one CLI
   run, or the dashboard, at a time). Use the existing in-process `threading.RLock` plus
   atomic temp-file + `os.replace` (already proven in `CheckpointManager`/`WorldState`
   and confirmed atomic on Windows). Add an OPTIONAL cross-process guard via `filelock`
   **only if it is already importable**; otherwise no-op. No new hard dependency.
4. **Word-count enforcement defaults** — RESOLVED: tolerance band ±25%, max 2 fix
   retries. Both read from config (`script.word_count_tolerance`,
   `script.word_count_max_retries`) with these defaults.

## Re-evaluation Notes (findings that confirm this design)

- `consult_with_writer` is invoked ONLY on the series-resume branch; on a fresh run the
  Writer's structural input is a side-effect of the `consult_on_config` questionnaire.
  This makes "Writer consent" largely nominal today and justifies the explicit
  consent step in `DecisionEngine` (Req 2, Req 14).
- The user's cliffhanger/compact duration is written to
  `config_overlay["video"]["total_duration_min"]` and then **overwritten** by
  `produce_runtime_config`, which returns a fresh overlay computing
  `total_duration_min = segment_count × segment_duration_min`. The DecisionRecord +
  lock ordering (apply user/cli last, then `resolve_conflicts`) fixes this directly
  (Req 3, Req 4, Req 6.8).

## Pydantic note

Use `default_factory` (not bare mutable defaults) for all list/dict fields in the new
models to avoid shared-instance bugs, consistent with Pydantic guidance. `Decision`
wrapper fields that hold defaults must be constructed per-record, not shared.
