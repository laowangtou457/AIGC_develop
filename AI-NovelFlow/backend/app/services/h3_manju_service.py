"""
H3 漫剧分镜服务

集成 ComfyUI-H3-Prompt-Builder 的「剧本 → 分镜剧本 → 逐镜 H3 提示词」能力，
作为 FXAI 的「编辑章节 → 生成文字分镜 → 生成分镜提示词」功能。

LLM 调用复用 FXAI 自身的 LLMService 配置（系统设置中已配置的 API），
规则引擎与 JSON 结构处理复用 h3_prompt_builder 包（manju_nodes.py 纯函数）。
"""
import json
import re
from typing import Dict, Any, Optional, List

from app.services.llm_service import LLMService
from app.services.h3_prompt_builder.manju_nodes import (
    build_manju_system_prompt,
    _extract_json,
    _build_summary,
    _policy_from_preset,
    DEFAULT_MANJU_POLICY,
    compute_shot_refs,
    build_wiring_note,
    build_preset_json,
    _load_json,
)

# 角色名白名单纠正：防止 H3 拆分 LLM 缩写/扩写/新造角色名（如 古月方→方源）
def _pick_shortest(matches):
    """多个匹配时优先不含括号的短名（如 方源 优先于 方源（古月族少年蛊师））。"""
    plain = [m for m in matches if '（' not in m and '(' not in m]
    pool = plain or matches
    return min(pool, key=len)


def normalize_character_name(name, whitelist):
    """把角色名映射回白名单；纠正不了返回原样。

    匹配顺序（从精确到宽松）：
    1. 完全一致 → 原样
    2. 去掉括号变体后一致（古月方源（少年） → 古月方源）
    3. 去掉常见姓氏前缀（古月/古）后，剩余部分命中白名单或其前缀
    4. 名字是白名单某项的前缀，且唯一命中
    5. 白名单某项是名字的前缀，且唯一命中（方 → 方源）
    """
    if not name or not whitelist:
        return name
    name = str(name).strip()
    if name in whitelist:
        return name
    base = re.split(r'[（(]', name)[0].strip()
    if base and base in whitelist:
        return base
    for w in whitelist:
        if re.split(r'[（(]', w)[0].strip() == base:
            return w
    for prefix in ('古月', '古'):
        if name.startswith(prefix):
            rest = name[len(prefix):]
            if rest in whitelist:
                return rest
            for w in whitelist:
                if re.split(r'[（(]', w)[0].strip() == rest:
                    return w
            subs = [w for w in whitelist if w.startswith(rest)]
            if subs:
                return _pick_shortest(subs)
    subs = [w for w in whitelist if w.startswith(name)]
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1:
        return _pick_shortest(subs)
    pre = [w for w in whitelist if name.startswith(w)]
    if len(pre) == 1:
        return pre[0]
    if len(pre) > 1:
        return _pick_shortest(pre)
    return name


def build_whitelist_hint(character_whitelist):
    """把角色白名单转成给 LLM 的约束文本；空白名单返回空串。"""
    if not character_whitelist:
        return ''
    names = '、'.join(character_whitelist)
    return (
        "\n\n【角色白名单（必须严格遵守）】\n"
        "本集可出现的角色名仅限：%s。\n"
        "硬性规则：\n"
        "- characters 数组、台词说话人、镜头描述中出现的每个角色名，必须与白名单完全一致；\n"
        "- 禁止缩写（如把「方源」写成「方」或「古月方」）、禁止扩写（如把「方源」写成「古月方源」）、禁止新造白名单外的角色名；\n"
        "- 台词必须使用「说话人：台词」格式，说话人必须取自白名单；\n"
        "- 同一角色全片使用同一个白名单名字，不得换用别名。"
    ) % names


DEFAULT_PRESET = {

    "style": "都市",
    "aspect_ratio": "9:16",
    "duration": None,
    "output_language": "自动（英文结构+保留原文）",
    "audio_text_policy": DEFAULT_MANJU_POLICY,
}


