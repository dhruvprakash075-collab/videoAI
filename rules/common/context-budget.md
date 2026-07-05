---
---
# Context Budget

> From ECC context-budget skill.

## Purpose

Audit context window usage across all loaded components. Identify waste and recommend token savings.

## Four-Phase Process

### Phase 1: Inventory

Scan component directories and estimate tokens:

| Component | Heuristic |
|-----------|-----------|
| Prose (AGENTS.md, rules) | words × 1.3 |
| Agent definitions | ~500 tokens per tool |
| MCP tools | ~500 tokens per tool |

**Flag heavy components:**
- Agents > 200 lines
- Descriptions > 30 words
- Skills > 400 lines
- Rules > 100 lines
- AGENTS.md/rules chains > 300 lines combined

### Phase 2: Classify

Sort components into:

| Bucket | Meaning |
|--------|---------|
| Always needed | Referenced in AGENTS.md or active command backing |
| Sometimes needed | Used for specific tasks |
| Rarely needed | Not referenced, no active command |

### Phase 3: Detect Issues

| Issue | Threshold |
|-------|-----------|
| Bloated agent descriptions | > 30 words |
| Heavy agent files | > 200 lines |
| Redundant components | Duplicate functionality |
| MCP over-subscription | > 10 servers |
| AGENTS.md/rules bloat | > 300 lines combined |

### Phase 4: Report

```markdown
Context Budget Report
──────────────────────────────
Category          Tokens    Files
AGENTS.md         1,200     1
Agents            3,500     7
Rules             4,200     20
Commands          2,100     8
──────────────────────────────
Total:            11,000

Warnings:
- 2 agents exceed 200 lines
- 3 rules exceed 100 lines

Top 3 Optimizations:
1. Trim agent descriptions (save ~500 tokens)
2. Merge similar rules (save ~300 tokens)
3. Remove rarely-used components (save ~200 tokens)
```

## Key Insight

MCP tools represent the biggest token lever — a 30-tool server can cost more than all skills combined. Agent descriptions also load into context even when never invoked.

## Best Practices

- Audit after every component addition
- Use `--verbose` for detailed per-file breakdowns
- Keep AGENTS.md focused (under 200 lines)
- Trim agent descriptions to essentials
- Remove unused rules and commands
