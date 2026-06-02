---
---
# Git Workflow Patterns

> From ECC git-workflow skill.

## Conventional Commits

```
<type>(<scope>): <subject>

[optional body]

[optional footer(s)]
```

### Types

| Type | Use For | Example |
|------|---------|---------|
| `feat` | New feature | `feat(auth): add OAuth2 login` |
| `fix` | Bug fix | `fix(api): handle null response` |
| `docs` | Documentation | `docs(readme): update instructions` |
| `style` | Formatting | `style: fix indentation` |
| `refactor` | Refactoring | `refactor(db): extract connection pool` |
| `test` | Tests | `test(auth): add unit tests` |
| `chore` | Maintenance | `chore(deps): update dependencies` |
| `perf` | Performance | `perf(query): add index` |

### Good vs Bad

```
# Bad
git commit -m "fixed stuff"
git commit -m "updates"
git commit -m "WIP"

# Good
git commit -m "fix(api): retry requests on 503 Service Unavailable

The external API occasionally returns 503 errors during peak hours.
Added exponential backoff retry logic with max 3 attempts.

Closes #123"
```

## Branching Strategy (GitHub Flow)

```
main (protected, always deployable)
  │
  ├── feature/user-auth      → PR → merge to main
  ├── feature/payment-flow   → PR → merge to main
  └── fix/login-bug          → PR → merge to main
```

**Rules:**
- `main` is always deployable
- Create feature branches from `main`
- Open Pull Request when ready for review
- After approval and CI passes, merge to `main`

## Branch Naming

```
feature/user-authentication
fix/login-redirect-loop
hotfix/critical-security-patch
release/1.2.0
experiment/new-caching-strategy
```

## PR Description Template

```markdown
## What
Brief description of what this PR does.

## Why
Explain the motivation and context.

## How
Key implementation details.

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing performed

## Checklist
- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Tests pass locally
- [ ] Related issues linked

Closes #123
```

## Merge vs Rebase

### Merge (Preserves History)
```bash
git checkout main
git merge feature/user-auth
```
**Use when:** Merging feature branches into `main`, multiple people worked on the branch.

### Rebase (Linear History)
```bash
git checkout feature/user-auth
git rebase main
```
**Use when:** Updating local feature branch, want linear history, branch is local-only.

### NEVER Rebase
- Branches pushed to shared repository
- Other people have based work on
- Protected branches (main, develop)

## Undoing Mistakes

```bash
# Undo last commit (keep changes)
git reset --soft HEAD~1

# Undo last commit (discard changes)
git reset --hard HEAD~1

# Undo last commit pushed to remote
git revert HEAD

# Fix last commit message
git commit --amend -m "New message"
```

## Git Hooks

### Pre-Commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit

# Run linting
ruff check . || exit 1

# Run tests
pytest tests/ -q || exit 1

# Check for secrets
if git diff --cached | grep -E '(password|api_key|secret)'; then
    echo "Possible secret detected. Commit aborted."
    exit 1
fi
```
