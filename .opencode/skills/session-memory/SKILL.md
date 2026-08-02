---
name: session-memory
description: Load and persist cross-session memory. Use when a session starts, when the user asks about previous sessions ("what did we do last time", "previous session", "last session", "memory", "remember", "context from before"), or when wrapping up work ("save this", "remember this for next time", "session log"). Reads the newest docs/session-*.md on start, writes/updates one on end.
---

# Session Memory

The repo keeps a durable, committed session log so any future session can
pick up without re-investigating. One file per day:

- `docs/session-YYYY-MM-DD.md` — log of that day's session(s).

## On session start (or when the user asks "what did we do before")

1. Glob `docs/session-*.md` and read the NEWEST file (sort by name — the
   ISO date prefix sorts correctly).
2. Also check `PONYTAIL-DEBT.md` (root) for the ponytail debt rows — the
   `no-trigger` rows are the silent-rot risk.
3. Give the user a 3-6 line digest of what's relevant to THIS conversation:
   last commits, open items, environment gotchas (e.g. remote is
   `github-origin`, not `origin`), pinned-deps rules.
4. Do not re-read older session files unless the user asks — one file back
   is enough context; older files are for archaeology.

## During the session

No action. Keep context lean.

## On session end (user says "done", "save this", "remember this", or asks to
wrap up)

1. If `docs/session-<today>.md` exists, APPEND a new section to it instead
   of overwriting. Otherwise create it, copying the structure of the newest
   existing session file (see template below).
2. Content — keep it dense, one line per fact:
   - Date + one-line scope
   - What was done (bullets, file:function anchors where useful)
   - Commits (short hashes)
   - Decisions + rationale (one line each)
   - New environment gotchas discovered this session
   - Open items / follow-ups (these feed the next session's start)
3. Commit and push: `git add docs/session-*.md && git commit -m "docs: session log <date>" && git push github-origin main`
   (remote is `github-origin` — `origin` does not exist in this repo.)

## Template

```markdown
# Session Log — YYYY-MM-DD: <short scope>

## What was done
- <what>, <where (file:line)>, <why>

## Commits
- `<hash>` <subject>

## Decisions
- <decision> — <one-line rationale>

## Gotchas
- <anything that cost time; e.g. remote name, interpreter path, flags>

## Open items
- <follow-up for the next session>
```

## Rules

- Commit the log before the session ends — uncommitted memory dies with the
  machine. Push it too (that's the point of "any session").
- Never rewrite history in session logs; append.
- If the user is mid-task and asks to save, write the log WITHOUT blocking
  their current work — do it after their current step completes.
