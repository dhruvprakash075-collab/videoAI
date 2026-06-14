<#
.SYNOPSIS
    Setup IndicF5 TTS environment for Video.AI.

.DESCRIPTION
    Creates a separate Python 3.10 environment for IndicF5 (ai4bharat/IndicF5),
    installs dependencies, and verifies HuggingFace access. This keeps IndicF5
    isolated from the main Video.AI Python environment to avoid dependency conflicts.

.EXAMPLE
    .\setup_indicf5.ps1

.NOTES
    Prerequisites:
    - Conda must be installed
    - HuggingFace account with access to ai4bharat/IndicF5 model
    - Accept terms at https://huggingface.co/ai4bharat/IndicF5

    Output:
    - indicf5_env/ - Python 3.10 environment with IndicF5
    - hf_cache/indicf5/ - Model cache directory
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  IndicF5 TTS Setup for Video.AI" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# Check if conda is available
$condaCmd = Get-Command conda -ErrorAction SilentlyContinue
if (-not $condaCmd) {
    Write-Host "[ERROR] Conda not found. Please install Miniconda or Anaconda." -ForegroundColor Red
    exit 1
}

# Use indicf5_env to match config.yaml default path
$envName = "indicf5_env"
$pythonVersion = "3.10"
$modelId = "ai4bharat/IndicF5"

Write-Host "[1/6] Checking IndicF5 conda environment..." -ForegroundColor Yellow
$envExists = conda env list 2>$null | Select-String "^$envName\s"

if ($envExists) {
    Write-Host "  Environment '$envName' already exists. Removing for clean reinstall..." -ForegroundColor Yellow
    conda env remove -n $envName -y
}

Write-Host "[2/6] Creating Python $pythonVersion environment..." -ForegroundColor Yellow
Write-Host "  Running: conda create -n $envName python=$pythonVersion -y" -ForegroundColor Gray
conda create -n $envName python=$pythonVersion -y

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to create conda environment" -ForegroundColor Red
    exit 1
}

Write-Host "[3/6] Installing IndicF5 and dependencies..." -ForegroundColor Yellow
$envPython = "$envName\Scripts\python.exe"

# First, upgrade pip and install basic dependencies
& "$envPython" -m pip install --upgrade pip setuptools wheel

# Install core dependencies
Write-Host "  Installing: transformers soundfile numpy..." -ForegroundColor Gray
& "$envPython" -m pip install transformers soundfile numpy torch torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install IndicF5 from GitHub
Write-Host "  Installing: git+https://github.com/ai4bharat/IndicF5.git..." -ForegroundColor Gray
& "$envPython" -m pip install git+https://github.com/ai4bharat/IndicF5.git

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to install IndicF5" -ForegroundColor Red
    Write-Host "  Try manual installation:" -ForegroundColor Yellow
    Write-Host "    conda activate $envName" -ForegroundColor Gray
    Write-Host "    pip install git+https://github.com/ai4bharat/IndicF5.git" -ForegroundColor Gray
    exit 1
}

Write-Host "[4/6] Creating cache directories..." -ForegroundColor Yellow
$cacheDir = Join-Path $RepoRoot "hf_cache\indicf5"
if (-not (Test-Path $cacheDir)) {
    New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
    Write-Host "  Created: $cacheDir" -ForegroundColor Gray
}

Write-Host "[5/6] Verifying HuggingFace model access..." -ForegroundColor Yellow
Write-Host "  Attempting to load ai4bharat/IndicF5 (this may download ~2GB)..." -ForegroundColor Gray

# Create test script that actually tries to load the model
$testScript = @"
import os
import sys
os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
os.environ['TRANSFORMERS_VERBOSITY'] = 'error'

try:
    from transformers import AutoModel
    print('Loading IndicF5 model...')
    model = AutoModel.from_pretrained('$modelId', trust_remote_code=True)
    print(f'Model loaded successfully: {type(model).__name__}')
    sys.exit(0)
except Exception as e:
    print(f'Model load failed: {e}')
    sys.exit(1)
"@

$testScript | Out-File -FilePath "$RepoRoot\test_indicf5_load.py" -Encoding UTF8

$loadResult = & "$envPython" "$RepoRoot\test_indicf5_load.py" 2>&1
Remove-Item "$RepoRoot\test_indicf5_load.py" -Force -ErrorAction SilentlyContinue

if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARNING] Model load failed. This usually means:" -ForegroundColor Yellow
    Write-Host "  - You haven't accepted the model terms at https://huggingface.co/$modelId" -ForegroundColor White
    Write-Host "  - You're not logged in (run: huggingface-cli login)" -ForegroundColor White
    Write-Host "  - Network issues" -ForegroundColor White
    Write-Host "  The environment is set up, but you must verify HF access before use." -ForegroundColor Yellow
} else {
    Write-Host "  $loadResult" -ForegroundColor Green
}

Write-Host "[6/6] Verifying worker script..." -ForegroundColor Yellow
$workerPath = Join-Path $RepoRoot "audio\indicf5_worker.py"
if (Test-Path $workerPath) {
    Write-Host "  Worker found: $workerPath" -ForegroundColor Gray
    & "$envPython" "$workerPath" --help 2>&1 | Select-Object -First 5
} else {
    Write-Host "[WARNING] Worker script not found at $workerPath" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  IndicF5 Setup Complete!" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Environment: $envName (Python $pythonVersion)" -ForegroundColor Cyan
Write-Host "Python path: $envPython" -ForegroundColor Cyan
Write-Host "Model ID: $modelId" -ForegroundColor Cyan
Write-Host "Cache Dir: $cacheDir" -ForegroundColor Cyan
Write-Host ""
Write-Host "IMPORTANT: You must still:" -ForegroundColor Yellow
Write-Host "  1. Accept terms at: https://huggingface.co/$modelId" -ForegroundColor White
Write-Host "  2. Run: huggingface-cli login (if using private model)" -ForegroundColor White
Write-Host ""
Write-Host "To enable IndicF5 in config.yaml:" -ForegroundColor Yellow
Write-Host "  tts:" -ForegroundColor Gray
Write-Host "    engine: 'indicf5'" -ForegroundColor Gray
Write-Host "    indicf5:" -ForegroundColor Gray
Write-Host "      enabled: true" -ForegroundColor Gray
Write-Host ""
Write-Host "To test manually:" -ForegroundColor Yellow
Write-Host "  $envPython audio\indicf5_worker.py --help" -ForegroundColor White
Write-Host ""