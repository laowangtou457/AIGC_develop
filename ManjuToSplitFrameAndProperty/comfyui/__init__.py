# -*- coding: utf-8 -*-
"""
ComfyUI 自定义节点包入口（漫剧抽帧与属性管线）。

安装：把本 comfyui/ 目录复制到 ComfyUI/custom_nodes/ManjuToSplitFrameAndProperty/
重启 ComfyUI 后，节点出现在分类 "MiniMax H3 / 漫剧 / 抽帧与属性" 下。
"""
import os
import sys

# 把工程根目录加入 sys.path，使 pipeline 包可被导入
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pipeline.comfyui_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS  # noqa: E402

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
