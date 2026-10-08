# -*- coding: utf-8 -*-
"""
视频资源替换 API
================
将 ManjuToSplitFrameAndProperty（漫剧抽帧与属性管线）接入平台：

  上传参考视频 → 后台运行管线（镜头切分/ASR/资产抽取/H3提示词导出）
  → 浏览分镜、关键帧、角色/场景资产、minimax_h3 提示词
  → 替换资产图（原创形象）→ 重新导出 H3 提示词 → 打包下载

实现说明：
- 管线以子进程方式调用工程自带 .venv（隔离依赖，不污染后端环境）
- 产物目录：<MANJU_ROOT>/output/<视频名stem>/
- 所有相对产物路径均相对 output_dir，文件服务带目录穿越防护
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime

from app.core.database import SessionLocal
from app.models.video_asset_history import VideoAssetHistory
from app.services.background_workers import worker_manager
from app.services.video_asset_video_service import run_generate_new_video, NEW_VIDEO_DIR

router = APIRouter()

# ── 管线工程常量 ──
MANJU_ROOT = Path(r"F:\Develop\NewAIProductionWorkflow\ManjuToSplitFrameAndProperty")
MANJU_PYTHON = MANJU_ROOT / ".venv" / "Scripts" / "python.exe"
MANJU_INPUT_DIR = MANJU_ROOT / "input"
MANJU_OUTPUT_DIR = MANJU_ROOT / "output"

# 下载包排除的大目录（frames 抽帧库 / audio 音轨，占空间且非核心交付）
ZIP_EXCLUDE_DIRS = {"frames", "audio"}


def _safe_stem(filename: str) -> str:
    """产物目录名：取文件名主干，过滤非法字符"""
    stem = Path(filename).stem or "video"
    return re.sub(r'[\\/:*?"<>|]', "_", stem).strip() or "video"


def _read_json_or_none(path: Path):
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_job(job_id: str):
    db = SessionLocal()
    try:
        job = db.query(VideoAssetHistory).filter(VideoAssetHistory.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job
    finally:
        db.close()


def _output_dir_of(job) -> Path:
    if not job.output_dir:
        raise HTTPException(status_code=404, detail="任务尚无产物目录")
    return Path(job.output_dir)


def _safe_output_path(job, rel: str) -> Path:
    """拼接产物路径并防止目录穿越"""
    base = _output_dir_of(job).resolve()
    p = (base / rel).resolve()
    if not str(p).startswith(str(base)):
        raise HTTPException(status_code=403, detail="路径越界")
    return p


def _summarize(output_dir: Path) -> dict:
    """读取产物 JSON，汇总关键信息"""
    shots = _read_json_or_none(output_dir / "shots.json") or {}
    assets = _read_json_or_none(output_dir / "assets.json") or {}
    storyboard = _read_json_or_none(output_dir / "storyboard.json") or {}
    info = _read_json_or_none(output_dir / "video_info.json") or {}
    return {
        "duration": shots.get("duration", info.get("duration", 0)),
        "shot_count": len(shots.get("shots", [])),
        "character_count": len(assets.get("characters", [])),
        "scene_count": len(assets.get("scenes", [])),
        "prop_count": len(assets.get("props", [])),
        "dialogue_count": sum(len(s.get("dialogue", [])) for s in storyboard.get("shots", [])) if isinstance(storyboard, dict) else 0,
        "has_prompts": (output_dir / "minimax_h3" / "prompts.md").exists(),
        "has_prompt_pack": (output_dir / "prompt_pack.md").exists(),
    }


# ═══════════════════════════ 后台管线任务 ═══════════════════════════

def _run_pipeline_sync(video_path: str, output_dir: Path):
    """同步运行管线（在线程池中执行）"""
    cmd = [
        str(MANJU_PYTHON),
        "run_all.py",
        video_path,
        "--asr-model", "tiny",
        "--platform", "minimax_h3",
    ]
    env = dict(os.environ)
    proc = subprocess.run(
        cmd, cwd=str(MANJU_ROOT), env=env,
        capture_output=True, text=True, timeout=7200,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:]


def _swap_asset_sync(video_path: str, output_dir: Path, asset_type: str, asset_id: str, new_image: Path):
    """替换资产图：覆盖 assets.json 中该资产的第一张参考图，并重跑阶段4"""
    assets_path = output_dir / "assets.json"
    assets = _read_json_or_none(assets_path)
    if assets is None:
        raise HTTPException(status_code=404, detail="未找到 assets.json，请先完成分析")
    ref_images = None
    if asset_type == "character":
        for c in assets.get("characters", []):
            if c.get("id") == asset_id:
                ref_images = c.get("ref_images", [])
                break
    elif asset_type == "scene":
        for s in assets.get("scenes", []):
            if s.get("id") == asset_id:
                ref_images = s.get("ref_images", [])
                break
    else:
        raise HTTPException(status_code=400, detail="asset_type 仅支持 character / scene")
    if not ref_images:
        raise HTTPException(status_code=404, detail=f"未找到资产 {asset_id}")

    # 覆盖第一张参考图（备份原文件一次）
    target = _safe_output_path_path(output_dir, ref_images[0])
    bak = target.with_suffix(target.suffix + ".orig.bak")
    if not bak.exists():
        try:
            shutil.copy2(target, bak)
        except Exception:
            pass
    shutil.copy2(new_image, target)

    # 重跑阶段4（--skip 1,2,3）→ 重新导出提示词/payload（video 参数仅占位，stage4 不读视频）
    cmd = [
        str(MANJU_PYTHON),
        "run_all.py",
        video_path,
        "--skip", "1,2,3",
        "--platform", "minimax_h3",
    ]
    proc = subprocess.run(
        cmd, cwd=str(MANJU_ROOT),
        capture_output=True, text=True, timeout=1800,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout[-1500:], proc.stderr[-1500:]


def _safe_output_path_path(output_dir: Path, rel: str) -> Path:
    """（辅助）拼接路径防穿越"""
    base = output_dir.resolve()
    p = (base / rel).resolve()
    if not str(p).startswith(str(base)):
        raise HTTPException(status_code=403, detail="路径越界")
    return p


# ═══════════════════════════ 端点 ═══════════════════════════

@router.post("/analyze")
async def analyze_video(file: UploadFile = File(...)):
    """
    上传视频并启动漫剧逆向分析（后台运行）
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    ext = Path(file.filename).suffix.lower()
    if ext not in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv"):
        raise HTTPException(status_code=400, detail=f"不支持的视频格式: {ext}，支持 mp4/mov/mkv/avi/webm/flv")

    MANJU_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    MANJU_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 保存上传视频（保留原文件名，产物目录=文件主干）
    stem = _safe_stem(file.filename)
    video_path = MANJU_INPUT_DIR / f"{stem}{ext}"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="视频内容为空")
    with open(video_path, "wb") as f:
        f.write(content)

    output_dir = MANJU_OUTPUT_DIR / stem
    output_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        job = VideoAssetHistory(
            video_name=file.filename,
            status="running",
            stage="提交管线：镜头切分 → 内容结构化 → 资产抽取 → H3提示词导出",
            video_path=str(video_path),
            output_dir=str(output_dir),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
    finally:
        db.close()

    # 后台执行（不阻塞响应）
    asyncio.get_event_loop().create_task(_run_job(job_id, str(video_path), str(output_dir)))

    return {"success": True, "job_id": job_id, "message": "分析任务已提交，后台运行中"}


async def _run_job(job_id: str, video_path: str, output_dir: str):
    loop = asyncio.get_event_loop()
    rc = 0
    stdout = stderr = ""
    try:
        rc, stdout, stderr = await loop.run_in_executor(
            None, _run_pipeline_sync, video_path, Path(output_dir)
        )
    except Exception as exc:
        rc = -1
        stderr = str(exc)

    db = SessionLocal()
    try:
        job = db.query(VideoAssetHistory).filter(VideoAssetHistory.id == job_id).first()
        if not job:
            return
        if rc == 0:
            job.status = "success"
            job.stage = "分析完成"
            try:
                job.summary_json = json.dumps(_summarize(Path(output_dir)), ensure_ascii=False)
            except Exception:
                job.summary_json = "{}"
        else:
            job.status = "failed"
            job.stage = "管线失败"
            job.error = (stderr or stdout or "未知错误")[-3000:]
        db.commit()
    finally:
        db.close()


@router.get("/jobs")
async def list_jobs():
    """任务列表（新→旧）"""
    db = SessionLocal()
    try:
        jobs = db.query(VideoAssetHistory).order_by(VideoAssetHistory.created_at.desc()).all()
        result = []
        for j in jobs:
            summary = {}
            try:
                summary = json.loads(j.summary_json or "{}")
            except Exception:
                pass
            result.append({
                "id": j.id,
                "video_name": j.video_name,
                "status": j.status,
                "stage": j.stage,
                "error": j.error,
                "video_status": j.video_status or "idle",
                "summary": summary,
                "created_at": j.created_at.strftime("%Y-%m-%d %H:%M:%S") if j.created_at else None,
                "updated_at": j.updated_at.strftime("%Y-%m-%d %H:%M:%S") if j.updated_at else None,
            })
        return {"success": True, "data": result}
    finally:
        db.close()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """任务详情：产物 JSON 全量 + 文件树"""
    job = _load_job(job_id)
    output_dir = _output_dir_of(job)

    # 产物文件树（过滤大目录，方便前端浏览）
    file_tree = []
    if output_dir.exists():
        for root, dirs, files in os.walk(output_dir):
            dirs[:] = [d for d in dirs if d not in ZIP_EXCLUDE_DIRS]
            rel_root = Path(root).relative_to(output_dir)
            for fn in sorted(files):
                rel = str(rel_root / fn) if str(rel_root) != "." else fn
                file_tree.append({
                    "path": rel.replace("\\", "/"),
                    "size": os.path.getsize(os.path.join(root, fn)),
                })

    summary = {}
    try:
        summary = json.loads(job.summary_json or "{}")
    except Exception:
        pass

    video_summary = {}
    try:
        video_summary = json.loads(job.video_summary_json or "{}")
    except Exception:
        pass

    return {
        "success": True,
        "data": {
            "id": job.id,
            "video_name": job.video_name,
            "status": job.status,
            "stage": job.stage,
            "error": job.error,
            "summary": summary,
            "video": {
                "status": job.video_status or "idle",
                "stage": job.video_stage,
                "error": job.video_error,
                "summary": video_summary,
            },
            "created_at": job.created_at.strftime("%Y-%m-%d %H:%M:%S") if job.created_at else None,
            "updated_at": job.updated_at.strftime("%Y-%m-%d %H:%M:%S") if job.updated_at else None,
            "shots": _read_json_or_none(output_dir / "shots.json"),
            "storyboard": _read_json_or_none(output_dir / "storyboard.json"),
            "assets": _read_json_or_none(output_dir / "assets.json"),
            "video_info": _read_json_or_none(output_dir / "video_info.json"),
            "file_tree": file_tree,
        },
    }


@router.get("/jobs/{job_id}/files/{path:path}")
async def get_job_file(job_id: str, path: str):
    """产物静态文件（关键帧/资产图/提示词/JSON）"""
    job = _load_job(job_id)
    fp = _safe_output_path(job, path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    media_type = "application/octet-stream"
    suffix = fp.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        media_type = "image/jpeg"
    elif suffix == ".png":
        media_type = "image/png"
    elif suffix == ".webp":
        media_type = "image/webp"
    elif suffix == ".md":
        media_type = "text/markdown; charset=utf-8"
    elif suffix == ".json":
        media_type = "application/json; charset=utf-8"
    elif suffix == ".srt":
        media_type = "text/plain; charset=utf-8"
    return FileResponse(path=str(fp), media_type=media_type, filename=fp.name)


@router.get("/jobs/{job_id}/download")
async def download_job(job_id: str):
    """打包产物（关键帧/资产/JSON/提示词）下载 zip"""
    job = _load_job(job_id)
    output_dir = _output_dir_of(job)
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="产物目录不存在")

    fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="video_asset_")
    os.close(fd)
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(output_dir):
            dirs[:] = [d for d in dirs if d not in ZIP_EXCLUDE_DIRS]
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, output_dir)
                zf.write(full, rel)

    safe_name = _safe_stem(job.video_name)
    return FileResponse(
        path=tmp_path,
        media_type="application/zip",
        filename=f"{safe_name}_分析产物.zip",
    )


