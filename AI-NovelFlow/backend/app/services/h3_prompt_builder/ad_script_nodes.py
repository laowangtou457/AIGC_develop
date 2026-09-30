"""广告剧本生成器 + Contex-Loop plan JSON 组装 — 纯标准库实现。"""

import json
import math
import os

try:
    from .nodes import RULES_DIR, call_llm, load_config, read_text_file
    from .manju_nodes import build_manju_system_prompt, resolve_llm_config
except ImportError:
    from nodes import RULES_DIR, call_llm, load_config, read_text_file
    from manju_nodes import build_manju_system_prompt, resolve_llm_config

H3_LENGTH_MIN = 5
H3_LENGTH_MAX = 3592
H3_GRID = 17
H3_REMAINDER = 5

SCRIPT_COUNT_OPTIONS = [1, 2, 3, 4, 5]
DURATION_OPTIONS = [15, 30, 45, 60]
SCENE_COUNT_OPTIONS = [2, 3, 4, 5, 6]
STYLE_TENDENCIES = ["自动", "搞笑", "悬疑", "温情", "抽象"]
SCALE_LEVELS = ["保守", "标准", "抽象"]


def h3_length_for_seconds(seconds):
    """把秒数换算为 H3 合法原始帧数（17k+5 网格），向上取整。"""
    seconds = max(0.1, float(seconds))
    requested = int(math.ceil(seconds * 24))
    length = requested
    while length % H3_GRID != H3_REMAINDER:
        length += 1
    return max(H3_LENGTH_MIN, min(H3_LENGTH_MAX, length))


def distribute_scene_seconds(total_seconds, scene_count):
    """均分总时长，最后一场吸收余数。"""
    total_seconds = max(5, int(total_seconds or 30))
    scene_count = max(1, int(scene_count or 1))
    base = total_seconds // scene_count
    last = total_seconds - base * (scene_count - 1)
    return [base] * (scene_count - 1) + [last]


def build_plan_json(prefix_lines, scene_prompts, scene_seconds, base_seed=20260827):
    """把全局规则与各场 prompt 组装成 Contex-Loop plan JSON 字符串。"""
    prefix_lines = [str(x) for x in (prefix_lines or []) if str(x).strip()]
    scene_prompts = list(scene_prompts or [])
    scene_seconds = list(scene_seconds or [])
    if not scene_prompts:
        raise ValueError("缺少场景提示词")
    if len(scene_prompts) != len(scene_seconds):
        raise ValueError("场景数量与时长数量不一致")
    shots = []
    for idx, (prompt_lines, seconds) in enumerate(zip(scene_prompts, scene_seconds), start=1):
        shots.append({
            "id": "scene_%02d" % idx,
            "prompt": [str(x) for x in prompt_lines],
            "duration_seconds": int(seconds),
            "seed": (int(base_seed) + idx) % (2 ** 64),
        })
    plan = {
        "prompt_prefix": prefix_lines,
        "defaults": {"duration_seconds": int(scene_seconds[0]), "steps": 20},
        "shots": shots,
    }
    return json.dumps(plan, ensure_ascii=False, indent=2)


def _strip_fence(text):
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_scripts(raw):
    """解析批量生成节点的 LLM 输出：JSON 数组 [{"title","script"}]，容忍代码围栏。"""
    text = _strip_fence(raw)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    scripts = []
    for item in data:
        if not isinstance(item, dict):
            return None
        title = str(item.get("title", "")).strip()
        script = str(item.get("script", "")).strip()
        if not title or not script:
            return None
        scripts.append({"title": title, "script": script})
    return scripts


def build_preview(scripts):
    """生成供人选择的一行一个剧本的预览文本。"""
    lines = []
    for idx, item in enumerate(scripts):
        first_line = item["script"].splitlines()[0] if item["script"].splitlines() else ""
        lines.append("[%d] %s｜%s" % (idx, item["title"], first_line))
    return "\n".join(lines)


