@echo off
title Video.AI Job Worker
cd /d "%~dp0"
echo Starting Video.AI Job Worker...
call venv\Scripts\activate.bat
:: Run worker (use --once to run a single job)
venv\Scripts\python.exe -m jobs.run_worker %*
exit /b 0
