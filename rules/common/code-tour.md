---
---
# Code Tour

> From ECC code-tour skill.

## When to Use

- User asks for a code tour, onboarding tour, architecture walkthrough
- User says "explain how X works" and wants a reusable guided artifact
- User wants a ramp-up path for a new engineer or reviewer
- Task is better served by a guided sequence than a flat summary

## Tour Personas

| Request | Persona | Steps |
|---------|---------|-------|
| "onboarding", "new joiner" | new-joiner | 9-13 |
| "quick tour", "vibe check" | vibecoder | 5-8 |
| "architecture" | architect | 14-18 |
| "tour this PR" | pr-reviewer | 7-11 |
| "why did this break" | rca-investigator | 7-11 |
| "security review" | security-reviewer | 7-11 |

## Video.AI Architecture Tour

```
1. bootstrap_pipeline.py         — Entry point, patches, preflight
2. core/pipeline_long.py         — Thin orchestrator
3. core/pre_production.py        — Director phase (research, outline)
4. core/segment_runner.py        — Per-segment loop
5. core/post_production.py       — Final assembly
6. agents/director_agent.py      — CrewAI agent factory
7. utils/ollama_client.py        — Circuit breaker pattern
8. utils/crewai_breaker.py       — Guarded kickoff
9. config/config.yaml            — All tunables
10. audio/                       — TTS, RVC, SFX
11. video/image_gen/             — Stable Diffusion
12. dashboard/                   — React frontend
```

## Step Types

### Directory

```json
{ "directory": "core", "title": "Pipeline Core", "description": "Orchestration logic lives here." }
```

### File + Line

```json
{ "file": "core/pipeline_long.py", "line": 50, "title": "Pipeline Entry", "description": "Main orchestration function." }
```

### Selection

```json
{
  "file": "utils/crewai_breaker.py",
  "selection": {
    "start": { "line": 20, "character": 0 },
    "end": { "line": 40, "character": 0 }
  },
  "title": "Circuit Breaker",
  "description": "Per-model circuit breaker for CrewAI calls."
}
```

## Writing Rule: SMIG

Each description should answer:
- **S**ituation: what the reader is looking at
- **M**echanism: how it works
- **I**mplication: why it matters for this persona
- **G**otcha: what a smart reader might miss

## Narrative Shape

1. Orientation
2. Module map
3. Core execution path
4. Edge case or gotcha
5. Closing / next move
