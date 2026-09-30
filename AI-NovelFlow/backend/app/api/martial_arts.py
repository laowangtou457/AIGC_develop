"""
武术指导 API — 一句话需求生成武打分镜海报提示词 + 视频提示词（独立工作流）

参考: https://github.com/CY-CHENYUE/martial-arts-director-cy (Apache-2.0)

两步工作流（8b 全流程：稳定、干净、快；30b 长文输出带过程叙述易截断，平台其他任务已验证）：
  Step 1 (qwen3:8b) : 解析需求 → 输出人物锚定卡 + N 招招式列表（JSON，紧凑）
  Step 2 (qwen3:8b) : 基于锚定卡+招式列表 → 组装图片提示词 + 视频提示词（两段）

生成扩展（独立工作流内的本地 ComfyUI 生成，全部串行防显存溢出）：
  POST /generate-image  : 文生图 → 武指角色形象图（自建 Flux2-Klein-4B 纯文生图工作流，锚定卡角色信息为输入）
  POST /edit-image      : 图生图 → 角色图(参考图) + 文字分镜提示词 → 分镜图（复用激活 single_image_edit 工作流，4B）
  POST /generate-video  : 图生视频 → 分镜图/角色图 → H3 视频（ref2va 单参考图 / first_last 首尾帧）
显存治理（4090 32G 实际可用约 23G，必须串行+预释放）：
  - 所有生成整体包 gpu_serial 全局锁（与 LLM 推理互斥）
  - ComfyUIClient.queue_prompt 内部已自动卸载 Ollama 30b/8b（keep_alive=0），生图/生视频独占显存
  - 视频生成前额外调用 ComfyUI /free 卸载未锁定模型缓存（flux 残留），避免与 H3 同时驻留 OOM
"""
import asyncio
import json
import random
import re
import httpx
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
import base64
import os
import time
from pathlib import Path
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
from urllib.parse import urlparse, parse_qs, quote
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.martial_arts_history import MartialArtsHistory

from app.api.deps import get_llm_service
from app.services.llm_service import LLMService
from app.services.file_storage import file_storage
from app.services.gpu_scheduler import gpu_serial
from app.services.comfyui.client import ComfyUIClient
from app.services.comfyui.workflows import WorkflowBuilder
from app.repositories import WorkflowRepository

router = APIRouter()

# ==================== 武打任务运行时监控（内存注册表，进程重启清零） ====================
import time as time_mod
import uuid as uuid_mod
from collections import deque

MARTIAL_TASKS = {}                # task_id -> record（运行中）
MARTIAL_RECENT = deque(maxlen=20) # 最近完成/失败记录

_TASK_LABEL = {
    "generate": "武打分镜提示词",
    "generate-image": "角色形象图（文生图）",
    "edit-image": "分镜图（图生图）",
    "generate-video": "武打视频（图生视频）",
}


def _martial_start(task_type: str, detail: str = "") -> str:
    """登记一个武打任务（接口开始处调用），返回 task_id。"""
    tid = uuid_mod.uuid4().hex[:12]
    MARTIAL_TASKS[tid] = {
        "id": tid,
        "task_type": task_type,
        "name": _TASK_LABEL.get(task_type, task_type),
        "status": "running",
        "stage": "启动",
        "detail": detail,
        "started_at": time_mod.time(),
        "finished_at": None,
        "elapsed_sec": None,
        "error": None,
    }
    return tid


def _martial_update(tid: str, stage: str = None, detail: str = None):
    """更新运行中任务的阶段/细节（幂等，任务不存在则忽略）。"""
    rec = MARTIAL_TASKS.get(tid)
    if not rec:
        return
    if stage:
        rec["stage"] = stage
    if detail is not None:
        rec["detail"] = detail


def _martial_finish(tid: str, error: str = None):
    """结束任务：移出运行表，写入最近记录（成功/失败）。"""
    rec = MARTIAL_TASKS.pop(tid, None)
    if not rec:
        return
    rec["status"] = "failed" if error else "completed"
    rec["error"] = error
    rec["finished_at"] = time_mod.time()
    rec["elapsed_sec"] = round(rec["finished_at"] - rec["started_at"], 1)
    MARTIAL_RECENT.appendleft(rec)

# ==================== Step 1：招式编排（8b，JSON 输出） ====================

PLAN_SYSTEM = """你是资深武术指导（借鉴袁和平/成龙/甄子丹/John Wick/87eleven的招式节奏与镜头语言）。根据用户需求做武打分镜的「招式编排」，输出严格 JSON。

【编排规则】
- 动作戏7原则：清晰(谁打谁/用什么/命中哪/结果)、场景几何(方位可追踪)、赌注、动机、编排有创意、脆弱(真实威胁)、后果(真实代价)。
- 人物清点(强制)：先识别需求中出现的全部人物（主角/对手/配角），逐一列进 card.characters；出场人物数量必须与需求严格一致（例如"太极宗师对外家拳师"=2人、"地铁阿姨 vs 三个劫匪"=4人），禁止漏人、禁止把对手弱化为无名的"虚化剪影"。
- 每个角色必须给出：姓名/身份(主角|对手|配角)/体型/服装/武器/武术体系；对手要有可辨识的外貌与服装特征，过招画面必须具体呈现。
- 姓名原称(强制)：characters[].name 必须使用需求中的具体人物称谓，例如"太极宗师""外家拳师""地铁清洁阿姨""劫匪甲"，禁止用"主角/对手/配角"等角色标签充当姓名；role 字段才填写主角|对手|配角。
- 姿态配额(N=宫格总数)：≥⌈N/5⌉低桩(半蹲/沉马/跪步/仆步)、≥⌈N/8⌉腾空高位、≥⌈N/5⌉转身或背身、≥⌈N/8⌉近景特写(手/兵器/眼神局部)、其余自由站姿。
- 第1格起势与第N格收势必须仪式感且明显不同（抱拳礼/持剑诀/单膝半跪/低头蓄势/立掌当胸/拈花指/双手合十/撑剑而立/跨步亮势/回身扣手/横兵于膝，按人物调性+兵器选）。
- 每招视觉差异化：朝向/重心/肢体张开度/发力部位不重复；每招 keypose 是"运动中的瞬间"（剑刚劈到一半/拳头正向胸口推进中），禁止"已完成定格姿态"。
- 天气氛围(强制)：card.scene_weather 必须写"场景+时段+天气+光感+氛围"（如"晨曦庭院：清晨薄雾、柔和晨光、微风拂动衣袂""雨夜长街：冷雨、霓虹倒影、水花四溅"）；card.equipment 服装的材质厚度/颜色/配饰必须与 scene_weather 协调（雪地配厚棉衣斗篷、雨夜配斗笠蓑衣、盛夏配轻衫短打），禁止服装与天气违和。
- 姿态参考(强制)：card.pose_reference 写经典武打电影的标志性姿态特征（黄飞鸿式沉桥架式/叶问式寸劲短打/截拳道式警戒架式/腾跃翻滚/大鹏展翅起手/单臂背刀蓄势等），用通用动作特征描述，不写真实角色名。
- 场景尺度(强制)：card.scene 与 scene_weather 必须写清场景的尺度与纵深，长/大/深等比例感必须量化表达——如"吊桥横跨数百米峡谷，两端铁索锚固于崖壁，桥身狭长悬垂、木板稀疏、随风摇晃，桥下深谷云雾缭绕不见底"；禁止把大桥画成几块木板、把大悬崖画成小土坡（参考绣春刀2结尾吊桥的构图：峡谷深不见底、桥身细长悬空、两端崖壁陡峭）。
- H3资产约束(强制)（参考图解析资产在场时优先采用）：每个角色必须明确——gender(性别：男/女/中性)、age(年龄：少年/青年/中年/老年或具体年龄)、clothing(服装材质+颜色+款式)、weapon_length(武器长短尺寸，如"单刀刃长约二尺五""长枪杆长约一丈")；card 必须明确——composition(场景布置与构图：主体位置/景别/镜头角度/空间层次，如"白衣女子立于桥中央偏左，黑衣刀客自右侧入画，全景平视，前景铁索/中景人物/背景山崖云雾")；动作风格(action)随 pose_reference 表达。上述字段同时写入 card 顶层与 characters[] 每行。
- 性别年龄(强制)：gender 必须明确无反串歧义（原文"少女"→gender=女+age=少女）；age 写年龄段或具体年龄，原文有则照原文，无则按身份合理推定。
- 武器长短(强制)：weapon/weapon_length 写明兵器类型+长短尺寸；长兵器（长枪/大刀/长棍）构图大开大合、攻击距离远，短兵器（短刀/匕首/双节棍）贴身近战、动作紧凑，兵器长短决定每格构图尺度与动作幅度。
- 形象细节(强制)：characters[] 每个角色的 physique/clothing/weapon/weapon_length 必须写足细节、禁止笼统词（"古装""帅气""漂亮"）——physique 含发型发饰、五官脸型、肤色、身高体型、气质；clothing 含材质+颜色+款式+配饰细节（腰带/护腕/护胸/绑腿/披风/纹样/发饰）；weapon/weapon_length 含类型+长度尺寸+材质+关键特征（刃形/把柄/鞘）。
- 涉及对手的招式在 point 中明确标注：对手姓名+目标高度(颈部/胸口/腹部)+两人相对方位（如"宗师向右转身，拳师自左侧直拳逼近"），对手必须是具体人物不是剪影。
- 招名精炼具体（"转身扫腿"而非"扫腿"）；动态招式 point 含残影/速度线/衣袂横拖等词。
- 动态动词(强制)：每招 point 必须含至少1个强动态动词（冲/推/撩/扫/砸/卸/震/勾/崩/截/缠/绞）+发力轨迹（如"自腰间直线崩出""借转身旋劲横扫"）；禁止只写静态站姿描述。
- 速度标记(强制)：每招 point 末尾用【快/中/慢/骤停】标出节奏，至少4处【快】、2处【骤停】（收势定格），让招式有快慢张弛。
- 用户没给的字段用默认值：宫格16=4×4、徒手、暗色舞台灰背景、暴力美学+电影武指调性、写实硬派。
- 合规：避开具体IP角色名，用通用视觉特征；软化暴力（震退/化解/火花飞溅替代血腥语）。

【输出格式】
只输出一个 JSON 对象，不要输出任何其他文字：
{
  "card": {
    "presentation_style": "呈现风格",
    "gender": "性别(主角，男/女/中性)",
    "age": "年龄(主角，少年/青年/中年/老年或具体年龄)",
    "physique": "体型与气质(主角)",
    "martial_system": "武术体系(主角)",
    "tone": "风格调性",
    "equipment": "装备/服装(主角，材质+颜色+款式)",
    "weapon_length": "武器长短(主角兵器类型+尺寸，如单刀刃长约二尺五)",
    "scene": "场景",
    "scene_weather": "场景+时段+天气+光感+氛围",
    "composition": "场景布置与构图(主体位置/景别/镜头角度/空间层次)",
    "pose_reference": "武打姿态参考(经典武打电影标志性动作)",
    "grid": "宫格规格",
    "weapon": "兵器",
    "reference": "影视参考",
    "characters": [
      {"name": "太极宗师", "role": "主角", "gender": "男", "age": "中年", "physique": "发型/五官/面形/身高体型/气质", "clothing": "服装材质+颜色+款式+细节装饰（纹样/腰带/护腕/发饰/披风）", "weapon": "兵器类型+材质", "weapon_length": "尺寸长度+刃形/把柄细节", "martial": "太极"},
      {"name": "外家拳师", "role": "对手", "gender": "男", "age": "壮年", "physique": "发型/五官/面形/身高体型/气质", "clothing": "服装材质+颜色+款式+细节装饰（纹样/腰带/护腕/发饰/披风）", "weapon": "兵器类型+材质", "weapon_length": "尺寸长度+刃形/把柄细节", "martial": "外家拳"}
    ]
  },
  "moves": [
    {"n": 1, "name": "招式名", "point": "动作要点(含双方姓名/相对方位/姿态/镜头)", "pose": "姿态标签(低桩/腾空/转身/近景特写/自由站姿)"}
  ]
}
characters 数量 = 需求出场人物数量（至少 2 人：主角+对手；若需求确实只有 1 人则 1 人）。
moves 的数量必须等于宫格总数（默认16）。"""


# ==================== Step 2：组装提示词（30b） ====================

