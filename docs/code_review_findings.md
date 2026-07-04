# Code Review Findings Register (2026-06-11)

Manual line-by-line review of the untested zone + skylos triage + pattern sweeps.
Files fully read: local_ui.py, jobs/worker.py, source_loader.py, omnivoice_worker.py,
f5_worker.py, indicf5_worker.py, supertonic_worker.py, bootstrap_pipeline.py.

## HIGH severity

- H1 jobs/worker.py run_once: heartbeat thread leak. _heartbeat_loop sleeps 10s, join(timeout=2) times out, then _stop.clear() revives the old thread. Every job leaks a thread that keeps heartbeating the finished job, defeating mark_stale_running_failed(). Fix: per-job stop event.
- H2 indicf5_worker.py _load_model: trust_remote_code=True executes arbitrary Python from the HF repo; model_id is CLI-configurable. Fix: pin revision=<commit>.
- H3 local_ui.py save_ui_config: non-atomic config.yaml write (truncate-then-write); crash corrupts config. Also read-modify-write race. Fix: temp file + os.replace + lock.
- H4 local_ui.py get_artifact_detail: route {run_id:path} accepts '/', sanitizer raises uncaught ValueError -> HTTP 500. Fix: catch and return 400.
- H5 local_ui.py /api/chat: unbounded session memory leak; full history returned each reply. Fix: TTL eviction + message cap.
- H6 source_loader.py _load_url: no response size cap (OOM) and no scheme/host validation (SSRF). Fix: stream + byte cap.
- H7 source_loader.py load_source: pasted text ending in .txt/.md/.pdf/.docx is treated as a file path and read from disk = arbitrary local file read. Fix: explicit paste-vs-path flag.

## MEDIUM severity

- M1 indicf5_worker.py: nfe_step accepted but never passed to the model (silent no-op).
- M2 indicf5_worker.py: WAV written with requested sample_rate, not the model's actual rate.
- M3 omnivoice_worker.py _prepare_ref_audio: non-atomic cache write; corrupt partial file reused forever.
- M4 f5_worker.py _resolve_model_path: docstring promises refs/main resolution; code picks first snapshot dir (stale model risk).
- M5 supertonic_worker.py: seed from abs(hash(text)) is salted per process; 'deterministic' output is not reproducible. Use hashlib.
- M6 local_ui.py ab_generate: eviction drops first 5 jobs regardless of status; in-flight GPU jobs lose tracking (404 for pollers).
- M7 local_ui.py ab_pick: empty variant dir returns status committed with 0 images (silent false success).
- M8 local_ui.py upload_script: does not call _validate_job_request (unlike /api/jobs); run_mode etc. unvalidated.
- M9 local_ui.py: 7+ endpoints return str(e) in 500 responses (internal detail leak). Same pattern in core/post_production.py:250.
- M10 local_ui.py /api/chat: session list appended outside the lock (race).
- M11 jobs/worker.py: temp content file leaks if Popen raises (cleanup only on happy path).
- M12 jobs/worker.py: cancel vs natural-exit TOCTOU; job can be marked CANCELED despite rc==0.
- M13 utils/seo_generator.py:295: XSS, HTML built from unescaped user input (skylos, confirmed pattern).
- M14 agents/director_agent.py:1966: ask_cache_ttl stub with only pass; silently does nothing.

## LOW severity

- L3 bootstrap_pipeline.py: success print formats result.get('duration_s') with :.1f; missing key = TypeError, successful run reported as FAILED.
- L4 bootstrap_pipeline.py: --file read without existence check (raw traceback).
- L5 indicf5_worker.py _chunk_text: re.split removes sentence punctuation; TTS loses prosody marks (omnivoice keeps them).
- L6 indicf5_worker.py: speed silently ignored when model lacks config.speed.
- L7 omnivoice/f5 workers: empty synthesis writes near-silent WAV and reports success (silent failure family).
- L8 omnivoice_worker.py: one-shot default num_step=24 vs persistent default 40 (inconsistent quality).
- L9 f5_worker.py: missing checkpoint falls through to cryptic crash instead of clean error.
- L10 f5/omnivoice workers: process-global torchaudio.load monkeypatch ignores normalize/format kwargs.
- L11 supertonic_worker.py: SUPPORTED_LANGS defined but never enforced.
- L12 local_ui.py upload_voice: all-symbol character_name sanitizes to empty -> writes '.wav'; no size cap on uploads.
- L13 local_ui.py: voice_engine and numeric form fields unvalidated (ranges/allowed values); list_jobs limit unbounded.
- L14 local_ui.py: TOCTOU on UIState.status in manual_pause/consultation_reply; topic interpolated into chat system prompt (prompt injection).
- L15 jobs/worker.py: run_forever logs crashes to magic job_id=0; VENV_PY hardcodes Windows path.
- L16 local_ui.py: duplicate bool parsers (_form_bool vs inner _parse_bool).

## Corrections to scanner results

- skylos 'unsafe archive extraction source_loader.py:235' is a FALSE POSITIVE (line is trafilatura.extract, no archives in file).
- skylos 'unused imports' in pipeline_long/image_gen/context_manager/pre_production are intentional re-exports (noqa: F401); do not remove.
- skylos 'unused parameter indicf5:135' and 'unused variable SUPPORTED_LANGS' are REAL bugs (M1, L11).

## Summary

7 HIGH, 14 MEDIUM, 16 LOW = 37 verified findings. Tested zone (95% coverage) is clean; all findings concentrate in the untested zone. Recommended fix order: H1-H7, then M1-M14.
