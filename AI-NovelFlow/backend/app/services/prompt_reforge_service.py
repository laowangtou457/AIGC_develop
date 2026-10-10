# -*- coding: utf-8 -*-
"""
提示词提取与重构 服务
=======================

功能：输入 提示词 / 剧本 / 小说 → 按「导演模型」提取剧本节拍 → 重构为
【AI 工具可生成级】的逐镜提示词集（MiniMax H3 / Seedance / Kling / Veo / 即梦）。

导演模型抽象（参考 AI 视频提示词导演技能安装的提示词升级经验）：
  - h3_ref2va      : MiniMax 官方 Ref2VA 六段式（subject_definitions/summary/retention_analysis/
                     detailed_description/overall_soundscape/non_diegetic_music）
  - h3_i2v         : MiniMax 官方 I2VA 三字段（首帧对齐指令 + integrated_multimodal_description/
                     overall_soundscape/non_diegetic_music）
  - martial_arts   : 武术指导导演（动作戏 7 原则/人物清点/姿态配额/天气氛围/场景尺度/武器约束）
  - general_cinematic : 通用影视导演（主体构图/动作表演/场景道具/运镜/光线氛围/技术规格）

执行链路（全 8b 轻量模型，串行防卡死）：
  ① 提取（Extract）: 输入文本 → 结构化节拍 JSON（角色/场景/动作/运镜/对白/氛围）
  ② 重构（Reforge）: 导演模型规则 + 节拍 JSON → 逐镜提示词正文
  ③ 组装（Pack）  : 按目标平台模板组装 → prompts.md + payloads/*.json + report.json 落盘

产物目录：<AI-NovelFlow>/backend/data/prompt_reforge/<task_id>/
"""
import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.database import SessionLocal
from app.models.prompt_reforge_history import PromptReforgeHistory
from app.services.llm_service import LLMService

# ── 常量 ──
H3_RULES_DIR = Path(r"F:\Develop\NewAIProductionWorkflow\ManjuToSplitFrameAndProperty\docs\references")
REFORGE_DATA_DIR = Path(r"F:\Develop\NewAIProductionWorkflow\AI-NovelFlow\backend\data\prompt_reforge")

MAX_CHAPTER_CHARS = 10000      # 智能断章：每章不超过 1 万字（中文字符按 len() 计）
MAX_CHAPTERS = 30              # 断章上限：超出部分不再处理，并在报告/产物中说明
MAX_BEATS = 12                 # 每章提取节拍上限（超出按重要度取前 N，防止输出截断）
MIN_DURATION, MAX_DURATION = 4, 15   # H3 单段时长约束

# 目标平台展示名
PLATFORM_LABELS = {
    "minimax_h3": "MiniMax H3",
    "seedance": "Seedance",
    "kling": "Kling",
    "veo": "Veo",
    "jimeng": "即梦",
}
DEFAULT_PLATFORMS = ["minimax_h3", "seedance", "kling", "veo", "jimeng"]

# ═══════════════════════════ 导演模型抽象 ═══════════════════════════

