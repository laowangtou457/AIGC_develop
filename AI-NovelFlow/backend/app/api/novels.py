"""
小说路由 - 小说 CRUD 和解析相关接口
"""
import json
import zipfile
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.models.novel import Novel
from app.schemas.novel import NovelCreate
from app.repositories import NovelRepository, ChapterRepository, CharacterRepository, PromptTemplateRepository
from app.services.novel_service import NovelService
from app.api.deps import get_novel_repo, get_chapter_repo, get_character_repo
from app.utils.time_utils import format_datetime

router = APIRouter()


PROMPT_TEMPLATE_EXPORT_FIELDS = [
    {"api_key": "stylePromptTemplateId", "attr": "style_prompt_template_id", "label": "风格提示词", "type": "style"},
    {"api_key": "characterParsePromptTemplateId", "attr": "character_parse_prompt_template_id", "label": "角色解析提示词", "type": "character_parse"},
    {"api_key": "sceneParsePromptTemplateId", "attr": "scene_parse_prompt_template_id", "label": "场景解析提示词", "type": "scene_parse"},
    {"api_key": "propParsePromptTemplateId", "attr": "prop_parse_prompt_template_id", "label": "道具解析提示词", "type": "prop_parse"},
    {"api_key": "promptTemplateId", "attr": "prompt_template_id", "label": "角色生成提示词", "type": "character"},
    {"api_key": "scenePromptTemplateId", "attr": "scene_prompt_template_id", "label": "场景生成提示词", "type": "scene"},
    {"api_key": "propPromptTemplateId", "attr": "prop_prompt_template_id", "label": "道具生成提示词", "type": "prop"},
    {"api_key": "chapterSplitPromptTemplateId", "attr": "chapter_split_prompt_template_id", "label": "分镜拆分提示词模板", "type": "chapter_split"},
    {"api_key": "keyframeDescriptionPromptTemplateId", "attr": "keyframe_description_prompt_template_id", "label": "关键帧描述提示词模板", "type": "keyframe_description"},
    {"api_key": "shotImagePromptTemplateId", "attr": "shot_image_prompt_template_id", "label": "主分镜图提示词模板", "type": "shot_image_prompt"},
    {"api_key": "videoModeRecommenderPromptTemplateId", "attr": "video_mode_recommender_prompt_template_id", "label": "视频生成模式推荐提示词模板", "type": "video_mode_recommender"},
    {"api_key": "keyframePlannerPromptTemplateId", "attr": "keyframe_planner_prompt_template_id", "label": "关键帧时间轴规划提示词模板", "type": "keyframe_planner"},
    {"api_key": "keyframeImagePromptTemplateId", "attr": "keyframe_image_prompt_template_id", "label": "关键帧生图提示词模板", "type": "keyframe_image_prompt"},
    {"api_key": "keyframeTransitionPromptTemplateId", "attr": "keyframe_transition_prompt_template_id", "label": "关键帧过渡规划提示词模板", "type": "keyframe_transition"},
    {"api_key": "h3SingleFramePromptTemplateId", "attr": "h3_single_frame_prompt_template_id", "label": "H3 单帧视频提示词模板", "type": "h3_single_frame_prompt"},
    {"api_key": "h3FirstLastFramePromptTemplateId", "attr": "h3_first_last_frame_prompt_template_id", "label": "H3 首尾帧视频提示词模板", "type": "h3_first_last_frame_prompt"},
    {"api_key": "h3MultiKeyframePromptTemplateId", "attr": "h3_multi_keyframe_prompt_template_id", "label": "H3 多关键帧视频提示词模板", "type": "h3_multi_keyframe_prompt"},
]


class PromptTemplatesExportRequest(BaseModel):
    templateIds: dict = {}


def _safe_filename_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value or "")).strip("_") or "item"


# ==================== 小说 CRUD ====================

