"""
LLM 服务封装 - 支持多厂商调用 (DeepSeek, OpenAI, Gemini, Anthropic, Azure, Custom)

对外暴露的服务层，内部使用 LLMClient 实现底层调用。
"""
from typing import Dict, Any, List, Optional
from app.core.config import get_settings
from app.services.llm import LLMClient, LLMConfig
from app.utils.json_parser import safe_parse_llm_json, clean_llm_response
from app.constants import (
    DEFAULT_TEMPERATURE,
    DEFAULT_MAX_TOKENS,
    DEFAULT_VIDEO_DURATION,
    NOVEL_TEXT_MAX_LENGTH,
    CHAPTER_CONTENT_MAX_LENGTH,
    DEFAULT_PARSE_CHARACTERS_PROMPT,
    CHAPTER_RANGE_PLACEHOLDER,
    DEFAULT_CHAPTER_RANGE_DESCRIPTION,
    DEFAULT_PARSE_SCENES_PROMPT,
    DEFAULT_VIDEO_PROMPT_EXPAND_SYSTEM,
    DEFAULT_CHARACTER_APPEARANCE_FALLBACK,
    DEFAULT_SCENE_SETTING_FALLBACK,
    DEFAULT_PROP_APPEARANCE_FALLBACK,
    get_character_appearance_prompt,
    get_scene_setting_prompt,
    get_prop_appearance_prompt,
)



def _normalize_llm_json(data, key):
    """兼容 qwen 等模型直接输出 JSON 数组的情况：list -> {key: list}。"""
    if isinstance(data, list):
        return {key: data}
    if isinstance(data, dict):
        return data
    return None


# 解析类任务（角色/场景/道具）使用轻量模型：30b-a3b 长文本 + JSON 约束下不稳定，
# 8b 更快更稳；推理/规划类重任务（视频推荐、关键帧规划、prompt 扩写等）保持系统默认模型。
LIGHT_MODEL_TASKS = {
    "parse_characters", "parse_scenes", "parse_props", "translate_appearance",
    "h3_prompt",  # H3 逐镜提示词：30b 会输出大量分析叙述污染六段式，8b 直接输出干净结果
    "shot_image_prompt",  # 主分镜图提示词：30b 输出 1.9 万字符分析叙述且易卡死，8b 直接输出 Qwen-Edit 提示词正文
    "martial_arts_plan",  # 武术指导-招式编排：8b 输出紧凑招式列表/锚定卡
    "martial_arts_build",  # 武术指导-提示词组装：30b 长文输出带过程叙述易截断，8b 直接输出两段正文
    "prompt_reforge_extract",  # 提示词提取与重构-节拍提取：8b JSON 输出稳定
    "prompt_reforge_build",    # 提示词提取与重构-逐镜重构：8b 直接输出干净六段式/六要素正文
}
LIGHT_PARSE_MODEL = "qwen3:8b"

# 重任务模型分流：需要更强推理/规划能力的任务显式指定 30b（如 H3 文字分镜拆分）。
# 此类任务即使系统默认模型被切成 8b（性能治理），仍强制使用 30b，保证产出质量。
HEAVY_MODEL_TASKS = {
    "h3_split_storyboard",  # H3 生成文字分镜：拆分需遵循 manju_storyboard 电影语言规则，30b 产出更丰满
}
HEAVY_MODEL = "qwen3:30b-a3b"


