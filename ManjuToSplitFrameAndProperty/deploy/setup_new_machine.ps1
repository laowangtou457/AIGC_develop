# Setup ManjuToSplitFrameAndProperty on the TARGET machine.
# Prereq: Python 3.12 already installed (python.org, check "Add to PATH").
# Usage: powershell -ExecutionPolicy Bypass -File .\deploy\setup_new_machine.ps1
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== Target: $Root =="

# 1. Python check
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "[ERROR] Python not found on PATH."
    Write-Host "Install Python 3.12 from https://www.python.org/downloads/ (check 'Add python.exe to PATH') then rerun."
    exit 1
}
python --version

# 2. Create venv (reuse if present)
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
} else {
    Write-Host "Existing .venv found, reusing."
}

# 3. Install dependencies
Write-Host "Installing dependencies (needs internet, ~2-3 min)..."
.\.venv\Scripts\python -m pip install --upgrade pip --quiet
.\.venv\Scripts\python -m pip install -r requirements.txt --quiet
Write-Host "Dependencies installed."

# 4. Optional: VLM (Ollama) check
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama) {
    Write-Host "[INFO] Ollama detected. If you want visual descriptions:"
    Write-Host "       ollama pull qwen2.5vl:3b"
} else {
    Write-Host "[INFO] Ollama NOT detected. Visual descriptions (VLM) will be skipped."
    Write-Host "       To enable: install Ollama + pull qwen2.5vl:3b, or set vlm_provider: "" in config.yaml."
}

# 5. Smoke test
Write-Host "Smoke test: parse sample_12s.mp4 (stage 1 only)..."
.\.venv\Scripts\python -m pipeline.stage1_parse "samples\sample_12s.mp4" --config config.yaml
Write-Host "== Setup done =="
Write-Host "Full run example:"
Write-Host "  .\.venv\Scripts\python run_all.py <your_video.mp4> --platform minimax_h3"
