from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


def generate_uuid():
    return str(uuid.uuid4())


class MartialArtsHistory(Base):
    """武术指导历史任务：一句话需求 → 提示词三件套 + 本地生成三产物"""

    __tablename__ = "martial_arts_history"

    id = Column(String, primary_key=True, default=generate_uuid)
    requirement = Column(Text, nullable=False)          # 一句话需求
    options_json = Column(Text, nullable=True)          # 高级选项 JSON（人物/体系/调性/装备/场景/宫格/兵器/影视参考）

    # 提示词三件套
    character_card = Column(Text, nullable=True)        # 人物锚定卡
    image_prompt = Column(Text, nullable=True)          # 文字分镜提示词（多宫格海报）
    video_prompt = Column(Text, nullable=True)          # 视频提示词（中文）
    h3_prompt = Column(Text, nullable=True)             # 实际执行用 H3 英文提示词（逐分镜独立镜头块）
    raw = Column(Text, nullable=True)                   # LLM 完整原始输出

    # 本地生成三产物（ComfyUI /view URL，展示时前端转同源代理）
    character_image_url = Column(Text, nullable=True)   # ① 角色形象图（文生图）
    storyboard_image_url = Column(Text, nullable=True)  # ② 分镜图（图生图）
    video_url = Column(Text, nullable=True)             # ③ 武打视频（图生视频）

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