def _read_rules(*names: str) -> str:
    """读取官方提示词规则存档（存在则加载，缺失时返回空）"""
    parts = []
    for name in names:
        p = H3_RULES_DIR / name
        if p.exists():
            try:
                parts.append(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return "\n\n".join(parts)


def _build_ref2va_rules() -> str:
    """H3 Ref2VA 六段式导演规则：官方 ref-en.txt + 工程控制要点"""
    official = _read_rules("ref-en.txt")
    return "\n".join([
        "你是资深 AI 视频提示词导演，精通 MiniMax H3 官方 Ref2VA 六段式提示词规范。",
        "必须严格按以下六段结构输出每一镜提示词（结构词全英文，内容可按输入语言）：",
        "1. subject_definitions: <Subject N> 主体定义（角色/场景，含外观来源）。",
        "2. summary: [任务类型] 一句话概括本镜（如 [reference generation]）。",
        "3. retention_analysis: <Subject N> (appears in [Shot N]): fully_preserved - 保持外观一致性。",
        "4. detailed_description: 风格开场句 + [Shot N] + 主体/机位/动作/光线/对白；后续镜头用 At MM:SS.mmm 时间戳。",
        "5. overall_soundscape: 环境音 1-4 句（不重复对白/音乐）。",
        "6. non_diegetic_music: 配乐 1-3 句（禁止抽象情绪词，如 sad/tense 不可用）。",
        "对白必须用 <d>[中文|English] 原文</d> 逐字保留；画面禁止出现字幕/文字/水印（写明 no subtitles）。",
        "首镜无时间戳，后续镜头 At MM:SS.mmm, the shot cuts to ...。",
        "每镜时长限制 4-15 秒，画幅 9:16。",
        "",
        "【官方规范参考】",
        (official[:6000] if official else "(官方规范文件缺失，按上述要点执行)"),
    ])


def _build_i2v_rules() -> str:
    """H3 I2VA 三字段导演规则：官方 base-en.txt + 控制要点"""
    official = _read_rules("base-en.txt")
    return "\n".join([
        "你是资深 AI 视频提示词导演，精通 MiniMax H3 官方 I2VA 三字段提示词规范（图生视频）。",
        "每镜提示词必须严格按以下结构输出：",
        "第一行固定对齐指令：For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot N]) is fully referenced.",
        "（空行）",
        "integrated_multimodal_description: 主体/动作推进/结果 + 机位/光线，强调首帧锚定 → 动作演进。",
        "overall_soundscape: 环境音 1-4 句。",
        "non_diegetic_music: 配乐 1-3 句（禁止抽象情绪词）。",
        "对白必须用 <d>[中文|English] 原文</d>；画面禁止字幕/文字/水印。",
        "每镜时长 4-15 秒，画幅 9:16。",
        "",
        "【官方规范参考】",
        (official[:6000] if official else "(官方规范文件缺失，按上述要点执行)"),
    ])


def _build_martial_arts_rules() -> str:
    """武术指导导演规则（借鉴 martial-arts-director-cy 的招式编排与镜头语言）"""
    return "\n".join([
        "你是资深武术指导（借鉴袁和平/成龙/甄子丹/John Wick 的招式节奏与镜头语言），负责把输入重构为武打镜头提示词集。",
        "【编排规则】",
        "- 动作戏 7 原则：清晰(谁打谁/用什么/命中哪/结果)、场景几何(方位可追踪)、赌注、动机、编排有创意、脆弱(真实威胁)、后果(真实代价)。",
        "- 人物清点（强制）：先识别全部出场人物（主角/对手/配角），数量必须与输入一致，禁止漏人、禁止虚化剪影；每个角色给出姓名/身份/体型/服装/武器/武术体系。",
        "- 姿态配额（N=镜头总数）：≥⌈N/5⌉ 低桩(半蹲/沉马/跪步)、≥⌈N/8⌉ 腾空高位、≥⌈N/5⌉ 转身或背身、≥⌈N/8⌉ 近景特写，其余自由站姿。",
        "- 第 1 镜起势与末镜收势必须仪式感且明显不同（抱拳礼/持剑诀/单膝半跪/立掌当胸/横兵于膝等）。",
        "- 每招视觉差异化：朝向/重心/发力部位不重复；keypose 是运动中瞬间，禁止已完成定格。",
        "- 天气氛围（强制）：每镜写明 场景+时段+天气+光感+氛围；服装材质/颜色/配饰必须与天气协调。",
        "- 场景尺度（强制）：写明尺度与纵深，量化表达（如'吊桥横跨数百米峡谷，两端铁索锚固崖壁，桥身狭长悬垂、木板稀疏、随风摇晃，桥下深谷云雾缭绕不见底'），禁止大桥画成木板。",
        "- 武器约束（强制）：每个角色明确 weapon_length（如'单刀刃长约二尺五'）；每镜写清 composition（主体位置/景别/镜头角度/空间层次）。",
        "- 经典动作参考：可用通用动作特征（黄飞鸿式沉桥架式/叶问式寸劲短打/大鹏展翅起手/单臂背刀蓄势等），不写真实角色名。",
        "每镜输出：镜头编号/时长/画幅/主体与构图/动作与招式/运镜/光线氛围/音效。",
    ])


def _build_general_cinematic_rules() -> str:
    """通用影视导演规则（Seedance / Kling / Veo / 即梦 通用可生成级）"""
    return "\n".join([
        "你是资深影视提示词导演，精通各 AI 视频生成平台（Seedance/Kling/Veo/即梦）的提示词写法，把输入重构为【AI 工具可生成级】逐镜提示词。",
        "【每镜提示词必须包含六要素】",
        "1. subject/composition：主体是谁、位置、景别（远景/全景/中景/近景/特写）、画面构图。",
        "2. action/performance：具体动作与表演细节（动词明确、有起承转合，禁止'做出动作'这类空话）。",
        "3. scene/props：场景环境 + 关键道具 + 空间层次与尺度。",
        "4. camera/motion：机位（正面/侧面/低角度/航拍等）+ 运镜（推/拉/摇/移/跟/环绕，含幅度与速度）。",
        "5. lighting/atmosphere：光线（晨光/暮色/霓虹/烛光等）+ 色调 + 氛围。",
        "6. technical/spec：时长 + 画幅 + 画质要求。",
        "【硬性规则】",
        "- 人物性别/年龄/服装必须写明（避免模型把女性生成男性、少女生成大妈）。",
        "- 多人场景必须逐一列出出场人物并保持数量一致，禁止漏人。",
        "- 动作必须具体可执行，按时间顺序推进（起势→交锋→结果）。",
        "- 禁止出现抽象情感词（如'紧张的气氛'），改为可视觉化的描述（'雨滴在刀身上滑落，呼吸可闻'）。",
        "- 每镜时长 4-15 秒；镜头间动作与场景保持连贯（可追踪的几何方位）。",
        "输出格式：Shot N（时长Xs｜9:16）→ 六要素逐行。",
    ])


DIRECTOR_MODELS = {
    "h3_ref2va": {
        "name": "H3 Ref2VA 六段式（MiniMax 官方）",
        "description": "输出可直接提交 MiniMax H3 的 Ref2VA 六段式逐镜提示词（参考图模式）",
        "build_rules": _build_ref2va_rules,
    },
    "h3_i2v": {
        "name": "H3 I2VA 三字段（MiniMax 官方）",
        "description": "输出可直接提交 MiniMax H3 的 I2VA 三字段逐镜提示词（图生视频模式）",
        "build_rules": _build_i2v_rules,
    },
    "martial_arts": {
        "name": "武术指导（动作片导演）",
        "description": "输出武打镜头提示词集：招式编排/姿态配额/天气氛围/场景尺度/武器约束",
        "build_rules": _build_martial_arts_rules,
    },
    "general_cinematic": {
        "name": "通用影视导演（多平台）",
        "description": "输出适配 Seedance/Kling/Veo/即梦 的通用可生成级逐镜提示词",
        "build_rules": _build_general_cinematic_rules,
    },
}

DEFAULT_DIRECTOR_MODEL = "h3_ref2va"


def get_director_model(key: str) -> dict:
    """获取导演模型定义（不存在时回退默认）"""
    model = DIRECTOR_MODELS.get(key)
    if not model:
        model = DIRECTOR_MODELS[DEFAULT_DIRECTOR_MODEL]
    return model


# ═══════════════════════════ 工具函数 ═══════════════════════════

# ── 智能断章 ─────────────────────────────────────────────────────
# 显式章节标记：第N章/回/幕/卷/部/场、Chapter N、Scene N、中文序数小标题
_CHAPTER_MARK_RE = re.compile(
    r"^\s*(?:第\s*[0-9一二三四五六七八九十百千零]+\s*[章节回幕卷部场]|"
    r"Chapter\s+\d+|Scene\s+\d+|"
    r"[一二三四五六七八九十]{1,3}\s*[、.．])",
    re.MULTILINE | re.IGNORECASE,
)


def _split_by_marks(text: str) -> list:
    """按显式章节标记切分（标记行保留在所属章首）"""
    matches = list(_CHAPTER_MARK_RE.finditer(text))
    if len(matches) <= 1:
        return [text.strip()]
    chapters = []
    for idx, m in enumerate(matches):
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            chapters.append(chunk)
    return chapters


def _split_by_paragraphs(text: str, max_chars: int) -> list:
    """按段落（空行/行首非空白）累积切分，每块 ≤ max_chars，单段超长按字符硬切"""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=\S)", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:max_chars])
            p = p[max_chars:]
        if not cur or len(cur) + len(p) + 1 <= max_chars:
            cur = (cur + "\n" + p).strip() if cur else p
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks or [text[:max_chars]]