BUILD_SYSTEM = """你是资深武术指导 + 摄影指导。基于给定的人物锚定卡和 N 招招式列表，输出两段可直接复制的提示词：多宫格海报图片提示词 + 图生视频提示词。

【核心理念】
- 武术七维艺术：身形/节奏/力道/气韵/弧线/静动对比/环境对话；武意——动静交替中的情感溢出。
- 武指+摄影联合：每招配镜头语言(景别/运镜/角度/速度)；同一人物锁定（所有格同一演武者、同一服装、同一调性）。
- 人物数量一致(强制)：图片与视频中出场人物数量必须与需求/锚定卡严格一致（如锚定卡出场人物=2人，画面必须出现2人且主次分明、同框过招），禁止只画主角、禁止对手缺席或退化为剪影。
- 动态生成反硬编码：按人物性格/场景/影视参考实质定制，不套模板。

【图片提示词规范（700-1050中文字符）】
- 不留占位符；N招按顺序全列出名字精炼具体；调性定语与锚定卡风格调性严格一致。
- 人物数量一致：开头即写明"画面共N人：A（主角，外貌/发型/体型/服装）、B（对手，外貌/发型/体型/服装）"，每个出场人物都有独立角色块；对手必须具体呈现（五官/服装/体态可辨识），并出现在过招画面中。
- 人物三锚点(外貌/发型/体型)+服装三要素(材质+颜色+款式)写进独立角色块「A与B全程保持同一形象」。
- 兵器材质颜色长度明确且动作连贯句至少出现2次；纯文字无markdown装饰。
- 涉及对手的招式标注：双方姓名+相对方位+目标高度；手部精细动作加"清晰可见的手指姿势"。
- 每格 keypose 是"运动中的瞬间"；禁止"距脸Xcm"等具体距离致命点，用"正向X推进中/逼近上身要害"。
- 速度张弛(强制)：每格动作要点末尾标【快/中/慢/骤停】，至少4处【快】、2处【骤停】（收势/发力定格），避免16格都是匀速站桩感；快慢交替体现武打节奏。
- 招式不重复(强制)：16格必须按招式列表顺序逐招写入，禁止重复使用同一招式凑格数；若招式不足16格，用"同招的不同攻防角度/双方换位"演化出新格，不得原样复制招式名与动作。
- 宫格布局(强制)：画面必须为 4×4 十六宫格分镜表（4行×4列共16格），从左到右、从上到下编号 01-16 与招式顺序一一对应；每格是独立小画面、格间有细边框分隔；所有 16 格必须等大正方形、排列均匀，禁止大小格混排（如上方特大全景格+下方小格）、禁止 2×2 四宫格、自由拼贴或单幅大图。
- 天气光感(强制)：每格背景写明场景时段天气光感（如"清晨薄雾/黄昏暖光/雨夜霓虹/雪地清冷"），人物服装与天气协调（雪地厚衣、雨夜蓑衣、盛夏轻衫），禁止服装与天气违和。
- 场景尺度(强制)：开头先总写场景尺度与纵深（如"吊桥横跨数百米峡谷、两端铁索锚固崖壁、桥身狭长悬垂、桥下深谷云雾不见底"），每格背景与人物站位呼应尺度（远景可见桥身延伸至对岸山崖、人物在桥上渺小、两侧栏杆铁索横贯）；禁止把大场景画成小道具、禁止场景缩水（参考绣春刀2结尾吊桥：峡谷深不见底、桥身细长悬空、木板稀疏）。
- 面部清晰(强制)：多宫格海报所有格人物面部清晰可辨、五官端正对称（双眼等大、双眉等高、口型正常）、比例无畸变，面部为武打表情（专注/发力/警惕/怒意），禁止模糊五官、残缺、变形脸、闭眼、侧面糊脸；每格人物占格内主体比例，面部不被发丝/衣领/兵器遮挡。

【视频提示词规范（中文输出，700-1000字，供阅读；生成视频时后端自动转换为 H3 英文模板执行）】
- 人物特征标注(强制)：开头先写明"画面共N人：A（主角/对手，性别，年龄，服装颜色款式，武器特征）"逐人列出；每节拍动作主语必须用带性别/身份的词（如"白衣女侠""黑衣枪兵"），禁止只写角色名或"他/她"，防止视频生成性别漂移。人物性别/年龄/服装/武器与图片提示词完全一致、全程不变。
- 节拍数量(强制)：16 个节拍与十六宫格一一对应（01-16），覆盖六阶段戏剧弧线：起势凝神→试探接触→爆发主攻→反转→高潮决招→收势凝望；每拍动作必须不同，禁止循环复制或重复相同招式（如"刀光如影"只允许出现一次）。
- 总时长(强制)：整段视频总时长 15 秒（由系统按节拍数均分，如 8 拍每拍约 1.9 秒；不要给每个节拍单独写秒数）。
- 逐节拍分行写：节拍名｜画面动作（谁+动态动词+发力轨迹+对手反应）｜镜头（运动类型+幅度+速度）｜音效情绪。禁止在节拍行写时长/秒数，禁止逐格秒级罗列。
- 人物数量一致(强制)：主角+对手全程同框对垒、对手不可虚化；至少2处连招链+3处决定性瞬间；肢体末梢（拳掌/腿膝/衣袂/兵器）有清晰位移弧线。
- 速度张弛(强制)：爆发突进/绵柔化力/骤停定格交替；默认实时搏击速度，慢镜/凝固定格仅1-2处。
- 动作连贯：前一招余势自然带出下一招，肢体不打断不瞬移；惯性/重心转移/衣袂鼓荡/落地震尘可见；物理重量感。
- 忠于参考图：人物数量/武器/服装/场景/光感以参考图为准不增不减；场景尺度保持全景（吊桥横跨峡谷、桥身细长、桥下深谷），不缩水成局部木板。
- 影视参考用1-3部最契合电影做风格锚点；暴力软化用震退/化解/火花飞溅。

【合规自检】避开具体IP角色名用通用视觉特征；暴力软化用震退/化解/火花飞溅。

【输出格式（严格遵守）】
只输出两段正文，段间必须用【独立一行】的三个等号 === 分隔（前后各留一个空行）：
===图片提示词===
（纯中文提示词正文，700-1050字）
===视频提示词===
（中文视频提示词正文，700-1000字，按上述中文规范；不要写英文 H3 字段名）
硬性要求：分隔线只能写 ===，禁止使用 ---、**视频提示词**、横线、加粗标题或任何其他格式；图片提示词段内绝对禁止出现"视频提示词/For the target video/integrated_multimodal_description"等视频内容字样；两段内容不得互相混入。
禁止输出任何思考过程、计划、草稿、版本迭代、总结、英文思考、markdown代码块标记。不要输出第二组分隔线。"""


class MartialArtsRequest(BaseModel):
    requirement: str
    character: Optional[str] = ""
    system_style: Optional[str] = ""
    tone: Optional[str] = ""
    equipment: Optional[str] = ""
    scene: Optional[str] = ""
    grid: Optional[str] = "16"
    weapon: Optional[str] = ""
    reference: Optional[str] = ""
    reference_assets: Optional[str] = ""  # 参考图解析资产（qwen2.5vl 解析结果，并入编排输入）
    characters: Optional[list] = None  # 用户显式定义的多角色列表（每个：name/role/gender/age/physique/clothing/weapon/martial）


def _build_user_message(data: MartialArtsRequest) -> str:
    fields = [
        ("人物/体型气质", data.character),
        ("武术体系", data.system_style),
        ("风格调性", data.tone),
        ("装备/服装", data.equipment),
        ("场景", data.scene),
        ("宫格规格", data.grid),
        ("是否带兵器", data.weapon),
        ("影视参考", data.reference),
    ]
    provided = "；".join(f"{k}：{v}" for k, v in fields if v.strip())
    msg = f"【一句话需求】{data.requirement.strip()}"
    if provided:
        msg += f"\n【补充信息】{provided}"
    if data.characters:
        role_lines = []
        for i, c in enumerate(data.characters or []):
            nm = (c.get("name") or "").strip() or f"角色{i + 1}"
            parts = [f"身份{ (c.get('role') or '').strip() or '主角' }"]
            for k, label in (("gender", "性别"), ("age", "年龄"), ("physique", "体型气质"),
                             ("clothing", "服装"), ("weapon", "武器"), ("martial", "武术体系")):
                v = str(c.get(k) or "").strip()
                if v:
                    parts.append(f"{label}：{v}")
            role_lines.append(f"  - {nm}（{'、'.join(parts)}）")
        msg += "\n【用户指定角色】(强制：人物清点以此为准，characters 数量必须与此完全一致、逐一对应，禁止合并/省略/改名)\n" + "\n".join(role_lines)
    assets = (data.reference_assets or "").strip()
    if assets:
        msg += f"\n【参考图解析资产】(以参考图为准，需与需求融合/去重，冲突时以需求为准)\n{assets}"
    return msg


def _parse_plan(raw: str) -> Optional[dict]:
    """解析 Step1 的 JSON 输出（容忍前后杂文本与代码块标记）。"""
    raw = raw.strip()
    raw = re.sub(r"```(?:json)?", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except Exception:
        return None


def _merge_characters(plan_chars: list, user_chars: list) -> list:
    """用户指定角色优先，缺失字段从 8b 同名角色补齐。

    8b 常漏输出 card.characters（退化）→ 用户传了角色定义就用用户的，
    避免锚定卡只显示一个角色导致多角色形象不稳定。
    """
    if not user_chars:
        return plan_chars or []
    by_name = {str(c.get("name") or "").strip(): c for c in (plan_chars or []) if c.get("name")}
    merged = []
    for uc in user_chars:
        m = dict(uc)
        p = by_name.get(str(uc.get("name") or "").strip())
        if p:
            for k, v in p.items():
                if k not in m or not str(m.get(k) or "").strip():
                    m[k] = v
        merged.append(m)
    return merged


def _card_markdown(card: dict) -> str:
    """人物锚定卡 → markdown 表格（全局字段 + 每个角色独立一行，多角色完整入卡）"""
    mapping = [
        ("presentation_style", "呈现风格"),
        ("tone", "风格调性"),
        ("scene", "场景"),
        ("scene_weather", "场景天气氛围"),
        ("composition", "场景布置与构图"),
        ("pose_reference", "武打姿态参考"),
        ("grid", "宫格规格"),
        ("weapon", "兵器"),
        ("reference", "影视参考"),
    ]
    lines = ["| 项目 | 内容 |", "|------|------|"]
    chars = card.get("characters") or []
    if isinstance(chars, list) and chars:
        names = "、".join(
            f"{c.get('name') or ''}（{c.get('role') or ''}）" for c in chars if c.get("name")
        )
        if names:
            lines.append(f"| 出场人物 | {names} |")
        for i, c in enumerate(chars):
            if not c.get("name"):
                continue
            tag = "①②③④⑤⑥⑦⑧⑨⑩"[i if i < 10 else 9]
            parts = [
                f"性别：{c.get('gender') or '未知'}",
                f"年龄：{c.get('age') or '未知'}",
            ]
            for k, label in (("physique", "体型与气质（形象细节）"), ("clothing", "装备/服装（材质+颜色+款式+细节装饰）"),
                             ("weapon", "武器（类型+材质）"), ("weapon_length", "武器长短（尺寸+刃形/把柄细节）"),
                             ("martial", "武术体系")):
                v = str(c.get(k) or "").strip()
                if v:
                    parts.append(f"{label}：{v}")
            lines.append(f"| 角色{tag} | {c['name']}（{c.get('role') or ''}）｜{'｜'.join(parts)} |")
    for key, label in mapping:
        v = str(card.get(key) or "").strip()
        if v:
            lines.append(f"| {label} | {v} |")
    return "\n".join(lines)


def _card_extract_persona(card_md: str) -> str:
    """从锚定卡 markdown 表格提取人物/场景描述行 → '标签：值；标签：值'

    供分镜图文生图使用：把锚定卡的人物一致性信息拼进生图提示词，
    保证 16 格海报内角色形象全图一致（替代图生图参考图）。
    """
    out = []
    for row in (card_md or "").splitlines():
        row = row.strip()
        if not row.startswith("|") or not row.endswith("|"):
            continue
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) < 2:
            continue
        k, v = cells[0], cells[1]
        if (k in ("出场人物", "场景", "场景天气氛围", "场景布置与构图", "武打姿态参考")
                or k.startswith("角色")) and v:
            out.append(f"{k}：{v}")
    return "；".join(out)


def _parse_grid_answer(ans: str):
    """解析宫格检查员回答 → (行, 列, 总格数)，解析不出返回 (None, None, None)"""
    a = (ans or "").replace("×", "x").replace("X", "x")
    m = re.search(r"(\d+)\s*行\s*[x]?\s*(\d+)\s*列", a)
    if m:
        r, c = int(m.group(1)), int(m.group(2))
        return r, c, r * c
    m = re.search(r"(\d+)\s*[x]\s*(\d+)", a)
    if m:
        r, c = int(m.group(1)), int(m.group(2))
        return r, c, r * c
    m = re.search(r"(\d+)\s*格", a)
    if m:
        return None, None, int(m.group(1))
    return None, None, None


