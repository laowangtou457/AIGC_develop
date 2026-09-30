"""
章节路由 - 章节 CRUD 和批量导入相关接口
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.novel import Chapter
from app.repositories import NovelRepository, ChapterRepository, CharacterRepository, SceneRepository, PropRepository
from app.api.deps import get_novel_repo, get_chapter_repo, get_character_repo, get_scene_repo, get_prop_repo
from app.utils.time_utils import format_datetime
from app.utils.text_utils import detect_encoding, parse_chapters_from_text

router = APIRouter()


# ==================== 章节 CRUD ====================

@router.get("/{novel_id}/chapters", response_model=dict)
async def list_chapters(
    novel_id: str, 
    novel_repo: NovelRepository = Depends(get_novel_repo), 
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """获取章节列表"""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    chapters = chapter_repo.list_by_novel(novel_id)
    return {
        "success": True,
        "data": [chapter_repo.to_response(c) for c in chapters]
    }


@router.post("/{novel_id}/chapters", response_model=dict)
async def create_chapter(
    novel_id: str, 
    data: dict, 
    db: Session = Depends(get_db), 
    novel_repo: NovelRepository = Depends(get_novel_repo), 
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """创建章节"""
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    chapter = Chapter(
        novel_id=novel_id,
        number=data.get("number", 1),
        title=data["title"],
        content=data.get("content", ""),
    )
    db.add(chapter)
    
    # 更新章节数
    novel.chapter_count = chapter_repo.count_by_novel(novel_id) + 1
    
    db.commit()
    db.refresh(chapter)
    
    return {
        "success": True,
        "data": chapter_repo.to_response(chapter)
    }


@router.get("/{novel_id}/chapters/{chapter_id}", response_model=dict)
async def get_chapter(
    novel_id: str, 
    chapter_id: str, 
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """获取章节详情"""
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    return {
        "success": True,
        "data": chapter_repo.to_detail_response(chapter)
    }


@router.get("/{novel_id}/chapters/{chapter_id}/final-video", response_model=dict)
async def get_chapter_final_video(
    novel_id: str,
    chapter_id: str,
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """获取章节最终视频（合并后）信息

    返回 get_final_chapter_video_info 的结构：
    finalVideo / chapterVideoUrl / chapterVideoDuration / chapterVideoSize
    chapterVideoShotCount / chapterVideoTaskId / chapterVideoCompletedAt；
    尚未合并时 data 为 None。
    """
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    info = chapter_repo.get_final_chapter_video_info(chapter)
    return {"success": True, "data": info}


@router.put("/{novel_id}/chapters/{chapter_id}", response_model=dict)
async def update_chapter(
    novel_id: str, 
    chapter_id: str, 
    data: dict, 
    db: Session = Depends(get_db),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """更新章节"""
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    if "title" in data:
        chapter.title = data["title"]
    if "content" in data:
        chapter.content = data["content"]
    if "parsedData" in data:
        chapter.parsed_data = data["parsedData"]
    
    db.commit()
    db.refresh(chapter)
    
    return {
        "success": True,
        "data": {
            **chapter_repo.to_response(chapter),
            "content": chapter.content,
            "parsedData": chapter.parsed_data,
            "updatedAt": format_datetime(chapter.updated_at),
        }
    }


@router.delete("/{novel_id}/chapters/{chapter_id}")
async def delete_chapter(
    novel_id: str, 
    chapter_id: str, 
    db: Session = Depends(get_db), 
    novel_repo: NovelRepository = Depends(get_novel_repo), 
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """删除章节"""
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    db.delete(chapter)
    
    # 更新小说章节数
    novel = novel_repo.get_by_id(novel_id)
    if novel:
        novel.chapter_count = chapter_repo.count_by_novel(novel_id) - 1
    
    db.commit()
    
    return {"success": True, "message": "删除成功"}


# ==================== 章节角色/场景解析 ====================

@router.post("/{novel_id}/chapters/{chapter_id}/parse-characters/", response_model=dict)
async def parse_chapter_characters(
    novel_id: str,
    chapter_id: str,
    is_incremental: bool = True,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    character_repo: CharacterRepository = Depends(get_character_repo)
):
    """解析单章节内容，提取角色信息（支持增量更新）"""
    from app.services.novel_service import NovelService
    
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    if not chapter.content:
        return {"success": False, "message": "章节内容为空"}
    
    service = NovelService(db)
    return await service.parse_characters(
        novel_id=novel_id,
        chapters=[chapter],
        start_chapter=chapter.number,
        end_chapter=chapter.number,
        is_incremental=is_incremental,
        character_repo=character_repo
    )


@router.post("/{novel_id}/chapters/{chapter_id}/parse-scenes/", response_model=dict)
async def parse_chapter_scenes(
    novel_id: str,
    chapter_id: str,
    is_incremental: bool = True,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    scene_repo: SceneRepository = Depends(get_scene_repo)
):
    """解析单章节内容，提取场景信息（支持增量更新）"""
    from app.services.novel_service import NovelService

    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)

    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    if not chapter.content:
        return {"success": False, "message": "章节内容为空"}

    service = NovelService(db)
    return await service.parse_scenes(
        novel_id=novel_id,
        chapter=chapter,
        is_incremental=is_incremental,
        scene_repo=scene_repo
    )


@router.post("/{novel_id}/chapters/{chapter_id}/parse-props/", response_model=dict)
async def parse_chapter_props(
    novel_id: str,
    chapter_id: str,
    is_incremental: bool = True,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    prop_repo: PropRepository = Depends(get_prop_repo)
):
    """解析单章节内容，提取道具信息（支持增量更新）"""
    from app.services.novel_service import NovelService

    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    chapter = chapter_repo.get_by_id(chapter_id, novel_id)

    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    if not chapter.content:
        return {"success": False, "message": "章节内容为空"}

    service = NovelService(db)
    return await service.parse_props(
        novel_id=novel_id,
        chapters=[chapter],
        start_chapter=chapter.number,
        end_chapter=chapter.number,
        is_incremental=is_incremental,
        prop_repo=prop_repo
    )


# ==================== 解析+生图聚合接口（章节编辑页三按钮） ====================

@router.post("/{novel_id}/chapters/{chapter_id}/parse-and-generate/", response_model=dict)
async def parse_and_generate_assets(
    novel_id: str,
    chapter_id: str,
    type: str = Query("characters", pattern="^(characters|scenes|props)$"),
    is_incremental: bool = True,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    character_repo: CharacterRepository = Depends(get_character_repo),
    scene_repo: SceneRepository = Depends(get_scene_repo),
    prop_repo: PropRepository = Depends(get_prop_repo),
):
    """解析章节内容提取角色/场景/道具 → 自动排队生成缺失图片 → 返回实体列表（含图片状态）。

    供章节编辑页「解析角色/解析场景/解析道具」按钮使用：
    一次调用完成 解析 + 生图任务排队 + 可展示的实体数据。
    """
    from app.services.novel_service import NovelService

    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    if not chapter.content:
        return {"success": False, "message": "章节内容为空"}

    service = NovelService(db)

    # ---- 1. 解析 ----
    if type == "characters":
        parse_result = await service.parse_characters(
            novel_id=novel_id, chapters=[chapter],
            start_chapter=chapter.number, end_chapter=chapter.number,
            is_incremental=is_incremental, character_repo=character_repo,
        )
        entity_type = "characters"
    elif type == "scenes":
        parse_result = await service.parse_scenes(
            novel_id=novel_id, chapter=chapter,
            is_incremental=is_incremental, scene_repo=scene_repo,
        )
        entity_type = "scenes"
    else:
        parse_result = await service.parse_props(
            novel_id=novel_id, chapters=[chapter],
            start_chapter=chapter.number, end_chapter=chapter.number,
            is_incremental=is_incremental, prop_repo=prop_repo,
        )
        entity_type = "props"

    if not parse_result.get("success"):
        return parse_result

    # ---- 2. 排队生成缺失图片（异步任务，ComfyUI 后台执行） ----
    queued = 0
    failed_items = []
    try:
        if entity_type == "characters":
            from app.services.character_service import CharacterService
            r = CharacterService(db).create_missing_character_portrait_tasks(novel_id)
            rdata = r.get("data") if isinstance(r, dict) else None
            queued = (rdata or {}).get("queuedCount", 0) if isinstance(rdata, dict) else 0
            failed_items = (rdata or {}).get("failedItems", []) if isinstance(rdata, dict) else []
        elif entity_type == "scenes":
            from app.services.scene_service import SceneService
            r = SceneService(db).create_missing_scene_image_tasks(novel_id)
            rdata = r.get("data") if isinstance(r, dict) else None
            queued = (rdata or {}).get("queuedCount", 0) if isinstance(rdata, dict) else 0
            failed_items = (rdata or {}).get("failedItems", []) if isinstance(rdata, dict) else []
        else:
            from app.services.prop_image_service import PropService
            r = PropService(db).create_missing_prop_image_tasks(novel_id)
            rdata = r.get("data") if isinstance(r, dict) else None
            queued = (rdata or {}).get("queuedCount", 0) if isinstance(rdata, dict) else 0
            failed_items = (rdata or {}).get("failedItems", []) if isinstance(rdata, dict) else []
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[ParseAndGenerate] 排队生图失败: {exc}")
        queued = 0
        failed_items = [{"message": str(exc)}]

    # ---- 3. 返回实体列表（含图片状态，供前端展示框使用） ----
    items = []
    if entity_type == "characters":
        for c in character_repo.list_by_novel(novel_id):
            items.append({
                "id": c.id, "name": c.name, "description": c.description,
                "appearance": c.appearance, "imageUrl": c.image_url,
                "generatingStatus": c.generating_status,
                "startChapter": c.start_chapter, "endChapter": c.end_chapter,
                "sourceRange": c.source_range,
                "updatedAt": format_datetime(c.updated_at),
            })
    elif entity_type == "scenes":
        for s in scene_repo.list_by_novel(novel_id):
            items.append({
                "id": s.id, "name": s.name, "description": s.description,
                "appearance": getattr(s, "appearance", None) or getattr(s, "setting_description", None),
                "imageUrl": s.image_url, "generatingStatus": s.generating_status,
                "startChapter": s.start_chapter, "endChapter": s.end_chapter,
                "sourceRange": s.source_range,
                "updatedAt": format_datetime(s.updated_at),
            })
    else:
        for p in prop_repo.list_by_novel(novel_id):
            items.append({
                "id": p.id, "name": p.name, "description": p.description,
                "appearance": getattr(p, "appearance", None),
                "imageUrl": p.image_url, "generatingStatus": p.generating_status,
                "startChapter": p.start_chapter, "endChapter": p.end_chapter,
                "sourceRange": p.source_range,
                "updatedAt": format_datetime(p.updated_at),
            })

    return {
        "success": True,
        "type": entity_type,
        "statistics": parse_result.get("statistics") or {},
        "queuedImageTasks": queued,
        "imageTaskFailures": failed_items,
        "message": parse_result.get("message") or "",
        "items": items,
    }


# ==================== 章节拆分 ====================

@router.post("/{novel_id}/chapters/{chapter_id}/split", response_model=dict)
async def split_chapter(
    novel_id: str, 
    chapter_id: str, 
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    character_repo: CharacterRepository = Depends(get_character_repo),
    scene_repo: SceneRepository = Depends(get_scene_repo),
    prop_repo: PropRepository = Depends(get_prop_repo)
):
    """使用小说配置的拆分提示词将章节拆分为分镜"""
    from app.services.novel_service import NovelService
    
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    
    # 获取当前小说的所有角色、场景和道具列表
    character_names = character_repo.get_names_by_novel(novel_id)
    scene_names = scene_repo.get_names_by_novel(novel_id)
    prop_names = prop_repo.get_names_by_novel(novel_id)
    
    service = NovelService(db)
    return await service.split_chapter(
        novel=novel,
        chapter=chapter,
        character_names=character_names,
        scene_names=scene_names,
        prop_names=prop_names
    )


# ==================== 批量导入 ====================

@router.post("/{novel_id}/chapters/batch-import/preview", response_model=dict)
async def batch_import_preview(
    novel_id: str,
    file: UploadFile = File(...),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """批量导入预览：解析 TXT 文件但不入库，返回章节列表及操作类型。"""
    # 校验文件扩展名
    if not file.filename or not file.filename.lower().endswith('.txt'):
        raise HTTPException(status_code=400, detail="仅支持 .txt 格式文件")

    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # 读取文件内容
    raw_bytes = await file.read()
    if not raw_bytes:
        return {
            "success": True,
            "data": {
                "chapters": [],
                "summary": {"total": 0, "new": 0, "replace": 0},
                "errors": [],
            }
        }

    encoding = detect_encoding(raw_bytes)
    text = raw_bytes.decode(encoding, errors='replace')

    # 解析章节
    chapters, errors = parse_chapters_from_text(text)

    # 获取已有章节号
    existing_chapters = chapter_repo.list_by_novel(novel_id)
    existing_numbers = {c.number for c in existing_chapters}

    # 计算 action 类型
    preview_chapters = []
    new_count = 0
    replace_count = 0
    for ch in chapters:
        action = "replace" if ch['number'] in existing_numbers else "new"
        if action == "new":
            new_count += 1
        else:
            replace_count += 1
        preview_chapters.append({
            "number": ch['number'],
            "title": ch['title'],
            "content_length": len(ch['content']),
            "action": action,
        })

    return {
        "success": True,
        "data": {
            "chapters": preview_chapters,
            "summary": {
                "total": len(preview_chapters),
                "new": new_count,
                "replace": replace_count,
            },
            "errors": errors,
        }
    }


@router.post("/{novel_id}/chapters/batch-import", response_model=dict)
async def batch_import_chapters(
    novel_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo)
):
    """批量导入执行：解析 TXT 文件并执行 bulk_upsert。"""
    # 校验文件扩展名
    if not file.filename or not file.filename.lower().endswith('.txt'):
        raise HTTPException(status_code=400, detail="仅支持 .txt 格式文件")

    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # 读取文件内容
    raw_bytes = await file.read()
    if not raw_bytes:
        return {
            "success": True,
            "data": {
                "total": 0,
                "created": 0,
                "updated": 0,
                "failed": 0,
                "errors": [],
                "chapters": [],
            },
            "message": "文件为空",
        }

    encoding = detect_encoding(raw_bytes)
    text = raw_bytes.decode(encoding, errors='replace')

    # 解析章节
    chapters, parse_errors = parse_chapters_from_text(text)

    # 完全无法解析章节
    if not chapters:
        return {
            "success": False,
            "message": "无法解析章节，请检查文件格式",
            "data": {"errors": parse_errors},
        }

    # 执行批量 upsert
    result = chapter_repo.bulk_upsert(novel_id, chapters)

    # 合并解析错误和 upsert 错误
    all_errors = parse_errors + result['errors']

    success_count = result['created'] + result['updated']
    failed_count = result['failed']

    # 构建消息
    if failed_count == 0:
        message = f"导入完成：成功 {success_count} 个"
    else:
        message = f"导入完成：成功 {success_count} 个，失败 {failed_count} 个"

    return {
        "success": True,
        "data": {
            "total": len(chapters),
            "created": result['created'],
            "updated": result['updated'],
            "failed": failed_count,
            "errors": all_errors,
            "chapters": result['chapters'],
        },
        "message": message,
    }
