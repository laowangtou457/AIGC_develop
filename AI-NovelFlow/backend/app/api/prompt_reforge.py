# -*- coding: utf-8 -*-
"""
提示词提取与重构 API
======================
主菜单新增功能【提示词提取与重构】：
  上传文件 或 直接输入 提示词/剧本/小说 → 按导演模型（H3 Ref2VA 六段式 / H3 I2V /
  武术指导 / 通用影视导演）提取剧本节拍 → 重构为【AI 工具可生成级】逐镜提示词集
  （MiniMax H3 / Seedance / Kling / Veo / 即梦），支持在线查看、复制与下载。

产物目录：<AI-NovelFlow>/backend/data/prompt_reforge/<task_id>/
"""
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from app.core.database import SessionLocal
from app.models.prompt_reforge_history import PromptReforgeHistory
from app.services.background_workers import worker_manager
from app.services.prompt_reforge_service import (
    run_prompt_reforge,
    DIRECTOR_MODELS,
    DEFAULT_DIRECTOR_MODEL,
    DEFAULT_PLATFORMS,
    PLATFORM_LABELS,
    REFORGE_DATA_DIR,
)

router = APIRouter()

MAX_FILE_BYTES = 2 * 1024 * 1024  # 上传文件上限 2MB（文本类）
TEXT_EXTS = {".txt", ".md", ".docx", ".doc", ".srt", ".json", ".csv"}


def _load_job(job_id: str):
    db = SessionLocal()
    try:
        job = db.query(PromptReforgeHistory).filter(PromptReforgeHistory.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job
    finally:
        db.close()


def _safe_output_path(job, rel: str) -> Path:
    base = Path(job.output_dir).resolve()
    p = (base / rel).resolve()
    if not str(p).startswith(str(base)):
        raise HTTPException(status_code=403, detail="路径越界")
    return p


@router.post("/tasks")
async def create_task(
    title: str = Form(""),
    input_type: str = Form("text"),
    director_model: str = Form(DEFAULT_DIRECTOR_MODEL),
    target_platforms: str = Form(""),
    input_text: str = Form(""),
    file: UploadFile = File(None),
):
    """
    新建提示词重构任务：上传文件（txt/md/docx/srt…）或直接粘贴文本。
    director_model: h3_ref2va / h3_i2v / martial_arts / general_cinematic
    target_platforms: 逗号分隔 minimax_h3,seedance,kling,veo,jimeng（空=全部）
    """
    # ── 输入校验 ──
    if director_model not in DIRECTOR_MODELS:
        raise HTTPException(status_code=400, detail=f"未知导演模型: {director_model}，可选 {list(DIRECTOR_MODELS)}")

    source_name = None
    raw_text = (input_text or "").strip()
    if file and file.filename:
        source_name = file.filename
        ext = Path(file.filename).suffix.lower()
        if ext not in TEXT_EXTS:
            raise HTTPException(status_code=400, detail=f"不支持的文本格式: {ext}，支持 txt/md/docx/doc/srt/json/csv")
        content = await file.read()
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail="文件超过 2MB 上限")
        if not content:
            raise HTTPException(status_code=400, detail="文件内容为空")
        try:
            raw_text = content.decode("utf-8").strip()
        except UnicodeDecodeError:
            # 尝试 GBK 回退（中文 Windows 常见）
            try:
                raw_text = content.decode("gbk").strip()
            except Exception:
                raise HTTPException(status_code=400, detail="无法识别文件编码（支持 UTF-8 / GBK）")
        input_type = "file"
    else:
        input_type = "text" if input_type != "file" else "text"

    if not raw_text:
        raise HTTPException(status_code=400, detail="请输入或上传需要重构的提示词/剧本/小说")

    if not title:
        first_line = raw_text.strip().splitlines()[0] if raw_text.strip().splitlines() else ""
        title = (first_line[:40] or source_name or "提示词重构任务")

    # 平台解析
    platforms = [p.strip() for p in target_platforms.replace("，", ",").split(",") if p.strip()]
    platforms = [p for p in platforms if p in PLATFORM_LABELS] or DEFAULT_PLATFORMS

    # ── 建任务落库（注意：commit 前取值，避免 detached 访问） ──
    db = SessionLocal()
    try:
        job = PromptReforgeHistory(
            title=title,
            input_type=input_type,
            source_name=source_name,
            input_text=raw_text,
            input_summary=raw_text[:800],
            director_model=director_model,
            target_platforms=json.dumps(platforms, ensure_ascii=False),
            status="running",
            stage="任务已提交，等待执行…",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        task_id = job.id
    finally:
        db.close()

    # 产物目录（模型默认 data/prompt_reforge/<task_id>）
    out_dir = REFORGE_DATA_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    _update_output_dir(task_id, str(out_dir))

    # ── 后台串行执行 ──
    _task_id = task_id
    _text = raw_text
    _director = director_model
    _platforms = platforms
    worker_manager.worker("prompt_reforge").enqueue(
        lambda: run_prompt_reforge(_task_id, _text, _director, _platforms)
    )

    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "message": f"重构任务已提交：{DIRECTOR_MODELS[director_model]['name']}",
        },
    }


