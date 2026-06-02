# Agent Orchestration

## Available Agents

Located in `agents/`:

| Agent | Purpose | When to Use |
|-------|---------|-------------|
| **planner** | Implementation planning | Complex features, refactoring |
| **architect** | System design | Architectural decisions |
| **tdd-guide** | Test-driven development | New features, bug fixes |
| **code-reviewer** | Code review | After writing code |
| **security-reviewer** | Security analysis | Before commits |
| **python-reviewer** | Python-specific review | After Python changes |
| **performance-optimizer** | Performance analysis | When code is slow |

## Immediate Agent Usage

No user prompt needed — invoke proactively:

- **Code changes** → code-reviewer
- **New features / bugs** → tdd-guide
- **Complex features** → planner
- **Architectural decisions** → architect
- **Before commits** → security-reviewer

## Parallel Task Execution

Use parallel execution for independent operations:

```markdown
# GOOD: Parallel execution
- Security review (agent: security-reviewer)
- Performance analysis (agent: performance-optimizer)
- Code review (agent: code-reviewer)

# BAD: Sequential execution (wastes time)
- Security review
- Then performance analysis
- Then code review
```

## Multi-Perspective Analysis

For complex problems, use split-role sub-agents:
- Factual reviewer
- Senior engineer
- Security expert
- Consistency reviewer
- Redundancy checker