async def _storyboard_is_16grid(image_url: str) -> bool:
    """qwen2.5vl:3b 视觉校验分镜海报是否为 4×4 十六宫格（严格计数）。

    只认"行×列=16 或总格数=16"；6/8 格、"无法判断"、模型/网络异常一律判失败重试，
    由外层最多 5 次换 seed 重试兜底，防止小模型幻觉把 6 格误判通过。
    """
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            b64 = base64.b64encode(r.content).decode()

        async def _call():
            payload = {
                "model": "qwen2.5vl:3b",
                "messages": [
                    {"role": "system", "content": "你是宫格漫画结构检查员。用户给你一幅多宫格漫画海报图。请判断它是几行几列共多少格。只回答如：4行4列16格。若无法判断回答：无法判断。"},
                    {"role": "user", "content": "请看这幅图，是几行几列的宫格？", "images": [b64]},
                ],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 64},
                "keep_alive": 0,
            }
            async with httpx.AsyncClient(timeout=300) as c2:
                r2 = await c2.post("http://127.0.0.1:11434/api/chat", json=payload)
                r2.raise_for_status()
                return r2.json().get("message", {}).get("content", "")
        ans = (await gpu_serial("martial_arts_grid_check", _call())).strip()
    except Exception:
        return False  # 校验异常/失败 → 判不通过，由重试兜底
    r_, c_, total = _parse_grid_answer(ans)
    if total == 16 or (r_ == 4 and c_ == 4):
        return True
    return False


def _moves_text(moves: list) -> str:
    lines = []
    for mv in moves:
        n = mv.get("n", "")
        name = mv.get("name", "")
        point = mv.get("point", "")
        pose = mv.get("pose", "")
        lines.append(f"{n:02d}｜{name}｜{point}｜[{pose}]")
    return "\n".join(lines)


def _parse_grid_count(grid_val: str) -> int:
    """从宫格规格文本解析格数（'16 = 4×4'/'16格'/16 → 16），默认 16"""
    if not grid_val:
        return 16
    m = re.search(r"(\d+)", str(grid_val))
    try:
        return int(m.group(1)) if m else 16
    except Exception:
        return 16


def _evolve_moves(moves: list, target: int) -> list:
    """确定性兜底：招式不足 target 时，用同招的不同攻防角度/双方换位演化补足。

    保证 build_input 永远有 target 条招式，避免 LLM 输出 5 招导致分镜只有 5 格。
    演化变体按顺序循环取：攻防换位/变换角度/双方换位/反手变式/缠斗变式/节奏变化。
    """
    if not moves:
        return moves
    if len(moves) >= target:
        return moves[:target]
    out = list(moves)
    variants = ["攻防换位", "变换角度", "双方换位", "反手变式", "缠斗变式", "节奏变化"]
    i = 0
    while len(out) < target:
        src = out[i % len(out)]
        i += 1
        v = variants[len(out) % len(variants)]
        new = dict(src)
        new["n"] = len(out) + 1
        new["name"] = f"{src.get('name') or '同招'}·{v}"
        base_point = src.get("point") or ""
        new["point"] = f"{base_point}；同招{v}（{v}后重新拉近距离/换至另一侧）"
        new["pose"] = src.get("pose") or "自由站姿"
        out.append(new)
    return out


_SEP_HEAD_RE = re.compile(
    r"^(?:={3,}|-{3,}|\*{2,})\s*(图片提示词|图片prompt|图像提示词|视频提示词|视频prompt)?\s*(?:={3,}|-{3,}|\*{2,})?\s*$"
)
_VIDEO_FEATURE_KWS = (
    "For the target video",
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
    "**视频提示词**",
    "**视频prompt**",
)


def _extract_two_sections(raw: str):
    """从 Step2 输出中提取图片提示词与视频提示词（行级鲁棒版）。

    兼容 LLM 的多种退化输出（8b 经常不遵守 === 协议）：
    - 分隔线形态：=== / --- / ** ** / 带"图片提示词"或"视频提示词"标题 / 无标题
    - 完全无分隔线：按视频特征关键词（For the target video / integrated_multimodal_description
      / overall_soundscape / non_diegetic_music / **视频提示词**）切分，防止视频内容混入图片段
    """
    raw = re.sub(r"```(?:json|text)?", "", raw)
    lines = raw.split("\n")
    img_lines: list = []
    vid_lines: list = []
    state = "img"
    for line in lines:
        stripped = line.strip()
        m = _SEP_HEAD_RE.match(stripped)
        if m:
            title = (m.group(1) or "").strip()
            if "视频" in title:
                state = "vid"
            elif "图片" in title or "图像" in title:
                state = "img"
            elif state == "img":
                # 无标题分隔线：图片→视频切换（首个分隔即分界）
                state = "vid"
            continue
        if stripped in ("**视频提示词**", "**视频prompt**", "视频提示词：", "视频prompt：",
                        "视频提示词:", "视频prompt:"):
            state = "vid"
            continue
        if state == "img":
            img_lines.append(line)
        else:
            vid_lines.append(line)

    img = "\n".join(img_lines).strip()
    vid = "\n".join(vid_lines).strip()

    # 兜底：若未切出视频段，在图片段内找视频特征关键词切断（防止视频混入图片提示词）
    if not vid:
        for kw in _VIDEO_FEATURE_KWS:
            idx = img.find(kw)
            if idx != -1:
                cut = img.rfind("\n", 0, idx) + 1
                vid = img[cut:].strip()
                img = img[:cut].strip()
                break
    return img, vid


def _normalize_beat_durations(text: str) -> str:
    """视频提示词时长兜底：整段总时长固定 15 秒、按节拍均分。

    8b 常把每个节拍写成十几秒（用户看到"每格 15 秒"）。此处：
    1. 剥离节拍行内的逐拍秒数（｜X秒｜ / ｜Xs｜ / X秒｜）；
    2. 段首声明总时长 15 秒 + 均分规则，实际成片时长以视频生成参数为准。
    """
    if not text:
        return text
    cleaned = []
    for ln in text.splitlines():
        ln = re.sub(r"｜\s*\d+(\.\d+)?\s*(秒|s|S)\s*", "｜", ln)
        ln = re.sub(r"\d+(\.\d+)?\s*(秒|s|S)\s*｜", "｜", ln)
        ln = ln.replace("｜｜", "｜")
        cleaned.append(ln)
    out = "\n".join(cleaned)
    if "总时长" not in out:
        beats = [ln for ln in out.splitlines() if ln.strip() and "｜" in ln]
        n = len(beats)
        out = (f"总时长：15 秒（{n} 个节拍，每拍约 {15.0 / max(n, 1):.1f} 秒，"
               f"由系统按节拍均分；实际成片时长以视频生成参数为准）\n" + out)
    return out


_GRID_BLOCKS = ((1, 2, 5, 6), (3, 4, 7, 8), (9, 10, 13, 14), (11, 12, 15, 16))


def _parse_panels(prompt: str):
    """把 16 格文字分镜提示词拆为 (全局头, {格号: 内容})。

    行格式：NN｜内容（编号 01-16）；编号行之前为全局段（人物/场景/光感）。
    缺失格用通用描述兜底，保证 16 格拼图不缺格。
    """
    head, panels, cur = [], {}, None
    for ln in prompt.splitlines():
        m = re.match(r"^\s*(\d{1,2})\s*[｜|]\s*(.*)$", ln.strip())
        if m:
            nn = int(m.group(1))
            if 1 <= nn <= 16:
                cur = nn
                panels[nn] = m.group(2).strip()
            continue
        if cur is not None and panels.get(cur):
            panels[cur] = panels[cur] + " " + ln.strip()
        else:
            head.append(ln)
    head_txt = "\n".join(head).strip()
    for k in range(1, 17):
        if k not in panels or not (panels.get(k) or "").strip():
            panels[k] = f"第 {k} 招对打瞬间，延续本场景光感与人物设定"
    return head_txt, panels


def _merge_2x2_grid(imgs, out_path: str) -> None:
    """4 张 2×2 子图（左上/右上/左下/右下块）拼成 4×4 十六宫格"""
    from PIL import Image
    w, h = imgs[0].size
    canvas = Image.new("RGB", (w * 2, h * 2), (255, 255, 255))
    for i, im in enumerate(imgs):
        r, c = divmod(i, 2)
        canvas.paste(im, (c * w, r * h))
    canvas.save(out_path, "PNG")


@router.post("/generate", response_model=dict)
async def generate_martial_arts(
    data: MartialArtsRequest,
    llm_service: LLMService = Depends(get_llm_service),
    db: Session = Depends(get_db),
):
    """一句话需求 → 人物锚定卡 + 图片提示词 + 视频提示词（两步串行）"""
    requirement = (data.requirement or "").strip()
    if not requirement:
        return {"success": False, "message": "请先输入一句话需求"}

    _tid = _martial_start("generate", f"需求：{requirement[:50]}")

    user_msg = _build_user_message(data)

    # ---------- Step 1：招式编排（8b，JSON） ----------
    async def _plan():
        return await llm_service.chat_completion(
            system_prompt=PLAN_SYSTEM,
            user_content=user_msg,
            temperature=0.5,
            max_tokens=2048,
            response_format="json_object",
            task_type="martial_arts_plan",
            prompt_template_name="武术指导-招式编排",
        )

    try:
        _martial_update(_tid, stage="招式编排（qwen3:8b）")
        plan_result = await gpu_serial("martial_arts_plan", _plan())
    except Exception as exc:
        _martial_finish(_tid, error=f"招式编排失败: {exc}")
        return {"success": False, "message": f"招式编排失败: {exc}"}

    if not plan_result.get("success"):
        _martial_finish(_tid, error=plan_result.get("error") or "招式编排调用失败")
        return {"success": False, "message": plan_result.get("error") or "招式编排调用失败"}

    plan = _parse_plan(plan_result.get("content") or "")
    if not plan:
        _martial_finish(_tid, error="招式编排返回格式异常")
        return {"success": False, "message": "招式编排返回格式异常，请重试"}

    card = plan.get("card") or {}
    # 多角色兜底：用户显式定义的角色强制进入锚定卡（8b 漏 characters 时以用户为准）
    if data.characters:
        card["characters"] = _merge_characters(card.get("characters") or [], data.characters)
    moves = plan.get("moves") or []
    if not moves:
        _martial_finish(_tid, error="招式编排未生成招式列表")
        return {"success": False, "message": "招式编排未生成招式列表，请重试"}

    # 格数确定性兜底：不足 N 招时同招演化补足，保证分镜格数与宫格布局一致
    grid_n = _parse_grid_count((data.grid or "").strip()) or _parse_grid_count(card.get("grid") or "")
    orig_n = len(moves)
    moves = _evolve_moves(moves, grid_n)
    if len(moves) != orig_n:
        _martial_update(_tid, stage=f"招式编排（qwen3:8b）· 已从 {orig_n} 招补足到 {len(moves)} 招")

    card_md = _card_markdown(card)
    moves_txt = _moves_text(moves)

    # ---------- Step 2：组装两段提示词（30b） ----------
    build_input = (
        f"【人物锚定卡】\n{card_md}\n\n"
        f"【招式列表】（共 {len(moves)} 招，与宫格总数一致）\n{moves_txt}\n\n"
        f"请据此输出图片提示词与视频提示词两段。图片提示词必须逐招写满全部 {len(moves)} 格（{len(moves)} 格分镜与招式一一对应），禁止少于 {len(moves)} 格。"
    )

    async def _build():
        return await llm_service.chat_completion(
            system_prompt=BUILD_SYSTEM,
            user_content=build_input,
            temperature=0.6,
            max_tokens=4096,
            task_type="martial_arts_build",
            prompt_template_name="武术指导-提示词组装",
        )

    try:
        _martial_update(_tid, stage="提示词组装（qwen3:8b）")
        build_result = await gpu_serial("martial_arts_director", _build())
    except Exception as exc:
        _martial_finish(_tid, error=f"提示词组装失败: {exc}")
        return {"success": False, "message": f"提示词组装失败: {exc}"}

    if not build_result.get("success"):
        _martial_finish(_tid, error=build_result.get("error") or "提示词组装调用失败")
        return {"success": False, "message": build_result.get("error") or "提示词组装调用失败"}

    raw_build = (build_result.get("content") or "").strip()
    img, vid = _extract_two_sections(raw_build)
    vid = _normalize_beat_durations(vid)
    if not (img or vid):
        # 兜底：无分隔线时整段作为图片提示词展示
        img = raw_build

    # 宫格布局确定性兜底：8b 可能忽略布局指令 → 强制注入 4×4 十六宫格描述
    _GRID_LAYOUT_HINT = ("画面布局：4×4 十六宫格分镜表（4行×4列共16格，从左到右、从上到下编号01-16"
                         "与招式顺序一一对应，每格独立小画面、格间细边框分隔；16格必须全部等大正方形、"
                         "排列均匀、禁止大小格混排（如上方特大全景格+下方小格）；禁止2×2四宫格；"
                         "所有格人物面部清晰端正、五官对称无畸变，禁止模糊或变形脸）。")
    if img and not any(k in img for k in ("4×4", "十六宫格", "宫格")):
        img = img.rstrip() + "\n" + _GRID_LAYOUT_HINT

    # 成功后自动保存为历史任务（供页面刷新后恢复/回看）
    options_json = json.dumps({
        "character": data.character or "",
        "system_style": data.system_style or "",
        "tone": data.tone or "",
        "equipment": data.equipment or "",
        "scene": data.scene or "",
        "grid": data.grid or "",
        "weapon": data.weapon or "",
        "reference": data.reference or "",
    }, ensure_ascii=False)
    history = MartialArtsHistory(
        requirement=requirement,
        options_json=options_json,
        character_card=card_md,
        image_prompt=img,
        video_prompt=vid,
        raw=raw_build,
        created_at=datetime.now(),  # 本地时间（避免 CURRENT_TIMESTAMP 存 UTC 导致前端晚8小时）
    )
    db.add(history)
    try:
        db.commit()
        db.refresh(history)
    except Exception:
        db.rollback()

    _martial_finish(_tid)

    return {
        "success": True,
        "data": {
            "characterCard": card_md,
            "imagePrompt": img,
            "videoPrompt": vid,
            "raw": raw_build,
            "historyId": history.id,
        },
        "message": ("生成成功，但视频提示词未输出（qwen3:8b 输出退化），请重新生成一次" if not vid else "生成成功"),
    }