def _chapter_heading(chunk: str, index: int) -> str:
    """从章首行提取标题（无标记时回退『第N部分』）。

    修复：原文首行常为『第十章：天有不测风云，炼蛊别具艰辛』这类带
    『第X章』前缀的标题；此处剥离前缀只保留冒号/分隔符后的标题正文，
    避免后续 _pack_outputs 拼接成『第 1 章：第十章：…』双重前缀。
    """
    first = next((l.strip() for l in chunk.splitlines() if l.strip()), "")
    if re.match(r"^第\s*[0-9一二三四五六七八九十百千零]+\s*[章节回幕卷部场]", first):
        # 剥离『第X章』前缀及紧邻分隔符（第X章 / 第X章： / 第X章、等）
        rest = re.sub(
            r"^第\s*[0-9一二三四五六七八九十百千零]+\s*[章节回幕卷部场]\s*[:：、.．,，\-—\s]*",
            "", first,
        ).strip()
        return (rest or first)[:40]
    if re.match(r"^Chapter\s+\d+|^Scene\s+\d+", first, re.IGNORECASE):
        return first[:40]
    return f"第{index}部分"


def _smart_split_chapters(
    text: str,
    max_chars: int = MAX_CHAPTER_CHARS,
    max_chapters: int = MAX_CHAPTERS,
) -> list:
    """
    智能断章：长文本按 章/回/幕/卷/部/场 显式标记优先切分，
    超长块按段落二次切分，确保每章 ≤ max_chars（默认 1 万字）。
    返回：[{"index": 1, "heading": "…", "text": "…"}, ...]
    """
    text = text or ""
    # 剥 BOM：Windows 记事本/工具导出的 UTF-8 文件常带 \ufeff，
    # 若不剥除会导致首个显式章节标记（第N章）匹配失败、整篇回退段落切分。
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    text = text.strip()
    if not text:
        return []
    # 1) 显式章节标记切
    parts = _split_by_marks(text)
    # 2) 超长块按段落二次切
    chapters = []
    for part in parts:
        if len(part) <= max_chars:
            chapters.append(part)
        else:
            chapters.extend(_split_by_paragraphs(part, max_chars))
    # 3) 碎片合并：<300 字并入上一章，避免大量碎块
    merged = []
    for ch in chapters:
        if merged and len(ch) < 300 and len(merged[-1]) + len(ch) <= max_chars + 1000:
            merged[-1] = merged[-1] + "\n" + ch
        else:
            merged.append(ch)
    # 4) 章节上限保护（避免极端长文产生数百个子任务）
    if len(merged) > max_chapters:
        kept = merged[:max_chapters]
        kept.append(
            f"（提示：输入文本超出断章上限 {max_chapters} 章，"
            f"后续 {len(merged) - max_chapters} 段内容未处理；请拆分输入或分批提交）"
        )
        merged = kept
    # 5) 生成带标题的结构
    result = []
    for i, ch in enumerate(merged, 1):
        result.append({"index": i, "heading": _chapter_heading(ch, i), "text": ch})
    return result


