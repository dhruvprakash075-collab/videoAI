@echo off
REM open_dashboard.bat — Launch Video.AI app (backend + dashboard)
cd /d "%~dp0"
start "Video.AI Backend" cmd /c ".\venv\Scripts\python.exe -m utils.local_ui"
timeout /t 2 /nobreak >nul
cd /d "%~dp0dashboard"
start "Video.AI Dashboard" cmd /c "npm run dev"
echo Video.AI starting... Browser will open automatically.
timeout /t 5 /nobreak >nul