@router.post("/jobs/{job_id}/swap-asset")
async def swap_asset(
    job_id: str,
    asset_type: str = Form(...),
    asset_id: str = Form(...),
    file: UploadFile = File(...),
):
    """
    替换资产图（原创形象）→ 自动重跑阶段4 → 重新导出 H3 提示词

    asset_type: character / scene
    asset_id:   assets.json 中的 id（如 c0 / s3）
    """
    if asset_type not in ("character", "scene"):
        raise HTTPException(status_code=400, detail="asset_type 仅支持 character / scene")
    if file.content_type not in ("image/png", "image/jpeg", "image/jpg", "image/webp"):
        raise HTTPException(status_code=400, detail="仅支持 PNG/JPG/WEBP 图片")

    job = _load_job(job_id)
    if job.status != "success":
        raise HTTPException(status_code=400, detail="任务尚未完成分析，不能替换资产")

    output_dir = _output_dir_of(job)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="图片内容为空")

    ext = Path(file.filename).suffix.lower() if file.filename else ".jpg"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".jpg"

    # 临时文件 → 替换
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        loop = asyncio.get_event_loop()
        rc, stdout, stderr = await loop.run_in_executor(
            None, _swap_asset_sync, job.video_path, output_dir, asset_type, asset_id, Path(tmp_path)
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    # 刷新摘要
    db = SessionLocal()
    try:
        job = db.query(VideoAssetHistory).filter(VideoAssetHistory.id == job_id).first()
        if rc == 0:
            job.summary_json = json.dumps(_summarize(output_dir), ensure_ascii=False)
            job.stage = "资产替换完成（提示词已重新导出）"
        else:
            job.error = (stderr or stdout or "未知错误")[-2000:]
        db.commit()
    finally:
        db.close()

    if rc != 0:
        raise HTTPException(status_code=500, detail=f"提示词重新导出失败: {stderr[-800:]}")

    return {
        "success": True,
        "message": f"资产 {asset_id} 已替换，H3 提示词已重新导出",
        "prompts_url": f"/api/video-asset/jobs/{job_id}/files/minimax_h3/prompts.md",
    }


# ═══════════════════════════ 生成新视频 ═══════════════════════════

@router.post("/jobs/{job_id}/generate-video")
async def generate_video(job_id: str):
    """
    生成新视频（替换资产后）：逐镜 H3 提示词 + 资产参考图 → ComfyUI H3 视频工作流
    → 全部镜头串行生成 → ffmpeg 合并成片（output/<stem>/new_video/final_video.mp4）

    主动点击触发；后台 worker 串行执行，进度写入 job.video_summary_json。
    """
    job = _load_job(job_id)
    if job.status != "success":
        raise HTTPException(status_code=400, detail="任务尚未完成分析，不能生成视频")
    if (job.video_status or "idle") == "running":
        raise HTTPException(status_code=400, detail="视频生成已在进行中，请勿重复提交")

    output_dir = _output_dir_of(job)
    if not (output_dir / "minimax_h3" / "payloads").exists() and not (output_dir / "minimax_h3" / "prompts.md").exists():
        raise HTTPException(status_code=400, detail="缺少逐镜提示词（minimax_h3 产物），请先完成分析")

    # 重置视频生成状态（注意：commit 后 ORM 实例属性过期，Session 关闭后再访问
    # 会触发 DetachedInstanceError，因此 video_path/output_dir 必须在 commit 前取值）
    db = SessionLocal()
    try:
        job = db.query(VideoAssetHistory).filter(VideoAssetHistory.id == job_id).first()
        job.video_status = "running"
        job.video_stage = "任务已提交，等待执行…"
        job.video_error = None
        job.video_summary_json = None
        _video_path = str(job.video_path)
        _output_dir = str(job.output_dir)
        db.commit()
    finally:
        db.close()

    # 后台串行执行（独立 worker，避免与小说/武术指导视频生成并发抢显存）
    worker_manager.worker("video_asset_video").enqueue(
        lambda: run_generate_new_video(job_id, _video_path, _output_dir)
    )

    return {
        "success": True,
        "message": "视频生成已提交，逐镜串行执行中",
    }


@router.get("/jobs/{job_id}/video")
async def get_job_video(job_id: str):
    """视频生成状态：逐镜进度 + 合并结果 + new_video 产物文件树"""
    job = _load_job(job_id)
    output_dir = _output_dir_of(job)

    video_summary = {}
    try:
        video_summary = json.loads(job.video_summary_json or "{}")
    except Exception:
        pass

    # new_video 产物文件树（逐镜视频 + 合并成片）
    video_files = []
    new_video_dir = output_dir / NEW_VIDEO_DIR
    if new_video_dir.exists():
        for root, dirs, files in os.walk(new_video_dir):
            rel_root = Path(root).relative_to(output_dir)
            for fn in sorted(files):
                rel = str(rel_root / fn) if str(rel_root) != "." else fn
                video_files.append({
                    "path": rel.replace("\\", "/"),
                    "size": os.path.getsize(os.path.join(root, fn)),
                })

    return {
        "success": True,
        "data": {
            "id": job.id,
            "video_status": job.video_status or "idle",
            "video_stage": job.video_stage,
            "video_error": job.video_error,
            "summary": video_summary,
            "files": video_files,
        },
    }
