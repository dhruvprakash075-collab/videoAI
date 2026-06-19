# Upstream Tooling References

This project keeps two upstream repositories checked out locally under `external/`.

## `external/ponytail`

Reference it when you want:

- compact, minimal agent instructions
- plugin and hook structure examples
- Codex / Cursor / CLI onboarding patterns

Useful entry points:

- `external/ponytail/README.md`
- `external/ponytail/.codex-plugin/`
- `external/ponytail/commands/`
- `external/ponytail/hooks/`

## `external/shadcn-improve`

Reference it when you want:

- a strong audit-to-plan workflow
- structured markdown plans
- review and execution patterns for agent work

Useful entry points:

- `external/shadcn-improve/README.md`
- `external/shadcn-improve/skills/`
- `external/shadcn-improve/examples/`

## Recommended use here

- Use Ponytail ideas for concise prompts, hooks, and editor integration.
- Use `improve` ideas for plan files, review checklists, and scoped execution steps.
- Keep this repo’s existing conventions as the source of truth for implementation details.
