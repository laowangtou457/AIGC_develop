"""
小说服务层

封装小说相关的业务逻辑
"""
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.novel import Novel, Chapter, Character, Scene
from app.models.prompt_template import PromptTemplate
from app.core.database import SessionLocal
from app.utils.time_utils import format_datetime
from app.services.llm_service import LLMService
from app.services.comfyui import ComfyUIService
from app.services.file_storage import file_storage
from app.services.prompt_builder import get_style
from app.utils.path_utils import url_to_local_path
from app.utils.image_utils import load_chinese_font, merge_character_images
from app.repositories.shot_repository import ShotRepository



def _str_field(value):
    """LLM 可能返回嵌套 dict/list，写库前统一转字符串。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json as _json
        return _json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalize_entity_item(item, fallback_desc=""):
    """兼容 qwen 等模型把实体输出为纯字符串的情况：str -> {"name": str}。"""
    if isinstance(item, str):
        return {"name": item, "description": fallback_desc}
    if isinstance(item, dict):
        return item
    return None



# 单次 LLM 解析输入上限（qwen3:8b 稳妥值）：全本拼接超长会导致解析返回空
MAX_PARSE_CHARS = 6000


def _split_content_chunks(chapters, prefix_fn=None):
    """把章节内容按字符数分块，返回 [text_chunk, ...]。prefix_fn(ch) 可选加标题头。"""
    chunks, buf = [], ""
    for ch in chapters:
        content = (ch.content or "").strip()
        if not content:
            continue
        head = prefix_fn(ch) if prefix_fn else ""
        piece = f"{head}{content}"
        if buf and len(buf) + len(piece) > MAX_PARSE_CHARS:
            chunks.append(buf)
            buf = piece
        else:
            buf = f"{buf}\n\n{piece}" if buf else piece
    if buf:
        chunks.append(buf)
    return chunks

class NovelService:
    """小说服务"""
    
    def __init__(self, db: Session = None):
        self.db = db
        self.comfyui_service = ComfyUIService()
    
    def get_llm_service(self) -> LLMService:
        """获取 LLMService 实例（每次调用创建新实例以获取最新配置）"""
        return LLMService()
    
    # ==================== 角色解析 ====================
    
    async def parse_characters(
        self,
        novel_id: str,
        chapters: List[Chapter],
        start_chapter: int = None,
        end_chapter: int = None,
        is_incremental: bool = False,
        character_repo = None
    ) -> Dict[str, Any]:
        """
        解析小说内容，自动提取角色信息
        
        Args:
            novel_id: 小说ID
            chapters: 章节列表
            start_chapter: 起始章节号
            end_chapter: 结束章节号
            is_incremental: 是否增量更新
            character_repo: 角色仓库
            
        Returns:
            解析结果
        """
        # 构造章节范围描述
        source_range = None
        if start_chapter is not None or end_chapter is not None:
            start_desc = f"第{start_chapter}章" if start_chapter is not None else "第1章"
            end_desc = f"第{end_chapter}章" if end_chapter is not None else f"第{chapters[-1].number}章"
            source_range = f"{start_desc}至{end_desc}"
        
        full_text = "\n\n".join([c.content for c in chapters if c.content])
        if not full_text.strip():
            return {"success": False, "message": "章节内容为空"}
        
        try:
            # 分块调用 LLM 解析文本提取角色（全本拼接一次调用会超上下文返回空）
            chunks = _split_content_chunks(chapters)
            characters_data = []
            for chunk_i, chunk_text in enumerate(chunks, 1):
                chunk_result = await self.get_llm_service().parse_novel_text(
                    chunk_text, novel_id=novel_id,
                    source_range=source_range if len(chunks) == 1 else f"{source_range or '全本'}·第{chunk_i}/{len(chunks)}块"
                )
                if chunk_result.get("error"):
                    print(f"[parse_characters] 分块 {chunk_i}/{len(chunks)} 失败: {chunk_result.get('error')}")
                    continue
                block = chunk_result.get("characters", [])
                characters_data.extend(block)
                print(f"[parse_characters] 分块 {chunk_i}/{len(chunks)} 提取 {len(block)} 个角色")

            if not characters_data:
                return {"success": True, "data": [], "message": "未识别到角色"}
            
            # 创建角色记录
            created_characters = []
            updated_characters = []
            deleted_count = 0
            parsed_names = set()

            # 角色名归一化：去空白、去"古月"姓氏前缀（古月方源 = 方源）。
            # 注意：带年龄段后缀的名字（如"方源（少年）"）归一化后仍与"方源"不同，
            # 因此不同年龄段天然保留为不同人物，符合"同段只留一个"的要求。
            def _norm_char_name(n):
                n = (n or "").replace(" ", "").strip()
                if n.startswith("古月") and len(n) > 2:
                    return n[2:]
                return n

            # 同批解析内存去重表：归一化名 -> 角色对象。
            # 同一批分块解析中同名角色（如"古月博"在多个分块出现）只创建一次。
            resolved_by_norm = {}
            # 现有角色一次查出缓存（跳过旁白），供姓氏变体/子串合并复用
            existing_cache = {c.name: c for c in character_repo.list_by_novel(novel_id) if not c.is_narrator}

            for raw_char in characters_data:
                char_data = _normalize_entity_item(raw_char) or {}
                char_data = {k: (_str_field(v) if k in ("description", "appearance", "voice_prompt") else v)
                             for k, v in char_data.items()}
                name = str(char_data.get("name", "") or "").strip()
                if not name:
                    continue
                parsed_names.add(name)
                norm_name = _norm_char_name(name)

                # 1) 同批内存去重：本批已创建/已合并过的同一归一化角色直接复用
                if norm_name in resolved_by_norm:
                    existing = resolved_by_norm[norm_name]
                    print(f"[parse_characters] 同批去重: '{name}' 与 '{existing.name}' 视为同一角色")
                else:
                    # 2) 精确名查重（DB 已存在角色）
                    existing = character_repo.get_by_name(novel_id, name)

                    # 3) 姓氏变体合并：归一化名相同（"古月方源" ↔ "方源"）
                    if not existing:
                        for ch in existing_cache.values():
                            if _norm_char_name(ch.name) == norm_name and ch.name != name:
                                existing = ch
                                print(f"[parse_characters] 合并姓氏变体: '{name}' -> '{ch.name}'")
                                break

                    # 4) 别名/包含名合并：归一化名互为子串且长度差<=2
                    #    （如 "白凝冰" ⊂ "白凝冰老祖"）
                    if not existing:
                        for ch in existing_cache.values():
                            c_norm = _norm_char_name(ch.name)
                            shorter, longer = (c_norm, norm_name) if len(c_norm) <= len(norm_name) else (norm_name, c_norm)
                            if (len(longer) - len(shorter) <= 2
                                    and shorter and longer != shorter
                                    and shorter in longer):
                                existing = ch
                                print(f"[parse_characters] 合并同义角色: '{name}' -> '{ch.name}'")
                                break

                    if existing:
                        resolved_by_norm[norm_name] = existing

                if existing:
                    # 增量更新模式：保留原有信息，只更新新解析的信息
                    if is_incremental:
                        if not existing.description and char_data.get("description"):
                            existing.description = char_data.get("description")
                        if not existing.appearance and char_data.get("appearance"):
                            existing.appearance = char_data.get("appearance")
                        if not existing.voice_prompt and char_data.get("voice_prompt"):
                            existing.voice_prompt = char_data.get("voice_prompt")
                        if source_range:
                            if existing.source_range:
                                existing.source_range += f", {source_range}"
                            else:
                                existing.source_range = source_range
                    else:
                        # 全量更新模式：直接覆盖
                        existing.description = char_data.get("description", existing.description)
                        existing.appearance = char_data.get("appearance", existing.appearance)
                        if char_data.get("voice_prompt"):
                            existing.voice_prompt = char_data.get("voice_prompt")
                        existing.source_range = source_range
                    
                    existing.last_parsed_at = datetime.now()
                    updated_characters.append(existing)
                else:
                    # 创建新角色
                    character = Character(
                        novel_id=novel_id,
                        name=name,
                        description=char_data.get("description", ""),
                        appearance=char_data.get("appearance", ""),
                        voice_prompt=char_data.get("voice_prompt", ""),
                        start_chapter=start_chapter,
                        end_chapter=end_chapter,
                        is_incremental=is_incremental,
                        source_range=source_range,
                        last_parsed_at=datetime.now()
                    )
                    self.db.add(character)
                    created_characters.append(character)

            # 全书非增量解析应以本次 LLM 返回结果为准，删除未再出现的旧角色。
            # 章节范围解析不删除，避免误删范围外章节的角色；旁白角色始终保留。
            should_delete_missing = not is_incremental and start_chapter is None and end_chapter is None
            if should_delete_missing:
                existing_characters = character_repo.list_by_novel(novel_id)
                for character in existing_characters:
                    if character.is_narrator:
                        continue
                    if character.name not in parsed_names:
                        self.db.delete(character)
                        deleted_count += 1

            self.db.commit()
            
            # 刷新对象以获取 ID
            for char in created_characters + updated_characters:
                self.db.refresh(char)
            
            # 构造响应消息
            message_parts = []
            if created_characters:
                message_parts.append(f"新增 {len(created_characters)} 个角色")
            if updated_characters:
                message_parts.append(f"更新 {len(updated_characters)} 个角色")
            if deleted_count:
                message_parts.append(f"删除 {deleted_count} 个旧角色")

            # 自动创建旁白角色
            from app.repositories import CharacterRepository
            character_repo_for_narrator = CharacterRepository(self.db)
            narrator = character_repo_for_narrator.get_narrator(novel_id)
            if not narrator:
                character_repo_for_narrator.create_narrator(novel_id)
                message_parts.append("自动创建旁白角色")

            return {
                "success": True,
                "data": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "description": c.description,
                        "appearance": c.appearance,
                        "voicePrompt": c.voice_prompt,
                        "startChapter": c.start_chapter,
                        "endChapter": c.end_chapter,
                        "isIncremental": c.is_incremental,
                        "sourceRange": c.source_range,
                        "lastParsedAt": format_datetime(c.last_parsed_at)
                    }
                    for c in created_characters + updated_characters
                ],
                "message": "，".join(message_parts) if message_parts else "未识别到角色",
                "statistics": {
                    "created": len(created_characters),
                    "updated": len(updated_characters),
                    "deleted": deleted_count,
                    "total": len(created_characters) + len(updated_characters)
                }
            }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"success": False, "message": f"解析异常: {str(e)}"}

    # ==================== 道具解析 ====================

    async def parse_props(
        self,
        novel_id: str,
        chapters: List[Chapter],
        start_chapter: int = None,
        end_chapter: int = None,
        is_incremental: bool = False,
        prop_repo = None
    ) -> Dict[str, Any]:
        """
        解析小说内容，自动提取道具信息

        Args:
            novel_id: 小说ID
            chapters: 章节列表
            start_chapter: 起始章节号
            end_chapter: 结束章节号
            is_incremental: 是否增量更新
            prop_repo: 道具仓库

        Returns:
            解析结果
        """
        from app.models.novel import Prop

        # 构造章节范围描述
        source_range = None
        if start_chapter is not None or end_chapter is not None:
            start_desc = f"第{start_chapter}章" if start_chapter is not None else "第1章"
            end_desc = f"第{end_chapter}章" if end_chapter is not None else f"第{chapters[-1].number}章"
            source_range = f"{start_desc}至{end_desc}"

        full_text = "\n\n".join([c.content for c in chapters if c.content])
        if not full_text.strip():
            return {"success": False, "message": "章节内容为空"}

        try:
            # 获取道具解析提示词模板
            prompt_template = None
            template = self.db.query(PromptTemplate).filter(
                PromptTemplate.type == 'prop_parse',
                PromptTemplate.is_system == True
            ).order_by(PromptTemplate.created_at.asc()).first()

            if template:
                prompt_template = template.template

            # 如果没有系统模板，使用默认模板文件
            if not prompt_template:
                template_path = os.path.join(os.path.dirname(__file__), '..', 'prompt_templates', 'prop_parse.txt')
                if os.path.exists(template_path):
                    with open(template_path, "r", encoding="utf-8") as f:
                        prompt_template = f.read()

            # 分块调用 LLM 解析文本提取道具（全本拼接会超上下文返回空）
            chunks = _split_content_chunks(chapters)
            props_data = []
            for chunk_i, chunk_text in enumerate(chunks, 1):
                chunk_result = await self.get_llm_service().parse_props(
                    text=chunk_text,
                    prompt_template=prompt_template,
                    prompt_template_name=template.name if template else "默认道具解析提示词"
                )
                if chunk_result.get("error"):
                    print(f"[parse_props] 分块 {chunk_i}/{len(chunks)} 失败: {chunk_result.get('error')}")
                    continue
                block = chunk_result.get("props", [])
                props_data.extend(block)
                print(f"[parse_props] 分块 {chunk_i}/{len(chunks)} 提取 {len(block)} 个道具")

            if not props_data:
                return {"success": True, "data": [], "message": "未识别到道具"}

            # 创建道具记录
            created_props = []
            updated_props = []

            # 获取现有道具
            existing_props = prop_repo.get_dict_by_novel(novel_id)
            # 分块解析时同一道具可能在多个块重复出现，按 name 去重只处理一次
            seen_names = set()

            for raw_prop in props_data:
                prop_data = _normalize_entity_item(raw_prop) or {}
                prop_data = {k: (_str_field(v) if k in ("description", "appearance") else v)
                             for k, v in prop_data.items()}
                name = str(prop_data.get("name", "") or "").strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)

                if name in existing_props:
                    # 更新现有道具
                    existing = existing_props[name]

                    if is_incremental:
                        if not existing.description and prop_data.get("description"):
                            existing.description = prop_data.get("description")
                        if not existing.appearance and prop_data.get("appearance"):
                            existing.appearance = prop_data.get("appearance")
                        if source_range:
                            if existing.source_range:
                                existing.source_range += f", {source_range}"
                            else:
                                existing.source_range = source_range
                    else:
                        existing.description = prop_data.get("description", existing.description)
                        existing.appearance = prop_data.get("appearance", existing.appearance)
                        existing.source_range = source_range

                    existing.last_parsed_at = datetime.now()
                    updated_props.append(existing)
                else:
                    # 创建新道具
                    prop = Prop(
                        novel_id=novel_id,
                        name=name,
                        description=prop_data.get("description", ""),
                        appearance=prop_data.get("appearance", ""),
                        start_chapter=start_chapter,
                        end_chapter=end_chapter,
                        is_incremental=is_incremental,
                        source_range=source_range,
                        last_parsed_at=datetime.now()
                    )
                    self.db.add(prop)
                    created_props.append(prop)
                    existing_props[name] = prop

            self.db.commit()

            # 刷新对象以获取 ID
            for prop in created_props + updated_props:
                self.db.refresh(prop)

            # 构造响应消息
            message_parts = []
            if created_props:
                message_parts.append(f"新增 {len(created_props)} 个道具")
            if updated_props:
                message_parts.append(f"更新 {len(updated_props)} 个道具")

            return {
                "success": True,
                "data": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "description": p.description,
                        "appearance": p.appearance,
                        "startChapter": p.start_chapter,
                        "endChapter": p.end_chapter,
                        "isIncremental": p.is_incremental,
                        "sourceRange": p.source_range,
                        "lastParsedAt": format_datetime(p.last_parsed_at)
                    }
                    for p in created_props + updated_props
                ],
                "message": "，".join(message_parts) if message_parts else "未识别到道具",
                "statistics": {
                    "created": len(created_props),
                    "updated": len(updated_props),
                    "total": len(created_props) + len(updated_props)
                }
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"success": False, "message": f"解析异常: {str(e)}"}

    # ==================== 场景解析 ====================
    
    async def parse_scenes_from_chapters(
        self,
        novel_id: str,
        chapters: List[Chapter],
        mode: str = "incremental",
        scene_repo = None,
        prompt_template_repo = None
    ) -> Dict[str, Any]:
        """
        解析多章节内容，提取场景信息
        
        Args:
            novel_id: 小说ID
            chapters: 章节列表
            mode: 解析模式 (incremental/overwrite)
            scene_repo: 场景仓库
            prompt_template_repo: 提示词模板仓库
            
        Returns:
            解析结果
        """
        if not chapters:
            return {"success": False, "message": "没有找到章节"}
        
        # 构建章节范围说明
        if len(chapters) == 1:
            source_range = f"第{chapters[0].number}章"
        else:
            source_range = f"第{chapters[0].number}章 ~ 第{chapters[-1].number}章"
        
        # 合并章节内容
        combined_content = ""
        for chapter in chapters:
            combined_content += f"\n\n【第{chapter.number}章 {chapter.title}】\n{chapter.content or ''}"
        
        # 获取场景解析提示词模板
        prompt_template = None
        prompt_template_name = None
        if prompt_template_repo:
            templates = prompt_template_repo.list_by_type('scene_parse')
            if templates:
                prompt_template = templates[0].template
                prompt_template_name = templates[0].name
        
        try:
            # 分块调用 LLM 解析场景（全本拼接会超上下文返回空）
            chunks = _split_content_chunks(
                chapters, prefix_fn=lambda ch: f"【第{ch.number}章 {ch.title}】\n")
            scenes_data = []
            for chunk_i, chunk_text in enumerate(chunks, 1):
                chunk_result = await self.get_llm_service().parse_scenes(
                    novel_id=novel_id,
                    chapter_content=chunk_text,
                    chapter_title=source_range,
                    prompt_template=prompt_template,
                    prompt_template_name=prompt_template_name
                )
                if chunk_result.get("error"):
                    print(f"[parse_scenes] 分块 {chunk_i}/{len(chunks)} 失败: {chunk_result.get('error')}")
                    continue
                block = chunk_result.get("scenes", [])
                scenes_data.extend(block)
                print(f"[parse_scenes] 分块 {chunk_i}/{len(chunks)} 提取 {len(block)} 个场景")
            
            # 获取现有场景
            existing_scene_names = scene_repo.get_dict_by_novel(novel_id)
            
            created_scenes = []
            updated_scenes = []
            
            for raw_scene in scenes_data:
                scene_data = _normalize_entity_item(raw_scene) or {}
                scene_data = {k: (_str_field(v) if k in ("description", "setting") else v)
                              for k, v in scene_data.items()}
                name = str(scene_data.get("name", "") or "")
                if not name:
                    continue
                
                if name in existing_scene_names:
                    # 更新现有场景
                    existing = existing_scene_names[name]
                    scene_repo.update(
                        existing,
                        description=scene_data.get("description", existing.description),
                        setting=scene_data.get("setting", existing.setting),
                        start_chapter=min(existing.start_chapter, chapters[0].number) if existing.start_chapter and mode == "incremental" else chapters[0].number,
                        end_chapter=max(existing.end_chapter, chapters[-1].number) if existing.end_chapter and mode == "incremental" else chapters[-1].number,
                        is_incremental=mode == "incremental",
                        source_range=source_range,
                        last_parsed_at=datetime.now()
                    )
                    updated_scenes.append(existing)
                else:
                    # 创建新场景
                    scene = scene_repo.create(
                        novel_id=novel_id,
                        name=name,
                        description=scene_data.get("description", ""),
                        setting=scene_data.get("setting", ""),
                        start_chapter=chapters[0].number,
                        end_chapter=chapters[-1].number,
                        source_range=source_range,
                    )
                    # 更新增量标记
                    if mode == "incremental":
                        scene.is_incremental = True
                        scene.last_parsed_at = datetime.now()
                        self.db.commit()
                    created_scenes.append(scene)
            
            # 构造响应消息
            message_parts = []
            if created_scenes:
                message_parts.append(f"新增 {len(created_scenes)} 个场景")
            if updated_scenes:
                message_parts.append(f"更新 {len(updated_scenes)} 个场景")
            
            return {
                "success": True,
                "data": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "description": s.description,
                        "setting": s.setting,
                        "startChapter": s.start_chapter,
                        "endChapter": s.end_chapter,
                        "isIncremental": s.is_incremental,
                        "sourceRange": s.source_range,
                        "lastParsedAt": format_datetime(s.last_parsed_at)
                    }
                    for s in created_scenes + updated_scenes
                ],
                "message": "，".join(message_parts) if message_parts else "未识别到场景",
                "statistics": {
                    "created": len(created_scenes),
                    "updated": len(updated_scenes),
                    "total": len(created_scenes) + len(updated_scenes)
                }
            }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"success": False, "message": f"解析异常: {str(e)}"}
    
    async def parse_scenes(
        self,
        novel_id: str,
        chapter: Chapter,
        is_incremental: bool = True,
        scene_repo = None
    ) -> Dict[str, Any]:
        """
        解析单章节内容，提取场景信息
        
        Args:
            novel_id: 小说ID
            chapter: 章节对象
            is_incremental: 是否增量更新
            scene_repo: 场景仓库
            
        Returns:
            解析结果
        """
        if not chapter.content:
            return {"success": False, "message": "章节内容为空"}
        
        # 获取场景解析提示词模板
        template = self.db.query(PromptTemplate).filter(
            PromptTemplate.type == 'scene_parse',
            PromptTemplate.is_system == True
        ).order_by(PromptTemplate.created_at.asc()).first()
        
        prompt_template = template.template if template else None
        
        try:
            source_range = f"第{chapter.number}章"
            
            # 调用 LLM 解析场景
            result = await self.get_llm_service().parse_scenes(
                novel_id=novel_id,
                chapter_content=chapter.content[:20000],  # 限制长度
                chapter_title=source_range,
                prompt_template=prompt_template,
                prompt_template_name=template.name if template else "默认场景解析提示词"
            )
            
            if result.get("error"):
                return {"success": False, "message": result["error"]}
            
            scenes_data = result.get("scenes", [])
            
            # 获取现有场景
            existing_scene_names = scene_repo.get_dict_by_novel(novel_id)
            
            created_scenes = []
            updated_scenes = []
            
            for raw_scene in scenes_data:
                scene_data = _normalize_entity_item(raw_scene) or {}
                scene_data = {k: (_str_field(v) if k in ("description", "setting") else v)
                              for k, v in scene_data.items()}
                name = str(scene_data.get("name", "") or "")
                if not name:
                    continue
                
                if name in existing_scene_names:
                    # 更新现有场景
                    existing = existing_scene_names[name]
                    
                    if is_incremental:
                        if not existing.description and scene_data.get("description"):
                            existing.description = scene_data.get("description")
                        if not existing.setting and scene_data.get("setting"):
                            existing.setting = scene_data.get("setting")
                        if existing.source_range:
                            if source_range not in existing.source_range:
                                existing.source_range += f", {source_range}"
                        else:
                            existing.source_range = source_range
                    else:
                        existing.description = scene_data.get("description", existing.description)
                        existing.setting = scene_data.get("setting", existing.setting)
                        existing.source_range = source_range
                    
                    existing.is_incremental = is_incremental
                    existing.last_parsed_at = datetime.now()
                    updated_scenes.append(existing)
                else:
                    # 创建新场景
                    scene = Scene(
                        novel_id=novel_id,
                        name=name,
                        description=scene_data.get("description", ""),
                        setting=scene_data.get("setting", ""),
                        start_chapter=chapter.number,
                        end_chapter=chapter.number,
                        is_incremental=is_incremental,
                        source_range=source_range,
                        last_parsed_at=datetime.now()
                    )
                    self.db.add(scene)
                    created_scenes.append(scene)
            
            self.db.commit()
            
            # 刷新对象
            for s in created_scenes + updated_scenes:
                self.db.refresh(s)
            
            # 构造响应消息
            message_parts = []
            if created_scenes:
                message_parts.append(f"新增 {len(created_scenes)} 个场景")
            if updated_scenes:
                message_parts.append(f"更新 {len(updated_scenes)} 个场景")
            
            return {
                "success": True,
                "data": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "description": s.description,
                        "setting": s.setting,
                        "startChapter": s.start_chapter,
                        "endChapter": s.end_chapter,
                        "isIncremental": s.is_incremental,
                        "sourceRange": s.source_range,
                        "lastParsedAt": format_datetime(s.last_parsed_at)
                    }
                    for s in created_scenes + updated_scenes
                ],
                "message": "，".join(message_parts) if message_parts else "未识别到场景",
                "statistics": {
                    "created": len(created_scenes),
                    "updated": len(updated_scenes),
                    "total": len(created_scenes) + len(updated_scenes)
                }
            }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"success": False, "message": f"解析异常: {str(e)}"}
    
    # ==================== 章节拆分 ====================
    
    async def split_chapter(
        self,
        novel: Novel,
        chapter: Chapter,
        character_names: List[str],
        scene_names: List[str],
        prop_names: List[str] = None
    ) -> Dict[str, Any]:
        """
        使用小说配置的拆分提示词将章节拆分为分镜
        
        Args:
            novel: 小说对象
            chapter: 章节对象
            character_names: 角色名称列表
            scene_names: 场景名称列表
            prop_names: 道具名称列表
            
        Returns:
            拆分结果
        """
        # 检查 LLM 配置
        llm_service = self.get_llm_service()
        if not llm_service.api_key and llm_service.provider != "ollama":
            return {
                "success": False,
                "data": {
                    "error": "LLM API Key 未配置，请在系统设置中配置 API Key",
                    "chapter": chapter.title,
                    "characters": [],
                    "scenes": [],
                    "shots": []
                }
            }
        
        if not llm_service.api_url:
            return {
                "success": False,
                "data": {
                    "error": "LLM API URL 未配置，请在系统设置中配置 API URL",
                    "chapter": chapter.title,
                    "characters": [],
                    "scenes": [],
                    "shots": []
                }
            }
        
        # 获取拆分提示词模板
        prompt_template = None
        if novel.chapter_split_prompt_template_id:
            prompt_template = self.db.query(PromptTemplate).filter(
                PromptTemplate.id == novel.chapter_split_prompt_template_id
            ).first()
        
        # 如果没有配置，使用默认模板
        if not prompt_template:
            prompt_template = self.db.query(PromptTemplate).filter(
                PromptTemplate.type == "chapter_split",
                PromptTemplate.is_system == True
            ).first()
        
        if not prompt_template:
            return {"success": False, "message": "未找到章节拆分提示词模板"}

        # 重新拆分代表旧分镜及其生成资源已失效，先清空，避免失败或长耗时期间继续展示旧数据。
        shot_repo = ShotRepository(self.db)
        file_storage.delete_chapter_directory(novel.id, chapter.id)
        shot_repo.delete_by_chapter(chapter.id)
        chapter.parsed_data = None
        chapter.shot_images = None
        chapter.shot_videos = None
        chapter.transition_videos = None
        chapter.merged_image = None
        self.db.commit()
        
        # 获取风格提示词
        style, style_template = get_style(self.db, novel, "character")
        print(f"[SplitChapter] Using style: {style}")
        
        # 调用 LLM 进行拆分
        result = await llm_service.split_chapter_with_prompt(
            chapter_title=chapter.title,
            chapter_content=chapter.content or "",
            prompt_template=prompt_template.template,
            word_count=100,
            character_names=character_names,
            scene_names=scene_names,
            prop_names=prop_names,
            style=style,
            novel_id=novel.id,
            chapter_id=chapter.id,
            prompt_template_name=prompt_template.name
        )

        # 检查是否有错误
        if result.get("error"):
            return {
                "success": False,
                "message": f"LLM 拆分失败: {result.get('error')}",
                "data": result
            }

        shots_data = result.get("shots", [])

        # 创建 Shot 记录
        created_shots = []
        for idx, shot_data in enumerate(shots_data, 1):
            shot = shot_repo.create(
                chapter_id=chapter.id,
                index=idx,
                description=shot_data.get("description", ""),
                video_description=shot_data.get("video_description", ""),
                characters=shot_data.get("characters", []),
                scene=shot_data.get("scene", ""),
                props=shot_data.get("props", []),
                duration=shot_data.get("duration", 4),
                dialogues=shot_data.get("dialogues", []),
            )
            created_shots.append(shot)

        # 更新 parsed_data，移除 shots 数组（已迁移到 Shot 表）
        parsed_data_for_storage = {
            "chapter": result.get("chapter", chapter.title),
            "characters": result.get("characters", []),
            "scenes": result.get("scenes", []),
            "props": result.get("props", []),
        }
        chapter.parsed_data = json.dumps(parsed_data_for_storage, ensure_ascii=False)
        self.db.commit()

        # 构造返回数据，包含 Shot 记录
        return {
            "success": True,
            "data": {
                "chapter": result.get("chapter", chapter.title),
                "characters": result.get("characters", []),
                "scenes": result.get("scenes", []),
                "props": result.get("props", []),
                "shots": [shot_repo.to_response(s) for s in created_shots],
            }
        }

    
    # ==================== 图片合并 ====================
    
    def merge_character_images(
        self,
        novel_id: str,
        chapter_id: str,
        shot_index: int,
        character_images: List[Tuple[str, str]]
    ) -> Optional[str]:
        """
        合并多个角色图片为一个参考图（委托给工具函数）
        
        Args:
            novel_id: 小说ID
            chapter_id: 章节ID
            shot_index: 分镜索引
            character_images: [(角色名, 图片路径), ...]
            
        Returns:
            合并后的图片路径，失败返回 None
        """
        return merge_character_images(
            novel_id, chapter_id, shot_index, character_images, file_storage
        )


# 后台任务函数已移至独立模块
from app.services.media_task_service import (
    generate_shot_task,
    generate_shot_video_task,
    generate_transition_video_task
)