# ==================== 生成扩展：文生图 / 图生图 / 图生视频 ====================
#
# 模型选型（用户要求：文生图用 Flux2-Klein-4B 替代 9B，降低显存占用）：
#   - 文生图：自建 Flux2-Klein-4B 纯文生图工作流（unet=flux-2-klein-4b.safetensors,
#     clip=qwen_3_4b.safetensors, vae=flux2-vae.safetensors, steps=8, cfg=1, 零参考图零模板污染）
#   - 图生图：平台激活 single_image_edit 工作流（已确认 JSON 内加载 4B）
#   - 图生视频：平台激活 video(ref2va) / first_last_video(H3 首尾帧) 工作流
# 显存：gpu_serial 全局串行 + queue_prompt 自动卸载 Ollama + 视频前 ComfyUI /free

ASPECT_RATIO_DIMS = {
    "1:1": (1088, 1088),
    "4:3": (1088, 832),
    "3:4": (832, 1088),
    "16:9": (1088, 704),
    "9:16": (1088, 1920),
}


class GenerateImageRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "1:1"          # 角色图比例
    history_id: Optional[str] = None   # 历史任务 id（有则生成成功后自动回写角色图 URL）
    extra_prompt: Optional[str] = None # 手动附加提示词约束（用户输入框追加，如五官/服装/风格）


class EditImageRequest(BaseModel):
    image_url: str                     # ComfyUI /view 链接（角色形象图）
    prompt: str                        # 文字分镜提示词（编辑指令）
    aspect_ratio: Optional[str] = "1:1"
    history_id: Optional[str] = None   # 历史任务 id（有则生成成功后自动回写分镜图 URL）


class GenerateVideoRequest(BaseModel):
    prompt: str                        # 视频提示词（生成结果中的 videoPrompt）
    image_url: str                     # 首帧/参考图（分镜图）
    second_image_url: Optional[str] = ""  # 尾帧（mode=first_last 时必填）
    mode: str = "ref2va"               # ref2va（单参考图）| first_last（首尾帧）
    duration_seconds: int = 4          # 4 / 6 / 8
    history_id: Optional[str] = None   # 历史任务 id（有则生成成功后自动回写视频 URL）
    image_is_storyboard: bool = False  # 参考图是否为16格分镜海报（是则裁第1格作单帧起点）
    split_segments: bool = False       # 分段拼接：16 拍拆 8+8，两段各 15 秒（H3 舒适区），第二段首帧=第一段尾帧，ffmpeg 拼 30 秒


# ---------- 辅助 ----------

def _comfyui_filename_from_url(url: str) -> str:
    """把 ComfyUI /view?filename=X&subfolder=Y 解析成 LoadImage 可用的 'subfolder/X'。"""
    try:
        parts = urlparse(url)
        qs = parse_qs(parts.query)
        filename = (qs.get("filename") or [""])[0]
        subfolder = (qs.get("subfolder") or [""])[0]
        if not filename:
            return ""
        return f"{subfolder}/{filename}" if subfolder else filename
    except Exception:
        return ""


async def _free_comfyui_cache():
    """调用 ComfyUI /free 卸载未锁定模型缓存（flux 残留），给 H3 视频腾显存。"""
    try:
        import httpx
        from app.core.config import get_settings
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.post(
                f"{get_settings().COMFYUI_HOST}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=30.0,
            )
        print("[MartialArts] ComfyUI 模型缓存已释放")
    except Exception as e:
        print(f"[MartialArts] ComfyUI /free 跳过(忽略): {e}")


async def _ensure_comfyui_input_filename(image_url: str) -> str:
    """把 ComfyUI /view 链接的图片（在 output 目录）下载并上传到 input 目录，
    返回 LoadImage 可引用的文件名。

    说明：ComfyUI 的 LoadImage 只认 input 目录，无法直接引用 output 目录文件，
    因此图生图/图生视频必须先把源图（刚生成的海报）回传为 input 文件。
    """
    import httpx
    import os
    import tempfile

    filename = _comfyui_filename_from_url(image_url)
    if not filename:
        return ""
    client = ComfyUIClient()
    tmp_path = None
    try:
        async with httpx.AsyncClient(trust_env=False) as http:
            resp = await http.get(image_url, timeout=60.0)
            if resp.status_code != 200:
                print(f"[MartialArts] 下载源图失败: HTTP {resp.status_code}")
                return ""
            ext = os.path.splitext(filename)[1] or ".png"
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                f.write(resp.content)
                tmp_path = f.name
        upload = await client.upload_image(tmp_path)
        if not upload.get("success"):
            print(f"[MartialArts] 上传源图失败: {upload.get('message')}")
            return ""
        return upload.get("filename") or ""
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


