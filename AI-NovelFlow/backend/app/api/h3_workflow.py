"""
H3 工作流路由 - 剧本→分镜剧本→逐镜 H3 提示词（集成 ComfyUI-H3-Prompt-Builder 能力）

替换 FXAI 原「章节拆分（split_chapter）→ 提示词生成」链路的 H3 版本：
- POST /{novel_id}/chapters/{chapter_id}/h3-split        剧本→文字分镜（创建 Shot 记录）
- POST /{novel_id}/chapters/{chapter_id}/h3-prompt-all   分镜→全部镜头 H3 提示词
- POST /{novel_id}/chapters/{chapter_id}/shots/{shot_id}/h3-prompt  分镜→单镜 H3 提示词
- POST /{novel_id}/chapters/{chapter_id}/h3-mapping       资源映射文本→mapping JSON
"""
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import (
    NovelRepository,
    ChapterRepository,
    ShotRepository,
    CharacterRepository,
    SceneRepository,
    PropRepository,
)
from app.api.deps import (
    get_novel_repo,
    get_chapter_repo,
    get_shot_repo,
    get_character_repo,
    get_scene_repo,
    get_prop_repo,
    get_llm_service,
)
from app.services.llm_service import LLMService
from app.services.h3_manju_service import H3ManjuService, normalize_character_name
from app.services.h3_prompt_builder.manju_nodes import parse_mapping_text, _load_json
from app.services.gpu_scheduler import gpu_serial

router = APIRouter()


def _get_h3_service(llm_service: LLMService) -> H3ManjuService:
    return H3ManjuService(llm_service)


def _chapter_storyboard_json(chapter) -> Optional[str]:
    """从 chapter.parsed_data 读取 H3 分镜 JSON。"""

def _build_compat_storyboard(chapter, shots) -> dict:
    """从普通 split 分镜构建 H3 storyboard 兼容结构。

    当 chapter.parsed_data 缺少 h3_storyboard（例如分镜由 split_chapter 生成、
    只存了 chapter/characters/scenes/props 汇总）时兜底，让
    「生成分镜提示词（H3）」无需先重跑 H3 文字分镜即可工作。

    H3 服务（compute_shot_refs / build_wiring_note）只需 shot 的
    characters / scene / props 与索引；action/dialogue 等作为
    LLM 生成 H3 提示词时的上下文输入。
    """
    built = []
    for s in sorted(shots, key=lambda x: x.index):
        try:
            chars = json.loads(s.characters) if s.characters else []
        except Exception:
            chars = []
        try:
            props = json.loads(s.props) if s.props else []
        except Exception:
            props = []
        try:
            dialogues = json.loads(s.dialogues) if s.dialogues else []
        except Exception:
            dialogues = []
        lines = []
        for d in dialogues or []:
            if isinstance(d, dict):
                name = d.get("character_name") or "旁白"
                text = str(d.get("text") or "").strip()
            else:
                name, text = "旁白", str(d).strip()
            if text:
                lines.append("%s：%s" % (name, text))
        built.append({
            "shot_id": s.index,
            "scene": s.scene or "",
            "characters": chars,
            "props": props,
            "action": s.description or "",
            "video_description": s.video_description or "",
            "duration": int(s.duration or 4),
            "dialogue": "\n".join(lines),
        })
    return {"episode_title": chapter.title, "shots": built}

    if not chapter or not chapter.parsed_data:
        return None
    try:
        parsed = json.loads(chapter.parsed_data) if isinstance(chapter.parsed_data, str) else chapter.parsed_data
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    sb = parsed.get("h3_storyboard")
    if isinstance(sb, dict):
        return json.dumps(sb, ensure_ascii=False, indent=2)
    if isinstance(sb, str):
        return sb
    return None


# ==================== 剧本 → 文字分镜 ====================

