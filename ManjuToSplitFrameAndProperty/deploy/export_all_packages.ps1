# Export ALL AIGC projects + docs into one migration package.
# Run on the SOURCE machine.
# Usage: powershell -ExecutionPolicy Bypass -File .\deploy\export_all_packages.ps1
# (Run from ManjuToSplitFrameAndProperty\deploy, or adjust $RootDev below)
$ErrorActionPreference = 'Stop'

$RootDev = "D:\AIGC\develop\New_AI_Production_Workflow"
$StageDir = "D:\AIGC\AIGC_migration_stage"
$ZipPath  = "D:\AIGC\AIGC_New_AI_Production_Workflow_migration.zip"

Write-Host "== Exporting all AIGC projects =="

if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

# robocopy: copy project tree excluding env / build / cache / logs / debug assets.
# exit codes 0-7 are success for robocopy.
function Copy-Tree($src, $dst) {
    robocopy $src $dst /E /NFL /NDL /NJH /NJS /R:1 /W:1 `
        /XD venv .venv node_modules dist debug __pycache__ .git build logs `
        /XF *.log *.pyc *.pyo
    $code = $LASTEXITCODE
    if ($code -ge 8) { throw "robocopy failed for $src (code $code)" }
    Write-Host "  [OK] $src -> $dst"
}

# 1. ManjuToSplitFrameAndProperty (already has own package; re-ship with docs)
Copy-Tree "$RootDev\ManjuToSplitFrameAndProperty" "$StageDir\ManjuToSplitFrameAndProperty"

# 2. AI-NovelFlow (backend + frontend + gpu monitor)
Copy-Tree "$RootDev\AI-NovelFlow" "$StageDir\AI-NovelFlow"

# 3. ComfyUI-H3-Prompt-Builder
Copy-Tree "$RootDev\ComfyUI-H3-Prompt-Builder" "$StageDir\ComfyUI-H3-Prompt-Builder"

# 4. docs (development documents)
New-Item -ItemType Directory -Force -Path "$StageDir\docs" | Out-Null
Copy-Item "$RootDev\docs\*" "$StageDir\docs\" -Recurse -Force
Write-Host "  [OK] docs"

# Strip caches again (robocopy already excluded, safety pass)
Get-ChildItem $StageDir -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $StageDir -Recurse -File -Filter '*.pyc' | Remove-Item -Force -ErrorAction SilentlyContinue

# Zip everything
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path "$StageDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal
$mb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
Write-Host "== Package ready =="
Write-Host "  ZIP  : $ZipPath"
Write-Host "  SIZE : $mb MB"
Write-Host "  Copy to target machine, extract, then follow 整体移植方案.md per project."
