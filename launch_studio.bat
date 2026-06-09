@echo off
setlocal enabledelayedexpansion
title Video.AI Studio Launcher
cd /d "%~dp0"

set LOG_DIR=logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set BACKEND_LOG=%LOG_DIR%\backend.log
set FRONTEND_LOG=%LOG_DIR%\frontend.log
set WORKER_LOG=%LOG_DIR%\worker.log
set WORKER_PID=%LOG_DIR%\worker.pid
set LAUNCHER_LOG=%LOG_DIR%\launcher.log

echo [%date% %time%] Launching Video.AI Studio... > "%LAUNCHER_LOG%"

:: ── Step 1: Ollama ─────────────────────────────────────────────────────────
echo [1/5] Checking Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% == 0 goto :ollama_running

echo   ! Ollama not running. Starting Ollama...
echo [%date% %time%] Starting Ollama... >> "%LAUNCHER_LOG%"
start /B "" "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve > "%LOG_DIR%\ollama.log" 2>&1
timeout /t 3 /nobreak >nul
set attempt=1

:ollama_wait
if %attempt% gtr 15 goto :ollama_ok
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% == 0 goto :ollama_ok
timeout /t 1 /nobreak >nul
set /a attempt+=1
goto :ollama_wait

:ollama_running
:ollama_ok
echo   [OK] Ollama is running
echo [%date% %time%] Ollama OK >> "%LAUNCHER_LOG%"

:: ── Step 2: Backend ────────────────────────────────────────────────────────
echo [2/5] Starting Backend...
curl -s http://127.0.0.1:8000/ >nul 2>&1
if %errorlevel% == 0 goto :backend_running

echo   Starting backend (logs: %BACKEND_LOG%)...
echo [%date% %time%] Starting backend... >> "%LAUNCHER_LOG%"
start "Video.AI Backend" /MIN cmd /c "cd /d %~dp0 && venv\Scripts\python.exe -m utils.local_ui > %BACKEND_LOG% 2>&1"
set attempt=1

:backend_wait
if %attempt% gtr 30 goto :backend_timeout
curl -s http://127.0.0.1:8000/ >nul 2>&1
if %errorlevel% == 0 goto :backend_ok
timeout /t 1 /nobreak >nul
set /a attempt+=1
goto :backend_wait

:backend_running
echo   [OK] Backend already running on port 8000
echo [%date% %time%] Backend already running on port 8000 >> "%LAUNCHER_LOG%"
goto :step3

:backend_timeout
echo   [!] Backend failed to start. Check %BACKEND_LOG%
echo [%date% %time%] Backend timeout >> "%LAUNCHER_LOG%"
goto :step3

:backend_ok
echo   [OK] Backend running on http://127.0.0.1:8000
echo [%date% %time%] Backend OK >> "%LAUNCHER_LOG%"

:: ── Step 3: Frontend ───────────────────────────────────────────────────────
:step3
echo [3/5] Starting Frontend...
if exist "dashboard\node_modules" (
    echo   node_modules found
) else (
    echo   no node_modules
)
curl -s http://localhost:5173/ >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Frontend already running
    goto :step4
)

echo   Starting frontend (logs: %FRONTEND_LOG%)...
start "Video.AI Frontend" /MIN cmd /c "cd /d %~dp0 && cd dashboard && npm run dev > ..\%FRONTEND_LOG% 2>&1"
set attempt=1

:frontend_wait
if %attempt% gtr 30 (
    echo   [!] Frontend failed to start. Check %FRONTEND_LOG%
    goto :step4
)
curl -s http://localhost:5173/ >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Frontend running on http://localhost:5173
    goto :step4
)
timeout /t 1 /nobreak >nul
set /a attempt+=1
goto :frontend_wait

:: ── Step 4: Worker ─────────────────────────────────────────────────────────
:step4
echo [4/5] Starting Worker...
set WORKER_RUNNING=0
if exist "%WORKER_PID%" (
    set /p OLD_PID=<"%WORKER_PID%"
    if not "!OLD_PID!"=="" (
        powershell -NoProfile -Command "if ((Get-Process -Id !OLD_PID! -ErrorAction SilentlyContinue) -and (Get-WmiObject Win32_Process -Filter 'ProcessId=!OLD_PID!' | Where-Object { $_.CommandLine -like '*jobs.run_worker*' })) { exit 0 } else { exit 1 }" >nul 2>&1
        if !errorlevel! == 0 (
            set WORKER_RUNNING=1
        )
    )
)
if "!WORKER_RUNNING!"=="1" (
    echo   [OK] Worker already running (PID from %WORKER_PID%)
    goto :dashboard
)

echo   Starting worker (logs: %WORKER_LOG%)...
echo [%date% %time%] Starting worker... >> "%LAUNCHER_LOG%"
start "Video.AI Worker" /MIN cmd /c "cd /d %~dp0 && venv\Scripts\python.exe -m jobs.run_worker > %WORKER_LOG% 2>&1"
:: Wait and capture PID, retry up to 5 seconds
powershell -NoProfile -Command ^
    "$tries = 0; $pid = $null;" ^
    "while ($tries -lt 10 -and -not $pid) {" ^
    "  $pid = (Get-WmiObject Win32_Process -Filter \"CommandLine LIKE '%%jobs.run_worker%%'\" 2>$null).ProcessId;" ^
    "  if (-not $pid) { Start-Sleep -Milliseconds 500; $tries++ }" ^
    "}" ^
    "if ($pid) { Set-Content -Path '%WORKER_PID%' -Value $pid; Write-Host ('  [OK] Worker PID: {0}' -f $pid) } ^ else { Write-Host '  [WARN] Worker PID not captured within 5s (check %WORKER_LOG%)' }"

:: ── Dashboard ──────────────────────────────────────────────────────────────
:dashboard
echo [5/5] Opening browser...
timeout /t 1 /nobreak >nul
start http://localhost:5173/
echo [%date% %time%] Browser opened >> "%LAUNCHER_LOG%"

echo.
echo ====================================================
echo   Video.AI Studio is running!
echo   Frontend: http://localhost:5173
echo   Backend:  http://127.0.0.1:8000
if "!WORKER_RUNNING!"=="1" (
echo   Worker:   Running (reused)
) else (
echo   Worker:   Started (PID saved)
)
echo   Logs:     %cd%\%LOG_DIR%\
echo ====================================================
echo.
echo Close this window to keep running, or use stop_studio.bat
echo to shut down cleanly.
echo.
pause
