#!/usr/bin/env pwsh

param(
    [string]$InputFile = "diagnostics/skylos_scan_results.json",
    [string]$OutputFile = "diagnostics/SKYLOS_ACTIONABLE_FIXES.md"
)

if (-not (Test-Path -Path $InputFile)) {
    Write-Error "Input file not found: $InputFile"
    exit 1
}

try {
    $issues = Get-Content -Path $InputFile -Raw | ConvertFrom-Json
    $actionable = @()

    foreach ($issue in $issues.findings) {
        if ($issue.severity -in @("CRITICAL", "HIGH", "MEDIUM") -and $issue.dead_code -eq $false) {
            $action = switch ($issue.category) {
                "security" { "Fix security vulnerability ($($issue.title))"; break }
                "dependency_vulnerability" { "Update vulnerable dependency: $($issue.title) >= $($issue.suggested_version)"; break }
                "dead_code" { "Remove unused code: $($issue.title) in $($issue.file):$($issue.line)"; break }
                "quality" { "Fix code quality issue: $($issue.title)"; break }
                default { "Review: $($issue.title)" }
            }
            
            $actionable += [PSCustomObject]@{
                Severity   = $issue.severity
                File       = $issue.file
                Line       = $issue.line
                Title      = $issue.title
                Action     = $action
            }
        }
    }

    $md = "# Skylos Actionable Fixes`n`n"
    $md += "| Severity | File | Line | Title | Action |`n"
    $md += "|----------|------|------|-------|--------|`n"
    foreach ($a in $actionable) {
        $md += "| $($a.Severity) | $($a.File) | $($a.Line) | $($a.Title) | $($a.Action) |`n"
    }
    $md | Out-File -FilePath $OutputFile -Encoding utf8
    Write-Host "Generated report: $OutputFile"
} catch {
    Write-Error "Failed to parse input file: $_"
    exit 1
}