def _update_state(task_id: str, *, status=None, stage=None, error=None, output_md=None, report=None):
    """状态落库（每次新建 Session，避免 detached 问题）"""
    db = SessionLocal()
    try:
        job = db.query(PromptReforgeHistory).filter(PromptReforgeHistory.id == task_id).first()
        if not job:
            return
        if status is not None:
            job.status = status
        if stage is not None:
            job.stage = stage
        if error is not None:
            job.error = error
        if output_md is not None:
            job.output_md = output_md
        if report is not None:
            job.report_json = json.dumps(report, ensure_ascii=False)
        db.commit()
    finally:
        db.close()


# ═══════════════════════════ ① 提取 ═══════════════════════════

EXTRACT_SYSTEM = """你是影视剧本结构化分析师。把输入的提示词/剧本/小说文本提取为严格的 JSON 剧本节拍表，供视频提示词导演使用。

【输出 JSON 结构】
{
  "title": "作品标题或主题",
  "style": "整体风格一句话（如：写实电影质感 / 水墨武侠 / 赛博朋克霓虹）",
  "characters": [{"name": "角色名", "gender": "男/女/中性", "age": "少年/青年/中年/老年或具体", "appearance": "外貌服装武器等关键特征"}],
  "scenes": [{"name": "场景名", "setting": "环境结构/时段/光线/尺度纵深"}],
  "beats": [
    {"beat_no": 1, "scene": "场景名", "summary": "本镜剧情一句话", "characters": ["角色名"],
     "action": "具体动作与事件（起承转合）", "camera": "机位与运镜建议",
     "atmosphere": "光线色调氛围（可视觉化）", "dialogue": [{"speaker": "角色名", "text": "对白原文"}]}
  ]
}

【规则】
- beats 最多 12 个，超过时按剧情重要度取前 12 个关键镜头。
- 每个 beat 必须有 scene/action/camera/atmosphere；有对白才写 dialogue，无对白用空数组。
- characters 必须与剧情一致，禁止漏人；性别/年龄/服装必须明确。
- 只输出一个合法 JSON，禁止输出任何解释文字、编号列表、markdown 代码块。"""