@router.get("/", response_model=dict)
async def list_novels(novel_repo: NovelRepository = Depends(get_novel_repo)):
    """获取小说列表"""
    result = novel_repo.list_with_cover()
    return {
        "success": True,
        "data": result
    }


@router.post("/", response_model=dict)
async def create_novel(novel: NovelCreate, db: Session = Depends(get_db)):
    """创建新小说"""
    from app.repositories import PromptTemplateRepository
    
    prompt_template_repo = PromptTemplateRepository(db)
    
    # 如果没有指定角色生成模板，使用默认系统模板
    prompt_template_id = novel.prompt_template_id
    if not prompt_template_id:
        default_template = prompt_template_repo.get_default_system_template("character")
        if default_template:
            prompt_template_id = default_template.id
    
    # 如果没有指定风格模板，使用默认系统模板
    style_prompt_template_id = novel.style_prompt_template_id
    if not style_prompt_template_id:
        default_style = prompt_template_repo.get_default_system_template("style")
        if default_style:
            style_prompt_template_id = default_style.id
    
    db_novel = Novel(
        title=novel.title,
        author=novel.author,
        description=novel.description,
        style_prompt_template_id=style_prompt_template_id,
        character_parse_prompt_template_id=novel.character_parse_prompt_template_id,
        scene_parse_prompt_template_id=novel.scene_parse_prompt_template_id,
        prop_parse_prompt_template_id=novel.prop_parse_prompt_template_id,
        prompt_template_id=prompt_template_id,
        scene_prompt_template_id=novel.scene_prompt_template_id,
        prop_prompt_template_id=novel.prop_prompt_template_id,
        chapter_split_prompt_template_id=novel.chapter_split_prompt_template_id,
        keyframe_description_prompt_template_id=novel.keyframe_description_prompt_template_id,
        shot_image_prompt_template_id=novel.shot_image_prompt_template_id,
        video_mode_recommender_prompt_template_id=novel.video_mode_recommender_prompt_template_id,
        keyframe_planner_prompt_template_id=novel.keyframe_planner_prompt_template_id,
        keyframe_image_prompt_template_id=novel.keyframe_image_prompt_template_id,
        keyframe_transition_prompt_template_id=novel.keyframe_transition_prompt_template_id,
        h3_single_frame_prompt_template_id=novel.h3_single_frame_prompt_template_id,
        h3_first_last_frame_prompt_template_id=novel.h3_first_last_frame_prompt_template_id,
        h3_multi_keyframe_prompt_template_id=novel.h3_multi_keyframe_prompt_template_id,
        aspect_ratio=novel.aspect_ratio or "16:9",
    )
    db.add(db_novel)
    db.commit()
    db.refresh(db_novel)
    return {
        "success": True,
        "data": {
            "id": db_novel.id,
            "title": db_novel.title,
            "author": db_novel.author,
            "description": db_novel.description,
            "cover": db_novel.cover,
            "status": db_novel.status,
            "chapterCount": db_novel.chapter_count,
            "stylePromptTemplateId": db_novel.style_prompt_template_id,
            "characterParsePromptTemplateId": db_novel.character_parse_prompt_template_id,
            "sceneParsePromptTemplateId": db_novel.scene_parse_prompt_template_id,
            "propParsePromptTemplateId": db_novel.prop_parse_prompt_template_id,
            "promptTemplateId": db_novel.prompt_template_id,
            "scenePromptTemplateId": db_novel.scene_prompt_template_id,
            "propPromptTemplateId": db_novel.prop_prompt_template_id,
            "chapterSplitPromptTemplateId": db_novel.chapter_split_prompt_template_id,
            "keyframeDescriptionPromptTemplateId": db_novel.keyframe_description_prompt_template_id,
            "shotImagePromptTemplateId": db_novel.shot_image_prompt_template_id,
            "videoModeRecommenderPromptTemplateId": db_novel.video_mode_recommender_prompt_template_id,
            "keyframePlannerPromptTemplateId": db_novel.keyframe_planner_prompt_template_id,
            "keyframeImagePromptTemplateId": db_novel.keyframe_image_prompt_template_id,
            "keyframeTransitionPromptTemplateId": db_novel.keyframe_transition_prompt_template_id,
            "h3SingleFramePromptTemplateId": db_novel.h3_single_frame_prompt_template_id,
            "h3FirstLastFramePromptTemplateId": db_novel.h3_first_last_frame_prompt_template_id,
            "h3MultiKeyframePromptTemplateId": db_novel.h3_multi_keyframe_prompt_template_id,
            "aspectRatio": db_novel.aspect_ratio or "16:9",
            "createdAt": format_datetime(db_novel.created_at),
        }
    }


