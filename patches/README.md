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
    git apply --unidiff-zero patches/phase6.patch
    git add -A && git commit -m "Phase 6: remove dead config switches; honor critic.enabled"
    git apply --unidiff-zero patches/phase7a.patch
    git add -A && git commit -m "Phase 7a: remove dormant replicate/pexels image backend"

## Phase 5 - research consolidation

agents/director_agent.py: research_story() now delegates to
utils.researcher.research_topic (honoring the research.* config) instead of the
legacy utils.web_search path, adapting ResearchItem objects into the
{topic, combined_summary, result_count, raw_results} dict the pipeline expects.
ImportError and runtime errors fall back to empty research so the pipeline never
breaks.

tests/test_director_agent_helpers.py: rewrote the research_story tests to patch
utils.researcher.research_topic and added an empty-results case.

## Phase 6 - truthful config switches

Removes config the code never honored, so the schema stops advertising behavior
that does not exist:

config/config_schemas.py + config/config.yaml: removed the duplicate
script.critic_enabled / script.critic_threshold / script.critic_max_rewrites
keys. The real critic settings live under the top-level critic: section
(critic.enabled / critic.threshold / critic.max_rewrites), read by
utils/critic.py. Also removed performance.checkpoint_interval, which had no
readers.

core/pipeline_graph.py: route_after_critic now reads critic.max_rewrites (the
canonical source) instead of the removed script.critic_max_rewrites. Added
route_after_write so critic.enabled is finally honored: when it is false the
writer routes straight to translation, skipping the critic node. The
write_script_node -> critic_node edge is now a conditional edge.

tests/test_pipeline_graph.py: _FakeCtx nests max_rewrites/threshold under
critic, plus two new tests covering route_after_write for the enabled and
disabled critic cases.

## Phase 7a - remove dormant replicate/pexels backend

The replicate and pexels image backends were dead code: defined in image_gen.py
but not wired into any active code path (no caller anywhere in the pipeline).
Removing them makes the dependency set and the image_gen surface truthful.

video/image_gen/image_gen.py: removed _replicate() and _pexels(), plus the
now-unused imports json, os, urllib.parse and urllib.request.

tests/test_image_gen.py: removed the _pexels/_replicate imports and the
tests that exercised the deleted functions
(test_pexels_search_url_has_no_literal_braces, TestReplicateRegression and
TestPexelsRegression).

requirements.txt: dropped the replicate>=1.0.7 dependency.
