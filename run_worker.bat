@echo off
title Video.AI Job Worker
cd /d "%~dp0"
echo Starting Video.AI Job Worker...
venv\Scripts\python.exe -m jobs.run_worker %*
exit /b %errorlevel%