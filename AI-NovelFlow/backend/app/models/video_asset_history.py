from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


def generate_uuid():
    return str(uuid.uuid4())


class VideoAssetHistory(Base):
    """视频资源替换历史任务：上传视频 → 漫剧逆向管线 → 分镜/资产/H3提示词 → 资产替换再导出 → 生成新视频"""

    __tablename__ = "video_asset_history"

    id = Column(String, primary_key=True, default=generate_uuid)
    video_name = Column(String, nullable=False)         # 上传视频文件名（含扩展名）
    status = Column(String, nullable=False, default="running")  # running / success / failed（分析管线状态）
    stage = Column(String, nullable=True)               # 当前阶段文案
    video_path = Column(Text, nullable=True)            # 输入视频绝对路径
    output_dir = Column(Text, nullable=True)            # 管线产物目录（output/<stem>）
    summary_json = Column(Text, nullable=True)          # 分析摘要（镜头数/角色数/场景数/时长）
    error = Column(Text, nullable=True)                 # 分析失败原因
    # ── 生成新视频（替换资产后）状态 ──
    video_status = Column(String, nullable=False, default="idle")  # idle / running / success / failed
    video_stage = Column(String, nullable=True)         # 视频生成阶段文案
    video_error = Column(Text, nullable=True)           # 视频生成失败原因
    video_summary_json = Column(Text, nullable=True)    # 逐镜进度与合并结果 JSON
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), onupdate=datetime.now)