async def _crop_grid_cell(image_url: str, cell_index: int, grid: int = 4) -> str:
    """把 4x4 分镜海报裁出第 cell_index 格（0-based）并上传回 ComfyUI input 目录。

    原因：H3 ref2va 把整张 4x4 海报当一帧参考，qwen3vl-32B 读不出格内细节，
    视频与分镜脱节；裁成单格后模型能读懂起点画面，配合节拍叙事生成动作。
    判定：分辨率 min 边 <1600 或非方形视为非海报（如单格图/角色图），返回空串由调用方回退原图。
    """
    import httpx
    import io
    import os
    import tempfile
    from PIL import Image

    tmp_path = None
    try:
        async with httpx.AsyncClient(trust_env=False) as http:
            resp = await http.get(image_url, timeout=60.0)
            if resp.status_code != 200:
                print(f"[MartialArts] 裁格下载失败: HTTP {resp.status_code}")
                return ""
        img = Image.open(io.BytesIO(resp.content))
        w, h = img.size
        # 仅方形图才按 grid×grid 裁格（16格海报 1088×1088；非方形如16:9封面不裁）
        if abs(w - h) > max(w, h) * 0.1:
            return ""
        cw, ch = w // grid, h // grid
        left = (cell_index % grid) * cw
        top = (cell_index // grid) * ch
        cell = img.crop((left, top, left + cw, top + ch))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            cell.save(f, format="PNG")
            tmp_path = f.name
        client = ComfyUIClient()
        upload = await client.upload_image(tmp_path)
        if not upload.get("success"):
            print(f"[MartialArts] 裁格上传失败: {upload.get('message')}")
            return ""
        print(f"[MartialArts] 分镜海报已裁第{cell_index + 1}格 -> {upload.get('filename')}")
        return upload.get("filename") or ""
    except Exception as e:
        print(f"[MartialArts] 裁格失败(回退原图): {e}")
        return ""
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


async def _queue_and_wait(workflow: dict, save_node_id: str, timeout: int = 3600) -> dict:
    """提交工作流并等待结果（queue_prompt 内部已自动卸载 Ollama 模型）。"""
    client = ComfyUIClient()
    queue_result = await client.queue_prompt(workflow)
    if not queue_result.get("success"):
        return {"success": False, "message": queue_result.get("error", "提交任务失败")}
    result = await client.wait_for_result(
        queue_result.get("prompt_id"), workflow, save_node_id, timeout=timeout
    )
    return result if result else {"success": False, "message": "生成失败"}


def _get_active_workflow_json(wtype: str) -> Optional[dict]:
    """从数据库取指定类型的激活工作流（JSON + node_mapping）。"""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        repo = WorkflowRepository(db)
        wf = repo.get_active_by_type(wtype) or repo.get_first_system_by_type(wtype)
        if not wf:
            return None
        return {
            "workflow": json.loads(wf.workflow_json),
            "node_mapping": json.loads(wf.node_mapping or "{}"),
        }
    finally:
        db.close()


def _build_flux2_4b_text2img_workflow(prompt: str, width: int, height: int, seed: Optional[int] = None) -> dict:
    """自建 Flux2-Klein-4B 纯文生图工作流（参照平台已验证的 Flux2 4B 结构：UNETLoader+CLIPLoader
    qwen_3_4b+Flux2Scheduler+SamplerCustomAdvanced+ConditioningZeroOut 负向）。

    不依赖任何用户工作流模板，避免"不要人物/三视图/参考图"等模板污染武指海报。
    """
    if seed is None:
        seed = random.randint(1, 2**32)
    return {
        "1": {"inputs": {"unet_name": "flux-2-klein-4b.safetensors", "weight_dtype": "default"}, "class_type": "UNETLoader"},
        "2": {"inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "flux2", "device": "default"}, "class_type": "CLIPLoader"},
        "3": {"inputs": {"vae_name": "flux2-vae.safetensors"}, "class_type": "VAELoader"},
        "4": {"inputs": {"width": width, "height": height, "batch_size": 1}, "class_type": "EmptyFlux2LatentImage"},
        "5": {"inputs": {"steps": 8, "width": width, "height": height}, "class_type": "Flux2Scheduler"},
        "6": {"inputs": {"text": prompt, "clip": ["2", 0]}, "class_type": "CLIPTextEncode"},
        "7": {"inputs": {"conditioning": ["6", 0]}, "class_type": "ConditioningZeroOut"},
        "8": {"inputs": {"sampler_name": "euler"}, "class_type": "KSamplerSelect"},
        "9": {"inputs": {"noise_seed": seed}, "class_type": "RandomNoise"},
        "10": {"inputs": {"noise": ["9", 0], "guider": ["11", 0], "sampler": ["8", 0], "sigmas": ["5", 0], "latent_image": ["4", 0]}, "class_type": "SamplerCustomAdvanced"},
        "11": {"inputs": {"cfg": 1, "model": ["1", 0], "positive": ["6", 0], "negative": ["7", 0]}, "class_type": "CFGGuider"},
        "12": {"inputs": {"samples": ["10", 0], "vae": ["3", 0]}, "class_type": "VAEDecode"},
        "13": {"inputs": {"filename_prefix": "martial_arts", "images": ["12", 0]}, "class_type": "SaveImage"},
    }


# ==================== 生图提示词英文化（qwen3:8b 翻译，失败回退中文） ====================

IMAGE_PROMPT_TRANSLATE_SYSTEM = """You are an expert English prompt translator for cinematic martial-arts character images. Translate the Chinese image-generation prompt into English.

Rules:
- Faithfully translate EVERY detail: number of characters, gender, age, clothing (material + color + style), weapon length, scene layout & composition, weather & lighting, concrete martial-arts pose, and the anti-floating / scene-integration constraints.
- Keep the original paragraph structure (Scene / Characters / Action / Overall) and keep bracketed labels like [Scene] [Characters] [Action] [Overall] as-is.
- Output ONLY the English prompt text, no explanations, no quotes around the whole text."""


async def _translate_image_prompt(prompt: str) -> str:
    """中文生图提示词 → 英文（qwen3:8b 直连 Ollama），翻译失败/超时回退中文"""
    if not re.search(r"[\u4e00-\u9fff]", prompt):
        return prompt  # 已是英文
    try:
        async def _call():
            payload = {
                "model": "qwen3:8b",
                "messages": [
                    {"role": "system", "content": IMAGE_PROMPT_TRANSLATE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 4096},
                "keep_alive": 0,
            }
            async with httpx.AsyncClient(timeout=240) as client:
                r = await client.post("http://127.0.0.1:11434/api/chat", json=payload)
                r.raise_for_status()
                return r.json().get("message", {}).get("content", "")
        translated = (await gpu_serial("martial_arts_img_translate", _call())).strip()
    except Exception:
        return prompt
    if len(translated) < 60 or not re.search(r"[A-Za-z]{4,}", translated):
        return prompt  # 翻译结果异常 → 回退中文
    return translated


H3_VIDEO_CONVERT_SYSTEM = """Translate a Chinese martial-arts video prompt into English, following the MiniMax-H3 video-prompt template. The output must start exactly with this line:

For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced. All characters' appearance, clothing, scene and lighting follow the reference image throughout without changing.

Then contain exactly three labeled sections:
integrated_multimodal_description: shots numbered [Shot 1], [Shot 2]...; [Shot 1] starts with a style statement (Cinematic, live-action) and anchors the reference image (character pose/clothing/scene), then describes the first action; every later shot begins with an increasing timestamp written as "At 00:SS.mmm, the camera cuts to ..." (example: [Shot 2] At 00:02.500, the camera cuts to ...); each beat is one full sentence: who + dynamic verb + force trajectory + opponent reaction + camera motion written naturally inside the sentence (e.g. "the camera pushes in with small amplitude at fast speed"), NOT stacked labels at the end; keep the exact number of characters and weapons, and keep each character's gender, age, clothing and weapon exactly as stated in the Chinese text (a female swordsman must stay female, a male spearman must stay male, appearance never changes); use at most 8 beats with no repeated beat wording.
overall_soundscape: an English summary of the ambient / action / non-verbal sounds found in the Chinese text; use N/A only if the text has none.
non_diegetic_music: an English description of instrumentation / tempo / dynamics found in the Chinese text; use N/A if the text has none.

CRITICAL: translate the ACTUAL content of the user's Chinese prompt into natural English. Never reproduce template placeholders (like <style + shot size>), field descriptions, rules, examples or instructions in the output. Output ONLY the translated English prompt, nothing else."""


_H3_SHOT_VERBS = re.compile(r"(立于|站立|挥|劈|斩|砍|挑|刺|扫|横截|格挡|挡|退|跃|腾|转身|旋身|缠|震|崩|点|戳|勾|推|引|卸|借力|收刀|收势|入画|逼近|闪避|侧身|后仰|踉跄|跌落|翻|滚|蹲|半跪|出拳|出招|连环)")
_H3_CAMERA_EN = {
    "长镜头": "a long shot", "远景": "a wide shot", "全景": "a full shot",
    "中景": "a medium shot", "近景": "a close-up shot", "特写": "an extreme close-up",
    "仰拍": "the camera tilts up", "俯拍": "the camera tilts down",
    "跟随": "the camera tracks", "推进": "the camera pushes in", "推近": "the camera pushes in",
    "拉远": "the camera pulls out", "慢镜头": "in slow motion",
}
_H3_SPEED_EN = {"慢": "at slow speed", "快": "at fast speed", "中速": "at normal speed"}


def _extract_video_beats(zh: str):
    """从中文视频提示词中确定性抽取动作节拍（兼容 NN｜/节拍名：/节拍名｜/裸行 4 种格式）。

    返回 (beats, persona, soundscape)：
    - beats: 清理掉 序号/时间戳/机位速度姿态标签 后的动作句列表
    - persona: 开头【人物特征】段（若有）
    - soundscape: 结尾的 场景设定/音效 段（若有）
    """
    lines = [ln.strip() for ln in zh.split("\n") if ln.strip()]
    beats, persona, sound = [], "", ""
    _HEAD_SKIP = ("总时长", "宫格规格", "兵器", "影视参考", "镜头语言", "节奏与风格",
                   "场景设定", "**视频提示词", "画面共", "（16 个节拍", "(16 个节拍", "背景", "人物数量一致")
    for ln in lines:
        if ln.startswith("【人物特征") or "以此为准" in ln:
            persona = ln
            continue
        if ln.startswith("场景设定") or ln.startswith("**场景"):
            sound += ln + " "
            continue
        if ln.startswith(_HEAD_SKIP):
            continue
        # 剥标签：NN｜ / 节拍名｜ / 节拍名： / 节拍名：内容｜[xx]
        core = re.sub(r"^(\d{1,2})\s*[｜|:：]\s*", "", ln)          # NN｜ 或 NN： 序号
        core = re.sub(r"^[^｜|:：。，]{1,12}[｜|:：]\s*", "", core)     # 节拍名：/｜
        # 剥句尾标签 【慢/快】 ｜[低桩] [腾空] [转身] [自由站姿] / 逐拍秒数
        core = re.sub(r"【[^】]*】", " ", core)
        core = re.sub(r"\s*｜?\s*\[[^\]]*\]", " ", core)
        core = re.sub(r"\d{1,2}:\d{2}-\d{1,2}:\d{2}", " ", core)
        core = re.sub(r"\d+\.?\d*\s*秒", " ", core)
        core = re.sub(r"\s+", " ", core).strip().strip("，。；;")
        if not core:
            continue
        if _H3_SHOT_VERBS.search(core) or len(core) > 12:
            beats.append(core)
    return beats, persona, sound.strip()


def _beat_fingerprint(beat: str) -> str:
    """动作指纹：去掉人名/兵器/修饰词，只留动词序列，用于去重"""
    for w in ("古月白", "白衣侠女", "黑衣男子", "白衣女子", "太极宗师", "外家拳师", "追兵1", "追兵", "男子", "女子", "侠女", "宗师",
              "唐横刀", "横刀", "长剑", "长枪", "绣春刀", "单刀", "刀", "剑", "枪"):
        beat = beat.replace(w, " ")
    # 只保留动词 + 程度词
    verbs = _H3_SHOT_VERBS.findall(beat)
    return " ".join(verbs[:6])


def _dedupe_video_beats(beats, max_n=16):
    """保留全部动作拍（16 拍与宫格一一对应），仅合并完全相同的连续重复行（8b 循环复制的极值）。

    时长上限由 H3 模型训练范围决定（124-362 帧 = 5-15 秒），16 拍在 15 秒内每拍约 0.94 秒，
    通过明确递增时间戳让模型按时间窗逐拍演绎。
    """
    if len(beats) <= max_n:
        return beats
    # 超过 16 拍（异常）：相邻完全相同行合并后仍超 → 均匀采样
    out, prev = [], None
    for b in beats:
        if b != prev:
            out.append(b)
        prev = b
    if len(out) > max_n:
        idx = sorted(set([0, len(out) - 1] + [round(i * (len(out) - 1) / (max_n - 1)) for i in range(1, max_n - 1)]))
        out = [out[i] for i in idx]
    return out


def _h3_camera_phrase(zh: str) -> str:
    """从中文节拍句里提取机位表达（自然内嵌英文）"""
    parts = []
    for k, v in _H3_CAMERA_EN.items():
        if k in zh:
            parts.append(v)
            break
    for k, v in _H3_SPEED_EN.items():
        if f"【{k}】" in zh or k in zh:
            parts.append(v)
            break
    if "从下向上" in zh or "从下往上" in zh:
        parts = ["the camera tilts up"]
    if parts:
        return ", ".join(parts)
    return ""


H3_BEAT_TRANSLATE_SYSTEM = """你是资深电影分镜师。把用户提供的 16 个中文武打动作节拍，逐拍翻译成独立英文镜头块，输出严格 JSON 数组（16 个对象，顺序与输入一致），每个对象：

{"shot": 拍号(1-16), "camera": "机位景别(英文短语，如 a wide shot from the side of the rope bridge / an extreme close-up of the blade collision / a low-angle tilt up at the fighter)", "subject_action": "主体动作(英文一句：谁+兵器+招式+轨迹，如 The white-clad female swordsman flicks her wrist and thrusts the Tang saber straight out from her waist, its edge cutting through the mist)", "opponent_reaction": "对手反应(英文一句或短句，没有则留空字符串)", "camera_motion": "相机运动(英文自然句，以 The camera 开头，如 The camera tracks the blade at fast speed；没有则留空字符串)", "pace": "节奏(英文，如 slow motion / fast / normal)"}

硬规则：
1. camera 必须逐拍有明显变化：wide shot / full shot / medium shot / close-up / extreme close-up / low-angle / high-angle / tracking / push-in / pull-out 等，按中文节拍里的机位标注选择，禁止连续两拍相同；
2. subject_action 必须写清性别与身份（female/male + 服装/兵器特征），动作动词具体（thrust/slash/spin/sweep/parry/leap 等），禁止抽象词；
3. 每拍英文总长控制在 45-60 词内，精简有力；
4. 只输出 JSON 数组，不要任何其他文字。"""


def _assemble_h3_blocks(blocks: list, persona="", duration: int = 15) -> str:
    """按官方 H3 模板组装独立镜头块：每镜 [Shot N] + 递增时间戳 + 机位 + 动作 + 反应 + 相机运动。"""
    n = len(blocks)
    per = duration / n if n else duration
    lines = []
    anchor = ("the character shown in <Picture 1> keeps the same appearance, clothing, weapon, "
              "gender and pose as the reference image throughout")
    if persona:
        anchor += "; " + persona.strip("【人物特征（以此为准，性别与外貌严禁改变）】").strip()
    b0 = blocks[0]
    head = f"[Shot 1] Cinematic, live-action, martial-arts film, {anchor}; {b0['camera']} as {b0['subject_action']}"
    if b0.get("opponent_reaction"):
        head += f", while {b0['opponent_reaction']}"
    if b0.get("camera_motion"):
        head += f". {b0['camera_motion']}"
    lines.append(head + ".")
    for i, b in enumerate(blocks[1:], start=2):
        t = per * (i - 1)
        mm = int(t // 60)
        ss = int(t % 60)
        ms = int(round((t - int(t)) * 1000))
        ts = f"{mm:02d}:{ss:02d}.{ms:03d}"
        line = f"[Shot {i}] At {ts}, the camera cuts to {b['camera']} as {b['subject_action']}"
        if b.get("opponent_reaction"):
            line += f", while {b['opponent_reaction']}"
        if b.get("camera_motion"):
            line += f". {b['camera_motion']}"
        lines.append(line + ".")
    body = "\n".join(lines)
    return (
        "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced. "
        "All characters' appearance, clothing, scene and lighting follow the reference image throughout without changing.\n\n"
        f"integrated_multimodal_description: {body}\n"
        "overall_soundscape: The sounds of clashing weapons, whooshing blade cuts through the air, footsteps and fabric movement in the wind.\n"
        "non_diegetic_music: N/A"
    )


def _assemble_h3_prompt(beats, persona="", duration=15) -> str:
    """确定性组装 H3 官方模板：I2VA instruction + [Shot N] 递增时间戳 + 机位内嵌"""
    n = len(beats)
    per = duration / n
    lines = []
    # 锚定参考图描述：persona 或通用
    anchor = "the character shown in <Picture 1> keeps the same appearance, clothing, weapon, gender and pose as the reference image throughout"
    if persona:
        anchor += "; " + persona.strip("【人物特征（以此为准，性别与外貌严禁改变）】").strip()
    first = beats[0]
    cam = _h3_camera_phrase(first)
    cam_txt = f" {cam} as " if cam else " as "
    lines.append(
        f"[Shot 1] Cinematic, live-action, martial-arts film, {anchor}; {first.strip()}."
    )
    for i, b in enumerate(beats[1:], start=2):
        t = per * (i - 1)
        mm = int(t // 60)
        ss = int(t % 60)
        ms = int(round((t - int(t)) * 1000))
        ts = f"{mm:02d}:{ss:02d}.{ms:03d}"
        cam = _h3_camera_phrase(b)
        if cam:
            if cam.startswith("the camera"):
                line = f"[Shot {i}] At {ts}, the camera cuts to {b.strip()}. {cam[0].upper() + cam[1:]}."
            elif cam.startswith(("a ", "an ")):
                line = f"[Shot {i}] At {ts}, the camera cuts to {b.strip()}. The camera holds {cam}."
            else:
                line = f"[Shot {i}] At {ts}, the camera cuts to {b.strip()}. The camera {cam}."
        else:
            line = f"[Shot {i}] At {ts}, the camera cuts to {b.strip()}."
        lines.append(line)
    body = "\n".join(lines)
    return (
        "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced. "
        "All characters' appearance, clothing, scene and lighting follow the reference image throughout without changing.\n\n"
        f"integrated_multimodal_description: {body}\n"
        "overall_soundscape: The sounds of clashing weapons, whooshing blade cuts through the air, footsteps and fabric movement in the wind.\n"
        "non_diegetic_music: N/A"
    )


async def _translate_beats_to_blocks(beats):
    """16 个中文节拍 → 8b 一次性翻译为结构化英文镜头块（JSON 数组）；失败返回 None"""
    import json as _json
    try:
        async def _call():
            payload = {
                "model": "qwen3:8b",
                "messages": [
                    {"role": "system", "content": H3_BEAT_TRANSLATE_SYSTEM},
                    {"role": "user", "content": "\n".join(f"{i+1}. {b}" for i, b in enumerate(beats))},
                ],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 4096},
                "keep_alive": 0,
            }
            async with httpx.AsyncClient(timeout=300) as client:
                r = await client.post("http://127.0.0.1:11434/api/chat", json=payload)
                r.raise_for_status()
                return r.json().get("message", {}).get("content", "")
        raw = (await gpu_serial("martial_arts_beats_translate", _call())).strip()
        start, end = raw.find("["), raw.rfind("]")
        if start == -1 or end <= start:
            return None
        blocks = _json.loads(raw[start:end + 1])
        if not isinstance(blocks, list) or len(blocks) != len(beats):
            return None
        out = []
        for b in blocks:
            if not isinstance(b, dict):
                return None
            cam = str(b.get("camera") or "").strip()
            act = str(b.get("subject_action") or "").strip()
            if not cam or not act:
                return None
            out.append({
                "camera": cam,
                "subject_action": act,
                "opponent_reaction": str(b.get("opponent_reaction") or "").strip(),
                "camera_motion": str(b.get("camera_motion") or "").strip(),
            })
        return out
    except Exception:
        return None


async def _to_h3_video_prompt(zh: str, duration: int = 15) -> str:
    """中文视频提示词 → H3 官方模板（确定性节拍压缩 + 时间戳组装）。

    16 拍全部保留（与宫格一一对应），每拍分配递增时间戳（[Shot N] At 00:SS.mmm），
    机位速度自然内嵌英文句——结构完全符合 MiniMax-H3 h3-prompt-writing 模板。
    时长受 H3 模型训练范围限制（5-15 秒，362 帧上限）。
    """
    if not re.search(r"[\u4e00-\u9fff]", zh):
        return zh  # 已是英文
    beats, persona, sound = _extract_video_beats(zh)
    beats = _dedupe_video_beats(beats, max_n=16)
    if len(beats) >= 2:
        try:
            blocks = await _translate_beats_to_blocks(beats)
            if blocks and len(blocks) == len(beats):
                return _assemble_h3_blocks(blocks, persona=persona, duration=max(4, min(duration, 60)))
        except Exception:
            pass
        try:
            return _assemble_h3_prompt(beats, persona=persona, duration=max(4, min(duration, 60)))
        except Exception:
            pass
    # 拆不出节拍 → 兜底：8b 转换（旧逻辑）
    try:
        async def _call():
            payload = {
                "model": "qwen3:8b",
                "messages": [
                    {"role": "system", "content": H3_VIDEO_CONVERT_SYSTEM},
                    {"role": "user", "content": zh},
                ],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 4096},
                "keep_alive": 0,
            }
            async with httpx.AsyncClient(timeout=240) as client:
                r = await client.post("http://127.0.0.1:11434/api/chat", json=payload)
                r.raise_for_status()
                return r.json().get("message", {}).get("content", "")
        converted = (await gpu_serial("martial_arts_h3_convert", _call())).strip()
    except Exception:
        return zh
    if (
        "integrated_multimodal_description" not in converted
        or "overall_soundscape" not in converted
        or "non_diegetic_music" not in converted
        or len(converted) < 100
        or "<style" in converted
    ):
        return zh  # 字段缺失或模板复读 → 回退中文原样
    return converted


# ==================== 分段拼接（8+8 拍 × 15 秒 → 30 秒） ====================

async def _assemble_segment_h3(beats: list, persona: str, duration: int = 15) -> str:
    """把一拍子集（8 拍）组装成 H3 官方模板（15 秒舒适区）。翻译失败回退中文模板/原句。"""
    if len(beats) >= 2:
        try:
            blocks = await _translate_beats_to_blocks(beats)
            if blocks and len(blocks) == len(beats):
                return _assemble_h3_blocks(blocks, persona=persona, duration=max(4, min(duration, 15)))
        except Exception:
            pass
        try:
            return _assemble_h3_prompt(beats, persona=persona, duration=max(4, min(duration, 15)))
        except Exception:
            pass
    return "\n".join(beats)


async def _extract_video_last_frame(video_url: str, out_path: str) -> bool:
    """用 ffmpeg 抽取视频最后一帧（第二段首帧 = 第一段真实尾帧，保证时空连贯）。"""
    import subprocess
    loop = asyncio.get_event_loop()

    def _run():
        return subprocess.run(
            ["ffmpeg", "-y", "-sseof", "-0.2", "-i", video_url, "-frames:v", "1", out_path],
            capture_output=True, text=True,
        )

    try:
        r = await loop.run_in_executor(None, _run)
        return r.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False


async def _concat_two_videos(path1: str, path2: str, out_path: str) -> bool:
    """ffmpeg concat 两段视频（同模型同参数：逐帧拼接，不重编码中间帧）。"""
    import subprocess
    loop = asyncio.get_event_loop()

    def _run():
        return subprocess.run(
            ["ffmpeg", "-y", "-i", path1, "-i", path2,
             "-filter_complex",
             "[0:v:0]setpts=PTS-STARTPTS[v0];[1:v:0]setpts=PTS-STARTPTS[v1];[v0][v1]concat=n=2:v=1:a=0[v]",
             "-map", "[v]", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-pix_fmt", "yuv420p", out_path],
            capture_output=True, text=True,
        )

    try:
        r = await loop.run_in_executor(None, _run)
        return r.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False


# ==================== 参考图解析（qwen2.5vl:3b 视觉资产） ====================

REFERENCE_ANALYZE_SYSTEM = """你是资深武术指导+美术指导+摄影指导。解析用户上传的参考图（武打场景/人物/构图参考图），提取可复用的视觉资产，输出严格 JSON（只描述图中可见事实，看不清的字段留空，禁止臆造）：

{
  "characters": [
    {"name": "角色名", "gender": "性别(男/女/中性)", "age": "年龄(少年/青年/中年/老年或具体)", "physique": "体型气质", "clothing": "服装(材质+颜色+款式)", "weapon": "兵器类型", "weapon_length": "兵器长短尺寸(如单刀刃长约二尺五/无)", "martial": "疑似武术体系"}
  ],
  "scene": {"place": "场景地点", "layout": "场景布置(空间结构/关键道具)", "composition": "构图(主体在画面中的位置/景别/镜头角度/空间层次)", "scale": "尺度纵深(长宽/距离量化，如吊桥横跨数百米峡谷)", "lighting": "光感(明暗/光源/色调)", "weather": "时段天气氛围"},
  "action_style": "动作风格与标志性姿态(如黄飞鸿式沉桥/叶问寸劲/腾跃翻滚，通用特征不写角色名)",
  "camera": "镜头语言参考(运镜方式/景别节奏)"
}
规则：图中有多名人物逐一列出 characters；武器未出现则 weapon=无、weapon_length=无；服装写材质+颜色+款式；构图写清主体位置与景别；场景尺度尽量量化。只输出 JSON，不要其他文字。"""


class AnalyzeReferenceRequest(BaseModel):
    image_url: str
    requirement: Optional[str] = ""


def _reference_assets_markdown(raw: str) -> str:
    """解析 qwen2.5vl 输出 → 资产 markdown（容错：非 JSON 时原样返回）"""
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}")
    data = None
    if start != -1 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
        except Exception:
            data = None
    if not data:
        return f"（参考图解析未返回结构化资产，原文）\n{raw[:2000]}"
    lines = []
    chars = data.get("characters") or []
    if isinstance(chars, list) and chars:
        lines.append("- 人物：")
        for c in chars:
            if not c.get("name"):
                continue
            detail = "、".join(x for x in [
                c.get("gender") or "", c.get("age") or "", c.get("physique") or "",
                c.get("clothing") or "", c.get("weapon") or "",
                (c.get("weapon_length") or "") if (c.get("weapon") or "").lower() not in ("无", "徒手") else "",
            ] if x)
            lines.append(f"  - {c.get('name')}：{detail}")
    sc = data.get("scene") or {}
    if isinstance(sc, dict) and any(sc.values()):
        lines.append("- 场景布置：")
        for k, label in [("place", "地点"), ("layout", "布置"), ("composition", "构图"),
                          ("scale", "尺度纵深"), ("lighting", "光感"), ("weather", "时段天气")]:
            v = str(sc.get(k) or "").strip()
            if v:
                lines.append(f"  - {label}：{v}")
    ast = str(data.get("action_style") or "").strip()
    if ast:
        lines.append(f"- 动作风格：{ast}")
    cam = str(data.get("camera") or "").strip()
    if cam:
        lines.append(f"- 镜头参考：{cam}")
    return "\n".join(lines)


@router.post("/upload-reference", response_model=dict)
async def upload_martial_reference(file: UploadFile = File(...)):
    """参考图上传：存 user_story/martial_arts_references/，返回可访问 URL"""
    allowed = ["image/png", "image/jpeg", "image/jpg", "image/webp"]
    if file.content_type not in allowed:
        return {"success": False, "message": f"不支持的类型 {file.content_type}，仅支持 PNG/JPG/WEBP"}
    try:
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp"}[file.content_type]
        ref_dir = Path(file_storage.base_dir) / "martial_arts_references"
        ref_dir.mkdir(parents=True, exist_ok=True)
        fname = f"ref_{uuid_mod.uuid4().hex[:12]}{ext}"
        fpath = ref_dir / fname
        content = await file.read()
        with open(fpath, "wb") as f:
            f.write(content)
        rel = fpath.relative_to(file_storage.base_dir)
        return {"success": True, "data": {"image_url": f"/api/files/{rel.as_posix()}", "file_path": str(fpath)}, "message": "上传成功"}
    except Exception as exc:
        return {"success": False, "message": f"上传失败: {exc}"}


@router.post("/analyze-reference", response_model=dict)
async def analyze_martial_reference(data: AnalyzeReferenceRequest):
    """参考图 → qwen2.5vl:3b 视觉解析 → 结构化资产（H3 对齐，供 generate 使用）"""
    image_url = (data.image_url or "").strip()
    if not image_url:
        return {"success": False, "message": "缺少 image_url"}
    if image_url.startswith("/api/files/"):
        local_path = Path(file_storage.base_dir) / image_url[len("/api/files/"):]
    elif image_url.startswith("http"):
        return {"success": False, "message": "暂只支持本地上传的参考图"}
    else:
        local_path = Path(image_url)
    if not local_path.exists():
        return {"success": False, "message": f"参考图不存在: {local_path}"}
    try:
        b64 = base64.b64encode(local_path.read_bytes()).decode()
    except Exception as exc:
        return {"success": False, "message": f"读取参考图失败: {exc}"}

    _tid = _martial_start("analyze-reference", f"参考图解析：{local_path.name}")
    try:
        async def _call():
            payload = {
                "model": "qwen2.5vl:3b",
                "messages": [
                    {"role": "system", "content": REFERENCE_ANALYZE_SYSTEM},
                    {
                        "role": "user",
                        "content": f"参考图解析。需求补充（可能为空）：{data.requirement or ''}\n请按系统规则输出 JSON 视觉资产。",
                        "images": [b64],
                    },
                ],
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 2048},
                "keep_alive": 0,
            }
            async with httpx.AsyncClient(timeout=300) as client:
                r = await client.post("http://127.0.0.1:11434/api/chat", json=payload)
                r.raise_for_status()
                return r.json().get("message", {}).get("content", "")
        content = await gpu_serial("martial_arts_ref_analyze", _call())
    except Exception as exc:
        _martial_finish(_tid, error=f"参考图解析失败: {exc}")
        return {"success": False, "message": f"参考图解析失败: {exc}"}
    _martial_finish(_tid)
    assets_md = _reference_assets_markdown(content)
    return {"success": True, "data": {"assets": assets_md, "raw": content}}


# ---------- 接口 ----------

@router.post("/generate-image", response_model=dict)
async def generate_martial_arts_image(
    data: GenerateImageRequest,
    db: Session = Depends(get_db),
):
    """文生图：武指角色形象图（Flux2-Klein-4B，串行防显存溢出）。

    输入为角色描述（由前端从人物锚定卡组装），输出全身角色形象图，
    作为后续图生图（角色参考 + 分镜文字 → 分镜图）的角色一致性锚点。
    """
    prompt = (data.prompt or "").strip()
    if not prompt:
        return {"success": False, "message": "请先提供角色描述（先生成武打分镜提示词，取人物锚定卡）"}
    # 画面结构硬约束（确定性兜底）：人物与场景深度融合、防虚空/防分离/防僵硬
    if not any(k in prompt for k in ("踩实", "虚空", "脱离", "漂浮", "深度融合")):
        prompt = (prompt + "\n[画面结构硬约束]：人物必须与场景深度融合一体——双脚踩实地面/桥板/台阶，身体置于场景纵深中，与场景元素（地面/栏杆/立柱/树木/山石）产生明确接触；全身完整入画，禁止人物脱离场景、禁止悬浮在虚空背景、禁止人物与背景分离成两层；人物动作是武术姿态（弓步/沉马/护胸/出拳/格挡等可见姿态），禁止僵直站桩。").strip()
    # 手动附加约束：追加到提示词末尾，约束生图稳定性（不影响自动生成的锚定卡描述）
    extra = (data.extra_prompt or "").strip()
    if extra:
        prompt = f"{prompt}\n[手动附加约束]：{extra}"

    # 生图提示词英文化：Flux2-Klein-4B（qwen3_4b CLIP）对英文提示词执行更稳定
    try:
        prompt_en = await _translate_image_prompt(prompt)
    except Exception:
        prompt_en = prompt
    if prompt_en != prompt:
        _martial_start("generate-image", "生图提示词已英文化（qwen3:8b 翻译）")
    prompt = prompt_en

    width, height = ASPECT_RATIO_DIMS.get(data.aspect_ratio or "1:1", ASPECT_RATIO_DIMS["1:1"])

    async def _gen():
        await _free_comfyui_cache()
        workflow = _build_flux2_4b_text2img_workflow(prompt, width, height)
        return await _queue_and_wait(workflow, save_node_id="13", timeout=1800)

    try:
        result = await gpu_serial("martial_arts_image", _gen())
    except Exception as exc:
        return {"success": False, "message": f"文生图失败: {exc}"}

    if not result.get("success"):
        return {"success": False, "message": result.get("message") or "文生图失败"}

    image_url = result.get("image_url")
    _patch_history_url(db, data.history_id, character_image_url=image_url)

    return {
        "success": True,
        "data": {"image_url": image_url, "aspect_ratio": data.aspect_ratio or "1:1"},
        "message": "角色形象图生成成功",
    }


@router.post("/edit-image", response_model=dict)
async def edit_martial_arts_image(
    data: EditImageRequest,
    db: Session = Depends(get_db),
):
    """分镜图：16 格文字分镜 → 确定性 4×4 十六宫格（4 张 2×2 子图 + 程序拼图）。

    单张 16 格文生图（Flux2-4B）执行随机（12/14/8/6 格，视觉校验不可靠）；
    改为 4 张 2×2 四宫格子图（Flux2-4B 对 2×2 稳定）+ PIL 拼成 4×4，结构 100% 确定。
    人物一致性用锚定卡 persona 注入每张子图。
    """
    prompt = (data.prompt or "").strip()
    if not prompt:
        return {"success": False, "message": "请提供文字分镜提示词"}

    persona = ""
    if data.history_id:
        row = db.query(MartialArtsHistory).filter(MartialArtsHistory.id == data.history_id).first()
        if row:
            persona = _card_extract_persona(row.character_card or "")

    head_txt, panels = _parse_panels(prompt)
    _tid = _martial_start("edit-image", "Flux2-4B 2×2 子图 ×4 → 4×4 十六宫格")

    async def _gen_block(block_prompt_zh: str):
        # 翻译（内部自带 gpu_serial 锁）必须在锁外执行：否则 gpu_serial 持有全局锁时
        # 内部再抢同一把 asyncio.Lock → 自死锁（本任务永远等锁，卡死 20+ 分钟）
        try:
            en = await _translate_image_prompt(block_prompt_zh)
        except Exception:
            en = block_prompt_zh
        if not re.search(r"[\u4e00-\u9fff]", en):
            en += ("\nRENDER EXACTLY 2x2 grid of FOUR EQUAL-SIZED SQUARE panels with thin borders; "
                   "panel top-left / top-right / bottom-left / bottom-right exactly as described above; "
                   "FORBIDDEN: more or fewer than 4 panels, no 3x3, no 1x4, no free collage, faces clear and symmetric, NO numbers, NO labels, NO text, NO watermark.")
        else:
            en += ("\n必须渲染 2×2 四宫格：四个等大正方形格子（左上/右上/左下/右下）与描述一一对应，"
                   "格间细边框；禁止 4 格以外的其他格数、禁止自由拼贴、面部清晰端正。")

        async def _gen():
            _martial_update(_tid, stage="子图生成（2×2 四宫格）")
            await _free_comfyui_cache()
            workflow = _build_flux2_4b_text2img_workflow(en, 544, 544, seed=random.randint(0, 2 ** 31 - 1))
            return await _queue_and_wait(workflow, save_node_id="13", timeout=1800)
        return await gpu_serial("martial_arts_edit_image", _gen())

    images = []
    try:
        for bi, block in enumerate(_GRID_BLOCKS):
            parts = [head_txt]
            if persona:
                parts.insert(0, f"【人物设定】{persona}")
            for loc, gnum in (("左上格", block[0]), ("右上格", block[1]),
                              ("左下格", block[2]), ("右下格", block[3])):
                parts.append(f"{loc}：{panels.get(gnum, '')}")
            parts.append("（本子图只含上述四个格子，画面必须严格四格等大，禁止多余格子）")
            block_zh = "\n".join(parts)
            res = None
            for sub in range(3):
                _martial_update(_tid, stage=f"子图 {bi + 1}/4 生成（尝试 {sub + 1}/3）")
                res = await _gen_block(block_zh)
                if res.get("success"):
                    break
            if not res or not res.get("success"):
                _martial_finish(_tid, error=(res or {}).get("message") or "子图生成失败")
                return {"success": False, "message": (res or {}).get("message") or "子图生成失败"}
            import urllib.request as _ur
            img_data = _ur.urlopen(res["image_url"], timeout=60).read()
            import io as _io
            from PIL import Image as _PIL
            images.append(_PIL.open(_io.BytesIO(img_data)).convert("RGB"))
            _martial_update(_tid, stage=f"子图 {bi + 1}/4 完成")
    except Exception as exc:
        _martial_finish(_tid, error=f"分镜图生成失败: {exc}")
        return {"success": False, "message": f"分镜图生成失败: {exc}"}

    try:
        out_dir = r"F:\Develop\ComfyUI\output"
        os.makedirs(out_dir, exist_ok=True)
        fn = f"martial_arts_merged_{int(time.time())}.png"
        out_path = os.path.join(out_dir, fn)
        _merge_2x2_grid(images, out_path)
    except Exception as exc:
        _martial_finish(_tid, error=f"拼图失败: {exc}")
        return {"success": False, "message": f"拼图失败: {exc}"}

    image_url = f"http://127.0.0.1:8188/view?filename={fn}&type=output"
    _patch_history_url(db, data.history_id, storyboard_image_url=image_url)
    _martial_finish(_tid)

    return {
        "success": True,
        "data": {"image_url": image_url},
        "message": "分镜图生成成功（4×4 十六宫格，2×2 子图拼接，结构确定性）",
    }


@router.post("/generate-video", response_model=dict)
async def generate_martial_arts_video(
    data: GenerateVideoRequest,
    db: Session = Depends(get_db),
):
    """图生视频：分镜图/角色图 → H3 视频（ref2va 单参考图 / first_last 首尾帧）。

    显存治理：视频前 ComfyUI /free 卸载 flux 缓存 + queue_prompt 卸载 Ollama，
    H3(qwen3vl-32B int8 + ref2va unet + vae) 独占 32G 卡约 23G 可用显存。
    """
    prompt = (data.prompt or "").strip()
    if not prompt:
        return {"success": False, "message": "请先提供视频提示词（先生成武打分镜提示词）"}
    if not data.image_url:
        return {"success": False, "message": "请先提供首帧/参考图（先生成分镜图或角色图）"}

    # 注入锚定卡人物特征（性别/年龄/服装/武器），防止视频人物性别漂移
    persona = ""
    if data.history_id:
        row = db.query(MartialArtsHistory).filter(MartialArtsHistory.id == data.history_id).first()
        if row:
            persona = _card_extract_persona(row.character_card or "")
    if persona and persona not in prompt:
        prompt = f"【人物特征（以此为准，性别与外貌严禁改变）】{persona}\n\n{prompt}"

    # 视频提示词英文化：中文（展示）→ H3 英文模板（执行）
    # 分段拼接模式跳过整体英文化（保留中文节拍，由分段分支逐段组装）
    _is_split_mode = data.split_segments and (data.mode or "ref2va") == "ref2va" and int(data.duration_seconds or 4) >= 24
    prompt_h3 = prompt
    if not _is_split_mode:
        try:
            prompt_h3 = await _to_h3_video_prompt(prompt, duration=int(data.duration_seconds or 15))
        except Exception:
            prompt_h3 = prompt
        if prompt_h3 != prompt:
            _martial_start("generate-video", "视频提示词已转 H3 英文模板")
        prompt = prompt_h3

    mode = (data.mode or "ref2va").strip()
    if mode not in ("ref2va", "first_last", "three_frame", "four_frame"):
        return {"success": False, "message": "mode 仅支持 ref2va / first_last / three_frame / four_frame"}
    if mode == "first_last" and not data.second_image_url:
        return {"success": False, "message": "首尾帧模式需要提供尾帧图（请先用图生图生成尾帧）"}
    if mode in ("three_frame", "four_frame") and not data.image_is_storyboard:
        return {"success": False, "message": "多关键帧模式需要分镜海报（请先完成②分镜图生成）"}
    if not (4 <= (data.duration_seconds or 4) <= 60):
        return {"success": False, "message": "时长需在 4-60 秒之间（H3 节点帧数上限 3600 帧 ≈ 150 秒；45/60 秒为实测范围，超 30 秒可能增加 OOM 风险）"}

    # LoadImage 只认 input 目录：把 output 目录的参考图下载回传为 input 文件
    first_filename = await _ensure_comfyui_input_filename(data.image_url)
    if not first_filename:
        return {"success": False, "message": "参考图上传失败，请先生成分镜图或角色图"}

    # H3 读不懂 4x4 分镜海报细节 → 分镜图裁第1格作单帧参考，配合节拍叙事演绎（角色图原样）
    if data.image_is_storyboard:
        first_filename = await _crop_grid_cell(data.image_url, 0) or first_filename

    wf_type = {
        "ref2va": "video",
        "first_last": "first_last_video",
        "three_frame": "three_frame_video",
        "four_frame": "four_frame_video",
    }[mode]
    wf_info = _get_active_workflow_json(wf_type)
    if not wf_info:
        return {"success": False, "message": f"未找到激活的{'图生视频' if mode=='ref2va' else '首尾帧'}工作流，请到系统配置检查"}

    # ── 分段拼接模式（split_segments + ref2va + 时长≥24）：16 拍拆 8+8，两段各 15 秒 → 拼 30 秒 ──
    if _is_split_mode:
        try:
            seg_workflow = wf_info["workflow"]
            seg_mapping = wf_info["node_mapping"]
            seg_builder = WorkflowBuilder()
            seg_save_node = str(seg_mapping.get("video_save_node_id", "150"))
            _tid = _martial_start("generate-video", "分段拼接 30s")

            _martial_update(_tid, stage="分段拼接：16 拍拆 8+8（两段各 15 秒）")
            seg_beats, _seg_persona, _seg_sound = _extract_video_beats(prompt)
            seg_beats = _dedupe_video_beats(seg_beats, max_n=16)
            if len(seg_beats) < 8:
                _martial_finish(_tid, error="分段拼接需要至少 8 个动作节拍")
                return {"success": False, "message": "分段拼接需要至少 8 个动作节拍（当前提示词解析不足）"}
            mid = len(seg_beats) // 2
            seg1_beats, seg2_beats = seg_beats[:mid], seg_beats[mid:]
            persona = _seg_persona or persona
            h3_1 = await _assemble_segment_h3(seg1_beats, persona, duration=15)
            h3_2 = await _assemble_segment_h3(seg2_beats, persona, duration=15)
            _martial_update(_tid, stage="分段 1/2：ComfyUI 图生视频（H3 15s）")

            async def _gen_seg(workflow_: dict, save_node_: str, prompt_h3_: str):
                import copy as _copy
                wf = _copy.deepcopy(workflow_)
                seg_builder._set_prompt(wf, str(seg_mapping.get("prompt_node_id", "")), prompt_h3_)
                duration_node = str(seg_mapping.get("duration_seconds_node_id", "") or "")
                if duration_node and duration_node in wf:
                    seg_builder._set_value(wf, duration_node, 15)
                seg_builder._set_random_seed(wf, random.randint(1, 2**32))
                await _free_comfyui_cache()
                return await _queue_and_wait(wf, save_node_, timeout=3600)

            # 段 1 参考图 = 分镜第 1 格（单帧起点）
            seg_ref_node = str(seg_mapping.get("reference_image_node_id", "137"))
            if seg_ref_node in seg_workflow:
                seg_workflow[seg_ref_node].setdefault("inputs", {})["image"] = first_filename
            # 段 1：分镜第 1 格作首帧
            r1 = await gpu_serial("martial_arts_video_seg1", _gen_seg(seg_workflow, seg_save_node, h3_1))
            if not r1.get("success") or not r1.get("video_url"):
                _martial_finish(_tid, error=(r1.get("message") or "分段 1 生成失败"))
                return {"success": False, "message": r1.get("message") or "分段 1 生成失败"}
            # 段 2 首帧 = 段 1 真实尾帧（保证时空连贯）
            import tempfile as _tempfile
            tmp_dir = _tempfile.mkdtemp(prefix="ma_seg_")
            seg1_path = os.path.join(tmp_dir, "seg1.mp4")
            last_frame_path = os.path.join(tmp_dir, "last.jpg")
            try:
                import subprocess as _sp
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: _sp.run(
                    ["ffmpeg", "-y", "-i", r1["video_url"], "-c", "copy", seg1_path],
                    capture_output=True))
            except Exception:
                seg1_path = ""
            ok_last = await _extract_video_last_frame(r1["video_url"], last_frame_path)
            if not ok_last:
                _martial_finish(_tid, error="抽取段 1 尾帧失败，无法保证拼接连贯")
                return {"success": False, "message": "抽取段 1 尾帧失败"}
            client = ComfyUIClient()
            up = await client.upload_image(last_frame_path)
            if not up.get("success") or not up.get("filename"):
                _martial_finish(_tid, error="段 1 尾帧上传失败")
                return {"success": False, "message": "段 1 尾帧上传失败"}
            seg_workflow2 = json.loads(json.dumps(seg_workflow))
            ref_node = str(seg_mapping.get("reference_image_node_id", "137"))
            if ref_node in seg_workflow2:
                seg_workflow2[ref_node].setdefault("inputs", {})["image"] = up["filename"]
            _martial_update(_tid, stage="分段 2/2：ComfyUI 图生视频（H3 15s，首帧=段1尾帧）")
            r2 = await gpu_serial("martial_arts_video_seg2", _gen_seg(seg_workflow2, seg_save_node, h3_2))
            if not r2.get("success") or not r2.get("video_url"):
                _martial_finish(_tid, error=(r2.get("message") or "分段 2 生成失败"))
                return {"success": False, "message": r2.get("message") or "分段 2 生成失败"}
            seg2_path = os.path.join(tmp_dir, "seg2.mp4")
            try:
                import subprocess as _sp2
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: _sp2.run(
                    ["ffmpeg", "-y", "-i", r2["video_url"], "-c", "copy", seg2_path],
                    capture_output=True))
            except Exception:
                seg2_path = ""
            out_dir = r"F:\Develop\ComfyUI\output"
            os.makedirs(out_dir, exist_ok=True)
            concat_fn = f"martial_arts_concat_{int(time.time())}.mp4"
            concat_path = os.path.join(out_dir, concat_fn)
            if seg1_path and seg2_path and await _concat_two_videos(seg1_path, seg2_path, concat_path):
                video_url = f"http://127.0.0.1:8188/view?filename={concat_fn}&type=output"
            else:
                video_url = r2["video_url"]  # 拼接失败：退回段 2 成片（不阻断）
            try:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
            _patch_history_url(db, data.history_id, video_url=video_url, h3_prompt=h3_1 + "\n\n==SEG2==\n" + h3_2)
            _martial_finish(_tid)
            return {
                "success": True,
                "data": {"video_url": video_url, "mode": "split_segments", "duration_seconds": 30,
                         "h3_prompt": h3_1 + "\n\n==SEG2==\n" + h3_2},
                "message": "分段拼接成功（8+8 拍 × 15 秒 → 30 秒，段间首帧衔接）",
            }
        except Exception as exc:
            _martial_finish(_tid, error=f"分段拼接失败: {exc}")
            return {"success": False, "message": f"分段拼接失败: {exc}"}

    workflow = wf_info["workflow"]
    mapping = wf_info["node_mapping"]
    builder = WorkflowBuilder()
    builder._set_prompt(workflow, str(mapping.get("prompt_node_id", "")), prompt)

    if mode == "ref2va":
        ref_node = str(mapping.get("reference_image_node_id", "137"))
        if ref_node in workflow:
            workflow[ref_node].setdefault("inputs", {})["image"] = first_filename
    elif mode == "first_last":
        first_node = str(mapping.get("first_image_node_id", "137"))
        last_node = str(mapping.get("last_image_node_id", "139"))
        last_filename = await _crop_grid_cell(data.second_image_url, 15) or await _ensure_comfyui_input_filename(data.second_image_url)
        if not last_filename:
            return {"success": False, "message": "尾帧图上传失败，请先通过图生图生成尾帧"}
        if first_node in workflow:
            workflow[first_node].setdefault("inputs", {})["image"] = first_filename
        if last_node in workflow:
            workflow[last_node].setdefault("inputs", {})["image"] = last_filename
    else:
        # three_frame / four_frame：主参考=第1格（起点），关键帧=中段/终点格
        ref_node = str(mapping.get("reference_image_node_id", "137"))
        if ref_node in workflow:
            workflow[ref_node].setdefault("inputs", {})["image"] = first_filename
        key_cells = [7, 15] if mode == "three_frame" else [5, 10, 15]
        key_nodes = [
            str(mapping.get("keyframe_node_1", "")),
            str(mapping.get("keyframe_node_2", "")),
            str(mapping.get("keyframe_node_3", "")),
        ]
        for cell_idx, node_id in zip(key_cells, key_nodes):
            if not node_id or node_id not in workflow:
                continue
            fn = await _crop_grid_cell(data.image_url, cell_idx)
            if fn:
                workflow[node_id].setdefault("inputs", {})["image"] = fn

    # 时长（duration_seconds_node_id 优先，否则 frame_count_node_id）
    duration_node = str(mapping.get("duration_seconds_node_id", "") or "")
    if duration_node and duration_node in workflow:
        builder._set_value(workflow, duration_node, int(data.duration_seconds))

    save_node = str(mapping.get("video_save_node_id", "150"))
    builder._set_random_seed(workflow, random.randint(1, 2**32))

    _tid = _martial_start("generate-video", f"{mode} {data.duration_seconds}s")

    async def _gen():
        await _free_comfyui_cache()
        return await _queue_and_wait(workflow, save_node, timeout=3600)

    try:
        _martial_update(_tid, stage="ComfyUI 图生视频（H3）")
        result = await gpu_serial("martial_arts_video", _gen())
    except Exception as exc:
        _martial_finish(_tid, error=f"图生视频失败: {exc}")
        return {"success": False, "message": f"图生视频失败: {exc}"}

    if not result.get("success"):
        _martial_finish(_tid, error=result.get("message") or "图生视频失败")
        return {"success": False, "message": result.get("message") or "图生视频失败"}

    video_url = result.get("video_url") or result.get("image_url")
    if not video_url:
        _martial_finish(_tid, error="视频生成未返回结果")
        return {"success": False, "message": "视频生成未返回结果"}

    _patch_history_url(db, data.history_id, video_url=video_url, h3_prompt=prompt_h3 if prompt_h3 != prompt else "")
    _martial_finish(_tid)

    return {
        "success": True,
        "data": {"video_url": video_url, "mode": mode, "duration_seconds": data.duration_seconds,
                 "h3_prompt": prompt_h3 if prompt_h3 != prompt else ""},
        "message": "视频生成成功",
    }


