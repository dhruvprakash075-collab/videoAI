# Test Audit — tests as suspects

Tests were treated as first-class audit subjects (the user's instruction:
"check test also they can also have problem").

## Suite health

- `python -m pytest tests` (system 3.14.5): **2048 passed, 5 skipped, 1 warning
  in 26.43 s**. Warning: fastapi/httpx deprecation (3rd-party, not ours).
- 5 skipped = smoke tests that auto-skip without a live instance — by design.
- `tests/conftest.py` injects lightweight stubs for `torch`(+cuda), `pyarrow`,
  `crewai`, `faster_whisper`, `whisper` — no real GPU/model calls anywhere in
  the suite; GPU interaction is mocked. Good pattern.
- `tests/unit/test_no_broad_suppress.py` — guards against broad exception
  suppression; passes. The discipline it enforces held up in the silent-
  failure sweep (bugs.md).

## Findings

1. **Vacuous test**: `tests/test_omnivoice_worker.py:7-10` `test_set_seed_noop`
   ends with a bare `assert True` — the tautology is the whole test body.
   It verifies "does not raise" for `_set_seed(None)`/`_set_seed(-1)`; the
   `assert True` should be removed (or the no-raise intent asserted via a
   `pytest.raises`-free docstring). Harmless but sets a bad example and would
   mask a real regression if the body ever shrank.
   (The other hit — `test_post_production.py:686 assert None not in
   passed_mp4s` — is a real assertion, not vacuous.)
2. **Dead-code keepalive**: `test_audio_fx.py`, `test_ip_adapter.py`,
   `test_retry_manager.py`, `test_web_search.py` — 4 test files cover modules
   with 0 production importers (usage.md). Deleting the modules (bugs.md #6-10)
   implies deleting these tests; keeping them makes the dead code look alive
   in coverage reports.
3. **Committed since audit start**: `tests/test_decision_engine.py` +
   `agents/decision_engine.py` (dirty at start, flagged) landed as
   bb6e6411c/c9a59b525 — working tree now clean, no surprise-dirty files.
4. **Mock targets resolve correctly** for the audited modules (torch/numpy/
   subprocess patches in `test_omnivoice_worker.py`, `test_rust_audio_opt_in.py`,
   `test_audio_proxy*.py` all patch existing symbols — verified against source).
   No patch-object-on-nonexistent-target pattern found in the files checked.
5. Test count breakdown: 109 test files, all import at least one prod module —
   no orphan test file, no test-only package.
6. **Skip/xfail inventory is clean**: 6 conditional skips total
   (`test_job_system.py:381,392,399,406` — requests/server availability;
   `test_sanitize_meta.py:116` — missing fixture; `test_world_state.py:130` —
   Ollama not running). Zero xfails, zero unconditional skips. Nothing is
   silently suppressed; the 5 skips seen in the suite run are all here.

## Scope note

Vacuous-assert and mock-target checks were manual/grep-assisted across all
109 test files; not every test body was read line-by-line. The 2048-passing
suite plus `test_no_broad_suppress` and the error-taxonomy tests give
reasonable confidence; the items above are what the sweep surfaced.