async def _extract_beats(input_text: str) -> dict:
    """调用本地 LLM 提取结构化节拍（带 1 次自动重试）

    重试原因：Ollama 在显存竞争/模型热加载等瞬时状态下可能返回
    200 + {"error": "Invalid request..."} 占位 JSON，或直接 non-200；
    重试一次可显著提高提取成功率，避免生成空节拍导致重构质量崩塌。
    """
    last_err: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            result = await LLMService().chat_completion(
                system_prompt=EXTRACT_SYSTEM,
                user_content=f"请提取以下文本的剧本节拍（JSON）：\n\n{input_text}",
                temperature=0.2,
                max_tokens=4000,
                response_format="json_object",
                task_type="prompt_reforge_extract",
                prompt_template_name="提示词重构-节拍提取",
            )
            if not result.get("success"):
                raise RuntimeError(result.get("error") or "节拍提取失败")
            raw = result.get("content") or ""
            try:
                data = json.loads(raw)
            except Exception:
                # 尝试剥离围栏/前后噪声
                m = re.search(r"\{.*\}", raw, re.S)
                if not m:
                    raise RuntimeError(f"节拍提取 JSON 解析失败: {raw[:300]}")
                data = json.loads(m.group(0))
            if not isinstance(data, dict):
                raise RuntimeError("节拍提取返回非对象")
            # 防御：模型/服务返回错误占位 JSON 或无节拍时视为失败并重试
            if data.get("error") or not data.get("beats"):
                raise RuntimeError(data.get("error") or "节拍提取返回空节拍")
            beats = data.get("beats") or []
            data["beats"] = beats[:MAX_BEATS]
            return data
        except Exception as e:
            last_err = e
            if attempt == 1:
                await asyncio.sleep(3)
    raise RuntimeError(f"节拍提取失败（重试后仍失败）：{last_err}")


# ═══════════════════════════ ② 重构 ═══════════════════════════

def _platform_pack_instruction(platforms: list) -> str:
    """按目标平台生成组装指令"""
    lines = ["【输出要求】按以下平台分区输出逐镜提示词集（每镜完整、可直接复制提交）："]
    labels = [PLATFORM_LABELS.get(p, p) for p in platforms]
    lines.append("分区顺序：" + " / ".join(labels))
    lines.append("【分区格式（必须遵守）】每个平台分区必须以单独一行 '### <平台英文名>' 开头，")
    lines.append("例如 '### MiniMax H3'、'### Seedance'、'### Kling'、'### Veo'、'### 即梦'；分区之间空一行；")
    lines.append("分区标题必须是独立行，禁止与其他文字混排。")
    for p in platforms:
        label = PLATFORM_LABELS.get(p, p)
        if p == "minimax_h3":
            lines.append(f"- {label} 分区：每镜按导演模型指定的 H3 官方结构（六段式/三字段）完整输出。")
        else:
            lines.append(f"- {label} 分区：每镜按六要素（subject/action/scene/camera/lighting/technical）完整输出。")
    lines.append("- 每个平台分区内逐镜编号一致（Shot 1…N）；镜号/时长/画幅信息放在每镜开头。")
    lines.append("- 全部镜头必须保持人物/场景/动作连贯（可追踪），禁止镜头间跳变无因。")
    return "\n".join(lines)


REFORGE_OUTPUT_TAIL = (
    "\n【硬性输出约束】只输出提示词正文，禁止任何解释性文字、过程叙述、分析总结；"
    "不要输出'以下是重构结果'之类引导语；不要输出 JSON 或代码块围栏。"
)


# 单平台重构的最大输出 token（六段式/六要素 × 8-12 镜需要充足余量；
# 6000 会截断导致 LLM 复读如 '### Seed' 洪水，拉高到 8000）
REFORGE_MAX_TOKENS = 8000


def _sanitize_md(md_text: str) -> str:
    """清洗 LLM 原始输出，防御截断复读污染。

    典型病态：模型在 max_tokens 截断后进入复读，连续输出数千行
    '### Seed' 等无意义占位；此处按『连续重复行』截断，并顺带清理
    行尾空白/空行堆积。
    """
    text = (md_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    cleaned: list[str] = []
    run_key, run_count = None, 0
    for ln in lines:
        key = ln.strip()
        if key == run_key:
            run_count += 1
            if run_count > 5:  # 同一行连续出现超过 5 次视为复读，丢弃
                continue
        else:
            run_key, run_count = key, 1
        cleaned.append(ln)
    # 折叠尾部连续空行
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned).strip()