@router.get("/{novel_id}", response_model=dict)
async def get_novel(novel_id: str, novel_repo: NovelRepository = Depends(get_novel_repo)):
    """获取小说详情"""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return {
        "success": True,
        "data": novel_repo.to_response(novel)
    }


@router.post("/{novel_id}/prompt-templates/export", response_model=None)
async def export_novel_prompt_templates(
    novel_id: str,
    request: PromptTemplatesExportRequest,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
):
    """按当前小说配置打包导出提示词模板。"""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    prompt_template_repo = PromptTemplateRepository(db)
    zip_buffer = BytesIO()
    manifest = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for index, field in enumerate(PROMPT_TEMPLATE_EXPORT_FIELDS, 1):
            template_id = request.templateIds.get(field["api_key"])
            if template_id is None:
                template_id = getattr(novel, field["attr"], None)
            template = prompt_template_repo.get_by_id(template_id) if template_id else None
            resolved_by = "configured"
            if not template:
                template = prompt_template_repo.get_default_system_template(field["type"])
                resolved_by = "default"
            if not template:
                manifest.append({
                    "field": field["api_key"],
                    "label": field["label"],
                    "type": field["type"],
                    "status": "missing",
                })
                continue

            filename = f"{index:02d}_{_safe_filename_part(field['label'])}_{_safe_filename_part(template.name)}.txt"
            content = "\n".join([
                f"字段: {field['label']}",
                f"字段Key: {field['api_key']}",
                f"模板ID: {template.id}",
                f"模板名称: {template.name}",
                f"模板类型: {template.type}",
                f"模板来源: {'系统模板' if template.is_system else '自定义模板'}",
                f"解析方式: {'使用默认模板' if resolved_by == 'default' else '使用当前配置模板'}",
                f"描述: {template.description or '-'}",
                "",
                "模板内容",
                "=" * 40,
                template.template or "",
                "",
            ])
            zip_file.writestr(filename, content)
            manifest.append({
                "field": field["api_key"],
                "label": field["label"],
                "type": field["type"],
                "template_id": template.id,
                "template_name": template.name,
                "is_system": template.is_system,
                "resolved_by": resolved_by,
                "filename": filename,
            })

        zip_file.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    zip_buffer.seek(0)
    filename = f"{novel.title or 'novel'}_prompt_templates.zip"
    encoded_filename = quote(filename)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=prompt_templates.zip; filename*=UTF-8''{encoded_filename}"},
    )


