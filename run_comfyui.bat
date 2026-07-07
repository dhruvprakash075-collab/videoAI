@echo off
setlocal
cd /d "%~dp0external\ComfyUI" || exit /b 1

powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8188/system_stats' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
  echo ComfyUI is already running at http://127.0.0.1:8188
  start "" "http://127.0.0.1:8188"
  exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
  echo Missing ComfyUI Python: %cd%\.venv\Scripts\python.exe
  pause
  exit /b 1
)

if not exist "models\checkpoints\DreamShaper_8.safetensors" (
  echo Missing checkpoint: %cd%\models\checkpoints\DreamShaper_8.safetensors
  pause
  exit /b 1
)

"%cd%\.venv\Scripts\python.exe" main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --preview-method auto
if errorlevel 1 pause
