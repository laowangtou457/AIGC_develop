@echo off
chcp 65001 >nul
title AI-NovelFlow 一键启动
echo ========================================
echo   AI-NovelFlow 全家桶启动器
echo ========================================
echo.

echo [1/4] 启动 Ollama（模型常驻 30 分钟）...
set OLLAMA_MODELS=F:\Develop\VLM\models
set OLLAMA_KEEP_ALIVE=30m
set "PATH=%PATH%;C:\Users\Administrator.DESKTOP-HK5Q0TB\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin"
start "" "C:\Users\Administrator.DESKTOP-HK5Q0TB\AppData\Local\Programs\Ollama\ollama.exe" serve
timeout /t 6 /nobreak >nul

echo [2/4] 启动 ComfyUI (8188)...
echo 清理残留 ComfyUI 进程（避免端口冲突）...
powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'main\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 3 /nobreak >nul
start "ComfyUI" cmd /c "cd /d F:\Develop\ComfyUI && venv\Scripts\python main.py --reserve-vram 3"

echo [3/4] 启动 AI-NovelFlow 后端 (8000)...
start "NovelFlow-Backend" cmd /c "cd /d F:\Develop\NewAIProductionWorkflow\AI-NovelFlow\backend && venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

echo [4/4] 启动 AI-NovelFlow 前端 (5173)...
start "NovelFlow-Frontend" cmd /c "cd /d F:\Develop\NewAIProductionWorkflow\AI-NovelFlow\frontend\my-app && npm run dev"

echo.
echo 全部启动指令已发出，等待服务就绪...
timeout /t 30 /nobreak >nul

echo.
echo ======== 服务检查 ========
powershell -Command ^
  "$ok=$true; foreach($p in @(@{n='Ollama';u='http://127.0.0.1:11434/api/tags'},@{n='ComfyUI';u='http://127.0.0.1:8188'},@{n='后端';u='http://127.0.0.1:8000/api/health'},@{n='前端';u='http://127.0.0.1:5173'})){ try{$r=Invoke-WebRequest -Uri $p.u -UseBasicParsing -TimeoutSec 5; Write-Host ($p.n+': OK')}catch{Write-Host ($p.n+': 未就绪 - '+$p.u); $ok=$false} }; if($ok){Write-Host '`n全部服务就绪！前端地址: http://localhost:5173'}else{Write-Host '`n部分服务未就绪，等 30 秒后刷新本窗口或查看各窗口日志'}"

echo.
pause