@router.put("/{novel_id}", response_model=dict)
async def update_novel(
    novel_id: str, 
    data: dict, 
    db: Session = Depends(get_db), 
    novel_repo: NovelRepository = Depends(get_novel_repo)
):
    """更新小说信息"""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    # 更新字段
    update_fields = {
        "title": "title",
        "author": "author", 
        "description": "description",
        "stylePromptTemplateId": "style_prompt_template_id",
        "characterParsePromptTemplateId": "character_parse_prompt_template_id",
        "sceneParsePromptTemplateId": "scene_parse_prompt_template_id",
        "propParsePromptTemplateId": "prop_parse_prompt_template_id",
        "promptTemplateId": "prompt_template_id",
        "scenePromptTemplateId": "scene_prompt_template_id",
        "propPromptTemplateId": "prop_prompt_template_id",
        "chapterSplitPromptTemplateId": "chapter_split_prompt_template_id",
        "keyframeDescriptionPromptTemplateId": "keyframe_description_prompt_template_id",
        "shotImagePromptTemplateId": "shot_image_prompt_template_id",
        "videoModeRecommenderPromptTemplateId": "video_mode_recommender_prompt_template_id",
        "keyframePlannerPromptTemplateId": "keyframe_planner_prompt_template_id",
        "keyframeImagePromptTemplateId": "keyframe_image_prompt_template_id",
        "keyframeTransitionPromptTemplateId": "keyframe_transition_prompt_template_id",
        "h3SingleFramePromptTemplateId": "h3_single_frame_prompt_template_id",
        "h3FirstLastFramePromptTemplateId": "h3_first_last_frame_prompt_template_id",
        "h3MultiKeyframePromptTemplateId": "h3_multi_keyframe_prompt_template_id",
        "aspectRatio": "aspect_ratio",
    }
    
    for api_field, db_field in update_fields.items():
        if api_field in data:
            setattr(novel, db_field, data[api_field])
    
    db.commit()
    db.refresh(novel)
    
    return {
        "success": True,
        "data": {
            **novel_repo.to_response(novel),
            "updatedAt": format_datetime(novel.updated_at),
        }
    }


@router.delete("/{novel_id}")
async def delete_novel(
    novel_id: str, 
    db: Session = Depends(get_db), 
    novel_repo: NovelRepository = Depends(get_novel_repo)
):
    """删除小说"""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    db.delete(novel)
    db.commit()
    
    return {"success": True, "message": "删除成功"}


# ==================== 小说解析 ====================

@router.post("/{novel_id}/parse-characters/", response_model=dict)
async def parse_characters(
    novel_id: str, 
    sync: bool = False,
    start_chapter: int = None,
    end_chapter: int = None,
    is_incremental: bool = False,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    character_repo: CharacterRepository = Depends(get_character_repo)
):
    """解析小说内容，自动提取角色信息（支持章节范围和增量更新）"""
    from app.services.novel_service import NovelService
    
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    # 获取指定章节范围的章节
    chapters = chapter_repo.get_by_range(novel_id, start_chapter, end_chapter)
    
    if not chapters:
        return {"success": False, "message": "指定章节范围内没有内容"}
    
    service = NovelService(db)
    return await service.parse_characters(
        novel_id=novel_id,
        chapters=chapters,
        start_chapter=start_chapter,
        end_chapter=end_chapter,
        is_incremental=is_incremental,
        character_repo=character_repo
    )


@router.post("/{novel_id}/parse-props/", response_model=dict)
async def parse_props(
    novel_id: str,
    sync: bool = False,
    start_chapter: int = None,
    end_chapter: int = None,
    is_incremental: bool = False,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """解析小说内容，自动提取道具信息（支持章节范围和增量更新）"""
    from app.services.novel_service import NovelService
    from app.repositories import PropRepository

    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # 获取指定章节范围的章节
    chapters = chapter_repo.get_by_range(novel_id, start_chapter, end_chapter)

    if not chapters:
        return {"success": False, "message": "指定章节范围内没有内容"}

    prop_repo = PropRepository(db)
    service = NovelService(db)
    return await service.parse_props(
        novel_id=novel_id,
        chapters=chapters,
        start_chapter=start_chapter,
        end_chapter=end_chapter,
        is_incremental=is_incremental,
        prop_repo=prop_repo
    )

# ==================== 全本解析后台任务（章节管理页「解析全本」用） ====================
import asyncio as _asyncio
import uuid as _uuid
import threading as _threading
from datetime import datetime as _datetime
from app.core.database import SessionLocal as _SessionLocal
from app.repositories import SceneRepository as _SceneRepository, PropRepository as _PropRepository

_PARSE_TASKS: dict = {}
_PARSE_TASKS_LOCK = _threading.Lock()


def _set_parse_task(task_id: str, **kw):
    with _PARSE_TASKS_LOCK:
        t = _PARSE_TASKS.setdefault(task_id, {})
        t.update(kw)


@router.get("/parse-tasks/{task_id}", response_model=dict)
async def get_parse_task(task_id: str):
    """轮询全本解析任务状态。"""
    with _PARSE_TASKS_LOCK:
        t = _PARSE_TASKS.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="解析任务不存在")
    return t


