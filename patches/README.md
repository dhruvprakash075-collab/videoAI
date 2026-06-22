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
    git rm video/image_gen/framepack_i2v.py tests/test_framepack_i2v.py tests/test_motion_engine.py
    git apply --unidiff-zero patches/phase7b.patch
    git add -A && git commit -m "Phase 7b: remove dead FramePack i2v module and motion_engine config"
    git rm video/image_gen/layered_v3.py tests/test_layered_v3.py docs/layered_v3_setup.md
    git apply --unidiff-zero patches/phase7c.patch
    git add -A && git commit -m "Phase 7c: remove dead Layered V3 composition module"

The Phase 7c part 2 patches below carry normal context; apply them with plain
git apply (not --unidiff-zero):

    git apply patches/phase7c_schema.patch
    git apply patches/phase7c_local_ui.patch
    git apply patches/phase7c_test_local_ui_api.patch
    git apply patches/phase7c_preflight.patch
    git apply patches/phase7c_test_preflight.patch
    git apply patches/phase7c_test_preflight_extended.patch
    git apply patches/phase7c_ui.patch
    # Manually delete the now-unused _check_layered_v3 function body from
    # utils/preflight.py: it contains literal f-string braces that a unified
    # diff cannot carry, so the patch only removes its registration line.
    git add -A && git commit -m "Phase 7c part 2: remove residual Layered V3 references"

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

## Phase 7b - remove dead FramePack i2v motion engine

FramePack image-to-video motion was never wired into the pipeline: the module
existed but no production code path imported or called it, and motion_engine /
motion_seconds_per_image were read nowhere outside the deleted tests. Removing
it makes the renderer surface and the config truthful.

Deleted outright (run the git rm shown in Applying; not reproduced in the patch):
  - video/image_gen/framepack_i2v.py - the dormant FramePack i2v module.
  - tests/test_framepack_i2v.py - tested only the deleted module.
  - tests/test_motion_engine.py - every test referenced FramePack/motion_engine.

phase7b.patch edits:
  - core/segment_runner.py: removed the false "FramePack image-to-video motion
    (V1, opt-in)" bullet from the module docstring (no such step ever ran).
  - config/config.yaml: removed video.motion_engine and
    video.motion_seconds_per_image (no readers).
  - config/config_schemas.py: removed VideoConfig.motion_engine and
    VideoConfig.motion_seconds_per_image to match.

## Phase 7c - remove dead Layered V3 composition module

The layered_v3 multi-pass composition path (character-sheet -> background ->
character-pose -> composite-refine) was never reachable in production:
image_gen.composition_mode defaults to one_pass and nothing flips it, so the
`backend == "comfyui" and composition_mode == "layered_v3"` branch never ran.
Removing the module makes the image_gen surface and the config truthful.

Deleted outright (run the git rm shown in Applying; not reproduced in the patch):
  - video/image_gen/layered_v3.py - the dormant layered composition module.
  - tests/test_layered_v3.py - tested only the deleted module.
  - docs/layered_v3_setup.md - documented only the deleted feature.

phase7c.patch edits:
  - video/image_gen/image_gen.py: removed the composition_mode == "layered_v3"
    dispatch block in generate_images() (the import of generate_layered_images
    and the bonsai fallback).
  - config/config.yaml: removed the image_gen.layered_v3 config block.
  - pyproject.toml: removed the tests.test_layered_v3 / test_layered_v3 /
    video.image_gen.layered_v3 mypy override entries for the deleted files.

## Phase 7c part 2 - remove residual Layered V3 references

Part 1 (above) removed the runtime dispatch. Part 2 removes every remaining
reference so layered_v3 disappears from the schema, the local UI server, the
preflight checks, the React dashboard, and their tests. These patches carry
normal (non-zero) context; apply them with plain git apply (not --unidiff-zero).

  - phase7c_schema.patch (config/config_schemas.py): removed the LayeredV3Config
    class and the ImageGenConfig.layered_v3 field.
  - phase7c_local_ui.patch (utils/local_ui.py): removed the layered_v3 form
    parsing and persistence; the composition_mode handler now accepts only
    one_pass.
  - phase7c_test_local_ui_api.patch (tests/test_local_ui_api.py): removed the
    tests that exercised the deleted layered_v3 form handling.
  - phase7c_preflight.patch (utils/preflight.py): removed the _check_layered_v3
    entry from the checks list. The now-unused _check_layered_v3 function body
    must be deleted manually - it contains literal f-string braces that a
    unified diff cannot carry, so the patch only removes its registration line.
  - phase7c_test_preflight.patch (tests/test_preflight.py) and
    phase7c_test_preflight_extended.patch (tests/test_preflight_extended.py):
    removed the _check_layered_v3 references and the brace-probe test.
  - phase7c_ui.patch (dashboard/src/components/ControlPanel.jsx): removed the
    Layered settings tab, the LayeredSettings component, the layeredV3 default
    config, the APPROVAL_OPTIONS list, the layered_v3 form fields, and the
    Layers/Layered menu entries. The Composition Mode selector now offers only
    One Pass. The composition_mode plumbing is intentionally kept (one_pass is
    a valid backend value).
