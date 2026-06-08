@echo off
title Video.AI Studio Launcher
cd /d "%~dp0"

:: BUG-535 FIX: Only set OLLAMA_MODELS if not already defined by user
if not defined OLLAMA_MODELS set OLLAMA_MODELS=C:\models
if not defined OLLAMA_FLASH_ATTENTION set OLLAMA_FLASH_ATTENTION=1

echo ====================================================
echo    Video.AI - Narrative Video Engine Launcher
echo ====================================================
echo.

:: ── Step 1: Check Ollama ──────────────────────────────────────────────────
echo [1/3] Checking Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% == 0 goto :ollama_running

echo   ! Ollama is not running. Attempting to start Ollama...

:: Check if command is in PATH
where ollama >nul 2>&1
if %errorlevel% == 0 (
    start "" ollama serve
    goto :wait_ollama
)

:: Try standard Local AppData path
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    start "" "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
    goto :wait_ollama
)

:: Try standard Program Files path
if exist "%ProgramFiles%\Ollama\ollama.exe" (
    start "" "%ProgramFiles%\Ollama\ollama.exe" serve
    goto :wait_ollama
)

:: Try explicit user path
if exist "C:\Users\%USERNAME%\AppData\Local\Programs\Ollama\ollama.exe" (
    start "" "C:\Users\%USERNAME%\AppData\Local\Programs\Ollama\ollama.exe" serve
    goto :wait_ollama
)

:: Fallback if not found
start "" ollama serve

:wait_ollama
echo   ! Waiting for Ollama server to initialize...
timeout /t 2 /nobreak >nul
set /a attempt=1

:: BUG-532 FIX: Removed "|| exit /b 1" that caused poll loop to exit on first failure
:poll_loop
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% == 0 goto :ollama_ready
if %attempt% geq 12 (
    echo   [!] Warning: Ollama is taking longer than expected to respond.
    goto :ollama_ready
)
set /a attempt+=1
timeout /t 1 /nobreak >nul
goto :poll_loop

:ollama_ready
echo   [OK] Ollama has successfully initialized.
goto :step2

:ollama_running
echo   [OK] Ollama is running

:step2
echo.

:: ── Step 2: Check for Director model ───────────────────────────
echo [2/3] Checking LLM model (hermes-director)...
curl -s http://localhost:11434/api/tags | findstr "hermes-director" >nul
if %errorlevel% neq 0 (
    echo   ! Director model not found. Checking C:\models directory...
    if exist "C:\models\hermes-director" (
        echo   [OK] Director model GGUF found in C:\models
    ) else (
        echo   ! Director model GGUF not found in C:\models
        echo   ! Please ensure hermes-director is installed.
        echo.
        echo   Run: ollama pull hermes-director
        pause
        exit /b 1
    )
) else (
    echo   [OK] Director model is available
)
echo.

:: ── Step 3: Choose Mode ───────────────────────────────────────────────────
set OPTION=%1
if "%OPTION%"=="1" goto :ui_mode
if "%OPTION%"=="ui" goto :ui_mode
if "%OPTION%"=="--ui" goto :ui_mode
if "%OPTION%"=="2" goto :cli_mode
if "%OPTION%"=="cli" goto :cli_mode
if "%OPTION%"=="--cli" goto :cli_mode

echo [3/3] Choose Studio Mode:
echo ----------------------------------------------------
echo   [1] Launch Local Studio Web UI (Recommended)
echo   [2] Run standard CLI Story Generator
echo ----------------------------------------------------
echo.
set /p OPTION="Select Mode (1 or 2, default is 1): "
if "%OPTION%"=="" set OPTION=1

if "%OPTION%"=="2" goto :cli_mode

:ui_mode
echo.
echo ====================================================
echo   Starting Local Studio Web UI...
echo   The Frontend will open at http://localhost:5173
echo   The Backend will run on http://127.0.0.1:8000
echo ====================================================
echo.

echo Starting Frontend (Vite) in a new window...
start cmd /k "cd /d %~dp0dashboard && npm install && npm run dev"

echo Starting Backend (FastAPI)...
call venv\Scripts\activate.bat
:: BUG-534 FIX: Use -m flag to run as module for proper imports
python -m utils.local_ui
if %errorlevel% neq 0 (
    echo.
    echo [!] UI Server failed to start.
    pause
)
exit /b 0

:cli_mode
echo.
echo Enter story topic (or press Enter for "Test Topic"):
set /p TOPIC=
if "%TOPIC%"=="" set TOPIC=Test Topic

:: BUG-533 FIX: Collect extra arguments to pass to pipeline
set ARGS=
echo.
set /p ARGS="Extra args (e.g. --duration 5 --dry-run, or press Enter): "

echo.
echo Running story pipeline for topic: "%TOPIC%"...
echo.
call venv\Scripts\activate.bat
:: BUG-534 FIX: Use -m flag to run as module for proper imports
venv\Scripts\python.exe bootstrap_pipeline.py --topic "%TOPIC%" %ARGS%
if %errorlevel% neq 0 (
    echo.
    if exist "studio_outputs\*_final_video.mp4" (
        echo [!] Pipeline had warnings but video was generated.
        dir /b /od studio_outputs\*_final_video.mp4 2>nul
    ) else (
        echo [!] Pipeline failed. Check logs above for details.
        pause
        exit /b 1
    )
)
echo.
echo ====================================================
echo   [OK] Story pipeline complete!
echo   Output: studio_outputs\
echo ====================================================
pause
exit /b 0
