# -*- coding: utf-8 -*-
"""
MiniMax H3 导出模块（pipeline.h3_export）

功能：
1. H3 提示词生成（对齐 MiniMax 官方 skills/h3-prompt-writing 规范，见 docs/references/）：
   - reference 全参考模式 → Ref2VA 六段式（ref-en.txt）：
     subject_definitions / summary / retention_analysis / detailed_description /
     overall_soundscape / non_diegetic_music
   - i2v 首帧模式 → I2VA 三字段（base-en.txt）：
     首帧对齐指令 + integrated_multimodal_description / overall_soundscape / non_diegetic_music
2. 逐镜 API payload（POST /v2/video_generation，多模态 content 数组）
3. 资产清单 + 映射文本（兼容「角色=图N」约定；<Subject N>=可复用内容，<Picture N>=图片锚点）
4. 资产合规校验（对照 MiniMax 官方限制：格式/体积/尺寸/数量/时长/提示词长度）
5. 支持两种生成模式：
   - reference 多模态参考生视频（角色/场景资产作 reference_image，适合"资产替换再生成"）
   - i2v 首帧图生视频（镜头关键帧作 first_frame）

官方规格（2026-09 核实，platform.minimaxi.com/docs/api-reference/video-generation-v2-create）：
- POST /v2/video_generation；model = MiniMax-H3 / MiniMax-H3-Max
- content 数组（text / image_url / video_url / audio_url），必须含非空 text
- reference_image ≤ 9 张；与 first_frame/last_frame 互斥
- 图片：JPG/JPEG/PNG/WEBP/HEIC/HEIF，单文件 ≤30MB，宽高 [256,5760]，宽高比 [0.4,2.5]
- resolution：H3=768P/2K；duration：H3=4~15 秒整数；ratio：adaptive/21:9/16:9/4:3/1:1/3:4/9:16
- 提示词 ≤ 7000 字符
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import LOG, ensure_dir, imread_unicode, load_config, load_json, save_json, setup_logger

# ---- 官方限制常量 ----
H3_LIMITS = {
    "image_formats": {"jpg", "jpeg", "png", "webp", "heic", "heif"},
    "image_max_bytes": 30 * 1024 * 1024,
    "image_dim_range": (256, 5760),
    "image_ratio_range": (0.4, 2.5),
    "max_reference_images": 9,
    "duration_range": (4, 15),
    "prompt_max_chars": 7000,
}

# 段落结构（官方 MiniMax-H3 技能 skills/h3-prompt-writing，2026-09 核实）
# - reference 全参考模式 → Ref2VA 六段式（references/ref-en.txt）
# - i2v 首帧模式 → I2VA 三字段（references/base-en.txt）
REF_SECTIONS = [
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
]
I2V_SECTIONS = [
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
]
SECTIONS = REF_SECTIONS  # 兼容旧引用（文档性常量，实际结构由 build_h3_prompt_ref / _i2v 控制）


# --------------------------------------------------------------------------
# 资产校验
# --------------------------------------------------------------------------
def validate_image(path: str | Path, root: Path) -> list[str]:
    """校验单张图片是否满足 H3 输入限制，返回警告列表（空=通过）。"""
    warnings: list[str] = []
    path = Path(path)
    p = path if path.is_absolute() else root / path
    if not p.exists():
        return [f"图片不存在: {path}"]
    ext = p.suffix.lower().lstrip(".")
    if ext not in H3_LIMITS["image_formats"]:
        warnings.append(f"{path}: 格式 {ext} 不在允许列表 {sorted(H3_LIMITS['image_formats'])}")
    size = p.stat().st_size
    if size > H3_LIMITS["image_max_bytes"]:
        warnings.append(f"{path}: {size/1048576:.1f}MB 超过 30MB 上限")
    try:
        import cv2
        img = imread_unicode(p)
        if img is None:
            warnings.append(f"{path}: 无法解码为图片")
        else:
            h, w = img.shape[:2]
            if not (H3_LIMITS["image_dim_range"][0] <= min(h, w)):
                warnings.append(f"{path}: 短边 {min(h, w)}px 小于 256px")
            if max(h, w) > H3_LIMITS["image_dim_range"][1]:
                warnings.append(f"{path}: 长边 {max(h, w)}px 大于 5760px")
            ratio = w / h if h else 0
            if not (H3_LIMITS["image_ratio_range"][0] <= ratio <= H3_LIMITS["image_ratio_range"][1]):
                warnings.append(f"{path}: 宽高比 {ratio:.2f} 超出 [0.4, 2.5]")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"{path}: 校验失败 {e}")
    return warnings


def validate_prompt_len(text: str) -> list[str]:
    if len(text) > H3_LIMITS["prompt_max_chars"]:
        return [f"提示词 {len(text)} 字符超过 7000 上限"]
    return []


# --------------------------------------------------------------------------
# 六段式提示词生成
# --------------------------------------------------------------------------
def _policy_flags(policy: str) -> tuple[bool, bool]:
    """返回 (禁BGM, 禁字幕)。"""
    p = policy or ""
    return ("无BGM" in p or "禁BGM" in p), ("禁字幕" in p)


def build_h3_prompt(shot: dict, tags: list[dict], cfg: dict, mode: str = "reference", speaker_ids: list[int] | None = None) -> str:
    """按模式分发提示词生成。

    - mode=reference：官方 Ref2VA 六段式（docs/references/ref-en.txt）
    - mode=i2v：官方 I2VA 三字段（docs/references/base-en.txt）
    tags 元素需含 subject/kind/tag/role/name/desc/image（由 build_h3_pack 注入）。
    """
    if mode == "i2v":
        return build_h3_prompt_i2v(shot, cfg, speaker_ids)
    return build_h3_prompt_ref(shot, tags, cfg, speaker_ids)


def _fmt_timestamp(seconds: float) -> str:
    """秒 → MM:SS.mmm（官方切点格式，如 00:03.500）。"""
    s = max(0.0, float(seconds or 0))
    mm, rem = divmod(int(s), 60)
    ms = int(round((s - int(s)) * 1000))
    if ms == 1000:
        rem += 1
        ms = 0
    return f"{mm:02d}:{rem:02d}.{ms:03d}"


def _detect_lang(text: str) -> str:
    """台词语言标签：含 CJK 字符 → 中文，否则 English（官方 <d>[Language] 约定）。"""
    return "中文" if any("\u4e00" <= ch <= "\u9fff" for ch in text) else "English"


def _clean_dialogue(text: str) -> str:
    """规范化台词（官方 ref-en.txt §5.4）：去 emoji/重复波浪号/尾部逗号，按语言补句读。"""
    import re
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\ufe0f]", "", text or "").strip()
    t = re.sub(r"[~～]{2,}", "", t).strip()
    t = re.sub(r"[;；,，、~～]+$", "", t).strip()
    if t and t[-1] not in ".?!。？!":
        t += "。" if any("\u4e00" <= ch <= "\u9fff" for ch in t) else "."
    return t


def build_h3_prompt_ref(shot: dict, tags: list[dict], cfg: dict, speaker_ids: list[int] | None = None) -> str:
    """官方 Ref2VA 六段式提示词（docs/references/ref-en.txt）。

    标签体系：<Subject N>=可复用内容（角色/场景），<Picture N>=图片锚点（对应 payload 图片顺序）。
    正文句式英文（官方结构词），内容字段保留 VLM 原文，对白原文进 <d>[语言]。
    """
    v = shot.get("visual") or {}
    shot_no = shot["id"] + 1
    dur_s = _shot_duration(shot, cfg)
    ar = cfg.get("ratio", "9:16")
    no_bgm, no_sub = _policy_flags(cfg.get("audio_text_policy", ""))
    retention = cfg.get("retention_level", "fully_preserved")  # 官方 4 档
    task_type = cfg.get("task_type", "reference generation")   # 官方 6 类可组合

    def scene_desc() -> str:
        return v.get("scene") or "（场景待补充）"

    lines: list[str] = []

    # 1) subject_definitions：<Subject N> 定义 + <Picture N> 来源
    lines.append("subject_definitions:")
    for t in tags:
        if t.get("kind") == "scene":
            lines.append(
                f"{t['subject']} is the environment {scene_desc()}, "
                f"whose appearance comes from {t['tag']}."
            )
        else:
            desc = t["desc"] or t["name"]
            lines.append(
                f"{t['subject']} is the character {t['name']} ({desc}), "
                f"whose appearance comes from {t['tag']}."
            )
    if not tags:
        lines.append("No reference assets for this shot; describe the scene purely from text.")

    # 2) summary：任务类型前缀（官方 6 类可 + 组合）+ 主体/场景/时长/画幅
    chars = "、".join(t["name"] for t in tags if t.get("kind") != "scene" and t["role"].startswith("c"))
    subject = chars or "the subject"
    lines.append("summary:")
    lines.append(
        f"[{task_type}] The target video shows {subject} in {scene_desc()}, "
        f"{v.get('action') or 'with the action described below'}, "
        f"lasting {dur_s} seconds at {ar} aspect."
    )

    # 3) retention_analysis：官方 4 档（fully_preserved / partially_preserved /
    #    attribute_transfer / weak_reference），由 config.yaml → h3.retention_level 控制
    lines.append("retention_analysis:")
    for t in tags:
        label = t["name"] if t.get("kind") != "scene" else scene_desc()
        lines.append(
            f"{t['subject']} (appears in [Shot {shot_no}]): {retention} - "
            f"the {label} keeps the appearance and consistency defined in subject_definitions."
        )

    # 4) detailed_description：英文句式骨架 + 内容原文 + 时间戳 + speaker ID + <d> 对白
    lines.append("detailed_description:")
    style = cfg.get("style", "写实电影质感")
    opener = f"The target video is in {style} style."
    shot_start = float(shot.get("start", 0) or 0)
    parts = []
    if shot_no == 1 or shot_start == 0:  # 首镜无时间戳（官方 §5.1）
        parts.append(f"[Shot {shot_no}] {scene_desc()}.")
    else:
        parts.append(f"[Shot {shot_no}] At {_fmt_timestamp(shot_start)}, the shot cuts to {scene_desc()}.")
    for t in tags:
        if t.get("kind") != "scene":
            parts.append(f"{t['subject']} appears as {t['name']}.")
    if v.get("camera"):
        parts.append(f"The camera: {v['camera']}.")
    if v.get("action"):
        parts.append(f"Action/event: {v['action']}.")
    if v.get("lighting"):
        parts.append(f"Lighting/tone: {v['lighting']}.")
    dialogue = shot.get("dialogue") or []
    spk = list(speaker_ids or range(1, len(dialogue) + 1))
    for d, sid in zip(dialogue, spk):
        text = _clean_dialogue(d.get("text", ""))
        parts.append(f"A speaker (S{sid}) says: <d>[{_detect_lang(text)}] {text}</d>")
    if no_sub:
        parts.append("The frame shows no subtitles, text, titles, watermarks, or decorative captions.")
    lines.append(opener + " " + " ".join(parts))

    # 5) overall_soundscape（官方：1-4 句，不重复对白；无音频分析时按规则占位）
    lines.append("overall_soundscape:")
    lines.append("N/A" if not dialogue else "Ambient sound focuses on dialogue with slight spatial reverb.")

    # 6) non_diegetic_music（官方：1-3 句，禁抽象情绪词）
    lines.append("non_diegetic_music:")
    music = v.get("music_note") or ""
    lines.append("N/A" if no_bgm or not music else music)

    text = "\n".join(lines)
    over = validate_prompt_len(text)
    if over:
        LOG.warning("镜头 %d 提示词超长: %s", shot["id"], "; ".join(over))
    return text


def build_h3_prompt_i2v(shot: dict, cfg: dict, speaker_ids: list[int] | None = None) -> str:
    """官方 I2VA 三字段提示词（docs/references/base-en.txt）。

    Part One = 首帧对齐指令（固定句，第一行 + 空行）；
    Part Two = integrated_multimodal_description / overall_soundscape / non_diegetic_music。
    <Picture 1> 恒指本镜首帧图（payload role=first_frame）。
    """
    v = shot.get("visual") or {}
    shot_no = shot["id"] + 1
    no_bgm, no_sub = _policy_flags(cfg.get("audio_text_policy", ""))

    def scene_desc() -> str:
        return v.get("scene") or "（场景待补充）"

    lines: list[str] = [
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced.",
        "",
    ]

    lines.append("integrated_multimodal_description:")
    style = cfg.get("style", "写实电影质感")
    shot_start = float(shot.get("start", 0) or 0)
    parts = []
    if shot_no == 1 or shot_start == 0:  # 首镜无时间戳（官方 §4.2）
        parts.append(f"[Shot {shot_no}] {style} style, {scene_desc()}.")
    else:
        parts.append(f"[Shot {shot_no}] At {_fmt_timestamp(shot_start)}, the shot cuts to {scene_desc()}.")
    if v.get("camera"):
        parts.append(f"The camera: {v['camera']}.")
    if v.get("action"):
        parts.append(f"Action/event: {v['action']}.")
    if v.get("lighting"):
        parts.append(f"Lighting/tone: {v['lighting']}.")
    dialogue = shot.get("dialogue") or []
    spk = list(speaker_ids or range(1, len(dialogue) + 1))
    for d, sid in zip(dialogue, spk):
        text = _clean_dialogue(d.get("text", ""))
        parts.append(f"A speaker (S{sid}) says: <d>[{_detect_lang(text)}] {text}</d>")
    if no_sub:
        parts.append("The frame shows no subtitles, text, titles, watermarks, or decorative captions.")
    lines.append(" ".join(parts))

    lines.append("overall_soundscape:")
    lines.append("N/A" if not dialogue else "Ambient sound focuses on dialogue with slight spatial reverb.")

    lines.append("non_diegetic_music:")
    music = v.get("music_note") or ""
    lines.append("N/A" if no_bgm or not music else music)

    text = "\n".join(lines)
    over = validate_prompt_len(text)
    if over:
        LOG.warning("镜头 %d 提示词超长: %s", shot["id"], "; ".join(over))
    return text


def _shot_duration(shot: dict, cfg: dict) -> int:
    """镜头时长夹取到 H3 允许范围 [4,15]。"""
    lo, hi = H3_LIMITS["duration_range"]
    if not cfg.get("duration_auto", True):
        try:
            return max(lo, min(hi, int(cfg.get("duration_seconds", 5))))
        except (TypeError, ValueError):
            pass
    dur = round(shot.get("end", 0) - shot.get("start", 0))
    return max(lo, min(hi, dur))


# --------------------------------------------------------------------------
# 逐镜 API payload
# --------------------------------------------------------------------------
def build_shot_payload(shot: dict, prompt_text: str, cfg: dict, mode: str, first_frame: str | None) -> dict:
    """组装 POST /v2/video_generation 的请求体。

    mode=reference：图片以 role=reference_image 传入（本镜角色/场景资产）；
    mode=i2v：图片以 role=first_frame 传入（镜头关键帧），ratio 固定 adaptive。
    本地图片以相对路径写入 url，提交工具会转 base64 data URL。
    """
    content: list[dict] = [{"type": "text", "text": prompt_text}]
    if mode == "i2v":
        if first_frame:
            content.append({"type": "image_url", "image_url": {"url": first_frame}, "role": "first_frame"})
        ratio = "adaptive"
    else:
        seen = set()
        for img in cfg.get("_shot_images", []):
            if img not in seen:
                seen.add(img)
                content.append({"type": "image_url", "image_url": {"url": img}, "role": "reference_image"})
        ratio = cfg.get("ratio", "9:16")
    payload = {
        "model": cfg.get("model", "MiniMax-H3"),
        "content": content,
        "resolution": cfg.get("resolution", "768P"),
        "duration": _shot_duration(shot, cfg),
        "ratio": ratio,
        "aigc_watermark": bool(cfg.get("aigc_watermark", False)),
        "_meta": {"shot_id": shot["id"]},
    }
    return payload


# --------------------------------------------------------------------------
# 组装整包（内存版，供 CLI 与 ComfyUI 节点共用）
# --------------------------------------------------------------------------
def build_h3_pack(storyboard: dict, assets: dict, cfg: dict, mode: str = "reference") -> dict:
    """由 storyboard + assets 生成 H3 导出包（不落盘）。

    返回 {prompts: [str], payloads: [dict], manifest: dict, mapping_text: str, warnings: [str]}
    """
    warnings: list[str] = []
    shots = storyboard.get("shots", [])
    characters = assets.get("characters", []) or []
    scenes = assets.get("scenes", []) or []

    # 角色 → 出现镜头（stage3 已记录 source_shots；为空时按 ref_time 兜底）
    char_by_shot: dict[int, list[dict]] = {}
    for c in characters:
        shots_ids = c.get("source_shots") or []
        if not shots_ids and c.get("ref_time") is not None:
            shots_ids = [next((s["id"] for s in shots if s["start"] <= c["ref_time"] <= s["end"]), None)]
        for sid in shots_ids or []:
            char_by_shot.setdefault(int(sid), []).append(c)

    scene_by_shot: dict[int, dict] = {}
    for sc in scenes:
        for sid in sc.get("shot_ids", []):
            scene_by_shot[int(sid)] = sc

    # 全局参考图编号（mapping：id=图N）
    mapping: list[tuple[str, str]] = []  # (asset_id, image_path)
    seen_ids = set()
    for c in characters:
        if c.get("ref_images") and c["id"] not in seen_ids:
            seen_ids.add(c["id"])
            mapping.append((c["id"], c["ref_images"][0]))
    for sc in scenes:
        if sc.get("ref_images") and sc["id"] not in seen_ids:
            seen_ids.add(sc["id"])
            mapping.append((sc["id"], sc["ref_images"][0]))
    mapping_text = "\n".join(f"{aid}=图{i+1}" for i, (aid, _) in enumerate(mapping))

    prompts: list[str] = []
    payloads: list[dict] = []
    per_shot_refs: list[dict] = []

    speaker_counter = 0  # 全局说话人 ID（官方 §5.4：按发声事件顺序分配，跨镜头复用）
    for shot in shots:
        c_tags = []
        for c in char_by_shot.get(shot["id"], []):
            c_tags.append({
                "subject": f"<Subject {len(c_tags) + 1}>",
                "kind": "character",
                "tag": f"<Picture {len(c_tags) + 1}>",
                "role": c.get("id", "?"),
                "name": c.get("name", c.get("id", "角色")),
                "desc": _char_desc(c, shot),
                "image": c.get("ref_images", [None])[0],
            })
        scene = scene_by_shot.get(shot["id"])
        if scene and scene.get("ref_images"):
            c_tags.append({
                "subject": f"<Subject {len(c_tags) + 1}>",
                "kind": "scene",
                "tag": f"<Picture {len(c_tags) + 1}>",
                "role": scene.get("id", "s?"),
                "name": "场景",
                "desc": (shot.get("visual") or {}).get("scene") or scene.get("id", "场景"),
                "image": scene["ref_images"][0],
            })
        cfg = dict(cfg)
        cfg["_shot_images"] = [t["image"] for t in c_tags if t.get("image")]
        if len(cfg["_shot_images"]) > H3_LIMITS["max_reference_images"]:
            warnings.append(f"镜头 {shot['id']} 参考图 {len(cfg['_shot_images'])} 张超过 H3 上限 9 张，请拆分镜头")
            cfg["_shot_images"] = cfg["_shot_images"][: H3_LIMITS["max_reference_images"]]
        speaker_ids: list[int] = []
        for _ in shot.get("dialogue") or []:
            speaker_counter += 1
            speaker_ids.append(speaker_counter)
        prompt = build_h3_prompt(shot, c_tags, cfg, mode, speaker_ids)
        first_frame = (shot.get("keyframes") or [None])[0]
        payload = build_shot_payload(shot, prompt, cfg, mode, first_frame)
        prompts.append(prompt)
        payloads.append(payload)
        per_shot_refs.append({"shot_id": shot["id"], "tags": c_tags, "images": cfg["_shot_images"]})

    manifest = {
        "model": cfg.get("model", "MiniMax-H3"),
        "mode": mode,
        "resolution": cfg.get("resolution", "768P"),
        "ratio": cfg.get("ratio", "9:16"),
        "mapping_text": mapping_text,
        "references": [
            {"id": aid, "image": img} for aid, img in mapping
        ],
        "shot_count": len(shots),
        "limits": {k: (sorted(v) if isinstance(v, set) else v) for k, v in H3_LIMITS.items()},
    }
    return {
        "prompts": prompts,
        "payloads": payloads,
        "manifest": manifest,
        "mapping_text": mapping_text,
        "per_shot_refs": per_shot_refs,
        "warnings": warnings,
    }


def _char_desc(c: dict, shot: dict) -> str:
    """角色语义描述：优先取该镜 VLM 描述中的人物条目，否则用中性占位。"""
    v = shot.get("visual") or {}
    chars = v.get("characters") or []
    if chars:
        parts = []
        for item in chars:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                # 兼容 VLM 返回结构化对象（name/description/attributes...）
                bits = []
                for key in ("name", "description", "外貌特征", "外观", "costume", "服装"):
                    val = item.get(key)
                    if isinstance(val, str) and val:
                        bits.append(val)
                    elif isinstance(val, (list, tuple)):
                        bits.extend(str(x) for x in val if x)
                parts.append("，".join(bits) if bits else str(item))
            else:
                parts.append(str(item))
        return "；".join(parts)
    return f"{c.get('name', c.get('id', '角色'))}（外观以参考图为准）"


# --------------------------------------------------------------------------
# 落盘导出（CLI 入口）
# --------------------------------------------------------------------------
def export_h3(video: str, cfg: dict, mode: str = "reference") -> Path:
    """读取 storyboard.json + assets.json，生成 output/<video>/minimax_h3/ 产物。"""
    video_path = Path(video).resolve()
    out_root = ensure_dir(cfg["workdir"] / video_path.stem)
    sb_path = out_root / "storyboard.json"
    if not sb_path.exists():
        raise FileNotFoundError(f"缺少 {sb_path}，请先运行阶段2")
    storyboard = load_json(sb_path)
    assets = load_json(out_root / "assets.json") if (out_root / "assets.json").exists() else {}

    h3cfg = dict(cfg.get("h3", {}))
    h3cfg.setdefault("style", cfg["generate"].get("style", "写实电影质感"))
    mode = mode or str(h3cfg.get("mode", "reference"))

    pack = build_h3_pack(storyboard, assets, h3cfg, mode)

    out_dir = ensure_dir(out_root / "minimax_h3")
    payload_dir = ensure_dir(out_dir / "payloads")

    # 校验资产图片
    validation = {}
    for ref in pack["manifest"]["references"]:
        aid, img = ref["id"], ref["image"]
        validation[aid] = validate_image(img, out_root)
    validation["_notes"] = pack["warnings"]

    # prompts.md（六段式逐镜提示词）
    md = [
        f"# MiniMax H3 提示词包 · {video_path.stem}",
        "",
        f"- 模型：**{pack['manifest']['model']}** | 模式：**{mode}** | 分辨率：{pack['manifest']['resolution']}",
        f"- 画幅：{pack['manifest']['ratio']} | 镜头数：{pack['manifest']['shot_count']}",
        "",
        "## 资源映射",
        "",
        "```",
        pack["mapping_text"],
        "```",
        "",
        "## 逐镜头提示词",
        "",
    ]
    section_name = "Ref2VA 六段式" if mode != "i2v" else "I2VA 三字段"
    for i, line in enumerate(md):
        if line == "## 逐镜头提示词":
            md[i] = f"## 逐镜头提示词（{section_name}）"
            break
    for i, (shot, prompt) in enumerate(zip(storyboard.get("shots", []), pack["prompts"])):
        md.append(f"### Shot {shot['id'] + 1}（{shot['start']}-{shot['end']} 秒）")
        md.append("")
        md.append("```")
        md.append(prompt)
        md.append("```")
        md.append("")
    md += ["---", "## 合规自检（发布前必读）", ""]
    md += [
        "1. 故事、台词、音乐是否全部来自你有权使用的内容（原创/授权/公有领域）？",
        "2. 一句话梗概测试：向别人讲一遍新片，是否还能认出是某部具体作品？",
        "3. 人物形象若基于真人，是否已获本人授权？",
        "4. 是否遵守目标平台的 AI 内容标识与搬运规则？",
    ]
    (out_dir / "prompts.md").write_text("\n".join(md), encoding="utf-8")

    # 逐镜 payload
    for shot, payload in zip(storyboard.get("shots", []), pack["payloads"]):
        save_json(payload, payload_dir / f"shot_{shot['id'] + 1:03d}.json")

    save_json(pack["manifest"], out_dir / "assets_manifest.json")
    (out_dir / "mapping.txt").write_text(pack["mapping_text"], encoding="utf-8")
    save_json(validation, out_dir / "validation_report.json")

    LOG.info("H3 导出完成: %s（%d 镜，%d 条资产）", out_dir, len(pack["payloads"]), len(pack["manifest"]["references"]))
    for aid, warns in validation.items():
        if warns and aid != "_notes":
            LOG.warning("资产 %s: %s", aid, "; ".join(warns))
    for w in pack["warnings"]:
        LOG.warning("%s", w)
    return out_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段4b：MiniMax H3 导出")
    parser.add_argument("video")
    parser.add_argument("--mode", default=None, choices=["reference", "i2v"], help="reference=多模态参考生视频 / i2v=首帧图生视频")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)
    setup_logger()
    cfg = load_config(args.config)
    mode = args.mode or cfg.get("h3", {}).get("mode", "reference")
    try:
        export_h3(args.video, cfg, mode)
    except FileNotFoundError as e:
        LOG.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