# ==================== 媒体代理（同源展示/下载） ====================

@router.get("/media-proxy")
async def martial_arts_media_proxy(filename: str, subfolder: str = "", type: str = "output"):
    """把 ComfyUI /view 产物转成平台同源地址，供 <img>/<video>/<a download> 使用。

    背景：浏览器加载 <img>/<video> 时会自动携带 Referer，ComfyUI /view 对跨源
    Referer 请求返回 403，导致前端图片裂开、视频无法播放。
    本接口由后端（无 Referer）转发 8188 文件流，前端统一走
    /api/martial-arts/media-proxy?filename=...&subfolder=...&type=...
    """
    if not re.fullmatch(r"[\w.\-]+", filename or ""):
        raise HTTPException(status_code=400, detail="非法 filename")
    url = (
        f"http://127.0.0.1:8188/view"
        f"?filename={quote(filename)}&subfolder={quote(subfolder)}&type={quote(type)}"
    )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ComfyUI 转发失败: {exc}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"ComfyUI /view 返回 {resp.status_code}")
    media_type = resp.headers.get("content-type") or (
        "video/mp4" if filename.lower().endswith(".mp4") else "application/octet-stream"
    )
    return Response(content=resp.content, media_type=media_type)


# ==================== 历史任务持久化（刷新不丢，可回看/恢复） ====================

