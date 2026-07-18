@echo off
REM run.bat — Launch the Video.AI pipeline
REM Usage: run.bat --topic "A story about..." [options]
REM    or:  run.bat --source "path\to\source.txt" [options]
REM    or:  run.bat --dry-run --topic "test"
if "%~1"=="" (
    echo Usage: run.bat --topic "A story about..." [options]
    echo    or:  run.bat --source "path\to\source.txt" [options]
    echo    or:  run.bat --dry-run --topic "test"
    echo.
    echo Run "run.bat --help" for all options.
    pause
    exit /b 1
)
.\venv\Scripts\python.exe bootstrap_pipeline.py %*