class LLMService:
    """多厂商 LLM API 服务封装

    对外暴露的服务层，内部使用 LLMClient 实现底层调用。
    """

    def __init__(self):
        # 每次实例化时重新获取 settings，确保获取最新配置
        from app.core.config import get_settings
        current_settings = get_settings()

        self.provider = current_settings.LLM_PROVIDER
        self.model = current_settings.LLM_MODEL
        self.api_url = current_settings.LLM_API_URL
        self.api_key = current_settings.LLM_API_KEY
        self.max_tokens = getattr(current_settings, 'LLM_MAX_TOKENS', None)  # 从配置中获取 max_tokens
        self.temperature = getattr(current_settings, 'LLM_TEMPERATURE', None)  # 从配置中获取 temperature
        self.timeout = getattr(current_settings, 'LLM_TIMEOUT', None)  # 从配置中获取请求超时（秒）

        # 代理配置
        self.proxy_enabled = current_settings.PROXY_ENABLED
        self.http_proxy = current_settings.HTTP_PROXY
        self.https_proxy = current_settings.HTTPS_PROXY

        # 仅 DeepSeek 自身允许读取历史 DeepSeek Key，不能把 OpenAI 等厂商改成 deepseek。
        if self.provider == "deepseek" and not self.api_key:
            self.api_key = current_settings.DEEPSEEK_API_KEY
            self.api_url = current_settings.DEEPSEEK_API_URL

        # 初始化 API Key 轮询机制
        self.api_keys = []
        if self.api_key:
            # 支持多 API Key，逗号分隔
            self.api_keys = [key.strip() for key in self.api_key.split(',') if key.strip()]
        else:
            self.api_keys = []

        # 当前使用的 API Key 索引
        self.current_key_index = 0

    def _get_client(self, model: str = None) -> LLMClient:
        """获取 LLMClient 实例（model 为空时使用系统默认模型）"""
        config = LLMConfig(
            provider=self.provider,
            model=model if model else self.model,
            api_url=self.api_url,
            api_key=self.api_key,
            max_tokens=self.max_tokens,
            temperature=float(self.temperature) if self.temperature else None,
            timeout=self.timeout,
            proxy_enabled=self.proxy_enabled,
            http_proxy=self.http_proxy,
            https_proxy=self.https_proxy,
        )
        return LLMClient(config)

    def _normalize_max_tokens(self, max_tokens: int) -> int:
        model_limits = {
            "deepseek-v4-flash": 393216,
            "deepseek-v4-pro": 393216,
        }
        limit = model_limits.get(self.model)
        if limit is not None:
            return min(max_tokens, limit)
        return max_tokens

    async def chat_completion(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        task_type: str = None,
        prompt_template_name: str = None,
        novel_id: str = None,
        chapter_id: str = None,
        character_id: str = None
    ) -> Dict[str, Any]:
        """
        发送对话请求

        内部使用 LLMClient 实现底层调用。

        Returns:
            {
                "success": bool,
                "content": str,
                "error": str (optional)
            }
        """
        # 系统设置是所有 LLM 请求的统一输出上限；仅在未配置时使用调用方默认值。
        final_temperature = float(self.temperature) if self.temperature else temperature
        final_max_tokens = self.max_tokens if self.max_tokens is not None else max_tokens
        if final_max_tokens is None:
            final_max_tokens = DEFAULT_MAX_TOKENS
        final_max_tokens = self._normalize_max_tokens(final_max_tokens)
        # 任务类型模型三级分流：
        #   LIGHT_MODEL_TASKS（解析/提示词类）→ 轻量模型 qwen3:8b
        #   HEAVY_MODEL_TASKS（拆分/规划类重任务）→ 重模型 qwen3:30b-a3b
        #   其余 → 系统默认模型（self.model，可在系统配置中调整）
        if task_type in LIGHT_MODEL_TASKS:
            task_model = LIGHT_PARSE_MODEL
        elif task_type in HEAVY_MODEL_TASKS:
            task_model = HEAVY_MODEL
        else:
            task_model = self.model
        print(f"[chat_completion] url: {self.api_url}, model: {task_model}, temperature: {final_temperature}, max_tokens: {final_max_tokens} \n system_prompt: {system_prompt}\n user_content: {user_content}")

        client = self._get_client(task_model)
        return await client.chat_completion(
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=final_temperature,
            max_tokens=final_max_tokens,
            response_format=response_format,
            task_type=task_type,
            prompt_template_name=prompt_template_name,
            novel_id=novel_id,
            chapter_id=chapter_id,
            character_id=character_id
        )

    async def check_health(self) -> bool:
        """检查 LLM API 状态"""
        try:
            # 简单测试请求
            result = await self.chat_completion(
                system_prompt="You are a helpful assistant.",
                user_content="Hi",
                max_tokens=10
            )
            return result["success"]
        except Exception:
            return False

    # ============== 业务方法 ==============

    async def parse_novel_text(self, text: str, novel_id: str = None, source_range: str = None) -> Dict[str, Any]:
        """解析小说文本，提取角色、场景、分镜信息（支持章节范围）"""
        # 获取当前配置
        from app.core.config import get_settings
        settings = get_settings()
        system_prompt = settings.PARSE_CHARACTERS_PROMPT or DEFAULT_PARSE_CHARACTERS_PROMPT

        # 替换章节范围占位符
        if source_range:
            system_prompt = system_prompt.replace(CHAPTER_RANGE_PLACEHOLDER, source_range)
        else:
            system_prompt = system_prompt.replace(CHAPTER_RANGE_PLACEHOLDER, DEFAULT_CHAPTER_RANGE_DESCRIPTION)

        result = await self.chat_completion(
            system_prompt=system_prompt,
            user_content=f"请解析以下小说文本：\n\n{text}" + '''\n\n【硬性输出约束】只输出一个合法 JSON，禁止输出任何解释性文字、编号列表、markdown 代码块、加粗或标题格式。''',
            temperature=DEFAULT_TEMPERATURE,
            max_tokens=NOVEL_TEXT_MAX_LENGTH,
            response_format="json_object",
            task_type="parse_characters",
            prompt_template_name="系统配置角色解析提示词",
            novel_id=novel_id
        )

        if result["success"]:
            data = _normalize_llm_json(safe_parse_llm_json(result["content"], default=None), "characters")
            if not data:
                print(f"[parse_novel_text] JSON 解析失败，原始内容：{result['content'][:500]}")
                return {
                    "error": "JSON 解析失败",
                    "characters": [],
                    "scenes": [],
                    "shots": []
                }
            return {
                "characters": data.get("characters", []) if isinstance(data, dict) else (data if isinstance(data, list) else []),
                "scenes": data.get("scenes", []) if isinstance(data, dict) else [],
                "shots": data.get("shots", []) if isinstance(data, dict) else []
            }
        else:
            return {
                "error": result.get("error", "未知错误"),
                "characters": [],
                "scenes": [],
                "shots": []
            }

    async def generate_character_appearance(
        self,
        character_name: str,
        description: str,
        style: str = "anime",
        novel_id: str = None,
        character_id: str = None
    ) -> str:
        """生成角色外貌描述"""
        system_prompt = get_character_appearance_prompt(style)

        result = await self.chat_completion(
            system_prompt=system_prompt,
            user_content=f"角色名称：{character_name}\n角色描述：{description}\n\n请生成详细的外貌描述：",
            temperature=0.8,
            max_tokens=1000,
            task_type="generate_character_appearance",
            prompt_template_name=f"{style} 角色外貌描述提示词",
            novel_id=novel_id,
            character_id=character_id
        )

        if result["success"]:
            return clean_llm_response(result["content"]).strip()
        else:
            return DEFAULT_CHARACTER_APPEARANCE_FALLBACK.format(character_name=character_name)

    async def translate_appearance_to_en(self, appearance: str) -> str:
        """把中文外貌描述翻译为英文 AI 绘图提示词（flux 系列模型对英文理解远好于中文）。
        轻量任务，走 qwen3:8b（task_type=translate_appearance 已加入轻量模型分流）。
        """
        if not appearance or not any('\u4e00' <= ch <= '\u9fff' for ch in appearance):
            return appearance  # 空或已英文，无需翻译
        system_prompt = (
            "你是 AI 绘图提示词翻译助手。把中文角色外貌描述翻译成精简英文绘图提示词（SDXL 动漫模型用）。"
            "要求：1. 只保留核心视觉特征并按重要性排序：发型/发色、眼睛、服装款式与主色、年龄气质、姿态；"
            "2. 总长控制在 60 词以内，删除场景背景、装饰物、动作过程等与角色本体无关的修饰；"
            "3. 服装必须明确款式+主色（如 white hanfu with red accents、dark blue robes），不要泛化为 kimono；"
            "4. 保持形容词准确（'少女'译 young girl 而非 woman）；5. 只输出英文提示词，不要解释。"
        )
        result = await self.chat_completion(
            system_prompt=system_prompt,
            user_content=f"翻译以下角色外貌描述为英文绘图提示词：\n{appearance}",
            temperature=0.3,
            max_tokens=800,
            task_type="translate_appearance",
            prompt_template_name="外貌描述英文化"
        )
        if result["success"]:
            translated = clean_llm_response(result["content"]).strip()
            return translated if translated else appearance
        return appearance

    async def translate_prop_appearance_to_en(self, appearance: str) -> str:
        """把中文道具外观描述翻译为精简英文绘图提示词（SDXL 对中文理解差，必须英文化）。
        轻量任务走 qwen3:8b（task_type=translate_appearance 已在轻量模型分流）。
        物品导向：只保留形状/尺寸/材质/颜色/关键特征/风格，≤60 词。
        """
        if not appearance or not any('\u4e00' <= ch <= '\u9fff' for ch in appearance):
            return appearance  # 空或已英文，无需翻译
        system_prompt = (
            "你是 AI 绘图提示词翻译助手。把中文道具/物品外观描述翻译成精简英文绘图提示词（SDXL 模型用）。"
            "要求：1. 只保留核心视觉特征并按重要性排序：物品类别与形状、材质、颜色、关键细节、整体风格；"
            "2. 总长控制在 60 词以内，删除与外观无关的功能、背景、来历描述；"
            "3. 必须保留物品类别名词（如 jade plate、bamboo cage、amber insect），禁止只译特征丢类别；"
            "4. 保持形容词准确（'透明'译 translucent、'青玉'译 green jade）；5. 只输出英文提示词，不要解释。"
        )
        result = await self.chat_completion(
            system_prompt=system_prompt,
            user_content=f"翻译以下中文道具外观描述为英文绘图提示词：\n{appearance}",
            temperature=0.3,
            max_tokens=800,
            task_type="translate_appearance",
            prompt_template_name="道具外观英文化"
        )
        if result["success"]:
            translated = clean_llm_response(result["content"]).strip()
            return translated if translated else appearance
        return appearance

    async def translate_scene_setting_to_en(self, setting: str) -> str:
        """把中文场景设定翻译为精简英文绘图提示词（SDXL 对中文理解差，必须英文化）。
        场景导向：必须保留空间类型（indoor/outdoor/interior）、时段、关键结构元素。
        兼容历史 JSON 格式 setting（如 {"time":"傍晚","elements":[...]}），先提取再翻译。
        轻量任务走 qwen3:8b（task_type=translate_appearance 已在轻量模型分流）。
        """
        if not setting or not any('\u4e00' <= ch <= '\u9fff' for ch in setting):
            return setting  # 空或已英文，无需翻译
        # 兼容 JSON 格式 setting
        raw = setting.strip()
        if raw.startswith('{') and raw.endswith('}'):
            try:
                import json as _json
                obj = _json.loads(raw)
                parts = []
                for v in obj.values():
                    if isinstance(v, str):
                        parts.append(v)
                    elif isinstance(v, list):
                        parts.extend(str(x) for x in v)
                if parts:
                    raw = '，'.join(parts)
            except Exception:
                pass
        system_prompt = (
            "你是 AI 绘图提示词翻译助手。把中文场景环境设定翻译成精简英文绘图提示词（SDXL 模型用）。"
            "要求：1. 必须优先保留空间类型词（indoor/outdoor/interior/room/street/tent/courtyard 等）与时段（night/day/dusk）；"
            "2. 保留关键结构元素与材质（木质窗框 wooden window frame、青瓷花瓶 celadon vase、油灯 oil lamp 等），删除叙事、氛围主观词与镜头建议（如'建议采用低角度仰视视角'）；"
            "3. 总长控制在 70 词以内；4. 只输出英文提示词，不要解释。"
        )
        result = await self.chat_completion(
            system_prompt=system_prompt,
            user_content=f"翻译以下中文场景设定为英文绘图提示词：\n{raw}",
            temperature=0.3,
            max_tokens=900,
            task_type="translate_appearance",
            prompt_template_name="场景设定英文化"
        )
        if result["success"]:
            translated = clean_llm_response(result["content"]).strip()
            return translated if translated else setting
        return setting

    async def expand_video_prompt(self, prompt: str, duration: int = DEFAULT_VIDEO_DURATION) -> str:
        """扩写视频生成提示词"""
        user_content = f"""原始提示词：{prompt}
视频时长：{duration}秒

请扩写为详细的视频生成提示词："""

        result = await self.chat_completion(
            system_prompt=DEFAULT_VIDEO_PROMPT_EXPAND_SYSTEM,
            user_content=user_content,
            temperature=DEFAULT_TEMPERATURE,
            max_tokens=2000
        )

        if result["success"]:
            return clean_llm_response(result["content"]).strip()
        else:
            return prompt

    async def generate_scene_setting(
        self,
        scene_name: str,
        description: str,
        style: str = "anime",
        novel_id: str = None
    ) -> str:
        """生成场景设定（环境设置）

        Args:
            scene_name: 场景名称
            description: 场景描述
            style: 画风风格
            novel_id: 小说 ID

        Returns:
            场景设定字符串（用于 AI 绘图的环境描述）
        """
        system_prompt = get_scene_setting_prompt(style)

        result = await self.chat_completion(
            system_prompt=system_prompt,
            user_content=f"场景名称：{scene_name}\n场景描述：{description}\n\n请生成详细的环境设定描述：",
            temperature=0.8,
            max_tokens=1000,
            task_type="generate_scene_setting",
            prompt_template_name=f"{style} 场景设定提示词",
            novel_id=novel_id
        )

        if result["success"]:
            return clean_llm_response(result["content"]).strip()
        else:
            return DEFAULT_SCENE_SETTING_FALLBACK.format(scene_name=scene_name)

    async def generate_prop_appearance(
        self,
        prop_name: str,
        description: str,
        style: str = "anime",
        novel_id: str = None
    ) -> str:
        """生成道具外观描述

        Args:
            prop_name: 道具名称
            description: 道具描述
            style: 画风风格
            novel_id: 小说 ID

        Returns:
            道具外观描述字符串（用于 AI 绘图）
        """
        system_prompt = get_prop_appearance_prompt(style)

        result = await self.chat_completion(
            system_prompt=system_prompt,
            user_content=f"道具名称：{prop_name}\n道具描述：{description}\n\n请生成详细的外观描述：",
            temperature=0.8,
            max_tokens=1000,
            task_type="generate_prop_appearance",
            prompt_template_name=f"{style} 道具外观提示词",
            novel_id=novel_id
        )

        if result["success"]:
            return clean_llm_response(result["content"]).strip()
        else:
            return DEFAULT_PROP_APPEARANCE_FALLBACK.format(prop_name=prop_name)


    async def parse_scenes(
        self,
        novel_id: str,
        chapter_content: str,
        chapter_title: str = "",
        prompt_template: str = None,
        prompt_template_name: str = None
    ) -> Dict[str, Any]:
        """解析场景信息

        Args:
            novel_id: 小说 ID
            chapter_content: 章节内容
            chapter_title: 章节标题
            prompt_template: 场景解析提示词模板

        Returns:
            {"scenes": [{"name": "", "description": "", "setting": ""}]}
        """
        system_prompt = prompt_template or DEFAULT_PARSE_SCENES_PROMPT

        user_content = f"""章节标题：{chapter_title}

章节内容：
{chapter_content[:CHAPTER_CONTENT_MAX_LENGTH]}

请解析以上文本中的场景信息。

【硬性输出约束】只输出一个合法 JSON，禁止输出任何解释性文字、编号列表、markdown 代码块、加粗或标题格式。"""

        result = await self.chat_completion(
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=DEFAULT_TEMPERATURE,
            max_tokens=CHAPTER_CONTENT_MAX_LENGTH,
            response_format="json_object",
            task_type="parse_scenes",
            prompt_template_name=prompt_template_name or ("自定义场景解析提示词" if prompt_template else "默认场景解析提示词"),
            novel_id=novel_id
        )

        if result["success"]:
            data = _normalize_llm_json(safe_parse_llm_json(result["content"], default=None), "scenes")
            if not data:
                print(f"[parse_scenes] JSON 解析失败，原始内容：{result['content'][:500]}")
                return {"scenes": []}
            return {
                "scenes": data.get("scenes", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            }
        else:
            return {
                "error": result.get("error", "未知错误"),
                "scenes": []
            }

    async def parse_props(
        self,
        text: str,
        prompt_template: str = None,
        prompt_template_name: str = None
    ) -> Dict[str, Any]:
        """
        解析小说文本中的道具信息

        Args:
            text: 小说文本
            prompt_template: 提示词模板（可选）

        Returns:
            道具解析结果
        """
        # 使用提供的模板或默认模板
        if not prompt_template:
            import os
            template_path = os.path.join(os.path.dirname(__file__), '..', 'prompt_templates', 'prop_parse.txt')
            if os.path.exists(template_path):
                with open(template_path, "r", encoding="utf-8") as f:
                    prompt_template = f.read()

        result = await self.chat_completion(
            system_prompt=prompt_template,
            user_content=text + "\n\n【硬性输出约束】只输出一个合法 JSON，禁止输出任何解释性文字、编号列表、markdown 代码块、加粗或标题格式。",
            temperature=0.3,
            max_tokens=4000,
            response_format="json_object",
            task_type="parse_props",
            prompt_template_name=prompt_template_name or ("自定义道具解析提示词" if prompt_template else "默认道具解析提示词")
        )

        if result["success"]:
            data = _normalize_llm_json(safe_parse_llm_json(result["content"], default=None), "props")
            if not data:
                print(f"[parse_props] JSON 解析失败，原始内容：{result['content'][:500]}")
                return {"props": [], "error": "JSON 解析失败"}
            return {
                "props": data.get("props", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            }
        else:
            return {
                "error": result.get("error", "未知错误"),
                "props": []
            }

    async def generate(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        response_format: Optional[str] = None
    ) -> str:
        """生成文本（兼容旧接口）"""
        result = await self.chat_completion(
            system_prompt=system_prompt,
            user_content=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format
        )
        return result.get("content", "")

    async def split_chapter_with_prompt(
        self,
        chapter_title: str,
        chapter_content: str,
        prompt_template: str,
        word_count: int = 100,
        character_names: List[str] = None,
        scene_names: List[str] = None,
        prop_names: List[str] = None,
        style: str = "anime style, high quality, detailed",
        novel_id: str = None,
        chapter_id: str = None,
        prompt_template_name: str = None
    ) -> Dict[str, Any]:
        """使用自定义提示词将章节拆分为分镜数据结构"""

        # 替换提示词模板中的占位符
        system_prompt = prompt_template.replace(
            "{每个分镜对应拆分故事字数}", str(word_count)
        ).replace(
            "{图像风格}", style
        ).replace(
            "##STYLE##", style
        )

        # 构建 allowed_characters 行
        # 去重 + 截断：白名单过长或含重复项时，本地模型（qwen3 系）会失控
        # （重复输出角色名、输出示例性 JSON 而非分镜）。
        # 实测：90 个含重复项白名单触发模型失控；去重截断到 30 个后正常输出 shots。
        allowed_characters_line = ""
        dedup_chars = _dedup_limited(character_names)
        if dedup_chars:
            allowed_characters_line = f"allowed_characters: {', '.join(dedup_chars)}\n"

        # 构建 allowed_scenes 行
        allowed_scenes_line = ""
        dedup_scenes = _dedup_limited(scene_names)
        if dedup_scenes:
            allowed_scenes_line = f"allowed_scenes: {', '.join(dedup_scenes)}\n"

        # 构建 allowed_props 行
        allowed_props_line = ""
        dedup_props = _dedup_limited(prop_names)
        if dedup_props:
            allowed_props_line = f"allowed_props: {', '.join(dedup_props)}\n"

        # 合并白名单行
        whitelist_lines = ""
        if allowed_characters_line or allowed_scenes_line or allowed_props_line:
            whitelist_lines = allowed_characters_line + allowed_scenes_line + allowed_props_line + "\n"

        user_content = f"""{whitelist_lines}章节标题：{chapter_title}

章节内容：
{chapter_content[:CHAPTER_CONTENT_MAX_LENGTH]}

请将以上章节内容拆分为分镜数据结构。"""

        result = await self.chat_completion(
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=DEFAULT_TEMPERATURE,
            max_tokens=CHAPTER_CONTENT_MAX_LENGTH,
            response_format="json_object",
            task_type="split_chapter",
            prompt_template_name=prompt_template_name or "分镜拆分提示词模板",
            novel_id=novel_id,
            chapter_id=chapter_id
        )

        if result["success"]:
            content = result["content"]
            data = safe_parse_llm_json(content)

            if not data:
                print(f"[split_chapter] JSON 解析失败")
                print(f"[split_chapter] 原始内容: {result['content'][:500]}")
                return {
                    "error": "JSON 解析失败",
                    "chapter": chapter_title,
                    "characters": [],
                    "scenes": [],
                    "shots": []
                }

            # 确保返回格式正确
            return {
                "chapter": data.get("chapter", chapter_title),
                "characters": data.get("characters", []),
                "scenes": data.get("scenes", []),
                "props": data.get("props", []),
                "shots": data.get("shots", [])
            }
        else:
            return {
                "error": result.get("error", "未知错误"),
                "chapter": chapter_title,
                "characters": [],
                "scenes": [],
                "shots": []
            }


def get_llm_service() -> LLMService:
    """获取 LLM 服务实例"""
    return LLMService()


def _dedup_limited(names, limit: int = 30) -> list:
    """白名单去重（保序）并截断到 limit 个。

    本地模型（qwen3 系）对超长白名单敏感：重复项会诱导模型反复输出
    同一批角色名（吃掉全部输出预算），带括号的别名（如"方源（古月族少年蛊师）"）
    会诱导模型跑题输出示例性 JSON。去重 + 截断后模型可稳定输出 shots。
    """
    if not names:
        return []
    seen = set()
    out = []
    for name in names:
        name = str(name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= limit:
            break
    return out