class HistoryCreateRequest(BaseModel):
    requirement: str
    options_json: Optional[str] = ""
    character_card: Optional[str] = ""
    image_prompt: Optional[str] = ""
    video_prompt: Optional[str] = ""
    raw: Optional[str] = ""


class HistoryUpdateRequest(BaseModel):
    """部分更新：只更新传入的非 None 字段"""
    requirement: Optional[str] = None
    options_json: Optional[str] = None
    character_card: Optional[str] = None
    image_prompt: Optional[str] = None
    video_prompt: Optional[str] = None
    raw: Optional[str] = None
    character_image_url: Optional[str] = None
    storyboard_image_url: Optional[str] = None
    video_url: Optional[str] = None


def _history_to_dict(h: MartialArtsHistory) -> dict:
    return {
        "id": h.id,
        "requirement": h.requirement,
        "optionsJson": h.options_json or "",
        "characterCard": h.character_card or "",
        "imagePrompt": h.image_prompt or "",
        "videoPrompt": h.video_prompt or "",
        "h3Prompt": h.h3_prompt or "",
        "raw": h.raw or "",
        "characterImageUrl": h.character_image_url or "",
        "storyboardImageUrl": h.storyboard_image_url or "",
        "videoUrl": h.video_url or "",
        "createdAt": h.created_at.isoformat() if h.created_at else "",
        "updatedAt": h.updated_at.isoformat() if h.updated_at else "",
    }


