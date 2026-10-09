"""工具函数模块"""
from datetime import datetime
from typing import Optional


def format_datetime(dt: Optional[datetime]) -> Optional[str]:
    """
    将 datetime 对象格式化为本地时间字符串（无时区后缀）

    全工程已统一为【本地时间】存储（datetime.now() 写入，naive 无时区），
    前端直接展示该字符串即可与系统时间一致；不再做 UTC→本地的二次转换。
    """
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")
