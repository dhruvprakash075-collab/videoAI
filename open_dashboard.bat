@echo off
echo Starting dashboard server...
cd /d "%~dp0dashboard"
start /b cmd /c "npm run dev >nul 2>&1"
timeout /t 3 /nobreak >nul
echo Opening browser with dashboard...
start "" "http://localhost:5173/"
echo.
echo Dashboard running at http://localhost:5173/
echo Close this window or press Ctrl+C to stop.
pause >nul