@router.post("/{novel_id}/chapters/{chapter_id}/h3-split", response_model=dict)
async def h3_split_chapter(
    novel_id: str,
    chapter_id: str,
    data: dict = None,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    shot_repo: ShotRepository = Depends(get_shot_repo),
    character_repo: CharacterRepository = Depends(get_character_repo),
    llm_service: LLMService = Depends(get_llm_service),
):
    """H3：剧本→文字分镜，创建 Shot 记录（替换原 split_chapter 的拆分逻辑）。"""
    data = data or {}
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    novel = novel_repo.get_by_id(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # 角色白名单：小说角色库去重、去括号变体、短名优先
    character_whitelist = []
    for raw_name in character_repo.get_names_by_novel(novel.id):
        base = re.split(r'[（(]', raw_name)[0].strip()
        if base and base not in character_whitelist:
            character_whitelist.append(base)
    character_whitelist.sort(key=len)

    script = (data.get("script") or chapter.content or "").strip()
    if not script:
        raise HTTPException(status_code=400, detail="剧本内容为空，请在章节中填写正文或在请求中提供 script")

    preset_json = data.get("preset_json") or ""
    service = _get_h3_service(llm_service)

    try:
        # 全局 GPU 串行：拆分（30b 重任务）独占 GPU，避免与 8b 提示词/生图并发抢显存
        result = await gpu_serial(
            "h3_split_storyboard",
            service.script_to_storyboard(script, preset_json,
                                         character_whitelist=character_whitelist))
    except Exception as exc:
        return {"success": False, "message": "H3 分镜生成失败：%s" % (exc,)}

    storyboard = result["storyboard"]
    assets = result["assets"]

    # 重新拆分：清空旧分镜及其资源，避免失败或长耗时期间继续展示旧数据
    from app.services.file_storage import file_storage
    file_storage.delete_chapter_directory(novel.id, chapter.id)
    shot_repo.delete_by_chapter(chapter.id)

    shots_data = storyboard.get("shots", [])
    created_shots = []
    for idx, shot_data in enumerate(shots_data, 1):
        dialogue_text = str(shot_data.get("dialogue") or "").strip()
        dialogues = []
        if dialogue_text:
            # 解析「说话人：台词」格式；无说话人前缀时归为旁白，保证音频生成不丢台词
            character_name, text = "", dialogue_text
            for sep in ("：", ":"):
                if sep in dialogue_text:
                    candidate_name, _, candidate_text = dialogue_text.partition(sep)
                    if candidate_name.strip() and candidate_text.strip():
                        character_name, text = candidate_name.strip(), candidate_text.strip()
                        break
            # 白名单纠正：防止 LLM 拆分时缩写/扩写角色名（如 古月方→方源）
            if character_name and character_whitelist:
                character_name = normalize_character_name(character_name, character_whitelist)
            dialogues = [{"character_name": character_name or "旁白", "text": text}]
        # 组装提示词输入（action/camera/mood/shot_size 拼入 video_description 作为视频描述）
        video_desc = (
            "【分镜 %s｜%s｜%s】\n动作：%s\n运镜：%s\n情绪：%s\n景别：%s"
            % (
                shot_data.get("shot_id", idx),
                shot_data.get("time_range", ""),
                shot_data.get("scene", ""),
                shot_data.get("action", ""),
                shot_data.get("camera", ""),
                shot_data.get("mood", ""),
                shot_data.get("shot_size", ""),
            )
        ).strip()
        description = str(shot_data.get("action") or shot_data.get("purpose") or "")
        shot = shot_repo.create(
            chapter_id=chapter.id,
            index=idx,
            description=description,
            video_description=video_desc,
            characters=shot_data.get("characters", []),
            scene=shot_data.get("scene", ""),
            props=shot_data.get("props", []),
            duration=int(shot_data.get("duration") or 4),
            dialogues=dialogues,
        )
        created_shots.append(shot)

    # 保存 H3 分镜与资源清单到 chapter.parsed_data（保留原 characters/scenes/props 汇总）
    parsed_for_storage = {
        "chapter": storyboard.get("episode_title") or chapter.title,
        "h3_storyboard": storyboard,
        "h3_assets": assets,
        "characters": [c.get("id") for c in assets.get("characters", [])],
        "scenes": [s.get("id") for s in assets.get("scenes", [])],
        "props": [p.get("id") for p in assets.get("props", [])],
    }
    chapter.parsed_data = json.dumps(parsed_for_storage, ensure_ascii=False)
    db.commit()

    return {
        "success": True,
        "message": "H3 分镜生成完成，共 %d 个镜头" % len(created_shots),
        "data": {
            "chapter": storyboard.get("episode_title") or chapter.title,
            "characters": [c.get("id") for c in assets.get("characters", [])],
            "scenes": [s.get("id") for s in assets.get("scenes", [])],
            "props": [p.get("id") for p in assets.get("props", [])],
            "storyboard_json": result["storyboard_json"],
            "assets_json": result["assets_json"],
            "summary": result["summary"],
            "shots": [shot_repo.to_response(s) for s in created_shots],
        },
    }


# ==================== 分镜 → 逐镜 H3 提示词 ====================

@router.post("/{novel_id}/chapters/{chapter_id}/h3-prompt-all", response_model=dict)
async def h3_prompt_all_shots(
    novel_id: str,
    chapter_id: str,
    data: dict = None,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    shot_repo: ShotRepository = Depends(get_shot_repo),
    llm_service: LLMService = Depends(get_llm_service),
):
    """H3：分镜→全部镜头 H3 提示词，逐个保存到 shot.video_description。"""
    data = data or {}
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    shots = shot_repo.get_by_chapter(chapter.id)
    if not shots:
        raise HTTPException(status_code=400, detail="该章节没有分镜记录")

    storyboard_json = _chapter_storyboard_json(chapter)
    if not storyboard_json:
        # 普通 split 分镜没有 h3_storyboard：用 DB 分镜构建兼容 storyboard
        storyboard_json = json.dumps(_build_compat_storyboard(chapter, shots), ensure_ascii=False)
    if not storyboard_json:
        raise HTTPException(status_code=400, detail="该章节尚未生成 H3 分镜，请先执行「生成文字分镜（H3）」")

    mapping_json = data.get("mapping_json") or "{}"
    try:
        _load_json(mapping_json, "mapping_json")
    except ValueError:
        mapping_json = "{}"

    service = _get_h3_service(llm_service)

    async def _run_all_shot_prompts():
        results = []
        for shot in sorted(shots, key=lambda s: s.index):
            try:
                out = await service.storyboard_to_shot_prompt(storyboard_json, mapping_json, shot.index)
            except Exception as exc:
                results.append({"index": shot.index, "success": False, "message": str(exc)})
                continue
            shot_repo.update(shot, video_description=out["h3_prompt"])
            results.append({
                "index": shot.index,
                "success": True,
                "h3_prompt": out["h3_prompt"],
                "wiring_note": out["wiring_note"],
            })
        return results

    # 全局 GPU 串行：整批逐镜提示词（8b）独占 GPU，避免与其他 LLM/生图并发
    results = await gpu_serial("h3_prompt_all", _run_all_shot_prompts())

    success_count = sum(1 for r in results if r.get("success"))
    return {
        "success": success_count > 0,
        "message": "H3 提示词生成完成：成功 %d/%d" % (success_count, len(results)),
        "data": {"results": results},
    }


@router.post("/{novel_id}/chapters/{chapter_id}/shots/{shot_id}/h3-prompt", response_model=dict)
async def h3_prompt_one_shot(
    novel_id: str,
    chapter_id: str,
    shot_id: str,
    data: dict = None,
    db: Session = Depends(get_db),
    novel_repo: NovelRepository = Depends(get_novel_repo),
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
    shot_repo: ShotRepository = Depends(get_shot_repo),
    llm_service: LLMService = Depends(get_llm_service),
):
    """H3：分镜→单镜 H3 提示词，保存到 shot.video_description。"""
    data = data or {}
    chapter = chapter_repo.get_by_id(chapter_id, novel_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    shot = shot_repo.get_by_id(shot_id)
    if not shot or shot.chapter_id != chapter.id:
        raise HTTPException(status_code=404, detail="分镜不存在")

    storyboard_json = _chapter_storyboard_json(chapter)
    if not storyboard_json:
        # 普通 split 分镜没有 h3_storyboard：用 DB 分镜构建兼容 storyboard
        shot_all = shot_repo.get_by_chapter(chapter.id)
        if shot_all:
            storyboard_json = json.dumps(_build_compat_storyboard(chapter, shot_all), ensure_ascii=False)
    if not storyboard_json:
        raise HTTPException(status_code=400, detail="该章节尚未生成 H3 分镜，请先执行「生成文字分镜（H3）」")

    mapping_json = data.get("mapping_json") or "{}"
    try:
        _load_json(mapping_json, "mapping_json")
    except ValueError:
        mapping_json = "{}"

    service = _get_h3_service(llm_service)
    try:
        # 全局 GPU 串行：单镜提示词（8b）独占 GPU，避免与拆分(30b)/生图并发抢显存
        out = await gpu_serial(
            "h3_prompt_one",
            service.storyboard_to_shot_prompt(storyboard_json, mapping_json, shot.index))
    except Exception as exc:
        return {"success": False, "message": "H3 提示词生成失败：%s" % (exc,)}

    shot_repo.update(shot, video_description=out["h3_prompt"])
    return {
        "success": True,
        "data": {
            "shot_id": shot.id,
            "index": shot.index,
            "h3_prompt": out["h3_prompt"],
            "wiring_note": out["wiring_note"],
        },
    }


# ==================== 资源映射辅助 ====================

@router.post("/{novel_id}/chapters/{chapter_id}/h3-mapping", response_model=dict)
async def h3_parse_mapping(
    novel_id: str,
    chapter_id: str,
    data: dict = None,
    chapter_repo: ChapterRepository = Depends(get_chapter_repo),
):
    """解析『角色A=图1』资源映射文本 → mapping JSON。"""
    data = data or {}
    text = data.get("text") or ""
    assets_json = data.get("assets_json") or ""
    mapping = parse_mapping_text(text, assets_json)
    try:
        mapping_obj = json.loads(mapping)
    except Exception:
        mapping_obj = {}
    return {"success": True, "data": {"mapping_json": mapping, "mapping": mapping_obj}}