def select_script(scripts_json, index):
    """按序号取剧本，返回 (script, title)；失败返回 (None, None)。"""
    try:
        data = json.loads(scripts_json or "[]")
    except Exception:
        return None, None
    if not isinstance(data, list) or not data:
        return None, None
    try:
        item = data[int(index)]
    except (IndexError, TypeError, ValueError):
        return None, None
    if not isinstance(item, dict):
        return None, None
    return str(item.get("script", "")).strip(), str(item.get("title", "")).strip()


def parse_scene_pack(raw):
    """解析转换节点的 LLM 输出，返回 (prefix_lines, prompts)；失败返回 None。"""
    text = _strip_fence(raw)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    prefix = data.get("prompt_prefix") or data.get("global_prompt") or []
    if isinstance(prefix, str):
        prefix = [prefix]
    prefix = [str(x) for x in prefix if str(x).strip()]
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return None
    prompts = []
    for scene in scenes:
        if isinstance(scene, str):
            prompts.append([scene])
            continue
        if not isinstance(scene, dict):
            return None
        p = scene.get("prompt") or scene.get("prompt_lines") or []
        if isinstance(p, str):
            p = [p]
        p = [str(x) for x in p]
        if not p:
            return None
        prompts.append(p)
    return prefix, prompts


class AdScriptBatch:
    """广告剧本：批量生成。一次 LLM 调用产出 N 个剧本。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "business": ("STRING", {"multiline": True, "default": "例如：拼豆手工店"}),
                "ad_copy": ("STRING", {"multiline": True, "default": "拼豆团购有优惠"}),
                "script_count": (SCRIPT_COUNT_OPTIONS, {"default": 3}),
                "duration_seconds": (DURATION_OPTIONS, {"default": 30}),
            },
            "optional": {
                "shop_name": ("STRING", {"default": ""}),
                "target_audience": ("STRING", {"default": "大众"}),
                "style_tendency": (STYLE_TENDENCIES, {"default": "自动"}),
                "scale_level": (SCALE_LEVELS, {"default": "标准"}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": ""}),
                "base_url": ("STRING", {"default": ""}),
                "llm_config": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("scripts_json", "preview", "script_count")
    FUNCTION = "build"
    CATEGORY = "MiniMax H3 / 广告剧本"

    def build(self, business, ad_copy, script_count=3, duration_seconds=30, shop_name="",
              target_audience="大众", style_tendency="自动", scale_level="标准",
              api_key="", model="", base_url="", llm_config=""):
        business = (business or "").strip()
        ad_copy = (ad_copy or "").strip()
        if not business or not ad_copy:
            msg = "请输入主营业务和广告语。"
            return (msg, msg, 0)
        count = max(1, min(5, int(script_count or 3)))
        key, model_name, endpoint, temperature, config, warning = resolve_llm_config(
            api_key, model, base_url, llm_config
        )
        if not key:
            msg = "未配置 API Key：请在节点 api_key 输入框或 config.json 中填写。"
            return (msg, msg, 0)
        system = build_manju_system_prompt("ad_script.txt", "")
        user_msg = (
            "【需求】\n主营业务：" + business
            + "\n广告语：" + ad_copy
            + "\n店名：" + (shop_name or "未提供")
            + "\n目标人群：" + (target_audience or "大众")
            + "\n风格倾向：" + (style_tendency or "自动")
            + "\n尺度档位：" + (scale_level or "标准")
            + "\n目标时长：" + str(duration_seconds) + " 秒"
            + "\n剧本数量：" + str(count)
            + "\n\n请按要求输出剧本 JSON 数组。"
        )
        temp = temperature if temperature is not None else config.get("manju_temperature", 0.2)
        try:
            raw = call_llm(
                endpoint, key, model_name, system, user_msg,
                temperature=temp,
                max_tokens=config.get("max_tokens", 32768),
            )
        except Exception as exc:
            msg = "LLM 调用失败：%s" % (exc,)
            return (msg, msg, 0)
        scripts = parse_scripts(raw)
        if scripts is None:
            msg = "剧本解析失败，请重试。模型输出：%s" % raw[:300]
            return (msg, msg, 0)
        if len(scripts) > count:
            scripts = scripts[:count]
        scripts_json = json.dumps(scripts, ensure_ascii=False, indent=2)
        return (scripts_json, build_preview(scripts), len(scripts))


class AdScriptSelect:
    """广告剧本：选择。按序号从列表中取一个剧本。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "scripts_json": ("STRING", {"multiline": True, "default": "[]"}),
                "index": ("INT", {"default": 0, "min": 0, "max": 20, "step": 1}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("selected_script", "selected_title")
    FUNCTION = "build"
    CATEGORY = "MiniMax H3 / 广告剧本"

    def build(self, scripts_json, index=0):
        script, title = select_script(scripts_json, index)
        if script is None:
            msg = "剧本列表为空或序号越界，请先运行批量生成节点。"
            return (msg, "")
        return (script, title)


class AdScriptToChainPlan:
    """广告剧本：转 Chain Plan JSON。LLM 产提示词，程序组装 JSON。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "script": ("STRING", {"multiline": True, "default": "粘贴选中的剧本"}),
                "duration_seconds": (DURATION_OPTIONS, {"default": 30}),
                "scene_count": (SCENE_COUNT_OPTIONS, {"default": 3}),
            },
            "optional": {
                "base_seed": ("INT", {"default": 20260827, "min": 0, "max": 2 ** 64 - 1}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": ""}),
                "base_url": ("STRING", {"default": ""}),
                "llm_config": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("plan_json", "plan_json_preview")
    FUNCTION = "build"
    CATEGORY = "MiniMax H3 / 广告剧本"

    def build(self, script, duration_seconds=30, scene_count=3, base_seed=20260827,
              api_key="", model="", base_url="", llm_config=""):
        script = (script or "").strip()
        if not script:
            msg = "请输入剧本（可接剧本选择节点）。"
            return (msg, msg)
        total = int(duration_seconds or 30)
        count = max(1, min(6, int(scene_count or 3)))
        scene_seconds = distribute_scene_seconds(total, count)
        key, model_name, endpoint, temperature, config, warning = resolve_llm_config(
            api_key, model, base_url, llm_config
        )
        if not key:
            msg = "未配置 API Key：请在节点 api_key 输入框或 config.json 中填写。"
            return (msg, msg)
        system = build_manju_system_prompt("ad_chain_plan.txt", "")
        user_msg = (
            "【剧本】\n" + script
            + "\n\n【场次配置】共 " + str(count) + " 场，各场时长（秒）："
            + json.dumps(scene_seconds, ensure_ascii=False)
            + "\n\n请把剧本拆成 " + str(count) + " 场，输出 prompt_prefix 与每场 prompt。"
        )
        temp = temperature if temperature is not None else config.get("manju_temperature", 0.2)
        try:
            raw = call_llm(
                endpoint, key, model_name, system, user_msg,
                temperature=temp,
                max_tokens=config.get("max_tokens", 32768),
            )
        except Exception as exc:
            msg = "LLM 调用失败：%s" % (exc,)
            return (msg, msg)
        pack = parse_scene_pack(raw)
        if pack is None:
            msg = "场景提示词解析失败，请重试。模型输出：%s" % raw[:300]
            return (msg, msg)
        prefix, prompts = pack
        if len(prompts) != count:
            msg = "LLM 返回 %d 场，但配置要求 %d 场，请重试。" % (len(prompts), count)
            return (msg, msg)
        try:
            plan_json = build_plan_json(prefix, prompts, scene_seconds, base_seed)
        except Exception as exc:
            msg = "plan JSON 组装失败：%s" % (exc,)
            return (msg, msg)
        pretty = json.dumps(json.loads(plan_json), ensure_ascii=False, indent=2)
        if warning:
            pretty = warning + "\n" + pretty
        return (plan_json, pretty)
