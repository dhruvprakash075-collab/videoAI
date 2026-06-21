# Phase 5-8 patches

This branch builds on Phases 1-4. The GitHub tooling used to author these
changes cannot apply diffs (only full-file rewrites), and the affected source
files are too large to rewrite reliably, so each phase is delivered here as a
git-applicable patch that reproduces the intended files exactly (sha256-verified).

## Applying

Patches use zero context, so apply with --unidiff-zero:

    git checkout codex/phases-5-8
    git apply --unidiff-zero patches/phase5.patch
    git add -A && git commit -m "Phase 5: route research through utils.researcher"

## Phase 5 - research consolidation

agents/director_agent.py: research_story() now delegates to
utils.researcher.research_topic (honoring the research.* config) instead of the
legacy utils.web_search path, adapting ResearchItem objects into the
{topic, combined_summary, result_count, raw_results} dict the pipeline expects.
ImportError and runtime errors fall back to empty research so the pipeline never
breaks.

tests/test_director_agent_helpers.py: rewrote the research_story tests to patch
utils.researcher.research_topic and added an empty-results case.
