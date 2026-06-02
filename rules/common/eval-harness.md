---
---
# Eval Harness

> From ECC eval-harness skill.

## Philosophy

Eval-Driven Development treats evals as the "unit tests of AI development":
- Define expected behavior BEFORE implementation
- Run evals continuously during development
- Track regressions with each change
- Use pass@k metrics for reliability measurement

## Eval Types

### Capability Evals

Test if the pipeline can do something:

```markdown
## Capability Eval: new-tts-engine

Task: Generate TTS audio with new engine
Success Criteria:
  - [ ] Audio file created
  - [ ] Duration matches expected
  - [ ] Quality meets threshold
Expected Output: WAV file in output directory
```

### Regression Evals

Ensure changes don't break existing functionality:

```markdown
## Regression Eval: segment-runner

Baseline: 2026-06-02
Tests:
  - segment-creation: PASS
  - audio-generation: PASS
  - image-generation: PASS
  - subtitle-rendering: PASS
Result: 4/4 passed (previously 4/4)
```

## Grader Types

### Code-Based Grader

Deterministic checks:

```bash
# Check if output file exists
test -f output/segment_01.mp4 && echo "PASS" || echo "FAIL"

# Check if tests pass
python -m pytest tests/ -q && echo "PASS" || echo "FAIL"

# Check if coverage meets threshold
python -m coverage report --fail-under=80 && echo "PASS" || echo "FAIL"
```

### Model-Based Grader

Use Claude to evaluate open-ended outputs:

```markdown
Evaluate the following video segment:
1. Does it match the script?
2. Is the audio clear?
3. Are the subtitles accurate?
4. Is the image quality acceptable?

Score: 1-5 (1=poor, 5=excellent)
```

## Metrics

### pass@k

"At least one success in k attempts"
- pass@1: First attempt success rate
- pass@3: Success within 3 attempts
- Typical target: pass@3 > 90%

### pass^k

"All k trials succeed"
- Higher bar for reliability
- pass^3: 3 consecutive successes
- Use for critical paths

## Eval Workflow

### 1. Define (Before Coding)

```markdown
## EVAL DEFINITION: feature-xyz

### Capability Evals
1. Pipeline generates video successfully
2. Audio quality meets threshold
3. Subtitles are accurate

### Regression Evals
1. Existing pipeline still works
2. Config changes don't break defaults
3. Error handling unchanged

### Success Metrics
- pass@3 > 90% for capability evals
- pass^3 = 100% for regression evals
```

### 2. Implement

Write code to pass the defined evals.

### 3. Evaluate

```bash
# Run capability evals
[Run each capability eval, record PASS/FAIL]

# Run regression evals
python -m pytest tests/ -q

# Generate report
```

### 4. Report

```markdown
EVAL REPORT: feature-xyz
========================

Capability Evals:
  pipeline-run:     PASS (pass@1)
  audio-quality:    PASS (pass@2)
  subtitle-accuracy: PASS (pass@1)
  Overall:          3/3 passed

Regression Evals:
  segment-runner:   PASS
  config-loading:   PASS
  error-handling:   PASS
  Overall:          3/3 passed

Metrics:
  pass@1: 67% (2/3)
  pass@3: 100% (3/3)

Status: READY FOR REVIEW
```

## Eval Storage

```
.claude/
  evals/
    feature-xyz.md      # Eval definition
    feature-xyz.log     # Eval run history
    baseline.json       # Regression baselines
```

## Best Practices

1. **Define evals BEFORE coding** — Forces clear thinking about success criteria
2. **Run evals frequently** — Catch regressions early
3. **Track pass@k over time** — Monitor reliability trends
4. **Use code graders when possible** — Deterministic > probabilistic
5. **Human review for security** — Never fully automate security checks
6. **Keep evals fast** — Slow evals don't get run
7. **Version evals with code** — Evals are first-class artifacts
