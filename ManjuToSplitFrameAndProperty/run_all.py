# -*- coding: utf-8 -*-
"""
一键运行整条管线：
  阶段1 视频解析 → 阶段2 内容结构化 → 阶段3 资产抽取 → 阶段4 提示词包

用法:
  python run_all.py <视频路径> [--asr-model off|tiny|base|small|medium]
      [--vlm-provider dashscope|openai] [--platform minimax_h3|seedance|kling|veo|runway|jimeng]
      [--skip 2,3] [--config config.yaml]

合规前提：本管线只处理你有权使用的内容（自有拍摄/授权素材/公有领域）。
发布前请阅读 output/<视频名>/prompt_pack.md 末尾的合规自检清单。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="短剧逆向重建管线：一键运行")
    parser.add_argument("video", help="输入视频路径")
    parser.add_argument("--asr-model", default=None, help="faster-whisper 模型，off=关闭")
    parser.add_argument("--vlm-provider", default=None, help="dashscope/openai，传空关闭")
    parser.add_argument("--platform", default=None, help="minimax_h3/seedance/kling/veo/runway/jimeng")
    parser.add_argument("--skip", default="", help="逗号分隔要跳过的阶段，如 2,3")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    video = Path(args.video).resolve()
    if not video.exists():
        print(f"视频不存在: {video}")
        return 1

    skip = {int(x) for x in args.skip.split(",") if x.strip()}
    stages = {1: "stage1_parse", 2: "stage2_structurize", 3: "stage3_assets", 4: "stage4_generate"}

    for n in (1, 2, 3, 4):
        if n in skip:
            print(f"[跳过] 阶段{n} {stages[n]}")
            continue
        cmd = [sys.executable, "-m", f"pipeline.{stages[n]}", str(video), "--config", args.config]
        if n == 2:
            if args.asr_model is not None:
                cmd += ["--asr-model", args.asr_model]
            if args.vlm_provider is not None:
                cmd += ["--vlm-provider", args.vlm_provider]
        if n == 4 and args.platform:
            cmd += ["--platform", args.platform]
        print(f"\n========== 阶段{n} {stages[n]} ==========")
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"阶段{n} 失败（exit={rc}），终止")
            return rc

    print("\n管线完成。产物位于 output/<视频名>/ 目录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