def _update_output_dir(task_id: str, output_dir: str):
    db = SessionLocal()
    try:
        job = db.query(PromptReforgeHistory).filter(PromptReforgeHistory.id == task_id).first()
        if job:
            job.output_dir = output_dir
            db.commit()
    finally:
        db.close()


def _job_dict(job) -> dict:
    platforms = []
    try:
        platforms = json.loads(job.target_platforms or "[]")
    except Exception:
        pass
    report = {}
    try:
        report = json.loads(job.report_json or "{}")
    except Exception:
        pass
    return {
        "id": job.id,
        "title": job.title,
        "input_type": job.input_type,
        "source_name": job.source_name,
        "director_model": job.director_model,
        "target_platforms": platforms,
        "status": job.status,
        "stage": job.stage,
        "error": job.error,
        "input_summary": job.input_summary,
        "shot_count": report.get("shot_count"),
        "created_at": job.created_at.strftime("%Y-%m-%d %H:%M:%S") if job.created_at else None,
        "updated_at": job.updated_at.strftime("%Y-%m-%d %H:%M:%S") if job.updated_at else None,
    }


@router.get("/tasks")
async def list_tasks():
    """任务列表（新→旧）"""
    db = SessionLocal()
    try:
        jobs = db.query(PromptReforgeHistory).order_by(PromptReforgeHistory.created_at.desc()).all()
        return {"success": True, "data": [_job_dict(j) for j in jobs]}
    finally:
        db.close()


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """任务详情：输入/节拍/提示词集/产物文件树"""
    job = _load_job(task_id)
    report = {}
    try:
        report = json.loads(job.report_json or "{}")
    except Exception:
        pass

    file_tree = []
    if job.output_dir and Path(job.output_dir).exists():
        for root, dirs, files in os.walk(job.output_dir):
            dirs[:] = [d for d in dirs if d not in {"frames", "audio"}]
            rel_root = Path(root).relative_to(job.output_dir)
            for fn in sorted(files):
                rel = str(rel_root / fn) if str(rel_root) != "." else fn
                file_tree.append({
                    "path": rel.replace("\\", "/"),
                    "size": os.path.getsize(os.path.join(root, fn)),
                })

    return {
        "success": True,
        "data": {
            **_job_dict(job),
            "input_text": job.input_text,
            "output_md": job.output_md or "",
            "report": report,
            "file_tree": file_tree,
        },
    }


@router.get("/tasks/{task_id}/files/{path:path}")
async def get_task_file(task_id: str, path: str):
    """产物文件（prompts.md / payloads/*.json / report.json）"""
    job = _load_job(task_id)
    if not job.output_dir:
        raise HTTPException(status_code=404, detail="任务尚无产物目录")
    fp = _safe_output_path(job, path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    media_type = "application/octet-stream"
    suffix = fp.suffix.lower()
    if suffix == ".md":
        media_type = "text/markdown; charset=utf-8"
    elif suffix == ".json":
        media_type = "application/json; charset=utf-8"
    elif suffix == ".txt":
        media_type = "text/plain; charset=utf-8"
    return FileResponse(path=str(fp), media_type=media_type, filename=fp.name)


@router.get("/director-models")
async def list_director_models():
    """导演模型清单（前端下拉用）"""
    return {
        "success": True,
        "data": [
            {"key": key, "name": model["name"], "description": model["description"]}
            for key, model in DIRECTOR_MODELS.items()
        ],
    }