async def _run_novel_parse(task_id: str, novel_id: str, type_: str, is_incremental: bool):
    db = _SessionLocal()
    try:
        from app.services.novel_service import NovelService
        from app.services.character_service import CharacterService
        from app.services.scene_service import SceneService
        from app.services.prop_image_service import PropService

        service = NovelService(db)
        chapter_repo = ChapterRepository(db)
        chapters = chapter_repo.get_by_range(novel_id, None, None)
        if not chapters:
            _set_parse_task(task_id, status="failed", message="没有可解析的章节")
            return

        if type_ == "characters":
            parse_result = await service.parse_characters(
                novel_id, chapters, None, None, is_incremental, CharacterRepository(db))
        elif type_ == "scenes":
            parse_result = await service.parse_scenes_from_chapters(
                novel_id, chapters,
                mode="incremental" if is_incremental else "overwrite",
                scene_repo=_SceneRepository(db))
        else:
            parse_result = await service.parse_props(
                novel_id, chapters, None, None, is_incremental, _PropRepository(db))

        if not parse_result.get("success"):
            _set_parse_task(task_id, status="failed", message=parse_result.get("message"))
            return

        # 排队生成缺失图片
        queued = 0
        failed_items = []
        try:
            if type_ == "characters":
                r = CharacterService(db).create_missing_character_portrait_tasks(novel_id)
            elif type_ == "scenes":
                r = SceneService(db).create_missing_scene_image_tasks(novel_id)
            else:
                r = PropService(db).create_missing_prop_image_tasks(novel_id)
            rdata = r.get("data") if isinstance(r, dict) else None
            queued = (rdata or {}).get("queuedCount", 0) if isinstance(rdata, dict) else 0
            failed_items = (rdata or {}).get("failedItems", []) if isinstance(rdata, dict) else []
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[NovelParseAndGenerate] 排队生图失败: {exc}")
            failed_items = [{"message": str(exc)}]

        _set_parse_task(
            task_id, status="completed",
            message=parse_result.get("message", ""),
            statistics={
                "parse": parse_result.get("statistics") or {},
                "queued": queued,
                "failed": failed_items,
            },
            finished_at=format_datetime(_datetime.utcnow()),
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _set_parse_task(task_id, status="failed", message=str(exc))
    finally:
        db.close()


@router.post("/{novel_id}/parse-and-generate/", response_model=dict)
async def parse_and_generate_novel(
    novel_id: str,
    type: str = Query("characters", pattern="^(characters|scenes|props)$"),
    is_incremental: bool = True,
    novel_repo: NovelRepository = Depends(get_novel_repo),
):
    """解析全本小说角色/场景/道具 → 后台异步分块解析 → 排队生成缺失图片。
    立即返回 task_id，前端轮询 /api/novels/parse-tasks/{task_id} 获取进度。"""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    task_id = str(_uuid.uuid4())
    _set_parse_task(
        task_id, status="running",
        message="全本解析任务已启动", novel_id=novel_id, type=type,
        created_at=format_datetime(_datetime.utcnow()),
    )
    _asyncio.create_task(_run_novel_parse(task_id, novel_id, type, is_incremental))
    return {"success": True, "task_id": task_id, "message": "全本解析任务已启动"}