class H3ManjuService:
    """H3 漫剧分镜服务：剧本→分镜剧本→逐镜 H3 提示词。"""

    def __init__(self, llm_service: Optional[LLMService] = None):
        self.llm = llm_service or LLMService()

    async def _chat(self, system_prompt: str, user_content: str,
                    temperature: float = 0.2, max_tokens: int = 32768,
                    task_type: Optional[str] = None) -> str:
        """调用 FXAI LLM，返回文本。task_type 控制轻量模型分流（如 h3_prompt 走 qwen3:8b）。"""
        result = await self.llm.chat_completion(
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=temperature,
            max_tokens=max_tokens,
            task_type=task_type,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "LLM 调用失败")
        content = (result.get("content") or "").strip()
        if not content:
            raise RuntimeError("LLM 返回为空")
        return content

    # ==================== 剧本 → 分镜剧本 ====================

    async def script_to_storyboard(self, script: str, preset_json: str = "",
                                     character_whitelist: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        剧本 → 分镜剧本 JSON + 资源清单 JSON + 摘要。

        Returns:
            {
                "storyboard_json": str,
                "assets_json": str,
                "summary": str,
                "storyboard": dict,
                "assets": dict,
            }
        """
        script = (script or "").strip()
        if not script:
            raise ValueError("剧本内容为空，请输入章节/剧本文本")
        preset = (preset_json or "").strip()
        policy = _policy_from_preset(preset)
        system = build_manju_system_prompt("manju_storyboard.txt", "")
        whitelist_hint = build_whitelist_hint(character_whitelist or [])
        format_schema = (
            "\n\n【输出 JSON 格式（必须严格遵守，禁止 Markdown 代码块、禁止其他结构）】\n"
            "{\n"
            '  "episode_title": "章节标题",\n'
            '  "shots": [\n'
            '    {"shot_id": 1, "time_range": "0:00-0:05", "duration": 5, "scene": "场景id", '
            '"characters": ["角色id"], "props": ["道具id"], "shot_size": "中景", '
            '"camera": "机位与运镜", "action": "画面动作描述", "dialogue": "说话人：台词", '
            '"sfx": "音效", "mood": "情绪", '
            '"continuity": {"start": "起始状态", "end": "结束状态"}, '
            '"purpose": "镜头目的", "screen_direction": "轴线方向"},\n'
            "    ...\n"
            "  ],\n"
            '  "assets": {\n'
            '    "characters": [{"id": "角色名", "description": "外观描述", "shots": [1, 2]}],\n'
            '    "scenes": [{"id": "场景名", "description": "环境描述", "shots": [1]}],\n'
            '    "props": [{"id": "道具名", "description": "描述", "shots": [1]}]\n'
            "  }\n"
            "}\n"
            "storyboard 必须是包含 shots 数组的对象；每个 shot 必须包含上述全部字段；"
            "dialogue 使用「说话人：台词」格式，无台词写空字符串。"
        )
        user_msg = (
            "【本集预设】\n" + (preset or "{}")
            + whitelist_hint
            + "\n\n【剧本原文】\n" + script
            + "\n\n请根据以上剧本与预设生成分镜 JSON（storyboard + assets）。"
            + format_schema
        )
        # 拆分属于重任务（需遵循电影语言规则产出丰满分镜），显式走 30b（HEAVY_MODEL_TASKS）
        raw = await self._chat(system, user_msg, temperature=0.2, task_type="h3_split_storyboard")
        parsed = _extract_json(raw)
        if parsed is None:
            raise ValueError("分镜 JSON 解析失败，请重试。模型输出前 300 字符：%s" % raw[:300])

        if not isinstance(parsed, dict):
            raise ValueError("分镜 JSON 顶层必须是对象，请重试。模型输出前 300 字符：%s" % raw[:300])
        # 宽容解析：兼容 storyboard 直接为 shots 数组（部分模型把 shots 列表直接放在 storyboard 下）
        storyboard = parsed.get("storyboard", parsed)
        if isinstance(storyboard, list):
            storyboard = {
                "episode_title": parsed.get("episode_title") or parsed.get("chapter") or "未命名章节",
                "shots": storyboard,
            }
        if not isinstance(storyboard, dict) or not storyboard.get("shots"):
            raise ValueError("分镜 JSON 缺少 shots 数组，请重试。模型输出前 300 字符：%s" % raw[:300])
        storyboard["_policy"] = policy
        assets = parsed.get("assets") or {"characters": [], "scenes": [], "props": []}
        if not isinstance(assets, dict):
            assets = {"characters": [], "scenes": [], "props": []}
        else:
            assets = {
                "characters": assets.get("characters") if isinstance(assets.get("characters"), list) else [],
                "scenes": assets.get("scenes") if isinstance(assets.get("scenes"), list) else [],
                "props": assets.get("props") if isinstance(assets.get("props"), list) else [],
            }
        storyboard_json = json.dumps(storyboard, ensure_ascii=False, indent=2)
        assets_json = json.dumps(assets, ensure_ascii=False, indent=2)
        summary = _build_summary(storyboard)
        return {
            "storyboard_json": storyboard_json,
            "assets_json": assets_json,
            "summary": summary,
            "storyboard": storyboard,
            "assets": assets,
        }

    # ==================== 分镜 → 逐镜 H3 提示词 ====================

    async def storyboard_to_shot_prompt(self, storyboard_json: str, mapping_json: str,
                                        shot_index: int) -> Dict[str, Any]:
        """
        分镜 → 该镜 H3 提示词 + 接线说明。

        Args:
            storyboard_json: 分镜剧本 JSON 字符串
            mapping_json: 资源映射 JSON（角色A=图1 解析结果）
            shot_index: 镜头序号（1-based）

        Returns:
            {"h3_prompt": str, "wiring_note": str, "refs": dict}
        """
        try:
            storyboard = _load_json(storyboard_json, "storyboard_json")
            _load_json(mapping_json, "mapping_json")
        except ValueError as exc:
            raise ValueError(str(exc))

        policy = storyboard.get("_policy") or DEFAULT_MANJU_POLICY
        refs = compute_shot_refs(storyboard_json, mapping_json, shot_index)
        wiring = build_wiring_note(storyboard_json, mapping_json, shot_index)

        system = build_manju_system_prompt("manju_shot_prompt.txt", "")
        user_msg = (
            "【本镜数据】\n"
            + json.dumps(
                {
                    "shot": refs["shot"],
                    "tags": refs["tags"],
                    "missing": refs["missing"],
                    "policy": policy,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n\n请按规则生成该镜头的 H3 提示词。"
            + "\n\n【硬约束】直接输出最终 H3 提示词文本，以 subject_definitions: 开头，"
              "包含六个字段（subject_definitions/summary/retention_analysis/"
              "detailed_description/overall_soundscape/non_diegetic_music），"
              "禁止输出任何分析、解释、思考过程或 Markdown 代码块。"
        )
        prompt = await self._chat(system, user_msg, temperature=0.4, task_type="h3_prompt")
        return {"h3_prompt": prompt, "wiring_note": wiring, "refs": refs}

    # ==================== 辅助 ====================

    @staticmethod
    def build_default_preset(style: str = "都市", aspect_ratio: str = "9:16",
                              duration: Optional[int] = None,
                              audio_text_policy: str = DEFAULT_MANJU_POLICY) -> str:
        """生成默认预设 JSON。"""
        preset = dict(DEFAULT_PRESET)
        preset["style"] = style or "都市"
        preset["aspect_ratio"] = aspect_ratio or "9:16"
        preset["duration"] = duration
        preset["audio_text_policy"] = audio_text_policy
        return json.dumps(preset, ensure_ascii=False, indent=2)

    @staticmethod
    def shot_to_dialogue_list(shot: dict, character_whitelist: Optional[List[str]] = None) -> list:
        """把 H3 分镜的 dialogue 字段转为 FXAI dialogues 数组格式。

        character_whitelist 非空时，说话人角色名会被纠正回白名单
        （防止 LLM 拆分时缩写/扩写角色名，如 古月方→方源）。
        """
        dialogue = shot.get("dialogue") or ""
        if not dialogue:
            return []
        raw = str(dialogue).strip()
        # 解析「说话人：台词」格式；无说话人前缀时归为旁白，保证音频生成不丢台词
        character_name, text = "", raw
        for sep in ("：", ":"):
            if sep in raw:
                candidate_name, _, candidate_text = raw.partition(sep)
                if candidate_name.strip() and candidate_text.strip():
                    character_name, text = candidate_name.strip(), candidate_text.strip()
                    break
        if character_name and character_whitelist:
            character_name = normalize_character_name(character_name, character_whitelist)
        return [{"character_name": character_name or "旁白", "text": text}]