def _quality_check(platform: str, md_text: str, beats: dict) -> bool:
    """轻量质量门：输出是否达到可生成级（不合格触发重试）"""
    text = md_text or ""
    if len(text.splitlines()) < max(3, len(beats.get("beats") or [])):
        return False
    if platform == "minimax_h3":
        # Ref2VA/I2VA 关键段必须出现，否则视为八股复读/摆烂输出
        return ("subject_definitions" in text and "detailed_description" in text) or \
               ("integrated_multimodal_description" in text)
    return True


async def _reforge_platform_prompts(
    director_key: str, beats: dict, platform: str, *, attempt: int = 0
) -> str:
    """按『单平台』重构逐镜提示词（一次调用只输出一个平台分区）"""
    director = get_director_model(director_key)
    rules = director["build_rules"]()
    system_prompt = "\n\n".join([
        rules,
        _platform_pack_instruction([platform]),
        "你是 AI 视频提示词导演，输出必须达到【AI 工具可生成级】：模型拿到提示词无需二次理解即可生成。",
    ])
    user_content = (
        f"导演模型：{director['name']}\n"
        f"目标平台：{PLATFORM_LABELS.get(platform, platform)}\n\n"
        f"【提取的剧本节拍】\n{json.dumps(beats, ensure_ascii=False, indent=2)}\n\n"
        "请按导演模型规则与平台要求，输出该平台分区的逐镜提示词集（每镜完整、可直接复制提交）。"
    )
    result = await LLMService().chat_completion(
        system_prompt=system_prompt,
        user_content=user_content + REFORGE_OUTPUT_TAIL,
        temperature=0.3,
        max_tokens=REFORGE_MAX_TOKENS,
        task_type="prompt_reforge_build",
        prompt_template_name=f"提示词重构-{director['name']}-{platform}",
    )
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "提示词重构失败")
    raw = (result.get("content") or "").strip()
    raw = _sanitize_md(raw)
    # 质量门：不合格自动重试一次（同平台最多 2 次）
    if not _quality_check(platform, raw, beats) and attempt < 1:
        await asyncio.sleep(2)
        return await _reforge_platform_prompts(
            director_key, beats, platform, attempt=attempt + 1
        )
    return raw


async def _reforge_prompts(director_key: str, beats: dict, platforms: list) -> str:
    """按导演模型 + 节拍 + 目标平台，生成多平台逐镜提示词集。

    修复：原实现一次调用输出全部平台，5 平台 × 8-12 镜远超单次
    max_tokens，必然截断并诱发 LLM 复读（'### Seed' 洪水）。
    现改为逐平台串行生成，每平台独立分区标题，单次输出量可控。
    """
    parts: list[str] = []
    for platform in platforms:
        part = await _reforge_platform_prompts(director_key, beats, platform)
        if not part:
            continue
        parts.append(f"### {PLATFORM_LABELS.get(platform, platform)}\n\n{part}")
    return "\n\n".join(parts)


# ═══════════════════════════ ③ 组装落盘 ═══════════════════════════

