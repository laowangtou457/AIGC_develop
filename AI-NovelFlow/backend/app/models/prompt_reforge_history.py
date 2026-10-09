from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, Integer
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


def generate_uuid():
    return str(uuid.uuid4())


class PromptReforgeHistory(Base):
    """提示词提取与重构历史任务：输入提示词/剧本/小说 → 按导演模型提取节拍 → 重构为 AI 工具可生成级提示词集"""

    __tablename__ = "prompt_reforge_history"

    id = Column(String, primary_key=True, default=generate_uuid)
    title = Column(String, nullable=False)                       # 任务标题
    input_type = Column(String, nullable=False, default="text")  # text(直接输入) / file(上传文件)
    source_name = Column(String, nullable=True)                  # 文件名或标题
    input_text = Column(Text, nullable=False)                    # 原始输入全文
    input_summary = Column(Text, nullable=True)                  # 输入摘要（截断预览）
    director_model = Column(String, nullable=False, default="h3_ref2va")  # 导演模型：h3_ref2va / h3_i2v / martial_arts / general_cinematic
    target_platforms = Column(Text, nullable=True)               # 目标平台 JSON 数组：["minimax_h3","seedance","kling","veo","jimeng"]
    status = Column(String, nullable=False, default="running")   # running / success / failed
    stage = Column(String, nullable=True)                        # 阶段文案（提取中/重构中/组装中）
    output_dir = Column(Text, nullable=True)                     # 产物目录（data/prompt_reforge/<task_id>）
    output_md = Column(Text, nullable=True)                      # 提示词集 Markdown（缓存）
    report_json = Column(Text, nullable=True)                    # 提取节拍 + 重构报告 JSON
    error = Column(Text, nullable=True)                          # 失败原因
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), onupdate=datetime.now)
