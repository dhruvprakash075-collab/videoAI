# launch_tui.ps1 - Opens the Studio TUI in a new Windows Terminal window
# Run this from anywhere: powershell -File c:\Video.AI\launch_tui.ps1

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot "venv\Scripts\python.exe"
$tui = Join-Path $projectRoot "studio_tui.py"

if (Get-Command wt -ErrorAction SilentlyContinue) {
    # Windows Terminal available - open in new tab
    wt --title "Video.AI Studio" powershell -NoExit -Command "cd '$projectRoot'; & '$python' '$tui'"
} else {
    # Fallback: open in a new PowerShell window
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$projectRoot'; & '$python' '$tui'"
}