def _shot_blocks_from_md(md_text: str) -> list:
    """从提示词集 Markdown 粗拆镜头块（供 payload JSON 使用，尽力而为）

    修复：
      1) 首个 Shot 行之前的共享段（<Subject N> 定义 / [reference generation]）
         不属于任何单镜，直接跳过，避免混入 Shot 1 的 payload；
      2) 每个块经 _sanitize_md 清洗，截断截断复读（'### Seed' 洪水）。
    """
    blocks = []
    current = []
    for line in (md_text or "").splitlines():
        if re.match(r"^#{1,6}\s*(Shot|镜头|【?第?[0-9]+镜)", line) or re.match(r"^Shot\s+\d+", line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
        # 首个 Shot 之前的行：不收集（属于分区共享段）
    if current:
        blocks.append("\n".join(current))
    cleaned = []
    for b in blocks:
        if not b.strip():
            continue
        c = _sanitize_md(b)
        if not c:
            continue
        # 过滤块内误入的平台分区标题行（LLM 在镜头中途插入
        # '### Seedance' 等分区标记属于边界噪声，不属于镜头内容）
        kept = [
            ln for ln in c.splitlines()
            if not re.match(r"^#{1,3}\s*(MiniMax\s*H3|Seedance|Kling|Veo|即梦)\s*$", ln.strip())
        ]
        c = "\n".join(kept).strip()
        if c:
            cleaned.append(c)
    return cleaned[:MAX_BEATS]


def _split_platform_parts(md_text: str, platforms: list) -> dict:
    """按 '### <平台名>' 分区标题把模型输出拆成 {platform: text}（找不到标记时整段归第一平台）"""
    parts: dict[str, str] = {}
    lines = (md_text or "").splitlines()
    cur_key = None
    cur_lines = []
    label_to_key = {PLATFORM_LABELS.get(p, p): p for p in platforms}
    for line in lines:
        m = re.match(r"^#{1,3}\s*(.+?)\s*$", line.strip())
        if m and m.group(1).strip() in label_to_key:
            if cur_key:
                parts[cur_key] = "\n".join(cur_lines).strip()
            cur_key = label_to_key[m.group(1).strip()]
            cur_lines = []
        elif cur_key is None and line.strip():
            cur_key = platforms[0]
            cur_lines = [line]
        else:
            cur_lines.append(line)
    if cur_key:
        parts[cur_key] = "\n".join(cur_lines).strip()
    # 未命中的平台留占位
    for p in platforms:
        parts.setdefault(p, parts.get(platforms[0], ""))
    return parts


def _pack_outputs(
    task_id: str,
    input_text: str,
    chapters: list,
    chapter_outputs: list,
    platforms: list,
) -> dict:
    """
    组装落盘（分章版）：
      prompts.md            汇总提示词集（按章分区，可整体复制）
      chapters/chapter_NN.md 每章独立提示词集（含该章节拍与平台分区）
      payloads/shot_NNN.json 全部镜头全局连续编号
      report.json           含 chapters 元信息（每章节拍数/镜头数/平台分区）
    返回 {"file_tree": [...], "shot_count": N, "chapter_count": M}
    """
    out_dir = REFORGE_DATA_DIR / task_id
    payload_dir = out_dir / "payloads"
    chapter_dir = out_dir / "chapters"
    payload_dir.mkdir(parents=True, exist_ok=True)
    chapter_dir.mkdir(parents=True, exist_ok=True)

    merged_platform_parts: dict[str, str] = {}
    full_parts, output_md_parts = [], []
    shot_global = 0
    payloads = []
    chapter_meta = []

    for co in chapter_outputs:
        idx, heading, beats, md_text = co["index"], co["heading"], co["beats"], co["md_text"]
        shot_n = len(_shot_blocks_from_md(md_text))

        # 每章独立 md
        ch_header = "\n".join([
            f"# 第 {idx} 章：{heading}",
            "",
            f"- 提取节拍数：{len(beats.get('beats') or [])}",
            f"- 本章镜头数：{shot_n}",
            "",
            "## 提取结果（剧本节拍）",
            "",
            "```json",
            json.dumps(beats, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 逐镜提示词集（AI 工具可生成级）",
            "",
        ])
        (chapter_dir / f"chapter_{idx:02d}.md").write_text(ch_header + md_text + "\n", encoding="utf-8")

        # 每章平台分区
        ch_parts = _split_platform_parts(md_text, platforms)

        # payloads：全局连续编号
        for b in _shot_blocks_from_md(md_text):
            shot_global += 1
            payload = {
                "shot": shot_global,
                "chapter": idx,
                "prompt_text": b,
                "duration": 4,   # 默认 4-15s 内，正文含时长时以正文为准
                "ratio": "9:16",
                "platforms": platforms,
            }
            payloads.append(payload)
            (payload_dir / f"shot_{shot_global:03d}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        full_parts.append(f"## 第 {idx} 章：{heading}\n\n{md_text.strip()}")
        output_md_parts.append(f"## 第 {idx} 章：{heading}\n\n{md_text.strip()}")
        chapter_meta.append({
            "index": idx,
            "heading": heading,
            "beat_count": len(beats.get("beats") or []),
            "shot_count": shot_n,
            "md_file": f"chapters/chapter_{idx:02d}.md",
            "platform_parts": ch_parts,
        })

    # 整篇平台分区（供前端平台 tab 快速切换）
    merged_md = "\n\n---\n\n".join(full_parts)
    merged_platform_parts = _split_platform_parts(merged_md, platforms)

    # prompts.md 汇总
    header = [
        "# 提示词提取与重构结果集",
        "",
        f"- 任务：{task_id}",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 目标平台：{'、'.join(PLATFORM_LABELS.get(p, p) for p in platforms)}",
        f"- 章节数：{len(chapter_outputs)}（智能断章，每章 ≤ {MAX_CHAPTER_CHARS} 字）",
        f"- 总镜头数：{shot_global}",
        "",
        "## 原始输入（节选）",
        "",
        f"```text\n{input_text[:1500]}\n```",
        "",
        "## 章节总览",
        "",
        "| 章 | 标题 | 节拍数 | 镜头数 | 独立文件 |",
        "|---|---|---|---|---|",
    ]
    for cm in chapter_meta:
        header.append(f"| {cm['index']} | {cm['heading']} | {cm['beat_count']} | {cm['shot_count']} | {cm['md_file']} |")
    header += ["", "## 逐镜提示词集（AI 工具可生成级，按章分区）", ""]
    md_path = out_dir / "prompts.md"
    md_path.write_text("\n".join(header) + "\n\n" + merged_md + "\n", encoding="utf-8")

    # report.json
    report = {
        "task_id": task_id,
        "chapter_count": len(chapter_outputs),
        "chapters": chapter_meta,
        "platforms": platforms,
        "shot_count": shot_global,
        "platform_parts": merged_platform_parts,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 文件树
    file_tree = []
    for root, _dirs, files in os.walk(out_dir):
        rel_root = Path(root).relative_to(out_dir)
        for fn in sorted(files):
            rel = str(rel_root / fn) if str(rel_root) != "." else fn
            file_tree.append({"path": rel.replace("\\", "/"), "size": os.path.getsize(os.path.join(root, fn))})
    return {"file_tree": file_tree, "shot_count": shot_global, "chapter_count": len(chapter_outputs)}


# ═══════════════════════════ 主流程 ═══════════════════════════

async def run_prompt_reforge(task_id: str, input_text: str, director_key: str, platforms: list):
    """
    后台任务：智能断章 → 逐章（提取 → 重构）→ 组装落盘。
    多章严格按序串行执行（for + await 顺序推进，worker 队列 + gpu_lock 保证
    同一时刻只跑一个模型任务，不并行抢显存）。
    """
    director = get_director_model(director_key)
    platforms = [p for p in platforms if p in PLATFORM_LABELS] or DEFAULT_PLATFORMS

    try:
        # ⓪ 智能断章（每章 ≤ 1 万字，不再整篇截断）
        chapters = _smart_split_chapters(input_text or "")
        if not chapters:
            _update_state(task_id, status="failed", stage="重构失败", error="输入为空")
            return
        _update_state(
            task_id,
            stage=f"智能断章完成：共 {len(chapters)} 章（每章 ≤ {MAX_CHAPTER_CHARS} 字），开始顺序生成…",
        )

        # ①+② 逐章：提取 → 重构（严格串行）
        chapter_outputs = []
        for i, ch in enumerate(chapters, 1):
            _update_state(task_id, stage=f"第 {i}/{len(chapters)} 章《{ch['heading']}》：① 提取剧本节拍…")
            beats = await _extract_beats(ch["text"])
            _update_state(
                task_id,
                stage=f"第 {i}/{len(chapters)} 章《{ch['heading']}》：② 重构逐镜提示词（{director['name']}）…",
            )
            md_text = await _reforge_prompts(director_key, beats, platforms)
            chapter_outputs.append({"index": ch["index"], "heading": ch["heading"], "beats": beats, "md_text": md_text})

        # ③ 组装落盘（分章产物 + 汇总）
        _update_state(task_id, stage=f"③ 组装 {len(chapters)} 章提示词集并落盘…")
        result = _pack_outputs(task_id, input_text, chapters, chapter_outputs, platforms)

        # 汇总 output_md（按章分区，供前端提示词集页签展示）
        output_md = "\n\n---\n\n".join(
            f"## 第 {co['index']} 章：{co['heading']}\n\n{co['md_text'].strip()}"
            for co in chapter_outputs
        )

        _update_state(
            task_id,
            status="success",
            stage=f"完成：{result['chapter_count']} 章 / {result['shot_count']} 个镜头提示词 / {len(platforms)} 个平台",
            output_md=output_md,
            report={
                "chapter_count": result["chapter_count"],
                "shot_count": result["shot_count"],
                "platforms": platforms,
            },
        )
        print(f"[PromptReforge] {task_id} 完成：{result['chapter_count']} 章 / {result['shot_count']} 镜 / {','.join(platforms)}")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _update_state(task_id, status="failed", stage="重构失败", error=str(exc)[:2000])
