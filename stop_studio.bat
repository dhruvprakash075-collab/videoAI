@echo off
title Video.AI Studio Stopper
cd /d "%~dp0"

echo Stopping Video.AI Studio servers...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$pidFile = 'logs\worker.pid';" ^
    "$procs = @();" ^
    "if (Test-Path $pidFile) { $pid = Get-Content $pidFile; $match = Get-WmiObject Win32_Process -Filter \"ProcessId=$pid AND CommandLine LIKE '%%jobs.run_worker%%'\" 2>$null; if ($match) { $procs = @($match) } else { Write-Host ('  Stale PID {0} — not a worker, falling back to command-line search' -f $pid) } }" ^
    "if (-not $procs) { $procs = Get-WmiObject Win32_Process -Filter \"CommandLine LIKE '%%jobs.run_worker%%'\" 2>$null }" ^
    "if ($procs) { foreach ($p in $procs) { Write-Host ('  Stopping Worker (PID: {0})...' -f $p.ProcessId); taskkill /PID $p.ProcessId /F 2>$null | Out-Null; Write-Host '  [OK] Worker stopped' }; if (Test-Path $pidFile) { Remove-Item $pidFile -ErrorAction SilentlyContinue } } else { Write-Host '  Worker was not running.' };" ^
    "$ports = @(8000, 5173); $names = @('Backend', 'Frontend');" ^
    "for ($i = 0; $i -lt $ports.Length; $i++) {" ^
    "  $conn = netstat -ano | Select-String (':{0} ' -f $ports[$i]) | Select-String LISTENING;" ^
    "  if ($conn) {" ^
    "    $procId = ($conn -split '\s+')[-1];" ^
    "    Write-Host ('  Stopping {0} (PID: {1})...' -f $names[$i], $procId);" ^
    "    taskkill /PID $procId /F 2>$null | Out-Null;" ^
    "    Write-Host ('  [OK] {0} stopped' -f $names[$i]);" ^
    "  } else {" ^
    "    Write-Host ('  {0} was not running.' -f $names[$i]);" ^
    "  }" ^
    "}"

echo.
echo [OK] Studio stopped.
pause
