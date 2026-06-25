@echo off
REM run.bat — Launch the Video.AI pipeline
REM Usage: run.bat --topic "A story about..." [options]
REM    or:  run.bat --source "path\to\source.txt" [options]
REM    or:  run.bat --dry-run --topic "test"
.\venv\Scripts\python.exe bootstrap_pipeline.py %*
