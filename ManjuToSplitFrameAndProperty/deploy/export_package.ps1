# Export ManjuToSplitFrameAndProperty as a portable package.
# Run this on the SOURCE machine.
# Usage: powershell -ExecutionPolicy Bypass -File .\deploy\export_package.ps1
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$PackageDir = "D:\AIGC\ManjuToSplitFrameAndProperty_package"
$ZipPath    = "D:\AIGC\ManjuToSplitFrameAndProperty_package.zip"

Write-Host "== Exporting project: $Root =="

if (Test-Path $PackageDir) { Remove-Item $PackageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

# Files/dirs to ship (source code + config + docs + samples; NO .venv, NO output)
$Include = @('pipeline','tools','docs','schemas','samples','workflows','comfyui','deploy',
             'config.yaml','README.md','requirements.txt','run_all.py')
foreach ($item in $Include) {
    $src = Join-Path $Root $item
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $PackageDir -Recurse -Force
        Write-Host "  [OK] $item"
    } else {
        Write-Host "  [SKIP] $item (not found)"
    }
}

# Strip caches
Get-ChildItem $PackageDir -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $PackageDir -Recurse -File -Filter '*.pyc' | Remove-Item -Force -ErrorAction SilentlyContinue
Write-Host "Caches stripped."

# Zip it
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $PackageDir '*') -DestinationPath $ZipPath -CompressionLevel Optimal
$mb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
Write-Host "== Package ready =="
Write-Host "  ZIP  : $ZipPath"
Write-Host "  SIZE : $mb MB"
Write-Host "  Copy this zip to the target machine, extract, then run deploy\setup_new_machine.ps1 there."
