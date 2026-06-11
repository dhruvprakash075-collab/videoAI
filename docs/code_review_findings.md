# Code Review Findings Register (2026-06-11)

Manual line-by-line review of the untested zone + skylos triage + pattern sweeps.
Files fully read: local_ui.py, jobs/worker.py, source_loader.py, omnivoice_worker.py,
f5_worker.py, indicf5_worker.py, supertonic_worker.py, bootstrap_pipeline.py, studio_tui.py.

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

- L1 studio_tui.py: path containment via str.startswith(PROJECT_ROOT) - prefix bug ('/project-evil' passes). Use Path.relative_to.
- L2 studio_tui.py: UIState.logs read/reset without _log_lock (local_ui locks; TUI does not).
- L3 bootstrap_pipeline.py: success print formats result.get('duration_s') with :.1f; missing key = TypeError, successful run reported as FAILED.
- L4 bootstrap_pipeline.py: --file read without existence check (raw traceback).
- L5 indicf5_worker.py _chunk_text: re.split removes sentence punctuation; TTS loses prosody marks (omnivoice keeps them).
- L6 indicf5_worker.py: speed silently ignored when model lacks config.speed.
