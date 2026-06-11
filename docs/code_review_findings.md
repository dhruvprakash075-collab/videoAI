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