def _patch_history_url(db: Session, history_id: Optional[str], **fields) -> None:
    """生成成功后回写历史记录对应字段；静默失败不阻断主流程"""
    if not history_id:
        return
    row = db.query(MartialArtsHistory).filter(MartialArtsHistory.id == history_id).first()
    if not row:
        return
    changed = False
    for key, value in fields.items():
        if value:
            setattr(row, key, value)
            changed = True
    if changed:
        try:
            db.commit()
        except Exception:
            db.rollback()


@router.get("/history", response_model=dict)
async def list_martial_arts_history(db: Session = Depends(get_db)):
    """历史任务列表（倒序，最多 100 条）"""
    rows = db.query(MartialArtsHistory).order_by(
        MartialArtsHistory.created_at.desc()
    ).limit(100).all()
    return {"success": True, "data": [_history_to_dict(r) for r in rows]}


@router.get("/history/{history_id}", response_model=dict)
async def get_martial_arts_history(history_id: str, db: Session = Depends(get_db)):
    """单条历史任务详情（用于点击回看/恢复）"""
    row = db.query(MartialArtsHistory).filter(MartialArtsHistory.id == history_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="历史任务不存在")
    return {"success": True, "data": _history_to_dict(row)}


@router.get("/monitor", response_model=dict)
async def martial_arts_monitor():
    """武打任务进程监控：当前运行中任务 + 最近完成/失败记录（内存注册表）。"""
    now = time_mod.time()
    running = []
    for rec in MARTIAL_TASKS.values():
        running.append({
            "id": rec["id"], "name": rec["name"], "task_type": rec["task_type"],
            "status": rec["status"], "stage": rec["stage"], "detail": rec["detail"],
            "started_at": rec["started_at"],
            "minutes": round((now - rec["started_at"]) / 60, 1),
        })
    recent = []
    for rec in MARTIAL_RECENT:
        recent.append({
            "id": rec["id"], "name": rec["name"], "task_type": rec["task_type"],
            "status": rec["status"], "stage": rec["stage"], "detail": rec["detail"],
            "error": rec["error"],
            "started_at": rec["started_at"], "finished_at": rec["finished_at"],
            "elapsed_sec": rec["elapsed_sec"],
        })
    return {"success": True, "data": {"running": running, "recent": recent}}


@router.post("/history", response_model=dict)
async def create_martial_arts_history(data: HistoryCreateRequest, db: Session = Depends(get_db)):
    """手动创建历史任务（一般由 /generate 自动创建，此接口供补录）"""
    row = MartialArtsHistory(
        requirement=data.requirement,
        options_json=data.options_json,
        character_card=data.character_card,
        image_prompt=data.image_prompt,
        video_prompt=data.video_prompt,
        raw=data.raw,
        created_at=datetime.now(),  # 本地时间（避免 CURRENT_TIMESTAMP 存 UTC 导致前端晚8小时）
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "data": _history_to_dict(row), "message": "已保存历史任务"}


@router.put("/history/{history_id}", response_model=dict)
async def update_martial_arts_history(
    history_id: str, data: HistoryUpdateRequest, db: Session = Depends(get_db)
):
    """部分更新历史任务（提示词/产物 URL）"""
    row = db.query(MartialArtsHistory).filter(MartialArtsHistory.id == history_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="历史任务不存在")
    for field in ("requirement", "options_json", "character_card", "image_prompt",
                  "video_prompt", "raw", "character_image_url", "storyboard_image_url", "video_url"):
        value = getattr(data, field)
        if value is not None:
            setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return {"success": True, "data": _history_to_dict(row), "message": "已更新历史任务"}


@router.delete("/history/{history_id}", response_model=dict)
async def delete_martial_arts_history(history_id: str, db: Session = Depends(get_db)):
    """删除历史任务"""
    row = db.query(MartialArtsHistory).filter(MartialArtsHistory.id == history_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="历史任务不存在")
    db.delete(row)
    db.commit()
    return {"success": True, "message": "已删除历史任务"}

