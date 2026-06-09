# Run and detach the worker on Windows. This script is a convenience wrapper.
# It assumes run_worker.bat exists at repo root and will start it detached.
param(
    [switch]$Once
)

$repo = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $repo

$bat = Join-Path $repo ".." "run_worker.bat"
$bat = (Resolve-Path $bat).Path

$logdir = Join-Path $repo ".." "logs"
if (-not (Test-Path $logdir)) { New-Item -ItemType Directory -Path $logdir | Out-Null }
$log = Join-Path $logdir "worker.log"

# Start the worker via cmd.exe to ensure proper batch handling. Use START to detach.
if ($Once) {
    & cmd /c `"$bat --once`"
    exit $LASTEXITCODE
} else {
    Start-Process -FilePath cmd -ArgumentList "/c", "start", "", `"$bat`" -WindowStyle Hidden
    Write-Output "Worker started (detached). Logs may be written to $log if run_worker is configured to do so."
}
