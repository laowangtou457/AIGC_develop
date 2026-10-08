"""
迁移脚本：为 video_asset_history 表添加「生成新视频」状态字段
运行: cd backend && python migrations/add_video_asset_video_fields.py

新增字段（视频资源替换 → 生成新视频 链路）：
- video_status        新视频生成状态：idle / running / success / failed
- video_stage         当前阶段文案（如：正在生成镜头 3/10…）
- video_error         视频生成失败原因
- video_summary_json  逐镜进度与合并结果（镜头状态/产物路径/合并成片）
"""
import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATABASE_URL = "sqlite:///./novelflow.db"

COLUMNS = [
    ("video_status", "VARCHAR DEFAULT 'idle'"),
    ("video_stage", "VARCHAR"),
    ("video_error", "TEXT"),
    ("video_summary_json", "TEXT"),
]


def migrate():
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        for col, ddl_type in COLUMNS:
            try:
                conn.execute(text(f"ALTER TABLE video_asset_history ADD COLUMN {col} {ddl_type}"))
                print(f"✓ Added {col} column")
            except Exception as e:
                if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                    print(f"✓ {col} column already exists")
                else:
                    print(f"✗ Error adding {col}: {e}")
        conn.commit()

    print("\n✅ Migration completed!")


if __name__ == "__main__":
    migrate()
