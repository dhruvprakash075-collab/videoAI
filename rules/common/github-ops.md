---
---
# GitHub Operations

> From ECC github-ops skill.

## Tool Requirements

Requires `gh` CLI with auth configured via `gh auth login`.

## Issue Triage

Classify issues by type and priority:

| Type | Label |
|------|-------|
| Bug | `bug` |
| Feature request | `feature-request` |
| Question | `question` |
| Documentation | `documentation` |
| Enhancement | `enhancement` |

**Workflow:**
1. Read the issue
2. Check for duplicates via search
3. Apply labels
4. Respond to questions
5. Request reproduction steps for bugs

## PR Management

**Review Checklist:**
- Check CI status: `gh pr checks`
- Check mergeability
- Check age/last activity
- Flag PRs older than 5 days without review

**Stale Policy:**
- Issues inactive 14+ days → stale label
- PRs inactive 7+ days → comment
- Stale issues auto-close after 30 days

## CI/CD Operations

When CI fails:
1. View failed run logs: `gh run view --log-failed`
2. Identify the failing step
3. Determine if flaky or real
4. Suggest fixes or note patterns

```bash
# List failed runs
gh run list --status failure

# Rerun failed jobs
gh run rerun --failed
```

## Release Management

1. Verify CI is green on main
2. Review unreleased merged PRs
3. Generate changelogs from PR titles
4. Create releases

```bash
# List merged PRs
gh pr list --state merged --json title,number,mergedAt

# Create release
gh release create v1.0.0 --generate-notes

# Create pre-release
gh release create v1.0.0-beta.1 --prerelease --generate-notes
```

## Security Monitoring

- Check Dependabot alerts: `gh api /repos/{owner}/{repo}/vulnerability-alerts`
- Auto-merge safe dependency bumps
- Flag critical/high severity alerts immediately
- Check Dependabot weekly

## Quality Gate

Before completing any task:
- Triaged issues have appropriate labels
- No PR older than 7 days lacks review
- CI failures investigated (not just re-run)
- Releases have accurate changelogs
- Security alerts acknowledged and tracked
