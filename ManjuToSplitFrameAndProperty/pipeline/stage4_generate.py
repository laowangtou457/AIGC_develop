# -*- coding: utf-8 -*-
"""
阶段4：再生成提示词包
- 读取 storyboard.json + assets.json
- 逐镜头组装提示词（画面/台词/运镜/资产引用）
- 输出 prompt_pack.json + prompt_pack.md（可直接粘贴到目标视频生成平台）
- 内置合规自检清单

用法: python -m pipeline.stage4_generate <视频> [--platform seedance|kling|veo|runway|jimeng] [--config config.yaml]
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from .common import LOG, ensure_dir, load_config, load_json, save_json, setup_logger

PLATFORM_HINTS = {
    "seedance": "模型: Seedance | 图生视频，保持参考图人物与场景一致性",
    "kling": "模型: Kling | 图生视频，镜头运动幅度 5，保持角色一致性",
    "veo": "模型: Veo | 电影感提示词，自然光，写实",
    "runway": "模型: Runway Gen-3 | 关键词式提示，避免长句",
    "jimeng": "模型: 即梦 | 图生视频，风格化保持",
    "minimax_h3": "模型: MiniMax-H3 | 六段式提示词 + reference/i2v 两种生成模式（见 pipeline/h3_export.py）",
}

COMPLIANCE_CHECKLIST = [
    "本片的故事、台词、音乐是否全部来自你有权使用的内容（原创/授权/公有领域）？",
    "一句话梗概测试：向别人讲一遍新片，对方是否还能认出是某部具体作品？能认出=仍需拉开距离。",
    "人物形象若基于真人，是否已获得本人授权？",
    "产出内容是否遵守目标平台的 AI 内容标识与搬运规则？",
]


def fmt_t(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


def build_shot_prompt(shot: dict, char_by_shot: dict[int, list[dict]], style: str, ar: str) -> str:
    v = shot.get("visual") or {}
    lines = [f"【镜头 {shot['id'] + 1:03d}】{fmt_t(shot['start'])} - {fmt_t(shot['end'])} 秒"]

    scene = v.get("scene") or "待补充：场景"
    chars = char_by_shot.get(shot["id"], [])
    char_txt = "；".join(f"{c['name']}(参考图: {c['ref_images'][0]})" for c in chars) if chars else "待补充：人物"
    action = v.get("action") or "待补充：动作"
    lines.append(f"画面：{scene}；人物：{char_txt}；动作：{action}")

    costume = v.get("costume") or "待补充"
    props = "、".join(v.get("props") or []) or "无/待补充"
    lines.append(f"服装：{costume}；道具：{props}")

    camera = v.get("camera") or "待补充"
    lighting = v.get("lighting") or "待补充"
    lines.append(f"运镜：{camera}")
    lines.append(f"光线/色调：{lighting}")

    dialogue = shot.get("dialogue") or []
    if dialogue:
        lines.append("台词：" + " / ".join(f"「{d['text']}」" for d in dialogue))
    lines.append(f"风格：{style}（{ar}）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段4 再生成提示词包")
    parser.add_argument("video")
    parser.add_argument("--platform", default=None)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)
    setup_logger()

    cfg = load_config(args.config)
    gcfg = cfg["generate"]
    platform = args.platform or gcfg["platform"]
    style, ar = gcfg["style"], gcfg["aspect_ratio"]

    video = Path(args.video).resolve()
    if not video.exists():
        LOG.error("视频不存在: %s", video)
        return 1
    out_root = ensure_dir(cfg["workdir"] / video.stem)
    sb_path = out_root / "storyboard.json"
    if not sb_path.exists():
        LOG.error("缺少 %s，请先运行阶段2", sb_path)
        return 1
    storyboard = load_json(sb_path)
    assets = load_json(out_root / "assets.json") if (out_root / "assets.json").exists() else {}

    shots = storyboard["shots"]
    char_by_shot: dict[int, list[dict]] = {}
    for c in assets.get("characters", []):
        for sid in c.get("source_shots", []):
            char_by_shot.setdefault(sid, []).append(c)

    LOG.info("=== 阶段4 提示词包: %s（%d 镜头，平台=%s）===", video, len(shots), platform)

    # MiniMax H3 走专用导出（六段式提示词 + API payload + 资产校验）
    if platform == "minimax_h3":
        from . import h3_export

        try:
            h3_export.export_h3(str(video), cfg, mode=(gcfg.get("h3_mode") or ""))
        except FileNotFoundError as e:
            LOG.error("%s", e)
            return 1
        LOG.info("阶段4完成（H3 导出），其余平台提示词可另行生成")
        return 0

    shot_prompts = [build_shot_prompt(s, char_by_shot, style, ar) for s in shots]

    # Markdown 导出
    md = [
        f"# 提示词包 · {video.stem}",
        "",
        f"- 目标平台：**{platform}**（{PLATFORM_HINTS.get(platform, '')}）",
        f"- 全局风格：{style}（画幅 {ar}）",
        "",
        "## 资产清单",
        "",
    ]
    if assets.get("characters"):
        md.append("### 角色")
        for c in assets["characters"]:
            md.append(f"- {c['name']}（{c['id']}）：{len(c['ref_images'])} 张参考图，出现于镜头 {c['source_shots']}")
    if assets.get("scenes"):
        md.append("")
        md.append("### 场景")
        for sc in assets["scenes"]:
            md.append(f"- {sc['id']}：镜头 {sc['shot_ids']}，参考图 {sc['ref_images'][0]}")
    md += ["", "## 逐镜头提示词", ""]
    for p in shot_prompts:
        md += [p, ""]

    md += ["---", "## 合规自检（发布前必读）", ""]
    for i, item in enumerate(COMPLIANCE_CHECKLIST, 1):
        md.append(f"{i}. {item}")
    md += ["", f"> 生成时间：{datetime.datetime.now().isoformat(timespec='seconds')}（数据来自 {video.stem} 的解析结果）"]

    save_json({
        "video": str(video),
        "platform": platform,
        "style": style,
        "aspect_ratio": ar,
        "assets": assets,
        "shots": shot_prompts,
        "compliance_checklist": COMPLIANCE_CHECKLIST,
    }, out_root / "prompt_pack.json")

    md_path = out_root / "prompt_pack.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    LOG.info("提示词包已写出: %s / %s", md_path, out_root / "prompt_pack.json")
    LOG.info("阶段4完成: %d 个镜头提示词", len(shot_prompts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
